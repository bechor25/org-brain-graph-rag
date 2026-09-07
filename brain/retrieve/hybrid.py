"""S1 — hybrid chunk search. The baseline every other strategy has to beat.

Vector plus fulltext, fused with RRF, no graph. That last part is the point: S1 is the
control in the experiment. If the graph strategies do not beat a good hybrid baseline on
the same corpus, the same embedder and the same 4k-token budget, then the graph is not
earning its cost, and Plan 3 should be able to say so with numbers rather than taste.

Why both halves are needed, in one example. `bge-m3` embeds `KAFKA-15123` as an
unremarkable identifier — a vector search for it returns chunks *about* consumer groups.
Lucene returns the chunks that literally contain the string. Neither is right alone: the
first misses the anchor, the second misses the paraphrase, and RRF prefers the chunks both
agree on.

The parent lookup at the end is what turns a chunk into a citation. A `Chunk` id is not
something a person can check; `KAFKA-15123 · description` is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.nodes import to_item
from brain.retrieve.types import Item, Result, RetrieveError
from brain.retrieve.vector import rrf, search_chunk_fulltext, search_chunk_vectors

MODES = ("hybrid", "vector", "fulltext")


def _parent_titles(ctx: RetrieveContext, rows: list[dict[str, Any]]) -> tuple[dict, str]:
    """`{chunk id: parent}` in one query — a per-chunk lookup would be `k` round trips."""
    ids = [r["node"]["id"] for r in rows]
    if not ids:
        return {}, ""
    cypher = (
        f"MATCH (p)-[:HAS_CHUNK]->(c:{ctx.label('Chunk')}) WHERE c.id IN $ids\n"
        "RETURN c.id AS id, head(labels(p)) AS label, "
        "coalesce(p.key, p.sha, p.id) AS key, coalesce(p.title, p.message) AS title, "
        "p.status AS status, p.type AS type, coalesce(p.synthetic, false) AS synthetic"
    )
    return {r["id"]: r for r in ctx.read(cypher, ids=ids)}, cypher


def _rank(rows: list[dict[str, Any]]) -> list[str]:
    return [r["node"]["id"] for r in rows]


def search_chunks(
    ctx: RetrieveContext,
    query: str,
    k: int = 10,
    mode: str = "hybrid",
    rerank: bool = False,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Top-`k` chunks for `query`. `mode` picks vector, fulltext or their RRF fusion."""
    if mode not in MODES:
        raise RetrieveError(f"mode must be one of {MODES}, got {mode!r}")
    timer = Timer()
    cyphers: list[str] = []
    vector_rows: list[dict[str, Any]] = []
    text_rows: list[dict[str, Any]] = []

    if mode in ("hybrid", "vector"):
        vector = ctx.embed_query(query)
        vector_rows = search_chunk_vectors(ctx, vector, k=k * 2 if mode == "hybrid" else k)
        if vector_rows:
            cyphers.append(vector_rows[0]["cypher"])
    if mode in ("hybrid", "fulltext"):
        text_rows = search_chunk_fulltext(ctx, query, k=k * 2 if mode == "hybrid" else k)
        if text_rows:
            cyphers.append(text_rows[0]["cypher"])

    by_id: dict[str, dict[str, Any]] = {}
    for row in (*vector_rows, *text_rows):
        by_id.setdefault(row["node"]["id"], row["node"])

    if mode == "hybrid":
        fused = rrf([_rank(vector_rows), _rank(text_rows)])
    elif mode == "vector":
        fused = {r["node"]["id"]: float(r["score"]) for r in vector_rows}
    else:
        fused = {r["node"]["id"]: float(r["score"]) for r in text_rows}

    top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
    rows = [{"node": by_id[i], "score": s} for i, s in top if i in by_id]
    if rerank:
        rows = _rerank(query, rows)

    parents, parent_cypher = _parent_titles(ctx, rows)
    if parent_cypher:
        cyphers.append(parent_cypher)

    items: list[Item] = []
    for row in rows:
        item = to_item("Chunk", row["node"], row["score"])
        parent = parents.get(item.key)
        if parent:
            item.props["parent_label"] = parent["label"]
            item.props["parent_title"] = parent["title"]
            if parent.get("status"):
                item.props["parent_status"] = parent["status"]
            item.props["synthetic"] = bool(parent.get("synthetic"))
            item.title = f"{parent['key']} · {item.props.get('kind', 'chunk')}"
            for prov in item.provenance:
                prov.source = str(parent["key"])
        items.append(item)

    return finish(
        "s1",
        items,
        question=query,
        timer=timer,
        cypher_used=cyphers,
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )


def _rerank(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-encoder rerank if Task 2's `rerank.py` is installed; otherwise unchanged.

    Plan decision 5: a missing reranker model is a warning and a fallback, never a failure
    — the flag exists so the evaluation can measure with and without, and an evaluation
    that crashes on the machine without `sentence-transformers` measures nothing.
    """
    try:
        from brain.retrieve.rerank import rerank_rows  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - the module arrives in Task 2
        return rows
    try:
        return rerank_rows(query, rows)
    except Exception:  # noqa: BLE001 - a model that will not load is not a retrieval failure
        return rows
