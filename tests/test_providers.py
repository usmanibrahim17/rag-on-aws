"""Provider tests. urlopen, sleep and SSM are patched, so no network or AWS call is made."""

import io
import json
import math
import urllib.error
from email.message import Message
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.common import providers
from app.common.config import ConfigError
from app.common.providers import (
    BedrockEmbedder,
    BedrockGenerator,
    GeminiEmbedder,
    GeminiGenerator,
    GenerationBlockedError,
    ProviderError,
    ProviderThrottledError,
)

KEY = "super-secret-api-key"


@pytest.fixture(autouse=True)
def isolate():
    """Fresh key cache per test; fake SSM; instant sleeps; no real network."""
    providers.get_gemini_api_key.cache_clear()
    ssm = MagicMock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": KEY}}
    with (
        patch("app.common.aws_clients.get_ssm", return_value=ssm),
        patch("app.common.providers.time.sleep") as sleep,
        patch("app.common.providers.random.uniform", return_value=1.0),
        patch("app.common.providers.urllib.request.urlopen") as urlopen,
    ):
        yield SimpleNamespace(ssm=ssm, sleep=sleep, urlopen=urlopen)
    providers.get_gemini_api_key.cache_clear()


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x", code, "err", headers, io.BytesIO(b"secret body"))


def sent(isolate):
    """Decoded JSON payloads of every request made so far."""
    return [json.loads(c.args[0].data) for c in isolate.urlopen.call_args_list]


def embed_response(n, value=3.0):
    return FakeResponse({"embeddings": [{"values": [value, 4.0]} for _ in range(n)]})


def embedder(dim=2):
    return GeminiEmbedder("models/gemini-embedding-001", dim, "/p")


# --- factory ----------------------------------------------------------------------------------


def settings(provider):
    return SimpleNamespace(
        ai_provider=provider,
        embedding_model_id="m-embed",
        embedding_dimension=768,
        generation_model_id="m-gen",
        gemini_api_key_param="/p",
    )


def test_factory_picks_implementation_from_ai_provider():
    assert isinstance(providers.get_embedder(settings("gemini")), GeminiEmbedder)
    assert isinstance(providers.get_generator(settings("gemini")), GeminiGenerator)
    assert isinstance(providers.get_embedder(settings("bedrock")), BedrockEmbedder)
    assert isinstance(providers.get_generator(settings("bedrock")), BedrockGenerator)


def test_factory_rejects_unknown_provider():
    with pytest.raises(ConfigError, match="openai"):
        providers.get_embedder(settings("openai"))
    with pytest.raises(ConfigError, match="openai"):
        providers.get_generator(settings("openai"))


def test_factory_does_not_touch_aws_or_network(isolate):
    providers.get_embedder(settings("gemini"))
    providers.get_generator(settings("gemini"))
    isolate.ssm.get_parameter.assert_not_called()
    isolate.urlopen.assert_not_called()


def test_models_prefix_is_normalized_in_url_and_payload(isolate):
    isolate.urlopen.return_value = embed_response(1)
    embedder().embed_query("hi")
    request = isolate.urlopen.call_args.args[0]
    assert request.full_url.endswith("/models/gemini-embedding-001:batchEmbedContents")
    assert sent(isolate)[0]["requests"][0]["model"] == "models/gemini-embedding-001"


# --- Gemini embeddings ---------------------------------------------------------------------


def test_250_texts_become_3_requests_in_order(isolate):
    isolate.urlopen.side_effect = lambda req, timeout: FakeResponse(
        {
            "embeddings": [
                {"values": [float(r["content"]["parts"][0]["text"]), 0.0]}
                for r in json.loads(req.data)["requests"]
            ]
        }
    )
    texts = [str(i + 1) for i in range(250)]

    vectors, tokens = embedder().embed_documents(texts)

    assert [len(p["requests"]) for p in sent(isolate)] == [100, 100, 50]
    assert tokens == 0
    assert len(vectors) == 250
    # normalized [n, 0] is [1, 0] for every n: also proves order is preserved per batch
    assert all(v == [1.0, 0.0] for v in vectors)


def test_document_and_query_task_types_and_dimension(isolate):
    isolate.urlopen.return_value = embed_response(2)
    embedder(dim=768).embed_documents(["a", "b"])
    isolate.urlopen.return_value = embed_response(1)
    embedder(dim=768).embed_query("q")

    docs, query = sent(isolate)
    assert {r["taskType"] for r in docs["requests"]} == {"RETRIEVAL_DOCUMENT"}
    assert {r["taskType"] for r in query["requests"]} == {"RETRIEVAL_QUERY"}
    assert all(r["outputDimensionality"] == 768 for r in docs["requests"] + query["requests"])


def test_vectors_are_l2_normalized(isolate):
    isolate.urlopen.return_value = embed_response(1, value=3.0)  # [3, 4] -> [0.6, 0.8]
    vector, _ = embedder().embed_query("q")
    assert vector == pytest.approx([0.6, 0.8])
    assert math.sqrt(sum(x * x for x in vector)) == pytest.approx(1.0)


def test_empty_document_list_makes_no_request(isolate):
    assert embedder().embed_documents([]) == ([], 0)
    isolate.urlopen.assert_not_called()


def test_mismatched_embedding_count_is_an_error(isolate):
    isolate.urlopen.return_value = embed_response(1)
    with pytest.raises(ProviderError, match="1 embeddings for 2"):
        embedder().embed_documents(["a", "b"])


# --- retries -------------------------------------------------------------------------------


def test_429_then_success_retries_with_backoff(isolate):
    isolate.urlopen.side_effect = [http_error(429), http_error(503), embed_response(1)]

    vector, _ = embedder().embed_query("q")

    assert vector == pytest.approx([0.6, 0.8])
    assert isolate.urlopen.call_count == 3
    assert [c.args[0] for c in isolate.sleep.call_args_list] == [1.0, 2.0]  # exponential


def test_retry_after_header_is_honoured_but_capped(isolate):
    isolate.urlopen.side_effect = [http_error(429, "3"), http_error(429, "999"), embed_response(1)]
    embedder().embed_query("q")
    assert [c.args[0] for c in isolate.sleep.call_args_list] == [
        3.0,
        providers.RETRY_AFTER_CAP_SECONDS,
    ]


def test_429_exhausted_raises_throttling_error(isolate):
    isolate.urlopen.side_effect = lambda req, timeout: (_ for _ in ()).throw(http_error(429))

    with pytest.raises(ProviderThrottledError) as exc:
        embedder().embed_query("q")

    assert isolate.urlopen.call_count == providers.MAX_ATTEMPTS
    assert isolate.sleep.call_count == providers.MAX_ATTEMPTS - 1  # no sleep after the last try
    assert "secret body" not in str(exc.value) and KEY not in str(exc.value)


def test_persistent_5xx_is_a_provider_error_not_throttling(isolate):
    isolate.urlopen.side_effect = lambda req, timeout: (_ for _ in ()).throw(http_error(500))
    with pytest.raises(ProviderError) as exc:
        embedder().embed_query("q")
    assert not isinstance(exc.value, ProviderThrottledError)
    assert isolate.urlopen.call_count == providers.MAX_ATTEMPTS


def test_network_errors_are_retried(isolate):
    isolate.urlopen.side_effect = [urllib.error.URLError("boom"), embed_response(1)]
    embedder().embed_query("q")
    assert isolate.urlopen.call_count == 2


def test_other_4xx_fails_immediately_without_leaking(isolate):
    isolate.urlopen.side_effect = http_error(400)
    with pytest.raises(ProviderError, match="HTTP 400") as exc:
        embedder().embed_query("q")
    assert isolate.urlopen.call_count == 1
    assert "secret body" not in str(exc.value)


# --- API key -------------------------------------------------------------------------------


def test_key_is_read_once_from_ssm_and_cached(isolate):
    isolate.urlopen.return_value = embed_response(1)
    e = embedder()
    e.embed_query("a")
    isolate.urlopen.return_value = embed_response(1)
    e.embed_query("b")
    GeminiGenerator("m", "/p")  # constructing must not read the key either

    isolate.ssm.get_parameter.assert_called_once_with(Name="/p", WithDecryption=True)


def test_key_goes_in_header_not_url_or_body(isolate):
    isolate.urlopen.return_value = embed_response(1)
    embedder().embed_query("q")

    request = isolate.urlopen.call_args.args[0]
    assert request.get_header("X-goog-api-key") == KEY
    assert KEY not in request.full_url
    assert KEY not in request.data.decode()


def test_key_is_never_logged(isolate, caplog):
    isolate.urlopen.side_effect = [http_error(429), embed_response(1)]
    with caplog.at_level(0):
        embedder().embed_query("q")
    assert KEY not in caplog.text


# --- Gemini generation ---------------------------------------------------------------------


def gen_response(text="Answer [1]", **usage):
    return FakeResponse(
        {
            "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, **usage},
        }
    )


def test_generate_sends_system_instruction_and_config_and_returns_usage(isolate):
    isolate.urlopen.return_value = gen_response()

    text, in_tokens, out_tokens = GeminiGenerator("models/gemini-3.5-flash-lite", "/p").generate(
        "SYSTEM", "USER", 300
    )

    assert (text, in_tokens, out_tokens) == ("Answer [1]", 100, 20)
    request = isolate.urlopen.call_args.args[0]
    assert request.full_url.endswith("/models/gemini-3.5-flash-lite:generateContent")
    payload = sent(isolate)[0]
    assert payload["systemInstruction"]["parts"][0]["text"] == "SYSTEM"
    assert payload["contents"][0]["parts"][0]["text"] == "USER"
    assert payload["generationConfig"] == {
        "temperature": providers.GENERATION_TEMPERATURE,
        "maxOutputTokens": 300,
    }


def test_thinking_tokens_count_as_output_and_thought_parts_are_dropped(isolate):
    isolate.urlopen.return_value = FakeResponse(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "reasoning...", "thought": True}, {"text": "Final."}]
                    }
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "thoughtsTokenCount": 40,
            },
        }
    )
    text, _, out_tokens = GeminiGenerator("m", "/p").generate("s", "u", 100)
    assert text == "Final." and out_tokens == 45


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "SAFETY"),
        ({"candidates": [{"finishReason": "SAFETY"}]}, "SAFETY"),
        ({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}, "MAX_TOKENS"),
        ({}, "no candidates"),
    ],
)
def test_response_without_text_raises_blocked_error(isolate, payload, reason):
    isolate.urlopen.return_value = FakeResponse(payload)
    with pytest.raises(GenerationBlockedError) as exc:
        GeminiGenerator("m", "/p").generate("s", "u", 100)
    assert exc.value.reason == reason


# --- Bedrock wrappers ----------------------------------------------------------------------


def test_bedrock_wrappers_delegate_to_the_existing_clients():
    with (
        patch("app.common.aws_clients.embed_many", return_value=([[1.0]], 4)) as many,
        patch("app.common.aws_clients.embed_text", return_value=([2.0], 3)) as one,
        patch("app.common.aws_clients.converse", return_value=("a", 1, 2)) as converse,
    ):
        e = BedrockEmbedder("titan", 1024)
        assert e.embed_documents(["x"]) == ([[1.0]], 4)
        assert e.embed_query("q") == ([2.0], 3)
        assert BedrockGenerator("nova").generate("s", "u", 50) == ("a", 1, 2)

    many.assert_called_once_with(["x"], "titan", 1024)
    one.assert_called_once_with("q", "titan", 1024)
    converse.assert_called_once_with("nova", "s", "u", 50)
