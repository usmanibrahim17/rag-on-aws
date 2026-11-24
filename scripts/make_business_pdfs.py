"""Render the demo company's PDF documents from Markdown sources (needs fpdf2, a dev dependency).

    python scripts/make_business_pdfs.py

Reads scripts/business_pdf_sources/*.md and writes sample-docs/business/<name>.pdf. The sources
are kept outside sample-docs/ so they are not uploaded and ingested as documents as well.
Text is plain ASCII on purpose: fpdf2's built-in fonts cover Latin-1 only.
"""

from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "scripts" / "business_pdf_sources"
OUT = ROOT / "sample-docs" / "business"


def render(markdown: str) -> bytes:
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))  # same input, same bytes
    pdf.set_title("Glimmerwick Gadgets (fictional demo company)")
    pdf.set_author("Glimmerwick Gadgets (fictional)")
    pdf.add_page()

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            pdf.ln(3)
        elif stripped.startswith("# "):
            pdf.set_font("Helvetica", "B", 17)
            pdf.multi_cell(0, 9, stripped[2:], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2)
        elif stripped.startswith("## "):
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 13)
            pdf.multi_cell(0, 8, stripped[3:], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        elif stripped.startswith("- "):
            pdf.set_font("Helvetica", "", 11)
            pdf.set_x(26)
            pdf.multi_cell(0, 6, "- " + stripped[2:], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(0, 6, stripped, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    return bytes(pdf.output())


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for source in sorted(SOURCES.glob("*.md")):
        target = OUT / f"{source.stem}.pdf"
        target.write_bytes(render(source.read_text(encoding="utf-8")))
        print(f"wrote {target.relative_to(ROOT)} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
