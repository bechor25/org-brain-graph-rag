"""`brain communities build` — project, run Leiden, write the partition, report it.

The step is idempotent by construction: Leiden with a fixed seed at concurrency 1 over the
same projection gives the same partition, and the partition is written with `MERGE` + a
sweep of what this run did not produce. Running it twice therefore leaves the same
`member_hash` on the same nodes and re-summarises nothing.

`--dry-run` does everything except touch the database: it projects (an in-memory catalog
graph, built through a `READ`-mode session), runs Leiden, reports the community counts, the
size distribution and the modularity, and drops the projection. That is what makes it safe
to measure the real graph while another step still has merges to apply to it.

The projection is dropped in a `finally`. Always (brief 09, note 2): an in-memory graph
that outlives its run is heap somebody else needs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from brain.community import graph as community_graph
from brain.community import projection as proj
from brain.community.partition import (
    DEFAULT_LEVELS,
    DEFAULT_MIN_SIZE,
    FINE,
    Community,
    Member,
    NodeAssignment,
    PartitionError,
    kind_placement,
    label_placement,
    level_summary,
    partition,
    size_distribution,
)
from brain.community.report import write_section
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso

#: Two levels whose community counts differ by less than this are not a hierarchy — the
#: coarse level says almost exactly what the fine level already said. Reported, never
#: applied: which levels to summarise is a planner decision, not a silent correction.
DEGENERATE_LEVEL_RATIO = 0.10


class BuildError(RuntimeError):
    """The communities cannot be computed, or cannot be written correctly."""


def assignments(
    rows: list[dict[str, Any]],
) -> tuple[list[NodeAssignment], dict[str, int], list[str]]:
    """Leiden rows -> `NodeAssignment`s, the GDS node id per member, and what was dropped.

    A row with no label or no key is a node the projection holds and this code cannot name
    — it should be impossible, because the projection matches on those labels — so it is
    counted and reported rather than silently skipped.
    """
    out: list[NodeAssignment] = []
    node_ids: dict[str, int] = {}
    dropped: list[str] = []
    for row in rows:
        label, key = row.get("label"), row.get("key")
        if not label or not key:
            dropped.append(f"nodeId={row.get('nodeId')} label={label!r} key={key!r}")
            continue
        member = Member(label=label, key=str(key))
        out.append(
            NodeAssignment(
                member=member,
                community_ids=tuple(int(c) for c in row["intermediateCommunityIds"]),
            )
        )
        node_ids[member.member_key] = int(row["nodeId"])
    return out, node_ids, dropped


def membership_rows(
    communities: list[Community], degrees: dict[str, float]
) -> dict[str, list[dict[str, Any]]]:
    """`IN_COMMUNITY` rows grouped by member label, so each label gets one keyed query."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for community in communities:
        for member in community.members:
            grouped.setdefault(member.label, []).append(
                {
                    "member_key": member.key,
                    "community": community.id,
                    "level": community.level,
                    "degree": degrees.get(member.member_key, 0.0),
                }
            )
    return grouped


#: Read off a stored report row rather than written back onto a node.
NOT_A_PROPERTY = frozenset({"embedding", "embed_hash", "previous_id", "member_hash", "level"})


def carried_rows(
    communities: list[Community],
    own: dict[tuple[str, int], dict[str, Any]],
    other: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Reports whose community still exists as the same set of members, re-pointed at it.

    A community gets back *its own* report: the one written for this member set at this
    level. Only if it has none does it borrow another level's report for the same members,
    and then `copied_from` says so — the same rule `brain communities batches` applies when
    it copies a report instead of paying an agent to write the text twice.

    The distinction is the whole point. 22 member sets on the live graph carry a report at
    both levels, and a carry keyed on the member set alone gave all 44 communities one of
    the two texts, stamped with the batch id, model and extraction time of a batch that had
    answered about the other one. Nothing failed; the provenance simply stopped being true.
    """
    rows: list[dict[str, Any]] = []
    carried: list[str] = []
    for community in communities:
        if not community.summarized:
            continue
        source: str | None = None
        stored = own.get((community.member_hash, community.level))
        if stored is None:
            stored = other.get(community.member_hash)
            source = str((stored or {}).get("previous_id") or "")
            if stored is None or source == community.id:
                continue
        props = {k: v for k, v in stored.items() if k not in NOT_A_PROPERTY and v is not None}
        if source:
            props[community_graph.COPIED_FROM] = source
        rows.append(
            {
                "id": community.id,
                "props": props,
                "embedding": stored.get("embedding"),
                "embed_hash": stored.get("embed_hash"),
            }
        )
        carried.append(community.id)
    return rows, carried


def level_warnings(levels: dict[str, Any], gds_levels: list[int]) -> list[str]:
    """Say it out loud when the two levels chosen are not really two levels."""
    out: list[str] = []
    counts = [levels[k]["communities"] for k in sorted(levels)]
    if len(counts) < 2:
        out.append(
            f"Leiden ran {len(gds_levels)} usable level(s); there is no coarse level to "
            "summarise beside the fine one"
        )
        return out
    fine, coarse = counts[0], counts[-1]
    if fine and abs(fine - coarse) / fine < DEGENERATE_LEVEL_RATIO:
        out.append(
            f"the hierarchy has converged: level 0 has {fine} communities and level "
            f"{len(counts) - 1} has {coarse} ({abs(fine - coarse) / fine:.1%} apart). The coarse "
            "level adds almost no structure — consider `--gamma` below 1.0, or explicit "
            "`--level-indices`, before spending agent time on both levels."
        )
    summarized = sum(levels[k]["summarized"] for k in levels)
    if not 100 <= summarized <= 200:
        out.append(
            f"{summarized} communities would be summarised; brief 09 decision 2 targets "
            "100-200. Tune `--min-size`, `--gamma` or `--levels` rather than accepting it "
            "by default."
        )
    return out


def run_build(
    *,
    ctx: GraphContext,
    reports_dir,
    embed_dim: int,
    levels: int = DEFAULT_LEVELS,
    level_indices: list[int] | None = None,
    min_size: int = DEFAULT_MIN_SIZE,
    seed: int = proj.DEFAULT_SEED,
    max_levels: int = proj.DEFAULT_MAX_LEVELS,
    gamma: float = proj.DEFAULT_GAMMA,
    include_synthetic: bool = False,
    dry_run: bool = False,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Project -> Leiden -> (write) -> report. The projection is always dropped."""
    started = time.perf_counter()
    built_at = utc_now_iso()
    config = proj.leiden_config(seed=seed, max_levels=max_levels, gamma=gamma)

    try:
        projected = proj.project(ctx, include_synthetic=include_synthetic)
        branch = proj.branch_counts(ctx, include_synthetic=include_synthetic)
        raw_branch = proj.raw_branch_counts(ctx, include_synthetic=include_synthetic)
        unprojected = proj.unprojected_edge_counts(ctx)
        stats = proj.leiden_stats(ctx, config)
        rows, stream_ms = proj.leiden_stream(ctx, config)
        degrees_by_id = proj.degree_stream(ctx)
    finally:
        proj.drop(ctx)

    rows_out, node_ids, dropped = assignments(rows)
    if not rows_out:
        raise BuildError("Leiden returned no nameable nodes; the projection has no keys")
    try:
        communities, gds_levels = partition(
            rows_out, levels=levels, min_size=min_size, indices=level_indices
        )
    except PartitionError as exc:
        raise BuildError(str(exc)) from exc

    degrees = {key: degrees_by_id.get(nid, 0.0) for key, nid in node_ids.items()}
    per_level = level_summary(communities)
    entity_keys = sorted({m.key for c in communities for m in c.members if m.label == "Entity"})
    kinds = proj.entity_kinds(ctx, entity_keys)

    written: dict[str, Any] = {"applied": False}
    schema_stats: dict[str, Any] = {}
    carried: list[str] = []
    if not dry_run:
        schema_stats = community_graph.apply_community_schema(ctx, embed_dim)
        previous = community_graph.read_reports(ctx)
        rows_to_write = [c.row() for c in communities]
        node_rows = [{"id": r["id"], "props": r} for r in rows_to_write]
        written = community_graph.replace_communities(
            ctx,
            node_rows,
            membership_rows(communities, degrees),
            built_at=built_at,
        )
        carry, carried = carried_rows(
            communities,
            community_graph.reports_by_member_level(previous),
            community_graph.reports_by_member(previous),
        )
        written["reports_carried_over"] = community_graph.carry_reports(ctx, carry)
        written["reports_dropped"] = len(previous) - len(carry)
        written["reports_copied_from_another_level"] = sum(
            1 for r in carry if community_graph.COPIED_FROM in r["props"]
        )
        written["applied"] = True

    report = build_report(
        communities=communities,
        gds_levels=gds_levels,
        per_level=per_level,
        projected=projected,
        branch=branch,
        raw_branch=raw_branch,
        unprojected=unprojected,
        stats=stats,
        stream_ms=stream_ms,
        config=config,
        min_size=min_size,
        kinds=kinds,
        dropped=dropped,
        written=written,
        schema_stats=schema_stats,
        carried=carried,
        census=community_graph.census(ctx) if not dry_run else {},
        unplaced=community_graph.unplaced_members(ctx, FINE) if not dry_run else {},
        counters=dict(ctx.counters),
        built_at=built_at,
        prefix=ctx.prefix,
        dry_run=dry_run,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    if write_report:
        path = write_section(reports_dir, "build", report)
        echo(f"report: {path}")
    echo(summarize(report))
    return report, 0


def build_report(
    *,
    communities: list[Community],
    gds_levels: list[int],
    per_level: dict[str, Any],
    projected: dict[str, Any],
    branch: dict[str, int],
    raw_branch: dict[str, int],
    unprojected: dict[str, int],
    stats: dict[str, Any],
    stream_ms: int,
    config: dict[str, Any],
    min_size: int,
    kinds: dict[str, str],
    dropped: list[str],
    written: dict[str, Any],
    schema_stats: dict[str, Any],
    carried: list[str],
    census: dict[str, Any],
    unplaced: dict[str, Any],
    counters: dict[str, int],
    built_at: str,
    prefix: str,
    dry_run: bool,
    duration_ms: int,
) -> dict[str, Any]:
    summarized = [c for c in communities if c.summarized]
    collapsed = {
        name: raw_branch[name] - branch.get(name, 0)
        for name in sorted(raw_branch)
        if raw_branch[name] - branch.get(name, 0)
    }
    return {
        "step": "communities.build",
        "generated_at": built_at,
        "duration_ms": duration_ms,
        "label_prefix": prefix,
        "dry_run": dry_run,
        "projection": {
            **projected,
            "rows_per_branch": branch,
            "relationships_per_branch": raw_branch,
            # raw relationships minus canonical pairs: the duplicate and reciprocal edges
            # that would otherwise have weighted one edge twice (see projection.Branch)
            "parallel_relationships_collapsed": collapsed,
            "parallel_relationships_collapsed_total": sum(collapsed.values()),
            "edges_left_out_by_type": unprojected,
            "collapse": {
                proj.MENTIONS_PARENT: "(parent)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(thing)",
                proj.DELIVERS_KIP: "(w:WorkItem)<-[:RESOLVES]-(:Commit)-[:IMPLEMENTS_KIP]->(d)",
            },
        },
        "leiden": {
            "config": config,
            "ran_levels": stats["ranLevels"],
            "did_converge": stats["didConverge"],
            "modularity": stats["modularity"],
            "modularities_by_gds_level": stats["modularities"],
            "community_count_final_level": stats["communityCount"],
            "community_distribution": stats["communityDistribution"],
            "compute_ms": stats["computeMillis"],
            "stats_wall_ms": stats["wall_ms"],
            "stream_wall_ms": stream_ms,
            "gds_levels_used": gds_levels,
            "level_map": {str(i): f"gds level {g}" for i, g in enumerate(gds_levels)},
        },
        "levels": per_level,
        "totals": {
            "communities": len(communities),
            "summarized": len(summarized),
            "misc": len(communities) - len(summarized),
            "min_size": min_size,
            "members": sum(c.size for c in communities),
            "sizes_all_levels": size_distribution([c.size for c in communities]),
        },
        "placement": {
            "Entity": label_placement(communities, "Entity"),
            "WorkItem": label_placement(communities, "WorkItem"),
            "Document": label_placement(communities, "Document"),
            "Component": label_placement(communities, "Component"),
            "entity_by_kind": kind_placement(communities, kinds),
        },
        "written": written,
        "schema": schema_stats,
        "reports_carried_over_ids": carried[:20],
        "census": census,
        "unplaced_members_fine_level": unplaced,
        "counters": counters,
        "warnings": {
            "level_choice": level_warnings(per_level, gds_levels),
            "nodes_without_a_key": dropped[:20],
            "nodes_without_a_key_count": len(dropped),
        },
        "examples": {
            "largest_fine": [
                {"id": c.id, "size": c.size, "by_label": c.counts_by_label()}
                for c in sorted((c for c in communities if c.level == FINE), key=lambda c: -c.size)[
                    :3
                ]
            ]
        },
    }


def summarize(report: dict[str, Any]) -> str:
    p, le, t = report["projection"], report["leiden"], report["totals"]
    lines = [
        f"communities build{' (dry run)' if report['dry_run'] else ''}: "
        f"{p['nodes']} nodes / {p['edges']} undirected edges projected "
        f"in {p['project_ms']} ms",
        f"leiden: {le['ran_levels']} levels, modularity {le['modularity']}, "
        f"{le['compute_ms']} ms compute; kept gds levels {le['gds_levels_used']}",
    ]
    if p["parallel_relationships_collapsed_total"]:
        lines.append(
            f"aggregated {p['parallel_relationships_collapsed_total']} parallel/reciprocal "
            f"relationships into single edges: {p['parallel_relationships_collapsed']}"
        )
    for level, data in sorted(report["levels"].items()):
        s = data["sizes"]
        lines.append(
            f"  level {level} ({data['name']}): {data['communities']} communities, "
            f"{data['summarized']} summarizable, {data['misc']} misc; sizes "
            f"min {s['min']} / p50 {s['p50']} / p90 {s['p90']} / p95 {s['p95']} / max {s['max']}"
        )
    lines.append(
        f"total: {t['communities']} communities, {t['summarized']} to summarise "
        f"(min size {t['min_size']})"
    )
    tech = report["placement"]["entity_by_kind"].get("Technology")
    if tech:
        lines.append(
            f"Technology entities: {tech['members']} placed, "
            f"{tech['in_singleton_community']} alone, {tech['in_misc_community']} in a misc "
            f"community ({tech['pct_in_misc']}%)"
        )
    for warning in report["warnings"]["level_choice"]:
        lines.append(f"WARNING {warning}")
    return "\n".join(lines)
