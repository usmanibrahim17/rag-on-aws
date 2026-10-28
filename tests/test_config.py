import pytest

from app.common.config import ConfigError, is_ingestible_key, load_settings

VALID = {
    "VECTOR_BUCKET_NAME": "vb",
    "VECTOR_INDEX_NAME": "idx",
    "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
}


def test_defaults_are_applied_for_optional_values():
    s = load_settings(VALID)
    assert (s.vector_bucket, s.index_name) == ("vb", "idx")
    assert (s.chunk_size, s.chunk_overlap) == (1000, 150)


def test_overrides_are_parsed_as_integers():
    s = load_settings({**VALID, "CHUNK_SIZE": "500", "CHUNK_OVERLAP": "50"})
    assert (s.chunk_size, s.chunk_overlap) == (500, 50)


def test_missing_required_variables_are_all_named():
    with pytest.raises(ConfigError) as exc:
        load_settings({"VECTOR_INDEX_NAME": "idx"})
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
        ("docs/a.pdf", False),
        ("other/a.md", False),
        ("a.md", False),
    ],
)
def test_is_ingestible_key(key, expected):
    assert is_ingestible_key(key) is expected
