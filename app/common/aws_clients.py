"""Thin boto3 wrappers: Titan embeddings, S3 Vectors, S3 reads.

Clients are created lazily and cached, so importing this module never touches AWS
(tests patch the get_* functions with fakes).
"""

import json
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import boto3
from botocore.config import Config

EMBEDDING_DIMENSIONS = 1024
EMBED_WORKERS = 5  # parallel Titan calls; adaptive retries below absorb throttling

# S3 Vectors API limits per call.
PUT_BATCH = 500
DELETE_BATCH = 500
GET_BATCH = 100

# "adaptive" retry mode backs off on throttling errors from Bedrock / S3 Vectors.
_CLIENT_CONFIG = Config(retries={"mode": "adaptive", "max_attempts": 8})


@lru_cache
def get_bedrock():
    return boto3.client("bedrock-runtime", config=_CLIENT_CONFIG)


@lru_cache
def get_s3vectors():
    return boto3.client("s3vectors", config=_CLIENT_CONFIG)


@lru_cache
def get_s3():
    return boto3.client("s3", config=_CLIENT_CONFIG)


def _batches(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


# --- S3 -------------------------------------------------------------------------------------


def read_text_object(bucket: str, key: str) -> str:
    body = get_s3().get_object(Bucket=bucket, Key=key)["Body"].read()
    return body.decode("utf-8-sig")  # -sig drops a BOM if the file has one


# --- Bedrock embeddings ---------------------------------------------------------------------


def embed_text(text: str, model_id: str) -> tuple[list[float], int]:
    """Embed one text with Titan v2. Returns (1024-dim normalized vector, input token count)."""
    response = get_bedrock().invoke_model(
        modelId=model_id,
        body=json.dumps({"inputText": text, "dimensions": EMBEDDING_DIMENSIONS, "normalize": True}),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    return payload["embedding"], payload.get("inputTextTokenCount", 0)


def embed_many(texts: list[str], model_id: str) -> tuple[list[list[float]], int]:
    """Embed many texts (Titan takes one per call, so run a few in parallel).

    Returns vectors in input order plus the total input tokens.
    """
    if not texts:
        return [], 0
    with ThreadPoolExecutor(max_workers=EMBED_WORKERS) as pool:
        results = list(pool.map(lambda t: embed_text(t, model_id), texts))
    return [vector for vector, _ in results], sum(tokens for _, tokens in results)


# --- S3 Vectors -----------------------------------------------------------------------------


def put_vectors(bucket: str, index: str, vectors: list[dict]) -> int:
    """Write vectors ({"key", "data": {"float32": [...]}, "metadata": {...}}) in batches.

    put_vectors overwrites an existing key, which is what makes re-uploads idempotent.
    Returns the number of API calls made.
    """
    calls = 0
    for batch in _batches(vectors, PUT_BATCH):
        get_s3vectors().put_vectors(vectorBucketName=bucket, indexName=index, vectors=batch)
        calls += 1
    return calls


def delete_vectors(bucket: str, index: str, keys: list[str]) -> None:
    for batch in _batches(keys, DELETE_BATCH):
        get_s3vectors().delete_vectors(vectorBucketName=bucket, indexName=index, keys=batch)


def find_chunk_keys(bucket: str, index: str, source_key: str, start: int = 0) -> list[str]:
    """Find existing vector keys "<source_key>#<n>" for n >= start.

    S3 Vectors cannot list by key prefix, and scanning the whole index per upload would not
    scale. Chunk indexes are contiguous, so instead probe n = start, start+1, ... with
    get_vectors (which returns only keys that exist) and stop at the first empty batch.
    Cost grows with the document, not the index.
    """
    found: list[str] = []
    n = start
    while True:
        candidates = [f"{source_key}#{i}" for i in range(n, n + GET_BATCH)]
        response = get_s3vectors().get_vectors(
            vectorBucketName=bucket,
            indexName=index,
            keys=candidates,
            returnData=False,
            returnMetadata=False,
        )
        existing = {v["key"] for v in response.get("vectors", [])}
        if not existing:
            return found
        found.extend(k for k in candidates if k in existing)
        n += GET_BATCH
