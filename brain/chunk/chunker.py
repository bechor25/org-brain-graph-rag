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
    fence_parity,
    sha1,
    split_blocks,
    tail_overlap,
    token_est,
)

TARGET_MIN = 500
TARGET_MAX = 800
OVERLAP = 50
#: What the *blocks* of a chunk may weigh: the overlap is part of the chunk's budget, not
#: an extra on top of it, so the emitted text stays inside `TARGET_MAX`.
BODY_MAX = TARGET_MAX - OVERLAP
HARD_MAX = 6000
#: Below this many characters a text is not a chunk, it is a shrug ("+1", "ok"). Brief §6.
MIN_CHARS = 20
#: Headings at or above this depth are chunk boundaries (brief §2: `#` / `##`).
SECTION_LEVEL = 2
#: A buffer smaller than this is not worth ending a chunk on *when the next block cannot
#: be cut*: a heading followed by a 1,500-token fence would otherwise emit a chunk holding
#: nothing but the heading. Prose always flushes, because prose was already cut to
#: `BODY_MAX` and a small chunk beats a chunk over the target.
MIN_FLUSH = 120
#: What separates two blocks in a chunk's text. Two characters, and they are in the budget.
JOIN = "\n\n"

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


#: Why a chunk is allowed to exceed `TARGET_MAX`. Anything landing in `other` is a bug in
#: the packer, not a property of the source, which is why they are counted apart.
OVERSIZE_ATOMIC = "atomic_block"  # an uncuttable code fence or table
OVERSIZE_SINGLE_UNIT = "single_unit"  # a comment or a commit message: 1 chunk by rule
OVERSIZE_OTHER = "other"


@dataclass
class Packed:
    """One chunk's text as the packer produced it, with the reason it may be oversize."""

    text: str
    heading: str | None = None
    cause: str | None = None
    #: True when a block in this chunk is a piece of one `HARD_MAX` had to cut, which is
    #: the only packing decision allowed to leave a fence open.
    holds_a_cut_block: bool = False


@dataclass
class ChunkStats:
    """Everything the chunker dropped or had to force, counted rather than logged."""

    rejected_short: int = 0
    rejected_empty: int = 0
    hard_split_blocks: int = 0
    wrapped_long_lines: int = 0
    truncated: int = 0
    oversize_chunks: int = 0
    oversize_by_cause: dict[str, int] = field(default_factory=dict)
    fence_parity_violations: int = 0
    #: `token_est` of every chunk the packer actually controls — no uncuttable fence or
    #: table, no comment or commit message that is one chunk by rule. This is the series
    #: the 500-800 target is a claim about; the corpus-wide one is a claim about the corpus.
    budgeted_tokens: list[int] = field(default_factory=list)
    by_kind: dict[str, int] = field(default_factory=dict)
    by_parent_kind: dict[str, int] = field(default_factory=dict)

    def count(self, chunk: Chunk, cause: str | None = None, *, blames_us: bool = True) -> None:
        """`blames_us` is False when an unclosed fence in this chunk is not the packer's.

        Two sources of a legitimately odd chunk: the record itself was unbalanced (one
        Jira description and one commit message in this corpus open a fence they never
        close), and `HARD_MAX` cut a 29,000-token fenced log dump because the model cannot
        read past 8,192. Counting either would make the invariant unfalsifiable.
        """
        self.by_kind[chunk.kind] = self.by_kind.get(chunk.kind, 0) + 1
        self.by_parent_kind[chunk.parent_kind] = self.by_parent_kind.get(chunk.parent_kind, 0) + 1
        if blames_us and not fence_parity(chunk.text):
            self.fence_parity_violations += 1
        if cause is None:
            self.budgeted_tokens.append(chunk.token_est)
        if chunk.token_est > TARGET_MAX:
            self.oversize_chunks += 1
            reason = cause or OVERSIZE_OTHER
            self.oversize_by_cause[reason] = self.oversize_by_cause.get(reason, 0) + 1


def _hard_split(block: Block, stats: ChunkStats) -> list[Block]:
    """Cut a block that no packing can fit — the one cut allowed to break a code fence.

    `bge-m3` truncates at 8,192 tokens, so a 29,000-token log dump pasted inside a fence
    (KAFKA-15932 is exactly that) has to be cut or most of it would be embedded as
    nothing. The pieces are marked `split` so the fence-parity count knows this chunk's
    unclosed fence was a decision, not an accident.
    """
    if block.tokens <= HARD_MAX:
        return [block]
    stats.hard_split_blocks += 1
    return _cut_lines(block, HARD_MAX)


def _cut_lines(block: Block, budget: int) -> list[Block]:
    """Cut a block into pieces of at most `budget` tokens, on line boundaries."""
    out: list[Block] = []
    buf: list[str] = []
    size = 0
    for line in block.text.split("\n"):
        line_tokens = token_est(line) + 1
        if buf and size + line_tokens > budget:
            out.append(Block(block.kind, "\n".join(buf), split=True))
            buf, size = [], 0
        buf.append(line)
        size += line_tokens
    if buf:
        out.append(Block(block.kind, "\n".join(buf), split=True))
    return out


def _cut_words(block: Block, budget: int, stats: ChunkStats) -> list[Block]:
    """Last resort for prose with no line to cut on: cut at a space, never mid-word.

    A Jira description pasted as one 23,000-character paragraph has no blank line and no
    newline. Leaving it whole would put a 5,800-token chunk in an index whose other
    chunks are 650, which is a retrieval problem, not a formatting one. Counted as
    `wrapped_long_lines` so the report says how much text was cut this way.
    """
    if block.tokens <= budget:
        return [block]
    limit = budget * 4
    out: list[Block] = []
    rest = block.text
    while token_est(rest) > budget:
        cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit  # one unbroken 3,000-character token; cut it anyway
        out.append(Block(block.kind, rest[:cut].rstrip(), split=True))
        rest = rest[cut:].lstrip()
        stats.wrapped_long_lines += 1
    if rest.strip():
        out.append(Block(block.kind, rest, split=True))
    return out


def _split_long_text(block: Block, stats: ChunkStats) -> list[Block]:
    """A prose block bigger than the body budget, cut on line then on word boundaries.

    The budget is `TARGET_MAX - OVERLAP`, not `TARGET_MAX`: the next chunk opens with the
    previous one's ~50-token tail, and a piece sized to the full target would push the
    chunk it lands in over the target by exactly the overlap it was given.
    """
    if block.tokens <= BODY_MAX or block.atomic:
        return [block]
    return [
        piece for line in _cut_lines(block, BODY_MAX) for piece in _cut_words(line, BODY_MAX, stats)
    ]


def pack(blocks: Iterable[Block], stats: ChunkStats, *, headings: bool = True) -> list[Packed]:
    """Blocks → chunks of 500–800 estimated tokens, overlapping by ~50.

    Boundaries come from three rules, in order: a `#`/`##` heading ends the current chunk
    once it is big enough to stand alone; a block that would push the chunk past the
    maximum ends it; a block bigger than the maximum on its own becomes its own chunk
    (cut only if it is prose, or if it is past `HARD_MAX`).

    `size` counts the overlap the next chunk has already inherited, so `TARGET_MAX` is a
    bound on the chunk that gets *written*, not on the blocks that go into it. Without
    that the overlap was pure overshoot and the section p95 sat at 850.
    """
    prepared: list[Block] = []
    for b in blocks:
        for piece in _hard_split(b, stats):
            prepared.extend(_split_long_text(piece, stats))

    out: list[Packed] = []
    buf: list[Block] = []
    last_heading: str | None = None  # most recent section heading seen
    chunk_heading: str | None = None  # the one in force for the buffer being built
    overlap = ""

    def render(extra: Block | None = None) -> str:
        parts = [b.text for b in buf]
        if extra is not None:
            parts.append(extra.text)
        body = JOIN.join(parts)
        return f"{overlap}{JOIN}{body}".strip() if overlap else body

    def size(extra: Block | None = None) -> int:
        """Tokens of the text that would be *written*, joiners and overlap included.

        Summing `Block.tokens` was 2 characters per join short, which is how eight chunks
        came out at 801 tokens against an 800-token target. The budget has to be measured
        on the string that gets embedded, not on the pieces it was built from.
        """
        return token_est(render(extra))

    def flush() -> None:
        nonlocal buf, overlap, chunk_heading
        if not buf:
            return
        text = render()
        cause = OVERSIZE_ATOMIC if any(b.atomic for b in buf) else None
        out.append(Packed(text, chunk_heading, cause, any(b.split for b in buf)))
        # The next chunk opens with this one's tail, and is charged for it.
        overlap = tail_overlap(text, OVERLAP)
        buf = []
        # A section split across several chunks keeps its heading on all of them.
        chunk_heading = last_heading

    for b in prepared:
        is_section = headings and b.kind == "heading" and b.level <= SECTION_LEVEL
        current = size()
        if is_section and current >= TARGET_MIN:
            flush()
        elif buf and size(b) > TARGET_MAX and (current >= MIN_FLUSH or not b.atomic):
            flush()
        if is_section:
            last_heading = b.text.lstrip("# ").strip()
            if not buf:
                chunk_heading = last_heading
        buf.append(b)
    flush()
    return out


def _emit(
    parent_key: str,
    parent_kind: ParentKind,
    kind: ChunkKind,
    packed: list[Packed],
    stats: ChunkStats,
    *,
    author: str | None = None,
    at: datetime | None = None,
    source_balanced: bool = True,
) -> Iterator[Chunk]:
    position = 0
    for item in packed:
        if not item.text.strip():
            stats.rejected_empty += 1
            continue
        if len(item.text.strip()) < MIN_CHARS:
            stats.rejected_short += 1
            continue
        chunk = Chunk(
            parent_key=parent_key,
            parent_kind=parent_kind,
            kind=kind,
            position=position,
            text=item.text,
            heading=item.heading,
            author=author,
            at=at,
        )
        stats.count(
            chunk,
            item.cause,
            blames_us=source_balanced and not item.holds_a_cut_block,
        )
        position += 1
        yield chunk


def chunk_document(doc: Document, stats: ChunkStats) -> Iterator[Chunk]:
    """A page by its `#`/`##` headings. The title is not prepended: it is already the
    first heading of every KIP body this corpus harvested."""
    yield from _emit(
        doc.key,
        "Document",
        "section",
        pack(split_blocks(doc.body_md), stats),
        stats,
        source_balanced=fence_parity(doc.body_md),
    )


def chunk_workitem_description(item: WorkItem, stats: ChunkStats) -> Iterator[Chunk]:
    """One chunk under 800 tokens, otherwise packed by paragraph (brief §2).

    `headings=False`: a Jira description that opens with `# Steps to reproduce` is not a
    page with sections, and letting a heading force a boundary would split a 200-token
    bug report into two useless halves.
    """
    text = (item.description or "").strip()
    if not text:
        return
    packed = pack(split_blocks(text), stats, headings=False)
    yield from _emit(
        item.key,
        "WorkItem",
        "description",
        packed,
        stats,
        source_balanced=fence_parity(text),
    )


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
    # A comment is never split, so its parity is whatever the author typed.
    stats.count(chunk, OVERSIZE_SINGLE_UNIT, blames_us=False)
    yield chunk


def chunk_commit(change: Change, stats: ChunkStats) -> Iterator[Chunk]:
    """A commit message is one chunk, whatever its size (spec §3.4: "no split")."""
    text = _cap(change.message or "", stats)
    yield from _emit(
        change.id,
        "Commit",
        "message",
        [Packed(text, None, OVERSIZE_SINGLE_UNIT)],
        stats,
        author=change.author_email,
        at=change.at,
        # Never split either, so an unclosed fence came from the commit message itself.
        source_balanced=False,
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
