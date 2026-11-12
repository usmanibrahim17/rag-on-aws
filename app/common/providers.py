"""AI providers: one Embedder and one Generator interface, two implementations each.

AI_PROVIDER=gemini  -> Google Gemini over HTTPS (standard-library urllib, no Google SDK)
AI_PROVIDER=bedrock -> Amazon Bedrock (Titan embeddings + Converse), unchanged behaviour

The handlers only see the interfaces, so switching provider is one setting. Note that vectors
from different embedding models are not comparable: switching means recreating the index and
re-ingesting the documents.
"""

import json
import math
import random
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from functools import lru_cache

from app.common import aws_clients
from app.common.config import ConfigError, normalize_gemini_model

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_BATCH_SIZE = 100  # batchEmbedContents accepts at most 100 texts per request
GENERATION_TEMPERATURE = 0.2  # we want faithful answers from the sources, not creativity

MAX_ATTEMPTS = 5  # total tries per request (1 + 4 retries)
REQUEST_TIMEOUT_SECONDS = 20
MAX_BACKOFF_SECONDS = 8
RETRY_AFTER_CAP_SECONDS = 10


class ProviderError(RuntimeError):
    """A provider call failed. Messages never contain keys, headers or response bodies."""


class ProviderThrottledError(ProviderError):
    """The provider kept answering 429 (rate limit / quota) after all retries."""


class GenerationBlockedError(ProviderError):
    """The model returned no text (e.g. safety block). `reason` is safe to log."""

    def __init__(self, reason: str):
        super().__init__(f"generation returned no text ({reason})")
        self.reason = reason


# --- Interfaces -----------------------------------------------------------------------------


class Embedder(ABC):
    @abstractmethod
    def embed_documents(self, texts: list[str]) -> tuple[list[list[float]], int]:
        """Embed document chunks (ingest). Returns vectors in input order + input token count."""

    @abstractmethod
    def embed_query(self, text: str) -> tuple[list[float], int]:
        """Embed a user question. Returns (vector, input token count)."""


class Generator(ABC):
    @abstractmethod
    def generate(self, system: str, user: str, max_tokens: int) -> tuple[str, int, int]:
        """Return (answer text, input tokens, output tokens)."""


# --- Bedrock --------------------------------------------------------------------------------


class BedrockEmbedder(Embedder):
    """Titan v2. It has no task types, so documents and queries are embedded the same way."""

    def __init__(self, model_id: str, dimension: int):
        self.model_id, self.dimension = model_id, dimension

    def embed_documents(self, texts):
        return aws_clients.embed_many(texts, self.model_id, self.dimension)

    def embed_query(self, text):
        return aws_clients.embed_text(text, self.model_id, self.dimension)


class BedrockGenerator(Generator):
    def __init__(self, model_id: str):
        self.model_id = model_id

    def generate(self, system, user, max_tokens):
        return aws_clients.converse(self.model_id, system, user, max_tokens)


# --- Gemini ---------------------------------------------------------------------------------


@lru_cache
def get_gemini_api_key(param_name: str) -> str:
    """Read the API key from SSM once per Lambda container; later calls hit the cache."""
    return aws_clients.read_secure_parameter(param_name)


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    """Exponential backoff with jitter (1, 2, 4, 8 s, each scaled by 0.5-1.0).

    Jitter stops many concurrent invocations from retrying in lock-step. A numeric Retry-After
    header from the server wins, capped so we never sleep past the Lambda's timeout.
    """
    if retry_after:
        try:
            return min(float(retry_after), RETRY_AFTER_CAP_SECONDS)
        except ValueError:
            pass
    return min(MAX_BACKOFF_SECONDS, 2**attempt) * random.uniform(0.5, 1.0)


def _post_json(url: str, payload: dict, api_key: str) -> dict:
    """POST JSON to Gemini, retrying 429 / 5xx / network errors with backoff.

    The key travels in a header (never the URL) so it cannot leak into logs or error text.
    """
    data = json.dumps(payload).encode()
    last_status: int | None = None
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"content-type": "application/json", "x-goog-api-key": api_key},
        )
        retry_after = None
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            exc.close()
            if exc.code != 429 and exc.code < 500:
                raise ProviderError(f"Gemini request failed with HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            last_status = None  # network problem: worth retrying like a 5xx

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(_backoff_seconds(attempt, retry_after))

    if last_status == 429:
        raise ProviderThrottledError("Gemini rate limit or quota exceeded (HTTP 429)")
    detail = f"HTTP {last_status}" if last_status else "network error"
    raise ProviderError(f"Gemini request failed after {MAX_ATTEMPTS} attempts ({detail})")


def _l2_normalize(vector: list[float]) -> list[float]:
    # Gemini only returns unit-length vectors at the full 3072 dims; truncated ones need this.
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class GeminiEmbedder(Embedder):
    def __init__(self, model: str, dimension: int, key_param: str):
        self.model, self.dimension, self.key_param = (
            normalize_gemini_model(model),
            dimension,
            key_param,
        )

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        url = f"{GEMINI_BASE_URL}/models/{self.model}:batchEmbedContents"
        api_key = get_gemini_api_key(self.key_param)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), GEMINI_BATCH_SIZE):
            batch = texts[start : start + GEMINI_BATCH_SIZE]
            payload = {
                "requests": [
                    {
                        "model": f"models/{self.model}",
                        "content": {"parts": [{"text": text}]},
                        "taskType": task_type,
                        "outputDimensionality": self.dimension,
                    }
                    for text in batch
                ]
            }
            embeddings = _post_json(url, payload, api_key).get("embeddings", [])
            if len(embeddings) != len(batch):
                raise ProviderError(
                    f"Gemini returned {len(embeddings)} embeddings for {len(batch)} texts"
                )
            vectors.extend(_l2_normalize(e["values"]) for e in embeddings)
        return vectors

    # Gemini embedding responses carry no token counts, so we report 0 (costs are estimated
    # from character counts instead).
    def embed_documents(self, texts):
        if not texts:
            return [], 0
        return self._embed(texts, "RETRIEVAL_DOCUMENT"), 0

    def embed_query(self, text):
        return self._embed([text], "RETRIEVAL_QUERY")[0], 0


class GeminiGenerator(Generator):
    def __init__(self, model: str, key_param: str):
        self.model, self.key_param = normalize_gemini_model(model), key_param

    def generate(self, system, user, max_tokens):
        url = f"{GEMINI_BASE_URL}/models/{self.model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": GENERATION_TEMPERATURE,
                "maxOutputTokens": max_tokens,
            },
        }
        result = _post_json(url, payload, get_gemini_api_key(self.key_param))

        candidates = result.get("candidates") or []
        parts = (candidates[0].get("content") or {}).get("parts", []) if candidates else []
        # Skip "thought" parts (a thinking model's reasoning); only the final answer is returned.
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not text:
            reason = (
                (result.get("promptFeedback") or {}).get("blockReason")
                or (candidates[0].get("finishReason") if candidates else None)
                or "no candidates"
            )
            raise GenerationBlockedError(str(reason))

        usage = result.get("usageMetadata") or {}
        # Thinking tokens are billed as output, so count them there.
        output_tokens = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        return text, usage.get("promptTokenCount", 0), output_tokens


# --- Factory --------------------------------------------------------------------------------


def get_embedder(settings) -> Embedder:
    """Pick the embedder from settings.ai_provider (gemini | bedrock)."""
    if settings.ai_provider == "gemini":
        return GeminiEmbedder(
            settings.embedding_model_id, settings.embedding_dimension, settings.gemini_api_key_param
        )
    if settings.ai_provider == "bedrock":
        return BedrockEmbedder(settings.embedding_model_id, settings.embedding_dimension)
    raise ConfigError(f"Unknown AI provider {settings.ai_provider!r}")


def get_generator(settings) -> Generator:
    """Pick the generator from settings.ai_provider (gemini | bedrock)."""
    if settings.ai_provider == "gemini":
        return GeminiGenerator(settings.generation_model_id, settings.gemini_api_key_param)
    if settings.ai_provider == "bedrock":
        return BedrockGenerator(settings.generation_model_id)
    raise ConfigError(f"Unknown AI provider {settings.ai_provider!r}")
