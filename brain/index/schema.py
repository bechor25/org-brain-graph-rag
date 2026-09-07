"""The index set Plan 1 exits with (step brief §10 decision 1, + the `chunk_text` amendment).

Three kinds, one rule each.

**Vector** indexes are created by the steps that fill them (`brain chunk`, `brain resolve`,
`brain communities`). They are repeated here because `brain index` is the one command that
answers "is the graph queryable" — an index that exists only as a side effect of the step
that happened to run is a promise nobody checks. `IF NOT EXISTS` makes the repeat free.

**Fulltext** indexes are created here and nowhere else: they are the lexical half of Plan
2's hybrid retrieval, and no Plan 1 step needs them, so this is the step that owns them.

**Range** indexes reuse the names `brain/graph/schema.py` already created. Neo4j's
`IF NOT EXISTS` matches on name *or* schema, so a new name over `(:Commit)` `at` would
also be a no-op — but it would be a no-op that the census then reports as a missing
index forever. Reusing the name means the census reads the index that actually exists.

`person_embedding` is deliberately *not* managed: decision 1 does not list it. It is in
`OBSERVED` so the census names its owner instead of filing it under "other".
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from brain.graph.context import GraphContext
from brain.graph.schema import _index_name

#: How long to wait for a managed index to come online, and how often to look.
AWAIT_SECONDS = 300
POLL_SECONDS = 0.5


@dataclass(frozen=True)
class IndexSpec:
    """One index, in the vocabulary the census prints rather than Neo4j's."""

    name: str
    kind: str  #: vector | fulltext | range
    label: str
    properties: tuple[str, ...]
    owner: str  #: the step whose data fills it
    purpose: str

    @property
    def neo4j_type(self) -> str:
        return {"vector": "VECTOR", "fulltext": "FULLTEXT", "range": "RANGE"}[self.kind]


#: Vector indexes. Dimension and similarity match `brain chunk` / `brain resolve` /
#: `brain communities` exactly, so whichever command runs first wins and the other is a
#: no-op rather than a conflicting definition.
VECTOR: tuple[IndexSpec, ...] = (
    IndexSpec(
        "chunk_embedding",
        "vector",
        "Chunk",
        ("embedding",),
        "chunk",
        "semantic search over chunk text (S1, S2)",
    ),
    IndexSpec(
        "entity_embedding",
        "vector",
        "Entity",
        ("embedding",),
        "resolve",
        "entity blocking for resolution; entity-anchored retrieval (S3)",
    ),
    IndexSpec(
        "community_embedding",
        "vector",
        "Community",
        ("embedding",),
        "communities",
        "global community search over report summaries (S5)",
    ),
)

#: Fulltext indexes. This step owns all of them.
FULLTEXT: tuple[IndexSpec, ...] = (
    IndexSpec(
        "workitem_text",
        "fulltext",
        "WorkItem",
        ("title", "description"),
        "index",
        "lexical half of hybrid search over issues",
    ),
    IndexSpec(
        "document_text",
        "fulltext",
        "Document",
        ("title", "body_md"),
        "index",
        "lexical half of hybrid search over KIPs and pages",
    ),
    IndexSpec(
        "entity_text",
        "fulltext",
        "Entity",
        ("name", "description"),
        "index",
        "name lookup for entity-anchored retrieval",
    ),
    IndexSpec(
        "person_text",
        "fulltext",
        "Person",
        ("display", "aliases"),
        "index",
        "who-is lookup across display names and merged aliases",
    ),
    IndexSpec(
        "chunk_text",
        "fulltext",
        "Chunk",
        ("text",),
        "index",
        "lexical half of hybrid chunk search (S1) — planner amendment to decision 1",
    ),
)

#: Range indexes on the properties the temporal questions filter and order by.
RANGE: tuple[IndexSpec, ...] = (
    IndexSpec(
        _index_name("StatusChange", "at"),
        "range",
        "StatusChange",
        ("at",),
        "load",
        "status timeline: what was the state on date D",
    ),
    IndexSpec(
        _index_name("WorkItem", "created"),
        "range",
        "WorkItem",
        ("created",),
        "load",
        "issue windows: what was opened between D1 and D2",
    ),
    IndexSpec(
        _index_name("Commit", "at"),
        "range",
        "Commit",
        ("at",),
        "load",
        "commit windows: what landed between D1 and D2",
    ),
)

#: Everything `brain index` creates, in creation order.
MANAGED: tuple[IndexSpec, ...] = (*VECTOR, *FULLTEXT, *RANGE)

#: Indexes another step owns. Reported by the census, never created here.
OBSERVED: tuple[IndexSpec, ...] = (
    IndexSpec(
        "person_embedding",
        "vector",
        "Person",
        ("embedding",),
        "resolve",
        "person blocking for resolution (not in decision 1's list)",
    ),
)


def _vector_cypher(ctx: GraphContext, spec: IndexSpec, dim: int) -> str:
    # Index options take literals, not parameters; `dim` is validated by the caller.
    return (
        f"CREATE VECTOR INDEX {ctx.name(spec.name)} IF NOT EXISTS "
        f"FOR (n:{ctx.label(spec.label)}) ON (n.`{spec.properties[0]}`) "
        "OPTIONS {indexConfig: {`vector.dimensions`: " + str(dim) + ", "
        "`vector.similarity_function`: 'cosine'}}"
    )


def _fulltext_cypher(ctx: GraphContext, spec: IndexSpec) -> str:
    props = ", ".join(f"n.`{p}`" for p in spec.properties)
    return (
        f"CREATE FULLTEXT INDEX {ctx.name(spec.name)} IF NOT EXISTS "
        f"FOR (n:{ctx.label(spec.label)}) ON EACH [{props}]"
    )


def _range_cypher(ctx: GraphContext, spec: IndexSpec) -> str:
    return (
        f"CREATE INDEX {ctx.name(spec.name)} IF NOT EXISTS "
        f"FOR (n:{ctx.label(spec.label)}) ON (n.`{spec.properties[0]}`)"
    )


def statements(ctx: GraphContext, dim: int) -> list[tuple[IndexSpec, str]]:
    """(spec, cypher) for every managed index, in creation order."""
    if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
        raise ValueError(f"vector dimension must be a positive int, got {dim!r}")
    out: list[tuple[IndexSpec, str]] = []
    for spec in MANAGED:
        if spec.kind == "vector":
            out.append((spec, _vector_cypher(ctx, spec, dim)))
        elif spec.kind == "fulltext":
            out.append((spec, _fulltext_cypher(ctx, spec)))
        else:
            out.append((spec, _range_cypher(ctx, spec)))
    return out


def await_indexes(
    ctx: GraphContext,
    timeout_s: float = AWAIT_SECONDS,
    poll_s: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Block until every *managed* index is ONLINE, or the timeout expires.

    Deliberately not `CALL db.awaitIndexes()`. That procedure waits for every index in the
    database and raises if any of them is not online — including the ones the live tests of
    other steps build in their own label spaces (`_ResetTest…`, `_Comm…`, `_Smoke…`). With
    four agents sharing one Neo4j, a half-built index belonging to somebody else's test run
    would fail `brain index` while every index this step owns was perfectly fine. Polling
    `SHOW INDEXES` for our own names is the same wait, scoped to what we can promise.

    A timeout is not raised either: the state we ended up with is returned, `not_online`
    goes into the report, and the gate fails on it. A census that crashed instead of
    reporting the state would tell the reader nothing about the state.
    """
    wanted = {f"{ctx.prefix}{spec.name}" for spec in MANAGED}
    started = time.monotonic()
    while True:
        rows = ctx.read("SHOW INDEXES YIELD name, state RETURN name, state")
        states = {r["name"]: r["state"] for r in rows}
        pending = {name: states.get(name, "MISSING") for name in sorted(wanted)}
        pending = {n: s for n, s in pending.items() if s != "ONLINE"}
        waited = time.monotonic() - started
        if not pending or waited >= timeout_s:
            return {
                "waited_s": round(waited, 2),
                "timeout_s": timeout_s,
                "timed_out": bool(pending),
                "not_online": pending,
            }
        sleep(poll_s)


def apply_indexes(ctx: GraphContext, dim: int, **wait_kw: Any) -> dict[str, Any]:
    """Create everything, then wait for it.

    `indexes_added` from the write summary is the only honest answer to "was this new":
    `IF NOT EXISTS` succeeds either way, and a rerun that reported "created 11" would make
    the idempotency criterion unfalsifiable.
    """
    by_index: dict[str, bool] = {}
    for spec, cypher in statements(ctx, dim):
        counters = ctx.write(cypher)
        by_index[spec.name] = bool(counters.get("indexes_added", 0))
    waited = await_indexes(ctx, **wait_kw)
    created = sum(1 for was_new in by_index.values() if was_new)
    return {
        "managed": len(MANAGED),
        "created": created,
        "existing": len(MANAGED) - created,
        "by_index": by_index,
        "dim": dim,
        "similarity": "cosine",
        "await": waited,
    }


def drop_indexes(ctx: GraphContext) -> None:
    """Tests only: remove what a scratch namespace created."""
    for spec in MANAGED:
        ctx.client.write(f"DROP INDEX {ctx.name(spec.name)} IF EXISTS")


def strip_prefix(ctx: GraphContext, name: str) -> str | None:
    """A name inside this namespace, without the prefix — or None if it is another's.

    With the production (empty) prefix every name matches, so the test namespaces that
    share the database (`_Comm…`, `_Smoke…`, `_ResetTest…`) have to be excluded explicitly.
    A census that counted them would report indexes over labels the corpus does not have.
    """
    if ctx.prefix:
        return name[len(ctx.prefix) :] if name.startswith(ctx.prefix) else None
    return None if name.startswith("_") else name


def index_status(ctx: GraphContext) -> list[dict[str, Any]]:
    """Every index in this namespace as the server reports it, managed ones first.

    A managed index the server does not have is returned with `state: "MISSING"` rather
    than omitted: "absent" is the answer the gate needs, and a shorter list is not one.
    """
    rows = ctx.read(
        "SHOW INDEXES YIELD name, type, state, populationPercent, entityType, "
        "labelsOrTypes, properties, options "
        "RETURN name, type, state, populationPercent, entityType, labelsOrTypes, "
        "properties, options"
    )
    owners = {s.name: s for s in (*MANAGED, *OBSERVED)}
    managed = {s.name for s in MANAGED}
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = strip_prefix(ctx, row["name"])
        if name is None:
            continue
        spec = owners.get(name)
        config = (row.get("options") or {}).get("indexConfig") or {}
        seen[name] = {
            "name": name,
            "present": True,
            "managed": name in managed,
            "owner": spec.owner if spec else "other",
            "purpose": spec.purpose if spec else "",
            "type": row["type"],
            "state": row["state"],
            "population_percent": row.get("populationPercent"),
            "entity_type": row.get("entityType"),
            "labels": row.get("labelsOrTypes"),
            "properties": row.get("properties"),
            "dim": config.get("vector.dimensions"),
            "similarity": config.get("vector.similarity_function"),
            "analyzer": config.get("fulltext.analyzer"),
        }
    out: list[dict[str, Any]] = []
    for spec in MANAGED:
        out.append(
            seen.pop(spec.name, None)
            or {
                "name": spec.name,
                "present": False,
                "managed": True,
                "owner": spec.owner,
                "purpose": spec.purpose,
                "type": spec.neo4j_type,
                "state": "MISSING",
                "population_percent": None,
                "labels": [spec.label],
                "properties": list(spec.properties),
            }
        )
    out.extend(seen[name] for name in sorted(seen))
    return out


def not_online(status: list[dict[str, Any]]) -> dict[str, str]:
    """Managed indexes that are not ONLINE, name -> state. Empty is the gate criterion.

    Unmanaged indexes are left out on purpose: this step cannot promise the state of an
    index it did not declare, and failing the gate on a stray test index would be a lie
    about the corpus.
    """
    return {
        row["name"]: row["state"]
        for row in status
        if row.get("managed") and row.get("state") != "ONLINE"
    }


def not_populated(status: list[dict[str, Any]]) -> dict[str, Any]:
    """Managed indexes below 100% population, name -> percent."""
    return {
        row["name"]: row.get("population_percent")
        for row in status
        if row.get("managed")
        and row.get("present")
        and (row.get("population_percent") or 0) < 100.0
    }
