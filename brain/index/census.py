"""The Plan 1 graph census (step brief §10 decision 2).

Every number here is read back from Neo4j, never inferred from what a previous step said
it wrote — that is the whole point of a census: `data/reports/load.json` answers "what did
`brain load` intend", this answers "what does the graph contain". The two exceptions are
stated as such: resolution precision/recall comes from `data/reports/resolve.json` (it is
computed against a gold file, not derivable from the graph) and the dangling-reference
count comes from `data/reports/load.json` (a ref that resolved to nothing left no node to
count). Both carry their source path in the output.

**Absence is an answer.** Three other steps write into this graph, and a census that
crashed on a label that has not landed yet would be useless exactly when it is needed. A
label the database does not have is reported `present: false`, a report file that does not
exist is reported `available: false`, and Ollama being down is a `versions` entry, not a
traceback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from brain.graph.context import GraphContext
from brain.index.schema import strip_prefix

#: Labels `brain load` writes, counted once each.
STRUCTURAL_LABELS: tuple[str, ...] = (
    "WorkItem",
    "Document",
    "Person",
    "Commit",
    "PullRequest",
    "File",
    "StatusChange",
    "Component",
    "Version",
    "Sprint",
    "Area",
    "Space",
)

#: Labels the later steps write. `IndexMeta` is bookkeeping, not corpus.
DERIVED_LABELS: tuple[str, ...] = ("Chunk", "Entity", "Community", "IndexMeta")

#: Labels whose nodes carry a `synthetic` flag. Reported as three buckets — true, false and
#: *missing* — because a label where the flag never arrived is a different fact from a label
#: where every node is real, and collapsing them hides a backfill that has not run.
SYNTHETIC_FLAGGED: tuple[str, ...] = (
    "WorkItem",
    "Document",
    "Person",
    "Commit",
    "PullRequest",
    "StatusChange",
    "Component",
    "Version",
    "Sprint",
    "Area",
    "Space",
    "File",
    "Chunk",
    "Entity",
)

#: The four properties conventions rule 3 requires on every LLM-derived node and edge.
PROVENANCE_PROPS: tuple[str, ...] = ("evidence_chunk_ids", "batch_id", "model", "extracted_at")

#: Node labels an LLM produced, and the condition that makes a node of that label LLM-derived.
#: `Community` is the subtle one: the partition itself is GDS Leiden output, deterministic
#: and reproducible from the projection, so an unsummarised community owes no provenance.
#: What an LLM wrote is the *report* — and a community carrying one must cite its evidence.
LLM_NODE_LABELS: dict[str, str] = {
    "Entity": "",
    "Community": "n.summary IS NOT NULL",
}

#: Edge types an LLM produced: the closed relation set of `brain extract`, plus the
#: `MENTIONS` edge merge mints from each entity's quote.
LLM_EDGE_TYPES: tuple[str, ...] = (
    "MENTIONS",
    "DECIDES",
    "MOTIVATED_BY",
    "REJECTS",
    "DEPENDS_ON",
    "IMPLEMENTS",
    "INTRODUCES_RISK",
)

#: `SAME_AS` is written by `brain resolve` at three tiers. Tiers 1 and 2 are deterministic
#: (name rules, cosine) and carry `rule`/`score` instead of provenance; only tier 3 is an
#: adjudicator verdict, so only tier 3 is held to conventions rule 3.
ADJUDICATED_TIER = 3


# --------------------------------------------------------------------------- namespacing


def node_filter(ctx: GraphContext, var: str) -> str:
    """A WHERE fragment selecting nodes of this namespace and no other.

    The production run uses the empty prefix, and the same database also holds the label
    spaces the live tests build (`_Smoke…`, `_Comm…`, `_ResetTest…`). "Everything" would
    therefore count another test's fixtures as corpus, so the empty prefix means "every
    label that does not start with an underscore" rather than "every label".
    """
    if ctx.prefix:
        return f"any(l IN labels({var}) WHERE l STARTS WITH '{ctx.prefix}')"
    return f"none(l IN labels({var}) WHERE l STARTS WITH '_')"


def _one(rows: list[dict[str, Any]], **defaults: Any) -> dict[str, Any]:
    """The single row an aggregate returns — or the defaults if the read came back empty.

    Neo4j always returns one row for a bare `count()`, so this never fires in production.
    It fires in the unit tests, and that is the point: the census's contract is that it
    reports absence, and a `[0]` on an empty list is a traceback instead of a report.
    """
    return rows[0] if rows else dict(defaults)


def _count(ctx: GraphContext, label: str) -> int:
    return _one(ctx.read(f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS c"), c=0)["c"]


def present_labels(ctx: GraphContext) -> list[str]:
    """Labels this namespace actually has nodes for, unprefixed.

    `db.labels()` alone is not the answer: `brain load`'s schema declares constraints on
    `Chunk`, `Entity` and `Community` before any step writes one, and declaring a
    constraint registers the label token. A census built on the token list would report a
    `Chunk` section for a graph with no chunks in it. So the token list decides which
    labels are *safe to query* (asking about one the server has never seen emits an
    `01N50` notification per query) and the count decides which are present.
    """
    rows = ctx.read("CALL db.labels() YIELD label RETURN label")
    names = sorted({n for n in (strip_prefix(ctx, r["label"]) for r in rows) if n})
    return [name for name in names if _count(ctx, name)]


# ------------------------------------------------------------------------------- sections


def node_census(ctx: GraphContext, present: set[str]) -> dict[str, Any]:
    """Counts per label, split into the three kinds of label this graph has.

    Work-item sub-labels (`Bug`, `TestPlan`, `Epic`, …) are counted separately and summing
    them with `WorkItem` double-counts every node — which is why `total` is a single query
    over the namespace rather than a sum of this map.
    """
    structural: dict[str, Any] = {}
    derived: dict[str, Any] = {}
    for label in STRUCTURAL_LABELS:
        structural[label] = (
            {"present": True, "count": _count(ctx, label)}
            if label in present
            else {"present": False, "count": 0}
        )
    for label in DERIVED_LABELS:
        derived[label] = (
            {"present": True, "count": _count(ctx, label)}
            if label in present
            else {"present": False, "count": 0}
        )
    known = {*STRUCTURAL_LABELS, *DERIVED_LABELS}
    sub: dict[str, int] = {}
    other: dict[str, int] = {}
    for label in sorted(present - known):
        rows = ctx.read(
            f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS total, "
            f"count(CASE WHEN n:{ctx.label('WorkItem')} THEN 1 END) AS wi"
        )
        total = rows[0]["total"] if rows else 0
        if not total:
            continue
        (sub if rows[0]["wi"] == total else other)[label] = total
    total_rows = ctx.read(f"MATCH (n) WHERE {node_filter(ctx, 'n')} RETURN count(n) AS c")
    return {
        "total": total_rows[0]["c"] if total_rows else 0,
        "structural": structural,
        "derived": derived,
        "workitem_sublabels": sub,
        "other_labels": other,
        "note": (
            "workitem_sublabels are second labels on the same nodes as WorkItem; adding "
            "them to the structural counts double-counts. `total` counts each node once."
        ),
    }


def corpus_census(ctx: GraphContext, present: set[str]) -> dict[str, Any]:
    """The four corpus sizes the Plan 1 gate is written against.

    Real issues, not work items: the synthetic Xray/ADO layer also lands as `WorkItem`, and
    a gate that counted 3,265 of them would pass on data this project generated itself.
    """
    out: dict[str, Any] = {}
    if "WorkItem" in present:
        rows = ctx.read(
            f"MATCH (w:{ctx.label('WorkItem')}) "
            "RETURN w.source AS source, coalesce(w.synthetic, false) AS synthetic, "
            "count(w) AS c ORDER BY source"
        )
        out["workitems_by_source"] = [
            {"source": r["source"], "synthetic": r["synthetic"], "count": r["c"]} for r in rows
        ]
        out["real_issues"] = sum(
            r["c"] for r in rows if not r["synthetic"] and r["source"] == "jira"
        )
        out["synthetic_workitems"] = sum(r["c"] for r in rows if r["synthetic"])
    if "Commit" in present:
        out["commits"] = _count(ctx, "Commit")
        if "Chunk" in present:
            out["commits_keyed"] = _one(
                ctx.read(
                    f"MATCH (n:{ctx.label('Commit')})-[:HAS_CHUNK]->(:{ctx.label('Chunk')}) "
                    "RETURN count(DISTINCT n) AS c"
                ),
                c=0,
            )["c"]
    if "Document" in present:
        embedded_clause = (
            "EXISTS { MATCH (d)-[:HAS_CHUNK]->(c) WHERE c.embedding IS NOT NULL "
            "AND NOT coalesce(c.orphaned, false) }"
            if "Chunk" in present
            else "false"
        )
        # "Referenced" is the definition `brain chunk` scoped on: a KIP named by a work item
        # or a commit — the slice's own reading list. KIP pages cite each other constantly,
        # so counting any incoming reference would answer a different question (773 here vs
        # 268) and turn the gate's "all referenced" into a criterion nothing could meet.
        referenced = (
            "EXISTS { MATCH (s)-[:REFERENCES|IMPLEMENTS_KIP]->(d) "
            f"WHERE s:{ctx.label('WorkItem')} OR s:{ctx.label('Commit')} }}"
        )
        row = _one(
            ctx.read(
                f"MATCH (d:{ctx.label('Document')}) WHERE d.kind = 'KIP' "
                f"WITH d, {referenced} AS referenced, "
                "EXISTS { MATCH ()-[:REFERENCES|IMPLEMENTS_KIP]->(d) } AS cited_by_anything, "
                f"{embedded_clause} AS embedded "
                "RETURN count(d) AS kips, count(CASE WHEN referenced THEN 1 END) AS referenced, "
                "count(CASE WHEN cited_by_anything THEN 1 END) AS cited, "
                "count(CASE WHEN embedded THEN 1 END) AS embedded, "
                "count(CASE WHEN referenced AND embedded THEN 1 END) AS both"
            ),
            kips=0,
            referenced=0,
            cited=0,
            embedded=0,
            both=0,
        )
        out.update(
            {
                "kip_documents": row["kips"],
                "kips_referenced": row["referenced"],
                "kips_cited_by_anything": row["cited"],
                "kips_embedded": row["embedded"],
                "kips_referenced_embedded": row["both"],
                "kips_referenced_not_embedded": row["referenced"] - row["both"],
                "kips_referenced_rule": (
                    "named by a WorkItem or a Commit — the scope `brain chunk` embedded "
                    "(brain/chunk/scope.py). kips_cited_by_anything additionally counts "
                    "KIP-to-KIP citations, which Phase A does not chunk on."
                ),
            }
        )
        out["documents"] = _count(ctx, "Document")
    return out


def synthetic_census(ctx: GraphContext, present: set[str]) -> dict[str, Any]:
    """Real vs synthetic vs *unflagged*, per label that should carry the flag."""
    by_label: dict[str, Any] = {}
    totals = {"real": 0, "synthetic": 0, "missing_flag": 0}
    for label in SYNTHETIC_FLAGGED:
        if label not in present:
            by_label[label] = {"present": False}
            continue
        rows = ctx.read(
            f"MATCH (n:{ctx.label(label)}) RETURN "
            "CASE WHEN n.synthetic IS NULL THEN 'missing_flag' "
            "WHEN n.synthetic THEN 'synthetic' ELSE 'real' END AS bucket, count(n) AS c"
        )
        buckets = {"real": 0, "synthetic": 0, "missing_flag": 0}
        for r in rows:
            buckets[r["bucket"]] = r["c"]
        by_label[label] = {"present": True, **buckets}
        for k in totals:
            totals[k] += buckets[k]
    return {
        "by_label": by_label,
        "totals": totals,
        "note": (
            "missing_flag = the node carries no `synthetic` property at all. It is not the "
            "same as `synthetic: false` and is reported separately so a backfill that has "
            "not run shows up as a number instead of as silence."
        ),
    }


def edge_census(ctx: GraphContext) -> dict[str, Any]:
    """Every relationship whose two ends are both inside this namespace, by type."""
    rows = ctx.read(
        f"MATCH (a)-[r]->(b) WHERE {node_filter(ctx, 'a')} AND {node_filter(ctx, 'b')} "
        "RETURN type(r) AS t, count(r) AS c ORDER BY t"
    )
    by_type = {r["t"]: r["c"] for r in rows}
    llm = {t: n for t, n in by_type.items() if t in LLM_EDGE_TYPES}
    structural = {t: n for t, n in by_type.items() if t not in LLM_EDGE_TYPES}
    return {
        "total": sum(by_type.values()),
        "by_type": by_type,
        "llm_derived": {"total": sum(llm.values()), "by_type": llm},
        "deterministic": {"total": sum(structural.values()), "by_type": structural},
    }


def _prov_expr(var: str) -> str:
    """True when all four provenance properties are present and the evidence is non-empty."""
    have = " AND ".join(f"{var}.`{p}` IS NOT NULL" for p in PROVENANCE_PROPS)
    return f"({have} AND size({var}.`evidence_chunk_ids`) > 0)"


def provenance_census(ctx: GraphContext, present: set[str]) -> dict[str, Any]:
    """Provenance coverage on everything an LLM produced. The gate reads `edges.missing`.

    Counted after the fact against the graph rather than trusted from the rows a merge
    said it sent, because "the rows I meant to write" is the one thing a bug lies about.
    """
    nodes: dict[str, Any] = {}
    node_missing = 0
    for label, llm_when in LLM_NODE_LABELS.items():
        if label not in present:
            nodes[label] = {"present": False}
            continue
        derived = llm_when or "true"
        gaps = ", ".join(
            f"count(CASE WHEN {derived} AND n.`{p}` IS NULL THEN 1 END) AS `missing_{p}`"
            for p in PROVENANCE_PROPS
        )
        row = _one(
            ctx.read(
                f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS total, "
                f"count(CASE WHEN {derived} THEN 1 END) AS derived, "
                f"count(CASE WHEN {derived} AND {_prov_expr('n')} THEN 1 END) AS complete, {gaps}"
            ),
            total=0,
            derived=0,
            complete=0,
            **{f"missing_{p}": 0 for p in PROVENANCE_PROPS},
        )
        total, llm_total, complete = row["total"], row["derived"], row["complete"]
        nodes[label] = {
            "present": True,
            "total": total,
            "llm_derived": llm_total,
            "llm_derived_rule": llm_when or "every node of this label",
            "with_provenance": complete,
            "missing": llm_total - complete,
            "pct": round(100 * complete / llm_total, 2) if llm_total else 100.0,
            "missing_by_prop": {
                p: row[f"missing_{p}"] for p in PROVENANCE_PROPS if row[f"missing_{p}"]
            },
        }
        node_missing += llm_total - complete

    edges: dict[str, Any] = {}
    edge_missing = 0
    for rel in LLM_EDGE_TYPES:
        row = _one(
            ctx.read(
                f"MATCH (a)-[r:`{rel}`]->(b) WHERE {node_filter(ctx, 'a')} RETURN count(r) AS "
                f"total, count(CASE WHEN {_prov_expr('r')} THEN 1 END) AS complete"
            ),
            total=0,
            complete=0,
        )
        total, complete = row["total"], row["complete"]
        if not total:
            continue
        edges[rel] = {
            "total": total,
            "with_provenance": complete,
            "missing": total - complete,
            "pct": round(100 * complete / total, 2),
        }
        edge_missing += total - complete

    same_as = _same_as_census(ctx)
    edge_missing += same_as["missing_on_adjudicated"]
    node_total = sum(v.get("llm_derived", 0) for v in nodes.values())
    edge_total = sum(v["total"] for v in edges.values())
    return {
        "rule": (
            "conventions rule 3: every LLM-derived node and edge carries "
            f"{list(PROVENANCE_PROPS)} with a non-empty evidence list."
        ),
        "nodes": {
            "by_label": nodes,
            "total": node_total,
            "missing": node_missing,
            "pct": round(100 * (node_total - node_missing) / node_total, 2)
            if node_total
            else 100.0,
        },
        "edges": {
            "by_type": edges,
            "total": edge_total,
            "missing": edge_missing,
            "pct": round(100 * (edge_total - edge_missing) / edge_total, 2)
            if edge_total
            else 100.0,
            "types": list(LLM_EDGE_TYPES),
        },
        "same_as": same_as,
    }


def _same_as_census(ctx: GraphContext) -> dict[str, Any]:
    """`SAME_AS` by tier. Only the adjudicated tier is held to conventions rule 3."""
    rows = ctx.read(
        f"MATCH (a)-[r:`SAME_AS`]->(b) WHERE {node_filter(ctx, 'a')} "
        "RETURN coalesce(r.tier, 0) AS tier, r.rule AS rule, count(r) AS c, "
        f"count(CASE WHEN {_prov_expr('r')} THEN 1 END) AS complete ORDER BY tier, rule"
    )
    total = sum(r["c"] for r in rows)
    adjudicated = [r for r in rows if r["tier"] == ADJUDICATED_TIER]
    return {
        "total": total,
        "by_tier": [
            {
                "tier": r["tier"],
                "rule": r["rule"],
                "edges": r["c"],
                "with_provenance": r["complete"],
            }
            for r in rows
        ],
        "adjudicated": sum(r["c"] for r in adjudicated),
        "missing_on_adjudicated": sum(r["c"] - r["complete"] for r in adjudicated),
        "note": (
            "tiers 1-2 are deterministic (name rules, cosine) and carry rule/score instead "
            "of provenance; they are surviving links, not merges — a merged pair leaves one "
            "node and no edge."
        ),
    }


def orphan_census(ctx: GraphContext, present: set[str]) -> dict[str, Any]:
    """Nodes with no relationship at all, per label.

    An orphan is not automatically a bug — a `Sprint` nothing was assigned to is honest —
    but it is always an answer the graph cannot give, so the census names every one.
    """
    by_label: dict[str, Any] = {}
    total = 0
    counted = 0
    for label in (*STRUCTURAL_LABELS, *DERIVED_LABELS):
        if label == "IndexMeta" or label not in present:
            continue
        rows = ctx.read(
            f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS total, "
            "count(CASE WHEN NOT (n)--() THEN 1 END) AS orphans"
        )
        row = rows[0] if rows else {"total": 0, "orphans": 0}
        counted += row["total"]
        if row["orphans"]:
            by_label[label] = {
                "orphans": row["orphans"],
                "of": row["total"],
                "pct": round(100 * row["orphans"] / row["total"], 2) if row["total"] else 0.0,
            }
            total += row["orphans"]
    return {
        "total": total,
        "of_nodes": counted,
        "pct": round(100 * total / counted, 2) if counted else 0.0,
        "by_label": by_label,
        "note": "IndexMeta is excluded: it is bookkeeping and has no edges by design.",
    }


def chunk_census(ctx: GraphContext, present: set[str]) -> dict[str, Any]:
    """Live vs orphaned vs embedded, from the step that owns the label."""
    if "Chunk" not in present:
        return {"present": False, "note": "no Chunk label — `brain chunk` has not run here."}
    from brain.chunk.graph import census as chunk_step_census

    out = chunk_step_census(ctx)
    out["present"] = True
    total, embedded = out.get("chunks", 0), out.get("embedded", 0)
    out["pct_embedded"] = round(100 * embedded / total, 2) if total else 0.0
    live = out.get("live", 0)
    live_embedded = _one(
        ctx.read(
            f"MATCH (c:{ctx.label('Chunk')}) WHERE c.embedding IS NOT NULL "
            "AND NOT coalesce(c.orphaned, false) RETURN count(c) AS c"
        ),
        c=0,
    )["c"]
    out["live_embedded"] = live_embedded
    out["pct_live_embedded"] = round(100 * live_embedded / live, 2) if live else 0.0
    return out


def community_census(ctx: GraphContext, present: set[str]) -> dict[str, Any]:
    """Communities per level, size distribution, and how much of the graph they cover.

    Written to survive `brain communities` not having run: the label is absent for most of
    Plan 1's life, and "absent" is the honest census entry, not a crash.
    """
    if "Community" not in present or not _count(ctx, "Community"):
        return {
            "present": False,
            "communities": 0,
            "note": (
                "no Community nodes — `brain communities` has not run against this graph "
                "yet. Re-run `brain index` after it lands to fill this section."
            ),
        }
    levels = ctx.read(
        f"MATCH (c:{ctx.label('Community')}) "
        "RETURN c.level AS level, count(c) AS communities, "
        "percentileDisc(c.size, 0.5) AS p50, percentileDisc(c.size, 0.95) AS p95, "
        "min(c.size) AS min, max(c.size) AS max, sum(c.size) AS members, "
        "count(CASE WHEN c.summary IS NOT NULL THEN 1 END) AS summarised "
        "ORDER BY level"
    )
    edges = _one(
        ctx.read(
            f"MATCH (n)-[r:`IN_COMMUNITY`]->(c:{ctx.label('Community')}) "
            "RETURN count(r) AS edges, count(DISTINCT n) AS members"
        ),
        edges=0,
        members=0,
    )
    covered = _one(
        ctx.read(
            f"MATCH (n)-[:`IN_COMMUNITY`]->(c:{ctx.label('Community')}) "
            "WHERE c.summary IS NOT NULL RETURN count(DISTINCT n) AS c"
        ),
        c=0,
    )["c"]
    members = edges["members"] or 0
    total_c = sum(r["communities"] for r in levels)
    summarised = sum(r["summarised"] for r in levels)
    return {
        "present": True,
        "communities": total_c,
        "summarised": summarised,
        "pct_summarised": round(100 * summarised / total_c, 2) if total_c else 0.0,
        "levels": [
            {
                "level": r["level"],
                "communities": r["communities"],
                "size_p50": r["p50"],
                "size_p95": r["p95"],
                "size_min": r["min"],
                "size_max": r["max"],
                "members": r["members"],
                "summarised": r["summarised"],
            }
            for r in levels
        ],
        "in_community_edges": edges["edges"],
        "distinct_members": members,
        "members_in_a_summarised_community": covered,
        "pct_members_in_a_summarised_community": (
            round(100 * covered / members, 2) if members else 0.0
        ),
    }


def index_meta_census(ctx: GraphContext, present: set[str], status: list[dict]) -> dict[str, Any]:
    """`IndexMeta` beside a **live** count of the nodes that actually carry a vector.

    Decision 5, from the chunk review: the stored `chunk_count` counts orphaned chunks too,
    so the census recomputes it. Both numbers are printed — the stored one is what a reader
    of the graph sees, the live one is what the index can actually return.
    """
    stored: dict[str, dict[str, Any]] = {}
    if "IndexMeta" in present:
        for r in ctx.read(f"MATCH (m:{ctx.label('IndexMeta')}) RETURN properties(m) AS p"):
            props = {k: v for k, v in r["p"].items() if k != "embedding"}
            name = strip_prefix(ctx, str(props.get("name", "")))
            if name:
                stored[name] = props
    out: list[dict[str, Any]] = []
    for row in status:
        if row.get("type") != "VECTOR":
            continue
        name = row["name"]
        raw_label = (row.get("labels") or [None])[0]
        label = strip_prefix(ctx, raw_label) if raw_label else None
        live = None
        if label and label in present:
            live = _one(
                ctx.read(
                    f"MATCH (n:{ctx.label(label)}) WHERE n.embedding IS NOT NULL "
                    "RETURN count(n) AS c"
                ),
                c=0,
            )["c"]
        meta = stored.get(name)
        entry: dict[str, Any] = {
            "index": name,
            "label": label,
            "index_state": row.get("state"),
            "index_dim": row.get("dim"),
            "has_index_meta": meta is not None,
            "live_vectors": live,
        }
        if meta:
            entry.update(
                {
                    "model": meta.get("model"),
                    "dim": meta.get("dim"),
                    "similarity": meta.get("similarity"),
                    "created_at": str(meta.get("created_at")) if meta.get("created_at") else None,
                    "updated_at": str(meta.get("updated_at")) if meta.get("updated_at") else None,
                    "stored_count": meta.get("live", meta.get("chunk_count")),
                }
            )
        out.append(entry)
    return {
        "rows": out,
        "missing_meta": [e["index"] for e in out if not e["has_index_meta"]],
        "note": (
            "live_vectors is counted by this step (`n.embedding IS NOT NULL`); stored_count "
            "is whatever the owning step last wrote onto the IndexMeta node. For "
            "chunk_embedding the stored count includes orphaned chunks — decision 5."
        ),
    }


# -------------------------------------------------------------- what only a report can say


def _read_report(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def resolution_census(reports_dir: Path) -> dict[str, Any]:
    """Precision/recall from `data/reports/resolve.json` plus the duplicate rates.

    Not recomputed here: P/R is measured against `data/eval/resolution_gold.jsonl` and the
    merge ledger, neither of which survives in the graph — a merged identity leaves one
    node behind and no record of the pair that produced it.
    """
    path = reports_dir / "resolve.json"
    report = _read_report(path)
    if report is None:
        return {"available": False, "source": str(path), "note": "resolve has not reported yet"}
    ev = report.get("eval") or {}
    out: dict[str, Any] = {
        "available": True,
        "source": str(path),
        "generated_at": report.get("generated_at"),
        "target": ev.get("target"),
        "kinds": {},
    }
    for kind in ("person", "entity"):
        section = report.get(kind) or {}
        scores = (ev.get(kind) or {}).get("through_tier_3") or {}
        before, after = section.get("before") or {}, section.get("after") or {}
        baseline = section.get("baseline") or {}
        out["kinds"][kind] = {
            "nodes_before": baseline.get("nodes", before.get("nodes")),
            "nodes_after": after.get("nodes"),
            "identities": after.get("identities", baseline.get("identities")),
            "identities_per_node": after.get("identities_per_node"),
            "duplicate_rate_before": before.get("duplicate_rate"),
            "duplicate_rate_after": after.get("duplicate_rate"),
            "resolved_marked": after.get("resolved"),
            "merges_by_tier": section.get("merges_by_tier"),
            "gold_pairs": (ev.get(kind) or {}).get("gold_pairs"),
            "precision": scores.get("precision"),
            "recall": scores.get("recall"),
            "f1": scores.get("f1"),
            "tp": scores.get("tp"),
            "fp": scores.get("fp"),
            "fn": scores.get("fn"),
            "tn": scores.get("tn"),
            "ungraded_merges": scores.get("ungraded_merges"),
        }
    out["note"] = ev.get("note")
    return out


def dangling_census(reports_dir: Path) -> dict[str, Any]:
    """References that named something outside the harvested slice, from `load.json`.

    They leave no trace in the graph on purpose — no edge query creates its far end — so
    the only place this number exists is the report of the step that dropped them.
    """
    path = reports_dir / "load.json"
    report = _read_report(path)
    if report is None:
        return {"available": False, "source": str(path)}
    loaders = report.get("loaders") or {}
    refs = loaders.get("refs") or {}
    links = (loaders.get("workitem_edges") or {}).get("links") or {}
    dangling = report.get("dangling_refs") or refs.get("dangling_refs") or {}
    return {
        "available": True,
        "source": str(path),
        "by_kind": dangling,
        "total": sum(v for v in dangling.values() if isinstance(v, int)),
        "refs_by_kind": refs.get("refs_by_kind") or {},
        "links_declared": links.get("declared"),
        "links_dangling": links.get("dangling"),
        "note": (
            "74% of issue refs point before the 2023-01-01 slice boundary (canon review). "
            "Loading them would mean minting empty WorkItems, so they are counted, not made."
        ),
    }


def canonical_fingerprint(canonical_dir: Path) -> dict[str, Any]:
    """sha256 + record count of every canonical file, so this census is attributable."""
    from brain.graph.report import canonical_inputs

    files = canonical_inputs(canonical_dir)
    return {"dir": str(canonical_dir), "files": files, "file_count": len(files)}


def versions(ctx: GraphContext, ollama_url: str, embed_model: str) -> dict[str, Any]:
    """Neo4j, GDS, APOC, Ollama and the embedding model — each one tolerant of absence."""
    out: dict[str, Any] = {}
    try:
        rows = ctx.read("CALL dbms.components() YIELD name, versions, edition RETURN *")
        kernel = next((r for r in rows if r["name"] == "Neo4j Kernel"), None)
        out["neo4j"] = {
            "version": next(iter((kernel or {}).get("versions") or []), None),
            "edition": (kernel or {}).get("edition"),
            "components": {r["name"]: r["versions"] for r in rows},
        }
    except Exception as exc:  # noqa: BLE001 - a census reports, it does not raise
        out["neo4j"] = {"available": False, "error": str(exc)}
    for name, call in (
        ("gds", "RETURN gds.version() AS v"),
        ("apoc", "RETURN apoc.version() AS v"),
    ):
        try:
            out[name] = {"version": _one(ctx.read(call), v=None)["v"]}
        except Exception as exc:  # noqa: BLE001
            out[name] = {"available": False, "error": str(exc)}
    out["ollama"] = _ollama_versions(ollama_url, embed_model)
    return out


def _ollama_versions(base_url: str, model: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    out: dict[str, Any] = {"url": base, "model": model}
    try:
        with httpx.Client(timeout=5.0) as client:
            out["version"] = client.get(f"{base}/api/version").json().get("version")
            tags = client.get(f"{base}/api/tags").json().get("models", [])
    except Exception as exc:  # noqa: BLE001
        return {**out, "available": False, "error": str(exc)}
    match = next(
        (
            m
            for m in tags
            if m.get("name") == model or str(m.get("name", "")).startswith(model + ":")
        ),
        None,
    )
    out["available"] = True
    if match:
        details = match.get("details") or {}
        out["model_detail"] = {
            "name": match.get("name"),
            "digest": (match.get("digest") or "")[:12],
            "size_bytes": match.get("size"),
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
            "family": details.get("family"),
        }
    else:
        out["model_detail"] = None
        out["warning"] = f"{model} is not pulled on {base}"
    return out
