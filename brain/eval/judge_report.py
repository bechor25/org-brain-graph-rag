"""`brain eval judge merge|sample` — de-blinding the scores and turning them into layer 3.

Everything the judge produced is anonymous. This is where the labels are resolved against
`data/eval/blind_map.json` and become numbers per strategy × question type — and where four
things are checked that a judge cannot check about itself:

* **Nulls are paired with their reason.** `faithfulness` may be null only for a case that
  declared `context_available: false`, and *must* be null there. A judge that scored a
  context it was never given has not scored the answer in front of it.
* **Every justification carries a quotation.** The rubric requires it; merge counts the ones
  that do not, because "2, it looks right" is an opinion in the shape of a measurement.
* **Citation validity is computed as well as judged.** The code check is binary — every
  bracket names something the retrieval actually returned, and something the graph holds —
  and the report prints where it and the judge disagree instead of picking a winner.
* **The two judges are compared on the overlap.** 20% of the cases were scored twice, and
  exact and within-1 agreement per metric is what says whether the averages mean anything.

A case that was scored twice contributes *one* score to every average (the mean of its two
judgments), so a shard's overlap cannot double-weight the questions that happened to be
sampled into it.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.common.jsonschema_mini import validate as schema_validate
from brain.eval import casebatch
from brain.eval.answers_batches import (
    ANSWERS_DIRNAME,
    MODE,
    cited_key_matches,
)
from brain.eval.blind import BlindMap
from brain.eval.judge_batches import (
    BASELINE,
    BATCH_DIR,
    ERRORS_FIELD,
    RETRY_FIELD,
    JudgeError,
    load_schema,
)
from brain.harvest.base import utc_now_iso

#: The four metrics of spec §5.2 layer 3, in the order every table prints them.
METRICS: tuple[str, ...] = ("faithfulness", "correctness", "citation_validity", "relevancy")
#: A justification without one of these is a score with no evidence behind it.
QUOTE_RE = re.compile(r'"[^"\n]{3,}"|“[^”\n]{3,}”|«[^»\n]{3,}»')
SAMPLE_DOC = "eval-judge-sample.md"
DEFAULT_SAMPLE = 0.10


@dataclass
class Judgment:
    """One judge's verdict on one case, already de-blinded."""

    label: str
    shard: str
    batch: str
    qid: str
    strategy: str
    lang: str = ""
    qtype: str = ""
    scores: dict[str, int | None] = field(default_factory=dict)
    unsupported_claims: list[str] = field(default_factory=list)
    justification: str = ""
    answer_language_ok: bool | None = None
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "shard": self.shard,
            "batch": self.batch,
            "qid": self.qid,
            "strategy": self.strategy,
            "lang": self.lang,
            "type": self.qtype,
            **{m: self.scores.get(m) for m in METRICS},
            "unsupported_claims": self.unsupported_claims,
            "justification": self.justification,
            "answer_language_ok": self.answer_language_ok,
            "problems": self.problems,
        }


@dataclass
class PairVerdict:
    """One pairwise verdict, with the side labels resolved back to strategy names."""

    label: str
    shard: str
    batch: str
    qid: str
    baseline: str
    challenger: str
    winner: str
    winner_strategy: str
    both_wrong: bool = False
    justification: str = ""
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "shard": self.shard,
            "batch": self.batch,
            "qid": self.qid,
            "baseline": self.baseline,
            "challenger": self.challenger,
            "winner_side": self.winner,
            "winner": self.winner_strategy,
            "both_wrong": self.both_wrong,
            "justification": self.justification,
            "problems": self.problems,
        }


# ----------------------------------------------------------------------------- reading


def cases_in(source: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """`case_id`/`pair_id` -> the case as the judge saw it, from the `.in.json`."""
    out: dict[str, dict[str, Any]] = {}
    for case in source.get("cases") or []:
        if isinstance(case, Mapping) and case.get("case_id"):
            out[str(case["case_id"])] = dict(case)
    for case in source.get("pairwise") or []:
        if isinstance(case, Mapping) and case.get("pair_id"):
            out[str(case["pair_id"])] = dict(case)
    return out


def has_quote(text: str) -> bool:
    return bool(QUOTE_RE.search(text or ""))


def check_score(
    score: Mapping[str, Any], case: Mapping[str, Any]
) -> tuple[dict[str, int | None], list[str]]:
    """The four metrics, plus every rule about them a schema cannot state."""
    problems: list[str] = []
    available = bool(case.get("context_available"))
    values: dict[str, int | None] = {}
    for metric in METRICS:
        value = score.get(metric)
        values[metric] = None if value is None else int(value)
        if value is None and (metric != "faithfulness" or available):
            problems.append(
                f"{metric} is null, but this case does not declare it inapplicable "
                "(only faithfulness on a case without a context may be null)"
            )
        if metric == "faithfulness" and not available and value is not None:
            problems.append(
                "faithfulness was scored on a case that carried no context; the judge "
                "cannot have seen what it measured"
            )
    if not has_quote(str(score.get("justification") or "")):
        problems.append("the justification carries no verbatim quotation")
    faith = values.get("faithfulness")
    if faith is not None and faith < 2 and not (score.get("unsupported_claims") or []):
        problems.append("faithfulness below 2 with no unsupported claim quoted")
    return values, problems


def read_judgments(
    root: Path,
    blind: BlindMap,
    *,
    schema: Mapping[str, Any] | None = None,
    questions: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Every `.out.json` under the judge batch tree, validated and de-blinded."""
    schema = schema or load_schema()
    questions = questions or {}
    batches = casebatch.read_outputs(Path(root), array_field="scores")
    judgments: list[Judgment] = []
    pairs: list[PairVerdict] = []
    rejected: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    judged: set[tuple[str, str]] = set()

    for batch in batches:
        # `pairs` may be absent — a judge whose batch carried no pairwise case has nothing
        # to put there, and quarantining an otherwise good batch over a missing empty array
        # would throw away real work. Present-but-not-an-array is still an envelope error,
        # and a pairwise case nobody answered is reported as unjudged either way.
        if batch.ok and "pairs" in batch.output and not isinstance(batch.output["pairs"], list):
            batch.errors.append("`pairs` is present but is not an array")
        if not batch.ok:
            failed.append(
                casebatch.handle_failure(batch, retry_field=RETRY_FIELD, errors_field=ERRORS_FIELD)
            )
            continue
        errors = schema_validate(dict(batch.output), schema)
        envelope_errors, per_score = casebatch.errors_by_index(errors, "scores")
        envelope_errors, per_pair = casebatch.errors_by_index(envelope_errors, "pairs")
        if envelope_errors:
            batch.errors.extend(envelope_errors[:20])
            failed.append(
                casebatch.handle_failure(batch, retry_field=RETRY_FIELD, errors_field=ERRORS_FIELD)
            )
            continue
        cases = cases_in(batch.source)

        for index, score in enumerate(batch.output.get("scores") or []):
            label = str(score.get("case_id") or "") if isinstance(score, Mapping) else ""
            problem = _reject_reason(
                label, per_score.get(index), cases, blind.cases, batch, judged, "case"
            )
            if problem:
                rejected.append(problem)
                continue
            case = cases[label]
            entry = blind.cases[label]
            values, problems = check_score(score, case)
            row = questions.get(str(entry.get("qid")), {})
            judgments.append(
                Judgment(
                    label=label,
                    shard=batch.shard,
                    batch=batch.batch_id,
                    qid=str(entry.get("qid") or ""),
                    strategy=str(entry.get("strategy") or ""),
                    lang=str(row.get("lang") or case.get("lang") or ""),
                    qtype=str(row.get("type") or case.get("question_type") or ""),
                    scores=values,
                    unsupported_claims=[str(c) for c in (score.get("unsupported_claims") or [])],
                    justification=str(score.get("justification") or ""),
                    answer_language_ok=score.get("answer_language_ok"),
                    problems=problems,
                )
            )

        for index, verdict in enumerate(batch.output.get("pairs") or []):
            label = str(verdict.get("pair_id") or "") if isinstance(verdict, Mapping) else ""
            problem = _reject_reason(
                label, per_pair.get(index), cases, blind.pairs, batch, judged, "pair"
            )
            if problem:
                rejected.append(problem)
                continue
            entry = blind.pairs[label]
            side = str(verdict.get("winner") or "")
            winner = "tie"
            if side in ("a", "b"):
                winner = str((entry.get(side) or {}).get("strategy") or "")
            problems: list[str] = []
            if not has_quote(str(verdict.get("justification") or "")):
                problems.append("the justification carries no verbatim quotation")
            pairs.append(
                PairVerdict(
                    label=label,
                    shard=batch.shard,
                    batch=batch.batch_id,
                    qid=str(entry.get("qid") or ""),
                    baseline=str(
                        (entry.get(str(entry.get("baseline_side") or "a")) or {}).get("strategy")
                        or BASELINE
                    ),
                    challenger=str(entry.get("challenger") or ""),
                    winner=side,
                    winner_strategy=winner,
                    both_wrong=bool(verdict.get("both_wrong")),
                    justification=str(verdict.get("justification") or ""),
                    problems=problems,
                )
            )
        casebatch.clear_failure_files(batch)

    return {
        "judgments": judgments,
        "pairs": pairs,
        "rejected": rejected,
        "failed": failed,
        "batches": batches,
    }


def _reject_reason(
    label: str,
    errors: list[str] | None,
    cases: Mapping[str, Any],
    known: Mapping[str, Any],
    batch: casebatch.Batch,
    judged: set[tuple[str, str]],
    kind: str,
) -> dict[str, Any] | None:
    """Why this record cannot be counted, or `None` when it can."""
    if errors:
        return {"batch": batch.batch_id, "label": label, "why": errors[:5]}
    if label not in cases:
        return {
            "batch": batch.batch_id,
            "label": label,
            "why": [f"{label!r} is not a {kind} of this batch"],
        }
    if label not in known:
        return {
            "batch": batch.batch_id,
            "label": label,
            "why": [f"{label!r} is not in the blind map; it cannot be attributed"],
        }
    if (batch.shard, label) in judged:
        return {
            "batch": batch.batch_id,
            "label": label,
            "why": ["this shard already judged it"],
        }
    judged.add((batch.shard, label))
    return None


# ----------------------------------------------------------------------------- the numbers


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def case_scores(judgments: Sequence[Judgment]) -> dict[str, dict[str, Any]]:
    """One row per case: the mean of its judgments, so an overlapped case counts once."""
    grouped: dict[str, list[Judgment]] = {}
    for judgment in judgments:
        grouped.setdefault(judgment.label, []).append(judgment)
    out: dict[str, dict[str, Any]] = {}
    for label, group in grouped.items():
        head = group[0]
        row: dict[str, Any] = {
            "label": label,
            "qid": head.qid,
            "strategy": head.strategy,
            "lang": head.lang,
            "type": head.qtype,
            "judgments": len(group),
        }
        for metric in METRICS:
            values = [j.scores[metric] for j in group if j.scores.get(metric) is not None]
            row[metric] = _mean([float(v) for v in values])  # type: ignore[arg-type]
        out[label] = row
    return out


def summarise(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The four means plus how many cases each rests on. `None` when nothing was scored."""
    out: dict[str, Any] = {"cases": len(rows)}
    for metric in METRICS:
        values = [float(r[metric]) for r in rows if r.get(metric) is not None]
        out[metric] = _mean(values)
        out[f"{metric}_n"] = len(values)
    return out


def group_by(rows: Sequence[Mapping[str, Any]], *keys: str) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        name = "|".join(str(row.get(k) or "") for k in keys)
        grouped.setdefault(name, []).append(row)
    return grouped


def matrix(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """strategy × question type, each cell the four means — the layer-3 half of the matrix."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for name, group in group_by(rows, "strategy", "type").items():
        strategy, _, qtype = name.partition("|")
        out.setdefault(strategy, {})[qtype] = summarise(group)
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def agreement(judgments: Sequence[Judgment]) -> dict[str, Any]:
    """Exact and within-1 agreement between the two judges, on the cases both scored."""
    grouped: dict[str, list[Judgment]] = {}
    for judgment in judgments:
        grouped.setdefault(judgment.label, []).append(judgment)
    overlap = {
        label: group
        for label, group in grouped.items()
        if len({j.shard for j in group}) > 1 and len(group) > 1
    }
    per_metric: dict[str, dict[str, Any]] = {}
    for metric in METRICS:
        exact = within = total = 0
        diffs: list[int] = []
        for group in overlap.values():
            values = [j.scores.get(metric) for j in group[:2]]
            if any(v is None for v in values):
                continue
            total += 1
            delta = abs(int(values[0]) - int(values[1]))  # type: ignore[arg-type]
            diffs.append(delta)
            exact += 1 if delta == 0 else 0
            within += 1 if delta <= 1 else 0
        per_metric[metric] = {
            "compared": total,
            "exact": exact,
            "within_1": within,
            "exact_pct": round(100 * exact / total, 2) if total else None,
            "within_1_pct": round(100 * within / total, 2) if total else None,
            "mean_abs_diff": _mean([float(d) for d in diffs]),
        }
    return {
        "overlap_cases": len(overlap),
        "overlap_labels": sorted(overlap),
        "by_metric": per_metric,
        "note": (
            "Only cases scored by two different shards are compared. A case both judges "
            "scored counts once in every average above; the two scores are averaged."
        ),
    }


def pairwise_table(pairs: Sequence[PairVerdict], *, baseline: str = BASELINE) -> dict[str, Any]:
    """Win / loss / tie against the baseline, per challenger strategy."""
    rows: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        row = rows.setdefault(
            pair.challenger,
            {"cases": 0, "wins": 0, "losses": 0, "ties": 0, "both_wrong": 0, "qids": []},
        )
        row["cases"] += 1
        row["qids"].append(pair.qid)
        if pair.winner_strategy == pair.challenger:
            row["wins"] += 1
        elif pair.winner_strategy == baseline:
            row["losses"] += 1
        else:
            row["ties"] += 1
        row["both_wrong"] += 1 if pair.both_wrong else 0
    for row in rows.values():
        row["qids"] = sorted(set(row["qids"]))
        row["win_rate"] = round(row["wins"] / row["cases"], 3) if row["cases"] else None
        row["win_rate_excluding_ties"] = (
            round(row["wins"] / (row["wins"] + row["losses"]), 3)
            if (row["wins"] + row["losses"])
            else None
        )
    return {
        "baseline": baseline,
        "by_challenger": dict(sorted(rows.items())),
        "note": (
            "`win_rate` counts a tie as a non-win. `both_wrong` ties are counted in `ties` "
            "and reported separately: two wrong answers agreeing is not a draw on quality."
        ),
    }


# ----------------------------------------------------------------- the deterministic check


def code_citation_check(
    answers: Sequence[Mapping[str, Any]],
    *,
    verify: Callable[[Iterable[Any]], Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per case: does every bracket name something retrieved, and something the graph holds?

    Binary on purpose. Code can say whether a cited key exists and whether it was in the
    context; it cannot say whether the key supports the sentence it follows, which is half
    of the judge's 0–2. Pretending otherwise would produce a number that looks like the
    judge's and is not comparable with it.
    """
    from brain.eval.citations import find_citations, unique

    out: dict[str, dict[str, Any]] = {}
    all_citations: list[Any] = []
    per_case: dict[str, list[Any]] = {}
    for answer in answers:
        case_id = str(answer.get("case_id") or "")
        citations = unique(find_citations(str(answer.get("answer") or "")))
        per_case[case_id] = citations
        all_citations.extend(citations)
    verdicts = dict(verify(all_citations)) if verify and all_citations else {}

    for answer in answers:
        case_id = str(answer.get("case_id") or "")
        citations = per_case.get(case_id, [])
        keys = {str(k) for k in (answer.get("context_keys") or [])}
        available = bool(answer.get("context_available"))
        not_in_context = [
            c.text
            for c in citations
            if available and cited_key_matches(c.value or c.text, keys) is None
        ]
        not_in_graph = [
            c.text for c in citations if verdicts and not getattr(verdicts.get(c.id), "ok", True)
        ]
        refused = bool(answer.get("refused"))
        if not citations:
            valid: bool | None = True if refused else False
            reason = "a refusal cites nothing" if refused else "the answer carries no citation"
        elif not_in_context or not_in_graph:
            valid, reason = (
                False,
                "; ".join(
                    filter(
                        None,
                        [
                            f"not in the context: {', '.join(not_in_context[:5])}"
                            if not_in_context
                            else "",
                            f"not in the graph: {', '.join(not_in_graph[:5])}"
                            if not_in_graph
                            else "",
                        ],
                    )
                ),
            )
        else:
            valid, reason = True, "every citation is in the context and resolves in the graph"
        out[case_id] = {
            "citations": len(citations),
            "not_in_context": not_in_context,
            "not_in_graph": not_in_graph,
            "context_checked": available,
            "graph_checked": bool(verdicts),
            "code_valid": valid,
            "reason": reason,
        }
    return out


def crosscheck(
    rows: Sequence[Mapping[str, Any]],
    answers: Sequence[Mapping[str, Any]],
    code: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Where the judge's citation score and the code check point opposite ways."""
    by_case = {str(a.get("case_id")): a for a in answers}
    label_to_case = {
        str(row.get("label")): f"{row.get('qid')}.{row.get('strategy')}" for row in rows
    }
    disagreements: list[dict[str, Any]] = []
    agreed = compared = 0
    for row in rows:
        case_id = label_to_case.get(str(row.get("label")), "")
        verdict = code.get(case_id)
        judged = row.get("citation_validity")
        if verdict is None or judged is None or case_id not in by_case:
            continue
        compared += 1
        code_valid = verdict["code_valid"]
        if code_valid and judged <= 0.5:
            disagreements.append(
                {
                    "case_id": case_id,
                    "judge": judged,
                    "code": "valid",
                    "why": verdict["reason"],
                }
            )
        elif not code_valid and judged >= 1.5:
            disagreements.append(
                {"case_id": case_id, "judge": judged, "code": "invalid", "why": verdict["reason"]}
            )
        else:
            agreed += 1
    return {
        "compared": compared,
        "agreed": agreed,
        "disagreements": disagreements,
        "disagreement_pct": (round(100 * len(disagreements) / compared, 2) if compared else None),
        "note": (
            "The code check is binary — every bracket names something the retrieval returned "
            "and the graph holds. A disagreement is flagged only at the extremes (the code "
            "says valid and the judge scored 0, or the reverse); a judge's 1 is compatible "
            "with either, because only the judge can see whether a citation supports its "
            "sentence."
        ),
    }


# ----------------------------------------------------------------------------- the merge


def run_merge(
    *,
    batches_dir: Path,
    eval_dir: Path,
    reports_dir: Path,
    rows: Sequence[Mapping[str, Any]],
    verify: Callable[[Iterable[Any]], Mapping[str, Any]] | None = None,
    sha: str = "",
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Validate, de-blind, measure, and write `data/reports/eval_answers.json`."""
    from brain.eval.answers_batches import read_merged
    from brain.eval.judge_batches import read_blind_map

    started = time.perf_counter()
    root = Path(batches_dir) / BATCH_DIR
    blind = read_blind_map(Path(eval_dir))
    questions = {str(r.get("id")): dict(r) for r in rows if r.get("id")}
    answers = read_merged(Path(eval_dir))
    if not answers:
        raise JudgeError(
            f"no merged answers under {Path(eval_dir) / ANSWERS_DIRNAME / MODE} — "
            "`brain eval answers merge` has not run."
        )

    read = read_judgments(root, blind, questions=questions)
    judgments: list[Judgment] = read["judgments"]
    pairs: list[PairVerdict] = read["pairs"]
    rows_by_case = case_scores(judgments)
    scored = list(rows_by_case.values())
    code = code_citation_check(answers, verify=verify)

    planned = set(casebatch.batch_ids(root))
    with_output = {b.batch_id for b in read["batches"]}
    expected = set(blind.cases) | set(blind.pairs)
    judged_labels = {j.label for j in judgments} | {p.label for p in pairs}

    report = {
        "step": "eval.judge.merge",
        "generated_at": utc_now_iso(),
        "sha": sha,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "metrics": list(METRICS),
        "batches": {
            "planned": len(planned),
            "with_output": len(with_output),
            "missing": sorted(planned - with_output),
            "failed": [dict(f) for f in read["failed"]],
        },
        "judgments": {
            "total": len(judgments),
            "cases": len(rows_by_case),
            "pairs": len(pairs),
            "rejected": len(read["rejected"]),
            "unjudged_labels": sorted(expected - judged_labels),
            "with_problems": [j.as_dict() for j in judgments if j.problems][:50],
            "problem_count": sum(1 for j in judgments if j.problems),
        },
        "rejected": [dict(r) for r in read["rejected"]],
        "cases": sorted(scored, key=lambda r: (str(r["strategy"]), str(r["qid"]))),
        "all_judgments": [j.as_dict() for j in judgments],
        "all_pairs": [p.as_dict() for p in pairs],
    }
    sections = {
        "step": "eval.answers",
        "judge_merge": report,
        "metrics": {
            "by_strategy": {
                name: summarise(group)
                for name, group in sorted(group_by(scored, "strategy").items())
            },
            "by_type": {
                name: summarise(group) for name, group in sorted(group_by(scored, "type").items())
            },
            "by_lang": {
                name: summarise(group) for name, group in sorted(group_by(scored, "lang").items())
            },
            "matrix": matrix(scored),
            "note": (
                "Layer 3 of spec §5.2. A case judged twice contributes the mean of its two "
                "judgments, once. `*_n` is how many cases carried that metric — faithfulness "
                "is null on agentic cases, which recorded no context."
            ),
        },
        "pairwise": pairwise_table(pairs),
        "agreement": agreement(judgments),
        "citation_crosscheck": crosscheck(scored, answers, code),
        "citation_code_check": dict(sorted(code.items())),
    }
    write_section_all(Path(reports_dir), sections, sha=sha)
    echo(summarize_merge(report, sections))
    return {**report, **sections}


def write_section_all(reports_dir: Path, sections: Mapping[str, Any], *, sha: str) -> Path:
    from brain.eval.answers_batches import REPORT_NAME
    from brain.retrieve.report import merge_sections

    Path(reports_dir).mkdir(parents=True, exist_ok=True)
    return merge_sections(dict(sections), Path(reports_dir) / REPORT_NAME, sha=sha or None)


def summarize_merge(report: Mapping[str, Any], sections: Mapping[str, Any]) -> str:
    judgments = report["judgments"]
    lines = [
        f"judge merge: {judgments['total']} judgment(s) over {judgments['cases']} case(s), "
        f"{judgments['pairs']} pairwise, {judgments['rejected']} rejected",
    ]
    head = f"{'strategy':<10}" + "".join(f"{m[:5]:>8}" for m in METRICS) + f"{'cases':>7}"
    lines += [head, "-" * len(head)]
    for name, row in sections["metrics"]["by_strategy"].items():
        cells = "".join(
            f"{'-' if row.get(m) is None else format(row[m], '.2f'):>8}" for m in METRICS
        )
        lines.append(f"{name:<10}{cells}{row['cases']:>7}")
    pairwise = sections["pairwise"]["by_challenger"]
    if pairwise:
        lines.append("")
        lines.append(f"pairwise vs {sections['pairwise']['baseline']}:")
        for name, row in pairwise.items():
            lines.append(
                f"  {name:<6} {row['wins']}W/{row['losses']}L/{row['ties']}T "
                f"· win rate {row['win_rate']}"
            )
    agree = sections["agreement"]["by_metric"]
    lines.append("")
    lines.append(f"inter-judge overlap: {sections['agreement']['overlap_cases']} case(s)")
    for metric, row in agree.items():
        lines.append(
            f"  {metric:<18} exact {row['exact_pct']}% · within-1 {row['within_1_pct']}% "
            f"(n={row['compared']})"
        )
    cross = sections["citation_crosscheck"]
    lines.append("")
    lines.append(
        f"citation cross-check: {cross['agreed']}/{cross['compared']} agree with the code; "
        f"{len(cross['disagreements'])} disagreement(s)"
    )
    if judgments["unjudged_labels"]:
        lines.append(
            f"UNJUDGED: {len(judgments['unjudged_labels'])} case(s) have no judgment at all"
        )
    if judgments["problem_count"]:
        lines.append(
            f"{judgments['problem_count']} judgment(s) broke a rubric rule "
            "(null metric, missing quotation, unevidenced faithfulness)"
        )
    return "\n".join(lines)


def exit_code(report: Mapping[str, Any]) -> int:
    judgments = report.get("judgments") or {}
    batches = report.get("batches") or {}
    if judgments.get("unjudged_labels") or batches.get("missing") or batches.get("failed"):
        return 1
    return 1 if judgments.get("rejected") else 0


# ----------------------------------------------------------------------------- the sample


def pick_sample(
    rows: Sequence[Mapping[str, Any]], *, fraction: float = DEFAULT_SAMPLE, seed: int = 2026
) -> list[Mapping[str, Any]]:
    """A stratified 10%: every strategy that was judged appears before any is repeated."""
    import random

    rng = random.Random(seed)
    wanted = max(1, round(len(rows) * fraction)) if rows else 0
    by_strategy: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_strategy.setdefault(str(row.get("strategy") or ""), []).append(row)
    for group in by_strategy.values():
        rng.shuffle(group)
    picked: list[Mapping[str, Any]] = []
    while len(picked) < wanted and any(by_strategy.values()):
        for name in sorted(by_strategy):
            if len(picked) >= wanted:
                break
            if by_strategy[name]:
                picked.append(by_strategy[name].pop())
    return sorted(picked, key=lambda r: (str(r.get("strategy")), str(r.get("qid"))))


def _keys(values: Any) -> str:
    return ", ".join(f"`{k}`" for k in (values or [])) or "—"


def _invalid(answer: Mapping[str, Any]) -> str:
    """The citations the answers merge already found to be outside the context."""
    bad = [str(entry.get("cited")) for entry in (answer.get("cited_keys_invalid") or [])]
    return f" — **invalid:** {_keys(bad)}" if bad else ""


def render_sample(
    picked: Sequence[Mapping[str, Any]],
    *,
    answers: Mapping[str, Mapping[str, Any]],
    judgments: Sequence[Mapping[str, Any]],
    questions: Mapping[str, Mapping[str, Any]],
    fraction: float,
    total: int,
    generated_at: str,
    sha: str,
) -> str:
    """The planner's spot-check list, in the shape the user reads it: case, answer, verdict."""
    by_label: dict[str, list[Mapping[str, Any]]] = {}
    for judgment in judgments:
        by_label.setdefault(str(judgment.get("label")), []).append(judgment)

    out = [
        "# Judge spot-check sample",
        "",
        f"Generated by `brain eval judge sample` at `{sha[:12] or 'unknown'}` "
        f"on {generated_at}. Every number and quotation below is copied from "
        "`data/reports/eval_answers.json` and `data/eval/answers/` — nothing here is written "
        "by hand.",
        "",
        f"{len(picked)} of {total} judged cases ({fraction:.0%}), stratified so every "
        "strategy that was judged appears before any is repeated. The strategy names are "
        "de-blinded here and were **not** visible to the judge.",
        "",
        "What to look for: does the score match the answer you are reading, does the "
        "justification quote something that is really there, and would you have scored it "
        "the same way?",
        "",
    ]
    for index, row in enumerate(picked, start=1):
        case_id = f"{row.get('qid')}.{row.get('strategy')}"
        answer = answers.get(case_id, {})
        question = questions.get(str(row.get("qid")), {})
        asked = question.get("question") or answer.get("question") or "—"
        out += [
            f"## {index}. `{case_id}` — {row.get('type') or '?'} / {row.get('lang') or '?'}",
            "",
            f"**Question** ({row.get('lang')}): {asked}",
            "",
            f"**Gold answer:** {question.get('gold_answer') or '—'}",
            "",
            f"**Gold evidence:** {_keys(question.get('gold_evidence'))}",
            "",
            f"**Context:** {answer.get('context_items', 0)} item(s), "
            f"{answer.get('context_tokens') or '?'} tokens"
            + ("" if answer.get("context_available", True) else " — agentic run, none recorded"),
            "",
            "**Answer:**",
            "",
            "> " + (str(answer.get("answer") or "—").replace("\n", "\n> ")),
            "",
            f"**Cited:** {_keys(answer.get('cited_keys'))}{_invalid(answer)}",
            "",
            "| metric | score | judge(s) |",
            "|---|---|---|",
        ]
        for metric in METRICS:
            values = [str(j.get(metric)) for j in by_label.get(str(row.get("label")), [])]
            mean = row.get(metric)
            out.append(
                f"| {metric} | {'-' if mean is None else f'{mean:.2f}'} | "
                f"{', '.join(values) or '—'} |"
            )
        out.append("")
        for judgment in by_label.get(str(row.get("label")), []):
            out += [
                f"**Justification** (judge `{judgment.get('shard')}`, batch "
                f"`{judgment.get('batch')}`): {judgment.get('justification') or '—'}",
                "",
            ]
            claims = judgment.get("unsupported_claims") or []
            if claims:
                out.append("Unsupported claims the judge listed:")
                out += [f"- {c}" for c in claims]
                out.append("")
            if judgment.get("problems"):
                out.append("Rule problems recorded by merge: " + "; ".join(judgment["problems"]))
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def run_sample(
    *,
    reports_dir: Path,
    eval_dir: Path,
    docs_dir: Path,
    rows: Sequence[Mapping[str, Any]],
    fraction: float = DEFAULT_SAMPLE,
    seed: int = 2026,
    sha: str = "",
) -> tuple[Path, int]:
    """Write `docs/report/eval-judge-sample.md` from the merged report. Never hand-edited."""
    from brain.eval.answers_batches import REPORT_NAME, read_merged

    path = Path(reports_dir) / REPORT_NAME
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JudgeError(
            f"{path} cannot be read ({exc}); run `brain eval judge merge` first."
        ) from exc
    merged = (report or {}).get("judge_merge") or {}
    cases = merged.get("cases") or []
    if not cases:
        raise JudgeError(
            f"{path} holds no judged cases; run `brain eval judge merge` before sampling."
        )
    answers = {str(a.get("case_id")): a for a in read_merged(Path(eval_dir))}
    questions = {str(r.get("id")): dict(r) for r in rows if r.get("id")}
    picked = pick_sample(cases, fraction=fraction, seed=seed)
    text = render_sample(
        picked,
        answers=answers,
        judgments=merged.get("all_judgments") or [],
        questions=questions,
        fraction=fraction,
        total=len(cases),
        generated_at=str(merged.get("generated_at") or utc_now_iso()),
        sha=sha or str(merged.get("sha") or ""),
    )
    out = Path(docs_dir) / SAMPLE_DOC
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out, len(picked)


def parse_fraction(value: str) -> float:
    """`--n 10%`, `--n 10` and `--n 0.1` all mean one tenth.

    The rule has to be stated rather than guessed at, because `1` is genuinely ambiguous:
    **a bare number of 1 or more is a percentage, a bare number below 1 is a fraction.** So
    `--n 1` is one percent and `--n 100` is everything, which is the reading that keeps
    `--n 1` consistent with `--n 10`.
    """
    text = str(value).strip()
    percent = text.endswith("%")
    number = float(text[:-1]) / 100 if percent else float(text)
    if not percent and number >= 1:
        number = number / 100
    if not 0 < number <= 1:
        raise ValueError(
            f"{value!r} is not a share between 0 and 1 — write `10%`, `10` or `0.1` for a tenth"
        )
    return number
