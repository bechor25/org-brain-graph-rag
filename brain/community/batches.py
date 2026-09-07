"""`brain communities batches` — one community, its top members and its evidence, on disk.

The packing rules are the ones step 04 paid for and step 07 wrote down: **indented JSON,
40 KB per file**, because an LLM-role agent's file reader truncates a single long line and
a batch nobody fully read is a report about text nobody saw. Sizes are measured on the real
serialised payload, never estimated.

What a community sends the agent (brief 09 decision 4): its top-25 members by *projected*
degree, and up to 8 evidence chunks of at most 600 characters, ranked by how many of the
community's entities each chunk names. That is the whole context — no member list of 1,200,
no full document bodies. A report is supposed to be about a theme, and a theme is legible
from its hubs.

Only communities that need a report are packed: `misc` ones are never summarised, and a
community whose `member_hash` already carries a summary in the graph is not summarised
again. That last rule is the incremental half of the step, and it is why a rebuild after a
small change costs a handful of batches instead of all of them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brain.community import graph as community_graph
from brain.community.build import BuildError
from brain.community.graph import COPIED_FROM
from brain.community.models import TASK, BatchInput, ShardStatus
from brain.community.report import write_section
from brain.extract.build import (
    IN_GLOB,
    MANIFEST_NAME,
    MAX_BATCH_BYTES,
    MAX_LINE_BYTES,
    OUT_GLOB,
    STALE_DIR,
    STATUS_NAME,
    longest_line_bytes,
    sha256_of,
    shard_name,
    shards_in_flight,
    write_input_if_changed,
)
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso, write_json_atomic

SCHEMA_PATH = Path(__file__).with_name("schema.json")
SCHEMA_REF = "brain/community/schema.json"
AGENT_REF = ".claude/agents/community-summarizer.md"

DEFAULT_SHARDS = 2
DEFAULT_TOP_MEMBERS = 25
DEFAULT_EVIDENCE = 8
DEFAULT_EVIDENCE_CHARS = 600


@dataclass
class PlannedBatch:
    shard: int
    index: int
    communities: list[dict[str, Any]]

    @property
    def batch_id(self) -> str:
        return f"{shard_name(self.shard)}/{self.index:03d}"


def envelope(
    *,
    batch_id: str,
    shard: str,
    index: int,
    communities: Sequence[dict[str, Any]],
    generated_at: str,
    schema_sha: str,
) -> dict[str, Any]:
    return BatchInput(
        batch_id=batch_id,
        shard=shard,
        index=index,
        generated_at=generated_at,
        schema_path=SCHEMA_REF,
        schema_sha256=schema_sha,
        community_count=len(communities),
        communities=list(communities),  # type: ignore[arg-type]
    ).model_dump(mode="json")


def serialise(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def pack(
    communities: Sequence[dict[str, Any]],
    *,
    shard: int,
    max_bytes: int = MAX_BATCH_BYTES,
    generated_at: str = "",
    schema_sha: str = "",
) -> list[PlannedBatch]:
    """Greedy packing against the *serialised* size, so "40 KB" is measured, not guessed.

    A community that alone exceeds the budget still gets a batch of its own: dropping
    members or evidence to fit would make the report thinner than the community deserves,
    and one oversize batch that the report names is better than a quiet truncation.
    """
    batches: list[PlannedBatch] = []
    current: list[dict[str, Any]] = []

    def close() -> None:
        if current:
            batches.append(
                PlannedBatch(shard=shard, index=len(batches) + 1, communities=list(current))
            )
            current.clear()

    def size(candidate: Sequence[dict[str, Any]]) -> int:
        payload = envelope(
            batch_id=f"{shard_name(shard)}/{len(batches) + 1:03d}",
            shard=shard_name(shard),
            index=len(batches) + 1,
            communities=candidate,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )
        return len(serialise(payload).encode("utf-8"))

    for community in communities:
        current.append(community)
        if size(current) > max_bytes and len(current) > 1:
            current.pop()
            close()
            current.append(community)
    close()
    return batches


def assign_shards(communities: Sequence[dict[str, Any]], shards: int) -> list[list[dict[str, Any]]]:
    """Largest community first into the lightest shard: balanced *reading*, not counts."""
    if shards < 1:
        raise BuildError(f"shards must be at least 1, got {shards}")
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(shards)]
    load = [0] * shards
    for community in sorted(communities, key=lambda c: (-_weight(c), c["community_id"])):
        i = min(range(shards), key=lambda n: (load[n], n))
        buckets[i].append(community)
        load[i] += _weight(community)
    return [sorted(b, key=lambda c: (c["level"], c["community_id"])) for b in buckets]


def _weight(community: dict[str, Any]) -> int:
    """How much reading this community is: its evidence text plus its member lines."""
    return sum(len(e["text"]) for e in community["evidence"]) + sum(
        len(m["name"] or "") + len(m["description"] or "") for m in community["members"]
    )


def split_copyable(
    wanted: Sequence[dict[str, Any]], reports: Mapping[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`(to_copy, to_summarize)` — the reports that already exist under another id.

    Leiden's coarse level does not always merge anything: on the live graph 22 coarse
    communities hold *exactly* the members of a fine one, so `member_hash` is equal and the
    two would get two agent-written reports about one set of things. The text would differ
    only in the way two runs of the same model differ, which is not information — it is 22
    batches of agent time and a global search that returns the same cluster twice under two
    ids that look unrelated.

    So the second one is copied instead of re-summarised: the same title, summary, findings
    and rank, the same vector, and the provenance of the run that actually wrote it — the
    batch, the model and the extraction time all name the report's real origin, with
    `copied_from` naming the community it was written for. Nothing here claims a second
    agent said the same thing twice.
    """
    to_copy: list[dict[str, Any]] = []
    to_summarize: list[dict[str, Any]] = []
    for row in wanted:
        stored = reports.get(row["member_hash"] or "")
        source = (stored or {}).get("previous_id")
        if not stored or not source or source == row["community_id"]:
            to_summarize.append(row)
            continue
        props = {
            k: v
            for k, v in stored.items()
            if k not in {"embedding", "embed_hash", "previous_id"} and v is not None
        }
        to_copy.append(
            {
                "id": row["community_id"],
                "community_id": row["community_id"],
                "member_hash": row["member_hash"],
                "source": source,
                "level": row["level"],
                "props": {**props, COPIED_FROM: source},
                "embedding": stored.get("embedding"),
                "embed_hash": stored.get("embed_hash"),
            }
        )
    return to_copy, to_summarize


def collect(
    ctx: GraphContext,
    *,
    top_members: int = DEFAULT_TOP_MEMBERS,
    evidence: int = DEFAULT_EVIDENCE,
    evidence_chars: int = DEFAULT_EVIDENCE_CHARS,
    resummarize: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Every community that still needs a report, with the context the agent gets.

    Returns `(selected, to_copy, stats)`: what an agent must write, what can be copied
    from a community that already holds these exact members, and the numbers behind both.
    """
    rows = ctx.read(
        f"MATCH (c:{ctx.label(community_graph.LABEL)})\n"
        "RETURN c.id AS community_id, c.level AS level, c.level_name AS level_name, "
        "c.size AS size, c.member_hash AS member_hash, c.misc AS misc, "
        "c.summary AS summary\n"
        "ORDER BY c.level, c.size DESC, c.id"
    )
    if not rows:
        raise BuildError(
            "no Community nodes in "
            f"{'namespace ' + ctx.prefix if ctx.prefix else 'this database'}. "
            "Run `brain communities build` first."
        )
    misc = [r for r in rows if r["misc"]]
    already = [r for r in rows if not r["misc"] and r["summary"]]
    asked = [r for r in rows if not r["misc"] and (resummarize or not r["summary"])]
    # `--resummarize` means "write these again", so it also overrides the copy rule: the
    # planner asking for a rewrite must not be answered with the old text under a new id.
    to_copy: list[dict[str, Any]] = []
    wanted = asked
    if not resummarize:
        to_copy, wanted = split_copyable(
            asked, community_graph.reports_by_member(community_graph.read_reports(ctx))
        )

    ids = [r["community_id"] for r in wanted]
    members = community_graph.read_members(ctx, ids, top_n=top_members)
    chunks = community_graph.read_evidence(ctx, ids, limit=evidence, chars=evidence_chars)

    out: list[dict[str, Any]] = []
    without_evidence: list[str] = []
    for row in wanted:
        cid = row["community_id"]
        found = chunks.get(cid, [])
        if not found:
            # Every finding must cite a chunk, and the agent may only cite what it was
            # given, so a community with no evidence cannot produce a compliant report.
            # Sending it anyway would buy a guaranteed rejection and a wasted batch; it is
            # excluded and counted instead, and the count is a signal about the corpus —
            # a community of nodes that no chunk ever talks about.
            without_evidence.append(cid)
            continue
        out.append(
            {
                "community_id": cid,
                "level": row["level"],
                "level_name": row["level_name"],
                "size": row["size"],
                "member_hash": row["member_hash"],
                "members_shown": len(members.get(cid, [])),
                "members": members.get(cid, []),
                "evidence": found,
            }
        )
    stats = {
        "communities_in_graph": len(rows),
        "misc_skipped": len(misc),
        "already_summarized_skipped": len(already),
        "copied_from_another_level": len(to_copy),
        "copied_pairs": [{"community": c["id"], "from": c["source"]} for c in to_copy],
        "no_evidence_skipped": len(without_evidence),
        "selected": len(out),
        "without_evidence": without_evidence[:20],
        "top_members": top_members,
        "evidence_per_community": evidence,
        "evidence_chars": evidence_chars,
        "resummarize": resummarize,
    }
    return out, to_copy, stats


def run_batches(
    *,
    ctx: GraphContext,
    batches_dir: Path,
    reports_dir,
    shards: int = DEFAULT_SHARDS,
    top_members: int = DEFAULT_TOP_MEMBERS,
    evidence: int = DEFAULT_EVIDENCE,
    evidence_chars: int = DEFAULT_EVIDENCE_CHARS,
    force: bool = False,
    resummarize: bool = False,
    schema_path: Path = SCHEMA_PATH,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Write `data/batches/communities/<shard>/NNN.in.json` + status + manifest."""
    started = time.perf_counter()
    if not schema_path.is_file():
        raise BuildError(f"the output contract is missing: {schema_path}")

    root = batches_dir / TASK
    busy = shards_in_flight(root)
    if busy and not force:
        listed = ", ".join(f"{shard} ({why})" for shard, why in busy.items())
        raise BuildError(
            f"refusing to rebuild: {listed}. An agent has already worked against the "
            "current inputs, and repacking would repoint its answers at different "
            "communities. Merge what is done, or clear the shard's status.json and move "
            "its .out.json files aside."
        )

    selected, to_copy, stats = collect(
        ctx,
        top_members=top_members,
        evidence=evidence,
        evidence_chars=evidence_chars,
        resummarize=resummarize,
    )
    if to_copy:
        stats["copied_written"] = community_graph.copy_reports(ctx, to_copy)
        echo(
            f"copied {len(to_copy)} report(s) onto communities that hold exactly the "
            "members of a community already summarised at the other level "
            f"(first: {to_copy[0]['id']} from {to_copy[0]['source']})"
        )
    if not selected:
        report = {
            "step": "communities.batches",
            "generated_at": utc_now_iso(),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "label_prefix": ctx.prefix,
            "selection": stats,
            "sharding": {"shards": shards, "batches_per_shard": {}},
            "totals": {"batches": 0, "communities": 0},
            "batches": [],
        }
        if write_report:
            write_section(reports_dir, "batches", report)
        echo("communities batches: nothing to summarise — every community already has a report")
        return report, 0

    schema_sha = sha256_of(schema_path)
    generated_at = utc_now_iso()
    planned: list[PlannedBatch] = []
    for i, bucket in enumerate(assign_shards(selected, shards)):
        planned.extend(pack(bucket, shard=i, generated_at=generated_at, schema_sha=schema_sha))

    written: list[dict[str, Any]] = []
    for batch in planned:
        shard_dir = root / shard_name(batch.shard)
        payload = envelope(
            batch_id=batch.batch_id,
            shard=shard_name(batch.shard),
            index=batch.index,
            communities=batch.communities,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )
        path = shard_dir / f"{batch.index:03d}.in.json"
        rewritten = write_input_if_changed(path, payload)
        status_path = shard_dir / STATUS_NAME
        if not status_path.is_file():
            write_json_atomic(
                status_path, ShardStatus(shard=shard_name(batch.shard)).model_dump(mode="json")
            )
        text = path.read_text(encoding="utf-8")
        written.append(
            {
                "id": batch.batch_id,
                "path": str(path.relative_to(root)),
                "communities": len(batch.communities),
                "members": sum(len(c["members"]) for c in batch.communities),
                "evidence": sum(len(c["evidence"]) for c in batch.communities),
                "bytes": path.stat().st_size,
                "max_line_bytes": longest_line_bytes(text),
                "sha256": sha256_of(path),
                "rewritten": rewritten,
            }
        )

    stale = _remove_stale(root, planned)
    for name in stale["removed_inputs"]:
        echo(f"removed stale input {name} (a previous run planned more batches than this one)")
    for name in stale["moved_outputs"]:
        echo(f"moved orphaned output to {name} (no batch of this plan asks for it)")

    manifest = manifest_of(
        written,
        selected=selected,
        stats=stats,
        shards=shards,
        schema_path=schema_path,
        schema_sha=schema_sha,
        generated_at=generated_at,
        stale=stale,
        prefix=ctx.prefix,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    write_json_atomic(root / MANIFEST_NAME, manifest)
    oversize = [b for b in written if b["bytes"] > MAX_BATCH_BYTES]
    for b in oversize:
        echo(f"WARNING {b['id']} is {b['bytes']} bytes, over the {MAX_BATCH_BYTES} budget")
    echo(summarize(manifest))
    echo(f"manifest: {root / MANIFEST_NAME}")
    if write_report:
        write_section(reports_dir, "batches", manifest)
    return manifest, (1 if oversize else 0)


def _remove_stale(root: Path, planned: Sequence[PlannedBatch]) -> dict[str, list[str]]:
    """Delete inputs this plan no longer asks for; move orphaned outputs aside, never delete."""
    current = {
        str((root / shard_name(b.shard) / f"{b.index:03d}.in.json").resolve()) for b in planned
    }
    planned_ids = {b.batch_id for b in planned}
    removed: list[str] = []
    moved: list[str] = []
    if not root.is_dir():
        return {"removed_inputs": removed, "moved_outputs": moved}
    for path in sorted(root.glob(f"shard-*/{IN_GLOB}")):
        if str(path.resolve()) in current:
            continue
        path.unlink()
        removed.append(str(path.relative_to(root)))
    for path in sorted(root.glob(f"shard-*/{OUT_GLOB}")):
        batch_id = f"{path.parent.name}/{path.name.split('.', 1)[0]}"
        if batch_id in planned_ids:
            continue
        target = path.parent / STALE_DIR / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        moved.append(str(target.relative_to(root)))
    return {"removed_inputs": removed, "moved_outputs": moved}


def manifest_of(
    written: list[dict[str, Any]],
    *,
    selected: Sequence[dict[str, Any]],
    stats: dict[str, Any],
    shards: int,
    schema_path: Path,
    schema_sha: str,
    generated_at: str,
    stale: dict[str, list[str]],
    prefix: str,
    duration_ms: int,
) -> dict[str, Any]:
    sizes = [b["bytes"] for b in written]
    per_shard: dict[str, int] = {}
    for b in written:
        shard = b["id"].split("/")[0]
        per_shard[shard] = per_shard.get(shard, 0) + 1
    by_level: dict[str, int] = {}
    for c in selected:
        by_level[str(c["level"])] = by_level.get(str(c["level"]), 0) + 1
    return {
        "step": "communities.batches",
        "generated_at": generated_at,
        "duration_ms": duration_ms,
        "label_prefix": prefix,
        "schema": {"path": SCHEMA_REF, "sha256": schema_sha, "bytes": schema_path.stat().st_size},
        "agent": AGENT_REF,
        "selection": {**stats, "selected_by_level": dict(sorted(by_level.items()))},
        "sharding": {
            "shards": shards,
            "max_batch_bytes": MAX_BATCH_BYTES,
            "max_line_bytes": MAX_LINE_BYTES,
            "rule": "shards balanced by evidence + member characters, not by community count",
            "batches_per_shard": dict(sorted(per_shard.items())),
            "removed_stale_inputs": list(stale["removed_inputs"]),
            "moved_stale_outputs": list(stale["moved_outputs"]),
        },
        "sizes": {
            "max_bytes": max(sizes) if sizes else 0,
            "min_bytes": min(sizes) if sizes else 0,
            "mean_bytes": round(sum(sizes) / len(sizes)) if sizes else 0,
            "total_bytes": sum(sizes),
            "max_line_bytes": max((b["max_line_bytes"] for b in written), default=0),
            "over_budget": [b["id"] for b in written if b["bytes"] > MAX_BATCH_BYTES],
        },
        "totals": {
            "batches": len(written),
            "communities": sum(b["communities"] for b in written),
            "members": sum(b["members"] for b in written),
            "evidence": sum(b["evidence"] for b in written),
            "batches_per_agent": round(len(written) / shards, 1) if shards else 0,
        },
        "batches": written,
    }


def summarize(manifest: dict[str, Any]) -> str:
    t, s, sel = manifest["totals"], manifest["sizes"], manifest["selection"]
    lines = [
        f"communities batches: {t['communities']} communities "
        f"({sel['misc_skipped']} misc and {sel['already_summarized_skipped']} already "
        f"summarised skipped) → {t['batches']} batches over "
        f"{manifest['sharding']['shards']} shards",
        f"sizes: max {s['max_bytes'] / 1000:.1f} KB, mean {s['mean_bytes'] / 1000:.1f} KB, "
        f"longest line {s['max_line_bytes']} B"
        + (f" — OVER BUDGET: {', '.join(s['over_budget'])}" if s["over_budget"] else ""),
        f"context: {t['members']} member rows, {t['evidence']} evidence chunks",
    ]
    if sel["no_evidence_skipped"]:
        lines.append(
            f"WARNING {sel['no_evidence_skipped']} communities were skipped with no evidence "
            f"chunk at all (first: {sel['without_evidence'][0]}) — no chunk in the corpus "
            "talks about any of their members, so no finding could cite anything"
        )
    return "\n".join(lines)
