"""Heading-aware Markdown chunker.

Pipeline: clean -> split into sections by heading -> split sections into blocks
(paragraphs / fenced code blocks) -> pack blocks into windows of at most `size` characters,
repeating the last block(s) of a window at the start of the next one as overlap.

Every chunk remembers its heading path ("Parent > Child") so retrieval results can be cited
and the embedding sees the context a bare paragraph would lack.
"""

import re
from dataclasses import dataclass

DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# "## Title", optionally closed with " ##". The closing run must follow a space so "C#" survives.
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
# FastAPI-style explicit anchors: "# Path Parameters { #path-parameters }"
ANCHOR_RE = re.compile(r"\s*\{\s*#[\w.:-]+\s*\}\s*$")
# Leftover "{* file.py hl[1] *}" include macros that nobody resolved: pure noise for retrieval.
INCLUDE_RE = re.compile(r"^\s*\{\*.*\*\}\s*$")
FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

BLOCK_SEP = "\n\n"


@dataclass(frozen=True)
class Chunk:
    index: int
    heading: str  # heading path, "" for text before the first heading
    text: str  # body only; its length is <= the configured size

    @property
    def embed_text(self) -> str:
        """What gets embedded: heading path first, so the vector carries the section context."""
        return f"{self.heading}\n\n{self.text}" if self.heading else self.text


def chunk_markdown(
    text: str,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """Split a Markdown/plain-text document into chunks of at most `size` characters.

    `size` counts the body only (not the heading path). Plain text without headings works
    too: it is simply one heading-less section.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if not 0 <= overlap < size:
        raise ValueError("overlap must be >= 0 and smaller than size")

    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    text = FRONT_MATTER_RE.sub("", text, count=1)

    chunks: list[Chunk] = []
    for heading, blocks in _sections(text):
        units = [piece for block in blocks for piece in _split_block(block, size)]
        for body in _window(units, size, overlap):
            chunks.append(Chunk(index=len(chunks), heading=heading, text=body))
    return chunks


def _closes_fence(line: str, opener: str) -> bool:
    """A closing fence uses the same character, is at least as long, and has nothing else."""
    stripped = line.strip()
    return bool(stripped) and set(stripped) == {opener[0]} and len(stripped) >= len(opener)


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Walk the document once, returning (heading path, blocks) per section.

    A block is a paragraph or a whole fenced code block. Tracking fences matters twice:
    a "# comment" inside a shell snippet is not a heading, and blank lines inside code
    must survive (they are not paragraph breaks). Runs of blank lines outside code simply
    produce no extra blocks, which is how excessive blank lines get collapsed.
    """
    sections: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []  # (level, title) of the headings above the cursor
    heading = ""
    blocks: list[str] = []
    lines: list[str] = []
    fence: str | None = None

    def flush_block() -> None:
        if lines:
            blocks.append("\n".join(lines).rstrip())
            lines.clear()

    def flush_section() -> None:
        flush_block()
        if blocks:
            sections.append((heading, list(blocks)))
            blocks.clear()

    for line in text.split("\n"):
        if fence is not None:
            lines.append(line)
            if _closes_fence(line, fence):
                fence = None
                flush_block()
            continue

        fence_match = FENCE_RE.match(line)
        if fence_match:
            flush_block()  # code gets its own block even without a blank line before it
            fence = fence_match.group(1)
            lines.append(line)
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush_section()
            level = len(heading_match.group(1))
            title = ANCHOR_RE.sub("", heading_match.group(2)).strip() or heading_match.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            heading = " > ".join(t for _, t in stack)
            continue

        if INCLUDE_RE.match(line):
            continue
        if not line.strip():
            flush_block()
            continue
        lines.append(line.rstrip())

    flush_section()
    return sections


def _split_block(block: str, size: int) -> list[str]:
    """Return the block as pieces of at most `size` characters.

    Oversized fenced code is cut on line boundaries and each piece is re-fenced so it still
    renders (and reads) as code.
    """
    if len(block) <= size:
        return [block]

    lines = block.split("\n")
    fence_match = FENCE_RE.match(lines[0])
    if not fence_match:
        return _split_text(block, size)

    opener = lines[0]
    closer = fence_match.group(1)
    body = lines[1:]
    if body and _closes_fence(body[-1], closer):
        body = body[:-1]
    budget = max(size - len(opener) - len(closer) - 2, 20)
    return [f"{opener}\n{piece}\n{closer}" for piece in _split_text("\n".join(body), budget)]


# Progressively finer places to cut oversized text: lines, then sentences, then raw characters.
_SPLITTERS = (("\n", lambda t: t.split("\n")), (" ", SENTENCE_RE.split))


def _split_text(text: str, limit: int, level: int = 0) -> list[str]:
    if len(text) <= limit:
        return [text]
    if level == len(_SPLITTERS):
        return [text[i : i + limit] for i in range(0, len(text), limit)]

    sep, splitter = _SPLITTERS[level]
    parts = splitter(text)
    if len(parts) == 1:
        return _split_text(text, limit, level + 1)

    pieces: list[str] = []
    current = ""
    for part in parts:
        for sub in _split_text(part, limit, level + 1):  # shrinks parts that are still too big
            if current and len(current) + len(sep) + len(sub) <= limit:
                current += sep + sub
            else:
                if current:
                    pieces.append(current)
                current = sub
    if current:
        pieces.append(current)
    return pieces


def _window(units: list[str], size: int, overlap: int) -> list[str]:
    """Greedily pack units (each <= size) into windows, carrying overlap between them."""
    windows: list[str] = []
    current: list[str] = []

    def joined_len(items: list[str]) -> int:
        return sum(len(i) for i in items) + len(BLOCK_SEP) * max(len(items) - 1, 0)

    for unit in units:
        if current and joined_len(current) + len(BLOCK_SEP) + len(unit) > size:
            windows.append(BLOCK_SEP.join(current))
            # Overlap: repeat the trailing units (up to `overlap` chars) so a fact that sits on
            # a boundary appears whole in at least one chunk.
            carry: list[str] = []
            for previous in reversed(current):
                if joined_len([previous, *carry]) > overlap:
                    break
                carry.insert(0, previous)
            # If the carry plus the new unit won't fit, shed overlap rather than exceed size.
            while carry and joined_len([*carry, unit]) > size:
                carry.pop(0)
            current = carry
        current.append(unit)

    if current:
        windows.append(BLOCK_SEP.join(current))
    return windows
