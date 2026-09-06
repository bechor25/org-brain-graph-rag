"""`brain load` — canonical JSONL into Neo4j, deterministically and idempotently.

Order is the whole design: schema, then every node, then every edge. Edges come last
because no edge query is allowed to create its endpoints (see `cypher.py`), so a
`REFERENCES` written before the documents existed would be silently dropped instead of
counted. Nodes and edges are `MERGE`-on-key throughout, which is what makes the second
run report `nodes_created: 0, relationships_created: 0`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.corpus import load_corpus
from brain.graph.loaders import changes, containers, documents, persons, refs, workitems
from brain.graph.provenance import SyntheticProvenance
from brain.graph.report import (
    PRIMARY_LABELS,
    build_checks,
    canon_expectations,
    canonical_inputs,
    edge_census,
    existing_labels,
    extra_edges,
    link_type_census,
    links_to_by_type,
    links_to_pair_invariant,
    node_census,
    node_total,
    orphan_census,
    references_by_via,
    secondary_labels,
    written_edges,
)
from brain.graph.resolution import Resolution
from brain.graph.schema import apply_schema
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.resolve.ledger import ResolutionLedger

REPORT_NAME = "load.json"

#: Things a reader of `data/reports/load.json` should know that are not counts.
NOTES = [
    "StatusChange.id is sha1(key|field|at|from|to). The step brief specifies "
    "sha1(key|field|at|to); measured on this corpus that formula collapses 26 changelog "
    "rows into 10 ids (removing several fix versions or components in one edit gives rows "
    "that share key, field, timestamp and a null `to`). `id_collisions` under "
    "status_changes is 0 with `from` included and would be 26 without it.",
    "No edge query creates a node. Every ref, link, parent and assignee that names "
    "something outside the harvested slice is counted (dangling_refs, links.dangling, "
    "parent_dangling, assignments_unknown_person) instead of minting an empty node.",
    "A commit's issue refs produce both REFERENCES and RESOLVES, and its KIP refs both "
    "REFERENCES and IMPLEMENTS_KIP: the brief asks for the generic edge from refs and for "
    "the typed edge from commit messages. The typed edge carries the stronger claim.",
    "ASSIGNED_TO is derived from changelog identity keys (`from_id`/`to_id`), never from "
    "display names. Jira spells the current assignee `kirktrue` in the issue and "
    "`JIRAUSER298607` in the changelog, so the current assignee's open interval is taken "
    "from the issue field — see assignments_from_field.",
]


def _merge_report(path: Path, section: dict[str, Any]) -> dict[str, Any]:
    """Keep what a previous run recorded; replace only the keys this run produced."""
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(section)
    return existing


def run_load(
    *,
    client: GraphClient,
    canonical_dir: Path,
    reports_dir: Path,
    prefix: str = "",
    schema_only: bool = False,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    started = time.perf_counter()
    ctx = GraphContext(client, prefix=prefix)
    durations: dict[str, float] = {}

    t0 = time.perf_counter()
    schema = apply_schema(ctx)
    durations["schema"] = round(time.perf_counter() - t0, 2)
    echo(
        f"schema: {schema['constraints']} constraints, {schema['indexes']} indexes "
        f"({schema['constraints_added']} + {schema['indexes_added']} new)"
    )

    report_path = reports_dir / REPORT_NAME
    if schema_only:
        report = _merge_report(
            report_path,
            {
                "step": "load",
                "schema": schema,
                "last_run": {
                    "mode": "schema-only",
                    "at": utc_now_iso(),
                    "duration_s": durations["schema"],
                },
            },
        )
        if write_report:
            write_json_atomic(report_path, report)
        return report, 0

    t0 = time.perf_counter()
    # The resolution ledger is applied to the person records before the corpus is indexed,
    # so every edge resolves to the surviving node. Without it a reload would recreate the
    # identities `brain resolve` merged away — see `brain/graph/resolution.py`.
    ledger = ResolutionLedger.load(canonical_dir)
    corpus = load_corpus(canonical_dir, ledger)
    resolution: Resolution = corpus.resolution or Resolution(canonical_nodes=len(corpus.persons))
    prov = SyntheticProvenance.load(canonical_dir)
    durations["read_canonical"] = round(time.perf_counter() - t0, 2)
    echo(
        f"canonical: {len(corpus.workitems)} work items, {len(corpus.documents)} documents, "
        f"{len(corpus.persons)} persons, {len(corpus.changes)} changes, "
        f"{len(corpus.containers)} containers; synthetic provenance: {prov.status()}; "
        f"resolution: {resolution.status()}"
    )

    stats: dict[str, Any] = {}
    t0 = time.perf_counter()
    stats["persons"] = persons.load_nodes(ctx, corpus, prov, resolution)
    stats["containers"] = containers.load_nodes(ctx, corpus, prov)
    stats["documents"] = documents.load_nodes(ctx, corpus, prov)
    stats["workitems"] = workitems.load_nodes(ctx, corpus, prov)
    stats["changes"] = changes.load_nodes(ctx, corpus, prov)
    durations["nodes"] = round(time.perf_counter() - t0, 2)
    echo(f"nodes: {durations['nodes']}s")

    t0 = time.perf_counter()
    stats["workitem_edges"] = workitems.load_edges(ctx, corpus, prov)
    stats["document_edges"] = documents.load_edges(ctx, corpus)
    stats["change_edges"] = changes.load_edges(ctx, corpus)
    stats["refs"] = refs.load_edges(ctx, corpus)
    durations["edges"] = round(time.perf_counter() - t0, 2)
    echo(f"edges: {durations['edges']}s")

    t0 = time.perf_counter()
    present = existing_labels(ctx)
    nodes = node_census(ctx, secondary_labels(corpus), present)
    by_type, by_pair = edge_census(ctx, present)
    via = references_by_via(ctx, present)
    census = {
        # Counted in the graph, each node once however many of its labels match: summing
        # `nodes_by_label` would count every work item twice (it also wears `Bug`).
        "nodes_total": node_total(ctx, present),
        "edges_total": sum(by_type.values()),
        "nodes_by_label": nodes,
        "edges_by_type": by_type,
        "edges_by_pair": by_pair,
        "links_to_by_type": links_to_by_type(ctx),
        "references_by_via": via,
        "orphans_by_label": orphan_census(ctx, present),
    }
    durations["census"] = round(time.perf_counter() - t0, 2)

    stamped = sum(
        int(section.get("stamped", 0)) for section in stats.values() if isinstance(section, dict)
    )
    invariants = {
        "links_to_same_type_both_directions": links_to_pair_invariant(ctx),
        "extra_edges": extra_edges(by_type, written_edges(stats)),
    }
    canon = canon_expectations(reports_dir)
    checks = build_checks(corpus, nodes, by_type, via, stats, ctx.counters, canon, invariants)
    total = round(time.perf_counter() - started, 2)
    report = _merge_report(
        report_path,
        {
            "step": "load",
            "generated_at": utc_now_iso(),
            "last_run": {
                "mode": "full",
                "canonical_dir": str(canonical_dir),
                "label_prefix": prefix,
                "duration_s": total,
                "per_stage_s": durations,
                "counters": ctx.counters,
            },
            "schema": schema,
            "inputs": canonical_inputs(canonical_dir),
            "census": census,
            "invariants": invariants,
            "loaders": stats,
            "alias_candidates": stats["workitem_edges"]["alias_candidates"],
            "source_link_types": link_type_census(corpus),
            "unknown_link_types": stats["workitem_edges"]["links"]["unknown_link_types"],
            "dangling_refs": stats["refs"]["dangling_refs"],
            "skipped_container_kinds": stats["containers"]["skipped_container_kinds"],
            "synthetic_provenance": prov.report(stamped),
            "resolution": resolution.report(),
            "checks": checks,
            "notes": NOTES,
        },
    )
    if write_report:
        write_json_atomic(report_path, report)

    # The idempotency check only means something on a rerun; a first load legitimately
    # creates everything, so it never decides the exit code.
    failed = [
        c["name"] for c in checks if not c["ok"] and c["name"] != "second_run_creates_nothing"
    ]
    for c in checks:
        mark = "OK  " if c["ok"] else "FAIL"
        echo(f"[{mark}] {c['name']}: expected {c['expected']}, actual {c['actual']}")
    echo(
        f"load: {census['nodes_total']} nodes, {sum(by_type.values())} edges, {total}s "
        f"(created {ctx.counters['nodes_created']} nodes, "
        f"{ctx.counters['relationships_created']} relationships)"
    )
    return report, (1 if failed else 0)


def load_from_settings(
    canonical_dir: Path,
    reports_dir: Path,
    *,
    schema_only: bool = False,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    from brain.config import get_settings

    s = get_settings()
    with GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) as client:
        return run_load(
            client=client,
            canonical_dir=canonical_dir,
            reports_dir=reports_dir,
            schema_only=schema_only,
            echo=echo,
        )


def wipe(ctx: GraphContext) -> None:
    """Delete everything in this context's label namespace. Tests only — the scratch
    namespace `make smoke` uses holds a few dozen nodes, so one transaction is enough."""
    for label in PRIMARY_LABELS:
        ctx.client.write(f"MATCH (n:{ctx.label(label)}) DETACH DELETE n")
