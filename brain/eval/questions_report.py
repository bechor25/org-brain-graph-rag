"""`brain eval questions merge` — what gets into `data/eval/questions.jsonl`, and what does not.

The build asked for questions. This decides which of them the evaluation is allowed to be
measured on, and the bar is not "well formed". A question that passes here has to be one
whose score means something:

* **Its evidence exists.** `gold_evidence` is the target layer 2 computes recall against.
  A key no retrieval could ever return would make every strategy look equally bad on that
  question, which is a fact about the question, not about retrieval. Keys are checked twice
  — against what the batch *offered* (so a forger cannot cite a node it never saw) and
  against the live graph (or, for a `truth:` id, against `synthetic_truth.json`).
* **It does not contain its own answer.** The path declares its `anchors` (the subject a
  question may name) and its `answers` (the nodes that *are* the answer). An answer key in
  the question text is a rejection, and so is a question that restates the gold answer for
  eight words or more — measured as a shared word run, which works the same in Hebrew.
* **It fits the plan.** Accepting every question the forger returns would hand back a set
  weighted towards whatever shape was easiest to write about. Each (type, language) cell
  keeps exactly what the deficit table asked for, in a stable order, and the rest is
  reported as surplus rather than thrown away silently.

The 19 competency questions from Plan 2 are carried in beside the forged ones on the same
schema. Their gold comes from `data/eval/competency_gold.jsonl`, which
`brain eval questions gold-competency` derives from the graph with a fixed query per
question — the same way `brain/retrieve/competency.py` chose their anchors. A row the
derivation could not fill stays `gold_source: "pending"` with a `gold_note` saying what came
back empty, because a null that says why is worth more than a gold nobody checked.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.common.jsonschema_mini import validate as schema_validate
from brain.eval.paths import QUESTION_TYPES
from brain.eval.questions import (
    BATCH_DIR,
    LANGS,
    SCHEMA_PATH,
    Demand,
    water_fill,
    write_section,
)
from brain.extract.build import IN_GLOB, OUT_GLOB
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.retrieve.context import RetrieveContext

QUESTIONS_FILE = "questions.jsonl"

#: A question that repeats this many consecutive words of its own gold answer is restating
#: it. Eight rather than five: a real question and a real answer share "the status of the
#: work item" without either giving anything away.
MAX_SHARED_RUN = 8

#: Prefix of an evidence id that lives in `synthetic_truth.json` rather than in the graph.
TRUTH_PREFIX = "truth:"

#: What a competency question's difficulty is until the planner writes its gold. Four of the
#: five types are a two-hop join from their anchor; a `global` question is a whole-corpus
#: theme, which is a 3 by the schema's own definition.
DIFFICULTY_DEFAULT: dict[str, int] = {
    "traceability": 2,
    "impact": 2,
    "rationale": 2,
    "global": 3,
    "temporal": 2,
}

#: label -> the property that holds the citable key, for the existence check. `PullRequest`
#: is last and keyless-indexed on purpose: its key is an integer, so it cannot ride the
#: `IN $keys` string index and is only worth a scan when something actually cites one.
#:
#: `Test` and `TestExecution` are listed even though an Xray test also carries `WorkItem`:
#: `TestExecution` does NOT (measured — an `XE-…` id resolved through no lookup here and was
#: rejected as `evidence_missing_in_graph`), and naming `Test` explicitly stops the next
#: relabelling from silently taking `XT-…` ids with it.
KEY_LOOKUPS: tuple[tuple[str, str], ...] = (
    ("WorkItem", "key"),
    ("Test", "key"),
    ("TestExecution", "key"),
    ("Document", "key"),
    ("Chunk", "id"),
    ("Entity", "id"),
    ("Person", "id"),
    ("Commit", "sha"),
    ("Component", "name"),
    ("Version", "name"),
    ("Sprint", "name"),
    ("Area", "name"),
    ("File", "path"),
    ("Community", "id"),
    ("StatusChange", "id"),
)

#: Word boundaries for the leakage check. `\w` under Unicode matching already covers
#: Hebrew letters, so one class serves both languages.
_WORD = re.compile(r"\W+", re.UNICODE)


class MergeError(RuntimeError):
    """The batches cannot be merged into a question set."""


# ----------------------------------------------------------------------------- reading


def read_outputs(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Every `<NNN>.out.json` with the `<NNN>.in.json` it answers, or a reason it was refused.

    Envelope-level refusals only. One bad *question* costs that question (see
    `review_question`); a file that is not readable, is not an object, names a batch other
    than its own, or has no input beside it cannot be trusted at all, including the parts
    that look fine.
    """
    outputs: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    root = Path(root)
    if not root.is_dir():
        return outputs, failures
    for out_path in sorted(root.glob(f"shard-*/{OUT_GLOB}")):
        batch_id = f"{out_path.parent.name}/{out_path.name.split('.', 1)[0]}"
        in_path = out_path.with_name(out_path.name.replace(".out.json", ".in.json"))
        if not in_path.is_file():
            failures.append({"batch": batch_id, "reason": f"no input beside it ({in_path.name})"})
            continue
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
            source = json.loads(in_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append({"batch": batch_id, "reason": f"unreadable JSON: {exc}"})
            continue
        if not isinstance(data, dict) or not isinstance(source, dict):
            failures.append({"batch": batch_id, "reason": "top level is not an object"})
            continue
        if data.get("batch_id") != batch_id:
            failures.append(
                {
                    "batch": batch_id,
                    "reason": f"batch_id {data.get('batch_id')!r} is not this file's batch",
                }
            )
            continue
        if not isinstance(data.get("questions"), list):
            failures.append({"batch": batch_id, "reason": "`questions` is not an array"})
            continue
        outputs.append({"batch_id": batch_id, "output": data, "input": source})
    return outputs, failures


def paths_in(source: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(p.get("path_id")): dict(p)
        for p in (source.get("paths") or [])
        if isinstance(p, dict) and p.get("path_id")
    }


def batches_planned(root: Path) -> list[str]:
    root = Path(root)
    if not root.is_dir():
        return []
    return [
        f"{p.parent.name}/{p.name.split('.', 1)[0]}"
        for p in sorted(root.glob(f"shard-*/{IN_GLOB}"))
    ]


# -------------------------------------------------------------------------- the checks


def words(text: str) -> list[str]:
    return [w for w in _WORD.split((text or "").casefold()) if w]


def longest_shared_run(a: str, b: str) -> int:
    """The longest run of consecutive words the two strings share, case/punctuation blind."""
    left, right = words(a), words(b)
    if not left or not right:
        return 0
    best = 0
    previous = [0] * (len(right) + 1)
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        for j in range(1, len(right) + 1):
            if left[i - 1] == right[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def truth_evidence_ok(evidence_id: str, truth: Mapping[str, Any]) -> bool:
    """`truth:<section>:<index>` -> is that row really in `synthetic_truth.json`?"""
    parts = str(evidence_id).split(":")
    if len(parts) != 3 or parts[0] != "truth":
        return False
    section = truth.get(parts[1])
    if not isinstance(section, list):
        return False
    try:
        index = int(parts[2])
    except ValueError:
        return False
    return 0 <= index < len(section)


def offered_keys(path: Mapping[str, Any]) -> set[str]:
    """Every id this path put in front of the forger. Nothing else may be cited.

    Edge endpoints count, and that is not a technicality. A `test_fix` path names its
    `TestExecution`s only on the `HAS_RUN` edges — `XE-10004` is an endpoint, never a node —
    so a question that correctly cites the execution its answer rests on was being rejected
    as `evidence_not_offered`. What the forger can see, the forger may cite.
    """
    keys = {str(n.get("key")) for n in (path.get("nodes") or []) if n.get("key")}
    for edge in path.get("edges") or []:
        keys |= {str(edge.get(end)) for end in ("from", "to") if edge.get(end)}
    keys |= {str(s.get("chunk_id")) for s in (path.get("snippets") or []) if s.get("chunk_id")}
    keys |= {str(t.get("id")) for t in (path.get("truth") or []) if t.get("id")}
    return keys


def mentions(text: str, key: str) -> bool:
    """Does the question name this key? Substring, because that is what 'verbatim' means."""
    if not key:
        return False
    return key.casefold() in (text or "").casefold()


@dataclass
class Verdict:
    ok: bool
    reasons: list[str]
    row: dict[str, Any]
    batch_id: str = ""
    question: Mapping[str, Any] = field(default_factory=dict)

    def as_rejection(self) -> dict[str, Any]:
        return {
            "id": self.row.get("id") or self.question.get("id"),
            "batch": self.batch_id,
            "reasons": list(self.reasons),
            "source_path_id": self.question.get("source_path_id"),
        }


def review_question(
    question: Mapping[str, Any],
    *,
    batch_id: str,
    paths: Mapping[str, Mapping[str, Any]],
    known_keys: set[str],
    truth: Mapping[str, Any],
    schema: Mapping[str, Any],
    seen_ids: set[str] | None = None,
    seen_questions: set[str] | None = None,
) -> Verdict:
    """One question, every reason it is not usable. Reasons are collected, not short-circuited."""
    reasons: list[str] = []
    question_schema = {**schema["$defs"]["question"], "$defs": schema["$defs"]}
    for error in schema_validate(question, question_schema):
        reasons.append(f"schema: {error}")
    if reasons:
        return Verdict(ok=False, reasons=reasons, row={}, batch_id=batch_id, question=question)

    qid = str(question["id"])
    text = str(question["question"])
    normalised = " ".join(words(text))
    if seen_ids and qid in seen_ids:
        reasons.append("duplicate_id")
    if seen_questions and normalised in seen_questions:
        reasons.append("duplicate_question")

    path = paths.get(str(question["source_path_id"]))
    if path is None:
        reasons.append("unknown_path")
        return Verdict(ok=False, reasons=reasons, row={}, batch_id=batch_id, question=question)

    if question["type"] != path.get("type"):
        reasons.append("type_mismatch")
    if question["gold_source"] != path.get("gold_source"):
        reasons.append("gold_source_mismatch")

    offered = offered_keys(path)
    anchors = [str(a) for a in (path.get("anchors") or [])]
    answers = [str(a) for a in (path.get("answers") or [])]
    evidence = [str(e) for e in question["gold_evidence"]]

    for item in evidence:
        if item not in offered:
            reasons.append("evidence_not_offered")
            break
    for item in evidence:
        found = (
            truth_evidence_ok(item, truth) if item.startswith(TRUTH_PREFIX) else item in known_keys
        )
        if not found:
            reasons.append("evidence_missing_in_graph")
            break

    if any(mentions(text, key) for key in answers):
        reasons.append("leak_answer_key")
    leaked = [
        item
        for item in evidence
        if item not in anchors and not item.startswith(TRUTH_PREFIX) and mentions(text, item)
    ]
    if leaked:
        reasons.append("leak_evidence_key")
    if longest_shared_run(text, str(question["gold_answer"])) >= MAX_SHARED_RUN:
        reasons.append("leak_answer_text")

    row = {
        "id": qid,
        "type": question["type"],
        "lang": question["lang"],
        "question": text,
        "gold_answer": question["gold_answer"],
        "gold_evidence": evidence,
        "difficulty": question["difficulty"],
        "expected_strategy": question["expected_strategy"],
        "source_path_id": question["source_path_id"],
        "gold_source": question["gold_source"],
        "origin": "forged",
        "difficulty_source": "forged",
        "gold_derived_by": "agent",
        "shape": path.get("shape"),
        "batch_id": batch_id,
        "anchors": anchors,
    }
    if question.get("notes"):
        row["notes"] = question["notes"]
    if question.get("substituted_for"):
        row["substituted_for"] = str(question["substituted_for"])
    return Verdict(ok=not reasons, reasons=reasons, row=row, batch_id=batch_id, question=question)


# ---------------------------------------------------------------------------- balancing


def select_balanced(
    verdicts: Sequence[Verdict], need: Mapping[tuple[str, str], int]
) -> tuple[list[Verdict], list[Verdict]]:
    """Keep what each (type, language) cell asked for; the slack becomes surplus.

    Sorted by question id first, so merging the same batches twice keeps the same
    questions — a set that changes between two runs of the same command is not a set.
    """
    kept: list[Verdict] = []
    surplus: list[Verdict] = []
    left = {cell: int(n) for cell, n in need.items()}
    for verdict in sorted(verdicts, key=lambda v: str(v.row.get("id"))):
        cell = (str(verdict.row.get("type")), str(verdict.row.get("lang")))
        if left.get(cell, 0) > 0:
            left[cell] -= 1
            kept.append(verdict)
        else:
            surplus.append(verdict)
    return kept, surplus


# -------------------------------------------------------------------------- competency


def competency_row(row: Mapping[str, Any], gold: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One of Plan 2's 19 questions on the Plan 3 schema, with whatever gold exists for it.

    `gold` is a row of `data/eval/competency_gold.jsonl`, which
    `brain eval questions gold-competency` derives from the graph with a fixed query. When
    there is none — or the derivation came back empty — the row stays `pending` with its
    reason attached, because a null that says why is worth more than a gold nobody checked.
    """
    qtype = str(row.get("type"))
    out = {
        "id": str(row.get("id")),
        "type": qtype,
        "lang": str(row.get("lang")),
        "question": str(row.get("question")),
        "gold_answer": None,
        "gold_evidence": [],
        "difficulty": DIFFICULTY_DEFAULT.get(qtype, 2),
        "expected_strategy": str(row.get("expected_strategy")),
        "source_path_id": None,
        "gold_source": "pending",
        "origin": "competency",
        "difficulty_source": "type_default",
        "shape": None,
        "batch_id": None,
        "anchors": [str(a) for a in (row.get("anchors") or [])],
    }
    if row.get("pair"):
        out["pair"] = str(row["pair"])
    if gold:
        out["gold_derived_by"] = str(gold.get("gold_derived_by") or "code")
        if gold.get("gold_note"):
            out["gold_note"] = str(gold["gold_note"])
        if gold.get("gold_source") != "pending" and gold.get("gold_answer"):
            out["gold_answer"] = str(gold["gold_answer"])
            out["gold_evidence"] = [str(e) for e in (gold.get("gold_evidence") or [])]
            out["gold_source"] = str(gold.get("gold_source") or "graph")
            if gold.get("gold_query"):
                out["gold_query"] = str(gold["gold_query"])
    return out


# ------------------------------------------------------------------------ graph checks


def existing_keys(ctx: RetrieveContext, keys: Iterable[str]) -> set[str]:
    """Which of these ids the graph actually holds — one indexed lookup per label."""
    wanted = sorted({str(k) for k in keys if k and not str(k).startswith(TRUTH_PREFIX)})
    if not wanted:
        return set()
    found: set[str] = set()
    for label, prop in KEY_LOOKUPS:
        rows = ctx.read(
            f"MATCH (n:{ctx.label(label)}) WHERE n.`{prop}` IN $keys RETURN n.`{prop}` AS key",
            keys=wanted,
        )
        found |= {str(r["key"]) for r in rows}
        if len(found) == len(wanted):
            return found
    numeric = [k for k in wanted if k.isdigit() and k not in found]
    if numeric:
        rows = ctx.read(
            f"MATCH (n:{ctx.label('PullRequest')}) WHERE toString(n.`number`) IN $keys "
            "RETURN toString(n.`number`) AS key",
            keys=numeric,
        )
        found |= {str(r["key"]) for r in rows}
    return found


# ------------------------------------------------------------------------------ runner


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_questions(rows: Sequence[Mapping[str, Any]], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return sha256_of_text(text)


def run_merge(
    *,
    ctx: RetrieveContext | None,
    batches_dir: Path,
    reports_dir: Path,
    eval_dir: Path,
    competency_rows: Sequence[Mapping[str, Any]],
    demand: Demand,
    truth: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read every batch output, decide question by question, and write the set and the report."""
    from brain.eval.gold_competency import GOLD_FILE, read_gold

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    root = Path(batches_dir) / BATCH_DIR
    gold = read_gold(Path(eval_dir) / GOLD_FILE)
    outputs, failures = read_outputs(root)

    all_paths: dict[str, Mapping[str, Any]] = {}
    for item in outputs:
        all_paths.update(paths_in(item["input"]))

    cited = {
        str(e)
        for item in outputs
        for q in item["output"]["questions"]
        if isinstance(q, dict)
        for e in (q.get("gold_evidence") or [])
    }
    known = existing_keys(ctx, cited) if (ctx is not None and cited) else set()

    verdicts: list[Verdict] = []
    seen_ids: set[str] = set()
    seen_questions: set[str] = set()
    for item in outputs:
        for raw in item["output"]["questions"]:
            if not isinstance(raw, dict):
                failures.append(
                    {"batch": item["batch_id"], "reason": "a question is not an object"}
                )
                continue
            verdict = review_question(
                raw,
                batch_id=item["batch_id"],
                paths=all_paths,
                known_keys=known,
                truth=truth or {},
                schema=schema,
                seen_ids=seen_ids,
                seen_questions=seen_questions,
            )
            verdicts.append(verdict)
            if verdict.ok:
                seen_ids.add(str(verdict.row["id"]))
                seen_questions.add(" ".join(words(str(verdict.row["question"]))))

    accepted = [v for v in verdicts if v.ok]
    rejected = [v for v in verdicts if not v.ok]
    need = {(c.type, c.lang): c.need for c in demand.cells}
    kept, surplus = select_balanced(accepted, need)

    rows = [competency_row(r, gold.get(str(r.get("id")))) for r in competency_rows]
    rows += [v.row for v in kept]
    row_schema = {**schema["$defs"]["row"], "$defs": schema["$defs"]}
    invalid = [
        {"id": r.get("id"), "errors": errs}
        for r in rows
        if (errs := schema_validate(r, row_schema))
    ]
    if invalid:
        raise MergeError(
            f"{len(invalid)} merged rows do not validate against $defs/row: {invalid[:3]}"
        )

    questions_path = Path(eval_dir) / QUESTIONS_FILE
    sha = write_questions(rows, questions_path)

    report = build_report(
        rows=rows,
        kept=kept,
        surplus=surplus,
        rejected=rejected,
        failures=failures,
        demand=demand,
        planned=batches_planned(root),
        answered=[item["batch_id"] for item in outputs],
        questions_path=questions_path,
        sha=sha,
    )
    write_section(Path(reports_dir), "merge", report)
    return report


def build_report(
    *,
    rows: Sequence[Mapping[str, Any]],
    kept: Sequence[Verdict],
    surplus: Sequence[Verdict],
    rejected: Sequence[Verdict],
    failures: Sequence[Mapping[str, str]],
    demand: Demand,
    planned: Sequence[str],
    answered: Sequence[str],
    questions_path: Path,
    sha: str,
) -> dict[str, Any]:
    by_type = {t: sum(1 for r in rows if r["type"] == t) for t in QUESTION_TYPES}
    by_lang = {lang: sum(1 for r in rows if r["lang"] == lang) for lang in LANGS}
    by_cell = {
        f"{t}/{lang}": sum(1 for r in rows if r["type"] == t and r["lang"] == lang)
        for t in QUESTION_TYPES
        for lang in LANGS
    }
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[str(r["gold_source"])] = by_source.get(str(r["gold_source"])) or 0
        by_source[str(r["gold_source"])] += 1
    by_origin: dict[str, int] = {}
    for r in rows:
        by_origin[str(r["origin"])] = by_origin.get(str(r["origin"]), 0) + 1
    by_reason: dict[str, int] = {}
    for v in rejected:
        for reason in v.reasons:
            head = reason.split(":", 1)[0]
            by_reason[head] = by_reason.get(head, 0) + 1

    shortfall = {
        f"{c.type}/{c.lang}": c.need
        - sum(1 for v in kept if (v.row["type"], v.row["lang"]) == (c.type, c.lang))
        for c in demand.cells
    }
    shortfall = {k: v for k, v in shortfall.items() if v > 0}
    leaked = sum(1 for v in rejected if any(r.startswith("leak_") for r in v.reasons))
    target_total = demand.existing_total + demand.new_total
    # Planner ruling: the accepted target is the most even split of N over the five types —
    # computed from zero, so it is a property of the set and not of whatever the 19 happened
    # to start at — with Hebrew at least a third. `per_type_goal` (8) stays in the report as
    # what the plan asked for and what five types × 8 would have cost, but it is not the bar.
    even_split = water_fill([0] * len(QUESTION_TYPES), len(rows))
    split_met = sorted(by_type.values(), reverse=True) == even_split
    hebrew_floor = max(math.ceil(len(rows) / 3), demand.hebrew_min)
    pending_gold = [r["id"] for r in rows if r["gold_source"] == "pending" or not r["gold_answer"]]
    checks = [
        {
            "name": "every planned batch answered",
            "ok": set(planned) == set(answered),
            "detail": f"{len(answered)}/{len(planned)} batches have an output",
            "met": "OK" if set(planned) == set(answered) else "PARTIAL",
        },
        {
            "name": "question count",
            "ok": len(rows) == target_total,
            "detail": f"{len(rows)} of {target_total} ({demand.existing_total} competency "
            f"+ {demand.new_total} forged)",
            "met": "OK" if len(rows) == target_total else "SHORT",
        },
        {
            "name": "per-type balance",
            "ok": split_met,
            "detail": f"{by_type} — the most even split of {len(rows)} over "
            f"{len(QUESTION_TYPES)} types is {even_split}",
            "met": "OK" if split_met else "SHORT",
            "gate": True,
        },
        {
            "name": f"Hebrew >= {hebrew_floor}",
            "ok": by_lang["he"] >= hebrew_floor,
            "detail": f"{by_lang['he']} Hebrew of {len(rows)} (floor is a third, "
            f"ceil({len(rows)}/3) = {math.ceil(len(rows) / 3)})",
            "met": "OK" if by_lang["he"] >= hebrew_floor else "SHORT",
            "gate": True,
        },
        {
            "name": "every row has gold",
            "ok": not pending_gold,
            "detail": f"{len(rows) - len(pending_gold)}/{len(rows)} rows carry a gold answer"
            + (f" — still pending: {', '.join(pending_gold[:6])}" if pending_gold else ""),
            "met": "OK" if not pending_gold else "PENDING",
            "gate": True,
        },
        {
            "name": "no failed batches",
            "ok": not failures,
            "detail": f"{len(failures)} batches could not be read",
            "met": "OK" if not failures else "FAILED",
            "gate": True,
        },
        {
            "name": "gold_evidence exists",
            "ok": not any("evidence_missing_in_graph" in v.reasons for v in rejected),
            "detail": f"{sum(1 for v in rejected if 'evidence_missing_in_graph' in v.reasons)} "
            "questions cited a key the graph does not hold (rejected)",
            "met": "OK",
        },
        {
            "name": "no leakage",
            "ok": True,
            "detail": f"{leaked} questions leaked their answer (rejected)",
            "met": "OK",
        },
    ]
    # Not `all(checks)`: "every planned batch answered" and "question count" are progress,
    # and a set can be complete without them (a merge of a reduced --n, a batch the planner
    # dropped). The three below are the ruling, and the one that used to be missing entirely
    # is `every row has gold` — 32 questions with 19 pending golds read as complete before.
    complete = split_met and by_lang["he"] >= hebrew_floor and not pending_gold and not failures
    return {
        "step": "eval.questions.merge",
        "generated_at": utc_now_iso(),
        "questions_path": str(questions_path),
        "sha256": sha,
        "complete": complete,
        "target": {
            "rule": "the most even split of N over the five types, Hebrew at least a third",
            "even_split": even_split,
            "split_met": split_met,
            "hebrew_floor": hebrew_floor,
            "pending_gold": pending_gold,
            # Informational: the plan asked for 8 per type, which five types cannot have at
            # 32 questions. Kept so the number in the plan and the number in the report can
            # be told apart by a reader who only has the report.
            "per_type_goal_8": by_type == dict.fromkeys(QUESTION_TYPES, demand.per_type_goal),
            "per_type_goal": demand.per_type_goal,
            "questions_for_per_type_goal": demand.questions_for_goal,
        },
        "totals": {
            "questions": len(rows),
            "target": target_total,
            "accepted_forged": len(kept),
            "surplus_forged": len(surplus),
            "rejected_forged": len(rejected),
            "failed_batches": len(failures),
        },
        "counts": {
            "by_type": by_type,
            "by_lang": by_lang,
            "by_cell": by_cell,
            "by_gold_source": dict(sorted(by_source.items())),
            "by_origin": dict(sorted(by_origin.items())),
        },
        "plan": demand.as_dict(),
        "shortfall": shortfall,
        "rejected": [v.as_rejection() for v in rejected],
        "rejected_by_reason": dict(sorted(by_reason.items())),
        "surplus": [
            {"id": v.row.get("id"), "type": v.row.get("type"), "lang": v.row.get("lang")}
            for v in surplus
        ],
        "failed_batches": [dict(f) for f in failures],
        "batches": {"planned": list(planned), "answered": list(answered)},
        "checks": checks,
    }


def summarize_merge(report: Mapping[str, Any]) -> str:
    t = report["totals"]
    c = report["counts"]
    lines = [
        f"questions merge: {t['questions']}/{t['target']} questions "
        f"({t['accepted_forged']} forged accepted, {t['surplus_forged']} surplus, "
        f"{t['rejected_forged']} rejected, {t['failed_batches']} failed batches)",
        "  by type: " + "  ".join(f"{k}:{v}" for k, v in c["by_type"].items()),
        "  by lang: "
        + "  ".join(f"{k}:{v}" for k, v in c["by_lang"].items())
        + "   by gold: "
        + "  ".join(f"{k}:{v}" for k, v in c["by_gold_source"].items()),
    ]
    if report["rejected_by_reason"]:
        lines.append(
            "  rejected: " + "  ".join(f"{k}×{v}" for k, v in report["rejected_by_reason"].items())
        )
    for check in report["checks"]:
        lines.append(f"  [{check['met']}] {check['name']}: {check['detail']}")
    lines.append(f"  {report['questions_path']}  sha256 {report['sha256'][:12]}")
    return "\n".join(lines)


def read_questions(path: Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line]


__all__ = [
    "DIFFICULTY_DEFAULT",
    "MAX_SHARED_RUN",
    "MergeError",
    "Verdict",
    "build_report",
    "competency_row",
    "existing_keys",
    "longest_shared_run",
    "read_outputs",
    "read_questions",
    "review_question",
    "run_merge",
    "select_balanced",
    "summarize_merge",
    "truth_evidence_ok",
    "write_questions",
    "write_json_atomic",
]
