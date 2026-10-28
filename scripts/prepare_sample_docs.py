"""Build sample-docs/ from a FastAPI clone: copy the tutorial Markdown and inline code examples.

FastAPI docs reference code with lines like
    {* ../../docs_src/path_params/tutorial001_py310.py hl[6:7] *}
which are meaningless without the referenced file. Most questions are about those
examples, so each include line is replaced by the file's content as a fenced code block.
HTML wrapper tags (div, span, ...) are stripped from the prose, keeping their inner text.

Usage: python scripts/prepare_sample_docs.py <fastapi-clone-dir> [out-dir]
"""

import html
import re
import sys
from pathlib import Path

# {* path options... *} on its own line; options (hl[..], ln[..], title[..]) are ignored.
INCLUDE_RE = re.compile(r"^\{\*\s+(\S+)(?:\s.*?)?\s*\*\}\s*$")
LANG_BY_SUFFIX = {".py": "python", ".json": "json", ".toml": "toml", ".sh": "bash"}


def resolve(ref: str, tutorial_dir: Path, clone: Path) -> Path | None:
    """Find the referenced file.

    First try the path as written, relative to the tutorial folder. In the FastAPI repo
    that points nowhere (the macro is resolved from the repo root by mkdocs), so fall
    back to the clone root with the leading "../" segments removed.
    """
    literal = (tutorial_dir / ref).resolve()
    if literal.is_file():
        return literal
    from_root = clone / ref.replace("\\", "/").lstrip("./").replace("../", "")
    return from_root if from_root.is_file() else None


# Presentational wrappers used by the FastAPI docs (termynal terminals, colored output).
# Only the tags go; the text inside stays. Links, images and <code>-like tags are left alone.
WRAPPER_TAG_RE = re.compile(r"</?(?:div|span|section|center|font)\b[^>]*>", re.IGNORECASE)
# Terminal screenshots are pasted as HTML (colors, bold, underline) inside ```console blocks.
CONSOLE_TAG_RE = re.compile(r"</?(?:span|font|div|u|b|i|em|strong)\b[^>]*>", re.IGNORECASE)
INLINE_CODE_RE = re.compile(r"(`+[^`]*`+)")  # `<div>` written as code must survive
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(\w*)")


def strip_html_wrappers(text: str) -> str:
    """Remove wrapper tags from prose, and color/style markup from console output blocks.

    Other fenced code is untouched (it may legitimately contain HTML). Lines that held
    nothing but a wrapper tag are dropped.
    """
    out = []
    fence: str | None = None
    lang = ""
    for line in text.split("\n"):
        if fence is None:
            m = FENCE_OPEN_RE.match(line)
            if m:
                fence, lang = m.group(1), m.group(2).lower()
                out.append(line)
                continue
            # Split on inline code spans: even parts are prose, odd parts are code.
            parts = INLINE_CODE_RE.split(line)
            cleaned = "".join(
                p if i % 2 else WRAPPER_TAG_RE.sub("", p) for i, p in enumerate(parts)
            )
            if cleaned != line and not cleaned.strip():
                continue  # the line was only a wrapper tag
            out.append(cleaned)
        else:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = None
                out.append(line)
            else:
                if lang == "console":
                    line = html.unescape(CONSOLE_TAG_RE.sub("", line))  # "&apos;" -> "'"
                out.append(line)
    return "\n".join(out)


def fence(code: str, lang: str) -> str:
    # Use a longer fence than any backtick run inside the code so it can't close early.
    longest = max((len(m) for m in re.findall(r"`+", code)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{code}\n{ticks}"


def inline_includes(text: str, tutorial_dir: Path, clone: Path, stats: dict) -> str:
    out = []
    for line in text.split("\n"):
        m = INCLUDE_RE.match(line)
        if not m:
            out.append(line)
            continue
        ref = m.group(1)
        path = resolve(ref, tutorial_dir, clone)
        if path is None:
            stats["missing"].append(ref)
            out.append(f"<!-- example not found: {ref} -->")
            continue
        stats["inlined"] += 1
        code = path.read_text(encoding="utf-8").replace("\r\n", "\n").rstrip("\n")
        out.append(fence(code, LANG_BY_SUFFIX.get(path.suffix, "")))
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    clone = Path(sys.argv[1]).resolve()
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "sample-docs")
    tutorial_dir = clone / "docs" / "en" / "docs" / "tutorial"
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = {"files": 0, "inlined": 0, "missing": []}
    for src in sorted(tutorial_dir.glob("*.md")):  # top level only, no subfolders
        text = src.read_text(encoding="utf-8").replace("\r\n", "\n")
        # Strip HTML first, so the inlined code examples below are never touched by it.
        text = strip_html_wrappers(text)
        text = inline_includes(text, tutorial_dir, clone, stats)
        (out_dir / src.name).write_text(text, encoding="utf-8", newline="\n")
        stats["files"] += 1

    print(f"files copied:      {stats['files']}")
    print(f"examples inlined:  {stats['inlined']}")
    print(f"examples missing:  {len(stats['missing'])}")
    for ref in stats["missing"]:
        print(f"  - {ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
