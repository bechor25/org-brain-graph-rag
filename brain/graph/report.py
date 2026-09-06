"""`data/reports/load.json` — what is in the graph, and what did not make it.

The census is read back from Neo4j rather than counted from the rows we sent, because the
question the report answers is "what does the graph contain", not "what did we intend".
Every count is label-scoped and explicitly projected: `GraphClient.read()` returns
`record.data()`, which drops labels, so a bare `RETURN n` would answer nothing.

`checks` evaluates the step brief's acceptance criteria against those numbers. A criterion
that fails stays in the file with `ok: false`; it is never relaxed to make the file green.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from brain.graph.context import GraphContext
from brain.graph.corpus import CANONICAL_FILES, Corpus
from brain.graph.mapping import dedupe_link_edges, link_edge, workitem_label

#: Every (relationship, source label, target label) this step can write. Counting through
#: an explicit pair keeps the census inside the label namespace of this run and doubles as
#: the readable statement of the schema `brain load` produces.
REL_SPECS: tuple[tuple[str, str, str], ...] = (
    ("REPORTED_BY", "WorkItem", "Person"),
    ("ASSIGNED_TO", "WorkItem", "Person"),
    ("IN_COMPONENT", "WorkItem", "Component"),
    ("FIX_VERSION", "WorkItem", "Version"),
    ("AFFECTS_VERSION", "WorkItem", "Version"),
    ("PARENT_OF", "WorkItem", "WorkItem"),
    ("LINKS_TO", "WorkItem", "WorkItem"),
    ("TESTS", "WorkItem", "WorkItem"),
    ("EXECUTED_IN", "WorkItem", "WorkItem"),
    ("IN_PLAN", "WorkItem", "WorkItem"),
    ("HAS_RUN", "WorkItem", "WorkItem"),
    ("HAS_CHANGE", "WorkItem", "StatusChange"),
    ("COMMENTED", "Person", "WorkItem"),
    ("AUTHORED", "Person", "Commit"),
    ("AUTHORED", "Person", "PullRequest"),
    ("AUTHORED", "Person", "Document"),
    ("CHILD_OF", "Document", "Document"),
    ("VARIANT_OF", "Document", "Document"),
    ("IN_SPACE", "Document", "Space"),
    ("TOUCHES", "Commit", "File"),
    ("HAS_COMMIT", "PullRequest", "Commit"),
    ("RESOLVES", "Commit", "WorkItem"),
    ("IMPLEMENTS_KIP", "Commit", "Document"),
    ("REFERENCES", "WorkItem", "WorkItem"),
    ("REFERENCES", "WorkItem", "Document"),
    ("REFERENCES", "WorkItem", "PullRequest"),
    ("REFERENCES", "Document", "WorkItem"),
    ("REFERENCES", "Document", "Document"),
    ("REFERENCES", "Document", "PullRequest"),
    ("REFERENCES", "Commit", "WorkItem"),
    ("REFERENCES", "Commit", "Document"),
    ("REFERENCES", "Commit", "PullRequest"),
    ("REFERENCES", "PullRequest", "WorkItem"),
    ("REFERENCES", "PullRequest", "Document"),
    ("REFERENCES", "PullRequest", "PullRequest"),
    ("MENTIONS_PERSON", "WorkItem", "Person"),
    ("MENTIONS_PERSON", "Document", "Person"),
    ("MENTIONS_PERSON", "Commit", "Person"),
    ("MENTIONS_PERSON", "PullRequest", "Person"),
)

PRIMARY_LABELS: tuple[str, ...] = (
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


def existing_labels(ctx: GraphContext) -> set[str]:
    """Labels the database has actually seen, without the namespace prefix.

    Querying a label no node has ever carried is legal but makes the server emit an
    `01N50 label does not exist` notification per query, which buries the report's own
    output in warnings about labels that are simply empty until the synthetic layer lands.
    """
    rows = ctx.read("CALL db.labels() YIELD label RETURN collect(label) AS labels")
    present = set(rows[0]["labels"]) if rows else set()
    return {label[len(ctx.prefix) :] for label in present if label.startswith(ctx.prefix)}


def node_census(ctx: GraphContext, secondary: list[str], present: set[str]) -> dict[str, int]:
    """Raw per-label counts — what `MATCH (n:Label)` actually returns.

    Secondary work item labels (`Bug`, `TestPlan`, …) are listed beside the primary ones,
    so summing this map double-counts every work item; :func:`node_total` counts each node
    once.
    """
    counts: dict[str, int] = {}
    for label in (*PRIMARY_LABELS, *sorted(secondary)):
        if label not in present:
            counts[label] = 0
            continue
        rows = ctx.read(f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS c")
        counts[label] = rows[0]["c"] if rows else 0
    return {k: v for k, v in counts.items() if v or k in PRIMARY_LABELS}


def node_total(ctx: GraphContext, present: set[str]) -> int:
    """Nodes this step owns, each counted once however many of its labels match."""
    labels = [label for label in PRIMARY_LABELS if label in present]
    if not labels:
        return 0
    where = " OR ".join(f"n:{ctx.label(label)}" for label in labels)
    rows = ctx.read(f"MATCH (n) WHERE {where} RETURN count(n) AS c")
    return rows[0]["c"] if rows else 0


def edge_census(ctx: GraphContext, present: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    by_type: Counter = Counter()
    by_pair: dict[str, int] = {}
    for rel, src, dst in REL_SPECS:
        by_type.setdefault(rel, 0)
        if src not in present or dst not in present:
            continue
        rows = ctx.read(
            f"MATCH (:{ctx.label(src)})-[r:`{rel}`]->(:{ctx.label(dst)}) RETURN count(r) AS c"
        )
        c = rows[0]["c"] if rows else 0
        by_type[rel] += c
        if c:
            by_pair[f"{src}-{rel}->{dst}"] = c
    return dict(sorted(by_type.items())), by_pair


def orphan_census(ctx: GraphContext, present: set[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for label in PRIMARY_LABELS:
        if label not in present:
            continue
        rows = ctx.read(f"MATCH (n:{ctx.label(label)}) WHERE NOT (n)--() RETURN count(n) AS c")
        c = rows[0]["c"] if rows else 0
        if c:
            out[label] = c
    return out


def links_to_by_type(ctx: GraphContext) -> dict[str, int]:
    rows = ctx.read(
        f"MATCH (:{ctx.label('WorkItem')})-[r:`LINKS_TO`]->(:{ctx.label('WorkItem')}) "
        "RETURN r.type AS type, count(r) AS c ORDER BY c DESC"
    )
    return {r["type"]: r["c"] for r in rows}


def references_by_via(ctx: GraphContext, present: set[str]) -> dict[str, int]:
    out: Counter = Counter()
    for rel, src, dst in REL_SPECS:
        if rel != "REFERENCES" or src not in present or dst not in present:
            continue
        rows = ctx.read(
            f"MATCH (:{ctx.label(src)})-[r:`REFERENCES`]->(:{ctx.label(dst)}) "
            "RETURN r.via AS via, count(r) AS c"
        )
        for r in rows:
            out[r["via"]] += r["c"]
    return dict(out)


def expected_links(corpus: Corpus) -> int:
    """Unique `LINKS_TO` pairs the canonical files ask for, computed independently."""
    edges = []
    for w in corpus.workitems:
        for link in w.links:
            e = link_edge(w.key, link)
            if e is None or e.rel != "LINKS_TO":
                continue
            if e.src in corpus.workitem_keys and e.dst in corpus.workitem_keys:
                edges.append(e)
    return len(dedupe_link_edges(edges))


def expected_counts(corpus: Corpus) -> dict[str, int]:
    return {
        "WorkItem": len(corpus.workitems),
        "Document": len(corpus.documents),
        "Person": len(corpus.persons),
        "Commit": len(corpus.commits),
        "PullRequest": len(corpus.pull_requests),
        "Component": len(corpus.container_names.get("Component", set())),
        "Version": len(corpus.container_names.get("Version", set())),
        "File": len({p for c in corpus.changes for p in c.files}),
    }


def secondary_labels(corpus: Corpus) -> list[str]:
    labels = {workitem_label(w.type, w.source) for w in corpus.workitems}
    return sorted(x for x in labels if x)


def canonical_inputs(canonical_dir: Path) -> dict[str, Any]:
    """sha256 and record count of every canonical file this run read.

    The census answers "what is in the graph"; this answers "from what". Without it a
    report is unattributable — two runs with the same counts could have read different
    files, which is exactly what happens while the synthetic layer is being merged.
    """
    out: dict[str, Any] = {}
    for name in (*CANONICAL_FILES, "synthetic_merged", "synthetic_truth"):
        for suffix in (".jsonl", ".json"):
            path = canonical_dir / f"{name}{suffix}"
            if not path.is_file():
                continue
            data = path.read_bytes()
            out[path.name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "records": data.count(b"\n") if suffix == ".jsonl" else None,
            }
    return out


def canon_expectations(reports_dir: Path) -> dict[str, Any]:
    """What `data/reports/canon.json` says the previous step produced.

    Reading the upstream report rather than recomputing means the two steps have to agree
    about the same corpus: a canon rerun that changed the numbers shows up here as a
    failed check instead of as a graph nobody compared to anything.
    """
    path = reports_dir / "canon.json"
    if not path.is_file():
        return {}
    try:
        canon = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    jira = ((canon.get("source_stats") or {}).get("jira")) or {}
    refs = canon.get("refs") or {}
    return {
        "report": str(path),
        "formal_links": jira.get("formal_links"),
        "link_types": jira.get("link_types") or {},
        "refs_by_kind": refs.get("totals_by_kind") or {},
        "counts_by_type": (canon.get("counts") or {}).get("by_type") or {},
    }


def build_checks(
    corpus: Corpus,
    nodes: dict[str, int],
    edges: dict[str, int],
    via: dict[str, int],
    stats: dict[str, Any],
    counters: dict[str, int],
    canon: dict[str, Any] | None = None,
    invariants: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    expected = expected_counts(corpus)
    checks: list[dict[str, Any]] = [
        {
            "name": f"{label.lower()}_count_matches_canonical",
            "expected": want,
            "actual": nodes.get(label, 0),
            "ok": nodes.get(label, 0) == want,
        }
        for label, want in expected.items()
    ]
    want_links = expected_links(corpus)
    checks.append(
        {
            "name": "links_to_equals_unique_pairs",
            "expected": want_links,
            "actual": edges.get("LINKS_TO", 0),
            "ok": edges.get("LINKS_TO", 0) == want_links,
            "note": "reciprocal Jira declarations collapse to one edge",
        }
    )
    dangling = stats.get("refs", {}).get("dangling_refs", {})
    canon_text = _canon_text_refs(corpus)
    dangling_text = canon_text["dangling_text"]
    want_text = canon_text["via_text_referenceable"] - dangling_text
    checks.append(
        {
            "name": "references_via_text_at_least_canon_minus_dangling",
            "expected": want_text,
            "actual": via.get("text", 0),
            "ok": via.get("text", 0) >= want_text,
            "note": (
                "canon counted "
                f"{canon_text['via_text_total']} text refs; "
                f"{canon_text['via_text_referenceable']} of them point at a node kind, "
                f"{dangling_text} of those at a target outside the corpus. "
                f"dangling_refs by kind: {dangling}"
            ),
        }
    )
    checks.append(
        {
            "name": "second_run_creates_nothing",
            "expected": 0,
            "actual": counters["nodes_created"] + counters["relationships_created"],
            "ok": counters["nodes_created"] + counters["relationships_created"] == 0,
            "note": "only meaningful on a rerun over an already-loaded graph",
        }
    )

    canon = canon or {}
    if canon.get("formal_links") is not None:
        declared = stats.get("workitem_edges", {}).get("links", {}).get("declared")
        checks.append(
            {
                "name": "formal_links_match_canon_report",
                "expected": canon["formal_links"],
                "actual": declared,
                "ok": declared == canon["formal_links"],
                "note": f"from {canon['report']} -> source_stats.jira.formal_links",
            }
        )
    if canon.get("refs_by_kind"):
        seen = stats.get("refs", {}).get("refs_by_kind", {})
        want = {k: v for k, v in canon["refs_by_kind"].items()}
        # canon counts the real corpus only; the synthetic layer adds refs on top, so the
        # graph may legitimately see more — never fewer.
        missing = {k: v for k, v in want.items() if seen.get(k, 0) < v}
        checks.append(
            {
                "name": "refs_by_kind_at_least_canon_report",
                "expected": want,
                "actual": seen,
                "ok": not missing,
                "note": f"from {canon['report']} -> refs.totals_by_kind; short by {missing}",
            }
        )
    if canon.get("counts_by_type"):
        want_wi = canon["counts_by_type"].get("workitems")
        checks.append(
            {
                "name": "workitems_at_least_canon_report",
                "expected": want_wi,
                "actual": nodes.get("WorkItem", 0),
                "ok": want_wi is None or nodes.get("WorkItem", 0) >= want_wi,
                "note": "canon.json counts the corpus load read; a later canon run may add",
            }
        )

    invariants = invariants or {}
    if "links_to_same_type_both_directions" in invariants:
        found = invariants["links_to_same_type_both_directions"]
        checks.append(
            {
                "name": "no_pair_carries_the_same_links_to_type_twice",
                "expected": 0,
                "actual": found,
                "ok": found == 0,
                "note": (
                    "graph-side invariant: a reciprocal Jira declaration would show up as "
                    "A-[:LINKS_TO{type}]->B and B-[:LINKS_TO{same type}]->A"
                ),
            }
        )
    extra = invariants.get("extra_edges") or {}
    checks.append(
        {
            "name": "no_edges_beyond_what_canonical_asks_for",
            "expected": {},
            "actual": extra,
            "ok": not extra,
            "note": (
                "graph edge counts vs the rows this run wrote. `brain load` never prunes: "
                "an edge whose canonical record disappeared stays until something deletes "
                "it, and shows up here rather than being silently tolerated."
            ),
        }
    )
    return checks


def extra_edges(graph: dict[str, int], written: dict[str, int]) -> dict[str, dict[str, int]]:
    """Edge types where the graph holds more than this run wrote."""
    out: dict[str, dict[str, int]] = {}
    for rel, count in graph.items():
        want = written.get(rel, 0)
        if count > want:
            out[rel] = {"in_graph": count, "written_this_run": want}
    return out


def written_edges(stats: dict[str, Any]) -> dict[str, int]:
    """Rows every loader actually sent, summed per relationship type."""
    out: Counter = Counter()
    for section in stats.values():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if key == "edges" and isinstance(value, dict):
                for rel, n in value.items():
                    out[rel] += int(n)
            elif key.isupper() and isinstance(value, int):
                out[key] += value
    return dict(out)


def links_to_pair_invariant(ctx: GraphContext) -> int:
    """Pairs holding the same `LINKS_TO.type` in both directions. Must be 0."""
    rows = ctx.read(
        f"MATCH (a:{ctx.label('WorkItem')})-[r:`LINKS_TO`]->(b:{ctx.label('WorkItem')}) "
        f"MATCH (b)-[r2:`LINKS_TO`]->(a) WHERE r2.type = r.type RETURN count(r) AS c"
    )
    return rows[0]["c"] if rows else 0


def _canon_text_refs(corpus: Corpus) -> dict[str, int]:
    """Split the canonical refs the way the acceptance criterion reads them."""
    total = 0
    referenceable = 0
    dangling = 0
    from brain.graph.loaders.refs import iter_ref_records, resolve_target

    for _source, _label, _key, refs in iter_ref_records(corpus):
        for ref in refs:
            if ref.via != "text":
                continue
            total += 1
            if ref.kind not in ("issue", "kip", "pr"):
                continue
            referenceable += 1
            if resolve_target(corpus, ref) is None:
                dangling += 1
    return {
        "via_text_total": total,
        "via_text_referenceable": referenceable,
        "dangling_text": dangling,
    }


def link_type_census(corpus: Corpus) -> dict[str, int]:
    """Every link type name the sources used, before the vocabulary mapped it."""
    raw: Counter = Counter()
    for w in corpus.workitems:
        raw.update(link.type for link in w.links)
    return dict(raw.most_common())
