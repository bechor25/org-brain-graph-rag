"""The Plan 1 exit gate: every roadmap criterion, its measured value, PASS or FAIL.

The criteria are copied from `docs/superpowers/plans/2026-09-03-plan1-corpus-to-graph.md`
Task 9 ("Plan 1 exit gate"), which is what the step brief cites. The roadmap's own summary
table carries an older draft of two of the thresholds (≥1,500 issues, ≥80 KIPs), written
before the probe measured the corpus at 1,416; both numbers are reported side by side
under `roadmap_table_variant` rather than one being quietly picked.

Two rules this module exists to enforce.

**Nothing is relaxed to make the table green.** Person recall is 0.744 against a target of
0.85 and the planner has accepted that in writing (`docs/planning/progress.md`, "הכרעות
מתכנן — resolve"). It is still a FAIL here, with the acceptance recorded in `note`. A gate
that prints PASS because someone decided the number was good enough is not a gate.

**Every criterion prints its value**, including the ones that pass and the ones that cannot
be evaluated yet. `UNKNOWN` (a report that has not been written, a `make smoke` that has
not been recorded) counts as a failure, because the gate's question is "is this proven",
not "has anyone disproved it".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Lessons the gate expects on disk, by number.
REQUIRED_LESSONS: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8, 9)

MIN_ISSUES = 1400
MIN_KIPS_EMBEDDED = 190
MIN_COMMITS = 4000
RESOLUTION_TARGET = 0.85

#: The roadmap summary table's earlier draft of two thresholds, kept for the record.
ROADMAP_TABLE_VARIANT = {"issues": 1500, "kips": 80}


@dataclass
class Criterion:
    name: str
    requirement: str
    value: Any
    ok: bool
    note: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "requirement": self.requirement,
            "value": self.value,
            "status": "PASS" if self.ok else "FAIL",
            "ok": self.ok,
            "note": self.note,
            **({"detail": self.detail} if self.detail else {}),
        }


def _num(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def corpus_criteria(corpus: dict[str, Any]) -> list[Criterion]:
    issues = corpus.get("real_issues")
    kips = corpus.get("kips_embedded")
    commits = corpus.get("commits")
    referenced = corpus.get("kips_referenced")
    reachable = corpus.get("kips_referenced_embedded")
    return [
        Criterion(
            "issues",
            f">= {MIN_ISSUES} real Jira issues",
            issues,
            _num(issues) is not None and issues >= MIN_ISSUES,
            note=(
                f"the roadmap summary table asks for >= {ROADMAP_TABLE_VARIANT['issues']}, a "
                "pre-probe draft; the probe measured the whole 2023-2025 three-component "
                "slice at 1,416, so Task 9 lowered it to 1,400. Against the table's number "
                f"this would be {'PASS' if _num(issues) and issues >= 1500 else 'FAIL'}."
            ),
            detail={"synthetic_workitems": corpus.get("synthetic_workitems")},
        ),
        Criterion(
            "kips_embedded",
            f">= {MIN_KIPS_EMBEDDED} KIP documents with embedded chunks",
            kips,
            _num(kips) is not None and kips >= MIN_KIPS_EMBEDDED,
            note=(
                "Phase A embeds the KIPs the harvested slice actually references, not all "
                f"{corpus.get('kip_documents')} harvested pages."
            ),
            detail={"kip_documents_total": corpus.get("kip_documents")},
        ),
        Criterion(
            "kips_referenced_all_embedded",
            "every referenced KIP that exists in the corpus is embedded",
            f"{reachable}/{referenced}",
            referenced is not None and reachable == referenced,
            note="the 'all referenced' half of the Task 9 criterion.",
        ),
        Criterion(
            "commits",
            f">= {MIN_COMMITS} commits",
            commits,
            _num(commits) is not None and commits >= MIN_COMMITS,
            detail={"commits_with_an_issue_key_chunked": corpus.get("commits_keyed")},
        ),
    ]


def resolution_criteria(resolution: dict[str, Any]) -> list[Criterion]:
    if not resolution.get("available"):
        return [
            Criterion(
                f"{kind}_resolution_{metric}",
                f">= {RESOLUTION_TARGET}",
                "UNKNOWN",
                False,
                note=f"{resolution.get('source')} is missing — `brain resolve eval` has not run",
            )
            for kind in ("person", "entity")
            for metric in ("precision", "recall")
        ]
    out: list[Criterion] = []
    for kind in ("person", "entity"):
        scores = (resolution.get("kinds") or {}).get(kind) or {}
        for metric in ("precision", "recall"):
            value = scores.get(metric)
            note = ""
            if kind == "person" and metric == "recall":
                note = (
                    "known and accepted by the planner (progress.md, 'הכרעות מתכנן — "
                    "resolve'): the road to 0.85 is a wider adjudication band (~3,133 "
                    "pairs, ~78 more batches), not a better judge. Reported as FAIL "
                    "because the criterion is the criterion."
                )
            out.append(
                Criterion(
                    f"{kind}_resolution_{metric}",
                    f">= {RESOLUTION_TARGET}",
                    value,
                    _num(value) is not None and value >= RESOLUTION_TARGET,
                    note=note,
                    detail={
                        "gold_pairs": scores.get("gold_pairs"),
                        "tp": scores.get("tp"),
                        "fp": scores.get("fp"),
                        "fn": scores.get("fn"),
                    },
                )
            )
    return out


def graph_criteria(provenance: dict[str, Any], indexes: dict[str, Any]) -> list[Criterion]:
    missing = (provenance.get("edges") or {}).get("missing")
    offline = indexes.get("not_online") or {}
    unpopulated = indexes.get("not_populated") or {}
    return [
        Criterion(
            "llm_edges_without_provenance",
            "== 0",
            missing,
            missing == 0,
            note=(
                "counted over "
                f"{(provenance.get('edges') or {}).get('total')} LLM-derived edges plus the "
                "adjudicated SAME_AS links, read back from the graph."
            ),
            detail={"by_type": (provenance.get("edges") or {}).get("by_type")},
        ),
        Criterion(
            "all_indexes_online",
            "every managed index ONLINE",
            f"{indexes.get('online')}/{indexes.get('managed')} ONLINE",
            not offline,
            note="" if not offline else f"not ONLINE: {offline}",
            detail={"below_100_percent": unpopulated} if unpopulated else {},
        ),
    ]


def process_criteria(*, docs_dir: Path, smoke: dict[str, Any] | None) -> list[Criterion]:
    lessons = lesson_status(docs_dir / "lessons")
    progress = progress_status(docs_dir / "planning" / "progress.md")
    smoke = smoke or {}
    smoke_ok = bool(smoke.get("ok"))
    return [
        Criterion(
            "make_smoke_green",
            "`make smoke` exits 0",
            ("PASS" if smoke_ok else smoke.get("status", "NOT RECORDED")),
            smoke_ok,
            note=(
                f"recorded {smoke.get('at')} in {smoke.get('duration_s')}s"
                if smoke
                else "run `brain index --smoke` (or `make smoke`, then re-run with --smoke) "
                "so the gate has a measured value instead of an assertion."
            ),
            detail={k: v for k, v in smoke.items() if k in ("exit_code", "command", "tail")},
        ),
        Criterion(
            "lessons_01_09",
            "docs/lessons/01..09 present",
            f"{len(lessons['present'])}/{len(REQUIRED_LESSONS)}",
            not lessons["missing"],
            note="" if not lessons["missing"] else f"missing: {lessons['missing']}",
            detail={"files": lessons["files"]},
        ),
        Criterion(
            "progress_complete",
            "every Plan 1 row in progress.md is done",
            f"{progress['done']}/{progress['rows']} rows done",
            progress["rows"] > 0 and not progress["open"],
            note="" if not progress["open"] else f"still open: {progress['open']}",
        ),
    ]


def lesson_status(lessons_dir: Path) -> dict[str, Any]:
    """Which of lessons 01-09 exist. A lesson is a file `NN-<name>.md`."""
    files = sorted(p.name for p in lessons_dir.glob("*.md")) if lessons_dir.is_dir() else []
    numbers = {int(m.group(1)) for name in files if (m := re.match(r"^(\d{2})-", name))}
    return {
        "dir": str(lessons_dir),
        "files": files,
        "present": sorted(n for n in REQUIRED_LESSONS if n in numbers),
        "missing": sorted(n for n in REQUIRED_LESSONS if n not in numbers),
    }


#: A progress row is done when its status cell holds a green check and nothing else.
_DONE = "✅"
_OPEN_MARKS = ("⬜", "🟡", "🔴", "❌")


def _short(status: str, limit: int = 40) -> str:
    text = " ".join(status.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def progress_status(path: Path) -> dict[str, Any]:
    """Plan 1 rows in `docs/planning/progress.md`, and which of them are still open.

    Parsed rather than trusted: the table is the project's source of truth for "where are
    we", and a gate that took its word for it would pass on a row nobody filled in.
    """
    if not path.is_file():
        return {"available": False, "rows": 0, "done": 0, "open": ["progress.md is missing"]}
    rows_open: list[str] = []
    rows = 0
    done = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] != "1":
            continue
        rows += 1
        # The status cell of a real row runs to hundreds of characters of Hebrew findings.
        # The gate needs the marker and the step, not the essay.
        step, status = cells[1][:40], _short(cells[2])
        if any(mark in cells[2] for mark in _OPEN_MARKS):
            rows_open.append(f"{step} [{status}]")
        elif _DONE in cells[2]:
            done += 1
        else:
            rows_open.append(f"{step} [{status or 'empty'}]")
    return {"available": True, "path": str(path), "rows": rows, "done": done, "open": rows_open}


def evaluate(
    *,
    corpus: dict[str, Any],
    resolution: dict[str, Any],
    provenance: dict[str, Any],
    indexes: dict[str, Any],
    docs_dir: Path,
    smoke: dict[str, Any] | None = None,
) -> dict[str, Any]:
    criteria = [
        *corpus_criteria(corpus),
        *resolution_criteria(resolution),
        *graph_criteria(provenance, indexes),
        *process_criteria(docs_dir=docs_dir, smoke=smoke),
    ]
    failed = [c.name for c in criteria if not c.ok]
    return {
        "source": (
            "docs/superpowers/plans/2026-09-03-plan1-corpus-to-graph.md, Task 9 'Plan 1 exit gate'"
        ),
        "roadmap_table_variant": ROADMAP_TABLE_VARIANT,
        "criteria": [c.as_dict() for c in criteria],
        "total": len(criteria),
        "passed": len(criteria) - len(failed),
        "failed": len(failed),
        "failed_names": failed,
        "ok": not failed,
    }


def render_table(gate: dict[str, Any]) -> str:
    """The `--check-gate` console table. Fixed width so it reads in a terminal log."""
    rows = gate["criteria"]
    width = max((len(r["name"]) for r in rows), default=10)
    lines = [
        f"{'criterion'.ljust(width)}  {'requirement'.ljust(38)}  {'value'.ljust(22)}  status",
        f"{'-' * width}  {'-' * 38}  {'-' * 22}  ------",
    ]
    for r in rows:
        lines.append(
            f"{r['name'].ljust(width)}  {str(r['requirement'])[:38].ljust(38)}  "
            f"{str(r['value'])[:22].ljust(22)}  {r['status']}"
        )
    lines.append("")
    lines.append(
        f"{gate['passed']}/{gate['total']} PASS"
        + (f" — FAIL: {', '.join(gate['failed_names'])}" if gate["failed_names"] else "")
    )
    for r in rows:
        if not r["ok"] and r.get("note"):
            lines.append(f"  ! {r['name']}: {r['note']}")
    return "\n".join(lines)
