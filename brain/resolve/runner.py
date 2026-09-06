"""`brain resolve` — three tiers, one pass each, and a number for every one of them.

Tiers run in order and each one runs against the graph the previous one left: tier 2
embeds *survivors*, so a person tier 1 has already assembled from three systems is
compared with all three systems' activity behind them. That ordering is also what makes
"merges per tier" an honest attribution — a pair is credited to the first tier that could
have found it, because by the time the next tier looks, it is one node.

Nothing here decides a threshold from the data. Sanity checks (a merge group of eight
identities, an auto-merge between two candidates with no activity at all) are counted and
reported; none of them silently drops a pair.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from brain.embed.client import OllamaEmbedder
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.resolve import embed as resolve_embed
from brain.resolve import graph as resolve_graph
from brain.resolve.ledger import ResolutionLedger
from brain.resolve.models import KINDS, Candidate, Pair
from brain.resolve.names import survivor
from brain.resolve.tiers import (
    ADJUDICATE_FLOOR,
    AUTO_THRESHOLD,
    dedupe,
    entity_tier1,
    person_tier1,
    tier2_pairs,
)
from brain.resolve.tiers import (
    groups as connected_groups,
)

REPORT_NAME = "resolve.json"
LABELS: dict[str, str] = {
    "person": resolve_graph.PERSON_LABEL,
    "entity": resolve_graph.ENTITY_LABEL,
}
TIERS: tuple[int, ...] = (1, 2, 3)
#: Neighbours per candidate the vector index returns. 25 is well past the largest merge
#: group this corpus can produce (4 identities) and still one cheap probe per node.
DEFAULT_K = 25
#: A merge group larger than this is reported as a warning, not refused: it is usually a
#: name so common that similarity chained several people together.
LARGE_GROUP = 5

NOTES = [
    "Tiers run in order, each against the graph the previous one left, so a merge is "
    "credited to the first tier that could have found it.",
    "`--dry-run` writes no SAME_AS, no merge and no ledger — but it does write embeddings, "
    "because tier 2 cannot be scored without them and a vector is derived data, not a "
    "decision.",
    "apoc.refactor.mergeNodes runs with `mergeRels: false`. Merging relationships would "
    "collapse two ASSIGNED_TO intervals with different `valid_from` into one; instead the "
    "edges are moved and a second pass deletes only edges identical in every property "
    "(duplicate_edges_deleted).",
    "The brief's `identities[]` is the `identity_keys` list `brain load` already writes on "
    "every Person; resolve unions it across the merged nodes rather than adding a second "
    "list with the same content.",
]


class ResolveError(RuntimeError):
    """Resolution cannot run against this graph or these inputs."""


def resolve_kinds(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(KINDS)
    chosen = [k.strip().lower() for k in value.split(",") if k.strip()]
    bad = [k for k in chosen if k not in KINDS]
    if bad or not chosen:
        raise ValueError(f"unknown kind(s) {bad or ['']}; pick from {', '.join(KINDS)} or all")
    return [k for k in KINDS if k in chosen]


def resolve_tiers(value: str) -> list[int]:
    if value.strip().lower() == "all":
        return list(TIERS)
    chosen: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or int(part) not in TIERS:
            raise ValueError(f"unknown tier {part!r}; pick from 1, 2, 3 or all")
        chosen.append(int(part))
    if not chosen:
        raise ValueError("no tier selected; pick from 1, 2, 3 or all")
    return sorted(set(chosen))


def load_alias_candidates(reports_dir: Path) -> list[dict[str, Any]]:
    """`alias_candidates[]` from `data/reports/load.json` — brief 08 decision 1 (c)."""
    path = reports_dir / "load.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("alias_candidates")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


# ---------------------------------------------------------------------------- one merge


def survivor_rows(
    kind: str,
    group: Sequence[str],
    by_id: dict[str, Candidate],
    *,
    tier: int,
    stamp: str,
) -> tuple[str, dict[str, Any]]:
    """The survivor id and the properties that record what it swallowed."""
    keep = survivor(kind, group)
    swallowed = [i for i in group if i != keep]
    members = [by_id[i] for i in group if i in by_id]
    identities = sorted({i for m in members for i in (m.identities or [m.id])})
    kept = by_id.get(keep)
    aliases = sorted(
        {m.name for m in members if m.name and (kept is None or m.name != kept.name)}
        | {a for m in members for a in m.aliases}
    )
    props: dict[str, Any] = {
        "resolved": True,
        "resolved_at": stamp,
        # The weakest evidence this node was built from: a person tier 1 assembled and
        # tier 3 then extended is a tier-3 person, because a tier-3 decision is now load
        # bearing in it.
        "resolution_tier": max([tier, *(m.resolution_tier or 0 for m in members)]),
        # Union, not replacement: the ids an earlier tier merged in are still merged in.
        "merged_from": sorted(set(swallowed) | {i for m in members for i in m.merged_from}),
        "aliases": aliases,
    }
    if kind == "person":
        props["identity_keys"] = identities
    return keep, props


def apply_merges(
    ctx: GraphContext,
    *,
    kind: str,
    pairs: Sequence[Pair],
    candidates: Sequence[Candidate],
    ledger: ResolutionLedger,
    tier: int,
    dry_run: bool,
    stamp: str,
) -> dict[str, Any]:
    """SAME_AS, then mergeNodes, then the survivor's lists and the ledger rows."""
    label = LABELS[kind]
    by_id = {c.id: c for c in candidates}
    grouped = connected_groups(pairs)
    ordered = [[survivor(kind, g), *[i for i in g if i != survivor(kind, g)]] for g in grouped]
    stats: dict[str, Any] = {
        "pairs": len(pairs),
        "groups": len(grouped),
        "identities_merged": sum(len(g) - 1 for g in grouped),
        "largest_group": max((len(g) for g in grouped), default=0),
        "large_groups": [g for g in grouped if len(g) > LARGE_GROUP],
    }
    if dry_run or not pairs:
        stats["applied"] = False
        return stats

    stats["same_as_edges"] = resolve_graph.write_same_as(ctx, label, pairs)
    merged = resolve_graph.merge_groups(ctx, label, ordered)
    stats["merge"] = merged

    rows: list[dict[str, Any]] = []
    entries: list[tuple[str, str, dict[str, Any]]] = []
    best: dict[str, Pair] = {}
    for pair in pairs:
        for node in (pair.a, pair.b):
            prior = best.get(node)
            if prior is None or (pair.tier, -pair.score) < (prior.tier, -prior.score):
                best[node] = pair
    for group in grouped:
        keep, props = survivor_rows(kind, group, by_id, tier=tier, stamp=stamp)
        rows.append({"id": keep, "props": props})
        for member in group:
            if member == keep:
                continue
            evidence = best.get(member)
            entries.append(
                (
                    member,
                    keep,
                    {
                        "tier": evidence.tier if evidence else tier,
                        "rule": evidence.rule if evidence else None,
                        "score": evidence.score if evidence else None,
                        "reason": evidence.reason if evidence else None,
                    },
                )
            )
    stats["survivors"] = resolve_graph.set_resolved(ctx, label, rows)
    stats["survivor_ids"] = [r["id"] for r in rows]
    stats["self_loops_deleted"] = resolve_graph.delete_self_loops(ctx, label)
    stats["duplicate_edges_deleted"] = resolve_graph.dedupe_relationships(
        ctx, label, [r["id"] for r in rows]
    )
    ledger.record(kind, entries, resolved_at=stamp)
    stats["applied"] = True
    return stats


# -------------------------------------------------------------------------------- tiers


def run_tier1(
    ctx: GraphContext,
    *,
    kind: str,
    candidates: Sequence[Candidate],
    alias_candidates: Sequence[dict[str, Any]],
    ledger: ResolutionLedger,
    dry_run: bool,
    stamp: str,
) -> dict[str, Any]:
    if kind == "person":
        pairs = person_tier1(candidates, alias_candidates=alias_candidates)
    else:
        pairs = entity_tier1(candidates)
    pairs = dedupe(pairs)
    by_rule: dict[str, int] = {}
    for pair in pairs:
        by_rule[pair.rule] = by_rule.get(pair.rule, 0) + 1
    stats = apply_merges(
        ctx,
        kind=kind,
        pairs=pairs,
        candidates=candidates,
        ledger=ledger,
        tier=1,
        dry_run=dry_run,
        stamp=stamp,
    )
    stats["by_rule"] = dict(sorted(by_rule.items()))
    stats["sample"] = [p.model_dump() for p in pairs[:20]]
    return stats


def score_tier2(
    ctx: GraphContext,
    *,
    kind: str,
    candidates: Sequence[Candidate],
    embedder: OllamaEmbedder,
    k: int,
    vector_evidence: int = resolve_embed.EVIDENCE_IN_VECTOR,
    echo: Callable[[str], None],
) -> tuple[list[Pair], list[Pair], dict[str, Any]]:
    """Embed, probe the vector index, split the band. Writes vectors, merges nothing."""
    label = LABELS[kind]
    by_id = {c.id: c for c in candidates}
    resolve_graph.apply_resolve_schema(ctx, label, embedder.dim)
    usage = resolve_embed.ensure_embeddings(
        ctx, label, candidates, embedder, evidence=vector_evidence, echo=echo
    )
    scored = resolve_graph.knn(ctx, label, k=k, floor=ADJUDICATE_FLOOR)
    auto, grey = tier2_pairs(scored, by_id, text_note=usage["text_choice"])
    stats = {
        "k": k,
        "band": [ADJUDICATE_FLOOR, AUTO_THRESHOLD],
        "embedding": usage,
        "scored_pairs": len(scored),
        "auto": len(auto),
        "grey": len(grey),
    }
    return auto, grey, stats


def run_tier2(
    ctx: GraphContext,
    *,
    kind: str,
    candidates: Sequence[Candidate],
    embedder: OllamaEmbedder,
    ledger: ResolutionLedger,
    k: int,
    dry_run: bool,
    stamp: str,
    vector_evidence: int,
    echo: Callable[[str], None],
) -> dict[str, Any]:
    auto, grey, stats = score_tier2(
        ctx,
        kind=kind,
        candidates=candidates,
        embedder=embedder,
        k=k,
        vector_evidence=vector_evidence,
        echo=echo,
    )
    by_id = {c.id: c for c in candidates}
    stats["no_activity_pairs"] = sum(
        1 for p in auto if not by_id[p.a].evidence or not by_id[p.b].evidence
    )
    stats.update(
        apply_merges(
            ctx,
            kind=kind,
            pairs=auto,
            candidates=candidates,
            ledger=ledger,
            tier=2,
            dry_run=dry_run,
            stamp=stamp,
        )
    )
    stats["auto_pairs"] = [p.model_dump() for p in auto]
    stats["grey_pairs_sample"] = [p.model_dump() for p in grey[:20]]
    if not dry_run and stats.get("applied"):
        # A survivor's text changed: it inherited the other node's activity. Dropping the
        # vector is what makes the next pass re-embed exactly the nodes the merge touched.
        stats["embeddings_cleared"] = resolve_graph.clear_embeddings(
            ctx, LABELS[kind], stats.get("survivor_ids", [])
        )
    return stats


# ------------------------------------------------------------------------------ the run


def _merge_report(path: Path, section: dict[str, Any]) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(section)
    return existing


def run_resolve(
    *,
    client: GraphClient,
    canonical_dir: Path,
    reports_dir: Path,
    kinds: Sequence[str],
    tiers: Sequence[int],
    embedder: OllamaEmbedder | None = None,
    batches_dir: Path | None = None,
    k: int = DEFAULT_K,
    vector_evidence: int = resolve_embed.EVIDENCE_IN_VECTOR,
    prefix: str = "",
    dry_run: bool = False,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    started = time.perf_counter()
    ctx = GraphContext(client, prefix=prefix)
    stamp = utc_now_iso()
    ledger = ResolutionLedger.load(canonical_dir)
    alias_candidates = load_alias_candidates(reports_dir)
    durations: dict[str, float] = {}
    sections: dict[str, Any] = {}
    warnings: list[str] = []

    for kind in kinds:
        label = LABELS[kind]
        t0 = time.perf_counter()
        before = resolve_graph.census(ctx, label)
        section: dict[str, Any] = {"before": before, "merges_by_tier": {}}
        echo(f"{kind}: {before['nodes']} nodes, {before['identities']} identities")
        if before["nodes"] == 0:
            section["skipped"] = "no nodes with this label"
            warnings.append(f"{kind}: nothing to resolve — the graph holds no {label} nodes")
            sections[kind] = section
            durations[kind] = round(time.perf_counter() - t0, 2)
            continue

        for tier in tiers:
            candidates = resolve_graph.read_candidates(ctx, kind)
            if tier == 1:
                stats = run_tier1(
                    ctx,
                    kind=kind,
                    candidates=candidates,
                    alias_candidates=alias_candidates,
                    ledger=ledger,
                    dry_run=dry_run,
                    stamp=stamp,
                )
            elif tier == 2:
                if embedder is None:
                    raise ResolveError("tier 2 needs an embedder; none was supplied")
                stats = run_tier2(
                    ctx,
                    kind=kind,
                    candidates=candidates,
                    embedder=embedder,
                    ledger=ledger,
                    k=k,
                    dry_run=dry_run,
                    stamp=stamp,
                    vector_evidence=vector_evidence,
                    echo=echo,
                )
            else:
                from brain.resolve.decisions import apply_decisions

                if batches_dir is None:
                    raise ResolveError("tier 3 needs a batches directory; none was supplied")
                stats = apply_decisions(
                    ctx,
                    kind=kind,
                    batches_dir=batches_dir,
                    candidates=candidates,
                    ledger=ledger,
                    dry_run=dry_run,
                    stamp=stamp,
                    echo=echo,
                )
            section[f"tier{tier}"] = stats
            section["merges_by_tier"][str(tier)] = stats.get("identities_merged", 0)
            for group in stats.get("large_groups", []):
                warnings.append(
                    f"{kind} tier {tier}: merge group of {len(group)} identities — {group[:6]}"
                )
            if stats.get("no_activity_pairs"):
                warnings.append(
                    f"{kind} tier 2: {stats['no_activity_pairs']} auto-merges where at least "
                    "one side has no activity at all — the vector saw a bare name"
                )
            echo(
                f"{kind} tier {tier}: {stats.get('pairs', 0)} pairs, "
                f"{stats.get('groups', 0)} groups, "
                f"{stats.get('identities_merged', 0)} identities merged"
                + ("  [dry-run]" if dry_run else "")
            )

        section["after"] = resolve_graph.census(ctx, label)
        durations[kind] = round(time.perf_counter() - t0, 2)
        sections[kind] = section

    if not dry_run:
        ledger.write(canonical_dir)
    report_path = reports_dir / REPORT_NAME
    total = round(time.perf_counter() - started, 2)
    report = _merge_report(
        report_path,
        {
            "step": "resolve",
            "generated_at": stamp,
            "last_run": {
                "kinds": list(kinds),
                "tiers": list(tiers),
                "dry_run": dry_run,
                "label_prefix": prefix,
                "k": k,
                "vector_evidence": vector_evidence,
                "duration_s": total,
                "per_kind_s": durations,
                "counters": ctx.counters,
            },
            **sections,
            "ledger": {
                "path": str(canonical_dir / "resolution_ledger.json"),
                "counts": ledger.counts(),
                "written": not dry_run,
            },
            "warnings": warnings,
            "notes": NOTES,
        },
    )
    if write_report:
        write_json_atomic(report_path, report)
    for line in warnings:
        echo(f"[WARN] {line}")
    echo(f"resolve: {total}s" + ("  [dry-run: nothing written]" if dry_run else ""))
    return report, 0


def _embedder() -> OllamaEmbedder:
    from brain.config import get_settings

    s = get_settings()
    return OllamaEmbedder(s.ollama_url, s.embed_model, s.embed_dim)


def resolve_from_settings(
    *,
    kinds: Sequence[str],
    tiers: Sequence[int],
    dry_run: bool = False,
    k: int = DEFAULT_K,
    vector_evidence: int = resolve_embed.EVIDENCE_IN_VECTOR,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    from brain.config import get_settings

    s = get_settings()
    needs_embedder = 2 in tiers
    embedder = _embedder() if needs_embedder else None
    try:
        with GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) as client:
            return run_resolve(
                client=client,
                canonical_dir=s.canonical_dir,
                reports_dir=s.reports_dir,
                batches_dir=s.batches_dir,
                kinds=kinds,
                tiers=tiers,
                embedder=embedder,
                k=k,
                vector_evidence=vector_evidence,
                dry_run=dry_run,
                echo=echo,
            )
    finally:
        if embedder is not None:
            embedder.close()
