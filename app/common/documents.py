"""Turn an uploaded file into text for the chunker (Markdown/text as-is, PDF via pypdf)."""

import io

MAX_PDF_BYTES = 20 * 1024 * 1024  # a larger PDF would risk the Lambda's memory/time limits


class UnreadableDocument(ValueError):
    """The file cannot be turned into text (encrypted, corrupt or too large). Do not retry."""


def is_pdf(key: str) -> bool:
    return key.lower().endswith(".pdf")


def extract_text(key: str, raw: bytes) -> str:
    """Return the document text. PDFs become "## Page N" sections so citations can name a page."""
    if not is_pdf(key):
        return raw.decode("utf-8-sig")  # -sig drops a BOM if the file has one
    return _pdf_to_markdown(raw)


def _pdf_to_markdown(raw: bytes) -> str:
    if len(raw) > MAX_PDF_BYTES:
        raise UnreadableDocument(f"PDF is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB")

    # Imported here so the Markdown-only path (and unit tests of it) never needs pypdf.
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise UnreadableDocument("PDF is encrypted")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except PyPdfError as exc:
        raise UnreadableDocument(f"PDF could not be read: {type(exc).__name__}") from None

    # Pages without text (scans, pure images) are dropped; if nothing is left the caller
    # indexes zero chunks (this system has no OCR).
    sections = [f"## Page {number}\n\n{text}" for number, text in enumerate(pages, start=1) if text]
    return "\n\n".join(sections)
