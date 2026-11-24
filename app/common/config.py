"""Settings read from environment variables (set by Terraform on the Lambda).

AI_PROVIDER picks which provider's variables are required:
  gemini  -> GEMINI_EMBEDDING_MODEL, GEMINI_API_KEY_PARAM (+ GEMINI_GENERATION_MODEL for queries)
  bedrock -> EMBEDDING_MODEL_ID (+ GENERATION_MODEL_ID for queries)
The settings expose the active provider's model names as embedding_model_id / generation_model_id.
"""

import os
from dataclasses import dataclass

from app.common.chunking import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE

# Only objects under this prefix with one of these suffixes are ingested.
DOCS_PREFIX = "docs/"
ALLOWED_SUFFIXES = (".md", ".txt", ".pdf")
# Files that live next to the docs but are not part of the corpus (e.g. license/credit notes).
SKIP_FILENAMES = frozenset({"source.md"})

# A sub-folder under docs/ is a collection: docs/business/faq.md belongs to "business". Files
# directly under docs/ belong to the default collection. /ask can filter retrieval by it.
DEFAULT_COLLECTION = "fastapi"
COLLECTION_RE = r"[a-z0-9][a-z0-9_-]{0,29}"

PROVIDERS = ("gemini", "bedrock")
COMMON_VARS = ("VECTOR_BUCKET_NAME", "VECTOR_INDEX_NAME", "AI_PROVIDER", "EMBEDDING_DIMENSION")
GEMINI_VARS = ("GEMINI_EMBEDDING_MODEL", "GEMINI_API_KEY_PARAM")
BEDROCK_VARS = ("EMBEDDING_MODEL_ID",)
GEMINI_QUERY_VARS = ("GEMINI_GENERATION_MODEL",)
BEDROCK_QUERY_VARS = ("GENERATION_MODEL_ID",)

DEFAULT_TOP_K = 5
# Cosine distance = 1 - cosine similarity; 0.6 is a starting point to be tuned with evals (M4).
DEFAULT_DISTANCE_THRESHOLD = 0.6
DEFAULT_MAX_OUTPUT_TOKENS = 512


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Ingest settings."""

    vector_bucket: str
    index_name: str
    ai_provider: str
    embedding_model_id: str
    embedding_dimension: int
    gemini_api_key_param: str | None
    chunk_size: int
    chunk_overlap: int


@dataclass(frozen=True)
class QuerySettings:
    vector_bucket: str
    index_name: str
    ai_provider: str
    embedding_model_id: str
    embedding_dimension: int
    gemini_api_key_param: str | None
    generation_model_id: str
    top_k: int
    distance_threshold: float
    max_output_tokens: int


def _int_var(env, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def _float_var(env, name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None


def normalize_gemini_model(name: str) -> str:
    """Accept both "gemini-x" and "models/gemini-x"; the code always uses the short form."""
    return name.removeprefix("models/")


def _load_common(env, query: bool) -> dict:
    """Check required variables for the chosen provider and return the shared fields."""
    provider = (env.get("AI_PROVIDER") or "").lower()
    if provider and provider not in PROVIDERS:
        raise ConfigError(f"AI_PROVIDER must be one of {', '.join(PROVIDERS)}, got {provider!r}")

    required = list(COMMON_VARS)
    if provider == "gemini":
        required += GEMINI_VARS + (GEMINI_QUERY_VARS if query else ())
    elif provider == "bedrock":
        required += BEDROCK_VARS + (BEDROCK_QUERY_VARS if query else ())
    # With AI_PROVIDER missing we can only report the common variables; it is named in the error.
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))

    dimension = _int_var(env, "EMBEDDING_DIMENSION", 0)
    if dimension <= 0:
        raise ConfigError(f"EMBEDDING_DIMENSION must be positive, got {dimension}")

    if provider == "gemini":
        embedding_model = normalize_gemini_model(env["GEMINI_EMBEDDING_MODEL"])
        generation_model = normalize_gemini_model(env.get("GEMINI_GENERATION_MODEL", ""))
        key_param = env["GEMINI_API_KEY_PARAM"]
    else:
        embedding_model = env["EMBEDDING_MODEL_ID"]
        generation_model = env.get("GENERATION_MODEL_ID", "")
        key_param = None

    return {
        "vector_bucket": env["VECTOR_BUCKET_NAME"],
        "index_name": env["VECTOR_INDEX_NAME"],
        "ai_provider": provider,
        "embedding_model_id": embedding_model,
        "embedding_dimension": dimension,
        "gemini_api_key_param": key_param,
        "_generation_model": generation_model,
    }


def load_settings(env=None) -> Settings:
    """Build ingest Settings from `env` (defaults to os.environ), failing with a clear message."""
    env = os.environ if env is None else env
    common = _load_common(env, query=False)
    common.pop("_generation_model")

    size = _int_var(env, "CHUNK_SIZE", DEFAULT_CHUNK_SIZE)
    overlap = _int_var(env, "CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP)
    if size <= 0:
        raise ConfigError(f"CHUNK_SIZE must be positive, got {size}")
    if not 0 <= overlap < size:
        raise ConfigError(f"CHUNK_OVERLAP must be >= 0 and smaller than CHUNK_SIZE, got {overlap}")

    return Settings(**common, chunk_size=size, chunk_overlap=overlap)


def load_query_settings(env=None) -> QuerySettings:
    """Build QuerySettings for the query Lambda, failing with a clear message."""
    env = os.environ if env is None else env
    common = _load_common(env, query=True)
    generation_model = common.pop("_generation_model")

    top_k = _int_var(env, "TOP_K", DEFAULT_TOP_K)
    threshold = _float_var(env, "DISTANCE_THRESHOLD", DEFAULT_DISTANCE_THRESHOLD)
    max_tokens = _int_var(env, "MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)
    if top_k <= 0:
        raise ConfigError(f"TOP_K must be positive, got {top_k}")
    if not 0 < threshold <= 2:  # cosine distance ranges over [0, 2]
        raise ConfigError(f"DISTANCE_THRESHOLD must be in (0, 2], got {threshold}")
    if max_tokens <= 0:
        raise ConfigError(f"MAX_OUTPUT_TOKENS must be positive, got {max_tokens}")

    return QuerySettings(
        **common,
        generation_model_id=generation_model,
        top_k=top_k,
        distance_threshold=threshold,
        max_output_tokens=max_tokens,
    )


def collection_for_key(key: str) -> str:
    """ "docs/business/faq.md" -> "business"; "docs/path-params.md" -> the default collection."""
    parts = key.split("/")
    if len(parts) > 2 and parts[0] == "docs":
        return parts[1].lower()
    return DEFAULT_COLLECTION


def is_ingestible_key(key: str) -> bool:
    """True for docs/**/*.md|txt|pdf, except files listed in SKIP_FILENAMES."""
    lowered = key.lower()
    if not key.startswith(DOCS_PREFIX) or not lowered.endswith(ALLOWED_SUFFIXES):
        return False
    return lowered.rsplit("/", 1)[-1] not in SKIP_FILENAMES
