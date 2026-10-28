"""Chunk a folder of docs locally and print stats. No AWS needed.

Usage: python scripts/preview_chunks.py [folder] [--size 1000] [--overlap 150]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.common.chunking import DEFAULT_CHUNK_OVERLAP, DEFAULT_CHUNK_SIZE, chunk_markdown  # noqa: E402
from app.common.config import ALLOWED_SUFFIXES, SKIP_FILENAMES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", default="sample-docs")
    parser.add_argument("--size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    args = parser.parse_args()

    # Same filter as the ingest Lambda, minus the docs/ prefix (the folder is the corpus).
    paths = sorted(
        p
        for p in Path(args.folder).rglob("*")
        if p.is_file()
        and p.suffix.lower() in ALLOWED_SUFFIXES
        and p.name.lower() not in SKIP_FILENAMES
    )

    chunks = []  # (file name, Chunk)
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        chunks.extend((path.name, c) for c in chunk_markdown(text, args.size, args.overlap))

    if not chunks:
        print(f"No chunks produced from {args.folder!r}")
        return 1

    lengths = [len(c.text) for _, c in chunks]
    print(f"folder:  {args.folder}  (size={args.size}, overlap={args.overlap})")
    print(f"files:   {len(paths)}")
    print(f"chunks:  {len(chunks)}")
    print(
        f"length:  min {min(lengths)} / avg {sum(lengths) / len(lengths):.0f} / max {max(lengths)}"
    )

    # First, middle and last chunk give a quick feel for the output.
    for pos in sorted({0, len(chunks) // 2, len(chunks) - 1}):
        name, chunk = chunks[pos]
        print(f"\n--- sample: {name} #{chunk.index} | heading: {chunk.heading or '(intro)'} ---")
        print(chunk.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
