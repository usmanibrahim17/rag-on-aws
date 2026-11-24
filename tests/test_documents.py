import pytest

from app.common import documents
from tests.pdfs import make_pdf


def test_markdown_and_text_are_decoded_and_a_bom_is_dropped():
    assert documents.extract_text("docs/a.md", "﻿# Title\n\nbody".encode()) == ("# Title\n\nbody")
    assert documents.extract_text("docs/a.txt", b"plain") == "plain"


def test_pdf_pages_become_page_sections():
    raw = make_pdf(["Returns are accepted within 30 days.", "Refunds take 5 business days."])

    text = documents.extract_text("docs/business/returns.pdf", raw)

    assert text.startswith("## Page 1\n\n")
    assert "Returns are accepted within 30 days." in text
    assert "## Page 2\n\nRefunds take 5 business days." in text


def test_blank_pages_are_dropped_and_page_numbers_are_kept():
    text = documents.extract_text("x.pdf", make_pdf(["First page text.", "", "Third page text."]))
    assert "## Page 2" not in text
    assert "## Page 1" in text and "## Page 3" in text


def test_pdf_without_any_text_yields_empty_text():
    assert documents.extract_text("x.pdf", make_pdf([""])) == ""


def test_extension_check_is_case_insensitive():
    assert documents.is_pdf("docs/Manual.PDF") and not documents.is_pdf("docs/a.md")
    assert documents.extract_text("docs/Manual.PDF", make_pdf(["hello there"])).endswith(
        "hello there"
    )


@pytest.mark.parametrize("raw", [b"not a pdf at all", b"%PDF-1.4 truncated", b""])
def test_corrupt_pdf_raises_unreadable_not_a_crash(raw):
    with pytest.raises(documents.UnreadableDocument):
        documents.extract_text("docs/x.pdf", raw)


def test_oversized_pdf_is_rejected_before_parsing(monkeypatch):
    monkeypatch.setattr(documents, "MAX_PDF_BYTES", 10)
    with pytest.raises(documents.UnreadableDocument, match="larger than"):
        documents.extract_text("docs/x.pdf", b"x" * 11)
