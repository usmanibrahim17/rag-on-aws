import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import estimate_cost as ec  # noqa: E402


def write_run(tmp_path, records, name="run.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"records": records}))
    return path


def rec(in_tokens, out_tokens, latency_ms=1000, ok=True):
    return {
        "ok": ok,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "latency_ms": latency_ms,
    }


def test_averages_cover_all_valid_questions_including_zero_token_refusals(tmp_path):
    run = write_run(tmp_path, [rec(1000, 100), rec(0, 0, 200), rec(500, 50, ok=False)])
    avg = ec.averages([run])
    assert avg["questions"] == 2  # the failed request is ignored
    assert avg["input_tokens"] == 500 and avg["output_tokens"] == 50
    assert avg["latency_s"] == pytest.approx(0.6)


def test_averages_combine_several_runs(tmp_path):
    a = write_run(tmp_path, [rec(100, 10)], "a.json")
    b = write_run(tmp_path, [rec(300, 30)], "b.json")
    assert ec.averages([a, b])["input_tokens"] == 200


def test_per_1000_questions_arithmetic():
    # 1,000 questions x 1,000 tokens = 1M tokens, so the price per 1M tokens comes back as is
    assert ec.per_1000(1000, 0.30) == pytest.approx(0.30)
    assert ec.per_1000(100, 2.50) == pytest.approx(0.25)


def test_gemini_paid_cost_for_a_known_profile():
    avg = {"questions": 10, "input_tokens": 1000, "output_tokens": 100, "latency_s": 1.0}
    c = ec.costs(avg)
    # 1M input @ 0.30 + 0.1M output @ 2.50 + 22.5k embedding tokens @ 0.15
    expected_models = 0.30 + 0.25 + 22.5 * 1000 / 1_000_000 * 0.15 / 1000 * 1000
    assert c["gemini_paid_models"] == pytest.approx(expected_models, rel=1e-3)
    # Bedrock (Nova Lite + Titan) is cheaper than Gemini paid for the same tokens
    assert c["bedrock_models"] < c["gemini_paid_models"]
    assert c["gemini_free_models"] == 0.0
    assert c["gemini_free_total"] == pytest.approx(c["infrastructure"])


def test_infrastructure_cost_scales_with_latency():
    fast, slow = ec.infrastructure_cost(0.5), ec.infrastructure_cost(2.0)
    assert slow > fast > 0
    # API Gateway + requests + vector query fees are the fixed part
    fixed = ec.infrastructure_cost(0.0)
    assert fixed == pytest.approx(0.001 + 0.0002 + 0.0025)


def test_main_prints_a_summary(tmp_path, capsys):
    run = write_run(tmp_path, [rec(900, 100, 1500)] * 4)
    assert ec.main([str(run)]) == 0
    out = capsys.readouterr().out
    assert "Gemini free tier" in out and "Bedrock Nova Lite" in out and "4 questions" in out
    assert ec.main([]) == 2
