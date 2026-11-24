"""Estimate the cost per 1,000 questions from the token usage logged by the eval runs.

    python scripts/estimate_cost.py evals/results/<run>.json [more runs...]

Averages input/output tokens over ALL questions of the given runs (refusals made by the
distance threshold cost 0 model tokens, so they pull the average down, as in real traffic) and
multiplies by the published prices below. Prices are USD, copied from the official sources on
the dates given; update them (and PRICES_AS_OF) when you re-check.
"""

import json
import sys
from pathlib import Path

PRICES_AS_OF = "2026-11-25"

# Gemini Developer API, https://ai.google.dev/gemini-api/docs/pricing (Standard tier, per 1M tokens)
GEMINI_FLASH_LITE_IN = 0.30
GEMINI_FLASH_LITE_OUT = 2.50  # the page says output includes thinking tokens
GEMINI_EMBEDDING_IN = 0.15
# Amazon Bedrock on-demand, us-east-1, from the AWS Price List API (published 2026-11-23),
# which backs https://aws.amazon.com/bedrock/pricing/ (per 1M tokens)
NOVA_LITE_IN = 0.06  # $0.00006 per 1K input tokens
NOVA_LITE_OUT = 0.24  # $0.00024 per 1K output tokens
TITAN_EMBED_V2_IN = 0.02  # $0.00002 per 1K input tokens
# AWS infrastructure, identical for both providers
API_GATEWAY_HTTP_PER_MILLION = 1.00  # https://aws.amazon.com/api-gateway/pricing/
LAMBDA_PER_GB_SECOND = 0.0000166667  # https://aws.amazon.com/lambda/pricing/
LAMBDA_PER_MILLION_REQUESTS = 0.20
S3_VECTORS_QUERY_PER_MILLION = 2.50  # https://aws.amazon.com/s3/pricing/ (S3 Vectors)
# S3 Vectors "data processed" ($0.004/TB) and "data returned" ($0.01/GB) are well under
# $0.0001 per 1,000 questions for an index this small, so they are left out.

LAMBDA_MEMORY_GB = 0.5  # the query Lambda is configured with 512 MB
QUESTION_CHARS = 90  # typical question length; ~4 characters per token for embeddings
QUESTIONS = 1000


def per_1000(tokens_per_question: float, price_per_million: float) -> float:
    return tokens_per_question * QUESTIONS * price_per_million / 1_000_000


def averages(result_files: list[Path]) -> dict:
    """Mean tokens and latency per question over all valid records of the given runs."""
    records = []
    for path in result_files:
        records += [r for r in json.loads(path.read_text(encoding="utf-8"))["records"] if r["ok"]]
    if not records:
        raise SystemExit("no valid records in the given result files")
    n = len(records)
    return {
        "questions": n,
        "input_tokens": sum(r["input_tokens"] for r in records) / n,
        "output_tokens": sum(r["output_tokens"] for r in records) / n,
        "latency_s": sum(r["latency_ms"] for r in records) / n / 1000,
    }


def infrastructure_cost(avg_latency_s: float) -> float:
    api = API_GATEWAY_HTTP_PER_MILLION * QUESTIONS / 1_000_000
    lam = avg_latency_s * LAMBDA_MEMORY_GB * QUESTIONS * LAMBDA_PER_GB_SECOND
    lam += LAMBDA_PER_MILLION_REQUESTS * QUESTIONS / 1_000_000
    vectors = S3_VECTORS_QUERY_PER_MILLION * QUESTIONS / 1_000_000
    return api + lam + vectors


def costs(avg: dict) -> dict:
    embed_tokens = QUESTION_CHARS / 4  # Gemini does not report embedding tokens
    infra = infrastructure_cost(avg["latency_s"])
    gemini_paid = (
        per_1000(avg["input_tokens"], GEMINI_FLASH_LITE_IN)
        + per_1000(avg["output_tokens"], GEMINI_FLASH_LITE_OUT)
        + per_1000(embed_tokens, GEMINI_EMBEDDING_IN)
    )
    bedrock = (
        per_1000(avg["input_tokens"], NOVA_LITE_IN)
        + per_1000(avg["output_tokens"], NOVA_LITE_OUT)
        + per_1000(embed_tokens, TITAN_EMBED_V2_IN)
    )
    return {
        "infrastructure": infra,
        "gemini_free_models": 0.0,
        "gemini_paid_models": gemini_paid,
        "bedrock_models": bedrock,
        "gemini_free_total": infra,
        "gemini_paid_total": infra + gemini_paid,
        "bedrock_total": infra + bedrock,
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    avg = averages([Path(a) for a in argv])
    c = costs(avg)
    print(f"Prices as of {PRICES_AS_OF}; {avg['questions']} questions measured")
    print(
        f"Average per question: {avg['input_tokens']:.0f} input / "
        f"{avg['output_tokens']:.0f} output tokens, {avg['latency_s']:.2f} s"
    )
    print(f"\nCost per {QUESTIONS:,} questions (USD):")
    print(f"  AWS infrastructure (API Gateway + Lambda + S3 Vectors): {c['infrastructure']:.4f}")
    rows = (
        ("(a) Gemini free tier", 0.0, c["gemini_free_total"]),
        ("(b) Gemini paid (Flash-Lite)", c["gemini_paid_models"], c["gemini_paid_total"]),
        ("(c) Bedrock Nova Lite + Titan", c["bedrock_models"], c["bedrock_total"]),
    )
    for label, models, total in rows:
        print(f"  {label:<30} models {models:.4f}   total {total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
