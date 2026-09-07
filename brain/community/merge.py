"""`brain communities merge` — the summarizer's reports, validated, then written.

Validation is the step-04 protocol, three gates deep: JSON Schema
(`brain/community/schema.json`), then pydantic, then the two rules a schema cannot state
because they need the *input* to check them —

* a `community_id` this batch did not ask about is not a report about anything;
* an `evidence_chunk_ids` entry that was not offered to *this* community is not evidence
  about it. The agent has no database; every id it can legitimately cite is one it was
  handed, and a citation of another community's chunk is a hallucinated link, not a
  cross-reference.

A report that fails is rejected and counted; the rest of the batch still lands. A whole
batch that fails goes to `retry/` twice and then to `quarantine/`, and the report names it
either way.

Provenance is stamped by this code and never by the agent (conventions rule 3): the union
of the findings' evidence chunk ids, the batch that said it, `opus:community-summarizer`,
and when. The run asserts afterwards, against the live graph, that no summarised community
lacks any of them.

The embedding is `title + summary` through the local Ollama embedder, written with
`db.create.setNodeVectorProperty` so `community_embedding` can serve S5. A community whose
report has not changed keeps the vector it has: `embed_hash` is sha1 of the embedded text,
and a re-merge of the same reports embeds nothing.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from brain.common.jsonschema_mini import validate as schema_validate
from brain.community import graph as community_graph
from brain.community.batches import SCHEMA_PATH
from brain.community.models import TASK, BatchInput, BatchOutput, Report
from brain.community.report import write_section
from brain.embed.client import OllamaEmbedder
from brain.extract.build import STATUS_NAME, sha256_of
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso, write_json_atomic

MAX_RETRIES = 2
RETRY_DIR = "retry"
QUARANTINE_DIR = "quarantine"
RETRY_FIELD = "_communities_retry"
LEDGER_NAME = "ledger.json"
MAX_ERRORS = 50
MODEL = community_graph.MODEL


class MergeError(RuntimeError):
    """Merge cannot produce a correct graph. Never downgraded to a partial run."""


# ---------------------------------------------------------------------------- discovery


@dataclass
class Rejection:
    community_id: str
    reason: str
    detail: str = ""

    def row(self) -> dict[str, str]:
        return {"community_id": self.community_id, "reason": self.reason, "detail": self.detail}


@dataclass
class Batch:
    batch_id: str
    shard: str
    index: int
    path: Path
    sha256: str = ""
    extracted_at: str = ""
    output: BatchOutput | None = None
    batch_input: BatchInput | None = None
    accepted: list[Report] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.output is not None and self.batch_input is not None

    @property
    def in_path(self) -> Path:
        return self.path.with_name(f"{self.index:03d}.in.json")


def discover(root: Path) -> list[Batch]:
    """Every `<shard>/NNN.out.json` under `root`, with the file facts merge stamps from."""
    out: list[Batch] = []
    for path in sorted(root.glob("shard-[0-9][0-9]/[0-9][0-9][0-9].out.json")):
        index = int(path.name.split(".", 1)[0])
        out.append(
            Batch(
                batch_id=f"{path.parent.name}/{index:03d}",
                shard=path.parent.name,
                index=index,
                path=path,
                sha256=sha256_of(path),
                # When the agent wrote it. `merged_at` is when we read it; the two are
                # different facts and a report that conflates them cannot be audited.
                extracted_at=_mtime_iso(path),
            )
        )
    return out


def stamp_extracted_at(batches: Sequence[Batch], seen: dict[str, Any]) -> int:
    """Keep the recorded extraction time for every batch whose bytes have not changed.

    `extracted_at` is provenance: it says when the model wrote this answer. A file's mtime
    is a proxy for that, and a proxy that a `git clone`, a `touch`, a restore from backup
    or a copy between machines resets — which would restamp 186 community reports with a
    moment no agent ever wrote anything at. The ledger records when each batch's *content*
    was first seen, keyed by its sha256, and that is what the node carries until the
    content actually changes. Returns how many times the recorded value was reused.
    """
    reused = 0
    for batch in batches:
        record = seen.get(batch.batch_id) or {}
        if record.get("sha256") == batch.sha256 and record.get("extracted_at"):
            batch.extracted_at = str(record["extracted_at"])
            reused += 1
    return reused


def batch_ledger(batches: Sequence[Batch]) -> dict[str, dict[str, str]]:
    """What `stamp_extracted_at` reads next time: content hash -> when it was first seen."""
    return {
        b.batch_id: {"sha256": b.sha256, "extracted_at": b.extracted_at}
        for b in sorted(batches, key=lambda b: b.batch_id)
    }


def _mtime_iso(path: Path) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- validation


def parse_batch(batch: Batch, schema: dict[str, Any]) -> None:
    """JSON -> schema -> pydantic -> the input it answers. Every stage's complaints."""
    raw = _read_json(batch.path)
    if raw is None:
        batch.errors.append("unreadable: not a JSON object this step can parse")
        return
    if raw.get("batch_id") != batch.batch_id:
        batch.errors.append(f"batch_id is {raw.get('batch_id')!r}, file says {batch.batch_id!r}")
    problems = schema_validate(raw, schema)
    batch.errors.extend(f"schema: {p}" for p in problems[:MAX_ERRORS])
    if problems:
        return
    try:
        batch.output = BatchOutput.model_validate(raw)
    except ValidationError as exc:
        batch.errors.extend(f"model: {e['loc']}: {e['msg']}" for e in exc.errors()[:MAX_ERRORS])
        return
    batch.notes = list(batch.output.notes)
    if not batch.in_path.is_file():
        batch.errors.append(
            f"no input beside it ({batch.in_path.name}); the community ids cannot be checked "
            "against anything. Re-run `brain communities batches`."
        )
        return
    try:
        batch.batch_input = BatchInput.model_validate_json(
            batch.in_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        batch.errors.append(f"input {batch.in_path.name} is unusable: {exc}")


def screen(batch: Batch, known_chunks: set[str] | None = None) -> None:
    """Per-report rules that need the input. One bad report costs that report only.

    `known_chunks`, when given, is the set of chunk ids the graph actually holds: an id
    that was offered by the batch but has since been re-chunked away is still not a
    citation anybody can follow.
    """
    if not batch.ok or batch.output is None or batch.batch_input is None:
        return
    asked = {c.community_id for c in batch.batch_input.communities}
    seen: set[str] = set()
    for report in batch.output.reports:
        cid = report.community_id
        if cid not in asked:
            batch.rejections.append(
                Rejection(cid, "unknown_community", f"not one of the {len(asked)} in this batch")
            )
            continue
        if cid in seen:
            batch.rejections.append(Rejection(cid, "duplicate_report", "answered twice"))
            continue
        seen.add(cid)
        offered = batch.batch_input.evidence_ids(cid)
        cited = set(report.evidence_chunk_ids)
        foreign = sorted(cited - offered)
        if foreign:
            batch.rejections.append(
                Rejection(
                    cid,
                    "evidence_not_offered",
                    f"{len(foreign)} chunk id(s) not in this community's evidence: {foreign[:3]}",
                )
            )
            continue
        if known_chunks is not None:
            gone = sorted(cited - known_chunks)
            if gone:
                batch.rejections.append(
                    Rejection(cid, "evidence_not_in_graph", f"{len(gone)} chunk id(s): {gone[:3]}")
                )
                continue
        if any(not f.evidence_chunk_ids for f in report.findings):
            batch.rejections.append(Rejection(cid, "finding_without_evidence", ""))
            continue
        batch.accepted.append(report)
    missing = sorted(asked - seen)
    if missing:
        batch.rejections.extend(
            Rejection(cid, "not_answered", "the batch asked about it and the output does not")
            for cid in missing
        )


def findings_without_evidence(batches: Sequence[Batch]) -> int:
    """The acceptance criterion, counted over everything the agents wrote. Must be 0."""
    total = 0
    for batch in batches:
        for report in batch.output.reports if batch.output else []:
            total += sum(1 for f in report.findings if not f.evidence_chunk_ids)
    return total


# ------------------------------------------------------------------------------- rows


def embed_text(report: Report) -> str:
    """What S5 matches a question against: the title, then the summary."""
    return f"{report.title}\n\n{report.summary}"


def embed_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def node_rows(batches: Sequence[Batch], merged_at: str) -> list[dict[str, Any]]:
    """One row per accepted report, provenance stamped here and nowhere else.

    Sorted by batch id and taken first-wins, so two batches that somehow answered the same
    community produce one deterministic node rather than whichever landed last.
    """
    rows: dict[str, dict[str, Any]] = {}
    for batch in sorted(batches, key=lambda b: b.batch_id):
        for report in batch.accepted:
            if report.community_id in rows:
                continue
            findings = [
                {"statement": f.statement, "evidence_chunk_ids": sorted(f.evidence_chunk_ids)}
                for f in report.findings
            ]
            rows[report.community_id] = {
                "id": report.community_id,
                "props": {
                    "title": report.title,
                    "summary": report.summary,
                    "findings": community_graph.encode_findings(findings),
                    "finding_statements": [f["statement"] for f in findings],
                    "rank": float(report.rank),
                    "rank_reason": report.rank_reason,
                    "evidence_chunk_ids": report.evidence_chunk_ids,
                    "batch_id": batch.batch_id,
                    "model": MODEL,
                    "extracted_at": batch.extracted_at,
                    "reported_at": merged_at,
                },
                "embed_text": embed_text(report),
            }
    return [rows[k] for k in sorted(rows)]


def report_is_current(stored: dict[str, Any] | None, props: dict[str, Any]) -> bool:
    """True when the node already carries exactly this report.

    A second merge over unchanged outputs must leave the graph alone. `SET c += props`
    would happily rewrite 186 identical nodes and report 186 writes, which reads as work
    and is noise: the counters, the report and any change-data-capture downstream would all
    say something happened. Everything the report *says* is compared; `reported_at` — when
    merge last ran — is not, because it differs on every run by construction.
    """
    if not stored:
        return False
    return all(stored.get(p) == props.get(p) for p in community_graph.COMPARED_PROPS)


def stale_reports(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows whose report is not already on the node, word for word."""
    if not rows:
        return []
    stored = community_graph.stored_reports(ctx, [r["id"] for r in rows])
    return [r for r in rows if not report_is_current(stored.get(r["id"]), r["props"])]


def stale_embeddings(ctx: GraphContext, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows whose embedded text is not what the node already carries."""
    if not rows:
        return []
    stored = {
        r["id"]: r["hash"]
        for r in ctx.read(
            f"MATCH (c:{ctx.label(community_graph.LABEL)}) "
            f"WHERE c.`{community_graph.EMBEDDING_PROP}` IS NOT NULL "
            f"RETURN c.id AS id, c.`{community_graph.EMBED_HASH_PROP}` AS hash",
        )
    }
    return [r for r in rows if stored.get(r["id"]) != embed_hash(r["embed_text"])]


# ----------------------------------------------------------------------- retry/quarantine


def handle_failure(batch: Batch, ledger_failed: dict[str, Any]) -> dict[str, Any]:
    """Copy the *input* into `retry/`, or `quarantine/` once the attempts are spent.

    The attempt counter only moves when the output actually changed: re-running merge over
    an unchanged bad batch is a re-read, not a new attempt.
    """
    previous = ledger_failed.get(batch.batch_id) or {}
    attempt = int(previous.get("attempt", 0))
    if previous.get("sha256") != batch.sha256:
        attempt += 1
    shard_dir = batch.path.parent
    retry_path = shard_dir / RETRY_DIR / batch.in_path.name
    quarantine_path = shard_dir / QUARANTINE_DIR / batch.in_path.name
    payload = _read_json(batch.in_path) or {"batch_id": batch.batch_id}
    payload[RETRY_FIELD] = {
        "attempt": attempt,
        "at": utc_now_iso(),
        "output": batch.path.name,
        "output_sha256": batch.sha256,
        "reasons": batch.errors[:20],
    }
    state = "retry" if attempt <= MAX_RETRIES else "quarantine"
    if state == "retry":
        write_json_atomic(retry_path, payload)
        quarantine_path.unlink(missing_ok=True)
    else:
        payload[RETRY_FIELD]["quarantined_after"] = MAX_RETRIES
        write_json_atomic(quarantine_path, payload)
        retry_path.unlink(missing_ok=True)
    return {
        "attempt": attempt,
        "sha256": batch.sha256,
        "state": state,
        "at": payload[RETRY_FIELD]["at"],
        "reasons": batch.errors[:20],
        "path": str(retry_path if state == "retry" else quarantine_path),
    }


def clear_failure_files(batch: Batch) -> None:
    shard_dir = batch.path.parent
    (shard_dir / RETRY_DIR / batch.in_path.name).unlink(missing_ok=True)
    (shard_dir / QUARANTINE_DIR / batch.in_path.name).unlink(missing_ok=True)


def read_shard_status(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("shard-[0-9][0-9]/" + STATUS_NAME)):
        data = _read_json(path)
        if data is not None:
            out[path.parent.name] = data
    return out


def agent_failures(status: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for shard, data in sorted(status.items()):
        for entry in data.get("failed") or []:
            if isinstance(entry, dict):
                out.append(
                    {
                        "batch": str(entry.get("batch") or shard),
                        "reason": str(entry.get("reason") or "no reason given"),
                    }
                )
    return out


# -------------------------------------------------------------------------------- the run


def run_merge(
    *,
    ctx: GraphContext,
    batches_dir: Path,
    reports_dir,
    embedder: OllamaEmbedder | None = None,
    embed_dim: int | None = None,
    schema_path: Path = SCHEMA_PATH,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Validate every report, write what survives, embed it, audit the graph, report."""
    started = time.perf_counter()
    root = batches_dir / TASK
    if not root.is_dir():
        raise MergeError(
            f"no batches to merge: {root} does not exist (run `brain communities batches`)"
        )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    batches = discover(root)
    if not batches:
        raise MergeError(f"no NNN.out.json under {root}; the summarizers have written nothing")
    ledger_path = root / LEDGER_NAME
    ledger = _read_json(ledger_path) or {}
    # Before anything reads `extracted_at`: a batch whose bytes are unchanged keeps the
    # time the ledger recorded, not whatever the filesystem now says about the file.
    reused_times = stamp_extracted_at(batches, dict(ledger.get("batches") or {}))
    for batch in batches:
        parse_batch(batch, schema)

    cited = {c for b in batches if b.output for r in b.output.reports for c in r.evidence_chunk_ids}
    known = community_graph.existing_chunk_ids(ctx, sorted(cited))
    for batch in batches:
        screen(batch, known)

    valid = [b for b in batches if b.ok]
    invalid = [b for b in batches if not b.ok]
    merged_at = utc_now_iso()

    dim = embed_dim if embed_dim is not None else (embedder.dim if embedder else None)
    if dim is None:
        raise MergeError("an embedding dimension is required to declare community_embedding")
    schema_stats = community_graph.apply_community_schema(ctx, dim)

    rows = node_rows(valid, merged_at)
    known_ids = _known_community_ids(ctx)
    missing_nodes = [r["id"] for r in rows if r["id"] not in known_ids]
    rows = [r for r in rows if r["id"] in known_ids]
    changed = stale_reports(ctx, rows)
    written = community_graph.write_reports(
        ctx, [{"id": r["id"], "props": r["props"]} for r in changed]
    )

    embedded = 0
    embed_ms = 0
    embed_usage: dict[str, Any] = {}
    to_embed = stale_embeddings(ctx, rows)
    if to_embed:
        if embedder is None:
            raise MergeError(
                f"{len(to_embed)} community reports need an embedding and no embedder was "
                "given. `Community.embedding` is what global search matches against."
            )
        embed_started = time.perf_counter()
        vectors = embedder.embed([r["embed_text"] for r in to_embed])
        embed_ms = round((time.perf_counter() - embed_started) * 1000)
        embedded = community_graph.write_embeddings(
            ctx,
            [
                {
                    "id": r["id"],
                    "embedding": v,
                    "embed_hash": embed_hash(r["embed_text"]),
                }
                for r, v in zip(to_embed, vectors, strict=True)
            ],
        )
    if embedder is not None:
        embed_usage = dict(embedder.usage())
    index_meta = community_graph.write_index_meta(
        ctx,
        model=(embedder.model if embedder is not None else "unknown"),
        dim=dim,
        now=merged_at,
    )

    provenance = community_graph.provenance_gaps(ctx)
    if provenance["total"]:
        raise MergeError(
            f"{provenance['total']} summarised communities carry no provenance "
            f"({provenance['examples']}). Conventions rule 3: every LLM-derived node names "
            "its evidence, its batch, its model and when. This is a bug in the merge."
        )

    ledger_failed: dict[str, Any] = dict(ledger.get("failed") or {})
    new_failed = {b.batch_id: handle_failure(b, ledger_failed) for b in invalid}
    for batch in valid:
        clear_failure_files(batch)
    hashes = _member_hashes(ctx, [r["id"] for r in rows])
    reported = dict(ledger.get("reported") or {})
    for row in rows:
        member_hash = hashes.get(row["id"])
        if member_hash:
            reported[member_hash] = {
                "community_id": row["id"],
                "batch_id": row["props"]["batch_id"],
                "model": MODEL,
                "extracted_at": row["props"]["extracted_at"],
                "reported_at": merged_at,
            }
    write_json_atomic(
        ledger_path,
        {
            "step": "communities.merge",
            "updated_at": merged_at,
            "model": MODEL,
            "note": (
                "member_hash -> the report written for that exact set of members. A rebuild "
                "that leaves a community's members unchanged reuses this instead of paying "
                "an agent to write it again."
            ),
            "reported": dict(sorted(reported.items())),
            "failed": dict(sorted(new_failed.items())),
            # When each batch's content was first seen, so `extracted_at` survives a
            # `touch`, a clone or a restore that moves the file's mtime.
            "batches": batch_ledger(batches),
        },
    )

    status = read_shard_status(root)
    report = build_report(
        batches=batches,
        valid=valid,
        invalid=invalid,
        rows=rows,
        written=written,
        unchanged=len(rows) - len(changed),
        embedded=embedded,
        embed_ms=embed_ms,
        embed_usage=embed_usage,
        to_embed=len(to_embed),
        missing_nodes=missing_nodes,
        schema_stats=schema_stats,
        index=community_graph.index_status(ctx),
        index_meta=index_meta,
        duplicates=community_graph.cross_level_duplicates(ctx),
        provenance=provenance,
        census=community_graph.census(ctx),
        top=community_graph.top_ranked(ctx, 3),
        reported_failures=agent_failures(status),
        reused_times=reused_times,
        counters=dict(ctx.counters),
        merged_at=merged_at,
        prefix=ctx.prefix,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    if write_report:
        path = write_section(reports_dir, "merge", report)
        echo(f"report: {path}")
    echo(summarize(report))
    return report, (1 if invalid or report["batches"]["reported_failed"] else 0)


def _known_community_ids(ctx: GraphContext) -> set[str]:
    rows = ctx.read(f"MATCH (c:{ctx.label(community_graph.LABEL)}) RETURN c.id AS id")
    return {r["id"] for r in rows}


def _member_hashes(ctx: GraphContext, ids: Sequence[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = ctx.read(
        f"MATCH (c:{ctx.label(community_graph.LABEL)}) WHERE c.id IN $ids "
        "RETURN c.id AS id, c.member_hash AS member_hash",
        ids=list(ids),
    )
    return {r["id"]: r["member_hash"] for r in rows if r["member_hash"]}


# ------------------------------------------------------------------------------ report


def build_report(
    *,
    batches: Sequence[Batch],
    valid: Sequence[Batch],
    invalid: Sequence[Batch],
    rows: Sequence[dict[str, Any]],
    written: int,
    unchanged: int,
    embedded: int,
    embed_ms: int,
    embed_usage: dict[str, Any],
    to_embed: int,
    missing_nodes: Sequence[str],
    schema_stats: dict[str, Any],
    index: dict[str, Any],
    index_meta: dict[str, Any],
    duplicates: dict[str, Any],
    provenance: dict[str, Any],
    census: dict[str, Any],
    top: Sequence[dict[str, Any]],
    reported_failures: Sequence[dict[str, Any]],
    reused_times: int,
    counters: dict[str, int],
    merged_at: str,
    prefix: str,
    duration_ms: int,
) -> dict[str, Any]:
    reasons: dict[str, int] = {}
    for batch in batches:
        for rejection in batch.rejections:
            reasons[rejection.reason] = reasons.get(rejection.reason, 0) + 1
    reports_seen = sum(len(b.output.reports) if b.output else 0 for b in batches)
    accepted = sum(len(b.accepted) for b in valid)
    findings = [f for r in rows for f in community_graph.decode_findings(r["props"]["findings"])]
    by_level: dict[str, int] = {}
    for row in rows:
        level = row["id"].split("-", 1)[0].lstrip("L")
        by_level[level] = by_level.get(level, 0) + 1
    return {
        "step": "communities.merge",
        "generated_at": merged_at,
        "duration_ms": duration_ms,
        "label_prefix": prefix,
        "model": MODEL,
        "batches": {
            "found": len(batches),
            "valid": len(valid),
            "invalid": len(invalid),
            "envelope_valid_rate": round(len(valid) / len(batches), 4) if batches else 0.0,
            "reported_failed": len(reported_failures),
            # `extracted_at` taken from the ledger rather than from the file's mtime
            "extraction_times_reused": reused_times,
        },
        "reports": {
            "seen": reports_seen,
            "accepted": accepted,
            "rejected": reports_seen - accepted,
            "written": written,
            # A re-merge over unchanged outputs writes nothing; this is how many it left
            # alone, and the two together always add up to the reports on the graph.
            "unchanged": unchanged,
            "on_graph": len(rows),
            "by_level": dict(sorted(by_level.items())),
            "rejection_reasons": dict(sorted(reasons.items())),
            "rejections": [r.row() for b in batches for r in b.rejections[:3]][:40],
            "community_nodes_missing": list(missing_nodes[:20]),
            "community_nodes_missing_count": len(missing_nodes),
        },
        "findings": {
            "total": len(findings),
            # over the reports on the graph, not over the ones this run wrote: a re-merge
            # writes nothing and would otherwise report 1,527 findings at 0.0 per report
            "per_report": round(len(findings) / len(rows), 2) if rows else 0.0,
            # The acceptance criterion. Zero, or the step failed.
            "without_evidence": findings_without_evidence(batches),
            "evidence_chunk_ids": sum(len(f.get("evidence_chunk_ids") or []) for f in findings),
        },
        "embedding": {
            "needed": to_embed,
            "written": embedded,
            "duration_ms": embed_ms,
            "text": "title + summary",
            "index": index,
            "index_meta": index_meta,
            **({"usage": embed_usage} if embed_usage else {}),
        },
        "cross_level_duplicates": duplicates,
        "schema": schema_stats,
        "provenance": {
            "required": list(community_graph.PROVENANCE_PROPS),
            "communities_without_provenance": provenance["total"],
            "examples": provenance["examples"],
        },
        "census": census,
        "counters": counters,
        "top_ranked": list(top),
        "notes_from_agents": {b.batch_id: b.notes[:5] for b in valid if b.notes},
        "rejected_batches": [
            {
                "batch": b.batch_id,
                "source": "merge",
                "output": str(b.path),
                "errors": b.errors[:20],
                "error_count": len(b.errors),
            }
            for b in invalid
        ]
        + [
            {
                "batch": f["batch"],
                "source": "status.json",
                "output": None,
                "errors": [f["reason"]],
                "error_count": 1,
            }
            for f in reported_failures
        ],
    }


def summarize(report: dict[str, Any]) -> str:
    b, r, f, e = (
        report["batches"],
        report["reports"],
        report["findings"],
        report["embedding"],
    )
    lines = [
        f"communities merge: {b['valid']}/{b['found']} batches valid → "
        f"{r['written']} reports written, {r['unchanged']} unchanged, "
        f"{r['rejected']} reports rejected",
        f"reports by level: {r['by_level']}",
        f"findings: {f['total']} ({f['per_report']} per report), "
        f"{f['without_evidence']} without evidence",
        f"embedding: {e['written']} written of {e['needed']} needed in {e['duration_ms']} ms; "
        f"index {e['index'].get('name')} is {e['index'].get('state')} "
        f"({e['index_meta'].get('live')} vectors, model {e['index_meta'].get('model')})",
    ]
    dup = report["cross_level_duplicates"]
    if dup["total"]:
        lines.append(
            f"{dup['total']} member sets carry a report at more than one level "
            f"({dup['communities']} communities): "
            + ", ".join("=".join(p["ids"]) for p in dup["pairs"][:3])
            + (" …" if dup["total"] > 3 else "")
        )
    if r["rejection_reasons"]:
        lines.append(
            "rejected because: "
            + ", ".join(f"{n} {reason}" for reason, n in r["rejection_reasons"].items())
        )
    for row in report["top_ranked"]:
        lines.append(f"  rank {row['rank']}: {row['title']} ({row['id']}, {row['size']} members)")
    if report["rejected_batches"]:
        lines.append(
            "rejected batches: "
            + ", ".join(
                f"{x['batch']} ({x['error_count']} errors via {x['source']})"
                for x in report["rejected_batches"]
            )
        )
    return "\n".join(lines)
