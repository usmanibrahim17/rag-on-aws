"""Tests for evals/run_evals.py: metric maths, threshold sweep, skipping, reports. No network."""

import json
import math
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "evals"))

import run_evals as ev  # noqa: E402


def rec(
    id_,
    answerable=True,
    expected=("docs/a.md",),
    keys=(),
    distances=None,
    answer="An answer [1].",
    ok=True,
    difficulty="direct",
    latency=1000,
    tokens=(100, 10),
    model="gemini-x",
):
    """Build a per-question record the way build_record would."""
    distances = (
        list(distances) if distances is not None else [0.2 + 0.05 * i for i in range(len(keys))]
    )
    return {
        "id": id_,
        "question": "q",
        "answerable": answerable,
        "difficulty": difficulty,
        "expected_sources": list(expected) if answerable else [],
        "status": 200 if ok else 503,
        "ok": ok,
        "attempts": 1,
        "answer": answer,
        "source_keys": list(keys),
        "distances": distances,
        "top_distance": min(distances, default=math.inf),
        "refusal": ev.classify_refusal(answer, sum(tokens)) if ok else None,
        "latency_ms": latency,
        "client_ms": latency,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "model_id": model,
        "error": None if ok else "HTTP 503",
    }


# --- the real question set ------------------------------------------------------------------


def test_real_question_set_is_valid_and_balanced():
    questions = ev.load_questions(REPO / "evals" / "questions.jsonl")
    answerable = [q for q in questions if q["answerable"]]
    assert len(questions) == 40 and len(answerable) == 32

    # every expected source is a real doc, and no doc is used more than twice
    uses = Counter(s for q in answerable for s in q["expected_sources"])
    for source in uses:
        assert (REPO / "sample-docs" / source.removeprefix("docs/")).is_file(), source
    assert max(uses.values()) <= 2

    difficulty = Counter(q["difficulty"] for q in questions)
    assert difficulty == {"direct": 20, "understanding": 8, "multi": 4, "unanswerable": 8}
    assert all(len(q["expected_sources"]) == 2 for q in questions if q["difficulty"] == "multi")


# --- loading / validation -------------------------------------------------------------------


def write_lines(tmp_path, *objs):
    path = tmp_path / "q.jsonl"
    path.write_text("\n".join(o if isinstance(o, str) else json.dumps(o) for o in objs))
    return path


GOOD = {"id": "a", "question": "Q?", "expected_sources": ["docs/x.md"], "answerable": True}


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("not json", "invalid JSON"),
        ({"id": "a", "question": "Q?", "answerable": True}, "missing field 'expected_sources'"),
        ({**GOOD, "expected_sources": []}, "needs expected_sources"),
        ({**GOOD, "answerable": False}, "must have expected_sources \\[\\]"),
        ({**GOOD, "question": "  "}, "non-empty"),
        ({**GOOD, "question": "x" * 501}, "500"),
        ({**GOOD, "answerable": "yes"}, "true or false"),
        ({**GOOD, "difficulty": "hard"}, "difficulty"),
    ],
)
def test_invalid_questions_are_rejected_with_a_line_number(tmp_path, bad, message):
    with pytest.raises(ValueError, match=f"line 2: .*{message}"):
        ev.load_questions(write_lines(tmp_path, GOOD | {"id": "ok"}, bad))


def test_duplicate_ids_and_empty_files_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate id"):
        ev.load_questions(write_lines(tmp_path, GOOD, GOOD))
    with pytest.raises(ValueError, match="no questions"):
        ev.load_questions(write_lines(tmp_path, ""))


def test_split_skipped_drops_any_question_expecting_a_skipped_doc():
    qs = [
        {"id": "1", "expected_sources": ["docs/cors.md"]},
        {"id": "2", "expected_sources": ["docs/a.md", "docs/sql-databases.md"]},  # multi-doc
        {"id": "3", "expected_sources": ["docs/a.md"]},
        {"id": "4", "expected_sources": []},  # unanswerable are never skipped
    ]
    kept, skipped = ev.split_skipped(qs, {"docs/cors.md", "docs/sql-databases.md"})
    assert skipped == ["1", "2"] and [q["id"] for q in kept] == ["3", "4"]
    assert ev.split_skipped(qs, set())[1] == []


# --- ranking and retrieval metrics ----------------------------------------------------------


def test_first_hit_rank_uses_first_matching_source():
    keys = ["docs/x.md", "docs/a.md", "docs/a.md"]
    assert ev.first_hit_rank(keys, ["docs/a.md"]) == 2
    assert ev.first_hit_rank(keys, ["docs/b.md"]) is None
    assert ev.first_hit_rank([], ["docs/a.md"]) is None
    # for several expected docs the best (earliest) one counts
    assert ev.first_hit_rank(keys, ["docs/a.md", "docs/x.md"]) == 1


def test_retrieval_metrics_hit_at_k_and_mrr():
    records = [
        rec("r1", keys=["docs/a.md", "docs/x.md"]),  # rank 1
        rec("r2", keys=["docs/x.md", "docs/y.md", "docs/a.md"]),  # rank 3
        rec("r3", keys=["docs/x.md"] * 4 + ["docs/a.md"]),  # rank 5
        rec("r4", keys=["docs/x.md", "docs/y.md"]),  # miss
    ]
    m = ev.retrieval_metrics(records)
    assert m["n"] == 4
    assert m["hit@1"] == pytest.approx(1 / 4)
    assert m["hit@3"] == pytest.approx(2 / 4)
    assert m["hit@5"] == pytest.approx(3 / 4)
    assert m["mrr"] == pytest.approx((1 + 1 / 3 + 1 / 5 + 0) / 4)


def test_retrieval_ignores_unanswerable_and_failed_requests():
    records = [
        rec("a", keys=["docs/a.md"]),
        rec("b", answerable=False, keys=["docs/a.md"]),
        rec("c", ok=False, keys=[]),
    ]
    m = ev.retrieval_metrics(records)
    assert m["n"] == 1 and m["hit@1"] == 1.0


def test_retrieval_with_no_answerable_questions_reports_none():
    m = ev.retrieval_metrics([rec("b", answerable=False)])
    assert m["n"] == 0 and m["hit@1"] is None and m["mrr"] is None


def test_multi_doc_coverage_counts_expected_docs_in_top_five():
    both = rec("m1", expected=("docs/a.md", "docs/b.md"), keys=["docs/a.md", "docs/b.md"])
    half = rec("m2", expected=("docs/a.md", "docs/b.md"), keys=["docs/a.md"] + ["docs/x.md"] * 5)
    single = rec("s", keys=["docs/a.md"])
    m = ev.retrieval_metrics([both, half, single])
    assert m["multi_doc_n"] == 2 and m["multi_doc_coverage@5"] == pytest.approx(0.75)


# --- refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "tokens", "expected"),
    [
        (ev.IDK_ANSWER, 0, "threshold"),  # the Lambda refused: no model call, 0 tokens
        (f"  {ev.IDK_ANSWER}  ", 0, "threshold"),
        (ev.IDK_ANSWER, 371, "llm"),  # same sentence, but the model said it (tokens were spent)
        ("I don't know based on the provided documents, sorry.", 400, "llm"),
        ("I do not know the answer.", 400, "llm"),
        ("Based on the provided documents, there is no information about rate limits.", 400, "llm"),
        ("The sources do not contain details on WebSockets.", 400, "llm"),
        ("Use Path(ge=1) [1].", 400, "none"),
        ("", 0, "none"),
        (None, 0, "none"),
    ],
)
def test_classify_refusal(answer, tokens, expected):
    assert ev.classify_refusal(answer, tokens) == expected


def test_refusal_metrics_separate_correct_wrong_and_who_refused():
    records = [
        rec("u1", answerable=False, answer=ev.IDK_ANSWER, tokens=(0, 0)),  # by threshold
        rec("u2", answerable=False, answer="Sorry, I don't know based on the provided documents."),
        rec("u3", answerable=False, answer="Paris is the capital."),  # should have refused
        rec("a1", answer="I don't know."),  # wrong refusal
        rec("a2"),
        rec("e", answerable=False, ok=False),  # failed request: not counted
    ]
    m = ev.refusal_metrics(records)
    assert (m["unanswerable_n"], m["answerable_n"]) == (3, 2)
    assert (
        m["correct_refusals"] == 2 and m["correct_by_threshold"] == 1 and m["correct_by_llm"] == 1
    )
    assert m["wrong_refusals"] == 1 and m["wrong_refusal_ids"] == ["a1"]


# --- percentiles ----------------------------------------------------------------------------


def test_percentile_nearest_rank():
    assert ev.percentile([], 50) is None
    assert ev.percentile([5], 95) == 5
    assert ev.percentile([4, 1, 3, 2], 50) == 2
    values = list(range(1, 101))
    assert ev.percentile(values, 50) == 50 and ev.percentile(values, 95) == 95


# --- threshold sweep ------------------------------------------------------------------------


def sweep_records():
    """Answerable top distances 0.20 / 0.30 / 0.41; unanswerable 0.35 / 0.47 / 0.55 / no sources."""
    return [
        rec("a1", distances=[0.20], keys=["docs/a.md"]),
        rec("a2", distances=[0.30], keys=["docs/a.md"]),
        rec("a3", distances=[0.41, 0.5], keys=["docs/a.md", "docs/b.md"]),
        rec("u1", answerable=False, distances=[0.35], keys=["docs/x.md"]),
        rec("u2", answerable=False, distances=[0.47], keys=["docs/x.md"]),
        rec("u3", answerable=False, distances=[0.55], keys=["docs/x.md"]),
        rec("u4", answerable=False, keys=[]),  # nothing retrieved: refused at every threshold
        rec("bad", answerable=False, ok=False),  # failed request is ignored
    ]


def test_thresholds_cover_0_3_to_0_6_in_steps_of_0_025():
    assert ev.THRESHOLDS[0] == 0.3 and ev.THRESHOLDS[-1] == 0.6 and len(ev.THRESHOLDS) == 13
    steps = [b - a for a, b in zip(ev.THRESHOLDS, ev.THRESHOLDS[1:], strict=False)]
    assert all(math.isclose(step, 0.025) for step in steps)


def test_sweep_counts_questions_above_each_threshold():
    rows = {r["threshold"]: r for r in ev.sweep_thresholds(sweep_records())}
    # at 0.30 a1 (0.20) and a2 (0.30, not above) survive; a3 (0.41) is wrongly refused
    assert rows[0.3]["answerable_wrongly_refused"] == 1
    assert rows[0.3]["unanswerable_refused"] == 4 and rows[0.3]["unanswerable_total"] == 4
    assert rows[0.45]["unanswerable_refused"] == 3  # u2, u3 and the empty one
    assert rows[0.45]["answerable_wrongly_refused"] == 0
    assert rows[0.6]["unanswerable_refused"] == 1  # only the question with no sources
    assert rows[0.6]["answerable_total"] == 3


def test_suggestion_has_zero_wrong_refusals_and_most_correct_ones():
    best = ev.suggest_threshold(ev.sweep_thresholds(sweep_records()))
    # Safe means a3 (top distance 0.41) is kept, so threshold >= 0.425. At 0.425 and 0.45 the
    # same 3 unanswerable questions (0.47, 0.55, no sources) are refused: the tie goes to 0.45.
    assert best["threshold"] == 0.45
    assert best["unanswerable_refused"] == 3


def test_suggestion_ties_prefer_the_highest_safe_threshold():
    rows = [
        {"threshold": 0.4, "unanswerable_refused": 2, "answerable_wrongly_refused": 0},
        {"threshold": 0.45, "unanswerable_refused": 2, "answerable_wrongly_refused": 0},
        {"threshold": 0.5, "unanswerable_refused": 3, "answerable_wrongly_refused": 1},
    ]
    assert ev.suggest_threshold(rows)["threshold"] == 0.45


def test_suggestion_is_none_when_nothing_safe_or_nothing_refused():
    unsafe = [{"threshold": 0.3, "unanswerable_refused": 3, "answerable_wrongly_refused": 2}]
    assert ev.suggest_threshold(unsafe) is None
    useless = [{"threshold": 0.6, "unanswerable_refused": 0, "answerable_wrongly_refused": 0}]
    assert ev.suggest_threshold(useless) is None


def test_sweep_text_marks_the_deployed_threshold_and_the_suggestion():
    text = ev.format_sweep(ev.sweep_thresholds(sweep_records()), deployed=0.6)
    assert "<- deployed" in text and "Suggested threshold: 0.450" in text


# --- running a question ---------------------------------------------------------------------


API_BODY = {
    "answer": "Use Path [1].",
    "sources": [
        {"n": 1, "key": "docs/a.md", "heading": "H", "distance": 0.31},
        {"n": 2, "key": "docs/b.md", "heading": "H", "distance": 0.4},
    ],
    "latency_ms": 1234,
    "usage": {"input_tokens": 500, "output_tokens": 40},
    "model_id": "gemini-3.5-flash-lite",
}
Q = {"id": "q1", "question": "Q?", "expected_sources": ["docs/a.md"], "answerable": True}


def test_build_record_flattens_the_response():
    r = ev.build_record(Q, 200, API_BODY, 1500, 1)
    assert r["ok"] and r["source_keys"] == ["docs/a.md", "docs/b.md"]
    assert r["top_distance"] == 0.31 and r["latency_ms"] == 1234
    assert (r["input_tokens"], r["output_tokens"]) == (500, 40) and r["refusal"] == "none"


def test_build_record_for_errors_and_empty_sources():
    failed = ev.build_record(Q, 503, {"error": "busy"}, 100, 3)
    assert not failed["ok"] and failed["error"] == "busy" and failed["refusal"] is None
    refused = {
        **API_BODY,
        "sources": [],
        "answer": ev.IDK_ANSWER,
        "usage": {"input_tokens": 0, "output_tokens": 0},  # the Lambda never called the model
    }
    empty = ev.build_record(Q, 200, refused, 90, 1)
    assert empty["top_distance"] == math.inf and empty["refusal"] == "threshold"
    # the same sentence with tokens spent was said by the model, not by the threshold
    said_by_model = ev.build_record(Q, 200, {**refused, "usage": API_BODY["usage"]}, 90, 1)
    assert said_by_model["refusal"] == "llm"


def test_transient_failures_are_retried_then_succeed():
    responses = [(503, {"error": "busy"}, 10), (0, None, 10), (200, API_BODY, 20)]
    sleeps = []
    with patch.object(ev, "post_question", side_effect=responses) as post:
        r = ev.run_question("u", Q, 5, retries=2, retry_pause=7, sleep=sleeps.append)
    assert r["ok"] and r["attempts"] == 3 and post.call_count == 3 and sleeps == [7, 7]


def test_retries_are_bounded_and_other_errors_are_not_retried():
    with patch.object(ev, "post_question", return_value=(503, {"error": "busy"}, 10)) as post:
        r = ev.run_question("u", Q, 5, retries=2, retry_pause=0, sleep=lambda s: None)
    assert not r["ok"] and r["attempts"] == 3 and post.call_count == 3

    with patch.object(ev, "post_question", return_value=(400, {"error": "bad"}, 10)) as post:
        r = ev.run_question("u", Q, 5, retries=2, retry_pause=0, sleep=lambda s: None)
    assert r["attempts"] == 1 and post.call_count == 1


# --- summary and reports --------------------------------------------------------------------


def test_summarize_latency_tokens_and_difficulty_breakdown():
    records = [
        rec("d1", keys=["docs/a.md"], latency=1000, tokens=(100, 10)),
        rec("d2", keys=["docs/x.md"], latency=3000, tokens=(300, 30)),
        rec("m1", expected=("docs/a.md", "docs/b.md"), keys=["docs/b.md"], difficulty="multi"),
        rec("u1", answerable=False, answer=ev.IDK_ANSWER, difficulty="unanswerable"),
        rec("bad", ok=False),
    ]
    s = ev.summarize(records)
    assert s["asked"] == 5 and s["errors"] == [{"id": "bad", "error": "HTTP 503"}]
    assert s["latency_p50_ms"] == 1000 and s["latency_p95_ms"] == 3000
    assert s["avg_input_tokens"] == pytest.approx(150) and s["avg_output_tokens"] == pytest.approx(
        15
    )
    assert s["by_difficulty"]["direct"]["hit@1"] == pytest.approx(0.5)
    assert s["by_difficulty"]["multi"]["hit@1"] == 1.0
    assert s["by_difficulty"]["unanswerable"]["refused"] == 1


CONFIG = {
    "embedding_model": "gemini-embedding-001",
    "chunk_size": 1000,
    "chunk_overlap": 150,
    "top_k": 5,
    "threshold": 0.6,
}


def test_markdown_row_has_every_column_and_marks_partial_runs():
    records = [rec("a", keys=["docs/a.md"]), rec("u", answerable=False, answer=ev.IDK_ANSWER)]
    row = ev.markdown_row(
        records,
        ev.summarize(records),
        CONFIG,
        ["q18", "q19"],
        {"docs/cors.md", "docs/sql-databases.md"},
    )
    cells = [c.strip() for c in row.strip().strip("|").split(" | ")]
    assert len(cells) == ev.RESULTS_HEADER.splitlines()[0].count("|") - 1
    assert "gemini-x" in row and "1000/150" in row and "1/1" in row
    assert "PARTIAL: skipped 2 question(s) (q18, q19)" in row
    assert "docs/cors.md, docs/sql-databases.md" in row


def test_complete_run_row_has_no_partial_note():
    records = [rec("a", keys=["docs/a.md"])]
    row = ev.markdown_row(records, ev.summarize(records), CONFIG, [], set())
    assert "PARTIAL" not in row and row.rstrip().endswith("| - |")


def test_results_file_gets_its_header_once(tmp_path):
    path = tmp_path / "RESULTS.md"
    ev.append_results_row(path, "| row1 |\n")
    ev.append_results_row(path, "| row2 |\n")
    text = path.read_text()
    assert text.count("| Date |") == 1 and text.endswith("| row1 |\n| row2 |\n")


def test_summary_text_lists_skipped_ids_and_errors():
    records = [rec("a", keys=["docs/a.md"]), rec("bad", ok=False)]
    text = ev.format_summary(ev.summarize(records), CONFIG, ["q18"])
    assert "Skipped by --skip-sources (1): q18" in text and "! bad: HTTP 503" in text


# --- end to end (HTTP patched) --------------------------------------------------------------


def fake_post(url, question, timeout, collection=None):
    return 200, API_BODY, 50


def run_main(tmp_path, *extra):
    qfile = tmp_path / "q.jsonl"
    qfile.write_text(
        "\n".join(
            json.dumps(q)
            for q in [
                {**Q, "id": "q1"},
                {**Q, "id": "q2", "expected_sources": ["docs/cors.md"]},
                {"id": "q3", "question": "Capital?", "expected_sources": [], "answerable": False},
            ]
        )
    )
    argv = [
        "--url", "http://x/ask", "--questions", str(qfile), "--pause", "0",
        "--results-dir", str(tmp_path / "results"), "--results-md", str(tmp_path / "RESULTS.md"),
        *extra,
    ]  # fmt: skip
    with patch.object(ev, "post_question", side_effect=fake_post):
        return ev.main(argv)


def test_main_skips_sources_and_records_them_everywhere(tmp_path, capsys):
    code = run_main(tmp_path, "--skip-sources", "docs/cors.md")

    assert code == 0
    out = capsys.readouterr().out
    assert "Skipping 1 by --skip-sources: q2" in out and "Skipped by --skip-sources (1): q2" in out
    saved = json.loads(next((tmp_path / "results").glob("*.json")).read_text())
    assert saved["skipped_ids"] == ["q2"] and saved["partial"] is True
    assert [r["id"] for r in saved["records"]] == ["q1", "q3"]
    assert "PARTIAL: skipped 1 question(s) (q2)" in (tmp_path / "RESULTS.md").read_text()


def test_main_smoke_run_does_not_touch_results_md(tmp_path):
    assert run_main(tmp_path, "--limit", "1") == 0
    assert not (tmp_path / "RESULTS.md").exists()
    assert list((tmp_path / "results").glob("*.json"))
    assert run_main(tmp_path, "--limit", "1", "--append-row") == 0
    assert (tmp_path / "RESULTS.md").exists()


def test_main_rejects_a_bad_question_file(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json")
    assert ev.main(["--url", "http://x", "--questions", str(bad)]) == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_infinite_distance_is_stored_as_null():
    assert ev._json_safe({"a": [math.inf, 1.0], "b": {"c": -math.inf}}) == {
        "a": [None, 1.0],
        "b": {"c": None},
    }


# --- business collection --------------------------------------------------------------------


def test_business_question_set_is_valid_and_points_at_real_files():
    questions = ev.load_questions(REPO / "evals" / "questions_business.jsonl")
    answerable = [q for q in questions if q["answerable"]]
    assert len(questions) == 10 and len(answerable) == 8
    for q in answerable:
        for source in q["expected_sources"]:
            assert source.startswith("docs/business/")
            assert (REPO / "sample-docs" / source.removeprefix("docs/")).is_file(), source
    # at least one question has to be answered from a PDF
    assert any(s.endswith(".pdf") for q in answerable for s in q["expected_sources"])
    assert {q["difficulty"] for q in questions} == {
        "direct",
        "understanding",
        "multi",
        "unanswerable",
    }


class _FakeHttpResponse:
    status = 200

    def read(self):
        return b'{"answer": "ok", "sources": []}'

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize(("collection", "expected"), [("business", "business"), (None, None)])
def test_collection_is_sent_in_the_request_only_when_set(collection, expected):
    with patch.object(ev.urllib.request, "urlopen", return_value=_FakeHttpResponse()) as urlopen:
        ev.post_question("http://x/ask", "Q?", 5, collection)
    sent = json.loads(urlopen.call_args.args[0].data)
    assert sent.get("collection") == expected and sent["question"] == "Q?"
    assert ("collection" in sent) is (collection is not None)


def test_main_passes_the_collection_to_every_request_and_records_it(tmp_path):
    calls = []

    def recording_post(url, question, timeout, collection=None):
        calls.append(collection)
        return 200, API_BODY, 50

    qfile = tmp_path / "q.jsonl"
    qfile.write_text(json.dumps({**Q, "id": "q1"}))
    argv = [
        "--url", "http://x/ask", "--questions", str(qfile), "--pause", "0",
        "--collection", "business",
        "--results-dir", str(tmp_path / "results"), "--results-md", str(tmp_path / "RESULTS.md"),
    ]  # fmt: skip
    with patch.object(ev, "post_question", side_effect=recording_post):
        assert ev.main(argv) == 0

    assert calls == ["business"]
    row = (tmp_path / "RESULTS.md").read_text().splitlines()[-1]
    assert row.startswith("| ") and "| business |" in row
