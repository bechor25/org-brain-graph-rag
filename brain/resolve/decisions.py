"""`brain resolve merge-decisions` — the adjudicator's verdicts, validated, then applied.

Only `same` merges. `different` and `unsure` are recorded in the report and change
nothing in the graph, which is the point of having three verdicts instead of two: a pair
the evidence does not decide is a pair that stays two nodes, and the count of those is a
number the planner reads rather than a silence.

Validation is the step-04 protocol: schema, then pydantic, then the two rules a schema
cannot express — every `pair_id` must be one of the pairs in the batch's own `.in.json`,
and every pair in that input must be answered. A batch that fails goes to `retry/` twice
and then to `quarantine/`, and the report names it either way.
"""

from __future__ import annotations

import itertools
import json
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from brain.common.jsonschema_mini import validate as schema_validate
from brain.graph.context import GraphContext
from brain.resolve import graph as resolve_graph
from brain.resolve.build import TASK
from brain.resolve.ledger import ResolutionLedger
from brain.resolve.models import BatchInput, BatchOutput, Candidate, make_pair

MAX_RETRIES = 2
RETRY_DIR = "retry"
QUARANTINE_DIR = "quarantine"
RETRY_FIELD = "_resolve_retry"
STATUS_NAME = "status.json"
MAX_ERRORS = 50
VERDICTS: tuple[str, ...] = ("same", "different", "unsure")
#: What wrote a tier-3 merge. One string, not per batch: every adjudicator runs the same
#: definition, and the batch is what tells them apart (conventions rule 3).
MODEL = "opus:entity-adjudicator"
#: Distinct parent documents an entity has to appear under before "the same words appear
#: in both" stops being evidence of identity. Boilerplate — "THIS TICKET CANNOT BE WORKED
#: ON UNTIL…", a licence header, a template sentence — is extracted once per page it is
#: pasted into, and two copies of it are two occurrences of a phrase, not one thing.
BOILERPLATE_PARENTS = 3


class DecisionsError(RuntimeError):
    """The decisions on disk cannot be applied to this graph."""


@dataclass
class Batch:
    batch_id: str
    shard: str
    index: int
    path: Path
    output: BatchOutput | None = None
    batch_input: BatchInput | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.output is not None and self.batch_input is not None

    @property
    def in_path(self) -> Path:
        return self.path.with_name(f"{self.index:03d}.in.json")


def discover(root: Path) -> list[Batch]:
    """Every `<shard>/NNN.out.json` under `root`, at either depth.

    `data/batches/resolve/<kind>/shard-NN/` is the layout `build-batches` writes now that
    two kinds exist; `data/batches/resolve/shard-NN/` is where the first person run put
    them. Both are read, so a completed round of adjudication is never orphaned by a
    layout change.
    """
    found = [
        *sorted(root.glob("shard-[0-9][0-9]/[0-9][0-9][0-9].out.json")),
        *sorted(root.glob("*/shard-[0-9][0-9]/[0-9][0-9][0-9].out.json")),
    ]
    return [
        Batch(
            batch_id=f"{p.parent.name}/{int(p.name.split('.', 1)[0]):03d}",
            shard=p.parent.name,
            index=int(p.name.split(".", 1)[0]),
            path=p,
        )
        for p in found
    ]


def parse_batch(batch: Batch, schema: dict[str, Any]) -> None:
    """JSON -> schema -> pydantic -> the input it answers. Every stage's complaints."""
    try:
        raw = json.loads(batch.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        batch.errors.append(f"unreadable: {exc}")
        return
    if not isinstance(raw, dict):
        batch.errors.append(f"top level must be an object, got {type(raw).__name__}")
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
    if not batch.in_path.is_file():
        batch.errors.append(
            f"no input beside it ({batch.in_path.name}); the pair ids cannot be checked "
            "against anything. Re-run `brain resolve build-batches`."
        )
        return
    try:
        batch.batch_input = BatchInput.model_validate_json(
            batch.in_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        batch.errors.append(f"input {batch.in_path.name} is unusable: {exc}")
        return

    asked = {p.pair_id for p in batch.batch_input.pairs}
    answered = [d.pair_id for d in batch.output.decisions]
    unknown = sorted(set(answered) - asked)
    missing = sorted(asked - set(answered))
    duplicated = sorted({p for p in answered if answered.count(p) > 1})
    if unknown:
        batch.errors.append(f"{len(unknown)} pair_id(s) not in this batch: {unknown[:5]}")
    if missing:
        batch.errors.append(f"{len(missing)} pair(s) left unanswered: {missing[:5]}")
    if duplicated:
        batch.errors.append(f"{len(duplicated)} pair(s) answered twice: {duplicated[:5]}")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def handle_failure(batch: Batch) -> dict[str, Any]:
    """`retry/` twice, then `quarantine/`. The counter lives inside the moved file."""
    raw = _read_json(batch.path) or {}
    attempts = int(raw.get(RETRY_FIELD, 0)) + 1
    target_dir = batch.path.parent / (RETRY_DIR if attempts <= MAX_RETRIES else QUARANTINE_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / batch.path.name
    raw[RETRY_FIELD] = attempts
    raw["_resolve_errors"] = batch.errors
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    batch.path.unlink()
    return {
        "batch": batch.batch_id,
        "attempts": attempts,
        "where": target_dir.name,
        "errors": batch.errors,
    }


def clear_failure_files(batch: Batch) -> None:
    """A batch that now validates leaves no retry/quarantine copy behind."""
    for name in (RETRY_DIR, QUARANTINE_DIR):
        stale = batch.path.parent / name / batch.path.name
        if stale.exists():
            stale.unlink()


def read_shard_status(root: Path) -> dict[str, dict[str, Any]]:
    """Keyed by the path under the task root, so `person/shard-01` and `entity/shard-01`
    are two rows in the report rather than one overwriting the other."""
    out: dict[str, dict[str, Any]] = {}
    for pattern in (f"shard-[0-9][0-9]/{STATUS_NAME}", f"*/shard-[0-9][0-9]/{STATUS_NAME}"):
        for path in sorted(root.glob(pattern)):
            raw = _read_json(path)
            if raw is not None:
                out[str(path.parent.relative_to(root))] = raw
    return out


def is_boilerplate(a: Candidate | None, b: Candidate | None) -> bool:
    """Should this `same` verdict be refused as a repeated phrase rather than one thing?

    Two conditions, both required. The sides share no document or work item — so nothing
    but the wording connects them — *and* at least one of them is quoted under three or
    more distinct parents, which is what a phrase pasted into many pages looks like and
    what a real, specific entity does not. An entity that genuinely spans three KIPs will
    normally share one of them with its duplicate, and that shared parent exempts it.

    Reported, never silent: every refused pair is named in `skipped_boilerplate`.
    """
    if a is None or b is None:
        return False
    if a.parents & b.parents:
        return False
    return max(len(a.parents), len(b.parents)) >= BOILERPLATE_PARENTS


def apply_decisions(
    ctx: GraphContext,
    *,
    kind: str,
    batches_dir: Path,
    candidates: Sequence[Candidate],
    ledger: ResolutionLedger,
    dry_run: bool,
    stamp: str,
    echo: Callable[[str], None] = print,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    from brain.resolve.build import SCHEMA_PATH
    from brain.resolve.runner import apply_merges

    root = batches_dir / TASK
    schema = json.loads((schema_path or SCHEMA_PATH).read_text(encoding="utf-8"))
    batches = discover(root)
    by_id = {c.id: c for c in candidates}

    verdicts: dict[str, int] = dict.fromkeys(VERDICTS, 0)
    pairs = []
    boilerplate: list[dict[str, Any]] = []
    refused_pairs: list[tuple[str, str]] = []
    graded: dict[str, list[tuple[str, str]]] = {"different": [], "unsure": []}
    #: Every `same`, stale or not, with the batch that said it — what `backfill_provenance`
    #: needs to put a batch id back on a merge an earlier run already applied.
    same_verdicts: list[tuple[str, str, str]] = []
    failed: list[dict[str, Any]] = []
    accepted: list[str] = []
    stale: list[str] = []
    notes: list[str] = []
    for batch in batches:
        parse_batch(batch, schema)
        if not batch.ok:
            failed.append(handle_failure(batch))
            continue
        clear_failure_files(batch)
        assert batch.batch_input is not None and batch.output is not None
        if batch.batch_input.kind != kind:
            continue
        accepted.append(batch.batch_id)
        notes.extend(batch.output.notes)
        sides = {p.pair_id: p for p in batch.batch_input.pairs}
        for decision in batch.output.decisions:
            verdicts[decision.verdict] += 1
            pair = sides[decision.pair_id]
            if decision.verdict != "same":
                if decision.verdict == "different":
                    # A reader looked at these two and said no. Closure must not overrule
                    # that by arriving at the same merge the long way round.
                    refused_pairs.append((pair.a.id, pair.b.id))
                graded[decision.verdict].append((pair.a.id, pair.b.id))
                continue
            same_verdicts.append((pair.a.id, pair.b.id, batch.batch_id))
            if pair.a.id not in by_id or pair.b.id not in by_id:
                # An earlier tier already merged one side away. Not an error: the verdict
                # agreed with a merge that happened first.
                stale.append(decision.pair_id)
                continue
            if kind == "entity" and is_boilerplate(by_id.get(pair.a.id), by_id.get(pair.b.id)):
                boilerplate.append(
                    {
                        "pair_id": decision.pair_id,
                        "a": pair.a.id,
                        "b": pair.b.id,
                        "a_parents": sorted(by_id[pair.a.id].parents)[:6],
                        "b_parents": sorted(by_id[pair.b.id].parents)[:6],
                        "reason": decision.reason,
                    }
                )
                continue
            pairs.append(
                make_pair(
                    pair.a.id,
                    pair.b.id,
                    kind=kind,
                    block=pair.block,
                    tier=3,
                    rule="adjudicator_same",
                    score=pair.similarity,
                    reason=decision.reason,
                    batch_id=batch.batch_id,
                    model=MODEL,
                )
            )

    stats: dict[str, Any] = {
        "batches_found": len(batches),
        "batches_accepted": len(accepted),
        "batches_failed": failed,
        "verdicts": verdicts,
        "stale_pairs": len(stale),
        "skipped_boilerplate": {
            "count": len(boilerplate),
            "min_parents": BOILERPLATE_PARENTS,
            "pairs": boilerplate,
            "note": "`same` verdicts refused: no shared parent, and one side is quoted "
            "under three or more documents. A phrase pasted into many pages is many "
            "occurrences of a phrase, not one entity.",
        },
        "agent_status": read_shard_status(root),
        "notes": notes[:50],
    }
    stats.update(
        apply_merges(
            ctx,
            kind=kind,
            pairs=pairs,
            candidates=candidates,
            ledger=ledger,
            tier=3,
            dry_run=dry_run,
            stamp=stamp,
            forbidden=refused_pairs,
        )
    )
    stats["closure_overrides"] = closure_overrides(ledger, kind, graded)
    rows, ledger_rows = backfill_provenance(ledger, kind, same_verdicts)
    stats["provenance"] = {
        "model": MODEL,
        "survivors": 0
        if dry_run
        else resolve_graph.stamp_provenance(ctx, resolve_graph.LABELS_BY_KIND[kind], rows),
        "survivors_to_stamp": len(rows),
        "ledger_rows_filled": ledger_rows,
        "note": "`resolution_batch_id` / `resolution_model` on every survivor a tier-3 "
        "verdict merged, this run's and earlier runs'. Under their own keys: an `Entity` "
        "already spends `batch_id` and `model` on the extraction that made it.",
    }
    echo(
        f"{kind} tier 3: {len(accepted)}/{len(batches)} batches, "
        f"same={verdicts['same']} different={verdicts['different']} unsure={verdicts['unsure']}"
        + (f", boilerplate refused={len(boilerplate)}" if boilerplate else "")
    )
    return stats


def backfill_provenance(
    ledger: ResolutionLedger, kind: str, same: Sequence[tuple[str, str, str]]
) -> tuple[list[dict[str, Any]], int]:
    """Survivor rows and ledger rows for tier-3 merges that were applied without a batch id.

    A merge deletes the node it swallows and the `SAME_AS` edge that argued for it, so a
    second `merge-decisions` run finds every pair stale and has nothing left to stamp. The
    verdicts on disk still name the batch, and the ledger still says which identity went
    into which survivor, so the two together can put the provenance back.

    Only where a tier-3 row actually made the merge. A pair the adjudicator also called
    `same` but tier 1 merged first is a tier-1 merge: writing `resolution_model` onto it
    would say a language model decided something no language model decided.
    """
    section = ledger.section(kind)
    batches: dict[str, set[str]] = {}
    ledger_rows = 0
    for a, b, batch_id in same:
        canonical = ledger.canonical(kind, a)
        if canonical != ledger.canonical(kind, b):
            continue
        decided = [s for s in (a, b) if int((section.get(s) or {}).get("tier") or 0) == 3]
        if not decided:
            continue
        for side in decided:
            if not section[side].get("batch_id"):
                section[side] |= {"batch_id": batch_id, "model": MODEL}
                ledger_rows += 1
        batches.setdefault(canonical, set()).add(batch_id)
    rows = [
        {
            "id": node,
            "props": {
                "resolution_batch_id": min(found),
                "resolution_batch_ids": sorted(found),
                "resolution_model": MODEL,
            },
        }
        for node, found in sorted(batches.items())
    ]
    return rows, ledger_rows


def closure_overrides(
    ledger: ResolutionLedger, kind: str, graded: dict[str, list[tuple[str, str]]]
) -> dict[str, Any]:
    """Pairs a reader graded `different` or `unsure` that are merged anyway.

    `different` should now be zero — `groups` refuses to close across one. `unsure` is
    expected and is not a bug: "I cannot tell" is not "not the same", so a chain of
    confident merges is allowed to answer the question the reader could not. It is
    reported because it is the number that says how much of the graph rests on closure
    rather than on a judgement.
    """
    components: dict[str, set[str]] = {}
    for identity, entry in ledger.section(kind).items():
        components.setdefault(str(entry["canonical"]), set()).update(
            {identity, str(entry["canonical"])}
        )
    merged = {
        p for members in components.values() for p in itertools.combinations(sorted(members), 2)
    }
    out: dict[str, Any] = {}
    for verdict, pairs in graded.items():
        hit = sorted({tuple(sorted(p)) for p in pairs} & merged)
        out[verdict] = {"graded": len(pairs), "merged_anyway": len(hit), "pairs": hit[:20]}
    out["note"] = (
        "`different` must be 0: closure refuses to cross an edge a reader rejected. "
        "`unsure` merged anyway is allowed — the reader did not decide, and a chain of "
        "confident merges may. Neither undoes an existing merge; this only counts them."
    )
    return out


def clean_batches(batches_dir: Path) -> None:
    """Tests only: forget a whole `data/batches/resolve` tree."""
    root = batches_dir / TASK
    if root.is_dir():
        shutil.rmtree(root)
