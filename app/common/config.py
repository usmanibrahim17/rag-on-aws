"""Settings read from environment variables (set by Terraform on the Lambda)."""

import os
from dataclasses import dataclass

from app.common.chunking import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE

# Only objects under this prefix with one of these suffixes are ingested.
DOCS_PREFIX = "docs/"
ALLOWED_SUFFIXES = (".md", ".txt")
# Files that live next to the docs but are not part of the corpus (e.g. license/credit notes).
SKIP_FILENAMES = frozenset({"source.md"})

REQUIRED_VARS = ("VECTOR_BUCKET_NAME", "VECTOR_INDEX_NAME", "EMBEDDING_MODEL_ID")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    vector_bucket: str
    index_name: str
    embedding_model_id: str
    chunk_size: int
    chunk_overlap: int


def _int_var(env, name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


def load_settings(env=None) -> Settings:
    """Build Settings from `env` (defaults to os.environ), failing with a clear message."""
    env = os.environ if env is None else env

    missing = [name for name in REQUIRED_VARS if not env.get(name)]
    if missing:
        raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))

    size = _int_var(env, "CHUNK_SIZE", DEFAULT_CHUNK_SIZE)
    overlap = _int_var(env, "CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP)
    if size <= 0:
        raise ConfigError(f"CHUNK_SIZE must be positive, got {size}")
    if not 0 <= overlap < size:
        raise ConfigError(f"CHUNK_OVERLAP must be >= 0 and smaller than CHUNK_SIZE, got {overlap}")

    return Settings(
        vector_bucket=env["VECTOR_BUCKET_NAME"],
        index_name=env["VECTOR_INDEX_NAME"],
        embedding_model_id=env["EMBEDDING_MODEL_ID"],
        chunk_size=size,
        chunk_overlap=overlap,
    )


def is_ingestible_key(key: str) -> bool:
    """True for docs/**/*.md|txt, except files listed in SKIP_FILENAMES."""
    lowered = key.lower()
    if not key.startswith(DOCS_PREFIX) or not lowered.endswith(ALLOWED_SUFFIXES):
        return False
    return lowered.rsplit("/", 1)[-1] not in SKIP_FILENAMES
