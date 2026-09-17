"""Mode A of spec §5.3 — every strategy on every question, under one context budget.

This is the measurement the plan calls its central learning product: a matrix of strategy ×
question type whose cells were produced by running, not by arguing. Four rules make the
cells comparable, and each of them is a line of code here rather than a note in the report:

1. **One budget.** Every strategy's `Result` is already packed to ~4k tokens by
   `brain/retrieve/pack.py`; nothing here widens it for anybody. `context_tokens` is what
   the answering agent would actually be handed.
2. **One corpus.** `include_synthetic=True` for all of them (plan decision 6), so the Xray
   and ADO layer is in scope for the vector baseline exactly as it is for the graph
   strategies.
3. **No silent skips.** A strategy that cannot apply to a question produces an `n/a` record
   *with a reason*, on disk, next to the runs that worked. An empty cell in the matrix that
   nobody can explain is worse than a low number.
4. **A rerun proves itself.** Every record is compared with its predecessor with latency and
   the clock removed. `unchanged` is the expected result and is reported per file; a
   `changed` file is kept until `--force`, so a surprise is something you read before it
   overwrites the evidence.

### The three ways a strategy does not apply

* **S4** answers from the Cypher example bank in mode A (`brain/retrieve/text2cypher.py`).
  When the closest example is below `MIN_SIMILARITY`, re-pointing its parameters would
  answer a question nobody asked — so that is an `n/a` with the similarity in the reason,
  decided *before* the run, which also avoids `ask()`'s runtime fallback to S3.
* **S6** needs a temporal template to bind: a key and a date, two versions and a component,
  or a key to walk. `runner._temporal` says so itself by falling back to S2, and a fallback
  is detected here as "`Result.strategy` is not the strategy we asked for".
* **S5** reduces community reports into a theme. Its scope is the questions that ask for a
  shape of the corpus — the router's thematic rules, or a question typed `global`. Widened
  with `--s5-scope all` when the planner wants the full row measured anyway.

The generic form of the second bullet catches every other fallback too: if we asked for
`sN` and `Result.strategy` came back as something else, the record is `n/a` with whatever
`route.fallback_reason` said. A strategy is never credited with another strategy's answer.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.eval import cost as cost_mod
from brain.eval import metrics

#: The seven columns of mode A. `s1r` is S1 plus the local cross-encoder — the baseline the
#: whole plan measures the graph strategies against (plan decision 3), kept as its own column
#: so "did the reranker earn its latency" is a number rather than an opinion.
STRATEGIES: tuple[str, ...] = ("s1", "s1r", "s2", "s3", "s4", "s5", "s6")
BASELINE = "s1r"
MODE = "fixed"
REPORT_NAME = "eval_retrieval.json"
RUNS_DIRNAME = "fixed"
#: The `log_mode` these calls write to `data/logs/retrieval.jsonl`, so an evaluation sweep is
#: distinguishable from an agent's traffic in the same file.
LOG_MODE = "eval"

#: Top-level stamps that say *when* and *at which commit*, not *what* — excluded from the
#: rerun comparison. Nested under `result`, only `sha` would be a content hash, so these two
#: are stripped at the top level only.
VOLATILE_TOP: frozenset[str] = frozenset({"generated_at", "sha"})
#: Every millisecond reading, at any depth, is a clock and not a result. The one that made
#: this rule necessary was `result.route.query_ms`, which `cypher_guard.run_cypher` records
#: inside the envelope: S4 reported 17 "changed" files on a rerun that had retrieved exactly
#: the same rows. A comparison that calls a stopwatch a difference cannot prove determinism.
VOLATILE_SUFFIX = "_ms"


class RunError(RuntimeError):
    """The sweep cannot start: no questions, an unknown strategy, an unreadable set."""


# ----------------------------------------------------------------------------- strategies


def base_strategy(strategy: str) -> str:
    """The retrieval `s1`–`s6` behind a column name. `s1r` is `s1` with the reranker on."""
    return "s1" if strategy == "s1r" else strategy


def parse_strategies(value: str | None) -> tuple[str, ...]:
    """`"s1,s3"` -> `("s1", "s3")`. An unknown name is an error, never a quiet no-op."""
    if not value:
        return STRATEGIES
    wanted = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = [name for name in wanted if name not in STRATEGIES]
    if unknown:
        raise ValueError(f"unknown strategy {', '.join(unknown)}; expected {', '.join(STRATEGIES)}")
    return tuple(dict.fromkeys(wanted))


def run_path(out_dir: Path | str, qid: str, strategy: str) -> Path:
    return Path(out_dir) / RUNS_DIRNAME / f"{qid}.{strategy}.json"


# ----------------------------------------------------------------------------- applicability


@dataclass(frozen=True)
class Plan:
    ok: bool
    reason: str = ""


#: Router rules that mean "this question asks for a shape of the corpus" (`route.py`).
THEMATIC_RULES: frozenset[str] = frozenset(
    {"themes", "overview", "he-themes", "he-overview", "theme"}
)


def applicability(
    row: dict[str, Any],
    strategy: str,
    suggestion: dict[str, Any],
    *,
    s5_scope: str = "thematic",
) -> Plan:
    """Whether this strategy applies to this question *before* anything is run.

    Only S5 can be decided from the sentence alone. S4 needs the example bank (decided in
    `run_one`, which has the embedder) and S6 needs the binding attempt itself.
    """
    if strategy != "s5" or s5_scope == "all":
        return Plan(True)
    if str(row.get("type")) == "global":
        return Plan(True)
    if str(suggestion.get("rule") or "") in THEMATIC_RULES:
        return Plan(True)
    return Plan(
        False,
        "S5 reduces community reports into a theme; this question carries no thematic "
        f"signal (router rule {suggestion.get('rule') or 'none'!r}) and is not typed `global`",
    )


# ----------------------------------------------------------------------------- one run


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _base_record(row: dict[str, Any], strategy: str, suggestion: dict[str, Any], sha: str) -> dict:
    record: dict[str, Any] = {
        "qid": row.get("id"),
        "strategy": strategy,
        "mode": MODE,
        "question": row.get("question"),
        "type": row.get("type"),
        "lang": row.get("lang"),
        "gold_source": row.get("gold_source"),
        "gold_evidence": list(row.get("gold_evidence") or []),
        "expected_strategy": row.get("expected_strategy"),
        "origin": row.get("origin"),
        "route": suggestion,
        "generated_at": _now(),
        "sha": sha,
    }
    if row.get("pair"):
        record["pair"] = row["pair"]
    return record


def _na(record: dict[str, Any], reason: str, *, executed: str | None = None, **extra) -> dict:
    record.update(
        {
            "status": "n/a",
            "reason": reason,
            "executed": executed,
            "result": None,
            "items": 0,
            "latency_ms": None,
            "context_tokens": 0,
            "cypher_count": 0,
            "tool_calls": 0,
            "truncated": False,
        }
    )
    record.update(extra)
    return record


def run_one(
    ctx: Any,
    row: dict[str, Any],
    strategy: str,
    *,
    ask: Callable[..., Any],
    select_example: Callable[..., Any],
    k: int = 10,
    s5_scope: str = "thematic",
    min_similarity: float | None = None,
    sha: str = "",
    log: bool = True,
) -> dict[str, Any]:
    """Run one (question, strategy) and return its record — `ok` or `n/a`, never nothing."""
    from brain.retrieve.pack import BUDGET_TOKENS, total_tokens
    from brain.retrieve.route import route
    from brain.retrieve.text2cypher import MIN_SIMILARITY
    from brain.retrieve.types import Item, RetrieveError

    threshold = MIN_SIMILARITY if min_similarity is None else min_similarity
    question = str(row.get("question") or "")
    suggestion = route(question)
    record = _base_record(row, strategy, suggestion, sha)
    record["budget_tokens"] = BUDGET_TOKENS
    record["include_synthetic"] = bool(getattr(ctx, "include_synthetic", True))
    record["k"] = k

    plan = applicability(row, strategy, suggestion, s5_scope=s5_scope)
    if not plan.ok:
        return _na(record, plan.reason)

    example_note: dict[str, Any] | None = None
    if strategy == "s4":
        # Decided before the run: `ask()` would otherwise fall back to S3/impact and hand us
        # another strategy's answer under S4's name.
        bank_type = _bank_type(row.get("type"))
        try:
            example, similarity = select_example(ctx, question, bank_type)
        except (RetrieveError, ValueError) as exc:
            return _na(record, f"the Cypher example bank could not be read: {exc}")
        if example is None:
            return _na(record, "the Cypher example bank is empty")
        if similarity < threshold:
            return _na(
                record,
                f"no bank example is close enough to this question (best {example['id']} at "
                f"{similarity:.2f} < {threshold})",
            )
        example_note = {"id": example["id"], "similarity": round(float(similarity), 4)}

    try:
        result = ask(
            ctx,
            question,
            strategy=base_strategy(strategy),
            k=k,
            rerank=strategy == "s1r",
            log_mode=LOG_MODE,
            log=log,
        )
    except RetrieveError as exc:
        return _na(record, str(exc))

    executed = getattr(result, "strategy", None)
    trace = getattr(result, "route", None) or {}
    if executed != base_strategy(strategy):
        # `runner.ask` records why it fell back; a strategy is never credited with another's
        # answer, so this is an `n/a` with that reason rather than a run under the wrong name.
        reason = str(
            trace.get("fallback_reason") or f"{strategy} fell back to {executed}: no template bound"
        )
        return _na(record, reason, executed=executed, attempt_latency_ms=result.latency_ms)

    items: list[Item] = list(result.items)
    dumped = result.model_dump()
    record.update(
        {
            "status": "ok",
            "reason": None,
            "executed": executed,
            "result": dumped,
            "items": len(items),
            "latency_ms": int(result.latency_ms),
            "context_tokens": total_tokens(items),
            "cypher_count": len(result.cypher_used or []),
            "tool_calls": 1,
            "truncated": bool(result.truncated),
        }
    )
    if example_note:
        record["example"] = example_note
    return record


def _bank_type(question_type: Any) -> str | None:
    """The example bank knows four types and not `global`; an unknown type searches all."""
    from brain.retrieve.examples import QUESTION_TYPES

    name = str(question_type or "")
    return name if name in QUESTION_TYPES else None


# ----------------------------------------------------------------------------- idempotency


def _without_clocks(value: Any) -> Any:
    """`value` with every `*_ms` key dropped, however deep it is nested."""
    if isinstance(value, dict):
        return {k: _without_clocks(v) for k, v in value.items() if not k.endswith(VOLATILE_SUFFIX)}
    if isinstance(value, list):
        return [_without_clocks(v) for v in value]
    return value


def comparable(record: dict[str, Any]) -> dict[str, Any]:
    """The record with every clock and the commit removed — what a rerun must reproduce."""
    return _without_clocks({k: v for k, v in record.items() if k not in VOLATILE_TOP})


def read_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save(path: Path, record: dict[str, Any], *, force: bool) -> str:
    """`new` | `unchanged` | `changed` | `rewritten`. Only `--force` overwrites a difference."""
    path = Path(path)
    previous = read_record(path) if path.exists() else None
    if previous is None:
        _write(path, record)
        return "new"
    if comparable(previous) == comparable(record):
        # Identical but for latency: leave the file alone, so the on-disk `generated_at`
        # keeps saying when the numbers were actually established.
        return "unchanged"
    if not force:
        return "changed"
    _write(path, record)
    return "rewritten"


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------------- the sweep


def run_all(
    ctx: Any,
    rows: list[dict[str, Any]],
    *,
    strategies: tuple[str, ...] = STRATEGIES,
    out_dir: Path | str,
    ask: Callable[..., Any],
    select_example: Callable[..., Any],
    k: int = 10,
    s5_scope: str = "thematic",
    force: bool = False,
    sha: str = "",
    log: bool = True,
    echo: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Every question × every strategy, written to `<out_dir>/fixed/<qid>.<strategy>.json`."""
    out_dir = Path(out_dir)
    records: list[dict[str, Any]] = []
    outcomes: dict[str, int] = {}
    changed: list[str] = []
    started = datetime.now(UTC)

    for row in rows:
        qid = str(row.get("id") or "")
        if not qid:
            raise RunError("a question row has no `id`; the run file could not be named")
        for strategy in strategies:
            record = run_one(
                ctx,
                row,
                strategy,
                ask=ask,
                select_example=select_example,
                k=k,
                s5_scope=s5_scope,
                sha=sha,
                log=log,
            )
            path = run_path(out_dir, qid, strategy)
            outcome = save(path, record, force=force)
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if outcome == "changed":
                changed.append(f"{qid}.{strategy}")
                # The kept file is the evidence; the metrics must describe what is on disk.
                stored = read_record(path)
                if stored is not None:
                    record = stored
            records.append(record)
            if echo:
                mark = record["status"] if record["status"] != "ok" else outcome
                echo(f"  {qid:<7} {strategy:<4} {mark}")

    return {
        "records": records,
        "expected": len(rows) * len(strategies),
        "outcomes": outcomes,
        "changed": changed,
        "questions": len(rows),
        "strategies": list(strategies),
        "duration_ms": int((datetime.now(UTC) - started).total_seconds() * 1000),
        "started_at": started.isoformat(timespec="seconds"),
    }


def coverage(
    rows: list[dict[str, Any]], strategies: tuple[str, ...], out_dir: Path | str
) -> dict[str, Any]:
    """Which (question, strategy) pairs have a file on disk — the acceptance criterion."""
    missing: list[str] = []
    present = 0
    for row in rows:
        qid = str(row.get("id") or "")
        for strategy in strategies:
            if run_path(out_dir, qid, strategy).is_file():
                present += 1
            else:
                missing.append(f"{qid}.{strategy}")
    return {
        "expected": len(rows) * len(strategies),
        "present": present,
        "missing": missing,
        "complete": not missing,
    }


def load_records(
    rows: list[dict[str, Any]], strategies: tuple[str, ...], out_dir: Path | str
) -> list[dict[str, Any]]:
    """Every run file for this question set, in question order — the report's only input."""
    out: list[dict[str, Any]] = []
    for row in rows:
        for strategy in strategies:
            record = read_record(run_path(out_dir, str(row.get("id") or ""), strategy))
            if record is not None:
                out.append(record)
    return out


# ----------------------------------------------------------------------------- the report


def build_report(
    records: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    strategies: tuple[str, ...],
    questions_path: Path | str,
    questions_complete: bool,
    questions_total: int,
    truth: dict[str, Any] | None,
    summary: dict[str, Any],
    out_dir: Path | str | None = None,
    sha: str = "",
) -> dict[str, Any]:
    """Layer 2 and the cost table, entirely computed — no number here is typed by hand."""
    scored = metrics.score_records(records, truth or {})
    pending = sum(1 for row in rows if metrics.is_pending(row))
    types = sorted({str(r.get("type") or "") for r in records})
    unresolved = unresolved_gold(rows, truth)

    return {
        "step": "eval.run.fixed",
        "generated_at": _now(),
        "sha": sha,
        "mode": MODE,
        "baseline": BASELINE,
        "strategies": list(strategies),
        "types": types,
        "k": (records[0].get("k") if records else None),
        "budget_tokens": (records[0].get("budget_tokens") if records else None),
        "include_synthetic": (records[0].get("include_synthetic") if records else None),
        "questions_file": str(questions_path),
        "questions_total": questions_total,
        "questions_complete": bool(questions_complete),
        "gold_pending": pending,
        "gold_unresolved": unresolved,
        "match_kinds": list(metrics.MATCH_KINDS),
        "strict_match_kinds": sorted(metrics.STRICT_KINDS),
        "by_strategy": metrics.by_strategy(scored),
        "by_type": metrics.by_type(scored),
        "matrix": metrics.matrix(scored),
        "questions": metrics.question_rows(scored),
        "cross_lingual": metrics.cross_lingual(scored),
        "cost": cost_mod.table(scored),
        "coverage": coverage(rows, strategies, out_dir if out_dir is not None else Path(".")),
        "run": {
            "started_at": summary.get("started_at"),
            "duration_ms": summary.get("duration_ms"),
            "outcomes": summary.get("outcomes"),
            "changed": summary.get("changed"),
            "expected": summary.get("expected"),
        },
        "notes": _notes(pending, questions_complete, unresolved),
    }


def unresolved_gold(
    rows: list[dict[str, Any]], truth: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Gold references that name nothing a returned item could ever match.

    A `truth:<section>:<index>` whose record the truth file does not hold scores 0 recall for
    every strategy, forever, and looks exactly like retrieval failing. It is the question set
    that is broken there, so it is reported as such rather than charged to the strategies.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        bad = [
            ref.ref
            for ref in metrics.gold_refs(row.get("gold_evidence"), truth or {})
            if ref.unresolved
        ]
        if bad:
            out.append({"qid": row.get("id"), "refs": bad})
    return out


def _notes(
    pending: int, complete: bool, unresolved: list[dict[str, Any]] | None = None
) -> list[str]:
    notes: list[str] = []
    if pending:
        notes.append(
            f"{pending} question(s) still carry `gold_source: pending`; they ran and their "
            "latency, tokens and Cypher counts are real, but they are excluded from every "
            "recall and precision average and counted as `pending`."
        )
    if not complete:
        notes.append(
            "The question set is not complete yet (`eval_questions.json` -> merge.complete "
            "is false). Re-run `brain eval run` after `brain eval questions merge` reaches 32."
        )
    if unresolved:
        named = ", ".join(f"{u['qid']}: {', '.join(u['refs'])}" for u in unresolved[:10])
        notes.append(
            f"{len(unresolved)} question(s) cite gold that resolves to nothing — {named}. "
            "Their recall is a fact about the question set, not about retrieval."
        )
    notes.append(
        "`recall` counts a chunk of the gold node as a hit (match kind `parent`); "
        "`recall_strict` does not. Both are reported so the vector baseline is not scored "
        "against an id scheme only the graph strategies emit."
    )
    return notes


def write_report(report: dict[str, Any], path: Path | str) -> Path:
    """Merge into `data/reports/eval_retrieval.json`, stamped, keeping other sections.

    `runs[]` is appended rather than replaced: the closing review of Plan 1 made that a
    convention — a second run must not delete the record of the first one that worked.
    """
    from brain.retrieve.report import merge_sections

    path = Path(path)
    previous = read_record(path) or {}
    history = list(previous.get("runs") or [])
    history.append(
        {
            "generated_at": report["generated_at"],
            "sha": report.get("sha"),
            "strategies": report["strategies"],
            "questions_total": report["questions_total"],
            "questions_complete": report["questions_complete"],
            **(report.get("run") or {}),
        }
    )
    sections = {key: value for key, value in report.items() if key != "run"}
    sections["runs"] = history[-50:]
    return merge_sections(sections, path, sha=report.get("sha") or None)


# ----------------------------------------------------------------------------- printing


def matrix_lines(
    matrix: dict[str, dict[str, dict[str, Any]]], types: tuple[str, ...] | list[str]
) -> list[str]:
    """The compact strategy × type matrix `brain eval run` prints when it is done."""
    types = list(types)
    width = max([len(t) for t in types] + [6]) + 2
    head = f"{'strategy':<9}" + "".join(f"{t:>{width}}" for t in types)
    out = [head, "-" * len(head)]
    for strategy in sorted(matrix):
        cells = matrix[strategy]
        row = f"{strategy:<9}"
        for qtype in types:
            row += f"{cell_text(cells.get(qtype)):>{width}}"
        out.append(row)
    out.append("")
    out.append("recall of gold_evidence · `pend` = gold still pending · `n/a` = did not apply")
    out.append("`*` = at least one of the cell's runs was n/a · `-` = nothing ran")
    return out


def cell_text(cell: dict[str, Any] | None) -> str:
    if not cell:
        return "-"
    if not cell.get("ok"):
        return "n/a"
    mark = "*" if cell.get("na") else ""
    if cell.get("recall") is None:
        return ("pend" if cell.get("pending") else "-") + mark
    return f"{cell['recall']:.2f}{mark}"


def summary_lines(report: dict[str, Any]) -> list[str]:
    """What the command prints: the matrix, the cost table, and what did not apply."""
    out: list[str] = []
    out += matrix_lines(report["matrix"], report["types"])
    out.append("")
    out += cost_mod.lines(report["cost"])
    out.append("")
    na = {f"{name}": values["na"] for name, values in report["by_strategy"].items() if values["na"]}
    if na:
        out.append("n/a: " + ", ".join(f"{k} ×{v}" for k, v in sorted(na.items())))
        for name, values in sorted(report["by_strategy"].items()):
            for reason, count in sorted(values.get("na_reasons", {}).items()):
                out.append(f"  {name}: {count}× {reason[:110]}")
    coverage_block = report["coverage"]
    out.append(
        f"coverage: {coverage_block['present']}/{coverage_block['expected']} run files"
        + (
            f" · missing {', '.join(coverage_block['missing'][:6])}"
            if coverage_block["missing"]
            else ""
        )
    )
    run = report.get("run") or {}
    outcomes = run.get("outcomes") or {}
    out.append(
        "rerun: "
        + (", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())) or "nothing ran")
        + (f" · CHANGED: {', '.join(run.get('changed') or [])}" if run.get("changed") else "")
    )
    if report["gold_pending"]:
        out.append(
            f"gold pending: {report['gold_pending']}/{report['questions_total']} questions — "
            "recall columns stay `pend` until the merge fills them."
        )
    return out


def exit_code(report: dict[str, Any]) -> int:
    """1 when a pair has no file at all, or a rerun changed something `--force` did not ask for."""
    if not report["coverage"]["complete"]:
        return 1
    return 1 if (report.get("run") or {}).get("changed") else 0
