import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_index  # noqa: E402


class FakeClient:
    """Serves vectors in pages of `page_size`, like list_vectors with nextToken."""

    def __init__(self, vectors, page_size):
        self.vectors, self.page_size, self.calls = vectors, page_size, []

    def list_vectors(self, **kwargs):
        self.calls.append(kwargs)
        start = int(kwargs.get("nextToken", 0))
        end = start + self.page_size
        page = {"vectors": self.vectors[start:end]}
        if end < len(self.vectors):
            page["nextToken"] = str(end)
        return page


def vec(source, i):
    return {"key": f"{source}#{i}", "metadata": {"source_key": source, "chunk_text": "x"}}


def test_iter_vectors_follows_pagination_and_skips_embeddings():
    vectors = [vec("docs/a.md", i) for i in range(5)]
    client = FakeClient(vectors, page_size=2)

    assert list(check_index.iter_vectors(client, "b", "i")) == vectors
    assert len(client.calls) == 3
    assert all(c["returnData"] is False and c["returnMetadata"] is True for c in client.calls)
    assert "nextToken" not in client.calls[0]


def test_summarize_counts_per_source_sorted():
    vectors = [vec("docs/b.md", 0), vec("docs/a.md", 0), vec("docs/a.md", 1)]
    summary = check_index.summarize(vectors)

    assert summary["total"] == 3
    assert list(summary["per_source"].items()) == [("docs/a.md", 2), ("docs/b.md", 1)]
    assert summary["sample"] == vectors[0]


def test_summarize_handles_missing_metadata_and_empty_index():
    summary = check_index.summarize([{"key": "k#0"}])
    assert summary["per_source"] == {check_index.MISSING: 1}

    empty = check_index.summarize([])
    assert empty == {"total": 0, "per_source": {}, "sample": None}
    assert "Total vectors: 0" in check_index.format_report(empty)


def test_format_report_truncates_long_chunk_text():
    sample = {
        "key": "docs/a.md#0",
        "metadata": {"source_key": "docs/a.md", "chunk_text": "y" * 500},
    }
    report = check_index.format_report(check_index.summarize([sample]))

    assert "docs/a.md#0" in report
    assert "y" * 500 not in report
    assert "..." in report
