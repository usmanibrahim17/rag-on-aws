"""Ingest handler tests with Bedrock, S3 Vectors and S3 replaced by in-memory fakes.

moto does not support Bedrock or S3 Vectors, so the boto3 client getters are patched with
small fakes that enforce the real API limits and keep state we can assert on.
"""

import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.ingest.handler import handler

BUCKET = "docs-bucket"


class FakeBedrock:
    def __init__(self):
        self.texts = []

    def invoke_model(self, modelId, body, **kwargs):
        payload = json.loads(body)
        assert payload["dimensions"] == 1024 and payload["normalize"] is True
        self.texts.append(payload["inputText"])
        result = {
            "embedding": [0.5] * 1024,
            "inputTextTokenCount": len(payload["inputText"].split()),
        }
        return {"body": io.BytesIO(json.dumps(result).encode())}


class FakeS3Vectors:
    def __init__(self):
        self.store = {}
        self.put_batches = []

    def put_vectors(self, vectorBucketName, indexName, vectors):
        assert len(vectors) <= 500
        self.put_batches.append(len(vectors))
        for v in vectors:
            assert len(v["data"]["float32"]) == 1024
            self.store[v["key"]] = v

    def get_vectors(self, vectorBucketName, indexName, keys, **kwargs):
        assert len(keys) <= 100
        return {"vectors": [{"key": k} for k in keys if k in self.store]}

    def delete_vectors(self, vectorBucketName, indexName, keys):
        assert len(keys) <= 500
        for k in keys:
            self.store.pop(k, None)


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)].encode())}


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("VECTOR_BUCKET_NAME", "vb")
    monkeypatch.setenv("VECTOR_INDEX_NAME", "idx")
    monkeypatch.setenv("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    monkeypatch.setenv("CHUNK_SIZE", "500")
    monkeypatch.setenv("CHUNK_OVERLAP", "0")
    fakes = SimpleNamespace(bedrock=FakeBedrock(), vectors=FakeS3Vectors(), s3=FakeS3())
    with (
        patch("app.common.aws_clients.get_bedrock", return_value=fakes.bedrock),
        patch("app.common.aws_clients.get_s3vectors", return_value=fakes.vectors),
        patch("app.common.aws_clients.get_s3", return_value=fakes.s3),
    ):
        yield fakes


def event(name, key):
    return {
        "Records": [{"eventName": name, "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}]
    }


def doc_with_chunks(n):
    """A document yielding exactly n chunks at size=500, overlap=0 (one 300-char paragraph each)."""
    return "# Title\n\n" + "\n\n".join(f"{i:03d} " + "w" * 296 for i in range(n))


def upload(aws, key, content):
    aws.s3.objects[(BUCKET, key)] = content
    return handler(event("ObjectCreated:Put", key), SimpleNamespace(aws_request_id="req-1"))


def keys_for(aws, key):
    return sorted(k for k in aws.vectors.store if k.startswith(key + "#"))


def test_create_event_writes_vectors_with_deterministic_keys_and_metadata(aws):
    content = "# Guide\n\nIntro text.\n\n## Setup\n\nInstall it."
    result = upload(aws, "docs/guide.md", content)

    assert result == {"processed": 1}
    assert sorted(aws.vectors.store) == ["docs/guide.md#0", "docs/guide.md#1"]
    first, second = (aws.vectors.store[f"docs/guide.md#{i}"] for i in (0, 1))
    assert first["metadata"] == {
        "source_key": "docs/guide.md",
        "heading": "Guide",
        "chunk_index": 0,
        "chunk_text": "Intro text.",
    }
    assert second["metadata"]["heading"] == "Guide > Setup"
    assert isinstance(second["metadata"]["chunk_index"], int)
    # the embedding input carries the heading path; the stored text does not
    assert aws.bedrock.texts == ["Guide\n\nIntro text.", "Guide > Setup\n\nInstall it."]


def test_reupload_overwrites_and_removes_stale_vectors(aws):
    upload(aws, "docs/a.md", doc_with_chunks(5))
    assert len(keys_for(aws, "docs/a.md")) == 5

    upload(aws, "docs/a.md", doc_with_chunks(2))

    assert keys_for(aws, "docs/a.md") == ["docs/a.md#0", "docs/a.md#1"]


def test_reupload_with_same_content_does_not_duplicate(aws):
    upload(aws, "docs/a.md", doc_with_chunks(3))
    upload(aws, "docs/a.md", doc_with_chunks(3))
    assert len(keys_for(aws, "docs/a.md")) == 3


def test_reupload_as_empty_file_removes_all_vectors(aws):
    upload(aws, "docs/a.md", doc_with_chunks(3))
    upload(aws, "docs/a.md", "")
    assert keys_for(aws, "docs/a.md") == []


def test_stale_cleanup_finds_vectors_beyond_one_probe_batch(aws):
    upload(aws, "docs/big.md", doc_with_chunks(230))  # > 2 probe batches of 100
    upload(aws, "docs/big.md", doc_with_chunks(1))
    assert keys_for(aws, "docs/big.md") == ["docs/big.md#0"]


@pytest.mark.parametrize("name", ["ObjectRemoved:Delete", "ObjectRemoved:DeleteMarkerCreated"])
def test_delete_event_removes_only_that_documents_vectors(aws, name):
    upload(aws, "docs/a.md", doc_with_chunks(3))
    upload(aws, "docs/ab.md", doc_with_chunks(2))  # "docs/a" is a prefix of "docs/ab": must survive

    handler(event(name, "docs/a.md"), None)

    assert keys_for(aws, "docs/a.md") == []
    assert len(keys_for(aws, "docs/ab.md")) == 2


@pytest.mark.parametrize(
    "key", ["docs/photo.pdf", "other/readme.md", "readme.md", "docs/SOURCE.md"]
)
def test_ignored_keys_touch_nothing(aws, key):
    aws.s3.objects[(BUCKET, key)] = "# T\n\nbody"
    handler(event("ObjectCreated:Put", key), None)
    handler(event("ObjectRemoved:Delete", key), None)
    assert aws.vectors.store == {} and aws.bedrock.texts == []


def test_url_encoded_keys_are_decoded(aws):
    aws.s3.objects[(BUCKET, "docs/my doc+1.md")] = "# T\n\nbody"
    handler(event("ObjectCreated:Put", "docs/my+doc%2B1.md"), None)
    assert list(aws.vectors.store) == ["docs/my doc+1.md#0"]


def test_more_than_500_vectors_are_written_in_batches(aws):
    upload(aws, "docs/huge.md", doc_with_chunks(520))
    assert aws.vectors.put_batches == [500, 20]
    assert len(keys_for(aws, "docs/huge.md")) == 520


def test_failed_record_is_reported_but_others_still_processed(aws):
    aws.s3.objects[(BUCKET, "docs/ok.md")] = "# T\n\nbody"
    ev = {
        "Records": [
            *event("ObjectCreated:Put", "docs/missing.md")["Records"],  # S3 read raises KeyError
            *event("ObjectCreated:Put", "docs/ok.md")["Records"],
        ]
    }
    with pytest.raises(RuntimeError, match="1 of 2"):
        handler(ev, None)
    assert keys_for(aws, "docs/ok.md") == ["docs/ok.md#0"]


def test_logs_are_structured_json_with_request_id_and_latency(aws, caplog):
    with caplog.at_level(logging.INFO, logger="rag"):
        upload(aws, "docs/a.md", "# T\n\nhello there world")
    entry = json.loads(caplog.records[-1].getMessage())
    assert entry["event"] == "record_processed"
    assert entry["request_id"] == "req-1"
    assert entry["source_key"] == "docs/a.md"
    assert entry["status"] == "ingested" and entry["chunks"] == 1
    assert entry["embedding_tokens"] > 0
    assert isinstance(entry["latency_ms"], int)


def test_missing_configuration_fails_with_a_clear_error(aws, monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODEL_ID")
    with pytest.raises(Exception, match="EMBEDDING_MODEL_ID"):
        handler(event("ObjectCreated:Put", "docs/a.md"), None)
