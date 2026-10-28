"""Ingest Lambda: S3 ObjectCreated / ObjectRemoved -> chunk -> embed -> S3 Vectors.

Vector keys are "<s3_key>#<chunk_index>", so re-uploading a document overwrites its vectors
instead of duplicating them, and a delete (or a shorter re-upload) removes the leftovers.
"""

import time
from urllib.parse import unquote_plus

from app.common import aws_clients
from app.common.chunking import chunk_markdown
from app.common.config import Settings, is_ingestible_key, load_settings
from app.common.logs import log_json

# Filterable metadata is capped at 2 KB per vector in S3 Vectors; keep the heading well below it.
MAX_HEADING_CHARS = 512


def handler(event, context):
    settings = load_settings()
    request_id = getattr(context, "aws_request_id", None)

    processed = 0
    failures = 0
    for record in event.get("Records", []):
        started = time.monotonic()
        info = _record_info(record)
        try:
            result = _process_record(record, settings)
        except Exception as exc:  # keep going so one bad file doesn't block the others
            failures += 1
            log_json(
                "record_failed",
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                error=repr(exc),
                **info,
            )
            continue
        processed += 1
        log_json(
            "record_processed",
            request_id=request_id,
            latency_ms=_elapsed_ms(started),
            **info,
            **result,
        )

    if failures:
        # Raising makes Lambda retry the whole event; every step is idempotent, so that is safe.
        raise RuntimeError(f"{failures} of {failures + processed} record(s) failed")
    return {"processed": processed}


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

    vectors_data, tokens = aws_clients.embed_many(
        [c.embed_text for c in chunks], settings.embedding_model_id
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
