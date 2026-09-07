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
    MIN_SUBSTANTIVE_TOKENS,
    band_table,
    cap_band,
    dedupe,
    entity_auto_ok,
    entity_tier1,
    generic_stems_seen,
    kip_title_links,
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
#: Neighbours per entity (coordinator's decision (a)). Ten inside the kind, not ten in the
#: label: `Entity` holds six kinds and 9,237 nodes, and all-pairs is 42M comparisons.
ENTITY_K = 10
#: The adjudicator's budget for the entity band. Ranked, not truncated — see `cap_band`.
ENTITY_BAND_LIMIT = 1200
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
    "DEVIATION from brief 08 decision 3, measured: the tier-2 vector is the display name "
    "alone (`vector_evidence: 0`), not display + 5 touched titles. Over the 570 gold "
    "identity pairs and 63 hard negatives, cosine p10/p50/p90 on true pairs is "
    "0.45/0.63/0.75 with the titles in the vector and 0.60/0.76/0.89 without them: with "
    "them the median true pair falls under the 0.80 floor the tier starts asking at, "
    "because two identities of one person live in different systems and share no item at "
    "all (0 shared neighbours across all 420 synthetic identities). The titles also pull "
    "*different* people who work one backlog together — on the mini corpus 'Jun Rao' and "
    "'Dana Lee' reach 0.94 and would auto-merge. The titles still reach the adjudicator "
    "as `evidence[]`. `--vector-evidence 5` restores the briefed behaviour.",
    "`baseline` is the census before any resolution ran; `before`/`after` and the tierN "
    "sections describe single invocations, each stamped with its own `at`. Cumulative "
    "totals are `merges_by_tier` / `merges_by_rule`, read from the ledger.",
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


def evidence_weight(by_id: dict[str, Candidate]) -> Callable[[str], int]:
    """How much the corpus says about a node — the entity survivor rule."""

    def weight(node_id: str) -> int:
        candidate = by_id.get(node_id)
        return len(candidate.evidence) if candidate else 0

    return weight


def survivor_rows(
    kind: str,
    group: Sequence[str],
    by_id: dict[str, Candidate],
    *,
    tier: int,
    stamp: str,
) -> tuple[str, dict[str, Any]]:
    """The survivor id and the properties that record what it swallowed."""
    keep = survivor(kind, group, evidence_weight(by_id) if kind == "entity" else None)
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
    else:
        # Every phrasing the merged entities carried, longest first: the survivor's own
        # description is one of them, so nothing a merge swallowed is lost.
        props["descriptions"] = sorted(
            {
                d
                for m in members
                for d in ([*m.descriptions, m.description] if m.description else m.descriptions)
                if d
            },
            key=lambda d: (-len(d), d),
        )
        if props["descriptions"]:
            props["description"] = props["descriptions"][0]
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
    weight = evidence_weight(by_id) if kind == "entity" else None
    ordered = [
        [survivor(kind, g, weight), *[i for i in g if i != survivor(kind, g, weight)]]
        for g in grouped
    ]
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
    survivor_ids = [r["id"] for r in rows]
    stats["survivors_sample"] = survivor_ids[:20]
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
    links: list[dict[str, Any]] = []
    if kind == "person":
        pairs = person_tier1(candidates, alias_candidates=alias_candidates)
    else:
        pairs = entity_tier1(candidates)
        # Brief 08 decision 2: a Feature named after a KIP is linked to the Document, not
        # merged into anything. It is an edge, so it never enters the merge machinery.
        links = kip_title_links(candidates, resolve_graph.document_titles(ctx))
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
    if kind == "person":
        stats["generic_stems_rejected"] = generic_stems_seen(candidates)
    if links:
        stats["kip_title_links"] = {
            "count": len(links),
            "written": 0 if dry_run else resolve_graph.write_document_links(ctx, links),
            "sample": [{"entity": r["src"], "document": r["dst"]} for r in links[:20]],
            "note": "SAME_AS to the Document. A link, never a merge — the KIP proposes the "
            "feature, it is not the feature.",
        }
    return stats


def score_tier2(
    ctx: GraphContext,
    *,
    kind: str,
    candidates: Sequence[Candidate],
    embedder: OllamaEmbedder,
    k: int,
    vector_evidence: int = resolve_embed.EVIDENCE_IN_VECTOR,
    band_limit: int = ENTITY_BAND_LIMIT,
    echo: Callable[[str], None],
) -> tuple[list[Pair], list[Pair], dict[str, Any]]:
    """Embed, probe the vector index, split the band. Writes vectors, merges nothing."""
    label = LABELS[kind]
    by_id = {c.id: c for c in candidates}
    resolve_graph.apply_resolve_schema(ctx, label, embedder.dim)
    usage = resolve_embed.ensure_embeddings(
        ctx, label, candidates, embedder, evidence=vector_evidence, echo=echo
    )
    scored = resolve_graph.knn(
        ctx, label, k=k, floor=ADJUDICATE_FLOOR, within_kind=(kind == "entity")
    )
    auto, grey, filtered = tier2_pairs(
        scored,
        by_id,
        text_note=usage["text_choice"],
        # Entities need more than a cosine to merge unasked: a shared word or a shared
        # parent document. Two Decisions phrased alike in unrelated KIPs are two decisions.
        auto_extra=entity_auto_ok if kind == "entity" else None,
    )
    cut_off = None
    if kind == "entity":
        grey, cut_off = cap_band(grey, by_id, limit=band_limit)
    stats = {
        "k": k,
        "band": [ADJUDICATE_FLOOR, AUTO_THRESHOLD],
        "min_substantive_tokens": MIN_SUBSTANTIVE_TOKENS,
        "embedding": usage,
        "scored_pairs": len(scored),
        "auto": len(auto),
        "grey": len(grey),
        "filtered": filtered,
        "band_limit": band_limit if kind == "entity" else None,
        "band_cut_off_rank": cut_off,
        # Reported, never applied: what the adjudicator's bill would be at other floors.
        "band_table": band_table(scored, by_id),
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
    band_limit: int = ENTITY_BAND_LIMIT,
    echo: Callable[[str], None],
) -> dict[str, Any]:
    auto, grey, stats = score_tier2(
        ctx,
        kind=kind,
        candidates=candidates,
        embedder=embedder,
        k=k,
        vector_evidence=vector_evidence,
        band_limit=band_limit,
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
            ctx, LABELS[kind], [survivor(kind, g) for g in connected_groups(auto)]
        )
    return stats


# ------------------------------------------------------------------------------ the run


def merges_by(ledger: ResolutionLedger, kind: str, field: str) -> dict[str, int]:
    """How many identities each tier (or rule) has swallowed, over every run so far.

    Read from the ledger rather than from this invocation, because tiers are meant to be
    run one command at a time: `--tier 2` on its own must still say what tier 1 did, and
    the `tier1`/`tier2` sections of the report only ever describe the last invocation.
    """
    counts: dict[str, int] = {}
    for entry in ledger.section(kind).values():
        value = entry.get(field)
        if value is not None:
            counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


#: How many invocations the report remembers. The tier sections describe only the last
#: one, so without this the "753 auto-merges" a real run made would vanish the moment a
#: no-op rerun proved idempotency.
MAX_HISTORY = 20


def derived_baseline(before: dict[str, Any], ledger: ResolutionLedger, kind: str) -> dict[str, Any]:
    """The census as it was before any resolution ran, reconstructed from the ledger.

    Used only the first time a report is written against an already-resolved graph — the
    ledger holds one row per identity a merge swallowed, so today's node count plus those
    rows is exactly the node count resolution started from. Flagged `derived` so nobody
    reads it as a measurement.
    """
    folded = len(ledger.section(kind))
    if not folded:
        return {**before, "derived": False}
    nodes = int(before["nodes"]) + folded
    identities = int(before["identities"])
    return {
        "nodes": nodes,
        "identities": identities,
        "identities_per_node": round(identities / nodes, 4) if nodes else 0.0,
        "duplicate_rate": round(1 - nodes / identities, 4) if identities else 0.0,
        "resolved": 0,
        "embedded": 0,
        "key": before.get("key"),
        "derived": True,
        "note": f"reconstructed as {before['nodes']} nodes now + {folded} identities the "
        "ledger says were merged away; the run that measured it directly predates this report",
    }


def _previous_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _merge_report(
    path: Path, section: dict[str, Any], history: dict[str, Any] | None = None
) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    runs = existing.get("runs")
    runs = list(runs) if isinstance(runs, list) else []
    existing.update(section)
    if history is not None:
        existing["runs"] = [*runs, history][-MAX_HISTORY:]
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
    previous = _previous_report(reports_dir / REPORT_NAME)
    durations: dict[str, float] = {}
    sections: dict[str, Any] = {}
    warnings: list[str] = []

    for kind in kinds:
        label = LABELS[kind]
        t0 = time.perf_counter()
        before = resolve_graph.census(ctx, label)
        # `before` is this invocation's starting point; `baseline` is the graph as it was
        # before any resolution ever ran. The brief asks for duplicates before *and* after,
        # and a rerun that merges nothing would otherwise report "1425 -> 1425".
        prior = previous.get(kind) or {}
        baseline = prior.get("baseline") or derived_baseline(before, ledger, kind)
        # Tiers are run one command at a time, so a `--tier 2` invocation must not erase
        # what the `--tier 1` invocation before it recorded. Each section carries the
        # timestamp of the run that produced it.
        section: dict[str, Any] = {
            **{k: v for k, v in prior.items() if k.startswith("tier")},
            "baseline": baseline,
            "before": before,
        }
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
                    # Ten neighbours inside the kind for entities, twenty-five for people:
                    # a person has a handful of identities, an entity kind has thousands of
                    # near-synonyms and a wide k buys duplicates of the same question.
                    k=ENTITY_K if (kind == "entity" and k == DEFAULT_K) else k,
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
            section[f"tier{tier}"] = {"at": stamp, **stats}
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

        # From the ledger, not from this run: `--tier 2` alone must still report what
        # tier 1 merged in the run before it, or the totals reset every invocation.
        section["merges_by_tier"] = merges_by(ledger, kind, "tier")
        section["merges_by_rule"] = merges_by(ledger, kind, "rule")
        section["after"] = resolve_graph.census(ctx, label)
        durations[kind] = round(time.perf_counter() - t0, 2)
        sections[kind] = section

    if not dry_run:
        ledger.write(canonical_dir)
    report_path = reports_dir / REPORT_NAME
    total = round(time.perf_counter() - started, 2)
    this_run = {
        "at": stamp,
        "kinds": list(kinds),
        "tiers": list(tiers),
        "dry_run": dry_run,
        "merged": {
            k: sections[k].get(f"tier{t}", {}).get("identities_merged", 0)
            for k in sections
            for t in tiers
        },
        "duration_s": total,
    }
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
        history=this_run,
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
