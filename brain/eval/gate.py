"""`data/reports/plan2_gate.json` — what nineteen agent answers actually contain.

Every number here is derived; the one column code cannot fill is `planner_verdicts`, which
is the planner's judgement of correct / partial / wrong (step 12 decision 4 — the blind
judge is Plan 3, not this). It starts empty and is *carried forward* on a rerun, because a
checker that deletes the only hand-written column in the report is a checker nobody runs
twice.

Two criteria decide the exit code, per the brief: every question answered, and every answer
carrying at least one citation that resolves. The rest — the ≥90% validity rate from the
step brief, the `strategy:` line, the answer's language, the share of sentences that carry a
citation — are measured and reported with `"gate": false`. That distinction is deliberate:
a number that is not allowed to fail the build is still a number the planner has to read.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.common.stamp import head_sha
from brain.eval.answers import Answer
from brain.eval.citations import Citation, find_citations, sentence_stats, unique
from brain.eval.verify import Verdict

STEP = "plan2-gate"
VERDICT_VALUES: tuple[str, ...] = ("correct", "partial", "wrong")
#: The step brief's acceptance criterion. Reported, not gated (see the module docstring).
VALIDITY_TARGET = 90.0


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_questions(path: Path) -> list[dict[str, Any]]:
    """The competency set, in file order. Malformed lines are an error, not a skipped row."""
    path = Path(path)
    questions: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number} is not JSON: {exc}") from exc
        if not row.get("id"):
            raise ValueError(f"{path}:{number} has no `id`")
        questions.append(row)
    return questions


def read_previous(path: Path) -> dict[str, Any]:
    """The last report, for the verdicts it holds. Unreadable is the same as absent."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return previous if isinstance(previous, dict) else {}


# ----------------------------------------------------------------------------- per question


def _citation_rows(
    citations: list[Citation], verdicts: dict[str, Verdict]
) -> tuple[list[dict[str, Any]], Counter[str]]:
    occurrences = Counter(c.id for c in citations)
    rows: list[dict[str, Any]] = []
    for citation in unique(citations):
        verdict = verdicts.get(citation.id) or Verdict(
            False, reason="the citation was never checked against the graph"
        )
        rows.append(
            {
                "text": citation.text,
                "kind": citation.kind,
                "value": citation.value,
                "occurrences": occurrences[citation.id],
                **verdict.as_dict(),
            }
        )
    return rows, Counter(row["kind"] for row in rows)


def _question_row(
    question: dict[str, Any], answer: Answer | None, verdicts: dict[str, Verdict]
) -> dict[str, Any]:
    """One question's whole line in the report — measured, with no gaps filled by guessing."""
    expected_lang = question.get("lang")
    if answer is None:
        return {
            "id": question["id"],
            "question": question.get("question", ""),
            "type": question.get("type"),
            "expected_strategy": question.get("expected_strategy"),
            "answered": False,
            "path": None,
            "answer_modified_at": None,
            "answer_chars": 0,
            "started_at": None,
            "finished_at": None,
            "latency_ms": None,
            "latency_source": None,
            "lang": None,
            "expected_lang": expected_lang,
            "lang_ok": False,
            "tools": [],
            "strategies": [],
            "strategy_line": None,
            "citations": {
                "found": 0,
                "occurrences": 0,
                "valid": 0,
                "invalid": 0,
                "by_kind": {},
                "items": [],
                "invalid_list": [],
            },
            "sentences": {
                "total": 0,
                "with_citation": 0,
                "without_citation": 0,
                "pct_with_citation": 0.0,
            },
            "problems": ["no answer file"],
        }

    citations = find_citations(answer.body)
    items, by_kind = _citation_rows(citations, verdicts)
    valid = sum(1 for row in items if row["valid"])
    return {
        "id": question["id"],
        "question": question.get("question", ""),
        "type": question.get("type"),
        "expected_strategy": question.get("expected_strategy"),
        "answered": True,
        "path": str(answer.path),
        "answer_modified_at": answer.modified_at,
        "answer_chars": len(answer.body),
        "started_at": answer.front.get("started_at"),
        "finished_at": answer.front.get("finished_at"),
        "latency_ms": answer.latency_ms,
        "latency_source": answer.latency_source,
        "lang": answer.lang,
        "expected_lang": expected_lang,
        "lang_ok": bool(expected_lang) and answer.lang == expected_lang,
        "tools": list(answer.tools),
        "strategies": list(answer.strategies),
        "strategy_line": answer.strategy_line,
        "citations": {
            "found": len(items),
            "occurrences": len(citations),
            "valid": valid,
            "invalid": len(items) - valid,
            "by_kind": dict(sorted(by_kind.items())),
            "items": items,
            "invalid_list": [
                {"text": row["text"], "kind": row["kind"], "reason": row["reason"]}
                for row in items
                if not row["valid"]
            ],
        },
        "sentences": sentence_stats(answer.body),
        "problems": list(answer.problems),
    }


# ---------------------------------------------------------------------------------- totals


def _percentiles(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"measured": 0, "p50": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "measured": len(ordered),
        "p50": ordered[len(ordered) // 2],
        "min": ordered[0],
        "max": ordered[-1],
    }


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answered = [r for r in rows if r["answered"]]
    found = sum(r["citations"]["found"] for r in rows)
    valid = sum(r["citations"]["valid"] for r in rows)
    tools: Counter[str] = Counter()
    for row in answered:
        tools.update(set(row["tools"]))
    return {
        "questions": len(rows),
        "answered": len(answered),
        "missing": [r["id"] for r in rows if not r["answered"]],
        "with_at_least_one_valid_citation": sum(1 for r in rows if r["citations"]["valid"] > 0),
        "citations_found": found,
        "citations_occurrences": sum(r["citations"]["occurrences"] for r in rows),
        "citations_valid": valid,
        "citations_invalid": found - valid,
        "validity_rate": round(100 * valid / found, 2) if found else 0.0,
        "by_kind": dict(
            sorted(sum((Counter(r["citations"]["by_kind"]) for r in rows), Counter()).items())
        ),
        "sentences_total": sum(r["sentences"]["total"] for r in rows),
        "sentences_with_citation": sum(r["sentences"]["with_citation"] for r in rows),
        "sentences_without_citation": sum(r["sentences"]["without_citation"] for r in rows),
        "tools_used": dict(sorted(tools.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_lang": dict(Counter(r["lang"] for r in answered if r["lang"])),
        "latency_ms": _percentiles([r["latency_ms"] for r in answered if r["latency_ms"]]),
        "with_a_strategy_line": sum(1 for r in answered if r["strategy_line"]),
        "answered_in_the_questions_language": sum(1 for r in answered if r["lang_ok"]),
    }


# ----------------------------------------------------------------------------------- gate


def _detail(failed: list[str], label: str, missing: list[str], clean: str) -> str:
    """Never "all present" while nineteen files are absent — say which half failed.

    A criterion whose `ok` is false because answers are missing used to read `all present`,
    which is true of the answers that exist and worthless to whoever is reading the row.
    """
    parts = []
    if failed:
        parts.append(f"{label}: {', '.join(failed)}")
    if missing:
        parts.append(f"no answer file: {', '.join(missing)}")
    return "; ".join(parts) if parts else clean


def _criteria(rows: list[dict[str, Any]], totals: dict[str, Any]) -> list[dict[str, Any]]:
    no_valid = [r["id"] for r in rows if r["answered"] and r["citations"]["valid"] == 0]
    missing = totals["missing"]
    no_strategy = [r["id"] for r in rows if r["answered"] and not r["strategy_line"]]
    wrong_lang = [r["id"] for r in rows if r["answered"] and not r["lang_ok"]]
    uncited = totals["sentences_without_citation"]
    return [
        {
            "name": "every_question_answered",
            "requirement": f"{totals['questions']}/{totals['questions']} answer files",
            "value": f"{totals['answered']}/{totals['questions']}",
            "ok": not missing,
            "gate": True,
            "detail": f"missing: {', '.join(missing)}" if missing else "every question has a file",
        },
        {
            "name": "every_answer_has_a_valid_citation",
            "requirement": "≥1 citation that resolves in the graph, per question",
            "value": f"{totals['with_at_least_one_valid_citation']}/{totals['questions']}",
            "ok": not missing and not no_valid,
            "gate": True,
            "detail": _detail(
                no_valid, "no valid citation", missing, "every answer cites something that exists"
            ),
        },
        {
            "name": "citation_validity_rate_at_least_90",
            "requirement": f"≥{VALIDITY_TARGET:.0f}% of distinct citations resolve",
            "value": f"{totals['validity_rate']}%",
            "ok": totals["validity_rate"] >= VALIDITY_TARGET,
            "gate": False,
            "detail": (
                f"{totals['citations_valid']}/{totals['citations_found']}"
                if totals["citations_found"]
                else "no citations to check"
            ),
        },
        {
            "name": "every_answer_has_a_strategy_line",
            "requirement": "a `strategy:` line naming the tools used",
            "value": f"{totals['with_a_strategy_line']}/{totals['answered']}",
            "ok": not no_strategy and not missing,
            "gate": False,
            "detail": _detail(no_strategy, "no `strategy:` line", missing, "all present"),
        },
        {
            "name": "every_answer_is_in_the_questions_language",
            "requirement": "answer in the language of the question",
            "value": f"{totals['answered_in_the_questions_language']}/{totals['answered']}",
            "ok": not wrong_lang and not missing,
            "gate": False,
            "detail": _detail(wrong_lang, "language mismatch", missing, "all match"),
        },
        {
            "name": "every_sentence_carries_a_citation",
            "requirement": "plan decision 2: cite in every factual sentence",
            "value": (
                f"{totals['sentences_with_citation']}/{totals['sentences_total']}"
                if totals["sentences_total"]
                else "0/0"
            ),
            "ok": uncited == 0 and totals["sentences_total"] > 0,
            "gate": False,
            "detail": (
                f"{uncited} of {totals['sentences_total']} sentences carry no citation"
                if totals["sentences_total"]
                else "no answer text to measure"
            ),
        },
    ]


def criterion(report: dict[str, Any], name: str) -> dict[str, Any]:
    """One named gate criterion, for a caller that wants to assert on it."""
    return next(c for c in report["gate"]["criteria"] if c["name"] == name)


def exit_code(report: dict[str, Any]) -> int:
    return 0 if report["gate"]["ok"] else 1


# ----------------------------------------------------------------------------- the report


def build(
    questions: list[dict[str, Any]],
    answers: dict[str, Answer],
    verify: Callable[[list[Citation]], dict[str, Verdict]],
    *,
    answers_dir: Path,
    questions_path: Path,
    report_path: Path,
    previous: dict[str, Any] | None = None,
    sha: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """The whole report. `verify` is called once, with every distinct citation at once."""
    every: list[Citation] = []
    for question in questions:
        answer = answers.get(question["id"])
        if answer is not None:
            every.extend(find_citations(answer.body))
    verdicts = verify(unique(every))

    rows = [_question_row(q, answers.get(q["id"]), verdicts) for q in questions]
    totals = _totals(rows)
    criteria = _criteria(rows, totals)
    previous = previous or {}
    ids = {q["id"] for q in questions}
    carried = {
        qid: verdict
        for qid, verdict in (previous.get("planner_verdicts") or {}).items()
        if qid in ids and verdict in VERDICT_VALUES
    }
    extra = sorted(set(answers) - ids)
    return {
        "step": STEP,
        "generated_at": generated_at or utc_now_iso(),
        "sha": head_sha() if sha is None else sha,
        "answers_dir": str(answers_dir),
        "questions_path": str(questions_path),
        "report_path": str(report_path),
        "answer_files_with_no_question": extra,
        "questions": rows,
        "totals": totals,
        "gate": {
            "ok": all(c["ok"] for c in criteria if c["gate"]),
            "passed": sum(1 for c in criteria if c["ok"]),
            "total": len(criteria),
            "criteria": criteria,
            "gated": [c["name"] for c in criteria if c["gate"]],
        },
        "planner_verdicts": carried,
        "planner_verdicts_note": {
            "values": list(VERDICT_VALUES),
            "how": (
                "step 12 decision 4: the planner reads the answer and its evidence and writes "
                "one of these per question id. The blind rubric judge is Plan 3, not this."
            ),
            "carried_from": previous.get("generated_at") if carried else None,
        },
    }


def write(report: dict[str, Any], path: Path) -> Path:
    """Temp + rename, so a crash never leaves a half-written report behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def summary_lines(report: dict[str, Any]) -> Iterable[str]:
    """What the command prints. Short enough to read, specific enough to act on."""
    totals = report["totals"]
    yield (
        f"answers: {totals['answered']}/{totals['questions']}"
        + (f" · missing: {', '.join(totals['missing'])}" if totals["missing"] else "")
    )
    yield (
        f"citations: {totals['citations_valid']}/{totals['citations_found']} valid "
        f"({totals['validity_rate']}%) · "
        f"{totals['with_at_least_one_valid_citation']}/{totals['questions']} answers with ≥1"
    )
    yield (
        f"sentences: {totals['sentences_with_citation']}/{totals['sentences_total']} cited · "
        f"latency p50 {totals['latency_ms']['p50']} ms"
    )
    for check in report["gate"]["criteria"]:
        mark = ("OK  " if check["ok"] else "FAIL") + ("" if check["gate"] else " (not gated)")
        yield f"  [{mark}] {check['name']}: {check['detail']}"
