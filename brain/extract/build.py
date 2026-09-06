"""`brain extract build` — Phase A chunks out of the graph, into batches an agent can read.

The one decision that shapes this file is brief 07 decision 1: **indented JSON, 40 KB per
file**. Step 04 shipped compact one-line batches, the LLM-role agents' file reader truncated
the single 100 KB line at ~48 KB, and 42% of the corpus was generated against text nobody
ever read — silently, with valid outputs. So a batch here is packed against a real byte
budget measured on the real serialised payload, not estimated: chunks are appended until
`json.dumps(..., indent=2)` would cross `MAX_BATCH_BYTES`, and then the batch closes.

Two smaller rules follow from what the agent has to do with the text:

* a parent's chunks stay in one shard, so the agent naming entities in section 4 of a KIP
  has already read sections 1-3 and names them the same way;
* shards are balanced by bytes, not by batch count — a shard of KIP sections is three times
  the reading of a shard of issue descriptions.

Rebuilding while an agent is working is refused, not raced: its `status.json` records
batches it finished against *these* inputs, and a reshard would repoint them.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.canon.runner import natural_key
from brain.extract.models import BatchInput, ShardStatus
from brain.extract.select import SelectedChunk, Selection
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso, write_json_atomic

TASK = "extract"
PHASE = "A"
MANIFEST_NAME = "MANIFEST.json"
STATUS_NAME = "status.json"
IN_GLOB = "[0-9][0-9][0-9].in.json"
SCHEMA_PATH = Path(__file__).with_name("schema.json")
SCHEMA_REF = "brain/extract/schema.json"
EXAMPLES_REF = "brain/extract/examples.md"

#: Bytes, decimal KB — the brief says "40 KB" and a reviewer dividing by 1000 must agree.
MAX_BATCH_BYTES = 40_000
#: A single line an agent's reader would truncate (~48 KB measured in step 04). No chunk in
#: this corpus is near it; the guard exists so the day one is, the build fails loudly.
MAX_LINE_BYTES = 45_000

DEFAULT_SHARDS = 4
DEFAULT_BATCH_SIZE = 20


class BuildError(RuntimeError):
    """Build cannot produce inputs an agent could safely work from."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def shard_name(index: int) -> str:
    return f"shard-{index + 1:02d}"


# --------------------------------------------------------------------------- planning


@dataclass
class Parent:
    """One KIP or one work item, and every Phase A chunk of it."""

    kind: str
    key: str
    source: str
    chunks: list[SelectedChunk] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(c.char_len for c in self.chunks)


def group_by_parent(chunks: Sequence[SelectedChunk]) -> list[Parent]:
    """Chunks grouped by their parent, parents in a stable (source, key) order."""
    grouped: dict[tuple[str, str], Parent] = {}
    for c in chunks:
        key = (c.parent_kind, c.parent_key)
        parent = grouped.get(key)
        if parent is None:
            parent = grouped[key] = Parent(kind=c.parent_kind, key=c.parent_key, source=c.source)
        parent.chunks.append(c)
    for parent in grouped.values():
        parent.chunks.sort(key=lambda c: c.position)
    return sorted(grouped.values(), key=lambda p: (p.source, natural_key(p.key)))


def assign_shards(parents: Sequence[Parent], shards: int) -> list[list[Parent]]:
    """Largest parent first into the lightest shard: balanced *reading*, not batch count."""
    if shards < 1:
        raise BuildError(f"shards must be at least 1, got {shards}")
    buckets: list[list[Parent]] = [[] for _ in range(shards)]
    load = [0] * shards
    for parent in sorted(parents, key=lambda p: (-p.chars, p.source, natural_key(p.key))):
        i = min(range(shards), key=lambda n: (load[n], n))
        buckets[i].append(parent)
        load[i] += parent.chars
    return [sorted(b, key=lambda p: (p.source, natural_key(p.key))) for b in buckets]


@dataclass
class PlannedBatch:
    shard: int
    index: int
    chunks: list[SelectedChunk]

    @property
    def batch_id(self) -> str:
        return f"{shard_name(self.shard)}/{self.index:03d}"


def envelope(
    *,
    batch_id: str,
    shard: str,
    index: int,
    chunks: Sequence[SelectedChunk],
    generated_at: str,
    schema_sha: str,
) -> dict[str, Any]:
    return BatchInput(
        batch_id=batch_id,
        shard=shard,
        index=index,
        task=TASK,
        phase=PHASE,
        generated_at=generated_at,
        schema_path=SCHEMA_REF,
        schema_sha256=schema_sha,
        chunk_count=len(chunks),
        chunks=[c.context() for c in chunks],  # type: ignore[arg-type]
    ).model_dump(mode="json")


def serialise(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def pack(
    chunks: Sequence[SelectedChunk],
    *,
    shard: int,
    batch_size: int,
    max_bytes: int = MAX_BATCH_BYTES,
    generated_at: str = "",
    schema_sha: str = "",
) -> list[PlannedBatch]:
    """Greedy packing against the *serialised* size, so "40 KB" is measured, not guessed.

    A chunk that alone exceeds the budget still gets a batch of its own: truncating it
    would break the verbatim-quote rule, which is the whole provenance story.
    """
    batches: list[PlannedBatch] = []
    current: list[SelectedChunk] = []

    def close() -> None:
        if current:
            batches.append(PlannedBatch(shard=shard, index=len(batches) + 1, chunks=list(current)))
            current.clear()

    def size(candidate: Sequence[SelectedChunk]) -> int:
        payload = envelope(
            batch_id=f"{shard_name(shard)}/{len(batches) + 1:03d}",
            shard=shard_name(shard),
            index=len(batches) + 1,
            chunks=candidate,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )
        return len(serialise(payload).encode("utf-8"))

    for chunk in chunks:
        current.append(chunk)
        if len(current) > batch_size:
            current.pop()
            close()
            current.append(chunk)
            continue
        if size(current) > max_bytes and len(current) > 1:
            current.pop()
            close()
            current.append(chunk)
    close()
    return batches


def plan_batches(
    selection: Selection, *, shards: int, batch_size: int, generated_at: str, schema_sha: str
) -> list[PlannedBatch]:
    parents = group_by_parent(selection.chunks)
    planned: list[PlannedBatch] = []
    for i, bucket in enumerate(assign_shards(parents, shards)):
        ordered = [c for parent in bucket for c in parent.chunks]
        planned.extend(
            pack(
                ordered,
                shard=i,
                batch_size=batch_size,
                generated_at=generated_at,
                schema_sha=schema_sha,
            )
        )
    return planned


# ----------------------------------------------------------------------------- writing


def longest_line_bytes(text: str) -> int:
    return max((len(line.encode("utf-8")) for line in text.splitlines()), default=0)


def write_batch_json(path: Path, payload: dict[str, Any]) -> None:
    """Indented, temp+rename, and never a line an agent's reader would truncate."""
    text = serialise(payload)
    longest = longest_line_bytes(text)
    if longest > MAX_LINE_BYTES:
        raise BuildError(
            f"{path}: longest line is {longest} bytes, over the {MAX_LINE_BYTES} an agent's "
            "reader handles. One chunk's text is too long to ship whole — a line an agent "
            "cannot read is text nobody extracts from."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def write_input_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    """Write unless the only difference from what is on disk is `generated_at`."""
    existing: Any = None
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
    if isinstance(existing, dict):
        stamp = existing.get("generated_at")
        if existing == {**payload, "generated_at": stamp}:
            return False
    write_batch_json(path, payload)
    return True


def shards_in_flight(root: Path) -> dict[str, int]:
    """Shards whose `status.json` already records finished batches, and how many."""
    busy: dict[str, int] = {}
    if not root.is_dir():
        return busy
    for path in sorted(root.glob("shard-[0-9][0-9]/" + STATUS_NAME)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        done = data.get("done") if isinstance(data, dict) else None
        if isinstance(done, list) and done:
            busy[path.parent.name] = len(done)
    return busy


def remove_stale_inputs(root: Path, planned: Sequence[PlannedBatch]) -> list[str]:
    """Delete `.in.json` files a previous, differently-shaped run left behind."""
    current = {
        str((root / shard_name(b.shard) / f"{b.index:03d}.in.json").resolve()) for b in planned
    }
    removed: list[str] = []
    if not root.is_dir():
        return removed
    for path in sorted(root.glob(f"shard-*/{IN_GLOB}")):
        if str(path.resolve()) in current:
            continue
        path.unlink()
        removed.append(str(path.relative_to(root)))
    return removed


def run_build(
    *,
    ctx: GraphContext,
    canonical_dir: Path,
    batches_dir: Path,
    reports_dir: Path,
    shards: int = DEFAULT_SHARDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    min_chars: int | None = None,
    schema_path: Path = SCHEMA_PATH,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Write `data/batches/extract/<shard>/NNN.in.json` + status + manifest. Idempotent."""
    from brain.extract import select as select_mod

    started = time.perf_counter()
    if not schema_path.is_file():
        raise BuildError(f"the output contract is missing: {schema_path}")

    root = batches_dir / TASK
    busy = shards_in_flight(root)
    if busy:
        listed = ", ".join(f"{shard} ({n} done)" for shard, n in busy.items())
        raise BuildError(
            f"refusing to rebuild: {listed} already has finished batches. An agent is "
            "working against the current inputs; merge what is done, or clear the shard's "
            "status.json."
        )

    kwargs: dict[str, Any] = {}
    if min_chars is not None:
        kwargs["min_chars"] = min_chars
    selection = select_mod.select(ctx, canonical_dir=canonical_dir, **kwargs)
    if not selection.chunks:
        raise BuildError(
            "no Phase A chunks in the graph. Has `brain chunk` run against this "
            f"{'namespace ' + ctx.prefix if ctx.prefix else 'database'}?"
        )
    commit_parents = sorted({c.parent_key for c in selection.chunks if c.parent_kind == "Commit"})
    if commit_parents:
        raise BuildError(
            f"selection returned {len(commit_parents)} commit chunks; Phase A excludes "
            "commits, whose parent_key is a bare sha and carries no context"
        )

    schema_sha = sha256_of(schema_path)
    generated_at = utc_now_iso()
    planned = plan_batches(
        selection,
        shards=shards,
        batch_size=batch_size,
        generated_at=generated_at,
        schema_sha=schema_sha,
    )

    written: list[dict[str, Any]] = []
    for batch in planned:
        shard_dir = root / shard_name(batch.shard)
        payload = envelope(
            batch_id=batch.batch_id,
            shard=shard_name(batch.shard),
            index=batch.index,
            chunks=batch.chunks,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )
        path = shard_dir / f"{batch.index:03d}.in.json"
        rewritten = write_input_if_changed(path, payload)

        status_path = shard_dir / STATUS_NAME
        if not status_path.is_file():
            write_json_atomic(
                status_path,
                ShardStatus(shard=shard_name(batch.shard)).model_dump(mode="json"),
            )

        text = path.read_text(encoding="utf-8")
        written.append(
            {
                "id": batch.batch_id,
                "path": str(path.relative_to(root)),
                "chunks": len(batch.chunks),
                "kip_sections": sum(1 for c in batch.chunks if c.source == "kip_section"),
                "issue_descriptions": sum(
                    1 for c in batch.chunks if c.source == "issue_description"
                ),
                "parents": len({c.parent_key for c in batch.chunks}),
                "chars": sum(c.char_len for c in batch.chunks),
                "token_est": sum(c.token_est for c in batch.chunks),
                "bytes": path.stat().st_size,
                "max_line_bytes": longest_line_bytes(text),
                "sha256": sha256_of(path),
                "rewritten": rewritten,
            }
        )

    stale = remove_stale_inputs(root, planned)
    for name in stale:
        echo(f"removed stale input {name} (a previous run planned more batches than this one)")

    manifest = build_manifest(
        written,
        selection=selection,
        planned=planned,
        shards=shards,
        batch_size=batch_size,
        canonical_dir=canonical_dir,
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
    echo(summarize_build(manifest))
    echo(f"manifest: {root / MANIFEST_NAME}")

    if write_report:
        write_section(reports_dir, "build", manifest)
    return manifest, (1 if oversize else 0)


def build_manifest(
    written: list[dict[str, Any]],
    *,
    selection: Selection,
    planned: Sequence[PlannedBatch],
    shards: int,
    batch_size: int,
    canonical_dir: Path,
    schema_path: Path,
    schema_sha: str,
    generated_at: str,
    stale: Sequence[str],
    prefix: str,
    duration_ms: int,
) -> dict[str, Any]:
    sizes = [b["bytes"] for b in written]
    per_shard: Counter = Counter(b["id"].split("/")[0] for b in written)
    by_shard_chunks: dict[str, int] = {}
    by_shard_tokens: dict[str, int] = {}
    for b in written:
        shard = b["id"].split("/")[0]
        by_shard_chunks[shard] = by_shard_chunks.get(shard, 0) + b["chunks"]
        by_shard_tokens[shard] = by_shard_tokens.get(shard, 0) + b["token_est"]
    return {
        "step": "extract.build",
        "phase": PHASE,
        "generated_at": generated_at,
        "duration_ms": duration_ms,
        "label_prefix": prefix,
        "schema": {"path": SCHEMA_REF, "sha256": schema_sha, "bytes": schema_path.stat().st_size},
        "examples": EXAMPLES_REF,
        "canonical_dir": str(canonical_dir),
        "selection": selection.stats,
        "sharding": {
            "shards": shards,
            "batch_size": batch_size,
            "max_batch_bytes": MAX_BATCH_BYTES,
            "max_line_bytes": MAX_LINE_BYTES,
            "rule": "a parent's chunks stay in one shard; shards balanced by characters",
            "batches_per_shard": dict(sorted(per_shard.items())),
            "chunks_per_shard": dict(sorted(by_shard_chunks.items())),
            "tokens_per_shard": dict(sorted(by_shard_tokens.items())),
            "removed_stale_inputs": list(stale),
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
            "chunks": sum(b["chunks"] for b in written),
            "kip_sections": sum(b["kip_sections"] for b in written),
            "issue_descriptions": sum(b["issue_descriptions"] for b in written),
            "token_est": sum(b["token_est"] for b in written),
            "chars": sum(b["chars"] for b in written),
            "batches_per_agent": round(len(written) / shards, 1) if shards else 0,
            "tokens_per_agent": round(sum(b["token_est"] for b in written) / shards)
            if shards
            else 0,
        },
        "batches": written,
    }


def summarize_build(manifest: dict[str, Any]) -> str:
    t = manifest["totals"]
    s = manifest["sizes"]
    sel = manifest["selection"]
    return "\n".join(
        [
            f"extract build: {t['chunks']} chunks "
            f"({t['kip_sections']} KIP sections + {t['issue_descriptions']} issue descriptions) "
            f"→ {t['batches']} batches over {manifest['sharding']['shards']} shards",
            f"sizes: max {s['max_bytes'] / 1000:.1f} KB, mean {s['mean_bytes'] / 1000:.1f} KB, "
            f"longest line {s['max_line_bytes']} B"
            + (f" — OVER BUDGET: {', '.join(s['over_budget'])}" if s["over_budget"] else ""),
            f"workload: ~{t['batches_per_agent']} batches / ~{t['tokens_per_agent']} est. tokens "
            f"per agent; {sel['parents']} parents, {sel['token_est']} est. tokens total",
        ]
    )


def load_manifest(batches_dir: Path) -> dict[str, Any] | None:
    path = batches_dir / TASK / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_section(reports_dir: Path, section: str, payload: dict[str, Any]) -> Path:
    """`data/reports/extract.json` holds both halves of the step: `build` and `merge`.

    Read-modify-write rather than two files: the conventions ask for one report per step,
    and build and merge run days apart with agents in between.
    """
    path = reports_dir / "extract.json"
    report: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                report = existing
        except (OSError, json.JSONDecodeError):
            report = {}
    report["step"] = TASK
    report["generated_at"] = utc_now_iso()
    report[section] = payload
    reports_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, report)
    return path
