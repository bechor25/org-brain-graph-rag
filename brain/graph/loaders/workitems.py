"""`WorkItem` — the node every other structured edge hangs off, and its history.

Two decisions carry the weight here.

*Time is a node.* Every kept changelog row becomes a `StatusChange` event with its own
identity, so "what was the status on 2024-03-01" is a traversal instead of a property
that the next edit overwrites. Of the 30,187 changelog rows in this corpus only 7,607
are kept — `RemoteIssueLink` alone is 17,718 rows of "somebody edited a link".

*Assignment is an interval.* `ASSIGNED_TO{valid_from, valid_to}` is derived from the
assignee transitions rather than from `fields.assignee`, so the graph can answer "who
owned this in March" and not only "who owns it now".
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from brain.canon.models import WorkItem
from brain.graph.context import GraphContext
from brain.graph.corpus import Corpus
from brain.graph.cypher import node_merge
from brain.graph.loaders import emit
from brain.graph.mapping import (
    IGNORED_CHANGELOG_FIELDS,
    STATUS_CHANGE_FIELDS,
    assignment_intervals,
    dedupe_link_edges,
    is_known_link_type,
    link_edge,
    statuschange_id,
    workitem_label,
)
from brain.graph.provenance import SyntheticProvenance

LABEL = "WorkItem"
KEY = "key"
NODE: tuple[str, str] = (LABEL, KEY)
PERSON: tuple[str, str] = ("Person", "id")
STATUS_CHANGE: tuple[str, str] = ("StatusChange", "id")


def node_rows(
    workitems: list[WorkItem], prov: SyntheticProvenance
) -> tuple[dict[str | None, list[dict[str, Any]]], Counter, Counter]:
    """Rows grouped by secondary label, the type census, and the types with no label."""
    grouped: dict[str | None, list[dict[str, Any]]] = {}
    types: Counter = Counter()
    unknown: Counter = Counter()
    for w in workitems:
        label = workitem_label(w.type, w.source)
        types[w.type] += 1
        if label is None:
            unknown[w.type] += 1
        props: dict[str, Any] = {
            "id": w.id,
            "key": w.key,
            "source": w.source,
            "type": w.type,
            "title": w.title,
            "description": w.description,
            "status": w.status,
            "resolution": w.resolution,
            "resolved_at": w.resolved_at,
            "priority": w.priority,
            "created": w.created,
            "updated": w.updated,
            "labels": w.labels,
            "synthetic": w.synthetic,
            "raw_url": w.raw_url,
        }
        props.update(prov.props(w.key))
        grouped.setdefault(label, []).append({"key": w.key, "props": props})
    return grouped, types, unknown


def load_nodes(ctx: GraphContext, corpus: Corpus, prov: SyntheticProvenance) -> dict[str, Any]:
    grouped, types, unknown = node_rows(corpus.workitems, prov)
    stamped = 0
    by_label: dict[str, int] = {}
    for label, rows in sorted(grouped.items(), key=lambda kv: kv[0] or ""):
        extra = (label,) if label else ()
        ctx.write_rows(node_merge(ctx, LABEL, KEY, extra_labels=extra), rows)
        by_label[label or "(none)"] = len(rows)
        stamped += sum(1 for r in rows if r["props"].get("batch_id"))
    return {
        "workitems": sum(by_label.values()),
        "by_secondary_label": by_label,
        "types": dict(types.most_common()),
        "unknown_types": dict(unknown),
        "stamped": stamped,
    }


def _status_change_rows(
    corpus: Corpus, prov: SyntheticProvenance
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    kept: Counter = Counter()
    skipped: Counter = Counter()
    collisions = 0
    for w in corpus.workitems:
        for entry in w.changelog:
            if entry.field not in STATUS_CHANGE_FIELDS:
                if entry.field not in IGNORED_CHANGELOG_FIELDS:
                    skipped[entry.field] += 1
                continue
            kept[entry.field] += 1
            sid = statuschange_id(w.key, entry)
            if sid in nodes:
                collisions += 1
                continue
            props: dict[str, Any] = {
                "id": sid,
                "item_key": w.key,
                "field": entry.field,
                "from": entry.from_,
                "to": entry.to,
                "from_id": entry.from_id,
                "to_id": entry.to_id,
                "at": entry.at,
                "by": entry.by,
                "synthetic": w.synthetic,
            }
            props.update(prov.props(w.key))
            nodes[sid] = {"key": sid, "props": props}
            edges.append({"src": w.key, "dst": sid})
    stats = {
        "kept_by_field": dict(kept.most_common()),
        "kept_rows": sum(kept.values()),
        "ignored_fields": {
            f: sum(1 for w in corpus.workitems for e in w.changelog if e.field == f)
            for f in sorted(IGNORED_CHANGELOG_FIELDS)
        },
        "skipped_fields": dict(skipped.most_common()),
        "id_collisions": collisions,
    }
    return list(nodes.values()), edges, stats


def _link_rows(corpus: Corpus) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    edges = []
    unknown: Counter = Counter()
    dangling = 0
    self_links = 0
    declared = 0
    for w in corpus.workitems:
        for link in w.links:
            declared += 1
            if not is_known_link_type(link.type):
                unknown[link.type] += 1
            edge = link_edge(w.key, link)
            if edge is None:
                self_links += 1
                continue
            if edge.src not in corpus.workitem_keys or edge.dst not in corpus.workitem_keys:
                dangling += 1
                continue
            edges.append(edge)

    deduped = dedupe_link_edges(edges)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for e in deduped:
        row: dict[str, Any] = {"src": e.src, "dst": e.dst}
        if e.rel == "LINKS_TO":
            row["type"] = e.type
            row["props"] = {"raw_type": e.raw_type}
        grouped.setdefault(e.rel, []).append(row)
    stats = {
        "declared": declared,
        "self_links": self_links,
        "dangling": dangling,
        "resolvable_before_dedupe": len(edges),
        "unknown_link_types": dict(unknown.most_common()),
    }
    return grouped, stats


def load_edges(ctx: GraphContext, corpus: Corpus, prov: SyntheticProvenance) -> dict[str, Any]:
    reported: list[dict[str, Any]] = []
    assigned: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    fix_versions: list[dict[str, Any]] = []
    affects: list[dict[str, Any]] = []
    parents: list[dict[str, Any]] = []
    commented: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "reporters_unknown": 0,
        "assignments_derived": 0,
        "assignments_unknown_person": 0,
        "assignments_from_field": 0,
        "parent_dangling": 0,
        "comments": 0,
        "comment_authors_unknown": 0,
        "comments_without_timestamp": 0,
        "components_unknown": 0,
        "versions_unknown": 0,
    }
    known_components = corpus.container_names.get("Component", set())
    known_versions = corpus.container_names.get("Version", set())
    seen_comments: set[tuple[str, str, Any]] = set()

    for w in corpus.workitems:
        reporter = corpus.person(w.source, w.reporter)
        if w.reporter and not reporter:
            stats["reporters_unknown"] += 1
        elif reporter:
            reported.append({"src": w.key, "dst": reporter})

        for a in assignment_intervals(w):
            stats["assignments_derived"] += 1
            person = corpus.person(w.source, a.person_key)
            if not person:
                stats["assignments_unknown_person"] += 1
                continue
            if a.from_field:
                stats["assignments_from_field"] += 1
            assigned.append(
                {
                    "src": w.key,
                    "dst": person,
                    "valid_from": a.valid_from,
                    "props": {"valid_to": a.valid_to},
                }
            )

        for name in dict.fromkeys(w.components):
            if name in known_components:
                components.append({"src": w.key, "dst": name})
            else:
                stats["components_unknown"] += 1
        for name in dict.fromkeys(w.fix_versions):
            if name in known_versions:
                fix_versions.append({"src": w.key, "dst": name})
            else:
                stats["versions_unknown"] += 1
        for name in dict.fromkeys(w.affects_versions):
            if name in known_versions:
                affects.append({"src": w.key, "dst": name})
            else:
                stats["versions_unknown"] += 1

        if w.parent:
            if w.parent in corpus.workitem_keys and w.parent != w.key:
                parents.append({"src": w.parent, "dst": w.key})
            else:
                stats["parent_dangling"] += 1

        for c in w.comments:
            stats["comments"] += 1
            author = corpus.person(w.source, c.author)
            if not author:
                stats["comment_authors_unknown"] += 1
                continue
            if c.at is None:
                # No timestamp, no merge key: one edge for every undated comment this
                # person left on this item, rather than a new edge on every run.
                stats["comments_without_timestamp"] += 1
                continue
            k = (author, w.key, c.at)
            if k in seen_comments:
                continue
            seen_comments.add(k)
            commented.append({"src": author, "dst": w.key, "at": c.at})

    link_groups, link_stats = _link_rows(corpus)
    sc_nodes, sc_edges, sc_stats = _status_change_rows(corpus, prov)
    ctx.write_rows(node_merge(ctx, "StatusChange", "id"), sc_nodes)

    edges: dict[str, Any] = {
        "REPORTED_BY": emit(ctx, NODE, "REPORTED_BY", PERSON, reported),
        "ASSIGNED_TO": emit(
            ctx, NODE, "ASSIGNED_TO", PERSON, assigned, key_props=("valid_from",), set_props=True
        ),
        "IN_COMPONENT": emit(ctx, NODE, "IN_COMPONENT", ("Component", "name"), components),
        "FIX_VERSION": emit(ctx, NODE, "FIX_VERSION", ("Version", "name"), fix_versions),
        "AFFECTS_VERSION": emit(ctx, NODE, "AFFECTS_VERSION", ("Version", "name"), affects),
        "PARENT_OF": emit(ctx, NODE, "PARENT_OF", NODE, parents),
        "COMMENTED": emit(ctx, PERSON, "COMMENTED", NODE, commented, key_props=("at",)),
        "HAS_CHANGE": emit(ctx, NODE, "HAS_CHANGE", STATUS_CHANGE, sc_edges),
    }
    for rel, rows in sorted(link_groups.items()):
        key_props = ("type",) if rel == "LINKS_TO" else ()
        edges[rel] = edges.get(rel, 0) + emit(
            ctx, NODE, rel, NODE, rows, key_props=key_props, set_props=rel == "LINKS_TO"
        )

    return {
        **stats,
        "links": link_stats,
        "status_changes": {**sc_stats, "nodes": len(sc_nodes)},
        "edges": edges,
    }
