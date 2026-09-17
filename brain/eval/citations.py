"""Pulling citations out of an answer, and counting the sentences that carry one.

`.claude/agents/brain-analyst.md` tells the analyst to end every factual sentence with
citations like `[KAFKA-15123]`, `[KIP-848]`, `[chunk:ab12…]`, `[sha]`, `[person:…]`,
`[community:…]`. This module is the other half of that contract: the bracket is what turns
a sentence into a checkable claim, so a key in running prose is *not* a citation — it is
usually the question's own subject quoted back.

Three things this deliberately does not match, because each one would inflate the numbers
with citations nobody made:

* `[KAFKA-100](https://…)` — a markdown link, not a claim about the graph.
* anything inside a ``` fence — an answer that shows the Cypher it ran contains
  `[:RESOLVES]` and `'KAFKA-100'`, and neither is evidence.
* `[1]`, `[see above]`, `[..3]` — brackets that carry no key at all.

The patterns come from `brain/retrieve/keys.py` rather than being retyped, so "what the
router recognises in a question" and "what the gate accepts in an answer" cannot drift
apart. That file also explains why every pattern anchors on ASCII uppercase or hex: `re`
treats Hebrew letters as `\\w`, and four of the nineteen questions are in Hebrew.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

from brain.retrieve.keys import DOCUMENT_KEY_RE, NOT_KEYS, WORKITEM_KEY_RE

CitationKind = Literal["workitem", "document", "chunk", "commit", "person", "community"]
CITATION_KINDS: tuple[str, ...] = get_args(CitationKind)

#: A `Chunk.id` is `sha1(parent_key|kind|position|text)` — 40 hex characters. An agent
#: quoting one truncates it, so the check is a prefix match; below eight characters a
#: prefix stops being an identifier and starts being a coincidence (16^8 ≈ 4.3e9 over a
#: corpus of ~14k chunks). Shorter than that is reported as an uncheckable citation, not
#: silently dropped and not silently accepted.
MIN_CHUNK_PREFIX = 8
#: git's own abbreviation floor is 7; a full sha is 40.
SHA_MIN, SHA_MAX = 7, 40

#: One bracket, no newlines inside it, so an unclosed `[` cannot swallow a paragraph.
BRACKET_RE = re.compile(r"\[([^\[\]\n]{1,300})\]")
#: A fence that is never closed still ends the document, hence `\Z`.
FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
TOKEN_RE = re.compile(r"[^,;\s]+")
HEX_RE = re.compile(r"[0-9a-fA-F]+")
SHA_TOKEN_RE = re.compile(rf"[0-9a-fA-F]{{{SHA_MIN},{SHA_MAX}}}")
#: Both spellings of "I truncated this", plus the punctuation an id collects in prose.
ELLIPSIS_RE = re.compile(r"(?:\.{2,}|…)+$")
TRAILING_RE = re.compile(r"[.,;:!?)\]]+$")
WRAPPER_CHARS = "`'\"*_“”‘’"

#: A line that is structure rather than a claim. Headings, table rows, rules, fences.
#: `-{3,}` and not `-` so a `- bullet` — where the claims usually are — still counts.
SKIP_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s|\||```|-{3,}\s*$|\*{3,}\s*$|={3,}\s*$)")
#: Split on sentence punctuation *followed by whitespace*, which is what keeps `3.7.0`
#: and `v2.8` in one piece. Hebrew ends sentences with the same three marks.
SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")
WORD_RE = re.compile(r"\w+")
#: Below this a segment is a fragment — a stray "OK.", a table cell, a bare citation —
#: and counting it would make "sentences without a citation" mostly punctuation.
MIN_WORDS_IN_A_SENTENCE = 3


@dataclass(frozen=True)
class Citation:
    """One citation as written, and the value the graph will be asked for.

    `problem` is set when the citation is recognisably a citation but cannot be checked —
    a chunk prefix too short to identify anything. Such a citation counts as found and as
    invalid: pretending not to see it would let a truncation habit hide from the report.
    """

    text: str
    kind: str
    value: str
    start: int = 0
    end: int = 0
    problem: str | None = None

    @property
    def id(self) -> str:
        """The dedup key: two spellings of the same node are one citation."""
        return f"{self.kind}:{self.value}"


def blank_code_fences(text: str) -> str:
    """Replace fenced blocks with spaces, keeping every offset where it was."""
    return FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _strip(token: str) -> str:
    token = token.strip().strip(WRAPPER_CHARS).strip()
    token = ELLIPSIS_RE.sub("", token)
    token = TRAILING_RE.sub("", token)
    return token.strip(WRAPPER_CHARS).strip()


def _prefixed(token: str, prefix: str) -> str | None:
    return token[len(prefix) :] if token.lower().startswith(prefix) else None


def classify(token: str, start: int = 0, end: int = 0) -> Citation | None:
    """One bracketed token -> a citation, or `None` when it names nothing the graph holds."""
    text = _strip(token)
    if not text:
        return None

    value = _prefixed(text, "chunk:")
    if value is not None:
        value = ELLIPSIS_RE.sub("", value.strip(WRAPPER_CHARS)).lower()
        problem = None
        if not value or not HEX_RE.fullmatch(value):
            problem = "a chunk citation must be the hex prefix of a Chunk.id"
        elif len(value) < MIN_CHUNK_PREFIX:
            problem = (
                f"a chunk citation needs at least {MIN_CHUNK_PREFIX} hex characters "
                f"to identify a chunk; this one has {len(value)}"
            )
        return Citation(text, "chunk", value, start, end, problem)

    for prefix, kind in (("person:", "person"), ("community:", "community")):
        value = _prefixed(text, prefix)
        if value is not None:
            value = value.strip(WRAPPER_CHARS)
            problem = None if value else f"an empty {kind} id"
            return Citation(text, kind, value, start, end, problem)

    if DOCUMENT_KEY_RE.fullmatch(text):
        return Citation(text, "document", text, start, end)
    if WORKITEM_KEY_RE.fullmatch(text) and text not in NOT_KEYS:
        return Citation(text, "workitem", text, start, end)
    if SHA_TOKEN_RE.fullmatch(text):
        return Citation(text, "commit", text.lower(), start, end)
    return None


def find_citations(text: str) -> list[Citation]:
    """Every citation in `text`, in reading order, occurrences included.

    Offsets are into `text` itself (fences are blanked in place, not removed), so a caller
    can ask which sentence a citation fell in without re-deriving the mapping.
    """
    scan = blank_code_fences(text or "")
    out: list[Citation] = []
    for match in BRACKET_RE.finditer(scan):
        if scan[match.end() : match.end() + 1] == "(":
            continue  # a markdown link
        base = match.start(1)
        for token in TOKEN_RE.finditer(match.group(1)):
            citation = classify(token.group(0), base + token.start(), base + token.end())
            if citation is not None:
                out.append(citation)
    return out


def unique(citations: list[Citation]) -> list[Citation]:
    """One entry per distinct citation, first spelling wins, order preserved."""
    seen: dict[str, Citation] = {}
    for citation in citations:
        seen.setdefault(citation.id, citation)
    return list(seen.values())


def _is_sentence(segment: str) -> bool:
    """Words are what make a sentence, and a citation is not one of them."""
    without_citations = BRACKET_RE.sub(" ", segment)
    return len(WORD_RE.findall(without_citations)) >= MIN_WORDS_IN_A_SENTENCE


def sentences(body: str) -> list[str]:
    """The factual-claim-sized pieces of an answer: prose and bullets, not structure."""
    out: list[str] = []
    for line in blank_code_fences(body or "").split("\n"):
        if not line.strip() or SKIP_LINE_RE.match(line):
            continue
        for segment in SENTENCE_END_RE.split(line):
            if _is_sentence(segment):
                out.append(segment.strip())
    return out


def sentence_stats(body: str) -> dict[str, float | int]:
    """How much of the answer is backed. Plan decision 2: cite in every factual sentence."""
    found = sentences(body)
    with_citation = sum(1 for s in found if find_citations(s))
    return {
        "total": len(found),
        "with_citation": with_citation,
        "without_citation": len(found) - with_citation,
        "pct_with_citation": round(100 * with_citation / len(found), 2) if found else 0.0,
    }
