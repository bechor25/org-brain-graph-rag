"""`brain eval report` — everything `docs/report/eval-report.md` says, read out of JSON.

Spec §5.6 and Plan 3 decision 8: the report is generated from the step reports, so the only
way to change a number in it is to rerun the step that measured the number. This module is
the *reading* half — it opens nine JSON files, states for each one when and at which commit
it was written, and shapes what it found into one dict. `report_render` turns that dict into
Hebrew. Nothing here formats and nothing there reads a file.

Three rules decide how a missing input behaves, and all three come from the closing review of
Plan 1:

1. **A section that was never measured says so, and says which command measures it.** An
   empty table is indistinguishable from a zero; "טרם נמדד + `brain eval judge merge`" is
   not. So every input carries its producing command next to its path.
2. **A number dragged forward carries its commit.** Each input reports the `sha` it was
   written at; when that is not HEAD the section is marked `stale` rather than quietly
   re-published. Reports that stamp their own sub-sections (`retrieve.json`,
   `eval_retrieval.json`, `eval_answers.json` all carry a `sections` index) have their stale
   sub-sections listed too — one file can be half fresh.
3. **A derived claim shows its arithmetic.** The "מתי מה" table picks a winner per question
   type, so it also prints the baseline's value and the cost delta that the win cost. A
   recommendation without the loser's number is an opinion.

Layer 3 and the incremental run are the two sections expected to be missing when this runs
for the first time: the judge batches are dispatched after the retrieval sweep, and the
incremental slice is a separate pipeline run. Both render as pending sections rather than
blocking the document — the point of generating the report early is that the last command of
Plan 3 is `brain eval report` and not a day of editing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

# Stdlib-only, so importing it costs `brain --help` nothing — which was the whole reason
# this module kept a private copy of the git call until the closing review of Plan 3.
from brain.common.stamp import head_sha

#: Where the document lands, and the file the whole plan's gate names.
DOCUMENT = Path("docs/report/eval-report.md")
#: Mode A's seven columns plus mode B's, in the order every table prints them. `s1r` is the
#: baseline (plan decision 3); `agentic` is mode B, which sits beside mode A rather than
#: inside it because it never recorded a context.
STRATEGY_ORDER: tuple[str, ...] = ("s1", "s1r", "s2", "s3", "s4", "s5", "s6", "agentic")
BASELINE = "s1r"
AGENTIC = "agentic"
#: The four judge metrics of spec §5.2 layer 3, in rubric order.
JUDGE_METRICS: tuple[str, ...] = ("faithfulness", "correctness", "citation_validity", "relevancy")
#: Where mode B's answers were merged to. A `<qid>.agentic.json` here is what turns the mode B
#: column on: the judge can only have scored what exists on disk.
ANSWERS_SUBDIR = Path("answers") / "fixed"
AGENTIC_GLOB = f"*.{AGENTIC}.json"


class ReportError(RuntimeError):
    """The document cannot be built: no report directory, or an unreadable input."""


# ------------------------------------------------------------------------------- the inputs


@dataclass(frozen=True)
class Source:
    """One JSON input: where it is, what it brings, and the command that writes it."""

    key: str
    filename: str
    command: str
    brings: str


#: Every file the document reads. The order is the order of the header table, which is also
#: the order of the pipeline: harvest → graph → retrieval → answers → judge → increment.
INPUTS: tuple[Source, ...] = (
    Source("harvest", "harvest.json", "brain harvest", "שכבה 0 — מה נמשך מהמקורות"),
    Source("index", "index.json", "brain index", "שכבה 1 — מפקד, provenance, קהילות"),
    Source("resolve", "resolve.json", "brain resolve eval", "שכבה 1 — P/R/F1 של איחוד ישויות"),
    Source(
        "retrieve",
        "retrieve.json",
        "brain cypher-examples check",
        "תשתית האחזור — reranker ו-guard",
    ),
    Source("eval_questions", "eval_questions.json", "brain eval questions merge", "סט השאלות"),
    Source("eval_retrieval", "eval_retrieval.json", "brain eval run --mode fixed", "שכבה 2"),
    Source(
        "eval_answers",
        "eval_answers.json",
        "brain eval answers merge · brain eval judge merge",
        "שכבה 3 — תשובות ושיפוט עיוור",
    ),
    Source("plan2_gate", "plan2_gate.json", "brain eval cite-check", "שער מצב B (ציטוטים)"),
    Source(
        "incremental",
        "incremental.json",
        "brain harvest --since <date> --source jira",
        "§5.5 — ריצת העדכון האינקרמנטלי (כל הפייפליין)",
    ),
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_json(path: Path) -> dict[str, Any] | None:
    """The file as a dict, or `None` when it is absent. A corrupt file is an error."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReportError(f"{path} does not hold a JSON object")
    return data


def load(reports_dir: Path) -> dict[str, dict[str, Any] | None]:
    """Every input, keyed by `Source.key`; `None` for the ones not written yet."""
    reports_dir = Path(reports_dir)
    return {s.key: read_json(reports_dir / s.filename) for s in INPUTS}


def _stale_subsections(data: Mapping[str, Any]) -> list[str]:
    """Which sub-sections of a self-stamping report are not at HEAD, per its own index."""
    index = data.get("sections")
    if not isinstance(index, Mapping):
        return []
    return sorted(
        name
        for name, stamp in index.items()
        if isinstance(stamp, Mapping) and stamp.get("stale") is True
    )


def same_commit(measured: str, sha: str) -> bool:
    """Whether two sha strings name the same commit, abbreviated or not.

    `brain harvest --since` stamps `incremental.json` with `git rev-parse --short` while
    `git rev-parse` gives forty characters. An equality test between the two marks a report
    written this second as stale, which is the opposite of what the staleness rule is for:
    it trains a reader to ignore the word.
    """
    if not measured or not sha:
        return False
    shortest = min(len(measured), len(sha))
    return shortest >= 7 and measured[:shortest].lower() == sha[:shortest].lower()


def input_row(source: Source, data: Mapping[str, Any] | None, *, sha: str) -> dict[str, Any]:
    """One row of the header table: presence, stamp, and whether the stamp is HEAD."""
    path = f"data/reports/{source.filename}"
    if data is None:
        return {
            "key": source.key,
            "path": path,
            "command": source.command,
            "brings": source.brings,
            "present": False,
            "sha": None,
            "generated_at": None,
            "stale": None,
            "stale_sections": [],
            "note": "הקובץ לא קיים",
        }
    measured = str(data.get("sha") or "")
    stale_sections = _stale_subsections(data)
    stale: bool | None
    moved = bool(measured and sha) and not same_commit(measured, sha)
    if measured and sha:
        stale = moved or bool(stale_sections)
    elif stale_sections:
        stale = True
    else:
        stale = None
    notes = []
    if moved:
        notes.append(f"נמדד ב-{measured[:8]}, HEAD הוא {sha[:8]}")
    if stale is None:
        notes.append("הדוח לא חותם sha — אי אפשר לדעת באיזה commit נמדד")
    # Only worth naming when the file itself is at HEAD: if the whole report moved, every
    # section in it moved, and printing all twenty-six of them buries the one fact that matters.
    if stale_sections and not moved:
        notes.append("סעיפים ישנים: " + ", ".join(stale_sections))
    return {
        "key": source.key,
        "path": path,
        "command": source.command,
        "brings": source.brings,
        "present": True,
        "sha": measured or None,
        "generated_at": data.get("generated_at") or data.get("at"),
        "stale": stale,
        "stale_sections": stale_sections,
        "note": "; ".join(notes),
    }


def _state(
    row: Mapping[str, Any],
    *,
    present: bool | None = None,
    note: str = "",
    missing: str = "",
    command: str = "",
) -> dict[str, Any]:
    """The per-section stale/missing note the renderer prints under the heading.

    `present` is the *section's* presence, which is not the file's: `eval_answers.json`
    exists from the moment answers are built and grows its judge sections later. The two are
    kept apart so a pending section can say "the file is there, the section is not" instead
    of the false "the file does not exist".
    """
    file_present = bool(row["present"])
    there = file_present if present is None else bool(present)
    if not file_present:
        default = "הקובץ לא קיים"
    elif not there:
        default = missing or "הקובץ קיים, אבל הסעיף הזה עדיין לא נכתב לתוכו"
    else:
        default = str(row.get("note") or "")
    return {
        "present": there,
        "file_present": file_present,
        "path": row["path"],
        "command": command or row["command"],
        "sha": row.get("sha"),
        "generated_at": row.get("generated_at"),
        "stale": row.get("stale") if there else None,
        "stale_sections": list(row.get("stale_sections") or []),
        "note": note or default,
    }


# --------------------------------------------------------------------------- layers 0 and 1


def _harvest(index: Mapping[str, Any] | None) -> dict[str, Any]:
    """Counts per connector, and the link density that decided the corpus was worth it."""
    if not index:
        return {"present": False, "sources": [], "link_density": {}}
    rows = []
    for name, part in sorted((index.get("sources") or {}).items()):
        checkpoint = part.get("checkpoint") or {}
        stats = part.get("stats") or {}
        rows.append(
            {
                "source": name,
                "records": checkpoint.get("records", part.get("records")),
                "pages": checkpoint.get("pages", part.get("pages")),
                "done": checkpoint.get("done"),
                "errors": len(part.get("errors") or []),
                "detail": {
                    k: v for k, v in stats.items() if isinstance(v, (int, float, str, bool))
                },
            }
        )
    return {
        "present": True,
        "sources": rows,
        "link_density": index.get("link_density") or {},
        "since": (index.get("last_run") or {}).get("since"),
    }


def _census(index: Mapping[str, Any] | None) -> dict[str, Any]:
    """The Plan 1 census in one table — the graph these numbers were measured against."""
    if not index:
        return {"present": False}
    gate = index.get("gate") or {}
    return {
        "present": True,
        "corpus": index.get("corpus") or {},
        "nodes_total": (index.get("nodes") or {}).get("total"),
        "edges_total": (index.get("edges") or {}).get("total"),
        "edges_llm": (index.get("edges") or {}).get("llm_derived", {}).get("total"),
        "edges_deterministic": (index.get("edges") or {}).get("deterministic", {}).get("total"),
        "chunks": {k: v for k, v in (index.get("chunks") or {}).items() if not isinstance(v, dict)},
        "orphans": {
            k: v for k, v in (index.get("orphans") or {}).items() if not isinstance(v, dict)
        },
        "gate": {
            "ok": gate.get("ok"),
            "passed": gate.get("passed"),
            "total": gate.get("total"),
            "failed_names": list(gate.get("failed_names") or []),
        },
        "document": index.get("census_document"),
    }


def _resolution(
    index: Mapping[str, Any] | None, resolve: Mapping[str, Any] | None
) -> dict[str, Any]:
    """P/R/F1 per kind. `index.json` already summarises it; `resolve.json` is the fallback."""
    summary = (index or {}).get("resolution") or {}
    kinds = summary.get("kinds") or {}
    source = summary.get("source") or "data/reports/resolve.json"
    #: The file the printed numbers are read out of, which is not the same as `source` —
    #: `source` is where the census got them from, one step further back.
    summary_source = "data/reports/index.json" if kinds else "data/reports/resolve.json"
    if not kinds and resolve:
        # `brain resolve eval` writes the same numbers one level deeper.
        evaluated = resolve.get("eval") or {}
        kinds = {
            kind: {**(part.get("through_tier_1") or {}), "gold_pairs": part.get("gold_pairs")}
            for kind, part in evaluated.items()
            if isinstance(part, Mapping) and part.get("through_tier_1")
        }
        source = "data/reports/resolve.json"
    rows = [
        {
            "kind": kind,
            "gold_pairs": part.get("gold_pairs"),
            "precision": part.get("precision"),
            "recall": part.get("recall"),
            "f1": part.get("f1"),
            "tp": part.get("tp"),
            "fp": part.get("fp"),
            "fn": part.get("fn"),
            "nodes_before": part.get("nodes_before"),
            "nodes_after": part.get("nodes_after"),
            "ungraded_merges": part.get("ungraded_merges"),
            "ungraded_merges_source": summary_source,
            # `resolve.json` counts the merges through tier 3; the census counts them one
            # tier earlier, so the two disagree by one for `person` (1,656 vs 1,657). Both
            # are printed with their file: one of them is not "the" number.
            "ungraded_merges_resolve": _ungraded_from_resolve(resolve, kind),
        }
        for kind, part in sorted(kinds.items())
    ]
    return {
        "present": bool(rows),
        "rows": rows,
        "target": summary.get("target"),
        "source": source,
        "ungraded_sources": {
            summary_source: "מה שהמפקד סופר",
            "data/reports/resolve.json": "`through_tier_3` של `brain resolve eval`",
        },
        "note": summary.get("note") or (resolve or {}).get("eval", {}).get("note"),
    }


def _ungraded_from_resolve(resolve: Mapping[str, Any] | None, kind: str) -> Any:
    """The same count as `brain resolve eval` writes it — through every tier."""
    part = ((resolve or {}).get("eval") or {}).get(kind) or {}
    for tier in ("through_tier_3", "through_tier_2", "through_tier_1"):
        value = (part.get(tier) or {}).get("ungraded_merges")
        if value is not None:
            return value
    return None


def _provenance(index: Mapping[str, Any] | None) -> dict[str, Any]:
    """Iron rule 3, counted: how much of what an LLM produced can name its evidence."""
    prov = (index or {}).get("provenance") or {}
    if not prov:
        return {"present": False, "nodes": [], "edges": []}
    nodes = [
        {
            "label": label,
            "total": info.get("total"),
            "llm_derived": info.get("llm_derived"),
            "with_provenance": info.get("with_provenance"),
            "missing": info.get("missing"),
            "pct": info.get("pct"),
        }
        for label, info in ((prov.get("nodes") or {}).get("by_label") or {}).items()
    ]
    edges = [
        {
            "type": rel,
            "total": info.get("total"),
            "with_provenance": info.get("with_provenance"),
            "missing": info.get("missing"),
            "pct": info.get("pct"),
        }
        for rel, info in ((prov.get("edges") or {}).get("by_type") or {}).items()
    ]
    return {
        "present": True,
        "rule": prov.get("rule"),
        "nodes": nodes,
        "edges": edges,
        "edges_total": (prov.get("edges") or {}).get("total"),
        "edges_pct": (prov.get("edges") or {}).get("pct"),
        "nodes_pct": (prov.get("nodes") or {}).get("pct"),
    }


def _communities(index: Mapping[str, Any] | None) -> dict[str, Any]:
    """How much of the graph a global (S5) question can actually see."""
    com = (index or {}).get("communities") or {}
    if not com.get("present"):
        return {"present": False, "note": com.get("note")}
    return {
        "present": True,
        "communities": com.get("communities"),
        "summarised": com.get("summarised"),
        "pct_summarised": com.get("pct_summarised"),
        "distinct_members": com.get("distinct_members"),
        "in_community_edges": com.get("in_community_edges"),
        "pct_members_in_a_summarised_community": com.get("pct_members_in_a_summarised_community"),
        "levels": list(com.get("levels") or []),
    }


#: A `gold_query` that starts with this is not an independent gold: it is the very function
#: the strategy under test executes, so a `1.0` for that strategy on that question says the
#: call is deterministic and says nothing about retrieval.
SHARED_CODE_PATH = "brain.retrieve."
#: The two arms the question set was supposed to be drawn from (spec §5.1). One of them
#: produced nothing, and a table of one row does not say so out loud.
GOLD_ARMS: tuple[str, ...] = ("graph", "truth")


def read_questions(eval_dir: Path, path: str = "") -> list[dict[str, Any]]:
    """`questions.jsonl` as rows. Missing or malformed is an empty set, never an error."""
    candidates = [Path(eval_dir) / "questions.jsonl"]
    if path:
        candidates.append(Path(path))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        rows = []
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows
    return []


def _gold_source(counts: Mapping[str, Any]) -> dict[str, Any]:
    """ "graph 32 / truth 0" — and, when an arm is empty, what that arm was for."""
    parts = [f"{arm} {int(counts.get(arm) or 0)}" for arm in GOLD_ARMS]
    extra = [f"{k} {v}" for k, v in sorted(counts.items()) if k not in GOLD_ARMS]
    empty = [arm for arm in GOLD_ARMS if not counts.get(arm)]
    gap = ""
    if "truth" in empty:
        gap = (
            "אף שאלה לא נגזרה מ-`synthetic_truth.json` — הזרוע הסינתטית של §5.1 לא נבדקה "
            "בפועל, וכל ה-gold מגיע מהגרף עצמו."
        )
    return {
        "gold_source_line": "gold source: " + " / ".join(parts + extra),
        "gold_source_empty_arms": empty,
        "gold_source_gap": gap,
    }


def _shared_code_path(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Which questions hand the gold the function the strategy under test runs."""
    shared = sorted(
        str(row.get("id"))
        for row in rows
        if str(row.get("gold_query") or "").startswith(SHARED_CODE_PATH)
    )
    types = sorted({str(r.get("type")) for r in rows if str(r.get("id")) in set(shared)})
    return {
        "gold_shares_code_path": shared,
        "gold_shares_code_path_types": types,
        "gold_shares_code_path_note": (
            "gold shares the strategy's code path — the gold answer of these questions is "
            "the output of the same `brain.retrieve.temporal.*` call the strategy executes, "
            "so their recall measures determinism rather than retrieval."
            if shared
            else ""
        ),
    }


def _questions(
    report: Mapping[str, Any] | None, *, rows: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """The set under measurement: how many, of which type, in which language."""
    if not report:
        return {"present": False, **_shared_code_path(rows)}
    merge = report.get("merge") or {}
    counts = merge.get("counts") or {}
    totals = merge.get("totals") or {}
    return {
        "present": True,
        "path": merge.get("questions_path"),
        "sha256": merge.get("sha256"),
        "complete": merge.get("complete"),
        "questions": totals.get("questions"),
        "target": totals.get("target"),
        "by_type": counts.get("by_type") or {},
        "by_lang": counts.get("by_lang") or {},
        "by_gold_source": counts.get("by_gold_source") or {},
        "by_origin": counts.get("by_origin") or {},
        "checks": list(merge.get("checks") or []),
        "hebrew_floor": (merge.get("target") or {}).get("hebrew_floor"),
        **_gold_source(counts.get("by_gold_source") or {}),
        **_shared_code_path(rows),
    }


# ------------------------------------------------------------------------------- layer 2


def order_strategies(names) -> list[str]:
    """`STRATEGY_ORDER` first, anything unexpected after it — never silently dropped."""
    known = [s for s in STRATEGY_ORDER if s in set(names)]
    extra = sorted(set(names) - set(STRATEGY_ORDER))
    return known + extra


def _na_reasons(cell: Mapping[str, Any]) -> list[str]:
    """Every reason a cell was `n/a`, commonest first — all of them, in count order.

    Order matters because the renderer shows the head of this list: S5 alone produces nine
    variants of one sentence (the router rule is spelled into the reason), and an
    alphabetical list would show three arbitrary ones. Sorted by count, the head is the
    reason, and the tail is the long way of saying it again.
    """
    reasons = (cell.get("na_reasons") or {}).items()
    return [
        f"{reason} ({count})"
        for reason, count in sorted(reasons, key=lambda kv: (-int(kv[1]), str(kv[0])))
    ]


def _layer2(retrieval: Mapping[str, Any] | None, infra: Mapping[str, Any] | None) -> dict[str, Any]:
    """The deterministic half of §5.2: matrix, cost, cross-lingual, and the stack's own cost."""
    stack = _infra(infra)
    if not retrieval:
        return {"present": False, "strategies": [], "types": [], "matrix": {}, "infra": stack}
    matrix = retrieval.get("matrix") or {}
    strategies = order_strategies(set(matrix) | set(retrieval.get("by_strategy") or {}))
    types = list(retrieval.get("types") or sorted({t for row in matrix.values() for t in row}))
    cost_rows = []
    for name in strategies:
        cost = (retrieval.get("cost") or {}).get(name) or {}
        summary = (retrieval.get("by_strategy") or {}).get(name) or {}
        cost_rows.append(
            {
                "strategy": name,
                "runs": cost.get("runs", summary.get("ok")),
                "na": cost.get("na", summary.get("na")),
                "latency_p50_ms": cost.get("latency_p50_ms"),
                "latency_p95_ms": cost.get("latency_p95_ms"),
                "context_tokens_p50": cost.get("context_tokens_p50"),
                "context_tokens_total": cost.get("context_tokens_total"),
                "tool_calls": cost.get("tool_calls"),
                "cypher_total": cost.get("cypher_total", summary.get("cypher_total")),
                "cypher_p50": cost.get("cypher_p50"),
                "agent_time_ms": cost.get("agent_time_ms"),
                "na_reasons": _na_reasons(summary),
            }
        )
    return {
        "present": True,
        "baseline": retrieval.get("baseline") or BASELINE,
        "budget_tokens": retrieval.get("budget_tokens"),
        "k": retrieval.get("k"),
        "mode": retrieval.get("mode"),
        "questions_total": retrieval.get("questions_total"),
        "match_kinds": list(retrieval.get("match_kinds") or []),
        "strict_match_kinds": list(retrieval.get("strict_match_kinds") or []),
        "strategies": strategies,
        "types": types,
        "matrix": matrix,
        "by_strategy": retrieval.get("by_strategy") or {},
        "by_type": retrieval.get("by_type") or {},
        "cost": cost_rows,
        "cross_lingual": list(retrieval.get("cross_lingual") or []),
        "coverage": retrieval.get("coverage") or {},
        "notes": list(retrieval.get("notes") or []),
        "missing_strategies": [s for s in STRATEGY_ORDER[:-1] if s not in strategies],
        "infra": stack,
    }


def _infra(retrieve: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the baseline's reranker costs, and whether the Cypher guard held — measured."""
    if not retrieve:
        return {"present": False}
    rerank = retrieve.get("rerank") or {}
    guard = retrieve.get("guard") or {}
    summary = rerank.get("summary") or {}
    blocked = guard.get("blocked") or {}
    allowed = guard.get("allowed") or {}
    return {
        "present": bool(rerank or guard),
        "rerank": {
            "present": bool(rerank),
            "model": rerank.get("model"),
            "available": rerank.get("available"),
            "questions": summary.get("questions"),
            "top1_changed": summary.get("top1_changed"),
            "top1_changed_pct": summary.get("top1_changed_pct"),
            "top1_source_changed": summary.get("top1_source_changed"),
            "mean_top5_overlap": summary.get("mean_top5_overlap"),
            "p50_without_ms": (summary.get("latency_without_rerank") or {}).get("p50_ms"),
            "p50_with_ms": (summary.get("latency_with_rerank") or {}).get("p50_ms"),
            "cost_p50_ms": summary.get("rerank_cost_p50_ms"),
            "note": rerank.get("note"),
        },
        "guard": {
            "present": bool(guard),
            "mode": guard.get("mode"),
            "cases": blocked.get("cases"),
            "refused": blocked.get("refused"),
            "refused_pct": blocked.get("refused_pct"),
            "leaked": len(blocked.get("leaked") or []),
            "reads_passed": allowed.get("passed"),
            "reads_cases": allowed.get("cases"),
            "limit_injected": allowed.get("limit_injected"),
            "timeout_ms": (guard.get("timeout") or {}).get("elapsed_ms"),
            "timeout_refused": (guard.get("timeout") or {}).get("refused"),
        },
    }


# ------------------------------------------------------------------------------- layer 3


def _agentic_answers(eval_dir: Path) -> dict[str, Any]:
    """Mode B's column exists exactly when `<qid>.agentic.json` does."""
    directory = Path(eval_dir) / ANSWERS_SUBDIR
    files = sorted(directory.glob(AGENTIC_GLOB)) if directory.is_dir() else []
    return {
        "present": bool(files),
        "dir": str(directory),
        "answers": len(files),
        "qids": [p.name.split(".")[0] for p in files],
        "column": AGENTIC,
    }


def _mode_b_gate(gate_report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Plan 2's deterministic citation gate — the only mode B number that is not a judge's."""
    if not gate_report:
        return {"present": False}
    totals = gate_report.get("totals") or {}
    gate = gate_report.get("gate") or {}
    return {
        "present": True,
        "questions": totals.get("questions"),
        "answered": totals.get("answered"),
        "citations_found": totals.get("citations_found"),
        "citations_valid": totals.get("citations_valid"),
        "citations_invalid": totals.get("citations_invalid"),
        "validity_rate": totals.get("validity_rate"),
        "with_at_least_one_valid_citation": totals.get("with_at_least_one_valid_citation"),
        "ok": gate.get("ok"),
        "passed": gate.get("passed"),
        "total": gate.get("total"),
    }


def _citation_code_summary(check: Mapping[str, Any] | None) -> dict[str, Any]:
    """The binary code check, counted — the thing the judge's 0–2 is compared against."""
    rows = list((check or {}).values())
    valid = sum(1 for r in rows if r.get("code_valid") is True)
    invalid = sum(1 for r in rows if r.get("code_valid") is False)
    return {
        "cases": len(rows),
        "code_valid": valid,
        "code_invalid": invalid,
        # The same count against the graph the retrieval saw, not the graph as it is now.
        "code_valid_in_snapshot": sum(1 for r in rows if r.get("code_valid_in_snapshot") is True),
        "no_citation": sum(1 for r in rows if not r.get("citations")),
        "not_in_context": sum(1 for r in rows if r.get("not_in_context")),
        "not_in_graph": sum(1 for r in rows if r.get("not_in_graph")),
        "not_in_snapshot": sum(1 for r in rows if r.get("not_in_snapshot")),
    }


def _with_n(cells: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every layer-3 row carries the denominator its means rest on, under one name.

    `judge merge` writes `n` now; a report written before it did carries only `cases`, and
    a table with an empty `n` column would read as "nobody counted" rather than "this file
    predates the column".
    """
    return {
        name: {**dict(cell), "n": cell.get("n", cell.get("cases"))} for name, cell in cells.items()
    }


def _dropped(metrics: Mapping[str, Any], merge: Mapping[str, Any]) -> dict[str, Any]:
    """The answered cases no judge ever saw, by strategy — why the denominators differ.

    Prefers what `judge merge` wrote; derives the same list from `answers merge` when the
    report predates that, so the section is never silently empty on an older file.
    """
    rows = list(metrics.get("dropped") or [])
    if not rows:
        from brain.eval.judge_report import dropped_cases

        rows = dropped_cases(merge)
    by_strategy: dict[str, int] = {}
    for row in rows:
        name = str(row.get("strategy") or "")
        by_strategy[name] = by_strategy.get(name, 0) + 1
    return {
        "count": len(rows),
        "rows": rows,
        "by_strategy": dict(sorted(by_strategy.items())),
        "reasons": sorted({str(r.get("reason") or "") for r in rows}),
    }


def _citations_by_strategy(merge: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per strategy: how many citations the answers carried, widened and strict.

    The widened count accepts a cited key that names the *parent* of a context item; strict
    wants the id the context actually listed. Printing only the widened one hides S4's
    habit of citing a row's work item instead of the row (192 valid, 140 strict)."""
    rows = (merge.get("answers") or {}).get("by_strategy") or {}
    return [
        {
            "strategy": name,
            "answers": cell.get("answers"),
            "refused": cell.get("refused"),
            "citations": cell.get("citations"),
            "valid": cell.get("valid"),
            "valid_strict": cell.get("valid_strict"),
            "in_context_pct": cell.get("citation_in_context_pct"),
            "in_context_strict_pct": cell.get("citation_in_context_strict_pct"),
        }
        for name, cell in sorted(rows.items(), key=lambda kv: _strategy_rank(kv[0]))
    ]


def _strategy_rank(name: str) -> tuple[int, str]:
    return (STRATEGY_ORDER.index(name) if name in STRATEGY_ORDER else 99, str(name))


def _layer3(
    answers: Mapping[str, Any] | None,
    *,
    agentic: Mapping[str, Any],
    gate_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Layer 3 of §5.2, as `brain eval judge merge` writes it into `eval_answers.json`."""
    answers = answers or {}
    metrics = answers.get("metrics") or {}
    matrix = metrics.get("matrix") or {}
    build = answers.get("answers_build") or {}
    merge = answers.get("answers_merge") or {}
    judged = bool(matrix or metrics.get("by_strategy"))
    strategies = order_strategies(set(matrix) | set(metrics.get("by_strategy") or {}))
    types = sorted({t for row in matrix.values() for t in row} | set(metrics.get("by_type") or {}))
    judge_merge = answers.get("judge_merge") or {}
    return {
        "present": judged,
        "metrics": JUDGE_METRICS,
        "strategies": strategies,
        "types": types,
        "matrix": matrix,
        "by_strategy": _with_n(metrics.get("by_strategy") or {}),
        "by_type": _with_n(metrics.get("by_type") or {}),
        "by_lang": _with_n(metrics.get("by_lang") or {}),
        "dropped": _dropped(metrics, merge),
        "citations": _citations_by_strategy(merge),
        "note": metrics.get("note"),
        "pairwise": answers.get("pairwise") or {},
        "agreement": answers.get("agreement") or {},
        "crosscheck": answers.get("citation_crosscheck") or {},
        "code_check": _citation_code_summary(answers.get("citation_code_check")),
        "judge_merge": {
            k: v
            for k, v in (answers.get("judge_merge") or {}).items()
            if k in {"generated_at", "sha", "batches", "judgments", "metrics"}
        },
        "answers": {
            "present": bool(build or merge),
            "cases": (build.get("totals") or {}).get("cases"),
            "batches": (build.get("totals") or {}).get("batches"),
            "skipped": (build.get("totals") or {}).get("skipped"),
            "accepted": (merge.get("answers") or {}).get("accepted"),
            "rejected": (merge.get("answers") or {}).get("rejected"),
            # Built, accepted and judged are three different numbers and the report used to
            # print one of them twice ("184 מתוך 184" while 190 cases were judged, because
            # mode B's nineteen were merged elsewhere).
            "judged": ((judge_merge.get("judgments") or {}).get("cases")),
            "judgments": ((judge_merge.get("judgments") or {}).get("total")),
            "complete": merge.get("complete"),
            "missing_batches": len((merge.get("batches") or {}).get("missing") or []),
        },
        "mode_b": {**dict(agentic), "gate": _mode_b_gate(gate_report)},
    }


def _layer3_pending(layer3: Mapping[str, Any]) -> str:
    """How far the answering pipeline got, for the note under an unjudged layer 3."""
    answers = layer3.get("answers") or {}
    if not answers.get("present"):
        return "התשובות של מצב A טרם נבנו (`brain eval answers build`)."
    return (
        f"התשובות נבנו ({_count(answers.get('cases'))} מקרים ב-"
        f"{_count(answers.get('batches'))} batches, {_count(answers.get('accepted'))} מוזגו), "
        "אבל השופטים טרם החזירו ציונים."
    )


def _count(value: Any) -> str:
    return "—" if value is None else str(value)


# -------------------------------------------------------------------------- "מתי מה"


#: Below this many questions a "leader" is a coincidence with a name. S5 answered six global
#: questions and nothing else; a cell with two is a sample, not a finding, so the table
#: prints its `n` and refuses the word.
MIN_LEADER_N = 3


def _best(
    cells: Mapping[str, Mapping[str, Any]], metric: str, *, count_key: str = "scored"
) -> dict[str, Any] | None:
    """Highest `metric` over the strategies that actually scored something, ties by order.

    Carries `n` — how many questions the winning cell rests on — and `enough`, which is
    `False` when that `n` is below `MIN_LEADER_N`. Both are printed: a leader whose lead
    nobody can test is worth naming and worth not believing.
    """
    ranked = [
        (float(cell[metric]), STRATEGY_ORDER.index(name) if name in STRATEGY_ORDER else 99, name)
        for name, cell in cells.items()
        if cell.get(metric) is not None
    ]
    if not ranked:
        return None
    value, _, name = max(ranked, key=lambda row: (row[0], -row[1]))
    count = (cells.get(name) or {}).get(count_key)
    n = int(count) if isinstance(count, (int, float)) else None
    return {
        "strategy": name,
        "value": value,
        "n": n,
        "enough": bool(n is not None and n >= MIN_LEADER_N),
    }


def _delta(cell: Mapping[str, Any] | None, base: Mapping[str, Any] | None, key: str) -> Any:
    if not cell or not base or cell.get(key) is None or base.get(key) is None:
        return None
    return round(float(cell[key]) - float(base[key]), 4)


def when_what(layer2: Mapping[str, Any], layer3: Mapping[str, Any]) -> dict[str, Any]:
    """Per question type: who won on retrieval, who won on correctness, what the win cost.

    This is the only derived table in the document. It states the baseline's number beside
    the winner's on purpose — "S3 is best for traceability" is a sentence, "0.61 against the
    baseline's 0.30, for +38ms and +1.2k tokens" is a measurement.
    """
    matrix2 = layer2.get("matrix") or {}
    matrix3 = layer3.get("matrix") or {}
    baseline = layer2.get("baseline") or BASELINE
    types = list(layer2.get("types") or []) or list(layer3.get("types") or [])
    rows = []
    for qtype in types:
        retrieval_cells = {
            name: row[qtype]
            for name, row in matrix2.items()
            if qtype in row and (row[qtype] or {}).get("scored")
        }
        answer_cells = {
            name: row[qtype]
            for name, row in matrix3.items()
            if qtype in row and (row[qtype] or {}).get("cases")
        }
        best_recall = _best(retrieval_cells, "recall")
        best_correct = _best(answer_cells, "correctness", count_key="cases")
        base_cell = (matrix2.get(baseline) or {}).get(qtype)
        base_answer = (matrix3.get(baseline) or {}).get(qtype)
        winner_cell = (
            (matrix2.get(best_recall["strategy"]) or {}).get(qtype) if best_recall else None
        )
        reasons = []
        if not retrieval_cells:
            reasons.append("אף אסטרטגיה לא נמדדה על הסוג הזה")
        elif baseline not in retrieval_cells:
            reasons.append(f"ה-baseline ({baseline}) לא רץ על הסוג הזה")
        if not answer_cells:
            reasons.append("אין שיפוט")
        differ = bool(
            best_recall and best_correct and best_recall["strategy"] != best_correct["strategy"]
        )
        rows.append(
            {
                "type": qtype,
                "best_recall": best_recall,
                "baseline_recall": (base_cell or {}).get("recall"),
                "best_correctness": best_correct,
                "baseline_correctness": (base_answer or {}).get("correctness"),
                # The three deltas price the *recall* leader's win. They used to be printed
                # beside the cell that names the correctness leader, which reads as the
                # correctness leader's price. It is not; this names whose it is.
                "cost_attributed_to": (best_recall or {}).get("strategy"),
                "latency_delta_ms": _delta(winner_cell, base_cell, "latency_p50_ms"),
                "tokens_delta": _delta(winner_cell, base_cell, "context_tokens_p50"),
                "cypher_delta": _delta(winner_cell, base_cell, "cypher_total"),
                "correctness_cost": _correctness_cost(best_correct, matrix2, base_cell, qtype),
                "leaders_differ": differ,
                "leaders_line": _leaders_line(best_recall, best_correct) if differ else "",
                "note": "; ".join(reasons),
            }
        )
    diverging = [r for r in rows if r["leaders_differ"]]
    return {
        "present": bool(rows),
        "baseline": baseline,
        "rows": rows,
        "judged": bool(matrix3),
        "diverging_types": len(diverging),
        "divergence_line": (
            f"{len(diverging)} מתוך {len(rows)} סוגי שאלה: מובילת ה-recall אינה מובילת "
            "הנכונוּת (" + ", ".join(r["type"] for r in diverging) + ")"
            if diverging
            else f"בכל {len(rows)} סוגי השאלה מובילת ה-recall היא גם מובילת הנכונוּת."
        ),
    }


def _correctness_cost(
    best_correct: Mapping[str, Any] | None,
    matrix2: Mapping[str, Any],
    base_cell: Mapping[str, Any] | None,
    qtype: str,
) -> dict[str, Any]:
    """What the correctness leader cost — or nothing at all, when nobody measured it.

    Mode B is the case this exists for: `agentic` is the most correct column in the document
    and has no layer-2 cost cell, because it packed no context and was run without a
    stopwatch. An empty number there has to read as "not measured", never as "free".
    """
    if not best_correct:
        return {"strategy": None, "available": False, "why": "אין שיפוט לסוג הזה"}
    name = str(best_correct["strategy"])
    cell = (matrix2.get(name) or {}).get(qtype)
    if not cell:
        return {
            "strategy": name,
            "available": False,
            "why": f"ל-`{name}` אין תא עלות בשכבה 2 — מצב B לא ארז הקשר ולא נמדד בשעון",
            "latency_delta_ms": None,
            "tokens_delta": None,
            "cypher_delta": None,
        }
    return {
        "strategy": name,
        "available": True,
        "why": "",
        "latency_delta_ms": _delta(cell, base_cell, "latency_p50_ms"),
        "tokens_delta": _delta(cell, base_cell, "context_tokens_p50"),
        "cypher_delta": _delta(cell, base_cell, "cypher_total"),
    }


def _n_of(best: Mapping[str, Any]) -> str:
    return "n=?" if best.get("n") is None else f"n={best['n']}"


def _leaders_line(best_recall: Mapping[str, Any], best_correct: Mapping[str, Any]) -> str:
    """The contradiction, as a generated sentence instead of as planner prose."""
    return (
        f"מובילת recall ≠ מובילת נכונוּת: `{best_recall['strategy']}` "
        f"(r={best_recall['value']}, {_n_of(best_recall)}) מול `{best_correct['strategy']}` "
        f"(c={best_correct['value']}, {_n_of(best_correct)})"
    )


# --------------------------------------------------------------------------- incremental


def _incremental(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """§5.5: what the increment cost per step, what it added, what it moved."""
    if not report:
        return {"present": False, "steps": [], "questions": []}
    try:
        from brain.harvest.incremental import PIPELINE_STEPS, validate_incremental

        expected = list(PIPELINE_STEPS)
        problems = validate_incremental(dict(report))
    except Exception:  # noqa: BLE001 - a report is still worth printing without its validator
        expected, problems = [], []
    measured = {str(row.get("step")): row for row in report.get("steps") or []}
    steps = [
        {
            "step": step,
            "command": (measured.get(step) or {}).get("command"),
            "duration_s": (measured.get(step) or {}).get("duration_s"),
            "notes": (measured.get(step) or {}).get("notes"),
            "measured": step in measured,
        }
        for step in (expected or list(measured))
    ]
    return {
        "present": True,
        "since": report.get("since"),
        "source": report.get("source"),
        "slice": report.get("slice"),
        "keys": list(report.get("keys") or []),
        "duration_s": report.get("duration_s"),
        "steps": steps,
        "graph": report.get("graph") or {},
        "chunks": report.get("chunks") or {},
        "entities": report.get("entities") or {},
        "communities": report.get("communities") or {},
        "summary": _incremental_summary(report),
        "rollback": _rollback(report),
        "questions": _incremental_questions(report),
        "problems": problems,
        "warnings": list(report.get("warnings") or []),
        "errors": list(report.get("errors") or []),
    }


def _delta_of(part: Mapping[str, Any] | None, key: str = "") -> int | None:
    """`{"before": x, "after": y}` -> `y - x`; `key` when each side is a census of its own."""
    part = part or {}
    before, after = part.get("before"), part.get("after")
    if key:
        before = before.get(key) if isinstance(before, Mapping) else None
        after = after.get(key) if isinstance(after, Mapping) else None
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    return int(after) - int(before)


def _coverage(report: Mapping[str, Any]) -> tuple[Any, Any]:
    """Community-report coverage before and after — measured by the increment's `brain index`."""
    for step in report.get("steps") or []:
        communities = ((step or {}).get("report") or {}).get("communities") or {}
        before = (communities.get("before") or {}).get("pct_members_in_a_summarised_community")
        after = (communities.get("after") or {}).get("pct_members_in_a_summarised_community")
        if before is not None or after is not None:
            return before, after
    return None, None


def _incremental_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    """The eight numbers §5.5 asks for, each under its own name.

    The section used to print four sub-objects into four table cells, and one of them held
    645 community ids. A cell that needs scrolling is not a measurement; and `chunks` was
    read from a `chunks` key the run never wrote, so it printed as empty while the number
    sat in `graph.census_after_part1.chunks`.
    """
    graph = report.get("graph") or {}
    census = graph.get("census_after_part1") or {}
    communities = report.get("communities") or {}
    before, after = _coverage(report)
    return {
        "nodes_added": _delta_of(census.get("nodes_total")),
        "edges_added": _delta_of(census.get("edges_total")),
        "chunks_added": _delta_of(census.get("chunks"), "chunks"),
        "communities_before": communities.get("total_before"),
        "communities_after": communities.get("total_after"),
        # The count only. The 645 ids live in `incremental.json`, which is where a reader
        # who wants them goes.
        "communities_changed": communities.get("member_hash_changed"),
        "reports_kept": communities.get("reports_carried_over"),
        "reports_lost": communities.get("reports_dropped"),
        "coverage_before": before,
        "coverage_after": after,
        "entities_minted": (report.get("entities") or {}).get("entities_minted_here"),
    }


#: `  incremental would delete      43 :Chunk nodes` — the dry run prints its plan as prose.
ROLLBACK_LINE = re.compile(r"would delete\s+([\d,]+)\s+(.+?)\s*$")


def _rollback(report: Mapping[str, Any]) -> dict[str, Any]:
    """What `brain reset --slice` said it would remove, counted per label.

    The later of the two dry runs wins: the first one ran before the provenance repair and
    counted three entities that the repair gave back their base-run evidence.
    """
    graph = report.get("graph") or {}
    run = graph.get("rollback_dry_run_after_fix") or graph.get("rollback_dry_run") or {}
    counts: dict[str, int] = {}
    for line in run.get("output") or []:
        found = ROLLBACK_LINE.search(str(line))
        if found:
            counts[found.group(2)] = int(found.group(1).replace(",", ""))
    return {
        "present": bool(run),
        "command": run.get("command"),
        "applied": bool(run.get("applied")),
        "counts": dict(sorted(counts.items())),
        "note": (
            "Dry run only. Plan 3 decision 7: the rollback is proven on a slice fixture in "
            "`_ResetTest`, not by deleting from the measured graph."
        ),
    }


def _incremental_questions(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The five questions about the new items, mode A beside mode B."""
    out = []
    for question in report.get("questions") or []:
        mode_b = question.get("mode_b") or {}
        out.append(
            {
                "qid": question.get("id") or question.get("qid"),
                "type": question.get("type"),
                "route": question.get("route"),
                "best_strategy": question.get("best_strategy"),
                "best_recall": question.get("best_recall"),
                "mode_a_citation_valid": question.get(
                    "mode_a_citation_valid", question.get("citation_valid")
                ),
                "mode_b_citations": mode_b.get("citations"),
                "mode_b_citations_valid": mode_b.get("citations_valid"),
                "mode_b_citation_valid": mode_b.get("citation_valid"),
            }
        )
    return out


# ------------------------------------------------------------------------------- the doc


def build(
    reports_dir: Path,
    *,
    eval_dir: Path,
    sha: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Everything the document says, read from `reports_dir` and `eval_dir`. No formatting."""
    reports_dir = Path(reports_dir)
    if not reports_dir.is_dir():
        raise ReportError(f"{reports_dir} does not exist — no step has written a report yet")
    sha = head_sha() if sha is None else sha
    data = load(reports_dir)
    rows = {s.key: input_row(s, data[s.key], sha=sha) for s in INPUTS}

    layer2 = _layer2(data["eval_retrieval"], data["retrieve"])
    agentic = _agentic_answers(Path(eval_dir))
    layer3 = _layer3(data["eval_answers"], agentic=agentic, gate_report=data["plan2_gate"])
    resolution = _resolution(data["index"], data["resolve"])
    communities = _communities(data["index"])
    incremental = _incremental(data["incremental"])
    questions_merge = (data["eval_questions"] or {}).get("merge") or {}
    question_rows = read_questions(Path(eval_dir), str(questions_merge.get("questions_path") or ""))

    return {
        "step": "eval.report",
        "generated_at": generated_at or utc_now_iso(),
        "sha": sha,
        "document": str(DOCUMENT),
        "reports_dir": str(reports_dir),
        "eval_dir": str(eval_dir),
        "command": "brain eval report",
        "inputs": [rows[s.key] for s in INPUTS],
        "questions": {
            **_questions(data["eval_questions"], rows=question_rows),
            "state": _state(rows["eval_questions"]),
        },
        "layer01": {
            "harvest": {**_harvest(data["harvest"]), "state": _state(rows["harvest"])},
            "census": {**_census(data["index"]), "state": _state(rows["index"])},
            "resolution": {
                **resolution,
                "state": _state(
                    rows["resolve"],
                    present=resolution["present"],
                    missing="הדוח קיים אבל אין בו סעיף `eval` — P/R נמדדים מול זוגות הזהב.",
                ),
            },
            "provenance": {**_provenance(data["index"]), "state": _state(rows["index"])},
            "communities": {
                **communities,
                "state": _state(
                    rows["index"],
                    present=communities["present"],
                    missing="המפקד קיים אבל לא מצא קהילות.",
                    command="brain communities",
                ),
            },
        },
        "layer2": {**layer2, "state": _state(rows["eval_retrieval"], present=layer2["present"])},
        "layer3": {
            **layer3,
            "state": _state(
                rows["eval_answers"],
                present=layer3["present"],
                missing=_layer3_pending(layer3),
                command="brain eval judge merge",
            ),
        },
        "incremental": {
            **incremental,
            "state": _state(rows["incremental"], present=incremental["present"]),
        },
        "when_what": when_what(layer2, layer3),
    }


def run_report(
    *,
    reports_dir: Path,
    eval_dir: Path,
    out_path: Path,
) -> tuple[Path, dict[str, Any]]:
    """Build the dict, render the Hebrew, keep the planner's two blocks, write the file."""
    from brain.eval import report_render

    out_path = Path(out_path)
    doc = build(reports_dir, eval_dir=Path(eval_dir))
    previous = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_render.render(doc, previous=previous), encoding="utf-8")
    return out_path, doc


def summary_lines(doc: Mapping[str, Any]) -> list[str]:
    """What the terminal says: which sections rendered, and which are still pending."""
    lines = []
    for row in doc["inputs"]:
        if not row["present"]:
            mark = "MISSING"
        elif row["stale"] is True:
            mark = "STALE  "
        elif row["stale"] is None:
            mark = "UNSTAMPED"
        else:
            mark = "OK     "
        lines.append(f"  [{mark}] {row['path']}  ({row['command']})")
    pending = [
        name
        for name, part in (
            ("layer 2", doc["layer2"]),
            ("layer 3", doc["layer3"]),
            ("incremental", doc["incremental"]),
        )
        if not part.get("present")
    ]
    lines.append(f"pending sections: {', '.join(pending) if pending else 'none'}")
    return lines


# ----------------------------------------------------------------------------------- CLI


def eval_report(
    out: str = typer.Option(
        str(DOCUMENT), "--out", metavar="FILE", help="Where to write the Hebrew report."
    ),
    reports: str = typer.Option(
        None, "--reports", metavar="DIR", help="Where the step reports are (default: data/reports)."
    ),
) -> None:
    """Generate docs/report/eval-report.md from the step reports — no number typed [Plan 3].

    Every section that has no input renders as `טרם נמדד` with the command that produces it,
    so the document is complete in shape from the first run and fills in as the steps land.
    The planner's two closing sections are carried across regenerations.
    """
    from brain.config import get_settings

    settings = get_settings()
    reports_dir = Path(reports) if reports else settings.reports_dir
    try:
        path, doc = run_report(
            reports_dir=reports_dir, eval_dir=settings.eval_dir, out_path=Path(out)
        )
    except ReportError as exc:
        typer.echo(f"eval report: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    for line in summary_lines(doc):
        typer.echo(line)
    typer.echo(f"report: {path}")


__all__ = [
    "INPUTS",
    "Source",
    "ReportError",
    "build",
    "eval_report",
    "load",
    "order_strategies",
    "run_report",
    "summary_lines",
    "when_what",
]
