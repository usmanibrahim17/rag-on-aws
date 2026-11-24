import pytest

from app.common.config import ConfigError, collection_for_key, is_ingestible_key, load_settings

VALID = {
    "VECTOR_BUCKET_NAME": "vb",
    "VECTOR_INDEX_NAME": "idx",
    "AI_PROVIDER": "bedrock",
    "EMBEDDING_DIMENSION": "1024",
    "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
}

GEMINI = {
    "VECTOR_BUCKET_NAME": "vb",
    "VECTOR_INDEX_NAME": "idx",
    "AI_PROVIDER": "gemini",
    "EMBEDDING_DIMENSION": "768",
    "GEMINI_EMBEDDING_MODEL": "models/gemini-embedding-001",
    "GEMINI_API_KEY_PARAM": "/rag-demo/gemini-api-key",
}


def test_defaults_are_applied_for_optional_values():
    s = load_settings(VALID)
    assert (s.vector_bucket, s.index_name) == ("vb", "idx")
    assert (s.chunk_size, s.chunk_overlap) == (1000, 150)
    assert (s.ai_provider, s.embedding_dimension) == ("bedrock", 1024)
    assert s.gemini_api_key_param is None


def test_gemini_settings_normalize_the_models_prefix():
    s = load_settings(GEMINI)
    assert s.ai_provider == "gemini"
    assert s.embedding_model_id == "gemini-embedding-001"
    assert (s.embedding_dimension, s.gemini_api_key_param) == (768, "/rag-demo/gemini-api-key")


def test_gemini_does_not_require_bedrock_variables():
    assert "EMBEDDING_MODEL_ID" not in GEMINI
    load_settings(GEMINI)  # must not raise


@pytest.mark.parametrize("missing", ["GEMINI_EMBEDDING_MODEL", "GEMINI_API_KEY_PARAM"])
def test_gemini_missing_variable_is_named(missing):
    env = {k: v for k, v in GEMINI.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


def test_missing_provider_and_dimension_are_named():
    env = {"VECTOR_BUCKET_NAME": "vb", "VECTOR_INDEX_NAME": "idx"}
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    assert "AI_PROVIDER" in str(exc.value) and "EMBEDDING_DIMENSION" in str(exc.value)


@pytest.mark.parametrize(
    "extra",
    [{"AI_PROVIDER": "openai"}, {"EMBEDDING_DIMENSION": "0"}, {"EMBEDDING_DIMENSION": "big"}],
)
def test_invalid_provider_or_dimension_is_rejected(extra):
    with pytest.raises(ConfigError):
        load_settings({**VALID, **extra})


def test_overrides_are_parsed_as_integers():
    s = load_settings({**VALID, "CHUNK_SIZE": "500", "CHUNK_OVERLAP": "50"})
    assert (s.chunk_size, s.chunk_overlap) == (500, 50)


def test_missing_required_variables_are_all_named():
    with pytest.raises(ConfigError) as exc:
        load_settings(
            {"VECTOR_INDEX_NAME": "idx", "AI_PROVIDER": "bedrock", "EMBEDDING_DIMENSION": "1024"}
        )
    message = str(exc.value)
    assert "VECTOR_BUCKET_NAME" in message and "EMBEDDING_MODEL_ID" in message
    assert "VECTOR_INDEX_NAME" not in message


def test_empty_required_variable_counts_as_missing():
    with pytest.raises(ConfigError, match="VECTOR_BUCKET_NAME"):
        load_settings({**VALID, "VECTOR_BUCKET_NAME": ""})


@pytest.mark.parametrize(
    "extra",
    [
        {"CHUNK_SIZE": "abc"},
        {"CHUNK_SIZE": "0"},
        {"CHUNK_SIZE": "100", "CHUNK_OVERLAP": "100"},
        {"CHUNK_OVERLAP": "-5"},
    ],
)
def test_invalid_chunk_settings_are_rejected(extra):
    with pytest.raises(ConfigError):
        load_settings({**VALID, **extra})


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("docs/a.md", True),
        ("docs/sub/b.TXT", True),
        ("docs/SOURCE.md", False),
        ("docs/sub/source.md", False),
        ("docs/a.pdf", True),
        ("docs/Sub/Manual.PDF", True),
        ("docs/a.docx", False),
        ("other/a.md", False),
        ("a.md", False),
    ],
)
def test_is_ingestible_key(key, expected):
    assert is_ingestible_key(key) is expected


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("docs/path-params.md", "fastapi"),  # directly under docs/: the default collection
        ("docs/business/faq.md", "business"),
        ("docs/Business/Returns.PDF", "business"),
        ("docs/business/deeper/x.md", "business"),  # only the first folder counts
        ("other/business/faq.md", "fastapi"),
    ],
)
def test_collection_for_key(key, expected):
    assert collection_for_key(key) == expected
