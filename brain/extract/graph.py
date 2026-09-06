"""Every write `brain extract merge` makes, and the two reads that audit it.

The shapes are the loaders' shapes: `UNWIND $rows MERGE` on a key, and an edge that
`MATCH`es both ends and creates neither. The second rule matters more here than anywhere
else — an extractor naming `KAFKA-99999` must not mint an empty work item, and a chunk id
that no longer exists must not mint a chunk. Both come back as counted, reported gaps.

`provenance_gaps()` is the live assertion of conventions rule 3: every LLM-derived edge
carries `evidence_chunk_ids`, `batch_id`, `model` and `extracted_at`, and a merge that
would leave one without them is a bug, not a warning.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from brain.extract.models import DERIVED_RELATION_TYPE, RELATION_TYPES
from brain.graph.context import GraphContext
from brain.graph.cypher import edge_merge, node_merge

ENTITY_LABEL = "Entity"
ENTITY_KEY = "id"
CHUNK_LABEL = "Chunk"
CHUNK_KEY = "id"
MODEL = "opus:kg-extractor"

#: What an extracted name may resolve to besides a new `Entity`, and the property each of
#: those nodes is keyed on (`brain load` merged them on exactly these).
NODE_KEYS: dict[str, str] = {
    "Entity": "id",
    "WorkItem": "key",
    "Document": "key",
    "Component": "name",
}

#: The relation types this step owns. `MENTIONS` is minted here, never by an agent.
LLM_RELATION_TYPES: tuple[str, ...] = (DERIVED_RELATION_TYPE, *RELATION_TYPES)

#: The four properties conventions rule 3 requires on every LLM-derived node and edge.
PROVENANCE_PROPS: tuple[str, ...] = ("evidence_chunk_ids", "batch_id", "model", "extracted_at")


def apply_extract_schema(ctx: GraphContext) -> dict[str, Any]:
    """`Entity.id` unique plus the two lookups resolve and retrieval will want.

    `brain load` already declares the `Entity.id` constraint; repeating it is what lets a
    scratch namespace run this step without running the load first.
    """
    statements = [
        f"CREATE CONSTRAINT {ctx.name('brain_entity_id_key')} IF NOT EXISTS "
        f"FOR (e:{ctx.label(ENTITY_LABEL)}) REQUIRE e.`id` IS UNIQUE",
        f"CREATE INDEX {ctx.name('brain_entity_kind_idx')} IF NOT EXISTS "
        f"FOR (e:{ctx.label(ENTITY_LABEL)}) ON (e.`kind`)",
        f"CREATE INDEX {ctx.name('brain_entity_norm_name_idx')} IF NOT EXISTS "
        f"FOR (e:{ctx.label(ENTITY_LABEL)}) ON (e.`norm_name`)",
    ]
    for cypher in statements:
        ctx.write(cypher)
    ctx.client.write("CALL db.awaitIndexes(300)")
    return {"constraints": 1, "indexes": 2}


SCHEMA_NAMES: tuple[str, ...] = (
    "brain_entity_id_key",
    "brain_entity_kind_idx",
    "brain_entity_norm_name_idx",
)


def drop_extract_schema(ctx: GraphContext) -> None:
    """Tests only: remove what a scratch namespace created."""
    for name in SCHEMA_NAMES:
        ctx.client.write(f"DROP CONSTRAINT {ctx.name(name)} IF EXISTS")
        ctx.client.write(f"DROP INDEX {ctx.name(name)} IF EXISTS")


# ------------------------------------------------------------------------------- reads


def existing_keys(ctx: GraphContext, label: str, values: Sequence[str]) -> set[str]:
    """Which of `values` the graph already holds under `label`. Empty in, empty out."""
    if not values:
        return set()
    prop = NODE_KEYS[label]
    rows = ctx.read(
        f"MATCH (n:{ctx.label(label)}) WHERE n.`{prop}` IN $values RETURN n.`{prop}` AS v",
        values=list(values),
    )
    return {r["v"] for r in rows}


def existing_chunk_ids(ctx: GraphContext, ids: Sequence[str]) -> set[str]:
    if not ids:
        return set()
    rows = ctx.read(
        f"MATCH (c:{ctx.label(CHUNK_LABEL)}) WHERE c.id IN $ids RETURN c.id AS id", ids=list(ids)
    )
    return {r["id"] for r in rows}


def component_names(ctx: GraphContext) -> dict[str, str]:
    """`{casefolded name: real name}` — an extractor writes `Connect`, the node is `connect`."""
    rows = ctx.read(f"MATCH (n:{ctx.label('Component')}) RETURN n.name AS name")
    return {r["name"].casefold(): r["name"] for r in rows if r["name"]}


# ------------------------------------------------------------------------------ writes


def write_entities(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> int:
    """MERGE on `Entity.id` (= `kind|norm_name`). `extracted_at` is set once, on create."""
    if not rows:
        return 0
    ctx.write_rows(node_merge(ctx, ENTITY_LABEL, ENTITY_KEY, on_create=True), rows)
    return len(rows)


def write_mentions(ctx: GraphContext, rows_by_label: dict[str, list[dict[str, Any]]]) -> int:
    """`(Chunk)-[:MENTIONS {quote}]->(target)`, one query per target label.

    One edge per (chunk, target) pair: the same chunk naming the same thing twice is one
    mention with the first quote, not two edges the reader has to de-duplicate.
    """
    total = 0
    for label, rows in sorted(rows_by_label.items()):
        if not rows:
            continue
        ctx.write_rows(
            edge_merge(
                ctx,
                (CHUNK_LABEL, CHUNK_KEY),
                DERIVED_RELATION_TYPE,
                (label, NODE_KEYS[label]),
                set_props=True,
            ),
            rows,
        )
        total += len(rows)
    return total


def write_relations(ctx: GraphContext, grouped: dict[tuple[str, str, str], list[dict]]) -> int:
    """One query per (type, source label, target label). Never creates an endpoint."""
    total = 0
    for (rel_type, src_label, dst_label), rows in sorted(grouped.items()):
        if not rows:
            continue
        ctx.write_rows(
            edge_merge(
                ctx,
                (src_label, NODE_KEYS[src_label]),
                rel_type,
                (dst_label, NODE_KEYS[dst_label]),
                set_props=True,
            ),
            rows,
        )
        total += len(rows)
    return total


def mark_weak_decisions(ctx: GraphContext) -> dict[str, int]:
    """`weak = true` on a Decision with no MOTIVATED_BY and no REJECTS (brief 07 decision 4).

    Recomputed over the whole graph on every merge, never per batch: a decision stated in
    one batch and motivated in another is not weak, and only the graph knows that.
    """
    rows = ctx.read(
        f"MATCH (e:{ctx.label(ENTITY_LABEL)}) WHERE e.kind = 'Decision' "
        "OPTIONAL MATCH (e)-[r:MOTIVATED_BY|REJECTS]->() "
        "WITH e, count(r) AS n RETURN e.id AS id, n AS supported"
    )
    weak = [{"id": r["id"]} for r in rows if r["supported"] == 0]
    strong = [{"id": r["id"]} for r in rows if r["supported"] > 0]
    for value, batch in ((True, weak), (False, strong)):
        if batch:
            ctx.write_rows(
                "UNWIND $rows AS row\n"
                f"MATCH (e:{ctx.label(ENTITY_LABEL)} {{`id`: row.id}})\n"
                f"SET e.weak = {str(value).lower()}",
                batch,
            )
    return {"decisions": len(rows), "weak": len(weak), "supported": len(strong)}


# ------------------------------------------------------------------------------ audits


#: Every label an edge of this step can start from. Relationship types are not namespaced,
#: so a scratch run and the production graph share `MENTIONS`; the label is what tells them
#: apart, and an audit that forgot it would read the other graph's edges.
TOUCHED_LABELS: tuple[str, ...] = (CHUNK_LABEL, *NODE_KEYS)


def _labelled(ctx: GraphContext, var: str) -> str:
    """A node this step could have touched, in this namespace and no other."""
    return " OR ".join(f"{var}:{ctx.label(label)}" for label in TOUCHED_LABELS)


def provenance_gaps(ctx: GraphContext) -> dict[str, Any]:
    """Every LLM-derived edge in this namespace that is missing a provenance property.

    The count must be zero. It is asserted after the merge rather than trusted from the
    rows we sent, because "the rows I meant to write" is exactly the thing a bug lies about.
    """
    missing = " OR ".join(f"r.`{p}` IS NULL" for p in PROVENANCE_PROPS)
    rows = ctx.read(
        "MATCH (a)-[r]->(b) "
        f"WHERE type(r) IN $types AND ({_labelled(ctx, 'a')}) "
        f"AND ({missing} OR size(r.evidence_chunk_ids) = 0) "
        "RETURN type(r) AS type, count(r) AS n ORDER BY type",
        types=list(LLM_RELATION_TYPES),
    )
    return {"total": sum(r["n"] for r in rows), "by_type": {r["type"]: r["n"] for r in rows}}


def census(ctx: GraphContext) -> dict[str, Any]:
    """What the graph holds now — read back, never inferred from what we meant to send."""
    entity = ctx.label(ENTITY_LABEL)
    total = ctx.read(f"MATCH (e:{entity}) RETURN count(e) AS c")[0]["c"]
    by_kind = {
        r["k"]: r["c"]
        for r in ctx.read(f"MATCH (e:{entity}) RETURN e.kind AS k, count(e) AS c ORDER BY k")
    }
    weak = ctx.read(f"MATCH (e:{entity}) WHERE e.weak RETURN count(e) AS c")[0]["c"]
    unmentioned = ctx.read(
        f"MATCH (e:{entity}) WHERE NOT ()-[:{DERIVED_RELATION_TYPE}]->(e) RETURN count(e) AS c"
    )[0]["c"]
    by_type: dict[str, int] = {}
    for rel_type in LLM_RELATION_TYPES:
        rows = ctx.read(
            f"MATCH (a)-[r:`{rel_type}`]->(b) WHERE ({_labelled(ctx, 'a')}) RETURN count(r) AS c"
        )
        by_type[rel_type] = rows[0]["c"] if rows else 0
    mentions_by_label = {
        label: ctx.read(
            f"MATCH (:{ctx.label(CHUNK_LABEL)})-[r:{DERIVED_RELATION_TYPE}]->(n:{ctx.label(label)})"
            " RETURN count(r) AS c"
        )[0]["c"]
        for label in NODE_KEYS
    }
    return {
        "entities": total,
        "entities_by_kind": by_kind,
        "weak_decisions": weak,
        "entities_without_mention": unmentioned,
        "edges_by_type": by_type,
        "mentions_by_target_label": mentions_by_label,
        "edges_total": sum(by_type.values()),
    }
