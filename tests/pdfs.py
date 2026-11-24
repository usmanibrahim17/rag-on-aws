"""Tiny PDF generator for tests (fpdf2 is a dev dependency): one page per string."""

from fpdf import FPDF


def make_pdf(pages: list[str]) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    for text in pages:
        pdf.add_page()
        if text:
            pdf.multi_cell(0, 8, text)
    return bytes(pdf.output())
