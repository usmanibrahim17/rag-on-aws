"""Ingest Lambda: S3 ObjectCreated / ObjectRemoved (via SQS) -> chunk -> embed -> S3 Vectors.

Vector keys are "<s3_key>#<chunk_index>", so re-uploading a document overwrites its vectors
instead of duplicating them, and a delete (or a shorter re-upload) removes the leftovers.
"""

import json
import time
from urllib.parse import unquote_plus

from app.common import aws_clients, providers
from app.common.chunking import chunk_markdown
from app.common.config import Settings, is_ingestible_key, load_settings
from app.common.logs import log_json

# Filterable metadata is capped at 2 KB per vector in S3 Vectors; keep the heading well below it.
MAX_HEADING_CHARS = 512


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
        elif _run_record(record, settings, request_id):
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
    results = [
        _run_record(inner, settings, request_id, message_id) for inner in body.get("Records", [])
    ]
    return all(results)


def _run_record(
    record: dict, settings: Settings, request_id: str | None, message_id: str | None = None
) -> bool:
    """Process one S3 event record; log the outcome; return False instead of raising."""
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
        return False
    log_json(
        "record_processed",
        request_id=request_id,
        latency_ms=_elapsed_ms(started),
        **info,
        **result,
    )
    return True


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
    text = aws_clients.read_text_object(bucket, key)
    chunks = chunk_markdown(text, settings.chunk_size, settings.chunk_overlap)

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
        "chunks": len(chunks),
        "embedding_tokens": tokens,
        "stale_vectors_deleted": len(stale),
    }
