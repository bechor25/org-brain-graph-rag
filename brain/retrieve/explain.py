"""`explain_edge(src, dst)` — why the brain believes this.

Every LLM-derived edge in this graph carries `evidence_chunk_ids`, `batch_id`, `model` and
`extracted_at` (conventions rule 3), and every `SAME_AS` carries the `tier`, `score` and
`rule` that merged two nodes. None of that is visible in an answer. This tool makes it
visible: hand it two keys and it returns the edges between them with their provenance
spelled out, plus the chunks that name both — which is the evidence a *missing* edge would
have been built from.

The no-edge case is the interesting one and it does not return empty. It returns the
shortest path (up to three hops) and the co-mentioning chunks, because "these two are not
connected" and "these two are connected through a component every issue touches" are very
different facts about the graph, and a reviewer needs to tell them apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.lookup import resolve_key
from brain.retrieve.nodes import key_case, label_case
from brain.retrieve.pack import clip
from brain.retrieve.types import Item, Provenance, Result

#: The properties that make an edge auditable, in the order a reader wants them.
PROVENANCE_PROPS: tuple[str, ...] = (
    "evidence_chunk_ids",
    "quote",
    "batch_id",
    "model",
    "extracted_at",
    "tier",
    "score",
    "rule",
    "reason",
    "via",
    "type",
    "status",
    "at",
    "valid_from",
    "valid_to",
    "source",
)
MAX_PATH_HOPS = 3
MAX_SHARED_CHUNKS = 5


def _edges(ctx: RetrieveContext, src: tuple[str, str], dst: tuple[str, str]):
    src_label, src_key = src
    dst_label, dst_key = dst
    cypher = (
        f"MATCH (a:{ctx.label(src_label)}) WHERE {key_case('a', ctx.prefix)} = $src\n"
        f"MATCH (b:{ctx.label(dst_label)}) WHERE {key_case('b', ctx.prefix)} = $dst\n"
        "MATCH (a)-[r]-(b)\n"
        "RETURN type(r) AS type,\n"
        "  CASE WHEN startNode(r) = a THEN 'src->dst' ELSE 'dst->src' END AS direction,\n"
        "  properties(r) AS props"
    )
    return ctx.read(cypher, src=src_key, dst=dst_key), cypher


def _shared_chunks(ctx: RetrieveContext, src: tuple[str, str], dst: tuple[str, str]):
    src_label, src_key = src
    dst_label, dst_key = dst
    cypher = (
        f"MATCH (a:{ctx.label(src_label)}) WHERE {key_case('a', ctx.prefix)} = $src\n"
        f"MATCH (b:{ctx.label(dst_label)}) WHERE {key_case('b', ctx.prefix)} = $dst\n"
        f"MATCH (c:{ctx.label('Chunk')})-[ma:MENTIONS]->(a)\n"
        f"MATCH (c)-[mb:MENTIONS]->(b)\n"
        "WHERE coalesce(c.orphaned, false) = false\n"
        "RETURN c.id AS chunk_id, c.parent_key AS parent_key, ma.quote AS quote_src,\n"
        "  mb.quote AS quote_dst, ma.batch_id AS batch_id, ma.model AS model LIMIT $limit"
    )
    return ctx.read(cypher, src=src_key, dst=dst_key, limit=MAX_SHARED_CHUNKS), cypher


def _shortest_path(ctx: RetrieveContext, src: tuple[str, str], dst: tuple[str, str]):
    src_label, src_key = src
    dst_label, dst_key = dst
    cypher = (
        f"MATCH (a:{ctx.label(src_label)}) WHERE {key_case('a', ctx.prefix)} = $src\n"
        f"MATCH (b:{ctx.label(dst_label)}) WHERE {key_case('b', ctx.prefix)} = $dst\n"
        f"MATCH p = shortestPath((a)-[*1..{MAX_PATH_HOPS}]-(b))\n"
        "RETURN [r IN relationships(p) | type(r)] AS types,\n"
        f"  [n IN nodes(p) | {key_case('n', ctx.prefix)}] AS keys,\n"
        f"  [n IN nodes(p) | {label_case('n', ctx.prefix)}] AS labels LIMIT 1"
    )
    return ctx.read(cypher, src=src_key, dst=dst_key), cypher


def explain_edge(
    ctx: RetrieveContext,
    src: str,
    dst: str,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Every edge between `src` and `dst`, with the provenance that justifies it."""
    timer = Timer()
    src_label, src_row, src_cypher = resolve_key(ctx, src)
    dst_label, dst_row, dst_cypher = resolve_key(ctx, dst)
    a, b = (src_label, src_row["key"]), (dst_label, dst_row["key"])

    edges, edge_cypher = _edges(ctx, a, b)
    shared, shared_cypher = _shared_chunks(ctx, a, b)
    cyphers = [src_cypher, dst_cypher, edge_cypher, shared_cypher]

    items: list[Item] = []
    for index, edge in enumerate(edges):
        props = {k: v for k, v in (edge["props"] or {}).items() if k in PROVENANCE_PROPS}
        missing = [
            p for p in ("evidence_chunk_ids", "batch_id", "model", "extracted_at") if p not in props
        ]
        items.append(
            Item(
                kind="Row",
                key=f"{src_row['key']}-[{edge['type']}]-{dst_row['key']}",
                title=f"{edge['type']} ({edge['direction']})",
                snippet=clip(props.get("quote") or props.get("reason") or "", 280)
                or f"{src_row['key']} {edge['type']} {dst_row['key']}",
                score=round(1.0 - index / 100, 4),
                props={
                    "type": edge["type"],
                    "direction": edge["direction"],
                    "src": src_row["key"],
                    "dst": dst_row["key"],
                    "provenance_props": {k: str(v) for k, v in props.items()},
                    "missing_provenance": missing,
                    "llm_derived": "model" in props,
                },
                provenance=[
                    Provenance(
                        chunk_id=chunk_id,
                        quote=props.get("quote"),
                        batch_id=props.get("batch_id"),
                        model=props.get("model"),
                    )
                    for chunk_id in (props.get("evidence_chunk_ids") or [])[:5]
                ],
            )
        )

    if not edges:
        rows, path_cypher = _shortest_path(ctx, a, b)
        cyphers.append(path_cypher)
        path = rows[0] if rows else None
        items.append(
            Item(
                kind="Row",
                key=f"{src_row['key']}~{dst_row['key']}",
                title="no direct edge",
                snippet=(
                    " → ".join(
                        f"{k} -[{t}]-"
                        for k, t in zip(path["keys"], [*path["types"], ""], strict=False)
                    )
                    + f" {path['keys'][-1]}"
                    if path
                    else f"no path of {MAX_PATH_HOPS} hops or fewer"
                ),
                score=0.5,
                props={
                    "src": src_row["key"],
                    "dst": dst_row["key"],
                    "direct_edges": 0,
                    "path_types": path["types"] if path else [],
                    "path_keys": path["keys"] if path else [],
                    "path_labels": path["labels"] if path else [],
                },
            )
        )

    for row in shared:
        items.append(
            Item(
                kind="Chunk",
                key=row["chunk_id"],
                title=f"{row['parent_key']} mentions both",
                snippet=clip(row["quote_src"] or row["quote_dst"] or ""),
                score=0.4,
                props={"parent_key": row["parent_key"], "mentions": [src, dst]},
                provenance=[
                    Provenance(
                        chunk_id=row["chunk_id"],
                        quote=row["quote_src"] or row["quote_dst"],
                        batch_id=row["batch_id"],
                        model=row["model"],
                        source=row["parent_key"],
                    )
                ],
            )
        )

    return finish(
        "lookup",
        items,
        question=f"explain_edge({src}, {dst})",
        timer=timer,
        cypher_used=cyphers,
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )
