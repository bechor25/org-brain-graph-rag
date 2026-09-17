"""`brain eval answers build|merge` — mode A answers, written from the context and nothing else.

Plan 3 decision 6: in mode A the answering agent is `brain-analyst` **with no tools**. It is
handed the packed `Result` a strategy returned and asked for an answer. That is the whole
point of the mode — the number that comes out compares *retrievals*, not the agent's talent
at using them — and it is enforced by what the batch does not contain: no question id it
could look up, no tool, no graph, no second context for the same question.

Three decisions worth stating, because each one is a number in the report:

* **The context goes in whole.** A case carries the items exactly as `brain/retrieve/pack.py`
  packed them, ~4k tokens, ~13 KB of JSON. That is why a 40 KB batch holds three cases and
  not ten: the budget is the measurement, and trimming a snippet to fit more cases in would
  quietly change what "S1 could not answer this" means.
* **Two cases of one question never share a batch.** `casebatch.pack` enforces it. An agent
  holding S1's context and S3's context for the same question can answer either from the
  other, and the two strategies would be measured on one blended retrieval.
* **A refusal is an answer.** `confidence: "none"` with a reason is the outcome the plan
  wants when retrieval missed; a case with no entry at all is reported as unanswered, which
  is a different fact and is not allowed to masquerade as one.

Merge keeps a bad citation rather than dropping it. `cited_keys` that name nothing in the
case's own context are marked invalid and stay attached to the answer, because "this
strategy's answers cite keys that were never retrieved" is exactly the sort of thing layer 3
exists to catch.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.common.jsonschema_mini import validate as schema_validate
from brain.eval import casebatch
from brain.eval.casebatch import Case, PlannedBatch
from brain.extract.build import MANIFEST_NAME, sha256_of, shard_name, shards_in_flight
from brain.harvest.base import utc_now_iso, write_json_atomic

TASK = "answers"
BATCH_DIR = "answers"
SCHEMA_PATH = Path(__file__).with_name("answer_schema.json")
SCHEMA_REF = "brain/eval/answer_schema.json"
AGENT_REF = ".claude/agents/brain-analyst.md"
CITATION_REF = "data/eval/plan2_answers/README.md"
REPORT_NAME = "eval_answers.json"

#: `data/eval/answers/<MODE>/<qid>.<strategy>.json`, mirroring `data/eval/runs/fixed/`.
ANSWERS_DIRNAME = "answers"
MODE = "fixed"
#: The column a mode-B (agentic) answer occupies in every strategy table.
AGENTIC = "agentic"
PLAN2_ANSWERS_DIRNAME = "plan2_answers"

DEFAULT_SHARDS = 4
#: A ceiling, not a target: the byte budget closes most batches long before ten cases.
DEFAULT_BATCH_SIZE = 10

RETRY_FIELD = "_answers_retry"
ERRORS_FIELD = "_answers_errors"

#: Item properties worth carrying into the context. `parent_key` is the one that matters:
#: a chunk's own key is a 40-hex id, and an answer that may only cite that cannot cite the
#: work item the chunk belongs to — which is what `gold_evidence` usually names.
CONTEXT_PROPS: tuple[str, ...] = (
    "parent_key",
    "parent_kind",
    "parent_title",
    "parent_status",
    "status",
    "at",
    "lang",
    "synthetic",
)


class AnswersError(RuntimeError):
    """The answer batches cannot be built, or cannot be merged."""


# ----------------------------------------------------------------------------- the context


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def context_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """One packed `Result` item, reduced to what an answer can be written and checked from.

    A provenance quote that is the snippet again is shipped as its chunk id alone. Nothing
    is lost — the snippet is right there — and on this corpus it is a third of the bytes,
    which is a case per batch.
    """
    snippet = str(item.get("snippet") or "")
    out: dict[str, Any] = {
        "kind": str(item.get("kind") or ""),
        "key": str(item.get("key") or ""),
        "title": str(item.get("title") or ""),
        "snippet": snippet,
    }
    props = item.get("props") or {}
    if isinstance(props, Mapping):
        kept = {name: props[name] for name in CONTEXT_PROPS if props.get(name) not in (None, "")}
        if kept:
            out["props"] = kept
    provenance: list[dict[str, Any]] = []
    for entry in item.get("provenance") or []:
        if not isinstance(entry, Mapping) or not entry.get("chunk_id"):
            continue
        record: dict[str, Any] = {"chunk_id": str(entry["chunk_id"])}
        quote = str(entry.get("quote") or "")
        if quote and _norm(quote) != _norm(snippet):
            record["quote"] = quote
        if entry.get("source"):
            record["source"] = str(entry["source"])
        provenance.append(record)
    if provenance:
        out["provenance"] = provenance
    return out


def context_of(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The context a run handed the answering agent, in the order it was packed."""
    items = ((record.get("result") or {}).get("items")) or []
    return [context_item(i) for i in items if isinstance(i, Mapping)]


def context_keys(context: Sequence[Mapping[str, Any]]) -> set[str]:
    """Every id a citation of this context may name — item keys, parents, chunk ids."""
    keys: set[str] = set()
    for item in context:
        if item.get("key"):
            keys.add(str(item["key"]))
        props = item.get("props") or {}
        if isinstance(props, Mapping) and props.get("parent_key"):
            keys.add(str(props["parent_key"]))
        for entry in item.get("provenance") or []:
            if isinstance(entry, Mapping):
                if entry.get("chunk_id"):
                    keys.add(str(entry["chunk_id"]))
                if entry.get("source"):
                    keys.add(str(entry["source"]))
    return keys


def context_sha(context: Sequence[Mapping[str, Any]]) -> str:
    """A hash of the context as shipped, so an answer can prove which retrieval it answers."""
    blob = json.dumps(list(context), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def cited_key_matches(cited: str, keys: set[str]) -> str | None:
    """The context key a cited id names, allowing the `chunk:`-prefixed and truncated forms."""
    value = (cited or "").strip()
    if not value:
        return None
    if value in keys:
        return value
    bare = value.split(":", 1)[1] if value.lower().startswith("chunk:") else value
    if bare in keys:
        return bare
    lowered = bare.lower()
    if len(lowered) >= 8 and all(c in "0123456789abcdef" for c in lowered):
        for key in sorted(keys):
            if key.lower().startswith(lowered):
                return key
    return None


# ----------------------------------------------------------------------------- the cases


def case_id_for(qid: str, strategy: str) -> str:
    return f"{qid}.{strategy}"


def split_case_id(case_id: str) -> tuple[str, str]:
    qid, _, strategy = str(case_id).rpartition(".")
    return qid, strategy


INSTRUCTION = (
    "Answer the question using only this case's `context`, in {lang_name}. End every "
    "factual sentence with citations in square brackets, and list them in `cited_keys`. "
    "If `context` does not answer the question, say so and set `confidence` to `none`."
)
LANG_NAMES = {"en": "English", "he": "Hebrew (עברית)"}

RULES: tuple[str, ...] = (
    "You have no tools in this mode and no memory of Kafka you are allowed to use. The only "
    "admissible source for an answer is the `context` of the case you are answering.",
    "Answer in the language the case declares in `lang`. Identifiers (KAFKA-…, KIP-…, XT-…, "
    "commit shas, component names) stay untranslated in a Hebrew answer.",
    "Cite with square brackets, in the format of " + CITATION_REF + ": [KAFKA-14649], "
    "[KIP-848], [XT-10007], [chunk:ab12cd34] (at least 8 hex characters), [a1b2c3d4] for a "
    "commit, [person:jira:mjsax], [community:L0-6]. A key in running prose is not a citation.",
    "Cite only ids that are in this case's `context` — an item's `key`, its `props.parent_key`, "
    "or a `provenance[].chunk_id`. A citation that is not in the context is recorded as "
    "invalid against the retrieval that produced it, so do not guess one.",
    "`cited_keys` repeats, once each, every id you cited in the text. It is checked against "
    "your brackets, and a mismatch is reported.",
    'When the context does not hold the answer, write one sentence saying so ("not in '
    'context" / "לא נמצא בהקשר"), set `confidence` to `none`, and name the gap in '
    "`unanswerable_reason`. Never fill it from what you know about Kafka: a right answer the "
    "context does not support is the failure this evaluation is built to find.",
    "Answer every case in the file. A case you leave out is reported as unanswered, which "
    "reads as the retrieval behind it having failed.",
    "Do not compare cases with each other, and do not carry anything from one case into the "
    "next. Each context is a different retrieval and stands alone.",
)


def build_case(record: Mapping[str, Any]) -> Case:
    """One `ok` run file -> one case for the answering agent."""
    qid = str(record.get("qid") or "")
    strategy = str(record.get("strategy") or "")
    lang = str(record.get("lang") or "en")
    context = context_of(record)
    payload = {
        "case_id": case_id_for(qid, strategy),
        "question": str(record.get("question") or ""),
        "lang": lang,
        "type": str(record.get("type") or ""),
        "context_items": len(context),
        "context_tokens": int(record.get("context_tokens") or 0),
        "context_sha256": context_sha(context),
        "instructions": INSTRUCTION.format(lang_name=LANG_NAMES.get(lang, lang)),
        "context": context,
    }
    return Case(case_id=payload["case_id"], group=qid, payload=payload)


def collect_cases(
    rows: Sequence[Mapping[str, Any]],
    strategies: Sequence[str],
    runs_dir: Path,
    *,
    read_record: Callable[[Path], dict[str, Any] | None],
) -> tuple[list[Case], list[dict[str, Any]], list[str]]:
    """Every `ok` run file becomes a case; everything else becomes a recorded skip.

    An `ok` run that returned *nothing* is a case too, with an empty context. Leaving it out
    was tried and is wrong: the strategy that retrieves nothing for a question would then be
    averaged over one question fewer than its rivals, and its worst cell would be the one
    missing from the denominator. The answering agent refuses it, which is the correct and
    reportable outcome.
    """
    cases: list[Case] = []
    skipped: list[dict[str, Any]] = []
    empty: list[str] = []
    for row in rows:
        qid = str(row.get("id") or "")
        for strategy in strategies:
            path = Path(runs_dir) / f"{qid}.{strategy}.json"
            record = read_record(path) if path.is_file() else None
            if record is None:
                skipped.append({"qid": qid, "strategy": strategy, "why": "no run file"})
                continue
            if record.get("status") != "ok":
                skipped.append(
                    {
                        "qid": qid,
                        "strategy": strategy,
                        "why": str(record.get("status") or "unknown"),
                        "reason": str(record.get("reason") or "")[:200],
                    }
                )
                continue
            if not (record.get("result") or {}).get("items"):
                empty.append(f"{qid}.{strategy}")
            cases.append(build_case(record))
    return cases, skipped, empty


# ----------------------------------------------------------------------------- the build


def envelope(
    *,
    batch_id: str,
    shard: str,
    index: int,
    cases: Sequence[Case],
    generated_at: str,
    schema_sha: str,
) -> dict[str, Any]:
    return {
        "batch_id": batch_id,
        "shard": shard,
        "index": index,
        "task": TASK,
        "mode": MODE,
        "generated_at": generated_at,
        "schema_path": SCHEMA_REF,
        "schema_sha256": schema_sha,
        "agent": AGENT_REF,
        "citation_format": CITATION_REF,
        "rules": list(RULES),
        "case_count": len(cases),
        "cases": [c.payload for c in cases],
    }


def run_build(
    *,
    rows: Sequence[Mapping[str, Any]],
    strategies: Sequence[str],
    runs_dir: Path,
    batches_dir: Path,
    reports_dir: Path,
    shards: int = DEFAULT_SHARDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
    sha: str = "",
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Write `data/batches/answers/<shard>/NNN.in.json` + status + manifest. Idempotent."""
    from brain.eval.runs import read_record

    started = time.perf_counter()
    if not SCHEMA_PATH.is_file():
        raise AnswersError(f"the output contract is missing: {SCHEMA_PATH}")
    root = Path(batches_dir) / BATCH_DIR
    busy = shards_in_flight(root)
    if busy and not force:
        listed = ", ".join(f"{shard} ({why})" for shard, why in busy.items())
        raise AnswersError(
            f"refusing to rebuild: {listed}. An agent has already answered against the "
            "current cases, and repacking would repoint its answers at different contexts. "
            "Merge what is done, or clear the shard's status.json and move its .out.json "
            "files aside."
        )

    cases, skipped, empty = collect_cases(rows, strategies, Path(runs_dir), read_record=read_record)
    if not cases:
        raise AnswersError(
            f"no `ok` run files under {runs_dir} for {', '.join(strategies)} — "
            "run `brain eval run --mode fixed` first."
        )

    schema_sha = sha256_of(SCHEMA_PATH)
    generated_at = utc_now_iso()

    def measure(index: int, packed: Sequence[Case]) -> dict[str, Any]:
        return envelope(
            batch_id=f"{shard_name(0)}/{index:03d}",
            shard=shard_name(0),
            index=index,
            cases=packed,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )

    planned: list[PlannedBatch] = []
    for index, bucket in enumerate(casebatch.assign_shards(cases, shards)):
        planned.extend(casebatch.pack(bucket, shard=index, batch_size=batch_size, envelope=measure))

    def payload_of(batch: PlannedBatch) -> dict[str, Any]:
        return envelope(
            batch_id=batch.batch_id,
            shard=shard_name(batch.shard),
            index=batch.index,
            cases=batch.cases,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )

    stale = casebatch.remove_stale_files(root, planned)
    written = casebatch.write_batches(root, planned, envelope=payload_of)
    for name in stale["removed_inputs"]:
        echo(f"removed stale input {name} (a previous build planned more batches than this one)")
    for name in stale["moved_outputs"]:
        echo(f"moved orphaned output to {name} (no batch of this plan asks for it)")

    manifest = build_manifest(
        written,
        cases=cases,
        skipped=skipped,
        empty=empty,
        strategies=strategies,
        shards=shards,
        batch_size=batch_size,
        schema_sha=schema_sha,
        generated_at=generated_at,
        stale=stale,
        sha=sha,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    write_json_atomic(root / MANIFEST_NAME, manifest)
    write_section(Path(reports_dir), "answers_build", manifest, sha=sha)
    return manifest


def build_manifest(
    written: Sequence[Mapping[str, Any]],
    *,
    cases: Sequence[Case],
    skipped: Sequence[Mapping[str, Any]],
    empty: Sequence[str],
    strategies: Sequence[str],
    shards: int,
    batch_size: int,
    schema_sha: str,
    generated_at: str,
    stale: Mapping[str, list[str]],
    sha: str,
    duration_ms: int,
) -> dict[str, Any]:
    sizes = [int(b["bytes"]) for b in written]
    per_shard: dict[str, int] = {}
    for batch in written:
        per_shard[str(batch["id"]).split("/")[0]] = (
            per_shard.get(str(batch["id"]).split("/")[0], 0) + 1
        )
    by_strategy: dict[str, int] = {}
    for case in cases:
        _, strategy = split_case_id(case.case_id)
        by_strategy[strategy] = by_strategy.get(strategy, 0) + 1
    duplicate_groups = [
        batch["id"]
        for batch in written
        if len({split_case_id(c)[0] for c in batch["case_ids"]}) != len(batch["case_ids"])
    ]
    return {
        "step": "eval.answers.build",
        "generated_at": generated_at,
        "sha": sha,
        "duration_ms": duration_ms,
        "mode": MODE,
        "schema": {"path": SCHEMA_REF, "sha256": schema_sha},
        "agent": AGENT_REF,
        "strategies": list(strategies),
        "sharding": {
            "shards": shards,
            "batch_size": batch_size,
            "max_batch_bytes": casebatch.MAX_BATCH_BYTES,
            "rule": "one question per batch at most; shards balanced by bytes after a "
            "round-robin spread of each question's strategies",
            "batches_per_shard": dict(sorted(per_shard.items())),
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
            "cases_per_batch": round(len(cases) / len(written), 2) if written else 0,
            "by_strategy": dict(sorted(by_strategy.items())),
            "skipped": len(skipped),
            "empty_context": len(empty),
        },
        "skipped": [dict(s) for s in skipped],
        # An `ok` run that retrieved nothing. It is a case, answered with a refusal, so the
        # strategy is measured on the questions it failed as well as the ones it served.
        "empty_context": list(empty),
        "batches": [dict(b) for b in written],
        "batches_with_a_repeated_question": duplicate_groups,
    }


def summarize_build(manifest: Mapping[str, Any]) -> str:
    totals = manifest["totals"]
    sizes = manifest["sizes"]
    sharding = manifest["sharding"]
    lines = [
        f"answers build: {totals['batches']} batches · {totals['cases']} cases "
        f"({totals['cases_per_batch']} per batch) · {totals['skipped']} run(s) skipped",
        "  per strategy: "
        + (", ".join(f"{k} {v}" for k, v in totals["by_strategy"].items()) or "none"),
        "  per shard: " + ", ".join(f"{k} {v}" for k, v in sharding["batches_per_shard"].items()),
        f"  sizes: max {sizes['max_bytes']}B, mean {sizes['mean_bytes']}B"
        + (f" — OVER BUDGET: {', '.join(sizes['over_budget'])}" if sizes["over_budget"] else ""),
    ]
    if manifest.get("batches_with_a_repeated_question"):
        lines.append(
            "WARNING a batch carries two cases of one question: "
            + ", ".join(manifest["batches_with_a_repeated_question"][:5])
        )
    return "\n".join(lines)


# ----------------------------------------------------------------------------- the merge


@dataclass
class Merged:
    """One accepted answer, and everything the citation check found in it."""

    case_id: str
    qid: str
    strategy: str
    record: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def cases_in(source: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(c.get("case_id")): dict(c)
        for c in (source.get("cases") or [])
        if isinstance(c, Mapping) and c.get("case_id")
    }


def check_answer(
    answer: Mapping[str, Any], case: Mapping[str, Any], *, sha: str, batch_id: str
) -> Merged:
    """One answer against the case it answers: schema-clean already, context-checked here."""
    from brain.eval.citations import find_citations, unique

    case_id = str(answer.get("case_id") or "")
    qid, strategy = split_case_id(case_id)
    context = list(case.get("context") or [])
    keys = context_keys(context)

    declared = [str(k) for k in (answer.get("cited_keys") or [])]
    valid: list[str] = []
    invalid: list[dict[str, str]] = []
    for cited in declared:
        match = cited_key_matches(cited, keys)
        if match is None:
            invalid.append({"cited": cited, "reason": "not in this case's context"})
        else:
            valid.append(match)

    body = str(answer.get("answer") or "")
    bracketed = unique(find_citations(body))
    in_text = [c.text for c in bracketed]
    declared_set = {c.strip() for c in declared}
    text_set = {t.strip() for t in in_text}

    problems: list[str] = []
    if invalid:
        problems.append(
            f"{len(invalid)} cited key(s) are not in the context: "
            + ", ".join(i["cited"] for i in invalid[:5])
        )
    missing_in_text = sorted(declared_set - text_set)
    missing_in_list = sorted(text_set - declared_set)
    if missing_in_text:
        problems.append(f"declared but not bracketed in the text: {', '.join(missing_in_text[:5])}")
    if missing_in_list:
        problems.append(f"bracketed but not declared: {', '.join(missing_in_list[:5])}")

    confidence = str(answer.get("confidence") or "")
    record = {
        "case_id": case_id,
        "qid": qid,
        "strategy": strategy,
        "mode": MODE,
        "batch_id": batch_id,
        "question": case.get("question"),
        "lang": case.get("lang"),
        "type": case.get("type"),
        "answer": body,
        "cited_keys": declared,
        "cited_keys_valid": sorted(set(valid)),
        "cited_keys_invalid": invalid,
        "cited_in_text": in_text,
        "confidence": confidence,
        "refused": confidence == "none",
        "unanswerable_reason": answer.get("unanswerable_reason"),
        "context_items": len(context),
        "context_tokens": case.get("context_tokens"),
        "context_sha256": case.get("context_sha256"),
        # Kept on the answer so the deterministic citation cross-check needs neither the
        # batch input nor the run file: "was this key ever retrieved" is answerable here.
        "context_keys": sorted(keys),
        "context_available": True,
        "citation_problems": problems,
        "generated_at": utc_now_iso(),
        "sha": sha,
    }
    return Merged(case_id=case_id, qid=qid, strategy=strategy, record=record, problems=problems)


def validate_batch(batch: casebatch.Batch, schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Envelope first (errors land on the batch), then one verdict per answer."""
    errors = schema_validate(dict(batch.output), schema)
    envelope_errors, per_answer = casebatch.errors_by_index(errors, "answers")
    if envelope_errors:
        batch.errors.extend(envelope_errors[:20])
        return []
    out: list[dict[str, Any]] = []
    for index, answer in enumerate(batch.output.get("answers") or []):
        out.append({"index": index, "answer": answer, "errors": per_answer.get(index, [])})
    return out


def run_merge(
    *,
    batches_dir: Path,
    eval_dir: Path,
    reports_dir: Path,
    runs_dir: Path | None = None,
    plan2_dir: Path | None = None,
    sha: str = "",
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Validate the answer batches, write `data/eval/answers/fixed/<qid>.<strategy>.json`."""
    started = time.perf_counter()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    root = Path(batches_dir) / BATCH_DIR
    out_dir = Path(eval_dir) / ANSWERS_DIRNAME / MODE
    planned = casebatch.batch_ids(root)
    batches = casebatch.read_outputs(root, array_field="answers")

    accepted: list[Merged] = []
    rejected: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    unanswered: list[str] = []
    seen: dict[str, str] = {}

    for batch in batches:
        if not batch.ok:
            failed.append(
                casebatch.handle_failure(batch, retry_field=RETRY_FIELD, errors_field=ERRORS_FIELD)
            )
            continue
        cases = cases_in(batch.source)
        verdicts = validate_batch(batch, schema)
        if not batch.ok:
            failed.append(
                casebatch.handle_failure(batch, retry_field=RETRY_FIELD, errors_field=ERRORS_FIELD)
            )
            continue
        answered: set[str] = set()
        for verdict in verdicts:
            answer = verdict["answer"]
            case_id = str(answer.get("case_id") or "") if isinstance(answer, Mapping) else ""
            if verdict["errors"]:
                rejected.append(
                    {"batch": batch.batch_id, "case_id": case_id, "why": verdict["errors"][:5]}
                )
                continue
            case = cases.get(case_id)
            if case is None:
                rejected.append(
                    {
                        "batch": batch.batch_id,
                        "case_id": case_id,
                        "why": [f"{case_id!r} is not a case of this batch"],
                    }
                )
                continue
            if case_id in seen:
                rejected.append(
                    {
                        "batch": batch.batch_id,
                        "case_id": case_id,
                        "why": [f"already answered in {seen[case_id]}"],
                    }
                )
                continue
            seen[case_id] = batch.batch_id
            answered.add(case_id)
            accepted.append(check_answer(answer, case, sha=sha, batch_id=batch.batch_id))
        unanswered.extend(sorted(set(cases) - answered))
        casebatch.clear_failure_files(batch)

    for merged in accepted:
        casebatch.write_pretty(out_dir / f"{merged.qid}.{merged.strategy}.json", merged.record)

    agentic = merge_agentic(
        plan2_dir=Path(plan2_dir) if plan2_dir else Path(eval_dir) / PLAN2_ANSWERS_DIRNAME,
        out_dir=Path(eval_dir) / ANSWERS_DIRNAME / MODE,
        sha=sha,
    )

    drift = context_drift(accepted, Path(runs_dir)) if runs_dir else []
    report = merge_report(
        accepted=accepted,
        rejected=rejected,
        failed=failed,
        unanswered=unanswered,
        planned=planned,
        batches=batches,
        agentic=agentic,
        drift=drift,
        out_dir=out_dir,
        root=root,
        sha=sha,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    write_section(Path(reports_dir), "answers_merge", report, sha=sha)
    echo(summarize_merge(report))
    return report


def context_drift(accepted: Sequence[Merged], runs_dir: Path) -> list[dict[str, Any]]:
    """Answers whose run file no longer packs the context they were written against.

    A rerun of `brain eval run` that changed a Result silently invalidates every answer to
    it — the judge would score an answer against a context nobody handed the answerer. The
    hash is what turns that into a line in the report instead of a wrong number.
    """
    from brain.eval.runs import read_record

    out: list[dict[str, Any]] = []
    for merged in accepted:
        path = Path(runs_dir) / f"{merged.qid}.{merged.strategy}.json"
        record = read_record(path)
        if record is None:
            out.append({"case_id": merged.case_id, "why": "the run file is gone"})
            continue
        now = context_sha(context_of(record))
        if now != merged.record.get("context_sha256"):
            out.append(
                {
                    "case_id": merged.case_id,
                    "why": "the run file now packs a different context",
                    "answered_against": merged.record.get("context_sha256"),
                    "run_file_now": now,
                }
            )
    return out


def merge_agentic(*, plan2_dir: Path, out_dir: Path, sha: str) -> dict[str, Any]:
    """Mode B (Plan 2 Task 4) answers, carried into the same shape as `<qid>.agentic.json`.

    The agentic run had tools, so nothing recorded the context it read. `context_available`
    is false and stays false: the judge is told not to score faithfulness on those cases, and
    the report reads the missing metric as missing rather than as a zero.
    """
    from brain.eval.answers import read_answers
    from brain.eval.citations import find_citations, unique

    plan2_dir = Path(plan2_dir)
    written: list[str] = []
    problems: list[dict[str, Any]] = []
    for qid, answer in sorted(read_answers(plan2_dir).items()):
        citations = unique(find_citations(answer.body))
        record = {
            "case_id": case_id_for(qid, AGENTIC),
            "qid": qid,
            "strategy": AGENTIC,
            "mode": "agentic",
            "batch_id": None,
            "question": None,
            "lang": answer.lang,
            "type": None,
            "answer": answer.body,
            "cited_keys": [c.text for c in citations],
            "cited_keys_valid": [],
            "cited_keys_invalid": [],
            "cited_in_text": [c.text for c in citations],
            "confidence": None,
            "refused": False,
            "unanswerable_reason": None,
            "context_items": 0,
            "context_tokens": None,
            "context_sha256": None,
            "context_available": False,
            "citation_problems": list(answer.problems),
            "tools": list(answer.tools),
            "latency_ms": answer.latency_ms,
            "source_file": str(answer.path),
            "generated_at": utc_now_iso(),
            "sha": sha,
        }
        casebatch.write_pretty(Path(out_dir) / f"{qid}.{AGENTIC}.json", record)
        written.append(qid)
        if answer.problems:
            problems.append({"qid": qid, "problems": answer.problems[:5]})
    return {
        "dir": str(plan2_dir),
        "answers": len(written),
        "qids": written,
        "problems": problems,
        "note": "mode B recorded no packed context; faithfulness is not scored on these cases",
    }


def merge_report(
    *,
    accepted: Sequence[Merged],
    rejected: Sequence[Mapping[str, Any]],
    failed: Sequence[Mapping[str, Any]],
    unanswered: Sequence[str],
    planned: Sequence[str],
    batches: Sequence[casebatch.Batch],
    agentic: Mapping[str, Any],
    drift: Sequence[Mapping[str, Any]],
    out_dir: Path,
    root: Path,
    sha: str,
    duration_ms: int,
) -> dict[str, Any]:
    by_strategy: dict[str, dict[str, Any]] = {}
    for merged in accepted:
        row = by_strategy.setdefault(
            merged.strategy,
            {"answers": 0, "refused": 0, "with_invalid_citations": 0, "citations": 0, "valid": 0},
        )
        row["answers"] += 1
        row["refused"] += 1 if merged.record["refused"] else 0
        row["citations"] += len(merged.record["cited_keys"])
        row["valid"] += len(merged.record["cited_keys_valid"])
        row["with_invalid_citations"] += 1 if merged.record["cited_keys_invalid"] else 0
    for row in by_strategy.values():
        row["citation_in_context_pct"] = (
            round(100 * row["valid"] / row["citations"], 2) if row["citations"] else None
        )
    answered = {f"{m.batch_id}" for m in batches if m.ok}
    return {
        "step": "eval.answers.merge",
        "generated_at": utc_now_iso(),
        "sha": sha,
        "duration_ms": duration_ms,
        "batches": {
            "planned": len(planned),
            "with_output": len(batches),
            "readable": len(answered),
            "failed": [dict(f) for f in failed],
            "missing": sorted(set(planned) - {b.batch_id for b in batches}),
        },
        "answers": {
            "accepted": len(accepted),
            "rejected": len(rejected),
            "unanswered_cases": list(unanswered),
            "by_strategy": dict(sorted(by_strategy.items())),
        },
        "rejected": [dict(r) for r in rejected],
        "context_drift": [dict(d) for d in drift],
        "agentic": dict(agentic),
        "answers_dir": str(out_dir),
        "batches_dir": str(root),
        "status": casebatch.read_status(root),
        "complete": (
            bool(planned)
            and not set(planned) - {b.batch_id for b in batches if b.ok}
            and not unanswered
            and not failed
        ),
    }


def summarize_merge(report: Mapping[str, Any]) -> str:
    batches = report["batches"]
    answers = report["answers"]
    lines = [
        f"answers merge: {answers['accepted']} accepted, {answers['rejected']} rejected, "
        f"{len(answers['unanswered_cases'])} case(s) unanswered",
        f"batches: {batches['readable']}/{batches['planned']} readable"
        + (f" · missing {', '.join(batches['missing'][:5])}" if batches["missing"] else "")
        + (f" · failed {len(batches['failed'])}" if batches["failed"] else ""),
    ]
    for name, row in answers["by_strategy"].items():
        pct = row["citation_in_context_pct"]
        lines.append(
            f"  {name:<8} {row['answers']:>3} answers · {row['refused']:>2} refused · "
            f"citations in context {'-' if pct is None else f'{pct}%'}"
        )
    agentic = report.get("agentic") or {}
    if agentic.get("answers"):
        lines.append(f"  agentic  {agentic['answers']:>3} answers carried from {agentic['dir']}")
    if report.get("context_drift"):
        lines.append(
            f"CONTEXT DRIFT: {len(report['context_drift'])} answer(s) were written against a "
            "context the run files no longer hold — re-run the affected cases."
        )
    return "\n".join(lines)


# ----------------------------------------------------------------------------- the report


def write_section(reports_dir: Path, section: str, payload: Mapping[str, Any], *, sha: str) -> Path:
    """One section of `data/reports/eval_answers.json`, stamped, leaving the others alone."""
    from brain.retrieve.report import merge_sections

    Path(reports_dir).mkdir(parents=True, exist_ok=True)
    return merge_sections(
        {"step": "eval.answers", section: dict(payload)},
        Path(reports_dir) / REPORT_NAME,
        sha=sha or None,
    )


def read_merged(eval_dir: Path, *, mode: str = MODE) -> list[dict[str, Any]]:
    """Every merged answer on disk, in `<qid>.<strategy>` order — the judge build's input."""
    directory = Path(eval_dir) / ANSWERS_DIRNAME / mode
    out: list[dict[str, Any]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("case_id"):
            out.append(data)
    return out
