"""Every read and write `brain resolve` makes against Neo4j.

Two things here are worth reading twice.

**`mergeRels: false`.** `apoc.refactor.mergeNodes` can merge the relationships it moves,
and with a `properties` strategy of `discard` that would quietly throw away one of two
`ASSIGNED_TO` intervals whose `valid_from` differed — a fact, deleted, invisibly. So the
edges are moved un-merged and a second pass deletes only the ones that are duplicates by
*every* property. The count of what it deleted is in the report.

**The vector index does the blocking.** Token blocking on names tops out at 85% recall on
this corpus (measured), so tier 2 asks the same k-NN question retrieval will ask later:
one `db.index.vector.queryNodes` probe per candidate over an index of the whole label. No
hand-written blocking key gets to decide, in advance, which duplicates are findable.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from brain.graph.context import GraphContext
from brain.resolve.models import Candidate, Evidence, Pair
from brain.resolve.names import first_line

PERSON_LABEL = "Person"
ENTITY_LABEL = "Entity"
EMBEDDING_PROP = "embedding"
EMBED_HASH_PROP = "embed_hash"
SAME_AS = "SAME_AS"

#: One vector index per label, named in the context's namespace.
INDEX_NAMES: dict[str, str] = {
    PERSON_LABEL: "person_embedding",
    ENTITY_LABEL: "entity_embedding",
}
KEY_PROPS: dict[str, str] = {PERSON_LABEL: "id", ENTITY_LABEL: "id"}

#: Labels a person can be attached to, and where their title lives.
EVIDENCE_LABELS: tuple[str, ...] = ("WorkItem", "Document", "Commit", "PullRequest")
#: Most identifying first: what a person *did* says more than what was said about them.
#: `WORKED_ON` is last because it is derived — the issue a commit of theirs resolves, not
#: a fact any system wrote about the person. It is also the only role that lets a git
#: identity and a Jira identity meet: their direct evidence is commit shas on one side and
#: issue keys on the other, and those two key spaces never intersect, so "identical display
#: plus activity overlap" would be dead as a rule without it.
ROLE_RANK: dict[str, int] = {
    "AUTHORED": 0,
    "ASSIGNED_TO": 1,
    "REPORTED_BY": 2,
    "COMMENTED": 3,
    "MENTIONS": 4,
    "WORKED_ON": 5,
}
#: How much attached context to read per candidate. Five reach the embedding and three the
#: adjudicator; the rest are what `display_and_activity` intersects.
MAX_EVIDENCE = 60
#: Commit messages are paragraphs. Only the subject line identifies the work.
TITLE_CHARS = 200


# --------------------------------------------------------------------------------- schema


def apply_resolve_schema(ctx: GraphContext, label: str, dim: int) -> dict[str, Any]:
    """The vector index tier 2 probes. `IF NOT EXISTS`, like every other schema call."""
    if not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"vector dimension must be a positive int, got {dim!r}")
    name = ctx.name(INDEX_NAMES[label])
    ctx.write(
        f"CREATE VECTOR INDEX {name} IF NOT EXISTS "
        f"FOR (n:{ctx.label(label)}) ON (n.`{EMBEDDING_PROP}`) "
        "OPTIONS {indexConfig: {`vector.dimensions`: " + str(dim) + ", "
        "`vector.similarity_function`: 'cosine'}}"
    )
    ctx.client.write("CALL db.awaitIndexes(300)")
    return {"index": INDEX_NAMES[label], "dim": dim}


def drop_resolve_schema(ctx: GraphContext) -> None:
    """Tests only: remove what a scratch namespace created."""
    for name in INDEX_NAMES.values():
        ctx.client.write(f"DROP INDEX {ctx.name(name)} IF EXISTS")


# ---------------------------------------------------------------------------------- reads


def _evidence_rows(ctx: GraphContext) -> list[dict[str, Any]]:
    labels = " OR ".join(f"x:{ctx.label(name)}" for name in EVIDENCE_LABELS)
    outgoing = (
        f"MATCH (p:{ctx.label(PERSON_LABEL)})-[r:AUTHORED|COMMENTED]->(x)\n"
        f"WHERE {labels}\n"
        "RETURN p.id AS pid, type(r) AS role, "
        "coalesce(x.key, x.sha, toString(x.number)) AS key, "
        f"left(coalesce(x.title, x.message, ''), {TITLE_CHARS}) AS title"
    )
    incoming = (
        f"MATCH (x)-[r:ASSIGNED_TO|REPORTED_BY|MENTIONS_PERSON]->(p:{ctx.label(PERSON_LABEL)})\n"
        f"WHERE {labels}\n"
        "RETURN p.id AS pid, type(r) AS role, "
        "coalesce(x.key, x.sha, toString(x.number)) AS key, "
        f"left(coalesce(x.title, x.message, ''), {TITLE_CHARS}) AS title"
    )
    # The issue behind a commit: the one key space a git identity and a Jira identity can
    # both land in. Without it "identical display + activity overlap" can never fire
    # across systems, because commit shas and issue keys never collide.
    derived = (
        f"MATCH (p:{ctx.label(PERSON_LABEL)})-[:AUTHORED]->(c)-[:RESOLVES|REFERENCES]->"
        f"(w:{ctx.label('WorkItem')})\n"
        f"WHERE c:{ctx.label('Commit')} OR c:{ctx.label('PullRequest')}\n"
        "RETURN p.id AS pid, 'WORKED_ON' AS role, w.key AS key, "
        f"left(coalesce(w.title, ''), {TITLE_CHARS}) AS title"
    )
    return ctx.read(f"{outgoing}\nUNION\n{incoming}\nUNION\n{derived}")


def _attach(rows: Iterable[dict[str, Any]]) -> dict[str, list[Evidence]]:
    """Group evidence per candidate, deterministically ordered and capped."""
    grouped: dict[str, set[tuple[int, str, str, str]]] = {}
    for row in rows:
        key = row.get("key")
        if not row.get("pid") or not key:
            continue
        role = row.get("role") or ""
        grouped.setdefault(row["pid"], set()).add(
            (ROLE_RANK.get(role, len(ROLE_RANK)), key, role, first_line(row.get("title")))
        )
    return {
        pid: [Evidence(role=role, key=key, title=title) for _, key, role, title in sorted(items)][
            :MAX_EVIDENCE
        ]
        for pid, items in grouped.items()
    }


def read_person_candidates(ctx: GraphContext) -> list[Candidate]:
    """Every `Person` node, with what it is attached to. One `block`: people are people."""
    rows = ctx.read(
        f"MATCH (p:{ctx.label(PERSON_LABEL)})\n"
        "RETURN p.id AS id, p.source AS source, p.display AS display, p.email AS email, "
        "p.identity_keys AS identity_keys, p.aliases AS aliases, "
        "p.merged_from AS merged_from, p.resolution_tier AS resolution_tier\n"
        "ORDER BY p.id"
    )
    evidence = _attach(_evidence_rows(ctx))
    out: list[Candidate] = []
    for row in rows:
        node_id = row["id"]
        out.append(
            Candidate(
                id=node_id,
                kind="person",
                block="person",
                name=row.get("display") or node_id.split(":", 1)[-1],
                source=row.get("source") or node_id.split(":", 1)[0],
                email=row.get("email"),
                identities=sorted(row.get("identity_keys") or [node_id]),
                aliases=sorted(row.get("aliases") or []),
                merged_from=sorted(row.get("merged_from") or []),
                resolution_tier=row.get("resolution_tier"),
                evidence=evidence.get(node_id, []),
            )
        )
    return out


def read_entity_candidates(ctx: GraphContext) -> list[Candidate]:
    """Every `Entity`, blocked by its kind, with up to `MAX_EVIDENCE` mention quotes."""
    rows = ctx.read(
        f"MATCH (e:{ctx.label(ENTITY_LABEL)})\n"
        "RETURN e.id AS id, e.kind AS kind, e.name AS name, e.description AS description, "
        "e.aliases AS aliases, e.merged_from AS merged_from, "
        "e.resolution_tier AS resolution_tier\n"
        "ORDER BY e.id"
    )
    quotes = ctx.read(
        f"MATCH (c:{ctx.label('Chunk')})-[m:MENTIONS]->(e:{ctx.label(ENTITY_LABEL)})\n"
        "RETURN e.id AS pid, 'MENTIONS' AS role, c.id AS key, "
        f"left(coalesce(m.quote, ''), {TITLE_CHARS}) AS title"
    )
    evidence = _attach(quotes)
    return [
        Candidate(
            id=row["id"],
            kind="entity",
            block=row.get("kind") or "Entity",
            name=row.get("name") or row["id"],
            description=row.get("description") or None,
            identities=[row["id"]],
            aliases=sorted(row.get("aliases") or []),
            merged_from=sorted(row.get("merged_from") or []),
            resolution_tier=row.get("resolution_tier"),
            evidence=evidence.get(row["id"], []),
        )
        for row in rows
    ]


def read_candidates(ctx: GraphContext, kind: str) -> list[Candidate]:
    return read_person_candidates(ctx) if kind == "person" else read_entity_candidates(ctx)


def embed_hashes(ctx: GraphContext, label: str) -> dict[str, str]:
    """Which nodes already carry a vector, and of which text."""
    rows = ctx.read(
        f"MATCH (n:{ctx.label(label)}) WHERE n.`{EMBEDDING_PROP}` IS NOT NULL "
        f"RETURN n.`{KEY_PROPS[label]}` AS id, n.`{EMBED_HASH_PROP}` AS hash"
    )
    return {r["id"]: r["hash"] for r in rows}


def knn(ctx: GraphContext, label: str, *, k: int, floor: float) -> list[tuple[str, str, float]]:
    """Top-`k` neighbours of every embedded node, scored by cosine, above `floor`.

    `k + 1` is asked for because the nearest neighbour of a node is always itself.
    """
    key = KEY_PROPS[label]
    rows = ctx.read(
        f"MATCH (n:{ctx.label(label)}) WHERE n.`{EMBEDDING_PROP}` IS NOT NULL\n"
        f"CALL db.index.vector.queryNodes($index, $k, n.`{EMBEDDING_PROP}`) "
        "YIELD node, score\n"
        f"WITH n, node, score WHERE node <> n AND score >= $floor AND node:{ctx.label(label)}\n"
        f"RETURN n.`{key}` AS a, node.`{key}` AS b, score ORDER BY score DESC, a, b",
        index=ctx.name(INDEX_NAMES[label]).strip("`"),
        k=k + 1,
        floor=floor,
    )
    return [(r["a"], r["b"], float(r["score"])) for r in rows]


def census(ctx: GraphContext, label: str) -> dict[str, Any]:
    """Nodes, identities and the duplicate rate the report quotes before and after."""
    key = KEY_PROPS[label]
    identity_prop = "identity_keys" if label == PERSON_LABEL else "aliases"
    rows = ctx.read(
        f"MATCH (n:{ctx.label(label)})\n"
        f"RETURN count(n) AS nodes, "
        f"sum(coalesce(size(n.`{identity_prop}`), 0)) AS identities, "
        "sum(CASE WHEN n.resolved THEN 1 ELSE 0 END) AS resolved, "
        f"count(n.`{EMBEDDING_PROP}`) AS embedded"
    )
    row = rows[0] if rows else {}
    nodes = int(row.get("nodes") or 0)
    identities = int(row.get("identities") or 0)
    if label == ENTITY_LABEL:
        # An entity's `aliases` holds only the *other* surface forms; the node's own name
        # is one too, so the identity count is aliases + one per node.
        identities += nodes
    identities = max(identities, nodes)
    return {
        "nodes": nodes,
        "identities": identities,
        "identities_per_node": round(identities / nodes, 4) if nodes else 0.0,
        "duplicate_rate": round(1 - nodes / identities, 4) if identities else 0.0,
        "resolved": int(row.get("resolved") or 0),
        "embedded": int(row.get("embedded") or 0),
        "key": key,
    }


# --------------------------------------------------------------------------------- writes


def write_embeddings(ctx: GraphContext, label: str, rows: Sequence[dict[str, Any]]) -> int:
    """`{id, embedding, embed_hash}` onto existing nodes. Never creates one."""
    if not rows:
        return 0
    key = KEY_PROPS[label]
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (n:{ctx.label(label)} {{`{key}`: row.id}})\n"
        f"SET n.`{EMBEDDING_PROP}` = row.embedding, n.`{EMBED_HASH_PROP}` = row.embed_hash",
        rows,
    )
    return len(rows)


def write_same_as(ctx: GraphContext, label: str, pairs: Sequence[Pair]) -> int:
    """`(a)-[:SAME_AS {tier, score, reason, rule}]->(b)` — the merge, before it happens.

    Written first and on purpose (brief 08 decision 5): with `--dry-run` this is the whole
    output, and it is what a reviewer reads to disagree with a merge before it is taken.
    """
    if not pairs:
        return 0
    key = KEY_PROPS[label]
    rows = [
        {
            "src": p.a,
            "dst": p.b,
            "props": {
                "tier": p.tier,
                "rule": p.rule,
                "score": p.score,
                "reason": p.reason,
            },
        }
        for p in pairs
    ]
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (a:{ctx.label(label)} {{`{key}`: row.src}})\n"
        f"MATCH (b:{ctx.label(label)} {{`{key}`: row.dst}})\n"
        f"MERGE (a)-[r:{SAME_AS}]->(b)\n"
        "SET r += row.props",
        rows,
    )
    return len(rows)


#: `combine` for the lists the brief names, `discard` (= keep the survivor's) for the rest.
#: Without the `.*` fallback every differing property — `id` included — becomes an array,
#: and an `id` that is an array breaks the uniqueness constraint the whole graph rests on.
MERGE_PROPERTIES: dict[str, str] = {
    "aliases": "combine",
    "merged_from": "combine",
    "identity_keys": "combine",
    "identities": "combine",
    ".*": "discard",
}


def merge_groups(ctx: GraphContext, label: str, groups: Sequence[Sequence[str]]) -> dict[str, Any]:
    """`apoc.refactor.mergeNodes` per group, survivor first. Returns what it deleted.

    Groups are applied one at a time rather than in one `UNWIND`: a group that fails
    (a node another group already swallowed) must not take the rest of the batch with it.
    """
    if not groups:
        return {"groups": 0, "merged": 0, "deleted": 0, "failed": []}
    key = KEY_PROPS[label]
    cypher = (
        "UNWIND $groups AS group\n"
        f"MATCH (n:{ctx.label(label)}) WHERE n.`{key}` IN group\n"
        f"WITH group, n ORDER BY apoc.coll.indexOf(group, n.`{key}`)\n"
        "WITH group, collect(n) AS nodes WHERE size(nodes) > 1\n"
        "CALL apoc.refactor.mergeNodes(nodes, {properties: $properties, mergeRels: false}) "
        "YIELD node\n"
        "RETURN count(node) AS merged"
    )
    counters = ctx.write(cypher, groups=[list(g) for g in groups], properties=MERGE_PROPERTIES)
    return {
        "groups": len(groups),
        "merged": sum(len(g) - 1 for g in groups),
        "deleted": counters.get("nodes_deleted", 0),
        "failed": [],
    }


def set_resolved(ctx: GraphContext, label: str, rows: Sequence[dict[str, Any]]) -> int:
    """Rewrite the survivor's lists exactly, then mark it resolved.

    `properties: combine` already concatenated the lists; this replaces them with the
    de-duplicated, sorted value this run computed, so two runs that merged the same
    identities leave byte-identical nodes.
    """
    if not rows:
        return 0
    key = KEY_PROPS[label]
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (n:{ctx.label(label)} {{`{key}`: row.id}})\n"
        "SET n += row.props",
        rows,
    )
    return len(rows)


def delete_self_loops(ctx: GraphContext, label: str) -> int:
    """A `SAME_AS` between two nodes that are now one node. Staging, spent."""
    counters = ctx.write(
        f"MATCH (n:{ctx.label(label)})-[r:{SAME_AS}]->(n) DELETE r",
    )
    return counters.get("relationships_deleted", 0)


def dedupe_relationships(ctx: GraphContext, label: str, ids: Sequence[str]) -> int:
    """Delete edges that are duplicates by type, direction, endpoints *and* properties.

    `mergeRels: false` moves both of two identical `AUTHORED` edges onto the survivor;
    this removes the copy. Edges that differ in any property are left alone — two
    `ASSIGNED_TO` intervals on one item are two facts, not one duplicated fact.
    """
    if not ids:
        return 0
    deleted = 0
    for direction in (
        f"MATCH (n:{ctx.label(label)})-[r]->(m) WHERE n.`{KEY_PROPS[label]}` IN $ids",
        f"MATCH (n:{ctx.label(label)})<-[r]-(m) WHERE n.`{KEY_PROPS[label]}` IN $ids",
    ):
        counters = ctx.write(
            f"{direction}\n"
            "WITH n, m, type(r) AS t, properties(r) AS props, collect(r) AS rels\n"
            "WHERE size(rels) > 1\n"
            "UNWIND tail(rels) AS extra\n"
            "DELETE extra",
            ids=list(ids),
        )
        deleted += counters.get("relationships_deleted", 0)
    return deleted


def clear_embeddings(ctx: GraphContext, label: str, ids: Sequence[str]) -> int:
    """Drop the vectors of nodes a merge changed, so the next run re-embeds those only.

    Scoped to the survivors on purpose: they are the nodes whose text moved (they
    inherited the other node's identities and activity). Clearing the whole label would
    make every later pass re-embed 2,187 people to catch the dozen that changed.
    """
    if not ids:
        return 0
    counters = ctx.write(
        f"MATCH (n:{ctx.label(label)}) WHERE n.`{KEY_PROPS[label]}` IN $ids "
        f"AND n.`{EMBEDDING_PROP}` IS NOT NULL "
        f"REMOVE n.`{EMBEDDING_PROP}`, n.`{EMBED_HASH_PROP}`",
        ids=list(ids),
    )
    return counters.get("properties_set", 0)
