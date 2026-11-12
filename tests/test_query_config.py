import pytest

from app.common.config import ConfigError, load_query_settings

BEDROCK = {
    "VECTOR_BUCKET_NAME": "vb",
    "VECTOR_INDEX_NAME": "idx",
    "AI_PROVIDER": "bedrock",
    "EMBEDDING_DIMENSION": "1024",
    "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
    "GENERATION_MODEL_ID": "us.amazon.nova-lite-v1:0",
}
GEMINI = {
    "VECTOR_BUCKET_NAME": "vb",
    "VECTOR_INDEX_NAME": "idx",
    "AI_PROVIDER": "gemini",
    "EMBEDDING_DIMENSION": "768",
    "GEMINI_EMBEDDING_MODEL": "gemini-embedding-001",
    "GEMINI_GENERATION_MODEL": "models/gemini-3.5-flash-lite",
    "GEMINI_API_KEY_PARAM": "/rag-demo/gemini-api-key",
}


def test_bedrock_defaults():
    s = load_query_settings(BEDROCK)
    assert s.generation_model_id == "us.amazon.nova-lite-v1:0"
    assert (s.top_k, s.distance_threshold, s.max_output_tokens) == (5, 0.6, 512)


def test_gemini_settings_use_gemini_models_and_normalize_prefix():
    s = load_query_settings(GEMINI)
    assert s.ai_provider == "gemini"
    assert s.generation_model_id == "gemini-3.5-flash-lite"
    assert s.embedding_model_id == "gemini-embedding-001"
    assert s.gemini_api_key_param == "/rag-demo/gemini-api-key"


def test_overrides_are_parsed():
    s = load_query_settings(
        {**BEDROCK, "TOP_K": "3", "DISTANCE_THRESHOLD": "0.45", "MAX_OUTPUT_TOKENS": "256"}
    )
    assert (s.top_k, s.distance_threshold, s.max_output_tokens) == (3, 0.45, 256)


@pytest.mark.parametrize(
    ("base", "missing"),
    [
        (BEDROCK, "GENERATION_MODEL_ID"),
        (GEMINI, "GEMINI_GENERATION_MODEL"),
        (GEMINI, "GEMINI_API_KEY_PARAM"),
    ],
)
def test_missing_provider_variable_is_named(base, missing):
    env = {k: v for k, v in base.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_query_settings(env)


@pytest.mark.parametrize(
    "extra",
    [
        {"TOP_K": "0"},
        {"TOP_K": "x"},
        {"DISTANCE_THRESHOLD": "abc"},
        {"DISTANCE_THRESHOLD": "0"},
        {"DISTANCE_THRESHOLD": "3"},
        {"MAX_OUTPUT_TOKENS": "-1"},
    ],
)
def test_invalid_values_are_rejected(extra):
    with pytest.raises(ConfigError):
        load_query_settings({**BEDROCK, **extra})
