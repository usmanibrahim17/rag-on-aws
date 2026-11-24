"""Query Lambda: POST /ask (API Gateway HTTP API, payload v2).

question -> embed (Titan) -> top-k vector search -> grounded answer (Bedrock Converse).
If nothing is close enough, we answer "I don't know" without calling the LLM.
"""

import base64
import json
import re
import time

from botocore.exceptions import ClientError

from app.common import aws_clients, providers
from app.common.config import COLLECTION_RE, ConfigError, QuerySettings, load_query_settings
from app.common.logs import log_json
from app.common.providers import GenerationBlockedError, ProviderThrottledError

MAX_QUESTION_CHARS = 500
COLLECTION_PATTERN = re.compile(rf"{COLLECTION_RE}\Z")
IDK_ANSWER = "I don't know based on the provided documents."
BLOCKED_ANSWER = "I couldn't generate an answer for this question. Please try rephrasing it."

BUSY_MESSAGE = "The service is busy right now. Please try again in a moment."

SYSTEM_PROMPT = (
    "You answer questions using ONLY the numbered sources provided by the user. "
    "Cite every claim with the source number in square brackets, like [1] or [2][3]. "
    "If the sources do not contain the answer, say you don't know based on the provided "
    "documents instead of guessing. Never use outside knowledge. Be concise."
)


class BadRequest(ValueError):
    """Invalid client input; the message is safe to show to the caller."""


def handler(event, context):
    started = time.monotonic()
    request_id = getattr(context, "aws_request_id", None)
    log = {"request_id": request_id, "question_chars": None}
    try:
        settings = load_query_settings()
        question, collection = _parse_request(event)
        log["question_chars"] = len(question)
        log["collection"] = collection
        body, log_fields = _answer(question, collection, settings)
        status = 200
    except BadRequest as exc:
        status, body, log_fields = 400, {"error": str(exc)}, {"outcome": "bad_request"}
    except ProviderThrottledError:
        status, body, log_fields = 503, {"error": BUSY_MESSAGE}, {"outcome": "throttled"}
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ThrottlingException":
            status, body, log_fields = 503, {"error": BUSY_MESSAGE}, {"outcome": "throttled"}
        else:
            status, body, log_fields = _internal_error(exc)
    except ConfigError as exc:
        status, body, log_fields = _internal_error(exc)
    except Exception as exc:  # never leak internals to the caller; details go to the logs
        status, body, log_fields = _internal_error(exc)

    latency_ms = _elapsed_ms(started)
    if status == 200:
        body["latency_ms"] = latency_ms
    log_json("ask", latency_ms=latency_ms, status=status, **log, **log_fields)
    return _response(status, body)


def _internal_error(exc: Exception):
    return (
        500,
        {"error": "Something went wrong while answering. Please try again later."},
        {"outcome": "error", "error": repr(exc)},
    )


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _response(status: int, body: dict) -> dict:
    # CORS headers are added by API Gateway (see query.tf).
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_request(event: dict) -> tuple[str, str | None]:
    """Validate the JSON body. Returns (question, optional collection to restrict retrieval to)."""
    raw = event.get("body")
    if raw and event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw or "")
    except ValueError:
        raise BadRequest("Request body must be valid JSON.") from None
    if not isinstance(payload, dict):
        raise BadRequest('Request body must be a JSON object like {"question": "..."}.')

    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise BadRequest('"question" is required and must be a non-empty string.')
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        raise BadRequest(f'"question" must be at most {MAX_QUESTION_CHARS} characters.')

    collection = payload.get("collection")
    if collection is not None and (
        not isinstance(collection, str) or not COLLECTION_PATTERN.match(collection)
    ):
        raise BadRequest('"collection" must be a short lowercase name like "business".')
    return question, collection


def _answer(question: str, collection: str | None, settings: QuerySettings) -> tuple[dict, dict]:
    vector, embed_tokens = providers.get_embedder(settings).embed_query(question)
    hits = aws_clients.query_vectors(
        settings.vector_bucket, settings.index_name, vector, settings.top_k, collection
    )
    # Sort defensively: the "best" hit is the one with the smallest cosine distance.
    hits = sorted(hits, key=lambda h: h["distance"])
    sources = [
        {
            "n": n,
            "key": (h.get("metadata") or {}).get("source_key", h["key"]),
            "heading": (h.get("metadata") or {}).get("heading", ""),
            "distance": round(h["distance"], 4),
        }
        for n, h in enumerate(hits, start=1)
    ]
    top_distance = hits[0]["distance"] if hits else None
    log_fields = {
        "top_distance": top_distance,
        "sources": [{"key": s["key"], "distance": s["distance"]} for s in sources],
        "embedding_tokens": embed_tokens,
    }

    # Nothing close enough: refuse rather than let the model guess. No LLM call, no LLM cost.
    if top_distance is None or top_distance > settings.distance_threshold:
        body = {
            "answer": IDK_ANSWER,
            "sources": sources,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "model_id": settings.generation_model_id,
        }
        return body, {**log_fields, "outcome": "no_context", "input_tokens": 0, "output_tokens": 0}

    try:
        answer, in_tokens, out_tokens = providers.get_generator(settings).generate(
            SYSTEM_PROMPT, build_user_prompt(question, hits), settings.max_output_tokens
        )
    except GenerationBlockedError as exc:
        # The model returned no text (e.g. a safety block): a clear 200, not a server error.
        body = {
            "answer": BLOCKED_ANSWER,
            "sources": sources,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "model_id": settings.generation_model_id,
        }
        log_fields.update(outcome="blocked", block_reason=exc.reason)
        log_fields.update(input_tokens=0, output_tokens=0)
        return body, log_fields
    body = {
        "answer": answer,
        "sources": sources,
        "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
        "model_id": settings.generation_model_id,
    }
    log_fields.update(outcome="answered", input_tokens=in_tokens, output_tokens=out_tokens)
    return body, log_fields


def build_user_prompt(question: str, hits: list[dict]) -> str:
    """Numbered sources [1]..[k] (heading + text), then the question."""
    blocks = []
    for n, hit in enumerate(hits, start=1):
        meta = hit.get("metadata") or {}
        heading = meta.get("heading") or "(intro)"
        blocks.append(f"[{n}] {heading}\n{meta.get('chunk_text', '')}")
    return "Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"
