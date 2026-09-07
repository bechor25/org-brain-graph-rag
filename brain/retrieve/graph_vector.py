"""S2 — graph-enhanced vector. The default when the question does not name its anchor.

The move is small and it is the whole idea of GraphRAG: find the text with a vector, then
stop returning *text*. A chunk that says "the coordinator recomputes the assignment" is an
answer to nothing; the same chunk's parent — `KAFKA-15279`, status Resolved, tested by
`XT-41`, fixed by commit `659dd0a`, in component `consumer` — is an answer to a
traceability question. The vector finds the neighbourhood; the graph says what is in it.

Two hops, not more, and a per-parent cap. Depth is where this strategy stops being useful:
hop 3 from any Kafka issue reaches component `core` and from there most of the corpus, and
a context window full of "everything is related to everything" is worse than no context.
The cap is also why `ORDER BY size(rels)` sits inside the subquery — with a limit, near
neighbours have to be collected before far ones or the limit throws away the useful half.

`ASSIGNED_TO` is filtered to the *current* interval (`valid_to IS NULL`). Every past
assignee of a five-year-old issue is a temporal question (S6), not context.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.nodes import key_case, label_case, to_item
from brain.retrieve.types import Item, Provenance, Result
from brain.retrieve.vector import search_chunk_vectors

#: The edges that carry traceability. Spec §4.1 S2, plan Task 1.
CONTEXT_RELS: tuple[str, ...] = (
    "REFERENCES",
    "LINKS_TO",
    "TESTS",
    "HAS_RUN",
    "RESOLVES",
    "IMPLEMENTS_KIP",
    "ASSIGNED_TO",
    "IN_COMPONENT",
    "FIX_VERSION",
)
#: Neighbours per anchor. Above this the answer stops being a neighbourhood and becomes a
#: crawl; below it a well-connected KIP loses its commits.
NEIGHBOURS_PER_ANCHOR = 25
#: How many chunks are returned as quotable evidence alongside the anchors.
EVIDENCE_CHUNKS = 3

_PARENT_LABELS = ("WorkItem", "Document", "Commit")


def _anchor_predicate(ctx: RetrieveContext, var: str) -> str:
    return " OR ".join(f"{var}:{ctx.label(x)}" for x in _PARENT_LABELS)


def neighbours(
    ctx: RetrieveContext, keys: list[str], hops: int = 1, per_anchor: int = NEIGHBOURS_PER_ANCHOR
) -> tuple[list[dict[str, Any]], str]:
    """One query for every anchor's neighbourhood, nearest first, capped per anchor."""
    if not keys:
        return [], ""
    hops = max(1, min(int(hops), 2))
    rels = "|".join(f"`{r}`" for r in CONTEXT_RELS)
    cypher = (
        "UNWIND $keys AS anchor\n"
        f"MATCH (p) WHERE ({_anchor_predicate(ctx, 'p')}) "
        "AND coalesce(p.key, p.sha) = anchor\n"
        "CALL (p) {\n"
        f"  MATCH (p)-[rels:{rels}*1..{hops}]-(n)\n"
        "  WHERE all(r IN rels WHERE type(r) <> 'ASSIGNED_TO' OR r.valid_to IS NULL)\n"
        "  RETURN n, rels ORDER BY size(rels) LIMIT $per_anchor\n"
        "}\n"
        "RETURN anchor, size(rels) AS hops, [r IN rels | type(r)] AS path,\n"
        f"  {label_case('n', ctx.prefix)} AS label,\n"
        f"  {key_case('n', ctx.prefix)} AS key,\n"
        "  coalesce(n.title, n.message, n.display, n.name) AS title,\n"
        "  n.status AS status, n.type AS type,\n"
        "  {via: last(rels).via, type: last(rels).type, status: last(rels).status,\n"
        "   reason: last(rels).reason} AS edge"
    )
    return ctx.read(cypher, keys=keys, per_anchor=per_anchor), cypher


def _anchor_rows(ctx: RetrieveContext, keys: list[str]) -> tuple[dict[str, Any], str]:
    """The anchor nodes themselves — the chunk only knows its parent's key, not its title."""
    if not keys:
        return {}, ""
    cypher = (
        f"MATCH (p) WHERE ({_anchor_predicate(ctx, 'p')}) AND coalesce(p.key, p.sha) IN $keys\n"
        f"RETURN coalesce(p.key, p.sha) AS key, {label_case('p', ctx.prefix)} AS label,\n"
        "  coalesce(p.title, p.message) AS title, p.status AS status, p.type AS type,\n"
        "  p.kind AS kind, p.resolution AS resolution, p.source AS source,\n"
        "  coalesce(p.synthetic, false) AS synthetic"
    )
    return {r["key"]: r for r in ctx.read(cypher, keys=keys)}, cypher


def search_with_context(
    ctx: RetrieveContext,
    query: str,
    k: int = 8,
    hops: int = 1,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Vector-find chunks, return their *parents* with a structured neighbourhood each."""
    timer = Timer()
    vector = ctx.embed_query(query)
    rows = search_chunk_vectors(ctx, vector, k=k * 3)
    cyphers = [rows[0]["cypher"]] if rows else []

    # Anchor score is the best chunk that pointed at it: a parent with one excellent chunk
    # beats a parent with five mediocre ones, which is what a reader would say too.
    anchors: dict[str, dict[str, Any]] = {}
    for row in rows:
        node, score = row["node"], float(row["score"])
        key = node.get("parent_key")
        if not key:
            continue
        entry = anchors.setdefault(key, {"score": 0.0, "chunks": []})
        entry["score"] = max(entry["score"], score)
        entry["chunks"].append((score, node))

    top = sorted(anchors.items(), key=lambda kv: -kv[1]["score"])[:k]
    keys = [key for key, _ in top]
    anchor_props, anchor_cypher = _anchor_rows(ctx, keys)
    if anchor_cypher:
        cyphers.append(anchor_cypher)
    neighbour_rows, neighbour_cypher = neighbours(ctx, keys, hops=hops)
    if neighbour_cypher:
        cyphers.append(neighbour_cypher)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in neighbour_rows:
        grouped.setdefault(row["anchor"], []).append(row)

    items: list[Item] = []
    for key, entry in top:
        props = anchor_props.get(key)
        if props is None:
            continue  # a chunk whose parent left the graph: reported by not being returned
        label = props["label"]
        item = to_item(label, _anchor_node(label, key, props), entry["score"])
        item.props["neighbors"] = _neighbour_summary(grouped.get(key, []))
        item.props["neighbor_count"] = len(grouped.get(key, []))
        item.props["hops"] = hops
        item.provenance = [
            Provenance(
                chunk_id=node["id"],
                quote=(node.get("text") or "")[:280],
                source=node.get("parent_kind"),
            )
            for _score, node in sorted(entry["chunks"], key=lambda p: -p[0])[:2]
        ]
        items.append(item)

    # The best chunks travel too: an agent that must cite verbatim needs the text, and the
    # anchor items carry only a 280-character taste of it.
    for score, node in sorted(
        ((s, n) for e in anchors.values() for s, n in e["chunks"]), key=lambda p: -p[0]
    )[:EVIDENCE_CHUNKS]:
        items.append(to_item("Chunk", node, score))

    return finish(
        "s2",
        items,
        question=query,
        timer=timer,
        cypher_used=cyphers,
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )


def _anchor_node(label: str, key: str, props: dict[str, Any]) -> dict[str, Any]:
    """The anchor's row shaped as the label's own projection expects it."""
    node = {k: v for k, v in props.items() if k not in ("key", "label", "title")}
    if label == "Commit":
        node.update({"sha": key, "message": props.get("title")})
    else:
        node.update({"key": key, "title": props.get("title")})
    return node


def _neighbour_summary(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """`{relation: [neighbour, …]}` — structured context, not a sentence to be re-parsed."""
    out: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        rel = "→".join(row["path"])
        signature = (rel, str(row.get("key")))
        if signature in seen:
            continue
        seen.add(signature)
        entry = {"label": row["label"], "key": row.get("key"), "title": row.get("title")}
        if row.get("status"):
            entry["status"] = row["status"]
        if row.get("type"):
            entry["type"] = row["type"]
        edge = row.get("edge") or {}
        for prop in ("status", "type", "via", "reason"):
            if isinstance(edge, dict) and edge.get(prop) and prop not in entry:
                entry[f"edge_{prop}"] = edge[prop]
        out.setdefault(rel, []).append(entry)
    return out
