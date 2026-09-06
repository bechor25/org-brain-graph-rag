"""Everything this step writes to Neo4j, and the two reads that make a rerun cheap.

The write is `UNWIND $rows MERGE` on `Chunk.id`, like every other loader, and for the
same reason: the id is `sha1(parent_key|kind|position|text)`, so an unchanged text merges
onto the node it produced last time and a changed text produces a new node. The second
run of `brain chunk` therefore creates nothing and — because `state()` reports which ids
already carry a vector — embeds nothing.

The vector index is created before the vectors are written and awaited after, which is
the cheap ordering: Neo4j populates a vector index in the background, and a `MERGE` that
races an index build is slower than one that follows it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from brain.chunk.chunker import Chunk
from brain.graph.context import GraphContext
from brain.graph.cypher import edge_merge, node_merge

LABEL = "Chunk"
KEY = "id"
META_LABEL = "IndexMeta"
INDEX_NAME = "chunk_embedding"
EMBEDDING_PROP = "embedding"

#: Which node a chunk hangs off, by `parent_kind`.
PARENTS: dict[str, tuple[str, str]] = {
    "Document": ("Document", "key"),
    "WorkItem": ("WorkItem", "key"),
    "Commit": ("Commit", "sha"),
}


@dataclass(frozen=True)
class ChunkState:
    """What the graph already knows: which ids exist, carry a vector, or are orphaned."""

    hashes: dict[str, str]
    embedded: set[str]
    orphaned: frozenset[str] = frozenset()

    def needs_embedding(self, chunk: Chunk) -> bool:
        return chunk.id not in self.embedded or self.hashes.get(chunk.id) != chunk.hash

    def is_current(self, chunk: Chunk) -> bool:
        """True when the stored node is already byte-for-byte what we would write.

        An orphaned node is never current even when its hash matches: the text came back,
        so `orphaned` has to be cleared, and clearing it means writing the row.
        """
        return self.hashes.get(chunk.id) == chunk.hash and chunk.id not in self.orphaned


def apply_chunk_schema(ctx: GraphContext, dim: int) -> dict[str, Any]:
    """Constraints, the parent lookup index and the vector index. All `IF NOT EXISTS`.

    `Chunk.id` is already constrained by `brain load`'s schema; repeating it here is what
    lets a test namespace (`_SmokeChunk`) run this step without running the load first.
    """
    if not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"vector dimension must be a positive int, got {dim!r}")
    statements = [
        f"CREATE CONSTRAINT {ctx.name('brain_chunk_id_key')} IF NOT EXISTS "
        f"FOR (c:{ctx.label(LABEL)}) REQUIRE c.`id` IS UNIQUE",
        f"CREATE CONSTRAINT {ctx.name('brain_indexmeta_name_key')} IF NOT EXISTS "
        f"FOR (m:{ctx.label(META_LABEL)}) REQUIRE m.`name` IS UNIQUE",
        f"CREATE INDEX {ctx.name('brain_chunk_parent_key_idx')} IF NOT EXISTS "
        f"FOR (c:{ctx.label(LABEL)}) ON (c.`parent_key`)",
        f"CREATE INDEX {ctx.name('brain_chunk_kind_idx')} IF NOT EXISTS "
        f"FOR (c:{ctx.label(LABEL)}) ON (c.`kind`)",
        # Index options take literals, not parameters; `dim` is checked as an int above.
        f"CREATE VECTOR INDEX {ctx.name(INDEX_NAME)} IF NOT EXISTS "
        f"FOR (c:{ctx.label(LABEL)}) ON (c.`{EMBEDDING_PROP}`) "
        "OPTIONS {indexConfig: {`vector.dimensions`: " + str(dim) + ", "
        "`vector.similarity_function`: 'cosine'}}",
    ]
    for cypher in statements:
        ctx.write(cypher)
    ctx.client.write("CALL db.awaitIndexes(300)")
    return {
        "constraints": 2,
        "indexes": 2,
        "vector_index": index_name(ctx),
        "dim": dim,
        "similarity": "cosine",
    }


#: Constraint and index names this step owns, in creation order.
SCHEMA_NAMES: tuple[str, ...] = (
    "brain_chunk_id_key",
    "brain_indexmeta_name_key",
    "brain_chunk_parent_key_idx",
    "brain_chunk_kind_idx",
    INDEX_NAME,
)


def drop_chunk_schema(ctx: GraphContext) -> None:
    """Tests only: remove what a scratch namespace created, in either flavour."""
    for name in SCHEMA_NAMES:
        ctx.client.write(f"DROP CONSTRAINT {ctx.name(name)} IF EXISTS")
        ctx.client.write(f"DROP INDEX {ctx.name(name)} IF EXISTS")


def index_name(ctx: GraphContext) -> str:
    return f"{ctx.prefix}{INDEX_NAME}"


def index_status(ctx: GraphContext) -> dict[str, Any]:
    rows = ctx.read(
        "SHOW INDEXES YIELD name, type, state, populationPercent, entityType, "
        "labelsOrTypes, properties, options WHERE name = $name "
        "RETURN name, type, state, populationPercent, labelsOrTypes, properties, options",
        name=index_name(ctx),
    )
    if not rows:
        return {"name": index_name(ctx), "state": "MISSING"}
    row = rows[0]
    config = (row.get("options") or {}).get("indexConfig", {})
    return {
        "name": row["name"],
        "type": row["type"],
        "state": row["state"],
        "population_percent": row["populationPercent"],
        "labels": row["labelsOrTypes"],
        "properties": row["properties"],
        "dim": config.get("vector.dimensions"),
        "similarity": config.get("vector.similarity_function"),
    }


def state(ctx: GraphContext) -> ChunkState:
    """One pass over the existing chunks: hash, vector, orphan flag. No text is read back —
    10,884 chunk bodies would be ~14 MB over the wire to decide three booleans."""
    rows = ctx.read(
        f"MATCH (c:{ctx.label(LABEL)}) "
        f"RETURN c.id AS id, c.hash AS hash, c.{EMBEDDING_PROP} IS NOT NULL AS embedded, "
        "coalesce(c.orphaned, false) AS orphaned"
    )
    return ChunkState(
        hashes={r["id"]: r["hash"] for r in rows},
        embedded={r["id"] for r in rows if r["embedded"]},
        orphaned=frozenset(r["id"] for r in rows if r["orphaned"]),
    )


def existing_ids(ctx: GraphContext) -> set[str]:
    return {r["id"] for r in ctx.read(f"MATCH (c:{ctx.label(LABEL)}) RETURN c.id AS id")}


def write_nodes(
    ctx: GraphContext, chunks: Sequence[Chunk], state: ChunkState | None = None
) -> dict[str, int]:
    """MERGE the chunks the graph does not already hold unchanged.

    `Chunk.id` is `sha1(parent_key|kind|position|text)` and `hash` is `sha1(text)`, so an
    id already present with the same hash is byte-for-byte the node we would write. Sending
    it anyway is not wrong — `MERGE` is idempotent — it is just 152,000 `properties_set`
    for zero work, which buries a real rerun's counters in noise.
    """
    rows = [
        {"key": c.id, "props": c.props()}
        for c in chunks
        if state is None or not state.is_current(c)
    ]
    if rows:
        ctx.write_rows(node_merge(ctx, LABEL, KEY), rows)
    return {
        "total": len(chunks),
        "written": len(rows),
        "skipped_unchanged": len(chunks) - len(rows),
    }


def write_edges(ctx: GraphContext, chunks: Iterable[Chunk]) -> dict[str, int]:
    """`(parent)-[:HAS_CHUNK]->(chunk)`, one query per parent label.

    Like every edge in this pipeline it matches both ends and creates neither: a chunk of
    a work item that is not in the graph gets no edge, and the gap shows up as an orphan
    in the census rather than as an invented `WorkItem`.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for c in chunks:
        grouped.setdefault(c.parent_kind, []).append({"src": c.parent_key, "dst": c.id})
    out: dict[str, int] = {}
    for parent_kind, rows in sorted(grouped.items()):
        target = PARENTS.get(parent_kind)
        if target is None:
            raise ValueError(f"no parent node kind for {parent_kind!r}")
        ctx.write_rows(edge_merge(ctx, target, "HAS_CHUNK", (LABEL, KEY)), rows)
        out[parent_kind] = len(rows)
    return out


def write_embeddings(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> int:
    """`db.create.setNodeVectorProperty` rather than `SET`: it stores the float32 array
    the vector index reads, and rejects a vector of the wrong shape at write time."""
    if not rows:
        return 0
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (c:{ctx.label(LABEL)} {{`id`: row.id}})\n"
        f"CALL db.create.setNodeVectorProperty(c, '{EMBEDDING_PROP}', row.vector)",
        rows,
    )
    return len(rows)


def mark_orphans(ctx: GraphContext, orphan_ids: Sequence[str]) -> int:
    """A chunk whose parent text changed keeps its node and gains `orphaned = true`.

    Deleting would be cheaper and wrong: `brain extract` will hang evidence off chunk ids,
    and a deleted chunk turns an entity's provenance into a dangling reference. Cleaning
    up orphans is a decision for the step that knows what still cites them.
    """
    if not orphan_ids:
        return 0
    rows = [{"id": i} for i in orphan_ids]
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (c:{ctx.label(LABEL)} {{`id`: row.id}})\n"
        "SET c.orphaned = true",
        rows,
    )
    return len(rows)


def write_index_meta(ctx: GraphContext, props: dict[str, Any], now: str) -> None:
    """`created_at` is set once and never rewritten; everything else is the current run.

    `ON CREATE SET` rather than a read-then-write: reading `m.created_at` before the node
    exists makes the server warn about six property keys it has never seen, on every first
    run, which is a lot of noise to pay for a value the MERGE can decide by itself.
    """
    ctx.write(
        f"MERGE (m:{ctx.label(META_LABEL)} {{`name`: $name}})\n"
        "ON CREATE SET m.created_at = $now\n"
        "SET m += $props",
        name=index_name(ctx),
        now=now,
        props=props,
    )


def read_index_meta(ctx: GraphContext) -> dict[str, Any] | None:
    rows = ctx.read(
        f"MATCH (m:{ctx.label(META_LABEL)} {{`name`: $name}}) "
        "RETURN m.name AS name, m.model AS model, m.dim AS dim, "
        "toString(m.created_at) AS created_at, toString(m.updated_at) AS updated_at, "
        "m.chunk_count AS chunk_count, m.similarity AS similarity",
        name=index_name(ctx),
    )
    return rows[0] if rows else None


def census(ctx: GraphContext) -> dict[str, Any]:
    """What the graph holds now — read back, never inferred from what we meant to send."""
    label = ctx.label(LABEL)
    total = ctx.read(f"MATCH (c:{label}) RETURN count(c) AS c")[0]["c"]
    embedded = ctx.read(
        f"MATCH (c:{label}) WHERE c.{EMBEDDING_PROP} IS NOT NULL RETURN count(c) AS c"
    )[0]["c"]
    orphaned = ctx.read(f"MATCH (c:{label}) WHERE c.orphaned RETURN count(c) AS c")[0]["c"]
    unlinked = ctx.read(f"MATCH (c:{label}) WHERE NOT ()-[:HAS_CHUNK]->(c) RETURN count(c) AS c")[
        0
    ]["c"]
    # Split live from orphaned: `chunks` counts everything the graph holds, and a reader
    # comparing it with `chunking.by_kind` (this run's output) would otherwise read the
    # difference as a bug rather than as the previous run's superseded text.
    live = "WHERE NOT coalesce(c.orphaned, false)"

    def census_by(prop: str, where: str = "") -> dict[str, int]:
        rows = ctx.read(f"MATCH (c:{label}) {where} RETURN c.{prop} AS k, count(c) AS c ORDER BY k")
        return {r["k"]: r["c"] for r in rows}

    by_kind = census_by("kind", live)
    by_parent = census_by("parent_kind", live)
    by_lang = census_by("lang", live)
    orphaned_by_kind = census_by("kind", "WHERE c.orphaned")
    edges = {}
    for parent_kind, (parent_label, _key) in PARENTS.items():
        edges[parent_kind] = ctx.read(
            f"MATCH (:{ctx.label(parent_label)})-[r:HAS_CHUNK]->(:{label}) RETURN count(r) AS c"
        )[0]["c"]
    return {
        "chunks": total,
        "live": total - orphaned,
        "embedded": embedded,
        "missing_embedding": total - embedded,
        "orphaned": orphaned,
        "orphaned_by_kind": orphaned_by_kind,
        "without_has_chunk": unlinked,
        "by_kind": by_kind,
        "by_parent_kind": by_parent,
        "by_lang": by_lang,
        "has_chunk_edges": edges,
        "has_chunk_total": sum(edges.values()),
    }


def query_similar(ctx: GraphContext, vector: Sequence[float], k: int = 5) -> list[dict[str, Any]]:
    """Top-`k` live chunks by cosine similarity. Read-only.

    Orphans stay in the index — they are still valid evidence for whatever `brain extract`
    cited them for — but they are text that no longer exists on any page, so a search
    result must never be one. The index is asked for extra candidates and the orphans are
    dropped, rather than filtering after the fact and returning fewer than `k`.
    """
    rows = ctx.read(
        "CALL db.index.vector.queryNodes($name, $k, $vector) YIELD node, score "
        "WHERE coalesce(node.orphaned, false) = false "
        "RETURN node.id AS id, node.parent_key AS parent_key, node.parent_kind AS parent_kind, "
        "node.kind AS kind, node.heading AS heading, node.text AS text, score "
        "ORDER BY score DESC LIMIT $k",
        name=index_name(ctx),
        k=k,
        vector=list(vector),
    )
    if len(rows) == k:
        return rows
    # Some of the top `k` were orphans; widen once rather than paging blindly.
    return ctx.read(
        "CALL db.index.vector.queryNodes($name, $wide, $vector) YIELD node, score "
        "WHERE coalesce(node.orphaned, false) = false "
        "RETURN node.id AS id, node.parent_key AS parent_key, node.parent_kind AS parent_kind, "
        "node.kind AS kind, node.heading AS heading, node.text AS text, score "
        "ORDER BY score DESC LIMIT $k",
        name=index_name(ctx),
        k=k,
        wide=k * 5,
        vector=list(vector),
    )
