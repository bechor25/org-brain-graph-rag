"""`brain eval judge build` — the blind batches the judge scores (Plan 3 decision 5, spec §5.4).

Everything here exists to make one sentence true: *the judge cannot know which retrieval
produced an answer.* Three mechanisms, none of which is sufficient alone:

1. **Opaque labels.** A case is addressed by eight random hex characters drawn from a seeded
   stream. The label → `(question, strategy)` map goes to `data/eval/blind_map.json`, which
   is outside `data/batches/judge/` — the judge's agent definition points it at its shard
   directory and at the rubric, and neither leads there.
2. **A structural leak check before every write.** `brain/eval/blind.py` refuses a payload
   that carries a field named `strategy`, a value that *is* a strategy id, or a question id.
   It checks structure and not prose on purpose: this corpus says "S3" about Amazon S3, and
   a check that cries wolf on a snippet is one somebody switches off.
3. **Randomised pairwise order.** A pairwise case shows the baseline and a graph strategy as
   `a` and `b` in an order drawn per case, so a judge that learns "the second one is usually
   the graph" learns nothing.

Two more properties the packing gives for free, both from `casebatch`: no two cases of the
same question share a batch (so two anonymous answers to one question cannot be lined up
against each other), and 20% of the cases are copied into the other shard so the two judges
overlap — which is the only way to say whether a 1.4 average means anything.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from brain.eval import casebatch
from brain.eval.answers_batches import (
    AGENTIC,
    context_of,
    context_sha,
)
from brain.eval.answers_batches import (
    REPORT_NAME as ANSWERS_REPORT_NAME,
)
from brain.eval.answers_batches import (
    write_section as write_answers_section,
)
from brain.eval.blind import BLIND_MAP_NAME, BlindMap, assert_blind, labels, map_is_outside
from brain.eval.casebatch import Case, PlannedBatch
from brain.extract.build import MANIFEST_NAME, sha256_of, shard_name, shards_in_flight
from brain.harvest.base import utc_now_iso, write_json_atomic

TASK = "judge"
BATCH_DIR = "judge"
SCHEMA_PATH = Path(__file__).with_name("judge_schema.json")
SCHEMA_REF = "brain/eval/judge_schema.json"
RUBRIC_PATH = Path(__file__).with_name("rubric.md")
RUBRIC_REF = "brain/eval/rubric.md"
AGENT_REF = ".claude/agents/eval-judge.md"
REPORT_NAME = ANSWERS_REPORT_NAME

#: Plan 3 decision 5: two judges.
DEFAULT_SHARDS = 2
DEFAULT_BATCH_SIZE = 10
#: The share of cases copied into the other shard, for inter-judge agreement.
DEFAULT_OVERLAP = 0.20
#: The same seed the question build uses, so "seeded" means one number in this plan.
DEFAULT_SEED = 2026

#: The baseline every pairwise case is built around (plan decision 3).
BASELINE = "s1r"
#: What the baseline is compared with by default — the graph strategies of spec §4.
PAIR_WITH: tuple[str, ...] = ("s2", "s3", "s4", "s5", "s6")

RETRY_FIELD = "_judge_retry"
ERRORS_FIELD = "_judge_errors"

RULES: tuple[str, ...] = (
    "Read " + RUBRIC_REF + " before scoring anything. It holds the 0-2 rubric for all four "
    "metrics, the pairwise rule, and what to do when you cannot score.",
    "Every label in this file is random. It encodes nothing. Do not reason about which "
    "system produced an answer, and do not let a guess about it move a score.",
    "`context_items` is exactly what the answering agent was given — it had no tools. An "
    "answer that is true about Kafka but absent from that context is correct and unfaithful, "
    "and that is a result, not a contradiction.",
    "When `context_available` is false, no context was recorded: set `faithfulness` to null "
    "and score the other three.",
    "Every `justification` contains at least one verbatim quotation in double quotes. Copy "
    "the words; never paraphrase inside quotation marks.",
    "In a pairwise case the order of `a` and `b` was randomised per case. It carries no "
    "information. Judge facts first, then citations, then directness.",
    "Judge each case on its own. Do not average across the batch, and do not let one weak "
    "answer set the bar for the next.",
    "Score every case in the file. A case you leave out has no layer-3 number at all, which "
    "reads in the report as the system behind it having failed.",
)


class JudgeError(RuntimeError):
    """The judge batches cannot be built."""


# ----------------------------------------------------------------------------- the cases


def _question_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): dict(row) for row in rows if row.get("id")}


def single_payload(
    label: str,
    *,
    answer: Mapping[str, Any],
    question: Mapping[str, Any],
    context: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """One answer, its gold, and the context it was written from — with no name on any of it."""
    available = bool(answer.get("context_available"))
    return {
        "case_id": label,
        "kind": "single",
        "question": str(question.get("question") or answer.get("question") or ""),
        "lang": str(answer.get("lang") or question.get("lang") or ""),
        "question_type": str(question.get("type") or ""),
        "gold_answer": question.get("gold_answer"),
        "gold_evidence": list(question.get("gold_evidence") or []),
        "context_available": available,
        "context_items": list(context) if available else [],
        "answer": str(answer.get("answer") or ""),
        "cited_keys": list(answer.get("cited_keys") or []),
    }


def pair_payload(
    label: str,
    *,
    question: Mapping[str, Any],
    sides: Sequence[Mapping[str, Any]],
    with_context: bool,
) -> dict[str, Any]:
    """Two answers to one question, in the order the caller already randomised."""
    payload: dict[str, Any] = {
        "pair_id": label,
        "kind": "pairwise",
        "question": str(question.get("question") or ""),
        "lang": str(question.get("lang") or ""),
        "question_type": str(question.get("type") or ""),
        "gold_answer": question.get("gold_answer"),
        "gold_evidence": list(question.get("gold_evidence") or []),
    }
    for name, side in zip(("a", "b"), sides, strict=True):
        entry: dict[str, Any] = {
            "answer": str(side.get("answer") or ""),
            "cited_keys": list(side.get("cited_keys") or []),
        }
        if with_context:
            entry["context_items"] = list(side.get("context") or [])
            entry["context_available"] = bool(side.get("context_available"))
        payload[name] = entry
    return payload


def plan_cases(
    answers: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    *,
    contexts: Mapping[str, list[dict[str, Any]]],
    seed: int = DEFAULT_SEED,
    baseline: str = BASELINE,
    pair_with: Sequence[str] = PAIR_WITH,
    pair_context: bool = False,
    sha: str = "",
) -> tuple[list[Case], BlindMap, dict[str, Any]]:
    """Every single and pairwise case, their labels, and what could not be built.

    The label stream and the A/B coin come from one seeded RNG, so a rebuild at the same
    commit produces byte-identical batches and the same blind map.
    """
    questions = _question_index(rows)
    stream = labels(seed)
    coin = random.Random(seed ^ 0x5EED)
    blind = BlindMap(seed=seed, generated_at=utc_now_iso(), sha=sha)
    cases: list[Case] = []
    skipped: list[dict[str, Any]] = []

    by_qid: dict[str, dict[str, Mapping[str, Any]]] = {}
    for answer in answers:
        qid = str(answer.get("qid") or "")
        strategy = str(answer.get("strategy") or "")
        if not qid or not strategy:
            skipped.append({"case_id": answer.get("case_id"), "why": "no qid or strategy"})
            continue
        by_qid.setdefault(qid, {})[strategy] = answer

    for qid in sorted(by_qid):
        question = questions.get(qid)
        if question is None:
            skipped.append({"qid": qid, "why": "no question row; the gold answer is unknown"})
            continue
        for strategy in sorted(by_qid[qid]):
            answer = by_qid[qid][strategy]
            case_id = str(answer.get("case_id") or f"{qid}.{strategy}")
            context = list(contexts.get(case_id) or [])
            if answer.get("context_available") and case_id not in contexts:
                # Absent, not empty. A retrieval that returned nothing still has a case —
                # its answer is a refusal, and the strategy is measured on it.
                skipped.append(
                    {"qid": qid, "why": f"{case_id}: the run file's context could not be read"}
                )
                continue
            label = next(stream)
            blind.add_case(label, qid=qid, strategy=strategy, kind="single", case_id=case_id)
            cases.append(
                Case(
                    case_id=label,
                    group=qid,
                    payload=single_payload(
                        label, answer=answer, question=question, context=context
                    ),
                )
            )

    for qid in sorted(by_qid):
        question = questions.get(qid)
        base = by_qid[qid].get(baseline)
        if question is None:
            continue
        if base is None:
            skipped.append({"qid": qid, "why": f"no {baseline} answer; no pairwise case"})
            continue
        for strategy in pair_with:
            other = by_qid[qid].get(strategy)
            if other is None or strategy == baseline:
                continue
            label = next(stream)
            sides = [
                {**base, "context": contexts.get(str(base.get("case_id")) or "", [])},
                {**other, "context": contexts.get(str(other.get("case_id")) or "", [])},
            ]
            flipped = coin.random() < 0.5
            if flipped:
                sides.reverse()
            blind.add_pair(
                label,
                qid=qid,
                kind="pairwise",
                a={"strategy": baseline if not flipped else strategy},
                b={"strategy": strategy if not flipped else baseline},
                baseline_side="b" if flipped else "a",
                challenger=strategy,
            )
            cases.append(
                Case(
                    case_id=label,
                    group=qid,
                    payload=pair_payload(
                        label, question=question, sides=sides, with_context=pair_context
                    ),
                )
            )
    return cases, blind, {"skipped": skipped}


def record_shards(buckets: Sequence[list[Case]], blind: BlindMap) -> None:
    """Note in the map which shard each case landed in, before any copy is made."""
    for index, bucket in enumerate(buckets):
        for case in bucket:
            entry = blind.cases.get(case.case_id) or blind.pairs.get(case.case_id)
            if entry is not None:
                entry["shards"] = [shard_name(index)]


def add_overlap(
    buckets: Sequence[list[Case]], *, fraction: float, seed: int, blind: BlindMap
) -> list[dict[str, Any]]:
    """Copy `fraction` of each shard's cases into the next shard, and say which.

    Inter-judge agreement needs the *same* case scored twice, so the copy keeps its label:
    one label, two judgments, one comparison. The copies are recorded in the blind map, so a
    label that appears twice in the merged output is expected rather than a duplicate bug.
    """
    if len(buckets) < 2 or fraction <= 0:
        return []
    rng = random.Random(seed ^ 0x0FE1)
    chosen: list[dict[str, Any]] = []
    originals = [list(bucket) for bucket in buckets]
    for index, bucket in enumerate(originals):
        take = min(len(bucket), round(len(bucket) * fraction))
        if not take:
            continue
        for case in rng.sample(bucket, take):
            target = (index + 1) % len(buckets)
            buckets[target].append(case)
            entry = blind.cases.get(case.case_id) or blind.pairs.get(case.case_id)
            if entry is not None:
                entry["overlap"] = True
                entry["shards"] = sorted(set(entry.get("shards") or []) | {shard_name(target)})
            chosen.append(
                {
                    "case_id": case.case_id,
                    "from": shard_name(index),
                    "to": shard_name(target),
                }
            )
    return sorted(chosen, key=lambda c: c["case_id"])


# ----------------------------------------------------------------------------- the build


def envelope(
    *,
    batch_id: str,
    shard: str,
    index: int,
    cases: Sequence[Case],
    generated_at: str,
    schema_sha: str,
    rubric_sha: str,
) -> dict[str, Any]:
    singles = [c.payload for c in cases if c.payload.get("kind") == "single"]
    pairs = [c.payload for c in cases if c.payload.get("kind") == "pairwise"]
    return {
        "batch_id": batch_id,
        "shard": shard,
        "index": index,
        "task": TASK,
        "generated_at": generated_at,
        "schema_path": SCHEMA_REF,
        "schema_sha256": schema_sha,
        "rubric_path": RUBRIC_REF,
        "rubric_sha256": rubric_sha,
        "agent": AGENT_REF,
        "blind": (
            "Labels are random and encode nothing. The map back to a system is deliberately "
            "not in this directory; do not go looking for it."
        ),
        "rules": list(RULES),
        "case_count": len(singles),
        "pair_count": len(pairs),
        "cases": singles,
        "pairwise": pairs,
    }


def run_build(
    *,
    answers: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, list[dict[str, Any]]],
    batches_dir: Path,
    eval_dir: Path,
    reports_dir: Path,
    shards: int = DEFAULT_SHARDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    overlap: float = DEFAULT_OVERLAP,
    seed: int = DEFAULT_SEED,
    baseline: str = BASELINE,
    pair_with: Sequence[str] = PAIR_WITH,
    pair_context: bool = False,
    force: bool = False,
    sha: str = "",
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Write the blind batches and `data/eval/blind_map.json`. Idempotent under one seed."""
    started = time.perf_counter()
    for path in (SCHEMA_PATH, RUBRIC_PATH):
        if not path.is_file():
            raise JudgeError(f"the judge's contract is missing: {path}")
    root = Path(batches_dir) / BATCH_DIR
    map_path = Path(eval_dir) / BLIND_MAP_NAME
    if not map_is_outside(map_path, Path(batches_dir)):
        raise JudgeError(
            f"the blind map would be written to {map_path}, inside the batch tree the judge "
            "reads. It must live outside it."
        )
    busy = shards_in_flight(root)
    if busy and not force:
        listed = ", ".join(f"{shard} ({why})" for shard, why in busy.items())
        raise JudgeError(
            f"refusing to rebuild: {listed}. A judge has already scored against the current "
            "labels, and relabelling would repoint its scores at other answers. Merge what "
            "is done, or clear the shard's status.json and move its .out.json files aside."
        )
    if not answers:
        raise JudgeError(
            "no merged answers to judge — run `brain eval answers merge` first "
            "(data/eval/answers/fixed/ is empty)."
        )

    cases, blind, planning = plan_cases(
        answers,
        rows,
        contexts=contexts,
        seed=seed,
        baseline=baseline,
        pair_with=pair_with,
        pair_context=pair_context,
        sha=sha,
    )
    if not cases:
        raise JudgeError("no judgable cases: every answer lacks a question row or a context")

    schema_sha = sha256_of(SCHEMA_PATH)
    rubric_sha = sha256_of(RUBRIC_PATH)
    generated_at = utc_now_iso()

    buckets = [list(b) for b in casebatch.assign_shards(cases, shards)]
    record_shards(buckets, blind)
    overlapped = add_overlap(buckets, fraction=overlap, seed=seed, blind=blind)

    def measure(index: int, packed: Sequence[Case]) -> dict[str, Any]:
        return envelope(
            batch_id=f"{shard_name(0)}/{index:03d}",
            shard=shard_name(0),
            index=index,
            cases=packed,
            generated_at=generated_at,
            schema_sha=schema_sha,
            rubric_sha=rubric_sha,
        )

    planned: list[PlannedBatch] = []
    for index, bucket in enumerate(buckets):
        planned.extend(casebatch.pack(bucket, shard=index, batch_size=batch_size, envelope=measure))

    secrets = blind.secrets

    def payload_of(batch: PlannedBatch) -> dict[str, Any]:
        payload = envelope(
            batch_id=batch.batch_id,
            shard=shard_name(batch.shard),
            index=batch.index,
            cases=batch.cases,
            generated_at=generated_at,
            schema_sha=schema_sha,
            rubric_sha=rubric_sha,
        )
        assert_blind(payload, secrets, where=batch.batch_id)
        return payload

    stale = casebatch.remove_stale_files(root, planned)
    written = casebatch.write_batches(root, planned, envelope=payload_of)
    for name in stale["removed_inputs"]:
        echo(f"removed stale input {name} (a previous build planned more batches than this one)")
    for name in stale["moved_outputs"]:
        echo(f"moved orphaned output to {name} (no batch of this plan asks for it)")

    blind.write(map_path)
    manifest = build_manifest(
        written,
        cases=cases,
        blind=blind,
        overlapped=overlapped,
        skipped=planning["skipped"],
        shards=shards,
        batch_size=batch_size,
        overlap=overlap,
        seed=seed,
        baseline=baseline,
        pair_with=pair_with,
        pair_context=pair_context,
        schema_sha=schema_sha,
        rubric_sha=rubric_sha,
        generated_at=generated_at,
        stale=stale,
        map_path=map_path,
        sha=sha,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    write_json_atomic(root / MANIFEST_NAME, manifest)
    write_answers_section(Path(reports_dir), "judge_build", manifest, sha=sha)
    return manifest


def build_manifest(
    written: Sequence[Mapping[str, Any]],
    *,
    cases: Sequence[Case],
    blind: BlindMap,
    overlapped: Sequence[Mapping[str, Any]],
    skipped: Sequence[Mapping[str, Any]],
    shards: int,
    batch_size: int,
    overlap: float,
    seed: int,
    baseline: str,
    pair_with: Sequence[str],
    pair_context: bool,
    schema_sha: str,
    rubric_sha: str,
    generated_at: str,
    stale: Mapping[str, list[str]],
    map_path: Path,
    sha: str,
    duration_ms: int,
) -> dict[str, Any]:
    sizes = [int(b["bytes"]) for b in written]
    per_shard: dict[str, int] = {}
    cases_per_shard: dict[str, int] = {}
    for batch in written:
        shard = str(batch["id"]).split("/")[0]
        per_shard[shard] = per_shard.get(shard, 0) + 1
        cases_per_shard[shard] = cases_per_shard.get(shard, 0) + int(batch["cases"])
    return {
        "step": "eval.judge.build",
        "generated_at": generated_at,
        "sha": sha,
        "duration_ms": duration_ms,
        "schema": {"path": SCHEMA_REF, "sha256": schema_sha},
        "rubric": {"path": RUBRIC_REF, "sha256": rubric_sha},
        "agent": AGENT_REF,
        "blind": {
            "map": str(map_path),
            "map_is_outside_batches": True,
            "seed": seed,
            "labels": len(blind.cases) + len(blind.pairs),
            "note": "the map is never copied into data/batches/judge/",
        },
        "pairwise": {
            "baseline": baseline,
            "compared_with": list(pair_with),
            "pairs": len(blind.pairs),
            "carries_context": pair_context,
            "order": "a/b drawn per case from the seeded RNG",
        },
        "sharding": {
            "shards": shards,
            "batch_size": batch_size,
            "max_batch_bytes": casebatch.MAX_BATCH_BYTES,
            "overlap_fraction": overlap,
            "overlap_cases": len(overlapped),
            "overlap": [dict(o) for o in overlapped],
            "rule": "one question per batch at most; each question's cases spread over the "
            "shards before packing; the overlap sample is copied into the next shard",
            "batches_per_shard": dict(sorted(per_shard.items())),
            "cases_per_shard": dict(sorted(cases_per_shard.items())),
            "removed_stale_inputs": list(stale["removed_inputs"]),
            "moved_stale_outputs": list(stale["moved_outputs"]),
        },
        "sizes": {
            "max_bytes": max(sizes) if sizes else 0,
            "min_bytes": min(sizes) if sizes else 0,
            "mean_bytes": round(sum(sizes) / len(sizes)) if sizes else 0,
            "total_bytes": sum(sizes),
            "max_line_bytes": max((int(b["max_line_bytes"]) for b in written), default=0),
            "over_budget": [
                b["id"] for b in written if int(b["bytes"]) > casebatch.MAX_BATCH_BYTES
            ],
        },
        "totals": {
            "batches": len(written),
            "cases": len(cases),
            "singles": len(blind.cases),
            "pairs": len(blind.pairs),
            "judgments_expected": len(cases) + len(overlapped),
            "skipped": len(skipped),
        },
        "skipped": [dict(s) for s in skipped],
        "batches": [dict(b) for b in written],
    }


def summarize_build(manifest: Mapping[str, Any]) -> str:
    totals = manifest["totals"]
    sizes = manifest["sizes"]
    sharding = manifest["sharding"]
    return "\n".join(
        [
            f"judge build: {totals['batches']} batches · {totals['singles']} single + "
            f"{totals['pairs']} pairwise cases · {totals['judgments_expected']} judgments "
            f"expected ({sharding['overlap_cases']} overlapping)",
            "  per shard: "
            + ", ".join(f"{k} {v}" for k, v in sharding["batches_per_shard"].items()),
            f"  sizes: max {sizes['max_bytes']}B, mean {sizes['mean_bytes']}B"
            + (
                f" — OVER BUDGET: {', '.join(sizes['over_budget'])}" if sizes["over_budget"] else ""
            ),
            f"  blind map: {manifest['blind']['map']} (outside the batch tree)",
        ]
    )


# ----------------------------------------------------------------------------- contexts


def contexts_for(
    answers: Sequence[Mapping[str, Any]], runs_dir: Path
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """The context each answer was written against, re-read from the run file it names.

    The hash recorded at answer-merge time is checked here: an answer whose run file has
    since been re-run would be judged against a context nobody handed the answerer, so it is
    reported and left out rather than quietly judged.
    """
    from brain.eval.runs import read_record

    out: dict[str, list[dict[str, Any]]] = {}
    drift: list[dict[str, Any]] = []
    for answer in answers:
        case_id = str(answer.get("case_id") or "")
        if not answer.get("context_available"):
            out[case_id] = []
            continue
        path = Path(runs_dir) / f"{answer.get('qid')}.{answer.get('strategy')}.json"
        record = read_record(path)
        if record is None:
            drift.append({"case_id": case_id, "why": f"no run file at {path}"})
            continue
        context = context_of(record)
        if context_sha(context) != answer.get("context_sha256"):
            drift.append({"case_id": case_id, "why": "the run file now packs another context"})
            continue
        out[case_id] = context
    return out, drift


def filter_answers(
    answers: Sequence[Mapping[str, Any]], strategies: Sequence[str] | None
) -> list[dict[str, Any]]:
    """`--strategies` for the judge set. `agentic` is a name like any other here."""
    if not strategies:
        return [dict(a) for a in answers]
    wanted = {s.strip() for s in strategies if s.strip()}
    return [dict(a) for a in answers if str(a.get("strategy")) in wanted]


def read_blind_map(eval_dir: Path) -> BlindMap:
    return BlindMap.read(Path(eval_dir) / BLIND_MAP_NAME)


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


__all__ = [
    "AGENTIC",
    "BASELINE",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_OVERLAP",
    "DEFAULT_SEED",
    "DEFAULT_SHARDS",
    "PAIR_WITH",
    "BlindMap",
    "JudgeError",
    "add_overlap",
    "build_manifest",
    "contexts_for",
    "envelope",
    "filter_answers",
    "load_schema",
    "pair_payload",
    "plan_cases",
    "read_blind_map",
    "run_build",
    "single_payload",
    "summarize_build",
]
