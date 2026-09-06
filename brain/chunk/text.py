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
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

#: Characters per token. Measured against `prompt_eval_count`, see the report.
CHARS_PER_TOKEN = 4

BlockKind = Literal["heading", "code", "table", "text"]

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(\S.*?)\s*#*\s*$")
_TABLE_ROW = re.compile(r"^\s{0,3}\|")
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

    @property
    def atomic(self) -> bool:
        """A block that must never be cut: a fence or a table."""
        return self.kind in ("code", "table")

    @property
    def tokens(self) -> int:
        return token_est(self.text)


def _closes_fence(line: str, marker: str) -> bool:
    """CommonMark: a closing fence is the same character, at least as long, nothing else."""
    stripped = line.strip()
    char = marker[0]
    return len(stripped) >= len(marker) and set(stripped) == {char}


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
    if tail.count("```") % 2 or tail.count("~~~") % 2:
        return ""
    if any(_TABLE_ROW.match(line) for line in tail.split("\n")):
        return ""
    return tail
