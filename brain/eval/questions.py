"""`brain eval questions build` — the deficit table, the sampled paths, and the batches.

The build answers one question: *given the 19 competency questions Plan 2 already has, what
exactly is missing?* Everything else follows from that answer — which shapes to sample, how
many paths of each, what to ask `question-forger` for, and how to check afterwards that it
delivered.

**The target is water-filled, not asserted.** Plan 3 asks for "32 questions, 8 per type,
≥11 Hebrew". Five question types × 8 is 40, so with 13 new questions 8-per-type is
arithmetically out of reach; pretending otherwise would mean silently favouring some types.
So the build states the goal (`per_type_goal`), says it is unreachable and what it would
cost (`questions_for_goal`), and fills the 13 by raising the *lowest* type first — which is
the same instinct 8-per-type had, applied to the budget that exists. `global` starts at 1
of 19 and gets 5 of the 13 for exactly that reason.

**Language is a second water-fill, capped by the first.** A type cannot receive more Hebrew
questions than it receives questions. Filling Hebrew lowest-first inside that cap is what
puts at least one Hebrew question in every type instead of eleven in the two biggest.

**The request carries slack.** A question the merge rejects (leakage, an evidence key that
does not exist) costs its cell, and a cell short by one unbalances the set. So the forger is
asked for 30% more than the deficit and the merge keeps the best `need` of each cell.

The batch protocol is the one the other LLM-role steps already use, imported rather than
re-implemented: indented JSON, 40 KB measured on the real payload, `status.json` per shard,
a MANIFEST with sha256 of the schema, and a refusal to rebuild under an agent's feet.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.eval import paths as paths_mod
from brain.eval.paths import QUESTION_TYPES, PathSample
from brain.extract.build import (
    IN_GLOB,
    MANIFEST_NAME,
    MAX_BATCH_BYTES,
    MAX_LINE_BYTES,
    STATUS_NAME,
    BuildError,
    longest_line_bytes,
    serialise,
    sha256_of,
    shard_name,
    shards_in_flight,
    write_input_if_changed,
)
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.retrieve.context import RetrieveContext

TASK = "questions"
BATCH_DIR = "questions"
SCHEMA_PATH = Path(__file__).with_name("question_schema.json")
SCHEMA_REF = "brain/eval/question_schema.json"
TEMPLATES_REF = "brain/eval/templates.md"
AGENT_REF = ".claude/agents/question-forger.md"

LANGS: tuple[str, str] = ("he", "en")

#: Plan 3 decision 1: 32 total, of which 13 are new, at least 11 in Hebrew, 8 per type.
DEFAULT_NEW_TOTAL = 13
DEFAULT_SHARDS = 2
DEFAULT_HEBREW_MIN = 11
PER_TYPE_GOAL = 8
#: How much more than the deficit to ask for, so one rejection does not empty a cell.
DEFAULT_SLACK = 0.30
#: Spare paths per BATCH — a fallback the forger may substitute in when a sampled path turns
#: out to make only unfair questions. Per batch rather than per type: shard-01/003 shipped
#: with two paths and both asked for, so an unusable one had nothing to replace it.
DEFAULT_SPARES = 1
#: Room kept free in each batch while packing the asked-for paths, so the spare added
#: afterwards fits. A little over the largest path measured on the real corpus (10.9 KB).
SPARE_RESERVE_BYTES = 12_000

#: `q001`…; each shard gets a disjoint block so two agents never collide on an id.
ID_BLOCK = 100


# --------------------------------------------------------------------------- the deficit


def water_fill(
    existing: Sequence[int], add: int, capacity: Sequence[int] | None = None
) -> list[int]:
    """Add `add` units, always to the lowest bucket, ties going to the earliest one.

    The result is the most even distribution reachable from `existing` — which is what
    "8 per type" wants and cannot have. `capacity[i]` caps how many units bucket `i` may
    *receive*; a bucket at capacity is skipped, and when every bucket is full the rest of
    `add` is simply not placed (the caller reports the shortfall rather than inventing
    room for it).
    """
    counts = [int(v) for v in existing]
    caps = [int(c) for c in capacity] if capacity is not None else [add] * len(counts)
    if len(caps) != len(counts):
        raise ValueError("capacity must have one entry per bucket")
    added = [0] * len(counts)
    for _ in range(int(add)):
        open_buckets = [i for i in range(len(counts)) if added[i] < caps[i]]
        if not open_buckets:
            break
        target = min(open_buckets, key=lambda i: (counts[i], i))
        counts[target] += 1
        added[target] += 1
    return counts


def count_existing(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], int]:
    """Every (type, language) cell, including the empty ones — a zero is a deficit."""
    counts = {(t, lang): 0 for t in QUESTION_TYPES for lang in LANGS}
    for row in rows:
        key = (str(row.get("type")), str(row.get("lang")))
        if key in counts:
            counts[key] += 1
    return counts


@dataclass(frozen=True)
class Cell:
    """One (type, language) cell of the deficit table."""

    type: str
    lang: str
    existing: int
    need: int
    request: int

    @property
    def final(self) -> int:
        return self.existing + self.need

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "lang": self.lang,
            "existing": self.existing,
            "need": self.need,
            "request": self.request,
            "final": self.final,
        }


@dataclass
class Demand:
    """What the build asks `question-forger` for, and the arithmetic behind it."""

    cells: list[Cell]
    existing_total: int
    new_total: int
    per_type_goal: int
    hebrew_min: int
    slack: float
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ views

    @property
    def need_by_type(self) -> dict[str, int]:
        return {t: sum(c.need for c in self.cells if c.type == t) for t in QUESTION_TYPES}

    @property
    def request_by_type(self) -> dict[str, int]:
        return {t: sum(c.request for c in self.cells if c.type == t) for t in QUESTION_TYPES}

    @property
    def final_by_type(self) -> dict[str, int]:
        return {t: sum(c.final for c in self.cells if c.type == t) for t in QUESTION_TYPES}

    @property
    def final_by_cell(self) -> dict[tuple[str, str], int]:
        return {(c.type, c.lang): c.final for c in self.cells}

    @property
    def hebrew_final(self) -> int:
        return sum(c.final for c in self.cells if c.lang == "he")

    @property
    def questions_for_goal(self) -> int:
        return self.per_type_goal * len(QUESTION_TYPES)

    @property
    def goal_reachable(self) -> bool:
        return self.existing_total + self.new_total >= self.questions_for_goal

    def paths_wanted(self, spares: int = 0) -> dict[str, int]:
        """Paths to sample per type: one per requested question, plus `spares` if asked.

        The build calls this with 0. Spares are per batch now, not per type, and a batch
        does not exist until the asked-for paths have been packed — see `run_build`.
        """
        return {t: n + spares for t, n in self.request_by_type.items() if n + spares > 0}

    def as_dict(self) -> dict[str, Any]:
        return {
            "existing_total": self.existing_total,
            "new_total": self.new_total,
            "final_total": self.existing_total + self.new_total,
            "per_type_goal": self.per_type_goal,
            "goal_reachable": self.goal_reachable,
            "questions_for_goal": self.questions_for_goal,
            "hebrew_min": self.hebrew_min,
            "hebrew_final": self.hebrew_final,
            "slack": self.slack,
            "requested_total": sum(c.request for c in self.cells),
            "need_by_type": self.need_by_type,
            "final_by_type": self.final_by_type,
            "cells": [c.as_dict() for c in self.cells],
            "notes": list(self.notes),
        }


def plan_demand(
    existing_rows: Sequence[Mapping[str, Any]],
    *,
    new_total: int = DEFAULT_NEW_TOTAL,
    hebrew_min: int = DEFAULT_HEBREW_MIN,
    per_type_goal: int = PER_TYPE_GOAL,
    slack: float = DEFAULT_SLACK,
) -> Demand:
    """The deficit table: what to ask for, per type and per language, and why."""
    counts = count_existing(existing_rows)
    existing_total = sum(counts.values())
    by_type = [sum(counts[(t, lang)] for lang in LANGS) for t in QUESTION_TYPES]
    by_type_he = [counts[(t, "he")] for t in QUESTION_TYPES]

    final_type = water_fill(by_type, new_total)
    need_type = [final_type[i] - by_type[i] for i in range(len(QUESTION_TYPES))]

    hebrew_existing = sum(by_type_he)
    hebrew_need = max(0, hebrew_min - hebrew_existing)
    final_he = water_fill(by_type_he, hebrew_need, capacity=need_type)
    need_he = [final_he[i] - by_type_he[i] for i in range(len(QUESTION_TYPES))]

    cells: list[Cell] = []
    for i, qtype in enumerate(QUESTION_TYPES):
        cells.append(Cell(qtype, "he", counts[(qtype, "he")], need_he[i], need_he[i]))
        en_need = need_type[i] - need_he[i]
        cells.append(Cell(qtype, "en", counts[(qtype, "en")], en_need, en_need))
    cells = _add_slack(cells, new_total, slack)

    notes: list[str] = []
    total = existing_total + new_total
    if total < per_type_goal * len(QUESTION_TYPES):
        notes.append(
            f"The plan's goal of {per_type_goal} per type over {len(QUESTION_TYPES)} types needs "
            f"{per_type_goal * len(QUESTION_TYPES)} questions; this set has {total} "
            f"({existing_total} existing + {new_total} new). The {new_total} new questions are "
            "water-filled onto the lowest types instead, which is the most even split "
            f"reachable: {dict(zip(QUESTION_TYPES, final_type, strict=True))}."
        )
    hebrew_final = hebrew_existing + sum(need_he)
    if hebrew_final < hebrew_min:
        notes.append(
            f"Hebrew floor {hebrew_min} is out of reach: {hebrew_existing} existing + at most "
            f"{new_total} new = {hebrew_final}. Every new question is Hebrew and it is still short."
        )
    return Demand(
        cells=cells,
        existing_total=existing_total,
        new_total=new_total,
        per_type_goal=per_type_goal,
        hebrew_min=hebrew_min,
        slack=slack,
        notes=notes,
    )


def _add_slack(cells: Sequence[Cell], new_total: int, slack: float) -> list[Cell]:
    """Spread `ceil(new_total * slack)` extra requests over the cells that need most."""
    extra = math.ceil(new_total * (1 + slack)) - new_total
    out = {(c.type, c.lang): c for c in cells}
    order = sorted(
        (c for c in cells if c.need > 0),
        key=lambda c: (-c.need, QUESTION_TYPES.index(c.type), LANGS.index(c.lang)),
    )
    for i in range(int(extra)):
        if not order:
            break
        target = order[i % len(order)]
        current = out[(target.type, target.lang)]
        out[(target.type, target.lang)] = Cell(
            current.type, current.lang, current.existing, current.need, current.request + 1
        )
    return [out[(c.type, c.lang)] for c in cells]


# ----------------------------------------------------------------------------- the asks


def lang_sequence(he: int, en: int) -> list[str]:
    """Interleave the two languages so one shape does not end up entirely Hebrew."""
    out: list[str] = []
    left, right = he, en
    while left or right:
        if left:
            out.append("he")
            left -= 1
        if right:
            out.append("en")
            right -= 1
    return out


def assign_asks(samples: Sequence[PathSample], demand: Demand) -> dict[str, int]:
    """Attach `asks[]` to paths — which question, in which language, from which path.

    One ask per path while there are paths to spare; the overflow doubles up on the richest
    paths (the ones the `select` superlative ranked first). A path left with no ask is a
    `spare`, which the forger may substitute in if one of the others turns out unfair.
    """
    assigned: dict[str, int] = {}
    for qtype in QUESTION_TYPES:
        pool = [p for p in samples if p.question_type == qtype]
        for p in pool:
            p.asks = []
        he = sum(c.request for c in demand.cells if c.type == qtype and c.lang == "he")
        en = sum(c.request for c in demand.cells if c.type == qtype and c.lang == "en")
        wanted = lang_sequence(he, en)
        assigned[qtype] = len(wanted)
        if not pool:
            continue
        for i, lang in enumerate(wanted):
            pool[i % len(pool)].asks.append({"lang": lang, "questions": 1})
    return assigned


# -------------------------------------------------------------------------- the batches


@dataclass
class PlannedBatch:
    shard: int
    index: int
    paths: list[PathSample]

    @property
    def batch_id(self) -> str:
        return f"{shard_name(self.shard)}/{self.index:03d}"


def envelope(
    *,
    batch: PlannedBatch,
    demand: Demand,
    generated_at: str,
    schema_sha: str,
) -> dict[str, Any]:
    """What one `.in.json` says. Every field the forger needs, and nothing it must not see."""
    asks = [
        {
            "type": p.question_type,
            "lang": a["lang"],
            "questions": a["questions"],
            "path_id": p.path_id,
        }
        for p in batch.paths
        for a in p.asks
    ]
    first = ID_BLOCK * batch.shard + 1
    return {
        "batch_id": batch.batch_id,
        "shard": shard_name(batch.shard),
        "index": batch.index,
        "task": TASK,
        "generated_at": generated_at,
        "schema_path": SCHEMA_REF,
        "schema_sha256": schema_sha,
        "templates_path": TEMPLATES_REF,
        "agent": AGENT_REF,
        "id_range": {
            "first": f"q{first:03d}",
            "last": f"q{first + ID_BLOCK - 1:03d}",
            "note": "Use ids from this range only; each shard owns a disjoint block.",
        },
        "questions_requested": len(asks),
        "asks": asks,
        "path_count": len(batch.paths),
        "paths": [p.context() for p in batch.paths],
    }


def pack(
    samples: Sequence[PathSample],
    *,
    shard: int,
    demand: Demand,
    max_bytes: int = MAX_BATCH_BYTES,
    generated_at: str = "",
    schema_sha: str = "",
) -> list[PlannedBatch]:
    """Greedy packing against the *serialised* size, so 40 KB is measured, not guessed.

    Callers pack the asked-for paths against `MAX_BATCH_BYTES - SPARE_RESERVE_BYTES` and
    add the spare afterwards; see `place_spares`.
    """
    batches: list[PlannedBatch] = []
    current: list[PathSample] = []

    def close() -> None:
        if current:
            batches.append(PlannedBatch(shard=shard, index=len(batches) + 1, paths=list(current)))
            current.clear()

    def size(candidate: Sequence[PathSample]) -> int:
        payload = envelope(
            batch=PlannedBatch(shard=shard, index=len(batches) + 1, paths=list(candidate)),
            demand=demand,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )
        return len(serialise(payload).encode("utf-8"))

    for sample in samples:
        current.append(sample)
        if size(current) > max_bytes and len(current) > 1:
            current.pop()
            close()
            current.append(sample)
    close()
    return batches


def batch_bytes(batch: PlannedBatch, *, demand: Demand, generated_at: str, schema_sha: str) -> int:
    payload = envelope(batch=batch, demand=demand, generated_at=generated_at, schema_sha=schema_sha)
    return len(serialise(payload).encode("utf-8"))


def place_spares(
    planned: Sequence[PlannedBatch],
    spares: Sequence[PathSample],
    *,
    demand: Demand,
    generated_at: str,
    schema_sha: str,
    max_bytes: int = MAX_BATCH_BYTES,
) -> tuple[list[str], list[PathSample]]:
    """One spare per batch, preferring a spare of a type that batch already asks about.

    Per batch, not per type: shard-01/003 shipped with two paths and both asked for, so a
    path that turned out unusable would have had nothing to replace it. A spare of the same
    type is worth more than any spare — the forger substituting it still owes the same
    question — so type is matched first and anything left over fills the rest.
    """
    pool = list(spares)
    without: list[str] = []
    for batch in planned:
        types = [p.question_type for p in batch.paths]
        ordered = sorted(pool, key=lambda p: (p.question_type not in types, p.path_id))
        for candidate in ordered:
            batch.paths.append(candidate)
            if (
                batch_bytes(batch, demand=demand, generated_at=generated_at, schema_sha=schema_sha)
                <= max_bytes
            ):
                pool.remove(candidate)
                break
            batch.paths.pop()
        else:
            without.append(batch.batch_id)
    return without, pool


def assign_shards(samples: Sequence[PathSample], shards: int) -> list[list[PathSample]]:
    """Spread the types evenly over the shards, heaviest path first inside each type.

    Round-robin over the *type-ordered* list rather than a straight split: a shard whose
    every path is a community is a shard whose agent only ever writes global questions,
    and two agents that never see the same shape cannot be compared.
    """
    buckets: list[list[PathSample]] = [[] for _ in range(max(1, shards))]
    ordered = sorted(
        samples,
        key=lambda p: (QUESTION_TYPES.index(p.question_type), p.shape, p.path_key),
    )
    for i, sample in enumerate(ordered):
        buckets[i % len(buckets)].append(sample)
    return buckets


def plan_batches(
    samples: Sequence[PathSample],
    *,
    shards: int,
    demand: Demand,
    generated_at: str,
    schema_sha: str,
    max_bytes: int = MAX_BATCH_BYTES,
) -> list[PlannedBatch]:
    planned: list[PlannedBatch] = []
    for i, bucket in enumerate(assign_shards(samples, shards)):
        planned.extend(
            pack(
                bucket,
                shard=i,
                demand=demand,
                max_bytes=max_bytes,
                generated_at=generated_at,
                schema_sha=schema_sha,
            )
        )
    return planned


def drop_snippetless(samples: Sequence[PathSample]) -> tuple[list[PathSample], list[str]]:
    """A path with no text is a path no answer can be quoted from. It does not ship.

    The global questions are why this exists: a community whose ten shown members happened
    to carry no chunks arrived with `snippets: []`, and a question written from a summary
    nobody can quote cannot be graded on faithfulness to its context.
    """
    kept = [p for p in samples if p.snippets]
    return kept, [p.path_id for p in samples if not p.snippets]


def remove_stale_files(root: Path, planned: Sequence[PlannedBatch]) -> dict[str, list[str]]:
    """Delete inputs no longer planned; *move* orphaned outputs aside, never delete them.

    Same rule as `brain/extract/build.py`: an `.out.json` is an agent's work, and an
    orphan left in place would be read as an answer to whatever `NNN.in.json` now holds.
    """
    from brain.extract.build import OUT_GLOB, STALE_DIR

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


def _sample_spares(
    ctx: RetrieveContext,
    planned: Sequence[PlannedBatch],
    *,
    per_batch: int,
    taken: set[str],
    seed: int,
    truth: Mapping[str, Any],
) -> list[PathSample]:
    """As many further paths as there are batch slots, of the types those batches ask about."""
    wanted: dict[str, int] = {}
    for batch in planned:
        types = sorted({p.question_type for p in batch.paths}, key=QUESTION_TYPES.index)
        for i in range(per_batch):
            qtype = types[i % len(types)] if types else QUESTION_TYPES[0]
            wanted[qtype] = wanted.get(qtype, 0) + 1
    # A different seed from phase one, so the spare pool is not the same pick re-filtered.
    return paths_mod.sample(ctx, wanted, seed=seed + 101, truth=dict(truth), exclude=taken)


# ------------------------------------------------------------------------------- runner


def run_build(
    *,
    ctx: RetrieveContext,
    batches_dir: Path,
    reports_dir: Path,
    existing_rows: Sequence[Mapping[str, Any]],
    new_total: int = DEFAULT_NEW_TOTAL,
    shards: int = DEFAULT_SHARDS,
    hebrew_min: int = DEFAULT_HEBREW_MIN,
    slack: float = DEFAULT_SLACK,
    spares: int = DEFAULT_SPARES,
    seed: int = paths_mod.DEFAULT_SEED,
    truth_path: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Sample the paths, write the batches, write MANIFEST.json and the report section."""
    started = time.monotonic()
    generated_at = utc_now_iso()
    root = Path(batches_dir) / BATCH_DIR
    schema_sha = sha256_of(SCHEMA_PATH)

    busy = shards_in_flight(root)
    if busy and not force:
        detail = ", ".join(f"{k} ({v})" for k, v in sorted(busy.items()))
        raise BuildError(
            f"{detail} — a question-forger has already answered against the current inputs. "
            "Rebuilding would change the questions under it. Merge first, or pass --force."
        )

    demand = plan_demand(existing_rows, new_total=new_total, hebrew_min=hebrew_min, slack=slack)
    truth = paths_mod.load_truth(truth_path)

    # Two phases, because "one spare per batch" cannot be planned before the batches exist.
    # Phase one packs only the paths questions are asked about, against a reduced budget;
    # phase two samples exactly as many further paths as there are batches and drops one
    # into each. `exclude` keeps a batch from being handed a spare it is already asking about.
    samples = paths_mod.sample(ctx, demand.paths_wanted(0), seed=seed, truth=truth)
    if not samples:
        raise BuildError("no paths sampled — is the graph loaded? (`brain load`, `brain extract`)")
    paths_mod.attach_snippets(ctx, samples)
    samples, snippetless = drop_snippetless(samples)
    if not samples:
        raise BuildError(
            "every sampled path came back without a single chunk to quote — has `brain chunk` run?"
        )
    assigned = assign_asks(samples, demand)

    planned = plan_batches(
        samples,
        shards=shards,
        demand=demand,
        generated_at=generated_at,
        schema_sha=schema_sha,
        max_bytes=MAX_BATCH_BYTES - SPARE_RESERVE_BYTES,
    )
    spare_paths: list[PathSample] = []
    without_spare: list[str] = []
    if spares > 0 and planned:
        spare_paths = _sample_spares(
            ctx,
            planned,
            per_batch=spares,
            taken={p.path_key for p in samples},
            seed=seed,
            truth=truth,
        )
        paths_mod.attach_snippets(ctx, spare_paths)
        spare_paths, spare_snippetless = drop_snippetless(spare_paths)
        snippetless.extend(spare_snippetless)
        without_spare, unplaced = place_spares(
            planned,
            spare_paths,
            demand=demand,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )
        spare_paths = [p for p in spare_paths if p not in unplaced]
    samples = [*samples, *spare_paths]
    snippet_total = sum(len(p.snippets) for p in samples)
    stale = remove_stale_files(root, planned)

    written: list[dict[str, Any]] = []
    for batch in planned:
        shard_dir = root / shard_name(batch.shard)
        shard_dir.mkdir(parents=True, exist_ok=True)
        payload = envelope(
            batch=batch, demand=demand, generated_at=generated_at, schema_sha=schema_sha
        )
        path = shard_dir / f"{batch.index:03d}.in.json"
        write_input_if_changed(path, payload)
        status_path = shard_dir / STATUS_NAME
        if not status_path.is_file():
            write_json_atomic(
                status_path, {"shard": shard_name(batch.shard), "done": [], "failed": []}
            )
        text = path.read_text(encoding="utf-8")
        written.append(
            {
                "id": batch.batch_id,
                "path": str(path.relative_to(root)),
                "paths": len(batch.paths),
                "questions_requested": sum(len(p.asks) for p in batch.paths),
                "spare_paths": sum(1 for p in batch.paths if p.spare),
                "snippets": sum(len(p.snippets) for p in batch.paths),
                "types": sorted({p.question_type for p in batch.paths}),
                "bytes": len(text.encode("utf-8")),
                "max_line_bytes": longest_line_bytes(text),
            }
        )

    manifest = build_manifest(
        written,
        demand=demand,
        samples=samples,
        assigned=assigned,
        snippet_total=snippet_total,
        shards=shards,
        seed=seed,
        schema_sha=schema_sha,
        generated_at=generated_at,
        stale=stale,
        prefix=ctx.prefix,
        snippetless=snippetless,
        without_spare=without_spare,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    write_json_atomic(root / MANIFEST_NAME, manifest)
    write_section(Path(reports_dir), "build", manifest)
    return manifest


def build_manifest(
    written: Sequence[Mapping[str, Any]],
    *,
    demand: Demand,
    samples: Sequence[PathSample],
    assigned: Mapping[str, int],
    snippet_total: int,
    shards: int,
    seed: int,
    schema_sha: str,
    generated_at: str,
    stale: Mapping[str, list[str]],
    prefix: str,
    duration_ms: int,
    snippetless: Sequence[str] = (),
    without_spare: Sequence[str] = (),
) -> dict[str, Any]:
    sizes = [int(b["bytes"]) for b in written]
    by_shape: dict[str, int] = {}
    for p in samples:
        by_shape[p.shape] = by_shape.get(p.shape, 0) + 1
    by_type: dict[str, int] = {}
    for p in samples:
        by_type[p.question_type] = by_type.get(p.question_type, 0) + 1
    return {
        "step": "eval.questions.build",
        "generated_at": generated_at,
        "duration_ms": duration_ms,
        "label_prefix": prefix,
        "seed": seed,
        "schema": {"path": SCHEMA_REF, "sha256": schema_sha},
        "templates": TEMPLATES_REF,
        "agent": AGENT_REF,
        "demand": demand.as_dict(),
        "paths": {
            "total": len(samples),
            "by_type": {t: by_type.get(t, 0) for t in QUESTION_TYPES},
            "by_shape": dict(sorted(by_shape.items())),
            "by_gold_source": _tally(p.gold_source for p in samples),
            "spares": sum(1 for p in samples if p.spare),
            "snippets": snippet_total,
            "snippet_chars": paths_mod.SNIPPET_CHARS,
            "dropped_without_snippets": list(snippetless),
            "ids": [p.path_id for p in samples],
        },
        "questions_requested": {
            "total": sum(int(b["questions_requested"]) for b in written),
            "by_type": dict(sorted(assigned.items())),
        },
        "sharding": {
            "shards": shards,
            "max_batch_bytes": MAX_BATCH_BYTES,
            "max_line_bytes": MAX_LINE_BYTES,
            "rule": "types spread round-robin over shards; packed to a measured 40 KB",
            "spare_reserve_bytes": SPARE_RESERVE_BYTES,
            "batches_without_spare": list(without_spare),
            "removed_stale_inputs": list(stale.get("removed_inputs", [])),
            "moved_stale_outputs": list(stale.get("moved_outputs", [])),
        },
        "sizes": {
            "max_bytes": max(sizes) if sizes else 0,
            "min_bytes": min(sizes) if sizes else 0,
            "mean_bytes": round(sum(sizes) / len(sizes)) if sizes else 0,
            "total_bytes": sum(sizes),
            "max_line_bytes": max((int(b["max_line_bytes"]) for b in written), default=0),
            "over_budget": [b["id"] for b in written if int(b["bytes"]) > MAX_BATCH_BYTES],
        },
        "totals": {"batches": len(written)},
        "batches": [dict(b) for b in written],
    }


def _tally(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def summarize_build(manifest: Mapping[str, Any]) -> str:
    d = manifest["demand"]
    p = manifest["paths"]
    s = manifest["sizes"]
    lines = [
        f"questions build: {p['total']} paths ({p['spares']} spare, {p['snippets']} snippets) "
        f"→ {manifest['totals']['batches']} batches over {manifest['sharding']['shards']} shards",
        f"deficit: {d['existing_total']} existing + {d['new_total']} new = {d['final_total']}; "
        f"asking for {d['requested_total']} (slack {d['slack']:.0%}); "
        f"Hebrew {d['hebrew_final']}/{d['hebrew_min']}",
        "  " + "  ".join(f"{t}:{n}" for t, n in d["final_by_type"].items()),
        f"sizes: max {s['max_bytes'] / 1000:.1f} KB, mean {s['mean_bytes'] / 1000:.1f} KB, "
        f"longest line {s['max_line_bytes']} B"
        + (f" — OVER BUDGET: {', '.join(s['over_budget'])}" if s["over_budget"] else ""),
    ]
    for note in d.get("notes", []):
        lines.append(f"note: {note}")
    return "\n".join(lines)


def write_section(reports_dir: Path, section: str, payload: Mapping[str, Any]) -> Path:
    """`data/reports/eval_questions.json` holds both halves: `build` and `merge`."""
    path = Path(reports_dir) / "eval_questions.json"
    report: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                report = existing
        except (OSError, json.JSONDecodeError):
            report = {}
    report["step"] = "eval.questions"
    report["generated_at"] = utc_now_iso()
    report[section] = dict(payload)
    Path(reports_dir).mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, report)
    return path
