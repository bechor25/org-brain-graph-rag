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

import time
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


SCHEMA_NAMES: tuple[str, ...] = (
    "brain_entity_id_key",
    "brain_entity_kind_idx",
    "brain_entity_norm_name_idx",
)

#: How long to wait for those three to come online. The same ceiling the
#: `CALL db.awaitIndexes(300)` this replaced used — but spent on three named indexes
#: instead of on every index in the database.
SCHEMA_TIMEOUT_S = 300.0


def schema_states(ctx: GraphContext) -> dict[str, str]:
    """`{index name: state}` for the three names this step owns, and no others.

    A uniqueness constraint's backing index carries the constraint's own name, so all
    three appear in `SHOW INDEXES` under exactly the names :data:`SCHEMA_NAMES` declares.
    A name the server has never heard of reads as `MISSING` rather than vanishing from
    the answer — "not there yet" and "online" must not look alike to the caller.
    """
    names = [f"{ctx.prefix}{name}" for name in SCHEMA_NAMES]
    rows = ctx.read(
        "SHOW INDEXES YIELD name, state WHERE name IN $names RETURN name, state", names=names
    )
    found = {str(r["name"]): str(r["state"]) for r in rows}
    return {name: found.get(name, "MISSING") for name in names}


def await_extract_schema(
    ctx: GraphContext, timeout_s: float = SCHEMA_TIMEOUT_S, sleep_s: float = 0.25
) -> dict[str, Any]:
    """Wait for *these three* indexes to come online. Never for anyone else's.

    `CALL db.awaitIndexes` is database-wide: it waits for every index the database holds,
    including the ones the other agents' scratch namespaces left half-built, and it raises
    on theirs. That is the Plan 1 closing-review rule — a step waits on the names it owns.

    A timeout is *reported*, not raised, and that is the one place this differs from
    :func:`brain.resolve.graph.await_index`. Resolve reads its index with
    `db.index.vector.queryNodes`, which answers from a half-built index without saying so,
    so a wrong answer is indistinguishable from a right one and the step must stop. Here
    the three are a uniqueness constraint (enforced from the moment it exists, index state
    or not) and two lookups; a POPULATING one costs the merge a scan, not its correctness.
    Stopping the merge over it would throw away a valid run, so the state travels into the
    report instead — `report["schema"]["await"]`.
    """
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    while True:
        states = schema_states(ctx)
        not_online = sorted(n for n, s in states.items() if s != "ONLINE")
        if not not_online or time.monotonic() >= deadline:
            break
        time.sleep(sleep_s)
    waited_ms = round((time.monotonic() - started) * 1000)
    out: dict[str, Any] = {
        "states": states,
        "online": len(states) - len(not_online),
        "not_online": not_online,
        "ready": not not_online,
        "timed_out": bool(not_online),
        "timeout_s": timeout_s,
        "waited_ms": waited_ms,
    }
    if not_online:
        out["warning"] = (
            f"{len(not_online)} of this step's indexes are not ONLINE after {timeout_s:.0f}s: "
            + ", ".join(f"{n} is {states[n]}" for n in not_online)
            + ". The merge ran anyway — the constraint is enforced regardless and the two "
            "lookups only cost a scan — but a MERGE-heavy run against a populating index "
            "is slow, and a FAILED one is a schema problem to fix before the next step."
        )
    return out


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
    return {"constraints": 1, "indexes": 2, "await": await_extract_schema(ctx)}


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


def synthetic_chunk_ids(ctx: GraphContext, ids: Sequence[str]) -> set[str]:
    """Which of these chunks came from a synthetic record.

    `brain chunk` stamps `Chunk.synthetic` from the parent; chunks written before that
    property existed have it as null, which reads here as "not synthetic" — the safe
    direction, since a wrongly-real entity survives a `--synthetic` reset and a wrongly-
    synthetic one would be deleted.
    """
    if not ids:
        return set()
    rows = ctx.read(
        f"MATCH (c:{ctx.label(CHUNK_LABEL)}) WHERE c.id IN $ids AND c.synthetic = true "
        "RETURN c.id AS id",
        ids=list(ids),
    )
    return {r["id"] for r in rows}


#: Is *this* chunk synthetic — derived from its parents, not read off `Chunk.synthetic`.
#:
#: The stored flag would be simpler and would make the answer depend on whether the chunk
#: pass had already run: against a graph whose chunks are still null this would report
#: "no entity changes", which is exactly the false all-clear the backfill exists to fix.
#: Deriving both sides from the same source of truth makes the dry run exact and the two
#: stamping passes order-independent.
_CHUNK_IS_SYNTHETIC = (
    "(size([(p)-[:HAS_CHUNK]->(c) | 1]) > 0 AND "
    "all(s IN [(p)-[:HAS_CHUNK]->(c) | coalesce(p.synthetic, false)] WHERE s))"
)

#: `Entity.synthetic`, derived from the evidence chunks the entity itself names — the
#: same rule `ExtractPlan.entity_rows` applies when merge writes the node, expressed
#: against the graph. An entity with no surviving evidence derives `false`: conventions
#: rule 3 makes that a provenance defect, and `brain reset --synthetic` says so rather
#: than deleting it.
_DERIVE_ENTITY_SYNTHETIC = (
    "OPTIONAL MATCH (c:{chunk}) WHERE c.id IN coalesce(e.evidence_chunk_ids, [])\n"
    "WITH e, collect(" + _CHUNK_IS_SYNTHETIC + ") AS flags\n"
    "WITH e, (size(flags) > 0 AND all(f IN flags WHERE f)) AS syn\n"
)

#: Nodes per stamping transaction. The same budget `brain reset` deletes with.
STAMP_BATCH = 1000


def synthetic_entity_drift(ctx: GraphContext) -> dict[str, int]:
    """What :func:`stamp_entity_synthetic` would change, without changing anything."""
    rows = ctx.read(
        f"MATCH (e:{ctx.label(ENTITY_LABEL)})\n"
        + _DERIVE_ENTITY_SYNTHETIC.format(chunk=ctx.label(CHUNK_LABEL))
        + "RETURN count(e) AS total,\n"
        "  count(CASE WHEN e.synthetic IS NULL THEN 1 END) AS null_flag,\n"
        "  count(CASE WHEN e.synthetic IS NOT NULL AND e.synthetic <> syn THEN 1 END) AS wrong,\n"
        "  count(CASE WHEN syn THEN 1 END) AS synthetic"
    )
    row = rows[0] if rows else {}
    return {
        "entities": int(row.get("total") or 0),
        "null": int(row.get("null_flag") or 0),
        "wrong": int(row.get("wrong") or 0),
        "synthetic_after": int(row.get("synthetic") or 0),
    }


def stamp_entity_synthetic(ctx: GraphContext) -> int:
    """Re-derive `Entity.synthetic` from the evidence chunks. Returns nodes stamped.

    Needed because the flag is a *function of the chunks*: an extract merge that ran while
    every `Chunk.synthetic` was still null wrote `false` on every entity, including the
    ones whose only evidence is the synthetic layer. Re-running `brain extract merge` would
    fix it too and would cost an LLM-free but full re-plan of every batch; this asks the
    graph the same question directly, and is idempotent for the same reason
    :func:`brain.chunk.graph.stamp_synthetic` is — only a differing flag is written.
    """
    cypher = (
        f"MATCH (e:{ctx.label(ENTITY_LABEL)})\n"
        + _DERIVE_ENTITY_SYNTHETIC.format(chunk=ctx.label(CHUNK_LABEL))
        + "WHERE e.synthetic IS NULL OR e.synthetic <> syn\n"
        f"WITH e, syn LIMIT {STAMP_BATCH}\n"
        "SET e.synthetic = syn"
    )
    stamped = 0
    while True:
        written = ctx.write(cypher).get("properties_set", 0)
        stamped += written
        if not written:
            return stamped


def component_names(ctx: GraphContext) -> dict[str, str]:
    """`{casefolded name: real name}` — an extractor writes `Connect`, the node is `connect`."""
    rows = ctx.read(f"MATCH (n:{ctx.label('Component')}) RETURN n.name AS name")
    return {r["name"].casefold(): r["name"] for r in rows if r["name"]}


# ------------------------------------------------------------------------------ writes


#: Provenance lists a merge may only ever add to. `SET n += row.props` replaces a list
#: property wholesale, which is invisible while every merge re-plans every batch — the
#: run's aggregate is then the whole truth. `brain extract merge --slice` is the first
#: partial merge this pipeline runs, and there the aggregate is a *fragment*: without the
#: union, an entity the corpus extracted comes out citing only the slice's chunk, and
#: `brain reset --slice` then reads it as deletable. Measured on the live graph on
#: 2026-09-17: three base entities lost their Plan 1 evidence that way.
UNIONED_LIST_PROPS: tuple[str, ...] = ("evidence_chunk_ids", "batch_ids")

#: Set once, by whoever said it first. "Which batch first stated this fact" does not change
#: when a later batch agrees, and `shard` is derived from it, so the two move together.
FIRST_SEEN_PROPS: tuple[str, ...] = ("batch_id", "shard")


def union_provenance(props: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """One row's props, merged onto the provenance the node or edge already carries.

    Pure and total: with an empty `existing` it returns `props` unchanged, which is what
    a full-corpus merge and a first sighting both want. Deduplicated and sorted, so a
    re-merge of the same batches leaves the arrays exactly the length they were.
    """
    merged = dict(props)
    for key in UNIONED_LIST_PROPS:
        both = set(existing.get(key) or []) | set(props.get(key) or [])
        if both:
            merged[key] = sorted(both)
    for key in FIRST_SEEN_PROPS:
        prior = existing.get(key)
        if prior:
            merged[key] = prior
    return merged


PROVENANCE_RETURN = ", ".join(f"x.`{p}` AS `{p}`" for p in (*UNIONED_LIST_PROPS, *FIRST_SEEN_PROPS))


def read_entity_provenance(ctx: GraphContext, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """What the graph already records for these entities. Empty for ones it has not seen."""
    if not ids:
        return {}
    rows = ctx.read(
        f"MATCH (x:{ctx.label(ENTITY_LABEL)}) WHERE x.`{ENTITY_KEY}` IN $ids "
        f"RETURN x.`{ENTITY_KEY}` AS key, {PROVENANCE_RETURN}",
        ids=list(ids),
    )
    return {r["key"]: {k: v for k, v in r.items() if k != "key"} for r in rows}


def read_edge_provenance(
    ctx: GraphContext,
    src: tuple[str, str],
    rel: str,
    dst: tuple[str, str],
    rows: Sequence[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """The same, for the edges these rows would MERGE, keyed by (src, dst)."""
    if not rows:
        return {}
    src_label, src_key = src
    dst_label, dst_key = dst
    found = ctx.read(
        "UNWIND $pairs AS pair\n"
        f"MATCH (a:{ctx.label(src_label)} {{`{src_key}`: pair.src}})"
        f"-[x:`{rel}`]->"
        f"(b:{ctx.label(dst_label)} {{`{dst_key}`: pair.dst}})\n"
        f"RETURN pair.src AS src, pair.dst AS dst, {PROVENANCE_RETURN}",
        pairs=[{"src": r["src"], "dst": r["dst"]} for r in rows],
    )
    return {
        (r["src"], r["dst"]): {k: v for k, v in r.items() if k not in ("src", "dst")} for r in found
    }


def write_entities(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> int:
    """MERGE on `Entity.id` (= `kind|norm_name`). `extracted_at` is set once, on create.

    The provenance lists are unioned with what the node already carries rather than
    replaced — see :data:`UNIONED_LIST_PROPS`.
    """
    if not rows:
        return 0
    existing = read_entity_provenance(ctx, [r["key"] for r in rows])
    merged = [
        {**row, "props": union_provenance(row["props"], existing.get(row["key"], {}))}
        for row in rows
    ]
    ctx.write_rows(node_merge(ctx, ENTITY_LABEL, ENTITY_KEY, on_create=True), merged)
    return len(merged)


def write_mentions(ctx: GraphContext, rows_by_label: dict[str, list[dict[str, Any]]]) -> int:
    """`(Chunk)-[:MENTIONS {quote}]->(target)`, one query per target label.

    One edge per (chunk, target) pair: the same chunk naming the same thing twice is one
    mention with the first quote, not two edges the reader has to de-duplicate.
    """
    total = 0
    for label, rows in sorted(rows_by_label.items()):
        if not rows:
            continue
        src = (CHUNK_LABEL, CHUNK_KEY)
        dst = (label, NODE_KEYS[label])
        existing = read_edge_provenance(ctx, src, DERIVED_RELATION_TYPE, dst, rows)
        merged = [
            {
                **row,
                "props": union_provenance(row["props"], existing.get((row["src"], row["dst"]), {})),
            }
            for row in rows
        ]
        ctx.write_rows(edge_merge(ctx, src, DERIVED_RELATION_TYPE, dst, set_props=True), merged)
        total += len(merged)
    return total


def write_relations(ctx: GraphContext, grouped: dict[tuple[str, str, str], list[dict]]) -> int:
    """One query per (type, source label, target label). Never creates an endpoint."""
    total = 0
    for (rel_type, src_label, dst_label), rows in sorted(grouped.items()):
        if not rows:
            continue
        src = (src_label, NODE_KEYS[src_label])
        dst = (dst_label, NODE_KEYS[dst_label])
        existing = read_edge_provenance(ctx, src, rel_type, dst, rows)
        merged = [
            {
                **row,
                "props": union_provenance(row["props"], existing.get((row["src"], row["dst"]), {})),
            }
            for row in rows
        ]
        ctx.write_rows(edge_merge(ctx, src, rel_type, dst, set_props=True), merged)
        total += len(merged)
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


#: The edges that make an entity part of the graph rather than a lonely quote. `MENTIONS`
#: is not one of them: every entity has one by construction.
SEMANTIC_TYPES: tuple[str, ...] = tuple(t for t in LLM_RELATION_TYPES if t != DERIVED_RELATION_TYPE)


def consolidation(ctx: GraphContext) -> dict[str, Any]:
    """How much of the corpus the extraction actually joined up.

    The number that matters for `brain resolve`: if almost every entity is cited by exactly
    one chunk, nothing was consolidated and the graph is 9,000 separate observations rather
    than a picture of a few hundred things. `isolated_by_kind` is the other half — an entity
    with no semantic edge answers no question a traversal can ask.
    """
    entity = ctx.label(ENTITY_LABEL)
    types = "|".join(f"`{t}`" for t in SEMANTIC_TYPES)
    spread = ctx.read(
        f"MATCH (c:{ctx.label(CHUNK_LABEL)})-[m:{DERIVED_RELATION_TYPE}]->(e:{entity}) "
        "WITH e, count(DISTINCT c) AS chunks "
        "RETURN count(e) AS entities, sum(chunks) AS mentions, "
        "sum(CASE WHEN chunks = 1 THEN 1 ELSE 0 END) AS single_chunk, "
        "max(chunks) AS max_chunks"
    )[0]
    per_chunk = ctx.read(
        f"MATCH (c:{ctx.label(CHUNK_LABEL)})-[m:{DERIVED_RELATION_TYPE}]->(:{entity}) "
        "WITH c, count(m) AS n "
        "RETURN count(c) AS chunks, avg(n) AS mean, percentileDisc(n, 0.5) AS p50, max(n) AS max"
    )[0]
    isolated = ctx.read(
        f"MATCH (e:{entity}) OPTIONAL MATCH (e)-[r:{types}]-() "
        "WITH e, count(r) AS n "
        "RETURN e.kind AS kind, count(e) AS entities, "
        "sum(CASE WHEN n = 0 THEN 1 ELSE 0 END) AS isolated ORDER BY kind"
    )
    entities = spread["entities"] or 0
    return {
        "entities": entities,
        "mentions": spread["mentions"] or 0,
        "mentions_per_entity": round((spread["mentions"] or 0) / entities, 3) if entities else 0,
        "max_chunks_per_entity": spread["max_chunks"] or 0,
        "entities_with_one_evidence_chunk": spread["single_chunk"] or 0,
        "pct_entities_with_one_evidence_chunk": (
            round(100 * (spread["single_chunk"] or 0) / entities, 1) if entities else 0
        ),
        "chunks_with_an_entity": per_chunk["chunks"] or 0,
        "entities_per_chunk_mean": round(per_chunk["mean"] or 0, 3),
        "entities_per_chunk_p50": per_chunk["p50"] or 0,
        "entities_per_chunk_max": per_chunk["max"] or 0,
        "isolated_by_kind": {
            r["kind"]: {
                "entities": r["entities"],
                "isolated": r["isolated"],
                "pct_isolated": round(100 * r["isolated"] / r["entities"], 1)
                if r["entities"]
                else 0,
            }
            for r in isolated
        },
        "isolated_total": sum(r["isolated"] for r in isolated),
        "semantic_types": list(SEMANTIC_TYPES),
    }


def stale(ctx: GraphContext, merged_at: str) -> dict[str, Any]:
    """Nodes and edges this step wrote that the current batches no longer declare.

    Every write of this run stamps `merged_at`; anything carrying an older stamp — or none
    at all, which is what a node written before this property existed looks like — was
    declared by a batch that has since been regenerated or removed. `coalesce` is doing real
    work in those queries: `null <> $now` is `null` in Cypher, so a bare `<>` silently drops
    exactly the rows the sweep exists to find.

    It is *reported*, never deleted: whether a fact the agents withdrew should leave the
    graph is a decision about evidence, not a cleanup, and deleting it silently would make
    the graph disagree with the batches with nothing to say so.
    """
    entities = ctx.read(
        f"MATCH (e:{ctx.label(ENTITY_LABEL)}) "
        "WHERE e.model = $model AND coalesce(e.merged_at, '') <> $now "
        "RETURN e.id AS id, e.merged_at AS merged_at ORDER BY id",
        model=MODEL,
        now=merged_at,
    )
    edges = ctx.read(
        "MATCH (a)-[r]->(b) "
        f"WHERE type(r) IN $types AND ({_labelled(ctx, 'a')}) "
        "AND r.model = $model AND coalesce(r.merged_at, '') <> $now "
        "RETURN type(r) AS type, count(r) AS n ORDER BY type",
        types=list(LLM_RELATION_TYPES),
        model=MODEL,
        now=merged_at,
    )
    return {
        "rule": "written by an earlier merge, not declared by the current batches",
        "action": "reported only — deleting withdrawn evidence is a planner decision",
        "entities": len(entities),
        "entity_examples": [e["id"] for e in entities[:20]],
        "edges": sum(e["n"] for e in edges),
        "edges_by_type": {e["type"]: e["n"] for e in edges},
    }


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
