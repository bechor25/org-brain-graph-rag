"""A whole retrieval stack over the mini fixture, in a label namespace of its own.

`brain load` and `brain chunk` build the structural half; this file builds the half
`brain extract` would build — five entities, the rationale edges between them and their
`MENTIONS` quotes — because S3 has nothing to walk without it and the mini fixture is not
an LLM. The entities are written with the same provenance properties conventions rule 3
requires of a real extraction, so a test that asserts "every item has a checkable chunk
id" is asserting the same thing here as in production.

Everything is namespaced `_Retr…`, so this neither reads nor damages the 13,846-chunk
real graph.
"""

from __future__ import annotations

import time
from typing import Any

from brain.embed.client import OllamaEmbedder
from brain.graph.context import GraphContext

PREFIX = "_Retr"
MODEL = "opus:kg-extractor"
BATCH = "mini-001"
EXTRACTED_AT = "2026-09-07T00:00:00+00:00"

#: id -> (kind, name, description). Ids are `kind|norm_name`, as `brain extract` writes them.
ENTITIES: dict[str, tuple[str, str, str]] = {
    "Decision|move assignment to the group coordinator": (
        "Decision",
        "move assignment to the group coordinator",
        "Consumers send heartbeats and the coordinator computes assignments.",
    ),
    "Alternative|keep client side assignment": (
        "Alternative",
        "keep client-side assignment",
        "Client-side assignment with incremental cooperative rebalancing.",
    ),
    "Problem|long rebalances": (
        "Problem",
        "long rebalances",
        "The classic protocol makes clients do assignment, which causes long rebalances.",
    ),
    "Feature|fencing of stale epochs": (
        "Feature",
        "fencing of stale epochs",
        "Members with stale epochs are fenced and must rejoin.",
    ),
    "Technology|group coordinator": (
        "Technology",
        "group coordinator",
        "The broker-side component that owns consumer group state.",
    ),
}

#: (source label, source key, relation, target label, target key)
RELATIONS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "Document",
        "KIP-5",
        "DECIDES",
        "Entity",
        "Decision|move assignment to the group coordinator",
    ),
    (
        "Entity",
        "Decision|move assignment to the group coordinator",
        "MOTIVATED_BY",
        "Entity",
        "Problem|long rebalances",
    ),
    ("Document", "KIP-5", "REJECTS", "Entity", "Alternative|keep client side assignment"),
    ("WorkItem", "KAFKA-100", "IMPLEMENTS", "Entity", "Feature|fencing of stale epochs"),
    (
        "Entity",
        "Feature|fencing of stale epochs",
        "DEPENDS_ON",
        "Entity",
        "Technology|group coordinator",
    ),
)

#: entity id -> the quote a chunk of that parent supports it with.
QUOTES: dict[str, tuple[str, str]] = {
    "Decision|move assignment to the group coordinator": (
        "KIP-5",
        "Move assignment to the group coordinator.",
    ),
    "Alternative|keep client side assignment": (
        "KIP-5",
        "Keeping client-side assignment with incremental cooperative rebalancing was rejected",
    ),
    "Problem|long rebalances": (
        "KIP-5",
        "The classic protocol makes clients do assignment, which causes long rebalances.",
    ),
    "Feature|fencing of stale epochs": (
        "KIP-5",
        "Members with stale epochs are fenced and must rejoin.",
    ),
    "Technology|group coordinator": (
        "KAFKA-101",
        "restarting the coordinator triggers repeated rebalances",
    ),
}

KEY_PROP = {"Document": "key", "WorkItem": "key", "Entity": "id", "Chunk": "id"}


def entity_index_name(ctx: GraphContext) -> str:
    return f"{ctx.prefix}entity_embedding"


def build_extract_layer(ctx: GraphContext, embedder: OllamaEmbedder, dim: int) -> dict[str, Any]:
    """Write the entities, their edges, their `MENTIONS` quotes and the vector index."""
    ctx.write(
        f"CREATE CONSTRAINT {ctx.name('brain_entity_id_key')} IF NOT EXISTS "
        f"FOR (e:{ctx.label('Entity')}) REQUIRE e.`id` IS UNIQUE"
    )
    ctx.write(
        f"CREATE VECTOR INDEX `{entity_index_name(ctx)}` IF NOT EXISTS "
        f"FOR (e:{ctx.label('Entity')}) ON (e.`embedding`) "
        "OPTIONS {indexConfig: {`vector.dimensions`: " + str(dim) + ", "
        "`vector.similarity_function`: 'cosine'}}"
    )

    rows = [
        {
            "key": entity_id,
            "props": {
                "id": entity_id,
                "kind": kind,
                "name": name,
                "norm_name": entity_id.split("|", 1)[1],
                "description": description,
                "synthetic": False,
                "weak": kind == "Decision" and entity_id not in _motivated(),
                "batch_id": BATCH,
                "model": MODEL,
                "extracted_at": EXTRACTED_AT,
            },
        }
        for entity_id, (kind, name, description) in ENTITIES.items()
    ]
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MERGE (e:{ctx.label('Entity')} {{`id`: row.key}})\n"
        "SET e += row.props",
        rows,
    )

    chunks = _pick_chunks(ctx)
    for entity_id, (parent, quote) in QUOTES.items():
        chunk_id = chunks.get(parent)
        if not chunk_id:
            continue
        ctx.write(
            f"MATCH (c:{ctx.label('Chunk')} {{`id`: $chunk}})\n"
            f"MATCH (e:{ctx.label('Entity')} {{`id`: $entity}})\n"
            "MERGE (c)-[m:MENTIONS]->(e)\n"
            "SET m += $props",
            chunk=chunk_id,
            entity=entity_id,
            props={
                "quote": quote,
                "evidence_chunk_ids": [chunk_id],
                "batch_id": BATCH,
                "model": MODEL,
                "extracted_at": EXTRACTED_AT,
            },
        )
        ctx.write(
            f"MATCH (e:{ctx.label('Entity')} {{`id`: $entity}}) "
            "SET e.evidence_chunk_ids = [$chunk]",
            entity=entity_id,
            chunk=chunk_id,
        )

    for src_label, src_key, rel, dst_label, dst_key in RELATIONS:
        chunk_id = chunks.get(QUOTES.get(dst_key, ("KIP-5", ""))[0]) or ""
        ctx.write(
            f"MATCH (a:{ctx.label(src_label)} {{`{KEY_PROP[src_label]}`: $src}})\n"
            f"MATCH (b:{ctx.label(dst_label)} {{`{KEY_PROP[dst_label]}`: $dst}})\n"
            f"MERGE (a)-[r:`{rel}`]->(b)\n"
            "SET r += $props",
            src=src_key,
            dst=dst_key,
            props={
                "evidence_chunk_ids": [chunk_id] if chunk_id else [],
                "batch_id": BATCH,
                "model": MODEL,
                "extracted_at": EXTRACTED_AT,
            },
        )

    vectors = embedder.embed(
        [f"{name}. {description}" for _kind, name, description in ENTITIES.values()]
    )
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (e:{ctx.label('Entity')} {{`id`: row.id}})\n"
        "CALL db.create.setNodeVectorProperty(e, 'embedding', row.vector)",
        [{"id": key, "vector": v} for key, v in zip(ENTITIES, vectors, strict=True)],
    )
    ctx.write(
        f"MERGE (m:{ctx.label('IndexMeta')} {{`name`: $name}}) SET m += $props",
        name=entity_index_name(ctx),
        props={"model": embedder.model, "dim": dim, "similarity": "cosine"},
    )
    _await(ctx, entity_index_name(ctx))
    return {"entities": len(ENTITIES), "relations": len(RELATIONS), "chunks": chunks}


def _await(ctx: GraphContext, name: str, timeout_s: float = 120.0) -> str:
    """Wait for *this* index. `db.awaitIndexes` would wait for every agent's namespace too."""
    deadline = time.monotonic() + timeout_s
    while True:
        rows = ctx.read("SHOW INDEXES YIELD name, state WHERE name = $name RETURN state", name=name)
        state = rows[0]["state"] if rows else "MISSING"
        if state == "ONLINE" or time.monotonic() > deadline:
            return state
        time.sleep(0.2)


def _motivated() -> set[str]:
    """Entity ids that carry a reason — everything else is a `weak` decision (81% in prod)."""
    return {
        src_key for _sl, src_key, rel, _dl, _dk in RELATIONS if rel in ("MOTIVATED_BY", "REJECTS")
    }


def _pick_chunks(ctx: GraphContext) -> dict[str, str]:
    """One chunk per parent, the first by position — a stable evidence id for the tests."""
    rows = ctx.read(
        f"MATCH (c:{ctx.label('Chunk')}) WHERE coalesce(c.orphaned, false) = false "
        "RETURN c.parent_key AS parent, c.id AS id, c.position AS position "
        "ORDER BY c.parent_key, c.position"
    )
    out: dict[str, str] = {}
    for row in rows:
        out.setdefault(row["parent"], row["id"])
    return out


def drop_extract_layer(ctx: GraphContext) -> None:
    ctx.client.write(f"MATCH (e:{ctx.label('Entity')}) DETACH DELETE e")
    ctx.client.write(f"DROP INDEX `{entity_index_name(ctx)}` IF EXISTS")
    ctx.client.write(f"DROP CONSTRAINT {ctx.name('brain_entity_id_key')} IF EXISTS")
