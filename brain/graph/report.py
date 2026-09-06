"""`data/reports/load.json` — what is in the graph, and what did not make it.

The census is read back from Neo4j rather than counted from the rows we sent, because the
question the report answers is "what does the graph contain", not "what did we intend".
Every count is label-scoped and explicitly projected: `GraphClient.read()` returns
`record.data()`, which drops labels, so a bare `RETURN n` would answer nothing.

`checks` evaluates the step brief's acceptance criteria against those numbers. A criterion
that fails stays in the file with `ok: false`; it is never relaxed to make the file green.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from brain.graph.context import GraphContext
from brain.graph.corpus import Corpus
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
    ("HAS_CHANGE", "WorkItem", "StatusChange"),
    ("COMMENTED", "Person", "WorkItem"),
    ("AUTHORED", "Person", "Commit"),
    ("AUTHORED", "Person", "PullRequest"),
    ("AUTHORED", "Person", "Document"),
    ("CHILD_OF", "Document", "Document"),
    ("VARIANT_OF", "Document", "Document"),
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


def node_census(ctx: GraphContext, secondary: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in (*PRIMARY_LABELS, *sorted(secondary)):
        rows = ctx.read(f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS c")
        counts[label] = rows[0]["c"] if rows else 0
    return {k: v for k, v in counts.items() if v or k in PRIMARY_LABELS}


def edge_census(ctx: GraphContext) -> tuple[dict[str, int], dict[str, int]]:
    by_type: Counter = Counter()
    by_pair: dict[str, int] = {}
    for rel, src, dst in REL_SPECS:
        rows = ctx.read(
            f"MATCH (:{ctx.label(src)})-[r:`{rel}`]->(:{ctx.label(dst)}) RETURN count(r) AS c"
        )
        c = rows[0]["c"] if rows else 0
        by_type[rel] += c
        if c:
            by_pair[f"{src}-{rel}->{dst}"] = c
    return dict(sorted(by_type.items())), by_pair


def orphan_census(ctx: GraphContext) -> dict[str, int]:
    out: dict[str, int] = {}
    for label in PRIMARY_LABELS:
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


def references_by_via(ctx: GraphContext) -> dict[str, int]:
    out: Counter = Counter()
    for rel, src, dst in REL_SPECS:
        if rel != "REFERENCES":
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


def build_checks(
    corpus: Corpus,
    nodes: dict[str, int],
    edges: dict[str, int],
    via: dict[str, int],
    stats: dict[str, Any],
    counters: dict[str, int],
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
    return checks


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
