"""Text primitives: token estimate, language, identity, and Markdown block splitting.

Two decisions live here.

*Tokens are estimated, not counted.* `bge-m3` runs behind Ollama, which exposes no
tokenizer endpoint (`/api/tokenize` is 404 on 0.32.6), and pulling a HuggingFace
tokenizer in would add a dependency this POC does not otherwise need. `len(text)/4` is
the estimate; `brain chunk --measure` calibrates it against the *real* token count
Ollama returns in `prompt_eval_count` and records the ratio in the report, so the
chunk-size targets are stated in a unit whose error is measured rather than assumed.

*Blocks are atomic.* A fenced code block and a Markdown table are single blocks, so no
packing decision can ever cut one in half — the brief's "never split a fence" is a
property of the block splitter, not a check the packer has to remember to make.

Indentation is not CommonMark here. CommonMark stops recognising a fence past three
leading spaces (four means an indented code block); the Markdown this corpus actually
holds comes out of Confluence through `markdownify`, which indents a fence or a table by
two, four, six or eight spaces whenever it sits inside a list item. Measured on the real
pages: 63 fence lines and 62 table rows are indented four spaces or more, and reading
them the CommonMark way split twelve code blocks down the middle. So a fence is a fence
at any indentation, and `fence_parity_violations` in the report is the number that keeps
this honest.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

#: Characters per token. Measured against `prompt_eval_count`, see the report.
CHARS_PER_TOKEN = 4

BlockKind = Literal["heading", "code", "table", "text"]

#: Any indentation, and an optional list marker on the same line. `markdownify` writes
#: both `    ``` ` (a fence nested in a list item) and `* ``` ` / `3. ``` ` (a fence that
#: *starts* one). KIP-149 alone has six of the second form; missing them left the parser
#: pairing an opening fence with the wrong closing one and cutting code blocks in half.
_LIST_MARKER = r"(?:[-*+]|\d{1,9}[.)])[ \t]+"
_FENCE = re.compile(rf"^[ \t]*(?:{_LIST_MARKER})?(`{{3,}}|~{{3,}})")
_TABLE_ROW = re.compile(rf"^[ \t]*(?:{_LIST_MARKER})?\|")
#: Headings keep the CommonMark rule. `    # comment` inside an indented snippet is a
#: comment, not a section, and there is no list-item dialect that indents a heading.
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(\S.*?)\s*#*\s*$")
_FENCE_MARKERS = ("```", "~~~")
_HEBREW = re.compile(r"[֐-׿]")
_LATIN = re.compile(r"[A-Za-z]")
_WORD_BREAK = re.compile(r"\s")


def token_est(text: str) -> int:
    """Estimated tokens. Deliberately cheap: this number is a budget, not an invoice."""
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def chunk_id(parent_key: str, kind: str, position: int, text: str) -> str:
    """`sha1(parent_key|kind|position|text)` — brief §06 decision 3.

    `kind` is in the key because a work item's description chunk 0 and its first comment
    would otherwise collide on (`KAFKA-1`, 0) whenever their texts happened to match.
    """
    return sha1(f"{parent_key}|{kind}|{position}|{text}")


def detect_lang(text: str) -> str:
    """`he` / `en` / `other` by script census — no model, no guessing at grammar.

    The corpus is English; the synthetic layer is bilingual. A Hebrew sentence carrying
    an English product name still reads as Hebrew, so the threshold is a share of the
    alphabetic characters, not a first-hit test.
    """
    hebrew = len(_HEBREW.findall(text))
    latin = len(_LATIN.findall(text))
    if hebrew and hebrew / (hebrew + latin) >= 0.2:
        return "he"
    if latin:
        return "en"
    return "other"


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    text: str
    level: int = 0  # heading depth, 0 for everything else
    #: True when this is a piece of a bigger block the chunker had to cut. Only `HARD_MAX`
    #: does that to an atomic block, and only because bge-m3 truncates past 8,192 tokens —
    #: so a piece marked here is the one place a fence may legitimately end up unclosed.
    split: bool = False

    @property
    def atomic(self) -> bool:
        """A block that must never be cut: a fence or a table."""
        return self.kind in ("code", "table")

    @property
    def tokens(self) -> int:
        return token_est(self.text)


def _closes_fence(line: str, marker: str) -> bool:
    """The same character, at least as long, nothing else on the line.

    Indentation is ignored on both ends: `markdownify` indents the opening fence of a
    list item's code block and closes it at a different depth often enough that matching
    the indent would leave the block open to the end of the page.
    """
    stripped = line.strip()
    char = marker[0]
    return len(stripped) >= len(marker) and set(stripped) == {char}


def fence_parity(text: str) -> bool:
    """True when every fence in `text` is closed — the invariant a chunk must not break.

    `brain chunk` counts the chunks that fail this over the whole real corpus and puts the
    number in the report as `fence_parity_violations`. It is 0 or the chunker is wrong:
    no page in this corpus has an odd fence count of its own, so any odd chunk was cut.
    """
    return all(text.count(marker) % 2 == 0 for marker in _FENCE_MARKERS)


def _flush(lines: list[str], kind: BlockKind, out: list[Block]) -> None:
    text = "\n".join(lines).strip("\n")
    if text.strip():
        out.append(Block(kind, text))
    lines.clear()


def split_blocks(text: str) -> list[Block]:
    """Markdown → headings, fenced code, tables and paragraphs, in document order.

    An unterminated fence swallows the rest of the document rather than falling back to
    line mode: that is what a Markdown renderer does, and matching it keeps `text` in a
    chunk identical to what a reader of the page sees.
    """
    blocks: list[Block] = []
    buf: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            _flush(buf, "text", blocks)
            marker = fence.group(1)
            body = [line]
            i += 1
            while i < len(lines):
                body.append(lines[i])
                i += 1
                if _closes_fence(body[-1], marker):
                    break
            blocks.append(Block("code", "\n".join(body).rstrip("\n")))
            continue
        heading = _HEADING.match(line)
        if heading:
            _flush(buf, "text", blocks)
            blocks.append(Block("heading", line.strip(), level=len(heading.group(1))))
            i += 1
            continue
        if _TABLE_ROW.match(line):
            _flush(buf, "text", blocks)
            rows = []
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                rows.append(lines[i])
                i += 1
            blocks.append(Block("table", "\n".join(rows)))
            continue
        if not line.strip():
            _flush(buf, "text", blocks)
            i += 1
            continue
        buf.append(line)
        i += 1
    _flush(buf, "text", blocks)
    return blocks


def tail_overlap(text: str, tokens: int) -> str:
    """The last ~`tokens` tokens of `text`, snapped forward to a word boundary.

    Returns "" when the tail would land inside a fence or a table — an overlap is a
    reading aid, and half a code block at the top of a chunk is the opposite of one.
    """
    if tokens <= 0 or not text:
        return ""
    want = tokens * CHARS_PER_TOKEN
    if len(text) <= want:
        tail = text
    else:
        tail = text[-want:]
        m = _WORD_BREAK.search(tail)
        tail = tail[m.end() :] if m else tail
    tail = tail.strip()
    if not tail:
        return ""
    if not fence_parity(tail):
        return ""
    if any(_TABLE_ROW.match(line) for line in tail.split("\n")):
        return ""
    return tail
