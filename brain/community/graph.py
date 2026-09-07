"""Every read and write this step makes against Neo4j.

Three things here are worth reading twice.

**The partition is replaced, never patched.** A Leiden run answers "what are the communities
of this graph *now*", so a `Community` node the current run did not produce is not a
community any more, and leaving it would let global search return a report about a cluster
that no longer exists. Build therefore deletes what the run did not produce — after
`carry_reports` has lifted every still-valid report out of the way.

**A report follows its members, not its id.** `Community.id` is `L<level>-<leidenId>` and
the Leiden id comes from internal node ids that move whenever the projection changes.
`member_hash` does not: it is sha1 over the member keys. So carry-over is keyed on the hash,
and a community whose membership is unchanged keeps its title, summary, findings *and its
embedding* across a rebuild — which is what "re-summarise only what changed" costs nothing
to be true.

**`findings` is a list of JSON strings.** Neo4j properties are scalars or arrays of scalars;
a finding is `{statement, evidence_chunk_ids[]}`. Splitting it across two parallel arrays
would let them drift; a `Finding` node is a schema change the spec does not have. One JSON
document per finding keeps a finding and its evidence in one value, and
`finding_statements` beside it keeps the text readable to a human and to a fulltext index.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

from brain.graph.context import GraphContext

LABEL = "Community"
KEY = "id"
IN_COMMUNITY = "IN_COMMUNITY"
INDEX_NAME = "community_embedding"
EMBEDDING_PROP = "embedding"
EMBED_HASH_PROP = "embed_hash"
MODEL = "opus:community-summarizer"

#: The four properties conventions rule 3 requires on every LLM-derived node.
PROVENANCE_PROPS: tuple[str, ...] = ("evidence_chunk_ids", "batch_id", "model", "extracted_at")
#: On a report this run copied from a community with the same members, the id it came from.
COPIED_FROM = "copied_from"
#: Everything a community report puts on the node. Carried across a rebuild as one unit.
REPORT_PROPS: tuple[str, ...] = (
    "title",
    "summary",
    "findings",
    "finding_statements",
    "rank",
    "rank_reason",
    "reported_at",
    COPIED_FROM,
    *PROVENANCE_PROPS,
)
#: What `report_is_current` compares. `reported_at` is when merge last ran, not what it
#: said, so including it would make every re-merge look like a change.
COMPARED_PROPS: tuple[str, ...] = tuple(p for p in REPORT_PROPS if p != "reported_at")
#: Wiped when a rebuild writes the partition, restored by `carry_reports`. A report belongs
#: to a set of members, so it may not survive on an id whose members changed.
CLEARED_ON_REBUILD: tuple[str, ...] = (*REPORT_PROPS, EMBEDDING_PROP, EMBED_HASH_PROP)

#: Where each projected label keeps its key, its display name and its description.
MEMBER_LABELS: tuple[str, ...] = ("Entity", "WorkItem", "Document", "Component")
DESCRIPTION_CHARS = 300


class CommunityGraphError(RuntimeError):
    """The graph cannot hold the communities this run computed."""


# -------------------------------------------------------------------------------- schema


def apply_community_schema(ctx: GraphContext, dim: int) -> dict[str, Any]:
    """`Community.id` unique, a level lookup, and the vector index S5 queries.

    `brain load` already declares the `Community.id` constraint; repeating it is what lets
    a scratch namespace run this step without running the load first.
    """
    if not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"vector dimension must be a positive int, got {dim!r}")
    statements = [
        f"CREATE CONSTRAINT {ctx.name('brain_community_id_key')} IF NOT EXISTS "
        f"FOR (c:{ctx.label(LABEL)}) REQUIRE c.`id` IS UNIQUE",
        f"CREATE INDEX {ctx.name('brain_community_level_idx')} IF NOT EXISTS "
        f"FOR (c:{ctx.label(LABEL)}) ON (c.`level`)",
        f"CREATE INDEX {ctx.name('brain_community_member_hash_idx')} IF NOT EXISTS "
        f"FOR (c:{ctx.label(LABEL)}) ON (c.`member_hash`)",
        # Index options take literals, not parameters; `dim` is checked as an int above.
        f"CREATE VECTOR INDEX {ctx.name(INDEX_NAME)} IF NOT EXISTS "
        f"FOR (c:{ctx.label(LABEL)}) ON (c.`{EMBEDDING_PROP}`) "
        "OPTIONS {indexConfig: {`vector.dimensions`: " + str(dim) + ", "
        "`vector.similarity_function`: 'cosine'}}",
    ]
    for cypher in statements:
        ctx.write(cypher)
    waited = await_own_indexes(ctx)
    return {
        "constraints": 1,
        "indexes": 2,
        "vector_index": index_name(ctx),
        "dim": dim,
        "similarity": "cosine",
        **waited,
    }


SCHEMA_NAMES: tuple[str, ...] = (
    "brain_community_id_key",
    "brain_community_level_idx",
    "brain_community_member_hash_idx",
    INDEX_NAME,
)

#: How long `await_own_indexes` waits before reporting what is still not ONLINE.
INDEX_WAIT_SECONDS = 120.0
INDEX_POLL_SECONDS = 0.2


def await_own_indexes(
    ctx: GraphContext, timeout: float = INDEX_WAIT_SECONDS, poll: float = INDEX_POLL_SECONDS
) -> dict[str, Any]:
    """Wait for *this step's* indexes. Never for the whole database.

    `CALL db.awaitIndexes` is database-wide: it blocks on every index in the database,
    including the ones another step's scratch namespace left half-built. Observed, not
    theorised — this call sat for four minutes on a `_ResetTestbrain_file_path_key` stuck
    at POPULATING 100% while three agents shared one Neo4j, and would have burned its full
    300-second timeout on an index this step neither made nor reads.

    Polling the four names this step owns answers the only question that matters. What is
    still pending when the timeout runs out is returned rather than raised: the index's
    state is in the report either way, and a merge that has already validated its batches
    should not throw them away over a slow index build.
    """
    names = [f"{ctx.prefix}{name}" for name in SCHEMA_NAMES]
    started = time.monotonic()
    pending: dict[str, str] = {}
    while True:
        rows = ctx.read(
            "SHOW INDEXES YIELD name, state WHERE name IN $names RETURN name, state", names=names
        )
        pending = {r["name"]: r["state"] for r in rows if r["state"] != "ONLINE"}
        if not pending or time.monotonic() - started >= timeout:
            break
        time.sleep(poll)
    return {
        "awaited_indexes": len(names),
        "await_ms": round((time.monotonic() - started) * 1000),
        "indexes_not_online": pending,
    }


def drop_community_schema(ctx: GraphContext) -> None:
    """Tests only: remove what a scratch namespace created — the meta node included.

    An `IndexMeta` left behind would make the next run in this namespace describe an index
    that no longer exists, which is worse than describing none.
    """
    for name in SCHEMA_NAMES:
        ctx.client.write(f"DROP CONSTRAINT {ctx.name(name)} IF EXISTS")
        ctx.client.write(f"DROP INDEX {ctx.name(name)} IF EXISTS")
    ctx.client.write(
        f"MATCH (m:{ctx.label(META_LABEL)} {{`name`: $name}}) DETACH DELETE m",
        name=index_name(ctx),
    )


def index_name(ctx: GraphContext) -> str:
    return f"{ctx.prefix}{INDEX_NAME}"


META_LABEL = "IndexMeta"


def write_index_meta(ctx: GraphContext, *, model: str, dim: int, now: str) -> dict[str, Any]:
    """Record what made these vectors, beside the index itself.

    `brain chunk` and `brain resolve` each write one of these for the same reason: a vector
    index is unreadable without knowing which model produced it, and a question embedded by
    a different model returns nonsense rather than an error. `live` is the number of
    `Community` nodes actually carrying a vector right now, so a reader can spot a
    half-embedded index — a `live` under the summarised count — without trusting this run's
    own arithmetic.
    """
    live = ctx.read(
        f"MATCH (c:{ctx.label(LABEL)}) WHERE c.`{EMBEDDING_PROP}` IS NOT NULL "
        "RETURN count(c) AS live"
    )
    status = index_status(ctx)
    props = {
        "model": model,
        "dim": dim,
        "similarity": "cosine",
        "label": LABEL,
        "live": int(live[0]["live"]) if live else 0,
        "state": status.get("state"),
        "updated_at": now,
    }
    ctx.write(
        f"MERGE (m:{ctx.label(META_LABEL)} {{`name`: $name}})\n"
        "ON CREATE SET m.created_at = $now\n"
        "SET m += $props",
        name=index_name(ctx),
        now=now,
        props=props,
    )
    return {"name": index_name(ctx), **props}


def read_index_meta(ctx: GraphContext) -> dict[str, Any] | None:
    rows = ctx.read(
        f"MATCH (m:{ctx.label(META_LABEL)} {{`name`: $name}}) "
        "RETURN m.name AS name, m.label AS label, m.model AS model, m.dim AS dim, "
        "m.similarity AS similarity, m.live AS live, m.state AS state, "
        "toString(m.created_at) AS created_at, toString(m.updated_at) AS updated_at",
        name=index_name(ctx),
    )
    return rows[0] if rows else None


def index_status(ctx: GraphContext) -> dict[str, Any]:
    """The vector index as the server reports it — `state` is the acceptance criterion."""
    rows = ctx.read(
        "SHOW INDEXES YIELD name, type, state, populationPercent, labelsOrTypes, properties, "
        "options WHERE name = $name "
        "RETURN name, type, state, populationPercent, labelsOrTypes, properties, options",
        name=index_name(ctx),
    )
    if not rows:
        return {"name": index_name(ctx), "exists": False, "state": "MISSING"}
    row = rows[0]
    config = (row.get("options") or {}).get("indexConfig") or {}
    return {
        "name": row["name"],
        "exists": True,
        "type": row["type"],
        "state": row["state"],
        "population_percent": row.get("populationPercent"),
        "labels": row.get("labelsOrTypes"),
        "properties": row.get("properties"),
        "dim": config.get("vector.dimensions"),
        "similarity": config.get("vector.similarity_function"),
    }


# --------------------------------------------------------------------------------- reads


def read_reports(ctx: GraphContext) -> list[dict[str, Any]]:
    """Every summarised community's report, with the member set and level it was written for.

    Read before the partition is replaced and written back after — the whole of "a
    community that did not change is not summarised again".

    A list, and not the map keyed on `member_hash` this used to return. One member set can
    carry two reports: 22 do on the live graph, where a coarse community holds exactly the
    members of a fine one and each was summarised on its own. A map keyed on the hash alone
    keeps one of them and drops the other silently, and the next rebuild then hands the
    survivor's text, batch id and extraction time to both — a report attributed to a batch
    that never wrote it, with nothing failing to say so.
    """
    props = ", ".join(f"c.`{p}` AS `{p}`" for p in REPORT_PROPS)
    return ctx.read(
        f"MATCH (c:{ctx.label(LABEL)}) WHERE c.`summary` IS NOT NULL\n"
        "RETURN c.`member_hash` AS member_hash, c.`level` AS level, c.`id` AS previous_id, "
        f"{props}, c.`{EMBEDDING_PROP}` AS embedding, c.`{EMBED_HASH_PROP}` AS embed_hash\n"
        "ORDER BY member_hash, previous_id"
    )


def reports_by_member_level(
    rows: Sequence[dict[str, Any]],
) -> dict[tuple[str, int], dict[str, Any]]:
    """`(member_hash, level)` -> the report written for exactly that community."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        member_hash, level = row.get("member_hash"), row.get("level")
        if member_hash and level is not None:
            out.setdefault((member_hash, int(level)), row)
    return out


def reports_by_member(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`member_hash` -> one report about that member set, whatever level wrote it.

    For the copy rule only, and deliberately lossy: when two levels hold the same members
    it keeps the lowest id. Which of two equally valid reports gets copied is arbitrary —
    but it must be the same one on every run, so the ordering is the query's, not the
    dictionary's.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: str(r.get("previous_id") or "")):
        member_hash = row.get("member_hash")
        if member_hash:
            out.setdefault(member_hash, row)
    return out


def census(ctx: GraphContext) -> dict[str, Any]:
    """What the graph holds now — read back, never inferred from what we meant to send."""
    label = ctx.label(LABEL)
    by_level = ctx.read(
        f"MATCH (c:{label})\n"
        "RETURN c.level AS level, count(c) AS communities, "
        "sum(CASE WHEN c.summary IS NOT NULL THEN 1 ELSE 0 END) AS summarized, "
        "sum(CASE WHEN c.misc THEN 1 ELSE 0 END) AS misc, "
        f"count(c.`{EMBEDDING_PROP}`) AS embedded, sum(c.size) AS members "
        "ORDER BY level"
    )
    edges = ctx.read(
        f"MATCH (m)-[r:{IN_COMMUNITY}]->(c:{label}) "
        "RETURN r.level AS level, count(r) AS n ORDER BY level"
    )
    return {
        "communities": sum(r["communities"] for r in by_level),
        "by_level": {
            str(r["level"]): {
                "communities": r["communities"],
                "summarized": r["summarized"],
                "misc": r["misc"],
                "embedded": r["embedded"],
                "members": r["members"],
            }
            for r in by_level
        },
        "in_community_edges": {str(r["level"]): r["n"] for r in edges},
        "in_community_total": sum(r["n"] for r in edges),
    }


def provenance_gaps(ctx: GraphContext) -> dict[str, Any]:
    """Summarised communities missing a provenance property. The count must be zero."""
    missing = " OR ".join(f"c.`{p}` IS NULL" for p in PROVENANCE_PROPS)
    rows = ctx.read(
        f"MATCH (c:{ctx.label(LABEL)}) WHERE c.`summary` IS NOT NULL "
        f"AND ({missing} OR size(c.`evidence_chunk_ids`) = 0) "
        "RETURN c.id AS id ORDER BY id"
    )
    return {"total": len(rows), "examples": [r["id"] for r in rows[:20]]}


def unplaced_members(ctx: GraphContext, level: int) -> dict[str, Any]:
    """Projected nodes with no `IN_COMMUNITY` at this level, by label. Must be zero."""
    out: dict[str, int] = {}
    for label in MEMBER_LABELS:
        rows = ctx.read(
            f"MATCH (n:{ctx.label(label)}) WHERE coalesce(n.synthetic, false) = false "
            f"AND NOT (n)-[:{IN_COMMUNITY} {{level: $level}}]->(:{ctx.label(LABEL)}) "
            "RETURN count(n) AS n",
            level=level,
        )
        out[label] = int(rows[0]["n"]) if rows else 0
    return {"by_label": out, "total": sum(out.values())}


def _member_projection(ctx: GraphContext, var: str) -> str:
    """`kind`, `name` and `description` for a member, whatever label it carries."""
    entity, work, doc, comp = (ctx.label(x) for x in MEMBER_LABELS)
    return (
        f"CASE WHEN {var}:{entity} THEN {var}.kind WHEN {var}:{work} THEN {var}.type "
        f"WHEN {var}:{doc} THEN {var}.kind ELSE 'Component' END AS kind, "
        f"CASE WHEN {var}:{entity} THEN {var}.name WHEN {var}:{comp} THEN {var}.name "
        f"ELSE {var}.title END AS name, "
        f"left(coalesce({var}.description, ''), {DESCRIPTION_CHARS}) AS description"
    )


def read_members(
    ctx: GraphContext, ids: Sequence[str], *, top_n: int
) -> dict[str, list[dict[str, Any]]]:
    """Top-`top_n` members per community by projected degree, with what names them."""
    if not ids:
        return {}
    label_case = " ".join(f"WHEN m:{ctx.label(x)} THEN '{x}'" for x in MEMBER_LABELS)
    key_case = " ".join(
        f"WHEN m:{ctx.label(x)} THEN m.`{p}`"
        for x, p in (
            ("Entity", "id"),
            ("WorkItem", "key"),
            ("Document", "key"),
            ("Component", "name"),
        )
    )
    rows = ctx.read(
        f"MATCH (m)-[r:{IN_COMMUNITY}]->(c:{ctx.label(LABEL)}) WHERE c.id IN $ids\n"
        f"WITH c.id AS cid, CASE {label_case} END AS label, CASE {key_case} END AS key, "
        f"coalesce(r.degree, 0) AS degree, {_member_projection(ctx, 'm')}\n"
        "ORDER BY cid, degree DESC, label, key\n"
        "WITH cid, collect({key: key, label: label, kind: kind, name: coalesce(name, key), "
        "description: description, degree: degree})[..$n] AS members\n"
        "RETURN cid, members",
        ids=list(ids),
        n=top_n,
    )
    return {r["cid"]: r["members"] for r in rows}


#: The map every evidence query collects. One place, so the two paths cannot disagree.
_EVIDENCE_MAP = (
    "{chunk_id: ch.id, parent_key: ch.parent_key, "
    "text: left(coalesce(ch.text, ''), $chars), members_mentioned: hits}"
)


def read_evidence(
    ctx: GraphContext, ids: Sequence[str], *, limit: int, chars: int
) -> dict[str, list[dict[str, Any]]]:
    """Up to `limit` chunks per community, most connected to its members first.

    Ranked by how many of the community's `Entity` members a chunk mentions — the chunk
    that names six of them is the paragraph the community is *about*. A community whose
    members are documents and work items rather than entities gets its parents' own
    chunks instead, so a report is never asked to cite evidence it was not given.
    Orphaned chunks are skipped: their text is gone from the corpus, and a citation of one
    would point a reader at a paragraph nobody can find again.
    """
    if not ids:
        return {}
    chunk = ctx.label("Chunk")
    community = ctx.label(LABEL)
    mentioned = ctx.read(
        f"MATCH (e:{ctx.label('Entity')})-[:{IN_COMMUNITY}]->(c:{community})\n"
        "WHERE c.id IN $ids\n"
        f"MATCH (ch:{chunk})-[:MENTIONS]->(e)\n"
        "WHERE coalesce(ch.orphaned, false) = false\n"
        "WITH c.id AS cid, ch, count(DISTINCT e) AS hits\n"
        "ORDER BY cid, hits DESC, ch.id\n"
        f"WITH cid, collect({_EVIDENCE_MAP})[..$limit] AS evidence\n"
        "RETURN cid, evidence",
        ids=list(ids),
        limit=limit,
        chars=chars,
    )
    out = {r["cid"]: list(r["evidence"]) for r in mentioned}

    short = [i for i in ids if len(out.get(i, [])) < limit]
    if short:
        parents = ctx.read(
            f"MATCH (p)-[:{IN_COMMUNITY}]->(c:{community})\n"
            "WHERE c.id IN $ids\n"
            f"MATCH (p)-[:HAS_CHUNK]->(ch:{chunk})\n"
            "WHERE coalesce(ch.orphaned, false) = false\n"
            "WITH c.id AS cid, ch, 0 AS hits\n"
            "ORDER BY cid, ch.parent_key, coalesce(ch.position, 0), ch.id\n"
            f"WITH cid, collect({_EVIDENCE_MAP})[..$limit] AS evidence\n"
            "RETURN cid, evidence",
            ids=short,
            limit=limit,
            chars=chars,
        )
        for row in parents:
            have = out.setdefault(row["cid"], [])
            seen = {e["chunk_id"] for e in have}
            for item in row["evidence"]:
                if len(have) >= limit:
                    break
                if item["chunk_id"] not in seen:
                    have.append(item)
                    seen.add(item["chunk_id"])
    return {i: out.get(i, []) for i in ids}


def existing_chunk_ids(ctx: GraphContext, ids: Sequence[str]) -> set[str]:
    """Which of `ids` the graph actually holds. A cited chunk that is gone is an error."""
    if not ids:
        return set()
    rows = ctx.read(
        f"MATCH (c:{ctx.label('Chunk')}) WHERE c.id IN $ids RETURN c.id AS id", ids=list(ids)
    )
    return {r["id"] for r in rows}


# -------------------------------------------------------------------------------- writes


#: `IN_COMMUNITY` starts at the member, so the member's label decides the query.
MEMBER_KEY_PROPS: dict[str, str] = {
    "Entity": "id",
    "WorkItem": "key",
    "Document": "key",
    "Component": "name",
}


def replace_communities(
    ctx: GraphContext,
    rows: Sequence[dict[str, Any]],
    membership: dict[str, list[dict[str, Any]]],
    *,
    built_at: str,
) -> dict[str, int]:
    """Write this run's partition and delete everything it did not produce.

    Order matters. The nodes are merged first so a carried-over report has somewhere to
    land; the stale nodes go next (with their `IN_COMMUNITY` edges, hence `DETACH`); the
    memberships are rewritten after, each stamped with `built_at`, and the sweep at the end
    deletes every edge this run did not stamp. That last step is what keeps a member from
    belonging to a community it left: `MERGE` alone only ever adds.
    """
    label = ctx.label(LABEL)
    # A `MERGE` onto an id this run reused would leave the previous run's report sitting on
    # a community that no longer holds those members — invisible, because `summarized` goes
    # back to false while `summary` keeps a text about something else, and `collect` skips
    # any community that has one. Every report property is removed here and put back by
    # `carry_reports` a moment later, for exactly the communities that earned it.
    cleared = ", ".join(f"c.`{p}`" for p in CLEARED_ON_REBUILD)
    ctx.write_rows(
        f"UNWIND $rows AS row\nMERGE (c:{label} {{`id`: row.id}})\n"
        f"SET c += row.props\nREMOVE {cleared}",
        rows,
    )
    ids = [r["id"] for r in rows]
    deleted = ctx.write(f"MATCH (c:{label}) WHERE NOT c.id IN $ids DETACH DELETE c", ids=ids).get(
        "nodes_deleted", 0
    )
    written = 0
    for member_label, member_rows in sorted(membership.items()):
        if not member_rows:
            continue
        key_prop = MEMBER_KEY_PROPS[member_label]
        ctx.write_rows(
            "UNWIND $rows AS row\n"
            f"MATCH (m:{ctx.label(member_label)} {{`{key_prop}`: row.member_key}})\n"
            f"MATCH (c:{label} {{`id`: row.community}})\n"
            f"MERGE (m)-[r:{IN_COMMUNITY} {{level: row.level}}]->(c)\n"
            "SET r.degree = row.degree, r.built_at = row.built_at",
            [{**r, "built_at": built_at} for r in member_rows],
        )
        written += len(member_rows)
    stale = ctx.write(
        f"MATCH ()-[r:{IN_COMMUNITY}]->(:{label}) WHERE coalesce(r.built_at, '') <> $now DELETE r",
        now=built_at,
    ).get("relationships_deleted", 0)
    return {
        "communities_written": len(rows),
        "communities_deleted": deleted,
        "memberships_written": written,
        "memberships_deleted": stale,
    }


def carry_reports(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> int:
    """Put a still-valid report (and its vector) back on the node that now holds it."""
    if not rows:
        return 0
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (c:{ctx.label(LABEL)} {{`id`: row.id}})\n"
        "SET c += row.props, c.summarized = true\n"
        "WITH c, row WHERE row.embedding IS NOT NULL\n"
        f"CALL db.create.setNodeVectorProperty(c, '{EMBEDDING_PROP}', row.embedding)\n"
        f"SET c.`{EMBED_HASH_PROP}` = row.embed_hash",
        rows,
    )
    return len(rows)


def copy_reports(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> int:
    """`carry_reports` across *levels* rather than across rebuilds.

    Same write, different question. `carry_reports` answers "this community survived a
    rebuild"; this one answers "another community holds exactly these members and already
    has a report". Both put a text somewhere it was not written for, and both keep the
    provenance of the run that wrote it — `copied_from` in the props is what says which.
    """
    return carry_reports(ctx, rows)


def stored_reports(ctx: GraphContext, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """The report properties these communities already carry, for change detection."""
    if not ids:
        return {}
    props = ", ".join(f"c.`{p}` AS `{p}`" for p in COMPARED_PROPS)
    rows = ctx.read(
        f"MATCH (c:{ctx.label(LABEL)}) WHERE c.id IN $ids AND c.`summary` IS NOT NULL\n"
        f"RETURN c.id AS id, {props}",
        ids=list(ids),
    )
    return {row.pop("id"): row for row in rows}


def cross_level_duplicates(ctx: GraphContext) -> dict[str, Any]:
    """Summarisable communities at different levels holding exactly the same members.

    Leiden's coarse level does not have to merge anything, and when it does not, the
    coarse community *is* the fine one under another id. Reported rather than hidden: it
    is the honest measure of how much a second level is worth on this corpus.
    """
    rows = ctx.read(
        f"MATCH (c:{ctx.label(LABEL)}) WHERE coalesce(c.misc, false) = false\n"
        "WITH c ORDER BY c.level, c.id\n"
        "WITH c.member_hash AS member_hash, c.size AS size, "
        "collect({id: c.id, level: c.level, title: c.title}) AS communities\n"
        "WHERE size(communities) > 1\n"
        "RETURN member_hash, size, communities ORDER BY size DESC, member_hash"
    )
    return {
        "total": len(rows),
        "communities": sum(len(r["communities"]) for r in rows),
        "levels_involved": sorted({c["level"] for r in rows for c in r["communities"]}),
        "pairs": [
            {
                "member_hash": r["member_hash"],
                "size": r["size"],
                "ids": [c["id"] for c in r["communities"]],
                "titles": [c["title"] for c in r["communities"]],
            }
            for r in rows
        ],
    }


def write_reports(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> int:
    """The agent's report onto an existing `Community`. Never creates one."""
    if not rows:
        return 0
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (c:{ctx.label(LABEL)} {{`id`: row.id}})\n"
        "SET c += row.props, c.summarized = true",
        rows,
    )
    return len(rows)


def write_embeddings(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> int:
    """`db.create.setNodeVectorProperty` — it stores the float32 array the index reads."""
    if not rows:
        return 0
    ctx.write_rows(
        "UNWIND $rows AS row\n"
        f"MATCH (c:{ctx.label(LABEL)} {{`id`: row.id}})\n"
        f"CALL db.create.setNodeVectorProperty(c, '{EMBEDDING_PROP}', row.embedding)\n"
        f"SET c.`{EMBED_HASH_PROP}` = row.embed_hash",
        rows,
    )
    return len(rows)


def encode_findings(findings: Sequence[dict[str, Any]]) -> list[str]:
    """One compact JSON document per finding — see the module docstring."""
    return [
        json.dumps(
            {
                "statement": f["statement"],
                "evidence_chunk_ids": sorted(f.get("evidence_chunk_ids") or []),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for f in findings
    ]


def decode_findings(values: Sequence[str] | None) -> list[dict[str, Any]]:
    """The inverse, for the report and for retrieval. A broken row is skipped, not fatal."""
    out: list[dict[str, Any]] = []
    for value in values or []:
        try:
            item = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def top_ranked(ctx: GraphContext, n: int = 3) -> list[dict[str, Any]]:
    """The `n` highest-ranked reports — the report's "does this read like sense" sample."""
    rows = ctx.read(
        f"MATCH (c:{ctx.label(LABEL)}) WHERE c.summary IS NOT NULL\n"
        "RETURN c.id AS id, c.level AS level, c.size AS size, c.title AS title, "
        "c.rank AS rank, c.rank_reason AS rank_reason\n"
        "ORDER BY c.rank DESC, c.size DESC, c.id LIMIT $n",
        n=n,
    )
    return rows
