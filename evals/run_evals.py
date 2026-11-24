"""Run the eval questions against the live /ask API and report retrieval + refusal metrics.

    python evals/run_evals.py --url https://<api-id>.execute-api.us-east-1.amazonaws.com/ask

One question at a time, with a pause between them (Gemini free-tier limits). Standard library
only. See evals/README.md for the question format and how to read the output.

Outputs: a terminal summary, evals/results/<timestamp>.json (git-ignored, every per-question
detail), and one appended row in evals/RESULTS.md (skipped for --limit/--ids smoke runs).
"""

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IDK_ANSWER = "I don't know based on the provided documents."  # must match app/query/handler.py
# Phrases the model uses when it refuses on its own (the system prompt asks it to say this).
LLM_REFUSAL_MARKERS = (
    "don't know",
    "don’t know",
    "do not know",
    "no information",
    "not contain",
    "doesn't contain",
    "does not mention",
    "doesn't mention",
    "not mentioned",
    "not covered",
    "cannot find",
    "can't find",
)
THRESHOLDS = [round(0.30 + 0.025 * i, 3) for i in range(13)]  # 0.300 ... 0.600
DIFFICULTIES = ("direct", "understanding", "multi", "unanswerable")

RESULTS_HEADER = (
    "| Date | Collection | Embedding model | Generation model | Chunk size/overlap | top_k "
    "| Threshold "
    "| Questions (ans/unans) | hit@1 | hit@3 | hit@5 | MRR | Correct refusals | Wrong refusals "
    "| p50 ms | p95 ms | Avg in/out tokens | Notes |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)


# --- Loading --------------------------------------------------------------------------------


def load_questions(path: Path) -> list[dict]:
    """Read and validate the JSONL question file; raise ValueError naming the bad line."""
    questions, seen = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            q = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path.name} line {number}: invalid JSON ({exc})") from None
        where = f"{path.name} line {number}"
        if not isinstance(q, dict):
            raise ValueError(f"{where}: expected a JSON object")
        for field in ("id", "question", "expected_sources", "answerable"):
            if field not in q:
                raise ValueError(f"{where}: missing field {field!r}")
        if not isinstance(q["question"], str) or not q["question"].strip():
            raise ValueError(f"{where}: question must be a non-empty string")
        if len(q["question"]) > 500:
            raise ValueError(f"{where}: question is longer than the API limit of 500 chars")
        if not isinstance(q["answerable"], bool):
            raise ValueError(f"{where}: answerable must be true or false")
        sources = q["expected_sources"]
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            raise ValueError(f"{where}: expected_sources must be a list of strings")
        if q["answerable"] and not sources:
            raise ValueError(f"{where}: an answerable question needs expected_sources")
        if not q["answerable"] and sources:
            raise ValueError(f"{where}: an unanswerable question must have expected_sources []")
        if q.get("difficulty") is not None and q["difficulty"] not in DIFFICULTIES:
            raise ValueError(f"{where}: difficulty must be one of {', '.join(DIFFICULTIES)}")
        if q["id"] in seen:
            raise ValueError(f"{where}: duplicate id {q['id']!r}")
        seen.add(q["id"])
        questions.append(q)
    if not questions:
        raise ValueError(f"{path.name}: no questions found")
    return questions


def split_skipped(questions: list[dict], skip_sources: set[str]) -> tuple[list[dict], list[str]]:
    """Drop questions whose expected_sources include a skipped doc; return (kept, skipped ids)."""
    kept, skipped = [], []
    for q in questions:
        if skip_sources & set(q["expected_sources"]):
            skipped.append(q["id"])
        else:
            kept.append(q)
    return kept, skipped


# --- Calling the API ------------------------------------------------------------------------


def post_question(
    url: str, question: str, timeout: float, collection: str | None = None
) -> tuple[int, dict | None, int]:
    """POST one question. Returns (HTTP status or 0 on network failure, parsed JSON or None, ms)."""
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {"question": question, **({"collection": collection} if collection else {})}
        ).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError):
        return 0, None, round((time.monotonic() - started) * 1000)
    elapsed = round((time.monotonic() - started) * 1000)
    try:
        return status, json.loads(raw), elapsed
    except ValueError:
        return status, None, elapsed


def classify_refusal(answer: str, total_tokens: int) -> str:
    """Return "threshold", "llm" or "none".

    "threshold" = the Lambda refused before calling the LLM; "llm" = the model refused.
    The model is told to say the exact same sentence, so the text alone can't tell them apart:
    a real threshold refusal never calls the model, so it reports 0 tokens.
    """
    text = (answer or "").strip()
    if text == IDK_ANSWER:
        return "threshold" if total_tokens == 0 else "llm"
    lowered = text.lower()
    if any(marker in lowered for marker in LLM_REFUSAL_MARKERS):
        return "llm"
    return "none"


def build_record(q: dict, status: int, body: dict | None, client_ms: int, attempts: int) -> dict:
    """Flatten one API response into the per-question record that is stored and scored."""
    body = body or {}
    sources = body.get("sources") or []
    distances = [s.get("distance") for s in sources]
    usage = body.get("usage") or {}
    ok = status == 200 and "answer" in body
    return {
        "id": q["id"],
        "question": q["question"],
        "answerable": q["answerable"],
        "difficulty": q.get("difficulty"),
        "expected_sources": q["expected_sources"],
        "status": status,
        "ok": ok,
        "attempts": attempts,
        "answer": body.get("answer"),
        "source_keys": [s.get("key") for s in sources],
        "distances": distances,
        "top_distance": min((d for d in distances if d is not None), default=math.inf),
        "refusal": classify_refusal(
            body.get("answer", ""), usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        )
        if ok
        else None,
        "latency_ms": body.get("latency_ms", client_ms),
        "client_ms": client_ms,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "model_id": body.get("model_id"),
        "error": None if ok else (body.get("error") or f"HTTP {status}"),
    }


def run_question(url, q, timeout, retries, retry_pause, sleep=time.sleep, collection=None) -> dict:
    """Ask one question, retrying transient failures (network, 429, 503)."""
    attempts = 0
    while True:
        attempts += 1
        status, body, client_ms = post_question(url, q["question"], timeout, collection)
        transient = status in (0, 429, 503)
        if not transient or attempts > retries:
            return build_record(q, status, body, client_ms, attempts)
        sleep(retry_pause)


# --- Metrics --------------------------------------------------------------------------------


def first_hit_rank(source_keys: list[str], expected: list[str]) -> int | None:
    """1-based position of the first returned source that is in `expected`, else None."""
    for rank, key in enumerate(source_keys, start=1):
        if key in expected:
            return rank
    return None


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in 0..100); None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[index]


def retrieval_metrics(records: list[dict]) -> dict:
    """hit@1/3/5 and MRR over the answerable records that got a valid response."""
    answerable = [r for r in records if r["ok"] and r["answerable"]]
    ranks = [first_hit_rank(r["source_keys"], r["expected_sources"]) for r in answerable]
    n = len(answerable)

    def hit(k):
        return sum(1 for rank in ranks if rank is not None and rank <= k) / n if n else None

    mrr = sum(1 / rank for rank in ranks if rank) / n if n else None

    # For questions needing several docs: share of expected docs found in the top 5 sources.
    coverage = [
        len(set(r["expected_sources"]) & set(r["source_keys"][:5])) / len(r["expected_sources"])
        for r in answerable
        if len(r["expected_sources"]) > 1
    ]
    return {
        "n": n,
        "hit@1": hit(1),
        "hit@3": hit(3),
        "hit@5": hit(5),
        "mrr": mrr,
        "multi_doc_coverage@5": sum(coverage) / len(coverage) if coverage else None,
        "multi_doc_n": len(coverage),
    }


def refusal_metrics(records: list[dict]) -> dict:
    ok = [r for r in records if r["ok"]]
    unanswerable = [r for r in ok if not r["answerable"]]
    answerable = [r for r in ok if r["answerable"]]
    correct = [r for r in unanswerable if r["refusal"] != "none"]
    wrong = [r for r in answerable if r["refusal"] != "none"]
    return {
        "unanswerable_n": len(unanswerable),
        "answerable_n": len(answerable),
        "correct_refusals": len(correct),
        "correct_by_threshold": sum(1 for r in correct if r["refusal"] == "threshold"),
        "correct_by_llm": sum(1 for r in correct if r["refusal"] == "llm"),
        "wrong_refusals": len(wrong),
        "wrong_refusal_ids": [r["id"] for r in wrong],
    }


def sweep_thresholds(records: list[dict], thresholds=THRESHOLDS) -> list[dict]:
    """For each threshold: how many unanswerable / answerable questions would be refused.

    A question is refused before the LLM when its top distance is above the threshold; a
    question with no sources at all counts as refused at every threshold.
    """
    ok = [r for r in records if r["ok"]]
    unanswerable = [r["top_distance"] for r in ok if not r["answerable"]]
    answerable = [r["top_distance"] for r in ok if r["answerable"]]
    return [
        {
            "threshold": t,
            "unanswerable_refused": sum(1 for d in unanswerable if d > t),
            "unanswerable_total": len(unanswerable),
            "answerable_wrongly_refused": sum(1 for d in answerable if d > t),
            "answerable_total": len(answerable),
        }
        for t in thresholds
    ]


def suggest_threshold(rows: list[dict]) -> dict | None:
    """Zero wrongly-refused answerable questions, then the most correct refusals.

    Ties go to the highest threshold (the least aggressive one). Returns None when no
    threshold in the range is safe or when the safe ones refuse nothing useful.
    """
    safe = [r for r in rows if r["answerable_wrongly_refused"] == 0]
    if not safe:
        return None
    best = max(r["unanswerable_refused"] for r in safe)
    if best == 0:
        return None
    top = max(r["threshold"] for r in safe if r["unanswerable_refused"] == best)
    row = next(r for r in rows if r["threshold"] == top)
    return {"threshold": top, "unanswerable_refused": best, "row": row}


def summarize(records: list[dict]) -> dict:
    ok = [r for r in records if r["ok"]]
    latencies = [r["latency_ms"] for r in ok]
    by_difficulty = {}
    for name in DIFFICULTIES:
        group = [r for r in records if r["difficulty"] == name]
        if not group:
            continue
        entry = {"n": len(group), "errors": sum(1 for r in group if not r["ok"])}
        if name == "unanswerable":
            entry["refused"] = refusal_metrics(group)["correct_refusals"]
        else:
            entry.update(retrieval_metrics(group))
        by_difficulty[name] = entry
    return {
        "asked": len(records),
        "errors": [{"id": r["id"], "error": r["error"]} for r in records if not r["ok"]],
        "retrieval": retrieval_metrics(records),
        "refusals": refusal_metrics(records),
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "avg_input_tokens": sum(r["input_tokens"] for r in ok) / len(ok) if ok else None,
        "avg_output_tokens": sum(r["output_tokens"] for r in ok) / len(ok) if ok else None,
        "by_difficulty": by_difficulty,
        "sweep": sweep_thresholds(records),
    }


# --- Reporting ------------------------------------------------------------------------------


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value, digits=0) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def format_summary(summary: dict, config: dict, skipped_ids: list[str]) -> str:
    r, ref = summary["retrieval"], summary["refusals"]
    lines = [
        "",
        "=== Eval summary ===",
        f"Questions asked: {summary['asked']}  (errors: {len(summary['errors'])})",
    ]
    if skipped_ids:
        lines.append(f"Skipped by --skip-sources ({len(skipped_ids)}): {', '.join(skipped_ids)}")
    for err in summary["errors"]:
        lines.append(f"  ! {err['id']}: {err['error']}")
    lines += [
        "",
        f"Retrieval (answerable, n={r['n']}):",
        f"  hit@1 {_pct(r['hit@1'])}   hit@3 {_pct(r['hit@3'])}   hit@5 {_pct(r['hit@5'])}"
        f"   MRR {_num(r['mrr'], 3)}",
    ]
    if r["multi_doc_n"]:
        lines.append(
            f"  multi-doc coverage@5: {_pct(r['multi_doc_coverage@5'])} (n={r['multi_doc_n']})"
        )
    lines += [
        "",
        "Refusals:",
        f"  correct (unanswerable refused): {ref['correct_refusals']}/{ref['unanswerable_n']}"
        f"  [by threshold: {ref['correct_by_threshold']}, by LLM: {ref['correct_by_llm']}]",
        f"  wrong (answerable refused):     {ref['wrong_refusals']}/{ref['answerable_n']}"
        + (f"  {ref['wrong_refusal_ids']}" if ref["wrong_refusal_ids"] else ""),
        "",
        f"Latency: p50 {_num(summary['latency_p50_ms'])} ms, "
        f"p95 {_num(summary['latency_p95_ms'])} ms",
        f"Tokens (avg per question): in {_num(summary['avg_input_tokens'], 1)}, "
        f"out {_num(summary['avg_output_tokens'], 1)}",
    ]
    if summary["by_difficulty"]:
        lines += ["", "By difficulty:"]
        for name, d in summary["by_difficulty"].items():
            if name == "unanswerable":
                lines.append(f"  {name:<14} n={d['n']:<3} refused {d['refused']}/{d['n']}")
            else:
                lines.append(
                    f"  {name:<14} n={d['n']:<3} hit@1 {_pct(d['hit@1'])}  hit@5 {_pct(d['hit@5'])}"
                    f"  MRR {_num(d['mrr'], 3)}"
                )
    lines += ["", format_sweep(summary["sweep"], config["threshold"])]
    return "\n".join(lines)


def format_sweep(rows: list[dict], deployed: float) -> str:
    lines = [
        "Threshold sweep (refuse before the LLM when top distance > threshold):",
        "  threshold | unanswerable refused | answerable wrongly refused",
        "  ----------+----------------------+---------------------------",
    ]
    for row in rows:
        marker = "  <- deployed" if math.isclose(row["threshold"], deployed) else ""
        lines.append(
            f"  {row['threshold']:<9.3f} | "
            f"{row['unanswerable_refused']:>3}/{row['unanswerable_total']:<17} | "
            f"{row['answerable_wrongly_refused']:>3}/{row['answerable_total']:<24}{marker}"
        )
    best = suggest_threshold(rows)
    if best:
        lines.append(
            f"Suggested threshold: {best['threshold']:.3f} (0 wrong refusals, "
            f"{best['unanswerable_refused']}/{best['row']['unanswerable_total']} "
            "unanswerable refused)."
        )
        lines.append(
            "  Small eval set: treat this as a starting point and keep a margin, don't tune to it."
        )
    else:
        lines.append(
            "No threshold in 0.300-0.600 refuses any unanswerable question without also "
            "wrongly refusing an answerable one."
        )
    return "\n".join(lines)


def markdown_row(records, summary, config, skipped_ids, skip_sources) -> str:
    r, ref = summary["retrieval"], summary["refusals"]
    models = sorted({rec["model_id"] for rec in records if rec["model_id"]})
    notes = []
    if skipped_ids:
        notes.append(
            f"PARTIAL: skipped {len(skipped_ids)} question(s) ({', '.join(skipped_ids)}) "
            f"expecting {', '.join(sorted(skip_sources))}"
        )
    if summary["errors"]:
        notes.append(f"{len(summary['errors'])} request error(s)")
    cells = [
        date.today().isoformat(),
        config.get("collection") or "all",
        config["embedding_model"],
        ", ".join(models) or "n/a",
        f"{config['chunk_size']}/{config['chunk_overlap']}",
        str(config["top_k"]),
        f"{config['threshold']:g}",
        f"{ref['answerable_n']}/{ref['unanswerable_n']}",
        _pct(r["hit@1"]),
        _pct(r["hit@3"]),
        _pct(r["hit@5"]),
        _num(r["mrr"], 3),
        f"{ref['correct_refusals']}/{ref['unanswerable_n']} "
        f"(threshold {ref['correct_by_threshold']}, LLM {ref['correct_by_llm']})",
        f"{ref['wrong_refusals']}/{ref['answerable_n']}",
        _num(summary["latency_p50_ms"]),
        _num(summary["latency_p95_ms"]),
        f"{_num(summary['avg_input_tokens'])}/{_num(summary['avg_output_tokens'])}",
        "; ".join(notes) or "-",
    ]
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |\n"


def append_results_row(path: Path, row: str) -> None:
    """Append `row` to RESULTS.md, writing the title and table header first if the file is new."""
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        path.write_text(
            "# Eval results\n\nOne row per run (newest last). Server settings are what was "
            "passed to run_evals.py, not read from the API.\n\n" + RESULTS_HEADER,
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(row)


# --- CLI ------------------------------------------------------------------------------------


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Run RAG evals against the live /ask endpoint.")
    p.add_argument("--url", required=True, help="ask_url from terraform output (…/ask)")
    p.add_argument("--questions", type=Path, default=ROOT / "questions.jsonl")
    p.add_argument(
        "--collection",
        help="restrict retrieval to this collection (e.g. fastapi, business); default: all",
    )
    p.add_argument("--pause", type=float, default=3.0, help="seconds between questions")
    p.add_argument("--timeout", type=float, default=60.0, help="per-request timeout (s)")
    p.add_argument("--retries", type=int, default=2, help="retries on 429/503/network errors")
    p.add_argument("--retry-pause", type=float, default=10.0, help="seconds before a retry")
    p.add_argument("--limit", type=int, help="only the first N questions (smoke run)")
    p.add_argument("--ids", help="comma-separated question ids to run (smoke run)")
    p.add_argument(
        "--skip-sources", default="", help="comma-separated docs; skip questions expecting any"
    )
    p.add_argument(
        "--append-row", action="store_true", help="append to RESULTS.md even for --limit/--ids"
    )
    p.add_argument("--results-dir", type=Path, default=ROOT / "results")
    p.add_argument("--results-md", type=Path, default=ROOT / "RESULTS.md")
    # Server-side settings: the API doesn't report them, so they are only recorded.
    p.add_argument("--embedding-model", default="gemini-embedding-001")
    p.add_argument("--chunk-size", type=int, default=1000)
    p.add_argument("--chunk-overlap", type=int, default=150)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--threshold", type=float, default=0.6, help="deployed DISTANCE_THRESHOLD")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        questions = load_questions(args.questions)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    skip_sources = {s.strip() for s in args.skip_sources.split(",") if s.strip()}
    questions, skipped_ids = split_skipped(questions, skip_sources)
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        questions = [q for q in questions if q["id"] in wanted]
    if args.limit:
        questions = questions[: args.limit]
    smoke = bool(args.ids or args.limit)
    if not questions:
        print("error: no questions to run after filtering", file=sys.stderr)
        return 2

    config = {
        "embedding_model": args.embedding_model,
        "chunk_size": args.chunk_size,
        "chunk_overlap": args.chunk_overlap,
        "top_k": args.top_k,
        "threshold": args.threshold,
        "collection": args.collection,
    }
    print(
        f"Running {len(questions)} question(s) against {args.url} "
        f"(pause {args.pause}s, collection: {args.collection or 'all'})"
    )
    if skipped_ids:
        print(f"Skipping {len(skipped_ids)} by --skip-sources: {', '.join(skipped_ids)}")

    records = []
    try:
        for i, q in enumerate(questions, start=1):
            record = run_question(
                args.url,
                q,
                args.timeout,
                args.retries,
                args.retry_pause,
                collection=args.collection,
            )
            records.append(record)
            detail = (
                f"top {_num(record['top_distance'], 3)} refusal={record['refusal']} "
                f"{record['latency_ms']}ms"
                if record["ok"]
                else f"ERROR {record['error']}"
            )
            print(f"[{i:>2}/{len(questions)}] {q['id']} {record['status']} {detail}")
            if i < len(questions):
                time.sleep(args.pause)
    except KeyboardInterrupt:
        print("\nInterrupted: summarizing the questions answered so far.")

    if not records:
        return 1
    summary = summarize(records)
    print(format_summary(summary, config, skipped_ids))

    args.results_dir.mkdir(parents=True, exist_ok=True)
    out = args.results_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    payload = {
        "config": config,
        "url": args.url,
        "skip_sources": sorted(skip_sources),
        "skipped_ids": skipped_ids,
        "partial": bool(skipped_ids) or smoke,
        "summary": summary,
        "records": records,
    }
    # math.inf (no sources) is not valid JSON, so store it as null.
    out.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results: {out}")

    if smoke and not args.append_row:
        print("Smoke run (--limit/--ids): RESULTS.md not updated (use --append-row to force).")
    else:
        append_results_row(
            args.results_md, markdown_row(records, summary, config, skipped_ids, skip_sources)
        )
        print(f"Appended a row to {args.results_md}")
    return 0 if not summary["errors"] else 1


def _json_safe(value):
    """Replace infinities (a question with no sources) by None so the file is valid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


if __name__ == "__main__":
    sys.exit(main())
