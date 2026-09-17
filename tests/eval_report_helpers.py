"""Fixtures for `brain eval report` — one tiny, complete version of every input.

The layer-3 fixture is not typed out by hand. `brain eval judge merge` is the only thing
that writes `metrics` / `pairwise` / `agreement` / `citation_crosscheck` into
`eval_answers.json`, so the fixture is produced by calling *its* functions on synthetic
judgments. A test built against a hand-copied shape passes forever after the producer
changes; this one stops compiling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.eval import judge_report

SHA = "a" * 40
OTHER_SHA = "b" * 40
AT = "2026-09-17T12:00:00+00:00"


def _stamps(keys, *, sha: str = SHA, at: str = AT, stale: tuple[str, ...] = ()) -> dict[str, Any]:
    """The `sections` index the self-stamping reports carry (`brain/retrieve/report.py`)."""
    return {key: {"sha": sha, "generated_at": at, "stale": key in stale} for key in keys}


# --------------------------------------------------------------------------- layers 0-1
#
# Every fixture carries the top-level `sha` its writer stamps (`brain/common/stamp.py`), so
# the header table renders `עדכני`/`STALE` rather than the third state, "בלי sha", which
# says only that nobody can tell. `without_sha()` builds the legacy shape on purpose.


def without_sha(report: dict[str, Any]) -> dict[str, Any]:
    """A report as the writers wrote it before they stamped — still on disk, still readable."""
    return {key: value for key, value in report.items() if key != "sha"}


def harvest() -> dict[str, Any]:
    return {
        "step": "harvest",
        "generated_at": AT,
        "sha": SHA,
        "last_run": {"since": None},
        "sources": {
            "jira": {
                "records": 0,
                "errors": [],
                "checkpoint": {"records": 1416, "pages": 3, "done": True},
                "stats": {"issues": 1416, "pct_changelog_expanded": 100.0},
            },
            "git": {
                "records": 0,
                "errors": [],
                "checkpoint": {"records": 6107, "pages": 7, "done": True},
                "stats": {},
            },
        },
        "link_density": {"all": {"n": 1416, "pct_formal_links": 36.0, "pct_kip_mention": 21.4}},
    }


def index() -> dict[str, Any]:
    return {
        "step": "index",
        "generated_at": AT,
        "sha": SHA,
        "census_document": "docs/report/plan1-graph-census.md",
        "corpus": {
            "real_issues": 1416,
            "synthetic_workitems": 1849,
            "kip_documents": 1334,
            "kips_referenced": 267,
            "commits": 6107,
        },
        "nodes": {"total": 58622},
        "edges": {
            "total": 161985,
            "llm_derived": {"total": 14762},
            "deterministic": {"total": 147223},
        },
        "chunks": {"chunks": 13846, "live": 12915, "embedded": 13846, "by_kind": {"issue": 9}},
        "orphans": {"total": 176, "pct": 0.3, "by_label": {"Entity": {"orphans": 1}}},
        "provenance": {
            "rule": "conventions rule 3",
            "nodes": {
                "pct": 100.0,
                "by_label": {
                    "Entity": {
                        "total": 9038,
                        "llm_derived": 9038,
                        "with_provenance": 9038,
                        "missing": 0,
                        "pct": 100.0,
                    }
                },
            },
            "edges": {
                "total": 14762,
                "pct": 100.0,
                "by_type": {
                    "MENTIONS": {"total": 9375, "with_provenance": 9375, "missing": 0, "pct": 100.0}
                },
            },
        },
        "communities": {
            "present": True,
            "communities": 1297,
            "summarised": 186,
            "pct_summarised": 14.34,
            "distinct_members": 11875,
            "in_community_edges": 23750,
            "pct_members_in_a_summarised_community": 89.41,
            "levels": [
                {
                    "level": 0,
                    "communities": 762,
                    "size_p50": 4,
                    "size_p95": 53,
                    "members": 11875,
                    "summarised": 106,
                }
            ],
        },
        "resolution": {
            "available": True,
            "source": "data/reports/resolve.json",
            "target": 0.85,
            "note": "gold pairs only",
            "kinds": {
                "person": {
                    "gold_pairs": 633,
                    "precision": 0.9801,
                    "recall": 0.7789,
                    "f1": 0.868,
                    "tp": 444,
                    "fp": 9,
                    "fn": 126,
                    "nodes_before": 2187,
                    "nodes_after": 1214,
                    "ungraded_merges": 1656,
                }
            },
        },
        "gate": {
            "ok": False,
            "passed": 11,
            "total": 13,
            "failed_names": ["person_resolution_recall"],
        },
    }


def resolve() -> dict[str, Any]:
    """`resolve.json` without the summary `index.json` normally carries — the fallback path."""
    return {
        "step": "resolve",
        "generated_at": AT,
        "sha": SHA,
        "eval": {
            "note": "gold pairs only",
            "entity": {
                "gold_pairs": 100,
                "through_tier_1": {"precision": 1.0, "recall": 0.98, "f1": 0.9899, "tp": 49},
            },
        },
    }


def retrieve() -> dict[str, Any]:
    return {
        "step": "plan2-task1-retrieval",
        "generated_at": AT,
        "sha": SHA,
        "rerank": {
            "model": "BAAI/bge-reranker-v2-m3",
            "available": True,
            "note": "measured, not improved",
            "summary": {
                "questions": 19,
                "top1_changed": 19,
                "top1_changed_pct": 100.0,
                "top1_source_changed": 19,
                "mean_top5_overlap": 2.84,
                "latency_without_rerank": {"p50_ms": 167},
                "latency_with_rerank": {"p50_ms": 1772},
                "rerank_cost_p50_ms": 1605,
            },
        },
        "guard": {
            "mode": "live",
            "blocked": {"cases": 56, "refused": 56, "refused_pct": 100.0, "leaked": []},
            "allowed": {"cases": 21, "passed": 21, "limit_injected": 9},
            "timeout": {"elapsed_ms": 1486, "refused": True},
        },
        "sections": _stamps(["rerank", "guard"]),
    }


def eval_questions() -> dict[str, Any]:
    return {
        "step": "eval.questions",
        "generated_at": AT,
        "sha": SHA,
        "merge": {
            "questions_path": "data/eval/questions.jsonl",
            "sha256": "c" * 64,
            "complete": True,
            "target": {"hebrew_floor": 11},
            "totals": {"questions": 32, "target": 32},
            "counts": {
                "by_type": {
                    "traceability": 7,
                    "impact": 7,
                    "rationale": 6,
                    "global": 6,
                    "temporal": 6,
                },
                "by_lang": {"he": 11, "en": 21},
                "by_gold_source": {"graph": 32},
                "by_origin": {"competency": 19, "forged": 13},
            },
            "checks": [{"name": "question count", "ok": True, "detail": "32 of 32"}],
        },
    }


# ----------------------------------------------------------------------------- layer 2


def _cell(**over: Any) -> dict[str, Any]:
    base = {
        "n": 7,
        "ok": 7,
        "na": 0,
        "na_reasons": {},
        "scored": 7,
        "pending": 0,
        "recall": 0.30,
        "recall_strict": 0.28,
        "precision": 0.16,
        "hit_at": {"1": 0.5, "3": 0.7, "5": 0.8, "10": 1.0},
        "items_p50": 10,
        "latency_p50_ms": 120,
        "latency_p95_ms": 300,
        "context_tokens_p50": 3000,
        "context_tokens_total": 21000,
        "cypher_total": 0,
        "cypher_mean": 0.0,
    }
    return {**base, **over}


def eval_retrieval(*, sha: str = SHA) -> dict[str, Any]:
    """Two strategies (baseline + a graph one) over two types — enough for every table."""
    matrix = {
        "s1r": {
            "traceability": _cell(recall=0.30, latency_p50_ms=170, context_tokens_p50=3000),
            "rationale": _cell(recall=0.40, latency_p50_ms=160, context_tokens_p50=2900),
        },
        "s3": {
            "traceability": _cell(
                recall=0.61, latency_p50_ms=208, context_tokens_p50=4200, cypher_total=14
            ),
            "rationale": _cell(
                recall=0.35,
                latency_p50_ms=150,
                context_tokens_p50=2500,
                cypher_total=7,
                na=1,
                ok=6,
                scored=6,
                na_reasons={"no anchor key": 1},
            ),
        },
    }
    return {
        "step": "eval.run.fixed",
        "generated_at": AT,
        "sha": sha,
        "mode": "fixed",
        "baseline": "s1r",
        "strategies": ["s1r", "s3"],
        "types": ["traceability", "rationale"],
        "k": 10,
        "budget_tokens": 4000,
        "questions_total": 32,
        "match_kinds": ["key", "chunk", "parent"],
        "strict_match_kinds": ["key", "chunk"],
        "matrix": matrix,
        "by_strategy": {
            "s1r": {
                "n": 14,
                "ok": 14,
                "na": 0,
                "na_reasons": {},
                "recall": 0.35,
                "cypher_total": 0,
            },
            "s3": {
                "n": 14,
                "ok": 13,
                "na": 1,
                "na_reasons": {"no anchor key": 1},
                "recall": 0.48,
                "cypher_total": 21,
            },
        },
        "by_type": {"traceability": {"n": 7}, "rationale": {"n": 7}},
        "cost": {
            "s1r": {
                "runs": 14,
                "na": 0,
                "latency_p50_ms": 170,
                "latency_p95_ms": 340,
                "context_tokens_p50": 3000,
                "context_tokens_total": 42000,
                "tool_calls": 14,
                "cypher_total": 0,
                "cypher_p50": 0,
                "agent_time_ms": None,
            },
            "s3": {
                "runs": 13,
                "na": 1,
                "latency_p50_ms": 208,
                "latency_p95_ms": 410,
                "context_tokens_p50": 4200,
                "context_tokens_total": 54600,
                "tool_calls": 13,
                "cypher_total": 21,
                "cypher_p50": 2,
                "agent_time_ms": None,
            },
        },
        "cross_lingual": [
            {
                "pair": "tests",
                "strategies": {
                    "s3": {
                        "en": {"qid": "cq01", "recall": 0.5, "items": 7},
                        "he": {"qid": "cq16", "recall": 0.5, "items": 7},
                        "shared_keys": ["KAFKA-1"],
                        "key_jaccard": 1.0,
                    }
                },
            }
        ],
        "coverage": {"expected": 32, "present": 32, "missing": [], "complete": True},
        "notes": ["recall counts a chunk of the gold node as a hit"],
        "sections": _stamps(["matrix", "cost", "cross_lingual"], sha=sha),
    }


# ----------------------------------------------------------------------------- layer 3


def _judgment(label: str, shard: str, qid: str, strategy: str, qtype: str, scores) -> Any:
    return judge_report.Judgment(
        label=label,
        shard=shard,
        batch=f"{shard}/001",
        qid=qid,
        strategy=strategy,
        lang="en",
        qtype=qtype,
        scores=dict(zip(judge_report.METRICS, scores, strict=True)),
    )


def judge_sections() -> dict[str, Any]:
    """`metrics`/`pairwise`/`agreement`/`citation_*` exactly as `run_merge` composes them."""
    judgments = [
        _judgment("L1", "shard-01", "cq01", "s1r", "traceability", (2, 1, 2, 2)),
        _judgment("L2", "shard-01", "cq01", "s3", "traceability", (2, 2, 2, 2)),
        _judgment("L3", "shard-01", "cq02", "s1r", "rationale", (1, 1, 1, 2)),
        _judgment("L4", "shard-01", "cq02", "s3", "rationale", (2, 1, 2, 1)),
        _judgment("L5", "shard-01", "cq03", "agentic", "traceability", (None, 2, 2, 2)),
        # The overlap: the same case scored by the second judge, which is what `agreement`
        # compares. One metric differs by one, which is the interesting case.
        _judgment("L2", "shard-02", "cq01", "s3", "traceability", (1, 2, 2, 2)),
    ]
    pairs = [
        judge_report.PairVerdict(
            label="P1",
            shard="shard-01",
            batch="shard-01/001",
            qid="cq01",
            baseline="s1r",
            challenger="s3",
            winner="A",
            winner_strategy="s3",
        ),
        judge_report.PairVerdict(
            label="P2",
            shard="shard-01",
            batch="shard-01/001",
            qid="cq02",
            baseline="s1r",
            challenger="s3",
            winner="B",
            winner_strategy="s1r",
        ),
    ]
    scored = list(judge_report.case_scores(judgments).values())
    code = {
        "cq01.s1r": {
            "citations": 2,
            "not_in_context": [],
            "not_in_graph": [],
            "context_checked": True,
            "graph_checked": True,
            "code_valid": True,
            "reason": "every citation is in the context and resolves in the graph",
        },
        "cq02.s3": {
            "citations": 1,
            "not_in_context": ["KAFKA-999"],
            "not_in_graph": [],
            "context_checked": True,
            "graph_checked": True,
            "code_valid": False,
            "reason": "not in the context: KAFKA-999",
        },
    }
    answers = [{"case_id": case_id} for case_id in code]
    return {
        "judge_merge": {
            "step": "eval.judge.merge",
            "generated_at": AT,
            "sha": SHA,
            "metrics": list(judge_report.METRICS),
            "batches": {"planned": 4, "with_output": 4, "missing": [], "failed": []},
            "judgments": {
                "total": len(judgments),
                "cases": len(scored),
                "pairs": len(pairs),
                "rejected": 0,
                "unjudged_labels": [],
                "with_problems": [],
                "problem_count": 0,
            },
        },
        "metrics": {
            "by_strategy": {
                name: judge_report.summarise(group)
                for name, group in sorted(judge_report.group_by(scored, "strategy").items())
            },
            "by_type": {
                name: judge_report.summarise(group)
                for name, group in sorted(judge_report.group_by(scored, "type").items())
            },
            "by_lang": {
                name: judge_report.summarise(group)
                for name, group in sorted(judge_report.group_by(scored, "lang").items())
            },
            "matrix": judge_report.matrix(scored),
            "note": "Layer 3 of spec §5.2.",
        },
        "pairwise": judge_report.pairwise_table(pairs),
        "agreement": judge_report.agreement(judgments),
        "citation_crosscheck": judge_report.crosscheck(scored, answers, code),
        "citation_code_check": code,
    }


def eval_answers(*, judged: bool = True, sha: str = SHA) -> dict[str, Any]:
    report: dict[str, Any] = {
        "step": "eval.answers",
        "generated_at": AT,
        "sha": sha,
        "answers_build": {
            "generated_at": AT,
            "sha": sha,
            "mode": "fixed",
            "totals": {"batches": 43, "cases": 184, "skipped": 40},
        },
        "answers_merge": {
            "generated_at": AT,
            "sha": sha,
            "batches": {"planned": 43, "with_output": 43, "missing": [], "failed": []},
            "answers": {"accepted": 184, "rejected": 0, "unanswered_cases": []},
            "complete": True,
        },
    }
    if judged:
        report.update(judge_sections())
    report["sections"] = _stamps(sorted(report), sha=sha)
    return report


def plan2_gate() -> dict[str, Any]:
    return {
        "step": "plan2-gate",
        "generated_at": AT,
        "sha": SHA,
        "totals": {
            "questions": 19,
            "answered": 19,
            "citations_found": 154,
            "citations_valid": 150,
            "citations_invalid": 4,
            "validity_rate": 97.4,
            "with_at_least_one_valid_citation": 19,
        },
        "gate": {"ok": True, "passed": 6, "total": 6, "criteria": []},
    }


# ------------------------------------------------------------------------- incremental


def incremental(*, complete: bool = True) -> dict[str, Any]:
    steps = ["harvest", "canon", "load"] if not complete else None
    from brain.harvest.incremental import PIPELINE_STEPS

    names = steps or list(PIPELINE_STEPS)
    return {
        "step": "incremental",
        "at": AT,
        "started_at": AT,
        "slice": "incremental",
        "source": "jira",
        "since": "2026-01-01",
        "keys": [f"KAFKA-{20000 + i}" for i in range(10)],
        "duration_s": 412.5,
        "steps": [
            {
                "step": name,
                "command": f"brain {name}",
                "started_at": AT,
                "duration_s": 12.5,
                "report": {},
                "notes": "",
            }
            for name in names
        ],
        "graph": {"workitems_added": 10, "edges_added": 34},
        "chunks": {"added": 41},
        "entities": {"added": 18},
        "communities": {"member_hash_changed": 7, "recomputed": False},
        "questions": [{"qid": f"inc{i}", "citation_valid": i < 4, "note": ""} for i in range(5)],
        "warnings": [],
        "errors": [],
    }


# ----------------------------------------------------------------------------- writing


BUILDERS = {
    "harvest": harvest,
    "index": index,
    "resolve": resolve,
    "retrieve": retrieve,
    "eval_questions": eval_questions,
    "eval_retrieval": eval_retrieval,
    "eval_answers": eval_answers,
    "plan2_gate": plan2_gate,
    "incremental": incremental,
}
FILENAMES = {
    "harvest": "harvest.json",
    "index": "index.json",
    "resolve": "resolve.json",
    "retrieve": "retrieve.json",
    "eval_questions": "eval_questions.json",
    "eval_retrieval": "eval_retrieval.json",
    "eval_answers": "eval_answers.json",
    "plan2_gate": "plan2_gate.json",
    "incremental": "incremental.json",
}


def write_reports(root: Path, *, only=None, **overrides: Any) -> Path:
    """Write the chosen inputs under `<root>/reports`; `only=()` writes an empty directory."""
    reports = Path(root) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    keys = list(BUILDERS) if only is None else list(only)
    for key in keys:
        data = overrides.get(key)
        if data is None:
            data = BUILDERS[key]()
        (reports / FILENAMES[key]).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return reports


def write_agentic(root: Path, qids=("cq01", "cq02")) -> Path:
    """Mode B's merged answers — the files that turn the mode B column on."""
    directory = Path(root) / "eval" / "answers" / "fixed"
    directory.mkdir(parents=True, exist_ok=True)
    for qid in qids:
        (directory / f"{qid}.agentic.json").write_text(
            json.dumps({"case_id": f"{qid}.agentic", "qid": qid, "strategy": "agentic"}),
            encoding="utf-8",
        )
    return directory
