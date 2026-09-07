"""The in-memory graph Leiden runs on, and the one query that builds it.

Brief 09 decision 1 in code. Four labels — `Entity`, `WorkItem`, `Document`, `Component` —
and a closed set of relationship types, projected **undirected** because a community is not
a direction. `Chunk`, `Person` and `Commit` are not nodes here: a chunk would make every
document a hub of its own paragraphs, a person would cluster the org by who talked, and a
commit is a step between two things that belong together anyway. What those nodes carry is
kept by *collapsing* them:

* `(parent)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(entity)` becomes one `parent–entity` edge.
  This is the only edge 83% of `Technology` entities have (`data/reports/extract.json`), so
  without it a sixth of the extraction sits outside every community.
* `(w:WorkItem)<-[:RESOLVES]-(:Commit)-[:IMPLEMENTS_KIP]->(d:Document)` becomes `w–d`: the
  work item that delivered the KIP, without the 6,107 commits in between.

Synthetic nodes are excluded unless `--include-synthetic` is passed, so v1 communities are
communities of the real organisation.

Everything is one `gds.graph.project` Cypher aggregation over a `UNION ALL`, including a
branch that projects the nodes with no edges at all. Dropping isolated nodes would be the
comfortable choice and the dishonest one: "how many entities are in no community" is a
finding about the extraction, and a projection that cannot represent them cannot report it.

Every edge branch collapses parallel relationships into one undirected edge — see `Branch`
for how and for why it matters that it does not depend on the duplicate cleanup running
elsewhere.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from brain.graph.context import GraphContext

#: The projected node labels, in the order the report lists them.
LABELS: tuple[str, ...] = ("Entity", "WorkItem", "Document", "Component")
#: The key property each label merges on — `brain load` and `brain extract` set these.
KEY_PROPS: dict[str, str] = {
    "Entity": "id",
    "WorkItem": "key",
    "Document": "key",
    "Component": "name",
}
#: Labels deliberately outside the projection, and why. Quoted in the report.
EXCLUDED_LABELS: dict[str, str] = {
    "Chunk": "collapsed into parent-entity edges; a paragraph is not a member of a theme",
    "Person": "who spoke is not what a theme is about (spec 3.7)",
    "Commit": "collapsed into WorkItem-Document via RESOLVES + IMPLEMENTS_KIP",
    "PullRequest": "an endpoint of REFERENCES only; not a projected node",
}

#: Structural and LLM-derived types projected as they stand, both endpoints in `LABELS`.
DIRECT_TYPES: tuple[str, ...] = (
    "REFERENCES",
    "LINKS_TO",
    "IMPLEMENTS",
    "DEPENDS_ON",
    "IN_COMPONENT",
    "DECIDES",
    "MOTIVATED_BY",
    "REJECTS",
)
#: The two collapsed edges, named for what they mean rather than for the path they came from.
MENTIONS_PARENT = "MENTIONS_PARENT"
DELIVERS_KIP = "DELIVERS_KIP"
COLLAPSED_TYPES: tuple[str, ...] = (MENTIONS_PARENT, DELIVERS_KIP)
PROJECTED_TYPES: tuple[str, ...] = (*DIRECT_TYPES, *COLLAPSED_TYPES)
#: An LLM relation type the brief's set leaves out. Named so the report can count what it
#: costs, instead of the omission being invisible.
NOT_PROJECTED_TYPES: tuple[str, ...] = ("INTRODUCES_RISK",)

#: What the report says about parallel edges, in the report's own words.
AGGREGATION_NOTE = (
    "single: each branch orders its endpoints by elementId and returns DISTINCT pairs, so "
    "duplicate relationships of one type between one pair — and reciprocal a->b/b->a pairs "
    "— project one undirected edge of weight 1. Different relationship types between the "
    "same pair stay separate edges: that is two reasons, not one edge counted twice."
)

GRAPH_NAME = "brain_communities"
#: Leiden's random seed only makes a run reproducible at concurrency 1 (GDS docs).
CONCURRENCY = 1
DEFAULT_SEED = 42
DEFAULT_MAX_LEVELS = 10
DEFAULT_GAMMA = 1.0


class ProjectionError(RuntimeError):
    """The projection cannot be built, or is too thin to run a community algorithm on."""


def graph_name(ctx: GraphContext) -> str:
    """Namespaced like the labels are, so a smoke run cannot drop the real projection."""
    return f"{ctx.prefix}{GRAPH_NAME}"


# ------------------------------------------------------------------------ query building


def _any_label(ctx: GraphContext, var: str) -> str:
    return " OR ".join(f"{var}:{ctx.label(label)}" for label in LABELS)


def _real(var: str) -> str:
    return f"coalesce({var}.synthetic, false) = false"


def node_predicate(ctx: GraphContext, var: str, *, include_synthetic: bool) -> str:
    clause = f"({_any_label(ctx, var)})"
    return clause if include_synthetic else f"{clause} AND {_real(var)}"


@dataclass(frozen=True)
class Branch:
    """One `UNION ALL` arm: the pattern, and which variables are the two endpoints.

    The projection query and the per-type count query are generated from the same object,
    so "how many `MENTIONS_PARENT` edges the report claims" and "how many the projection
    got" cannot drift apart the way two hand-written queries would.

    Every edge arm emits its endpoints **ordered by `elementId` and then `DISTINCT`**, so
    one pair of nodes contributes exactly one row per relationship type. GDS keeps every
    row it is handed, so a second row for the same pair is a second parallel relationship
    and twice the weight Leiden sees on that edge. Two things produce those rows here:

    * duplicate relationships of one type between one pair — extract merge left ~98 behind
      after resolve merged their endpoints, and the resolve step is deleting them while
      this runs, so the projection must not depend on when that finishes;
    * reciprocal pairs, where `a→b` and `b→a` both exist. On the live graph that is 470
      `REFERENCES` pairs and one `LINKS_TO` pair — invisible without the ordering, because
      each direction is a perfectly valid distinct row.

    Different relationship *types* between the same pair are still separate edges. That is
    not a duplicate: a curated `DEPENDS_ON` and a mention in the same document are two
    independent reasons to believe those two things belong together.
    """

    name: str
    match: str
    source: str
    target: str | None = None

    def canonical_pair(self) -> str:
        """`WITH` that puts the pair in `elementId` order — direction is not a community."""
        a, b = self.source, self.target
        return (
            f"WITH CASE WHEN elementId({a}) <= elementId({b}) THEN {a} ELSE {b} END AS source,\n"
            f"     CASE WHEN elementId({a}) <= elementId({b}) THEN {b} ELSE {a} END AS target"
        )

    def cypher(self) -> str:
        if self.target is None:
            return f"{self.match}\nRETURN {self.source} AS source, null AS target, null AS relType"
        return (
            f"{self.match}\n{self.canonical_pair()}\n"
            f"RETURN DISTINCT source, target, '{self.name}' AS relType"
        )

    def count_cypher(self) -> str:
        """Rows this branch hands the projection — one per canonical pair."""
        if self.target is None:
            return f"{self.match}\nRETURN count(DISTINCT {self.source}) AS n"
        return (
            f"{self.match}\n{self.canonical_pair()}\nRETURN count(DISTINCT [source, target]) AS n"
        )

    def raw_count_cypher(self) -> str:
        """Relationships matched before the collapse. The difference is the receipt."""
        return f"{self.match}\nRETURN count(*) AS n"


def node_branch(ctx: GraphContext, *, include_synthetic: bool) -> Branch:
    """Every projected node, edge or no edge."""
    return Branch(
        name="nodes",
        match=f"MATCH (n) WHERE {node_predicate(ctx, 'n', include_synthetic=include_synthetic)}",
        source="n",
    )


def direct_branch(ctx: GraphContext, rel_type: str, *, include_synthetic: bool) -> Branch:
    """One relationship type, both endpoints inside the projected node set."""
    return Branch(
        name=rel_type,
        match=(
            f"MATCH (a)-[:`{rel_type}`]->(b)\n"
            f"WHERE ({node_predicate(ctx, 'a', include_synthetic=include_synthetic)})\n"
            f"  AND ({node_predicate(ctx, 'b', include_synthetic=include_synthetic)})\n"
            "  AND a <> b"
        ),
        source="a",
        target="b",
    )


def mentions_branch(ctx: GraphContext, *, include_synthetic: bool) -> Branch:
    """`parent -HAS_CHUNK-> chunk -MENTIONS-> thing` collapsed to `parent-thing`.

    `DISTINCT` on purpose: a KIP that names one entity in six sections is one edge, not
    six. Six parallel edges would weight this collapse six times heavier than a curated
    `DEPENDS_ON`, which is not a claim the corpus makes.
    """
    return Branch(
        name=MENTIONS_PARENT,
        match=(
            f"MATCH (p)-[:HAS_CHUNK]->(c:{ctx.label('Chunk')})-[:MENTIONS]->(t)\n"
            f"WHERE ({node_predicate(ctx, 'p', include_synthetic=include_synthetic)})\n"
            f"  AND ({node_predicate(ctx, 't', include_synthetic=include_synthetic)})\n"
            "  AND p <> t"
        ),
        source="p",
        target="t",
    )


def delivers_kip_branch(ctx: GraphContext, *, include_synthetic: bool) -> Branch:
    """`workitem <-RESOLVES- commit -IMPLEMENTS_KIP-> document` collapsed to `item-doc`."""
    where = "" if include_synthetic else f"\nWHERE {_real('w')} AND {_real('d')}"
    return Branch(
        name=DELIVERS_KIP,
        match=(
            f"MATCH (w:{ctx.label('WorkItem')})<-[:RESOLVES]-(x:{ctx.label('Commit')})"
            f"-[:IMPLEMENTS_KIP]->(d:{ctx.label('Document')}){where}"
        ),
        source="w",
        target="d",
    )


def branches(ctx: GraphContext, *, include_synthetic: bool) -> list[Branch]:
    """Every `UNION ALL` arm, in report order."""
    return [
        node_branch(ctx, include_synthetic=include_synthetic),
        *(direct_branch(ctx, t, include_synthetic=include_synthetic) for t in DIRECT_TYPES),
        mentions_branch(ctx, include_synthetic=include_synthetic),
        delivers_kip_branch(ctx, include_synthetic=include_synthetic),
    ]


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


def projection_query(ctx: GraphContext, *, include_synthetic: bool) -> str:
    """The whole projection: one `gds.graph.project` aggregation over every branch."""
    body = "\n  UNION ALL\n".join(
        _indent(b.cypher()) for b in branches(ctx, include_synthetic=include_synthetic)
    )
    return (
        "CALL () {\n"
        f"{body}\n"
        "}\n"
        "RETURN gds.graph.project(\n"
        "  $name, source, target,\n"
        "  {relationshipType: relType},\n"
        "  {undirectedRelationshipTypes: ['*']}\n"
        ") AS g"
    )


# --------------------------------------------------------------------------------- calls


def drop(ctx: GraphContext, name: str | None = None) -> bool:
    """`gds.graph.drop(name, false)`. Always called, in a `finally`, brief 09 note 2."""
    rows = ctx.read(
        "CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName AS g",
        name=name or graph_name(ctx),
    )
    return bool(rows)


def exists(ctx: GraphContext, name: str | None = None) -> bool:
    rows = ctx.read("RETURN gds.graph.exists($name) AS e", name=name or graph_name(ctx))
    return bool(rows and rows[0]["e"])


def density(nodes: int, edges: int) -> float:
    """Undirected edge density. Computed here: the projection call does not return one.

    `gds.graph.project` yields `graphName`, `nodeCount`, `relationshipCount` and
    `projectMillis` and nothing else, so reading a missing `density` off it and defaulting
    to zero would print a measurement nobody made.
    """
    if nodes < 2:
        return 0.0
    return round(2 * edges / (nodes * (nodes - 1)), 8)


def project(ctx: GraphContext, *, include_synthetic: bool = False) -> dict[str, Any]:
    """Build the in-memory graph. Dropped first, so a crashed run does not block the next.

    The projection itself runs in `READ` mode: `gds.graph.project` writes to the GDS
    catalog, never to the database, and asking the driver to prove that is free.
    """
    name = graph_name(ctx)
    drop(ctx, name)
    started = time.perf_counter()
    rows = ctx.read(projection_query(ctx, include_synthetic=include_synthetic), name=name)
    if not rows:
        raise ProjectionError("gds.graph.project returned no result")
    g = rows[0]["g"]
    elapsed = round((time.perf_counter() - started) * 1000)
    # An aggregation over zero rows returns null rather than a map with nodeCount 0, so
    # "there is nothing to project" arrives here as a None and has to be named as such.
    if not isinstance(g, dict) or not g.get("nodeCount"):
        drop(ctx, name)
        raise ProjectionError(
            f"the projection is empty. Are there {'/'.join(LABELS)} nodes in "
            f"{'namespace ' + ctx.prefix if ctx.prefix else 'this database'}?"
        )
    return {
        "graph": name,
        "nodes": int(g["nodeCount"]),
        # GDS stores an undirected edge as two relationships; the honest number of *edges*
        # is half that, and both are reported so neither can be mistaken for the other.
        "relationships_stored": int(g["relationshipCount"]),
        "edges": int(g["relationshipCount"]) // 2,
        "density": density(int(g["nodeCount"]), int(g["relationshipCount"]) // 2),
        "project_ms": elapsed,
        "gds_project_ms": int(g.get("projectMillis") or 0),
        "include_synthetic": include_synthetic,
        "labels": list(LABELS),
        "excluded_labels": dict(EXCLUDED_LABELS),
        "types": list(PROJECTED_TYPES),
        "types_not_projected": list(NOT_PROJECTED_TYPES),
        "undirected": True,
        "parallel_relationship_aggregation": AGGREGATION_NOTE,
    }


def branch_counts(ctx: GraphContext, *, include_synthetic: bool = False) -> dict[str, int]:
    """How many rows each branch contributed. The projection's receipt, per edge type."""
    out: dict[str, int] = {}
    for branch in branches(ctx, include_synthetic=include_synthetic):
        rows = ctx.read(branch.count_cypher())
        out[branch.name] = int(rows[0]["n"]) if rows else 0
    return out


def raw_branch_counts(ctx: GraphContext, *, include_synthetic: bool = False) -> dict[str, int]:
    """Relationships each branch matched *before* the pair collapse.

    Reported beside `branch_counts` so the number of duplicate and reciprocal edges the
    projection aggregated away is a figure in the report, not a claim in a docstring.
    """
    out: dict[str, int] = {}
    for branch in branches(ctx, include_synthetic=include_synthetic):
        if branch.target is None:
            continue
        rows = ctx.read(branch.raw_count_cypher())
        out[branch.name] = int(rows[0]["n"]) if rows else 0
    return out


def unprojected_edge_counts(ctx: GraphContext) -> dict[str, int]:
    """Edges of a type the brief's relation set leaves out, so the cost is a number."""
    out: dict[str, int] = {}
    for rel_type in NOT_PROJECTED_TYPES:
        rows = ctx.read(
            f"MATCH (a)-[r:`{rel_type}`]->(b) WHERE ({_any_label(ctx, 'a')}) RETURN count(r) AS n"
        )
        out[rel_type] = int(rows[0]["n"]) if rows else 0
    return out


# ------------------------------------------------------------------------------- leiden


def leiden_config(
    *, seed: int = DEFAULT_SEED, max_levels: int = DEFAULT_MAX_LEVELS, gamma: float = DEFAULT_GAMMA
) -> dict[str, Any]:
    """One config, used by both `stats` and `stream`, so their answers cannot disagree."""
    return {
        "includeIntermediateCommunities": True,
        "maxLevels": max_levels,
        "gamma": gamma,
        "randomSeed": seed,
        "concurrency": CONCURRENCY,
    }


def leiden_stats(ctx: GraphContext, config: dict[str, Any]) -> dict[str, Any]:
    """Modularity and the level count, from GDS rather than recomputed here."""
    started = time.perf_counter()
    rows = ctx.read(
        "CALL gds.leiden.stats($name, $config) YIELD ranLevels, didConverge, nodeCount, "
        "communityCount, communityDistribution, modularity, modularities, computeMillis "
        "RETURN ranLevels, didConverge, nodeCount, communityCount, communityDistribution, "
        "modularity, modularities, computeMillis",
        name=graph_name(ctx),
        config=config,
    )
    if not rows:
        raise ProjectionError("gds.leiden.stats returned no result")
    row = dict(rows[0])
    row["wall_ms"] = round((time.perf_counter() - started) * 1000)
    row["modularity"] = round(float(row["modularity"]), 6)
    row["modularities"] = [round(float(m), 6) for m in row.get("modularities") or []]
    return row


def _label_case(ctx: GraphContext, var: str, prop: str) -> str:
    arms = " ".join(f"WHEN {var}:{ctx.label(x)} THEN '{x}'" for x in LABELS)
    return f"CASE {arms} END AS {prop}"


def _key_case(ctx: GraphContext, var: str, prop: str) -> str:
    arms = " ".join(f"WHEN {var}:{ctx.label(x)} THEN {var}.`{KEY_PROPS[x]}`" for x in LABELS)
    return f"CASE {arms} END AS {prop}"


def leiden_stream(ctx: GraphContext, config: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """`(rows, ms)` — one row per node: internal id, label, key and every level's community."""
    started = time.perf_counter()
    rows = ctx.read(
        "CALL gds.leiden.stream($name, $config) "
        "YIELD nodeId, communityId, intermediateCommunityIds\n"
        "WITH nodeId, communityId, intermediateCommunityIds, gds.util.asNode(nodeId) AS n\n"
        f"RETURN nodeId, communityId, intermediateCommunityIds, {_label_case(ctx, 'n', 'label')}, "
        f"{_key_case(ctx, 'n', 'key')}\n"
        "ORDER BY nodeId",
        name=graph_name(ctx),
        config=config,
    )
    return rows, round((time.perf_counter() - started) * 1000)


def degree_stream(ctx: GraphContext) -> dict[int, float]:
    """Projected degree per internal node id — the ranking the batches use.

    Degree *in the projection*, not in Neo4j: a `WorkItem`'s Neo4j degree is dominated by
    its `StatusChange` events, which say nothing about which theme it belongs to.
    """
    rows = ctx.read(
        "CALL gds.degree.stream($name, {concurrency: $c}) YIELD nodeId, score RETURN nodeId, score",
        name=graph_name(ctx),
        c=CONCURRENCY,
    )
    return {int(r["nodeId"]): float(r["score"]) for r in rows}


def entity_kinds(ctx: GraphContext, keys: Sequence[str]) -> dict[str, str]:
    """`{Entity.id: kind}` for the members of the partition, for the placement report."""
    if not keys:
        return {}
    rows = ctx.read(
        f"MATCH (e:{ctx.label('Entity')}) WHERE e.id IN $keys RETURN e.id AS id, e.kind AS kind",
        keys=list(keys),
    )
    return {r["id"]: r["kind"] or "unknown" for r in rows}
