"""Query handler tests: aws_clients functions are patched, so nothing touches AWS or Bedrock."""

import base64
import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.common.providers import GenerationBlockedError, ProviderThrottledError
from app.query import handler as query_handler
from app.query.handler import BLOCKED_ANSWER, IDK_ANSWER, handler

ENV = {
    "VECTOR_BUCKET_NAME": "vb",
    "VECTOR_INDEX_NAME": "idx",
    "AI_PROVIDER": "gemini",
    "EMBEDDING_DIMENSION": "768",
    "GEMINI_EMBEDDING_MODEL": "gemini-embedding-001",
    "GEMINI_GENERATION_MODEL": "gemini-3.5-flash-lite",
    "GEMINI_API_KEY_PARAM": "/rag-demo/gemini-api-key",
}
MODEL = "gemini-3.5-flash-lite"
CONTEXT = SimpleNamespace(aws_request_id="req-1")


def hit(key, distance, heading="Intro", text="some text"):
    return {
        "key": f"{key}#0",
        "distance": distance,
        "metadata": {"source_key": key, "heading": heading, "chunk_text": text},
    }


def event(question=None, raw=None, b64=False):
    body = raw if raw is not None else json.dumps({"question": question})
    if b64:
        body = base64.b64encode(body.encode()).decode()
    return {"body": body, "isBase64Encoded": b64}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def aws():
    """Fake providers (embedder + generator) and a patched vector search: nothing hits a network."""
    embed = MagicMock(return_value=([0.1] * 768, 0))  # embed_query
    generate = MagicMock()  # Generator.generate(system, user, max_tokens)
    embedder = SimpleNamespace(embed_query=embed)
    generator = SimpleNamespace(generate=generate)
    with (
        patch.object(query_handler.providers, "get_embedder", return_value=embedder),
        patch.object(query_handler.providers, "get_generator", return_value=generator),
        patch.object(query_handler.aws_clients, "query_vectors") as q,
    ):
        yield SimpleNamespace(embed=embed, query=q, converse=generate)


def call(ev):
    result = handler(ev, CONTEXT)
    return result["statusCode"], json.loads(result["body"])


@pytest.mark.parametrize(
    ("ev", "message"),
    [
        ({"body": None}, "valid JSON"),
        (event(raw="not json"), "valid JSON"),
        (event(raw="[1, 2]"), "JSON object"),
        (event(raw="{}"), "required"),
        (event(question=""), "required"),
        (event(question="   "), "required"),
        (event(question=123), "required"),
        (event(question="x" * 501), "500"),
    ],
)
def test_invalid_input_returns_400_without_touching_aws(aws, ev, message):
    status, body = call(ev)
    assert status == 400 and message in body["error"]
    aws.embed.assert_not_called()


def test_question_of_exactly_500_chars_is_accepted(aws):
    aws.query.return_value = []
    status, _ = call(event(question="x" * 500))
    assert status == 200


def test_base64_body_is_decoded(aws):
    aws.query.return_value = []
    status, _ = call(event(question="hello", b64=True))
    assert status == 200
    assert aws.embed.call_args.args[0] == "hello"


def test_far_away_results_return_idk_and_skip_the_llm(aws):
    aws.query.return_value = [hit("docs/a.md", 0.9), hit("docs/b.md", 0.95)]

    status, body = call(event("What is X?"))

    assert status == 200
    assert body["answer"] == IDK_ANSWER
    assert [s["key"] for s in body["sources"]] == ["docs/a.md", "docs/b.md"]
    assert body["usage"] == {"input_tokens": 0, "output_tokens": 0}
    aws.converse.assert_not_called()


def test_empty_index_returns_idk(aws):
    aws.query.return_value = []
    status, body = call(event("What is X?"))
    assert status == 200 and body["answer"] == IDK_ANSWER and body["sources"] == []
    aws.converse.assert_not_called()


def test_normal_path_builds_numbered_prompt_and_response_shape(aws):
    aws.query.return_value = [
        hit("docs/b.md", 0.45, "Second", "text B"),
        hit("docs/a.md", 0.2, "First", "text A"),  # out of order on purpose
    ]
    aws.converse.return_value = ("It is A [1].", 120, 15)

    status, body = call(event("What is A?"))

    assert status == 200
    # Sorted best-first, numbered from 1.
    assert body["sources"] == [
        {"n": 1, "key": "docs/a.md", "heading": "First", "distance": 0.2},
        {"n": 2, "key": "docs/b.md", "heading": "Second", "distance": 0.45},
    ]
    assert body["answer"] == "It is A [1]."
    assert body["usage"] == {"input_tokens": 120, "output_tokens": 15}
    assert body["model_id"] == MODEL
    assert isinstance(body["latency_ms"], int)

    system_prompt, user_text, max_tokens = aws.converse.call_args.args
    assert max_tokens == 512
    assert "ONLY" in system_prompt and "[1]" in system_prompt
    assert "[1] First\ntext A" in user_text
    assert "[2] Second\ntext B" in user_text
    assert user_text.endswith("Question: What is A?")


def test_best_distance_exactly_at_threshold_still_answers(aws):
    aws.query.return_value = [hit("docs/a.md", 0.6)]
    aws.converse.return_value = ("ok [1]", 1, 1)
    _, body = call(event("q"))
    assert body["answer"] == "ok [1]"


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "secret internals"}}, "Converse")


def test_bedrock_throttling_returns_503(aws):
    aws.query.return_value = [hit("docs/a.md", 0.2)]
    aws.converse.side_effect = _client_error("ThrottlingException")

    status, body = call(event("q"))

    assert status == 503
    assert "try again" in body["error"]


def test_provider_throttling_returns_503(aws):
    aws.query.return_value = [hit("docs/a.md", 0.2)]
    aws.converse.side_effect = ProviderThrottledError("Gemini rate limit (HTTP 429)")

    status, body = call(event("q"))

    assert status == 503 and "try again" in body["error"]
    assert "Gemini" not in json.dumps(body)


def test_throttled_embedding_also_returns_503(aws):
    aws.embed.side_effect = ProviderThrottledError("429")
    status, _ = call(event("q"))
    assert status == 503
    aws.converse.assert_not_called()


def test_blocked_generation_returns_200_with_clear_message(aws, caplog):
    aws.query.return_value = [hit("docs/a.md", 0.2)]
    aws.converse.side_effect = GenerationBlockedError("SAFETY")

    with caplog.at_level(logging.INFO, logger="rag"):
        status, body = call(event("q"))

    assert status == 200
    assert body["answer"] == BLOCKED_ANSWER
    assert [s["key"] for s in body["sources"]] == ["docs/a.md"]
    assert body["usage"] == {"input_tokens": 0, "output_tokens": 0}
    record = json.loads(caplog.records[-1].message)
    assert record["outcome"] == "blocked" and record["block_reason"] == "SAFETY"


def test_other_errors_return_500_without_leaking_details(aws):
    aws.query.return_value = [hit("docs/a.md", 0.2)]
    aws.converse.side_effect = _client_error("AccessDeniedException")

    status, body = call(event("q"))

    assert status == 500
    assert "secret" not in json.dumps(body) and "AccessDenied" not in json.dumps(body)


def test_missing_config_returns_500(aws, monkeypatch):
    monkeypatch.delenv("GEMINI_GENERATION_MODEL")
    status, body = call(event("q"))
    assert status == 500 and "GEMINI_GENERATION_MODEL" not in json.dumps(body)


def test_structured_log_has_metrics_but_not_the_question(aws, caplog):
    aws.query.return_value = [hit("docs/a.md", 0.2)]
    aws.converse.return_value = ("ans [1]", 50, 9)

    with caplog.at_level(logging.INFO, logger="rag"):
        call(event("my secret question"))

    record = json.loads(caplog.records[-1].message)
    assert record["event"] == "ask"
    assert record["request_id"] == "req-1"
    assert record["question_chars"] == len("my secret question")
    assert record["outcome"] == "answered"
    assert record["top_distance"] == 0.2
    assert (record["input_tokens"], record["output_tokens"]) == (50, 9)
    assert record["sources"] == [{"key": "docs/a.md", "distance": 0.2}]
    assert "latency_ms" in record
    assert "my secret question" not in caplog.text
