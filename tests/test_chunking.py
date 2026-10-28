import pytest

from app.common.chunking import chunk_markdown


def test_empty_and_whitespace_documents_produce_no_chunks():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n \n") == []


def test_front_matter_only_document_is_empty():
    assert chunk_markdown("---\ntitle: x\n---\n") == []


def test_front_matter_is_stripped():
    chunks = chunk_markdown("---\ntitle: Secret\ntags: [a]\n---\n# Intro\n\nHello world.")
    assert len(chunks) == 1
    assert chunks[0].heading == "Intro"
    assert "Secret" not in chunks[0].text
    assert chunks[0].text == "Hello world."


def test_no_headings_gives_a_single_headingless_chunk():
    chunks = chunk_markdown("Just some text.\n\nAnother paragraph.")
    assert len(chunks) == 1
    assert chunks[0].heading == ""
    assert chunks[0].text == "Just some text.\n\nAnother paragraph."
    assert chunks[0].embed_text == chunks[0].text  # no heading prefix when there is none


def test_text_before_first_heading_is_kept_separately():
    chunks = chunk_markdown("Preamble.\n\n# Title\n\nBody.")
    assert [(c.heading, c.text) for c in chunks] == [("", "Preamble."), ("Title", "Body.")]


def test_nested_headings_build_a_path_and_siblings_reset_it():
    doc = "# A\n\na body\n\n## B\n\nb body\n\n### C\n\nc body\n\n## D\n\nd body\n\n# E\n\ne body"
    chunks = chunk_markdown(doc)
    assert [c.heading for c in chunks] == ["A", "A > B", "A > B > C", "A > D", "E"]
    assert [c.index for c in chunks] == [0, 1, 2, 3, 4]


def test_embed_text_prefixes_the_heading_path():
    chunk = chunk_markdown("# A\n\n## B\n\nbody")[0]
    assert chunk.embed_text == "A > B\n\nbody"


def test_heading_anchors_and_closing_hashes_are_removed_but_c_sharp_survives():
    chunks = chunk_markdown("# Path Parameters { #path-parameters }\n\nx\n\n## Learn C# ##\n\ny")
    assert [c.heading for c in chunks] == ["Path Parameters", "Path Parameters > Learn C#"]


def test_hash_comment_inside_code_fence_is_not_a_heading():
    doc = "# Install\n\n```bash\n# create venv\npython -m venv .venv\n```\n\nDone."
    chunks = chunk_markdown(doc)
    assert [c.heading for c in chunks] == ["Install"]
    assert "# create venv" in chunks[0].text


def test_blank_lines_collapse_outside_code_but_not_inside():
    doc = "# T\n\n\n\n\npara one\n\n\n\npara two\n\n```python\na = 1\n\n\nb = 2\n```"
    text = chunk_markdown(doc)[0].text
    assert "\n\n\n" not in text.split("```python")[0]
    assert "a = 1\n\n\nb = 2" in text  # blank lines inside code are preserved


def test_unresolved_include_macros_are_stripped():
    chunks = chunk_markdown("# T\n\nBefore.\n\n{* ../../docs_src/x/y.py hl[1] *}\n\nAfter.")
    assert chunks[0].text == "Before.\n\nAfter."


def test_long_section_splits_on_paragraph_boundaries_within_size():
    paragraphs = [f"{i:02d} " + "word " * 40 for i in range(10)]  # ~200 chars each
    chunks = chunk_markdown("# T\n\n" + "\n\n".join(paragraphs), size=500, overlap=0)
    assert len(chunks) > 1
    assert all(len(c.text) <= 500 for c in chunks)
    assert all(c.heading == "T" for c in chunks)
    # no paragraph was cut in half: every paragraph appears intact in exactly one chunk
    for p in (p.rstrip() for p in paragraphs):
        assert sum(p in c.text for c in chunks) == 1


def test_overlap_repeats_trailing_paragraph_in_next_chunk():
    paragraphs = [f"P{i} " + "x" * 80 for i in range(6)]  # ~83 chars each
    chunks = chunk_markdown("\n\n".join(paragraphs), size=200, overlap=100)
    assert len(chunks) > 1
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        last_paragraph = prev.text.split("\n\n")[-1]
        assert nxt.text.startswith(last_paragraph)
    assert all(len(c.text) <= 200 for c in chunks)


def test_single_paragraph_longer_than_size_is_split_on_sentences():
    sentence = "This is a sentence of moderate length. "
    chunks = chunk_markdown(sentence * 30, size=200, overlap=0)
    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)  # cut at sentence ends, not mid-word


def test_unbreakable_text_falls_back_to_hard_split():
    chunks = chunk_markdown("x" * 1050, size=400, overlap=0)
    assert [len(c.text) for c in chunks] == [400, 400, 250]


def test_code_block_that_fits_stays_whole():
    code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(10)) + "\n```"
    chunks = chunk_markdown(f"# T\n\nIntro text.\n\n{code}", size=400, overlap=0)
    assert len(chunks) == 1
    assert code in chunks[0].text


def test_oversized_code_block_is_split_on_lines_and_refenced():
    code = "```python\n" + "\n".join(f"value_{i} = {i}" for i in range(100)) + "\n```"
    chunks = chunk_markdown(code, size=300, overlap=0)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 300
        assert c.text.startswith("```python\n") and c.text.endswith("\n```")


def test_windows_line_endings_are_normalized():
    chunks = chunk_markdown("# T\r\n\r\nline one\r\nline two\r\n")
    assert chunks[0].text == "line one\nline two"


def test_invalid_parameters_are_rejected():
    with pytest.raises(ValueError):
        chunk_markdown("x", size=0)
    with pytest.raises(ValueError):
        chunk_markdown("x", size=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_markdown("x", size=100, overlap=-1)
