"""Attaching a checkable chunk to an answer that was derived, not read.

S6 answers a temporal question from `StatusChange` events and `impact` answers from four
traversals. Neither reads any text, so neither has a quote — and an item with no
`provenance[].chunk_id` cannot be verified by `brain eval cite-check` (Plan 2 Task 4) or by
a human. The fix is not to invent a quote; it is to hand back the node's *own* text: the
description chunk of the issue the answer is about.

`kind = 'description'` first, then anything, and orphans never: an orphaned chunk is text
that has since been rewritten, so citing it would point a reader at a page that no longer
says that.

Every entry this module makes is stamped `source_kind="node-text"`, and that stamp is the
honest half of the trick: the answer is not backed by anyone writing about this node, it is
backed by the node describing itself. Task 4's cite-check counts the two apart, so an answer
built entirely out of self-description cannot pass as an answer built out of evidence.
"""

from __future__ import annotations

from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.types import Provenance

#: Chunk kinds in the order they make the best citation for a node.
PREFERRED_KINDS: tuple[str, ...] = ("description", "section", "message", "comment")


def own_chunks(
    ctx: RetrieveContext, keys: list[str], per_key: int = 1
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    """`{parent key: [chunk, …]}` — the text each of these nodes is made of.

    Matched on `Chunk.parent_key`, which `brain load` gave a range index, rather than by
    walking `HAS_CHUNK` from the parent: the walk means resolving 50 work items before it
    can look at a chunk, and measured 839 ms against 60 for the same answer.
    """
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}, ""
    ranks = " ".join(
        f"WHEN '{kind}' THEN {len(PREFERRED_KINDS) - i}" for i, kind in enumerate(PREFERRED_KINDS)
    )
    cypher = (
        "UNWIND $keys AS k\n"
        f"MATCH (c:{ctx.label('Chunk')} {{`parent_key`: k}})\n"
        "WHERE coalesce(c.orphaned, false) = false\n"
        f"WITH k, c, CASE c.kind {ranks} ELSE 0 END AS rank\n"
        "ORDER BY rank DESC, c.position\n"
        "WITH k, collect({id: c.id, text: c.text, kind: c.kind})[..$per_key] AS chunks\n"
        "RETURN k AS key, chunks"
    )
    rows = ctx.read(cypher, keys=keys, per_key=per_key)
    return {r["key"]: r["chunks"] for r in rows}, cypher


def as_provenance(
    chunks: list[dict[str, Any]],
    source: str | None = None,
    source_kind: str = "node-text",
) -> list[Provenance]:
    """The node's own chunks as provenance. `node-text` by default — see the module docstring."""
    return [
        Provenance(
            chunk_id=c["id"],
            quote=(c.get("text") or "")[:280],
            source=source,
            source_kind=source_kind,
        )
        for c in chunks
        if c.get("id")
    ]
