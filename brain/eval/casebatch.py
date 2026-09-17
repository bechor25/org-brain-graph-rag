"""Packing evaluation *cases* into the batch files an LLM-role agent reads.

`brain/extract/build.py` already owns the batch protocol — indented JSON, 40 KB measured on
the real payload, `status.json` per shard, a MANIFEST with the schema's sha256, and a refusal
to rebuild under a working agent. This module is the same protocol for a different unit of
work: extraction ships *chunks*, Plan 3 Task 3 ships *cases* (one answer to write, or one
answer to judge), so the packing rule and the shard assignment differ while everything below
them is imported rather than retyped.

Two rules here that extraction does not need, both about what an agent must not see:

* **One group per batch.** A case's `group` is its question id. Two cases of the same
  question in one file would hand the answering agent a second retrieval's context for a
  question it is answering from the first — and would let the judge line two anonymous
  answers up against each other and guess which retrieval produced them. So a group appears
  at most once per batch, and the shard assignment spreads a group's cases round-robin
  across the shards before anything is packed.
* **Size is measured on the envelope.** A case carries a packed ~4k-token context, which is
  ~13 KB of JSON. Estimating would put six of them in a "40 KB" file that is really 80 KB
  and gets truncated at ~48 KB by the agent's reader — the failure that made step 04's
  batches unreadable. `pack` serialises the candidate envelope and reads its length.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.extract.build import (
    IN_GLOB,
    MAX_BATCH_BYTES,
    MAX_LINE_BYTES,
    OUT_GLOB,
    STATUS_NAME,
    BuildError,
    longest_line_bytes,
    serialise,
    shard_name,
)
from brain.harvest.base import write_json_atomic

__all__ = [
    "IN_GLOB",
    "MAX_BATCH_BYTES",
    "MAX_LINE_BYTES",
    "OUT_GLOB",
    "STATUS_NAME",
    "BuildError",
    "Case",
    "PlannedBatch",
    "assign_shards",
    "batch_ids",
    "case_bytes",
    "clear_failure_files",
    "errors_by_index",
    "handle_failure",
    "longest_line_bytes",
    "pack",
    "read_outputs",
    "serialise",
    "shard_name",
    "write_batches",
    "write_pretty",
]

#: `retry/` twice, then `quarantine/` — the same three-strike rule `brain resolve merge`
#: uses, with the attempt counter inside the moved file so no ledger is needed.
MAX_RETRIES = 2
RETRY_DIR = "retry"
QUARANTINE_DIR = "quarantine"


@dataclass(frozen=True)
class Case:
    """One unit of agent work, and the question it belongs to.

    `group` is never written into the payload by this module — it is the packer's key, and
    for the judge it is exactly the fact the batch must not carry.
    """

    case_id: str
    group: str
    payload: dict[str, Any]


@dataclass
class PlannedBatch:
    shard: int
    index: int
    cases: list[Case] = field(default_factory=list)

    @property
    def batch_id(self) -> str:
        return f"{shard_name(self.shard)}/{self.index:03d}"

    @property
    def groups(self) -> set[str]:
        return {c.group for c in self.cases}


def case_bytes(case: Case) -> int:
    """The serialised size of one case on its own — what shard balancing sorts on."""
    return len(json.dumps(case.payload, ensure_ascii=False, indent=2).encode("utf-8"))


def assign_shards(cases: Sequence[Case], shards: int) -> list[list[Case]]:
    """Spread each group round-robin, then balance the rest by bytes.

    Round-robin first so a question's cases land in different shards whenever there are at
    least as many shards as cases in the group; the byte balance then only decides between
    shards that are equally acceptable.
    """
    if shards < 1:
        raise BuildError(f"shards must be at least 1, got {shards}")
    buckets: list[list[Case]] = [[] for _ in range(shards)]
    load = [0] * shards
    seen: dict[str, int] = {}
    for case in cases:
        position = seen.get(case.group, 0)
        seen[case.group] = position + 1
        # Every shard this group has not filled yet, preferring the lightest.
        used = {i for i, bucket in enumerate(buckets) if any(c.group == case.group for c in bucket)}
        open_shards = [i for i in range(shards) if i not in used] or list(range(shards))
        start = position % shards
        target = min(open_shards, key=lambda i: (load[i], (i - start) % shards))
        buckets[target].append(case)
        load[target] += case_bytes(case)
    return buckets


def interleave(cases: Sequence[Case]) -> list[Case]:
    """Order a shard so two cases of one group are as far apart as the shard allows."""
    rounds: dict[int, list[Case]] = {}
    seen: dict[str, int] = {}
    for case in cases:
        position = seen.get(case.group, 0)
        seen[case.group] = position + 1
        rounds.setdefault(position, []).append(case)
    return [case for position in sorted(rounds) for case in rounds[position]]


def pack(
    cases: Sequence[Case],
    *,
    shard: int,
    batch_size: int,
    envelope: Callable[[int, Sequence[Case]], dict[str, Any]],
    max_bytes: int = MAX_BATCH_BYTES,
) -> list[PlannedBatch]:
    """Greedy packing against the serialised envelope, one group per batch.

    A case that alone exceeds the budget still gets a batch of its own: the alternative is
    truncating a context, and an answer written against half a context measures nothing.
    """
    batches: list[PlannedBatch] = []
    current: list[Case] = []
    pending: list[Case] = []

    def close() -> None:
        if current:
            batches.append(PlannedBatch(shard=shard, index=len(batches) + 1, cases=list(current)))
            current.clear()

    def size(candidate: Sequence[Case]) -> int:
        return len(serialise(envelope(len(batches) + 1, candidate)).encode("utf-8"))

    queue = list(interleave(cases))
    while queue or pending:
        if not queue:  # only deferred cases left: they start the next batch
            close()
            queue, pending = pending, []
            continue
        case = queue.pop(0)
        if any(c.group == case.group for c in current):
            pending.append(case)  # same question, different retrieval — never side by side
            continue
        current.append(case)
        if len(current) > batch_size or (size(current) > max_bytes and len(current) > 1):
            current.pop()
            close()
            current.append(case)
    close()
    return batches


def write_batches(
    root: Path,
    planned: Sequence[PlannedBatch],
    *,
    envelope: Callable[[PlannedBatch], dict[str, Any]],
    status_extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Write `<shard>/NNN.in.json` + a `status.json` per shard. Returns one row per batch."""
    from brain.extract.build import sha256_of, write_input_if_changed

    written: list[dict[str, Any]] = []
    for batch in planned:
        shard_dir = Path(root) / shard_name(batch.shard)
        path = shard_dir / f"{batch.index:03d}.in.json"
        rewritten = write_input_if_changed(path, envelope(batch))
        status_path = shard_dir / STATUS_NAME
        if not status_path.is_file():
            write_json_atomic(
                status_path,
                {
                    "shard": shard_name(batch.shard),
                    "done": [],
                    "failed": [],
                    **(status_extra or {}),
                },
            )
        text = path.read_text(encoding="utf-8")
        written.append(
            {
                "id": batch.batch_id,
                "path": str(path.relative_to(root)),
                "cases": len(batch.cases),
                "case_ids": [c.case_id for c in batch.cases],
                "bytes": path.stat().st_size,
                "max_line_bytes": longest_line_bytes(text),
                "sha256": sha256_of(path),
                "rewritten": rewritten,
            }
        )
    return written


def batch_ids(root: Path) -> list[str]:
    """Every batch the current inputs plan, as `shard-NN/NNN`."""
    root = Path(root)
    if not root.is_dir():
        return []
    return [
        f"{p.parent.name}/{p.name.split('.', 1)[0]}"
        for p in sorted(root.glob(f"shard-*/{IN_GLOB}"))
    ]


def remove_stale_files(root: Path, planned: Sequence[PlannedBatch]) -> dict[str, list[str]]:
    """Delete inputs no plan asks for; move orphaned outputs aside rather than delete them."""
    root = Path(root)
    planned_ids = {b.batch_id for b in planned}
    removed: list[str] = []
    moved: list[str] = []
    if not root.is_dir():
        return {"removed_inputs": removed, "moved_outputs": moved}
    for path in sorted(root.glob(f"shard-*/{IN_GLOB}")):
        batch_id = f"{path.parent.name}/{path.name.split('.', 1)[0]}"
        if batch_id in planned_ids:
            continue
        path.unlink()
        removed.append(str(path.relative_to(root)))
    for path in sorted(root.glob(f"shard-*/{OUT_GLOB}")):
        batch_id = f"{path.parent.name}/{path.name.split('.', 1)[0]}"
        if batch_id in planned_ids:
            continue
        target = path.parent / "stale" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        moved.append(str(target.relative_to(root)))
    return {"removed_inputs": removed, "moved_outputs": moved}


# ----------------------------------------------------------------------------- reading


@dataclass
class Batch:
    """One `<NNN>.out.json`, the input it answers, and why it may have been refused."""

    batch_id: str
    shard: str
    path: Path
    in_path: Path
    output: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return exc


def read_outputs(root: Path, *, array_field: str) -> list[Batch]:
    """Every output beside its input, with envelope-level problems already recorded.

    Envelope refusals only: a file that is unreadable, is not an object, names a batch that
    is not its own, or has no input beside it cannot be trusted at all — including the parts
    that look fine. One bad *case* costs that case, and is decided by the caller.
    """
    out: list[Batch] = []
    root = Path(root)
    if not root.is_dir():
        return out
    for path in sorted(root.glob(f"shard-*/{OUT_GLOB}")):
        batch_id = f"{path.parent.name}/{path.name.split('.', 1)[0]}"
        in_path = path.with_name(path.name.replace(".out.json", ".in.json"))
        batch = Batch(batch_id=batch_id, shard=path.parent.name, path=path, in_path=in_path)
        if not in_path.is_file():
            batch.errors.append(f"no input beside it ({in_path.name})")
            out.append(batch)
            continue
        data, source = _read_json(path), _read_json(in_path)
        for name, value in (("output", data), ("input", source)):
            if isinstance(value, Exception):
                batch.errors.append(f"unreadable {name} JSON: {value}")
            elif not isinstance(value, dict):
                batch.errors.append(f"the {name}'s top level is not an object")
        if batch.errors:
            out.append(batch)
            continue
        batch.output, batch.source = data, source  # type: ignore[assignment]
        if batch.output.get("batch_id") != batch_id:
            batch.errors.append(
                f"batch_id {batch.output.get('batch_id')!r} is not this file's batch"
            )
        if not isinstance(batch.output.get(array_field), list):
            batch.errors.append(f"`{array_field}` is not an array")
        out.append(batch)
    return out


def handle_failure(batch: Batch, *, retry_field: str, errors_field: str) -> dict[str, Any]:
    """`retry/` twice, then `quarantine/`. The counter lives inside the moved file."""
    raw = batch.output if isinstance(batch.output, dict) and batch.output else {}
    if not raw:
        loaded = _read_json(batch.path)
        raw = loaded if isinstance(loaded, dict) else {}
    raw = dict(raw)
    attempts = int(raw.get(retry_field, 0)) + 1
    target_dir = batch.path.parent / (RETRY_DIR if attempts <= MAX_RETRIES else QUARANTINE_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / batch.path.name
    raw[retry_field] = attempts
    raw[errors_field] = batch.errors[:20]
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    batch.path.unlink(missing_ok=True)
    return {
        "batch": batch.batch_id,
        "attempts": attempts,
        "where": target_dir.name,
        "errors": batch.errors[:20],
        "path": str(target),
    }


def clear_failure_files(batch: Batch) -> None:
    """A batch that now validates leaves no retry/quarantine copy behind."""
    for name in (RETRY_DIR, QUARANTINE_DIR):
        stale = batch.path.parent / name / batch.path.name
        stale.unlink(missing_ok=True)


def errors_by_index(errors: Sequence[str], field: str) -> tuple[list[str], dict[int, list[str]]]:
    """Split validator messages into envelope problems and per-record ones.

    `brain/common/jsonschema_mini.validate` reports `$.answers[3].confidence: …`. The index
    is what decides whether one bad record costs the record or the whole batch, so it is
    parsed here once rather than re-derived by each merge.
    """
    prefix = f"$.{field}["
    envelope: list[str] = []
    per: dict[int, list[str]] = {}
    for message in errors:
        if not message.startswith(prefix):
            envelope.append(message)
            continue
        head = message[len(prefix) :].split("]", 1)[0]
        if head.isdigit():
            per.setdefault(int(head), []).append(message)
        else:  # pragma: no cover - the validator always emits a numeric index
            envelope.append(message)
    return envelope, per


def write_pretty(path: Path, payload: Any) -> Path:
    """Indented JSON via temp+rename. What a person, not only a parser, will open."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def read_status(root: Path) -> dict[str, dict[str, Any]]:
    """Each shard's `status.json` — what the agent says it finished and what it refused."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(root).glob(f"shard-[0-9][0-9]/{STATUS_NAME}")):
        data = _read_json(path)
        if isinstance(data, dict):
            out[path.parent.name] = data
    return out
