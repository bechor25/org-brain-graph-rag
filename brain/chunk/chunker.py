"""Records → chunks. The only place that decides where a text unit begins and ends.

The packer has one shape and four callers. A document, a description, a comment and a
commit message differ in *what* they hand it, not in how it packs: blocks in, chunks of
500–800 estimated tokens out, with a ~50 token overlap and no cut inside a fence or a
table. A comment and a commit message hand it a single unit and get a single chunk back,
which is the brief's rule expressed as data rather than as a branch.

Three sizes matter. `TARGET_MIN` is when a chunk is allowed to end; `TARGET_MAX` is when
it must; `HARD_MAX` is the point past which even an atomic block is cut, because
`bge-m3` truncates at 8,192 tokens and a silently truncated chunk is a chunk whose text
and whose embedding disagree.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from brain.canon.models import Change, Comment, Document, WorkItem
from brain.chunk.text import (
    Block,
    chunk_id,
    detect_lang,
    sha1,
    split_blocks,
    tail_overlap,
    token_est,
)

TARGET_MIN = 500
TARGET_MAX = 800
OVERLAP = 50
HARD_MAX = 6000
#: Below this many characters a text is not a chunk, it is a shrug ("+1", "ok"). Brief §6.
MIN_CHARS = 20
#: Headings at or above this depth are chunk boundaries (brief §2: `#` / `##`).
SECTION_LEVEL = 2
#: A buffer smaller than this is not worth ending a chunk on. Without it, a short section
#: followed by a 1,500-token code block would emit a chunk containing only its heading.
MIN_FLUSH = 120

ParentKind = str  # Document | WorkItem | Commit
ChunkKind = str  # section | description | comment | message


@dataclass
class Chunk:
    parent_key: str
    parent_kind: ParentKind
    kind: ChunkKind
    position: int
    text: str
    heading: str | None = None
    author: str | None = None
    at: datetime | None = None

    @property
    def id(self) -> str:
        return chunk_id(self.parent_key, self.kind, self.position, self.text)

    @property
    def hash(self) -> str:
        return sha1(self.text)

    @property
    def char_len(self) -> int:
        return len(self.text)

    @property
    def token_est(self) -> int:
        return token_est(self.text)

    @property
    def lang(self) -> str:
        return detect_lang(self.text)

    def props(self) -> dict[str, Any]:
        """Exactly the property set of brief §06 decision 3, plus `orphaned` (§5).

        `heading` is the one addition: the section title a document chunk came from.
        Retrieval shows it to a reader and `brain extract` reads it as context, and
        recovering it later would mean re-parsing the page.
        """
        return {
            "id": self.id,
            "parent_key": self.parent_key,
            "parent_kind": self.parent_kind,
            "kind": self.kind,
            "position": self.position,
            "text": self.text,
            "char_len": self.char_len,
            "token_est": self.token_est,
            "lang": self.lang,
            "heading": self.heading,
            "author": self.author,
            "at": self.at,
            "hash": self.hash,
            "orphaned": False,
        }


@dataclass
class ChunkStats:
    """Everything the chunker dropped or had to force, counted rather than logged."""

    rejected_short: int = 0
    rejected_empty: int = 0
    hard_split_blocks: int = 0
    truncated: int = 0
    oversize_chunks: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    by_parent_kind: dict[str, int] = field(default_factory=dict)

    def count(self, chunk: Chunk) -> None:
        self.by_kind[chunk.kind] = self.by_kind.get(chunk.kind, 0) + 1
        self.by_parent_kind[chunk.parent_kind] = self.by_parent_kind.get(chunk.parent_kind, 0) + 1
        if chunk.token_est > TARGET_MAX:
            self.oversize_chunks += 1


def _hard_split(block: Block, stats: ChunkStats) -> list[Block]:
    """Cut a block that no packing can fit. Line boundaries only, never mid-line."""
    if block.tokens <= HARD_MAX:
        return [block]
    stats.hard_split_blocks += 1
    out: list[Block] = []
    buf: list[str] = []
    size = 0
    for line in block.text.split("\n"):
        line_tokens = token_est(line) + 1
        if buf and size + line_tokens > HARD_MAX:
            out.append(Block(block.kind, "\n".join(buf)))
            buf, size = [], 0
        buf.append(line)
        size += line_tokens
    if buf:
        out.append(Block(block.kind, "\n".join(buf)))
    return out


def _split_long_text(block: Block) -> list[Block]:
    """A prose block bigger than the target, cut on line boundaries into ≤ max pieces."""
    if block.tokens <= TARGET_MAX or block.atomic:
        return [block]
    out: list[Block] = []
    buf: list[str] = []
    size = 0
    for line in block.text.split("\n"):
        line_tokens = token_est(line) + 1
        if buf and size + line_tokens > TARGET_MAX:
            out.append(Block(block.kind, "\n".join(buf)))
            buf, size = [], 0
        buf.append(line)
        size += line_tokens
    if buf:
        out.append(Block(block.kind, "\n".join(buf)))
    return out


def pack(blocks: Iterable[Block], stats: ChunkStats, *, headings: bool = True) -> list[tuple]:
    """Blocks → `(text, heading)` pairs of 500–800 estimated tokens with ~50 overlap.

    Boundaries come from three rules, in order: a `#`/`##` heading ends the current chunk
    once it is big enough to stand alone; a block that would push the chunk past the
    maximum ends it; a block bigger than the maximum on its own becomes its own chunk
    (cut only if it is prose, or if it is past `HARD_MAX`).
    """
    prepared: list[Block] = []
    for b in blocks:
        for piece in _hard_split(b, stats):
            prepared.extend(_split_long_text(piece))

    out: list[tuple[str, str | None]] = []
    buf: list[Block] = []
    size = 0
    last_heading: str | None = None  # most recent section heading seen
    chunk_heading: str | None = None  # the one in force for the buffer being built
    overlap = ""

    def flush() -> None:
        nonlocal buf, size, overlap, chunk_heading
        if not buf:
            return
        body = "\n\n".join(b.text for b in buf)
        text = f"{overlap}\n\n{body}".strip() if overlap else body
        out.append((text, chunk_heading))
        overlap = tail_overlap(text, OVERLAP)
        buf, size = [], 0
        # A section split across several chunks keeps its heading on all of them.
        chunk_heading = last_heading

    for b in prepared:
        is_section = headings and b.kind == "heading" and b.level <= SECTION_LEVEL
        if is_section and size >= TARGET_MIN:
            flush()
        elif buf and size + b.tokens > TARGET_MAX and size >= MIN_FLUSH:
            flush()
        if is_section:
            last_heading = b.text.lstrip("# ").strip()
            if not buf:
                chunk_heading = last_heading
        buf.append(b)
        size += b.tokens
    flush()
    return out


def _emit(
    parent_key: str,
    parent_kind: ParentKind,
    kind: ChunkKind,
    pairs: list[tuple],
    stats: ChunkStats,
    *,
    author: str | None = None,
    at: datetime | None = None,
) -> Iterator[Chunk]:
    position = 0
    for text, heading in pairs:
        if not text.strip():
            stats.rejected_empty += 1
            continue
        if len(text.strip()) < MIN_CHARS:
            stats.rejected_short += 1
            continue
        chunk = Chunk(
            parent_key=parent_key,
            parent_kind=parent_kind,
            kind=kind,
            position=position,
            text=text,
            heading=heading,
            author=author,
            at=at,
        )
        stats.count(chunk)
        position += 1
        yield chunk


def chunk_document(doc: Document, stats: ChunkStats) -> Iterator[Chunk]:
    """A page by its `#`/`##` headings. The title is not prepended: it is already the
    first heading of every KIP body this corpus harvested."""
    yield from _emit(doc.key, "Document", "section", pack(split_blocks(doc.body_md), stats), stats)


def chunk_workitem(item: WorkItem, stats: ChunkStats) -> Iterator[Chunk]:
    """Description first, then one chunk per comment in the order the source lists them."""
    yield from chunk_workitem_description(item, stats)
    yield from chunk_comments(item, stats)


def chunk_workitem_description(item: WorkItem, stats: ChunkStats) -> Iterator[Chunk]:
    """One chunk under 800 tokens, otherwise packed by paragraph (brief §2).

    `headings=False`: a Jira description that opens with `# Steps to reproduce` is not a
    page with sections, and letting a heading force a boundary would split a 200-token
    bug report into two useless halves.
    """
    text = (item.description or "").strip()
    if not text:
        return
    pairs = pack(split_blocks(text), stats, headings=False)
    yield from _emit(item.key, "WorkItem", "description", pairs, stats)


def chunk_comments(item: WorkItem, stats: ChunkStats) -> Iterator[Chunk]:
    """One chunk per comment, never split — a comment is already the unit a human wrote.

    `position` is the comment's index on the item, so it survives a reworded comment: the
    id changes (text is in the hash), the position does not.
    """
    for position, comment in enumerate(item.comments):
        yield from _comment_chunk(item, position, comment, stats)


def _comment_chunk(item: WorkItem, position: int, comment: Comment, stats: ChunkStats):
    text = _cap(comment.body or "", stats)
    if not text.strip():
        stats.rejected_empty += 1
        return
    if len(text.strip()) < MIN_CHARS:
        stats.rejected_short += 1
        return
    chunk = Chunk(
        parent_key=item.key,
        parent_kind="WorkItem",
        kind="comment",
        position=position,
        text=text,
        author=comment.author,
        at=comment.at,
    )
    stats.count(chunk)
    yield chunk


def chunk_commit(change: Change, stats: ChunkStats) -> Iterator[Chunk]:
    """A commit message is one chunk, whatever its size (spec §3.4: "no split")."""
    text = _cap(change.message or "", stats)
    yield from _emit(
        change.id,
        "Commit",
        "message",
        [(text, None)],
        stats,
        author=change.author_email,
        at=change.at,
    )


def _cap(text: str, stats: ChunkStats) -> str:
    """A comment or a message longer than the model's window, cut at a line boundary.

    Nothing in the Kafka corpus reaches this — the longest comment is ~4.5k estimated
    tokens — but a chunk whose tail the model never saw would be a silent lie about what
    the embedding represents, so the cut is explicit and counted.
    """
    if token_est(text) <= HARD_MAX:
        return text
    stats.truncated += 1
    return _hard_split(Block("text", text), ChunkStats())[0].text
