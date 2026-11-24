"""Ingest Lambda: S3 ObjectCreated / ObjectRemoved (via SQS) -> chunk -> embed -> S3 Vectors.

Vector keys are "<s3_key>#<chunk_index>", so re-uploading a document overwrites its vectors
instead of duplicating them, and a delete (or a shorter re-upload) removes the leftovers.
"""

import json
import random
import time
from urllib.parse import unquote_plus

from app.common import aws_clients, documents, providers
from app.common.chunking import chunk_markdown
from app.common.config import Settings, collection_for_key, is_ingestible_key, load_settings
from app.common.logs import log_json
from app.common.providers import ProviderThrottledError

# Filterable metadata is capped at 2 KB per vector in S3 Vectors; keep the heading well below it.
MAX_HEADING_CHARS = 512

# Outcomes of processing one S3 record.
OK, FAILED, THROTTLED = "ok", "failed", "throttled"

# Retry backoff for messages the AI provider throttled (HTTP 429): 60 s, 120 s, 240 s, 480 s,
# then capped. The queue's own visibility timeout (30 min) is far too slow for rate limits,
# which clear within a minute or two, so a throttled message is released earlier than that.
RETRY_BASE_SECONDS = 60
RETRY_MAX_SECONDS = 600


def handler(event, context):
    """Entry point for SQS events (S3 -> SQS -> Lambda) and, for compatibility, direct S3 events.

    SQS: each message body is an S3 event notification. Failed messages are reported in
    batchItemFailures (we don't raise), so only those messages go back to the queue and,
    after maxReceiveCount tries, to the dead-letter queue.
    """
    settings = load_settings()
    request_id = getattr(context, "aws_request_id", None)

    processed = 0
    direct_failures = 0
    batch_failures: list[dict] = []
    has_sqs = False
    for record in event.get("Records", []):
        if record.get("eventSource") == "aws:sqs":
            has_sqs = True
            if _process_sqs_message(record, settings, request_id):
                processed += 1
            else:
                batch_failures.append({"itemIdentifier": record["messageId"]})
        elif _run_record(record, settings, request_id) == OK:
            processed += 1
        else:
            direct_failures += 1

    if direct_failures:
        # Direct S3 invoke: raising makes Lambda retry the event; every step is idempotent.
        raise RuntimeError(f"{direct_failures} of {direct_failures + processed} record(s) failed")
    result: dict = {"processed": processed}
    if has_sqs:
        result["batchItemFailures"] = batch_failures
    return result


def _process_sqs_message(message: dict, settings: Settings, request_id: str | None) -> bool:
    """Handle one SQS message. True = done (delete it), False = report as a batch item failure."""
    message_id = message.get("messageId")
    try:
        body = json.loads(message.get("body") or "")
        if not isinstance(body, dict):
            raise ValueError("message body is not a JSON object")
    except ValueError as exc:
        log_json("message_failed", request_id=request_id, message_id=message_id, error=repr(exc))
        return False

    # S3 sends this once when the notification is created; there is nothing to ingest.
    if body.get("Event") == "s3:TestEvent":
        log_json(
            "message_skipped", request_id=request_id, message_id=message_id, reason="s3:TestEvent"
        )
        return True

    # Run every inner record even if one fails: progress is kept, and retrying is idempotent.
    outcomes = [
        _run_record(inner, settings, request_id, message_id) for inner in body.get("Records", [])
    ]
    if THROTTLED in outcomes:
        _retry_sooner(message, request_id)
    return all(outcome == OK for outcome in outcomes)


def retry_delay_seconds(receive_count: int) -> int:
    """Seconds before a throttled message should be retried: exponential with jitter, capped.

    receive_count 1 -> ~60 s, 2 -> ~120 s, 3 -> ~240 s, 4 -> ~480 s, 5+ -> up to 600 s. The jitter
    (+/-20%) spreads retries out so the messages that were throttled together don't all come
    back in the same second and get throttled again.
    """
    base = RETRY_BASE_SECONDS * 2 ** (max(receive_count, 1) - 1)
    return min(RETRY_MAX_SECONDS, round(base * random.uniform(0.8, 1.2)))


def queue_url_from_arn(arn: str) -> str:
    """arn:aws:sqs:<region>:<account>:<name> -> https://sqs.<region>.amazonaws.com/<account>/<name>"""
    _, _, _, region, account, name = arn.split(":", 5)
    return f"https://sqs.{region}.amazonaws.com/{account}/{name}"


def _retry_sooner(message: dict, request_id: str | None) -> None:
    """Shorten the wait before SQS redelivers a throttled message (best effort).

    The message is still reported as a batch item failure by the caller, so it will be
    retried either way; if this call fails we only lose the shorter delay.
    """
    message_id = message.get("messageId")
    try:
        receive_count = int((message.get("attributes") or {}).get("ApproximateReceiveCount", 1))
        delay = retry_delay_seconds(receive_count)
        aws_clients.change_message_visibility(
            queue_url_from_arn(message["eventSourceARN"]), message["receiptHandle"], delay
        )
    except Exception as exc:  # the default visibility timeout still applies
        log_json(
            "retry_delay_failed", request_id=request_id, message_id=message_id, error=repr(exc)
        )
        return
    log_json(
        "retry_scheduled",
        request_id=request_id,
        message_id=message_id,
        receive_count=receive_count,
        retry_in_seconds=delay,
    )


def _run_record(
    record: dict, settings: Settings, request_id: str | None, message_id: str | None = None
) -> str:
    """Process one S3 event record and log it. Returns OK, FAILED or THROTTLED, never raises."""
    started = time.monotonic()
    info = _record_info(record)
    if message_id:
        info["message_id"] = message_id
    try:
        result = _process_record(record, settings)
    except Exception as exc:  # one bad file must not block the others
        log_json(
            "record_failed",
            request_id=request_id,
            latency_ms=_elapsed_ms(started),
            error=repr(exc),
            **info,
        )
        return THROTTLED if isinstance(exc, ProviderThrottledError) else FAILED
    log_json(
        "record_processed",
        request_id=request_id,
        latency_ms=_elapsed_ms(started),
        **info,
        **result,
    )
    return OK


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _record_info(record: dict) -> dict:
    s3 = record.get("s3", {})
    return {
        "event_name": record.get("eventName"),
        "bucket": s3.get("bucket", {}).get("name"),
        # S3 event keys are URL-encoded ("+" for spaces, %XX for the rest).
        "source_key": unquote_plus(s3.get("object", {}).get("key", "")),
    }


def _process_record(record: dict, settings: Settings) -> dict:
    info = _record_info(record)
    event_name, bucket, key = info["event_name"] or "", info["bucket"], info["source_key"]

    if not is_ingestible_key(key):
        return {"status": "skipped", "reason": "not an ingestible docs/ .md or .txt file"}

    if event_name.startswith("ObjectRemoved:"):
        # Also covers DeleteMarkerCreated on a versioned bucket (what a normal delete produces).
        stale = aws_clients.find_chunk_keys(settings.vector_bucket, settings.index_name, key)
        aws_clients.delete_vectors(settings.vector_bucket, settings.index_name, stale)
        return {"status": "deleted", "vectors_deleted": len(stale)}

    if event_name.startswith("ObjectCreated:"):
        return _ingest_object(bucket, key, settings)

    return {"status": "skipped", "reason": f"unsupported event {event_name!r}"}


def _ingest_object(bucket: str, key: str, settings: Settings) -> dict:
    try:
        text = documents.extract_text(key, aws_clients.read_object_bytes(bucket, key))
    except documents.UnreadableDocument as exc:
        # Retrying cannot fix an encrypted/corrupt/oversized file: log it and move on.
        return {"status": "skipped", "reason": str(exc)}
    chunks = chunk_markdown(text, settings.chunk_size, settings.chunk_overlap)
    collection = collection_for_key(key)

    # One batched call per up-to-100 chunks with Gemini; parallel per-chunk calls with Titan.
    vectors_data, tokens = providers.get_embedder(settings).embed_documents(
        [c.embed_text for c in chunks]
    )
    vectors = [
        {
            "key": f"{key}#{chunk.index}",
            "data": {"float32": vector},
            "metadata": {
                "source_key": key,
                "collection": collection,  # filterable: /ask can restrict retrieval to it
                "heading": chunk.heading[:MAX_HEADING_CHARS] or "(intro)",
                "chunk_index": chunk.index,
                # Marked non-filterable on the index; returned with query results for the prompt.
                "chunk_text": chunk.text,
            },
        }
        for chunk, vector in zip(chunks, vectors_data, strict=True)
    ]
    aws_clients.put_vectors(settings.vector_bucket, settings.index_name, vectors)

    # Write first, then clean up: the document is never missing from the index mid-update.
    # Anything at index >= len(chunks) is left over from an earlier, longer version.
    stale = aws_clients.find_chunk_keys(
        settings.vector_bucket, settings.index_name, key, start=len(chunks)
    )
    aws_clients.delete_vectors(settings.vector_bucket, settings.index_name, stale)

    return {
        "status": "ingested",
        "collection": collection,
        "chunks": len(chunks),
        "embedding_tokens": tokens,
        "stale_vectors_deleted": len(stale),
    }
