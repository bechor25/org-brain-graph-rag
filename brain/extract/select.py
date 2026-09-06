"""Which chunks Phase A extracts from, read back out of the graph (brief 07 decision 2).

Three rules, and the reason each one is a rule:

1. **KIP sections.** The document set is `brain.chunk.scope.select()`, not a Cypher
   predicate of this module's own. `brain chunk` decided which KIPs are in the corpus
   (referenced by the slice, plus the `ambiguous-kip` variants that lost a number
   collision), and one of those variants is stored with `kind = "Page"` — a rule spelled
   `Document.kind = 'KIP'` here would silently drop it and the two steps would disagree
   about what "the KIP set" means.

2. **Issue descriptions.** A real (non-synthetic) work item's `description` chunk of at
   least `MIN_CHARS`, when the item is a Bug / Improvement / New Feature *or* points at a
   KIP. Length first: a 200-character description is a title with a full stop, and the
   agent time it costs buys nothing.

3. **Nothing else.** No comments (Phase B), no commit messages, no synthetic records
   (Phase B) — and a commit could not be context anyway: its `parent_key` is a bare sha,
   which tells an extractor nothing about what it is reading.

`kip_keys_referenced` is joined from the parent's `REFERENCES`/`IMPLEMENTS_KIP` edges. The
chunk does not carry it: `brain load` put the reference on the record, not on the text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.canon.runner import natural_key
from brain.chunk.scope import select as scope_select
from brain.graph.context import GraphContext
from brain.graph.corpus import load_corpus

#: Below this, a description is a restated title (brief 07 decision 2).
MIN_CHARS = 300

#: Issue types worth extracting from whatever they reference. `Sub-task` and `Test` are not
#: here on purpose — they get in only by naming a KIP, which is what makes them design text.
ISSUE_TYPES: tuple[str, ...] = ("Bug", "Improvement", "New Feature")

DOC_CHUNK_KIND = "section"
ISSUE_CHUNK_KIND = "description"

#: How many KIP keys of context one chunk carries. A KIP index page references dozens; the
#: list is context, not a task list, and a long one crowds the text it describes.
MAX_KIP_KEYS = 12


@dataclass(frozen=True)
class SelectedChunk:
    """One chunk plus the context brief 07 decision 3 asks for, and its size."""

    chunk_id: str
    parent_key: str
    parent_kind: str
    parent_title: str
    position: int
    kip_keys_referenced: tuple[str, ...]
    text: str
    char_len: int
    token_est: int
    source: str  # "kip_section" | "issue_description"

    def context(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "parent_key": self.parent_key,
            "parent_kind": self.parent_kind,
            "parent_title": self.parent_title,
            "position": self.position,
            "kip_keys_referenced": list(self.kip_keys_referenced),
            "text": self.text,
        }


@dataclass
class Selection:
    chunks: list[SelectedChunk] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


DOC_CYPHER = """
MATCH (d:{doc})-[:HAS_CHUNK]->(c:{chunk})
WHERE d.key IN $doc_keys AND c.kind = $kind AND coalesce(c.orphaned, false) = false
OPTIONAL MATCH (d)-[:REFERENCES|IMPLEMENTS_KIP]->(k:{doc})
  WHERE k.key STARTS WITH 'KIP-' AND k.key <> d.key
WITH c, d, collect(DISTINCT k.key) AS kips
RETURN c.id AS chunk_id, d.key AS parent_key, 'Document' AS parent_kind,
       coalesce(d.title, d.key) AS parent_title, c.position AS position,
       kips AS kip_keys, c.text AS text, c.char_len AS char_len, c.token_est AS token_est
ORDER BY parent_key, position
"""

ISSUE_CYPHER = """
MATCH (w:{item})-[:HAS_CHUNK]->(c:{chunk})
WHERE c.kind = $kind AND coalesce(c.orphaned, false) = false
  AND coalesce(w.synthetic, false) = false
  AND c.char_len >= $min_chars
OPTIONAL MATCH (w)-[:REFERENCES|IMPLEMENTS_KIP]->(k:{doc}) WHERE k.key STARTS WITH 'KIP-'
WITH c, w, collect(DISTINCT k.key) AS kips
WHERE w.type IN $types OR size(kips) > 0
RETURN c.id AS chunk_id, w.key AS parent_key, 'WorkItem' AS parent_kind,
       coalesce(w.title, w.key) AS parent_title, c.position AS position,
       kips AS kip_keys, c.text AS text, c.char_len AS char_len, c.token_est AS token_est,
       w.type AS issue_type
ORDER BY parent_key, position
"""


def _cypher(ctx: GraphContext, template: str) -> str:
    return template.format(
        doc=ctx.label("Document"),
        item=ctx.label("WorkItem"),
        chunk=ctx.label("Chunk"),
    )


def _kip_keys(raw: Sequence[str] | None) -> tuple[str, ...]:
    keys = sorted({k for k in (raw or []) if k}, key=natural_key)
    return tuple(keys[:MAX_KIP_KEYS])


def kip_document_keys(canonical_dir: Path) -> tuple[list[str], dict[str, Any]]:
    """The KIP set `brain chunk` chunked, straight from `brain.chunk.scope`."""
    scope = scope_select(load_corpus(canonical_dir), all_docs=False)
    keys = [d.key for d in scope.documents]
    return keys, {
        "canonical_dir": str(canonical_dir),
        "kip_keys_referenced": scope.stats["kip_keys_referenced"],
        "documents_reachable": scope.stats["documents_reachable"],
        "documents_ambiguous_variants": scope.stats["documents_ambiguous_variants"],
        "documents_in_scope": len(keys),
    }


def select(
    ctx: GraphContext,
    *,
    canonical_dir: Path,
    min_chars: int = MIN_CHARS,
    issue_types: Sequence[str] = ISSUE_TYPES,
) -> Selection:
    """Every Phase A chunk, in a stable order, with its context and its size."""
    doc_keys, scope_stats = kip_document_keys(canonical_dir)

    doc_rows = ctx.read(_cypher(ctx, DOC_CYPHER), doc_keys=doc_keys, kind=DOC_CHUNK_KIND)
    issue_rows = ctx.read(
        _cypher(ctx, ISSUE_CYPHER),
        kind=ISSUE_CHUNK_KIND,
        min_chars=min_chars,
        types=list(issue_types),
    )

    chunks: list[SelectedChunk] = []
    for row in doc_rows:
        chunks.append(_selected(row, "kip_section"))
    for row in issue_rows:
        chunks.append(_selected(row, "issue_description"))

    docs_with_chunks = {r["parent_key"] for r in doc_rows}
    issue_types_seen: dict[str, int] = {}
    for row in issue_rows:
        issue_types_seen[row["issue_type"]] = issue_types_seen.get(row["issue_type"], 0) + 1

    stats = {
        "min_chars": min_chars,
        "issue_types": list(issue_types),
        "rule": (
            "KIP sections of the documents brain.chunk.scope selected (referenced + "
            f"ambiguous-kip variants); description chunks >= {min_chars} chars of real "
            f"work items that are {'/'.join(issue_types)} or reference a KIP. "
            "No comments, no commits, no synthetic (Phase B)."
        ),
        "scope": scope_stats,
        "kip_sections": len(doc_rows),
        "issue_descriptions": len(issue_rows),
        "documents_with_chunks": len(docs_with_chunks),
        "documents_in_scope_without_chunks": len(set(doc_keys) - docs_with_chunks),
        "issue_descriptions_by_type": dict(sorted(issue_types_seen.items())),
        "chunks": len(chunks),
        "chars": sum(c.char_len for c in chunks),
        "token_est": sum(c.token_est for c in chunks),
        "max_chars": max((c.char_len for c in chunks), default=0),
        "parents": len({(c.parent_kind, c.parent_key) for c in chunks}),
    }
    return Selection(chunks=chunks, stats=stats)


def _selected(row: dict[str, Any], source: str) -> SelectedChunk:
    return SelectedChunk(
        chunk_id=row["chunk_id"],
        parent_key=row["parent_key"],
        parent_kind=row["parent_kind"],
        parent_title=row["parent_title"],
        position=row["position"],
        kip_keys_referenced=_kip_keys(row.get("kip_keys")),
        text=row["text"],
        char_len=row["char_len"],
        token_est=row["token_est"],
        source=source,
    )
