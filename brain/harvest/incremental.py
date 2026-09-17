"""The incremental slice: the read-only probe, and the shape of the run's report.

Spec §3.9 and §5.5, Plan 3 decision 7. Two things live here, and they are two halves of
one question — *what does the base slice not have, and what does adding it cost?*

:func:`probe_incremental`
    A network **read**. It asks Jira how many issues exist outside the harvested window
    and shows the ten oldest of them, so the run that follows is planned against real
    numbers instead of an assumption. It writes nothing but
    ``data/reports/incremental_probe.json`` and touches neither the graph nor
    ``data/raw/``. It is the same JQL the harvest will use — that is the point of a probe:
    "what will the next command fetch", answered before the next command runs.

    Why *oldest*: the newest issues change every day, so a pull ordered by `created DESC`
    is a different ten each morning and the measurement is unrepeatable. The oldest issues
    created after the slice ended are a fixed set, and stay fixed.

:class:`IncrementalRun`
    The writer for ``data/reports/incremental.json``, which the run fills step by step:
    a row per pipeline step with its duration and its own report, what the graph gained,
    which communities changed, and the five questions asked afterwards. It is written
    after **every** step, not at the end, so a run that dies at `extract merge` still
    leaves behind the four steps that succeeded and their timings.

Placement: this is a harvest module because the probe is a connector call and the slice is
a harvest concept (`data/raw/<source>/since-<date>/`). The report it writes spans the whole
pipeline, and the alternative — a module per step writing its own slice of one file — is
what makes a report nobody can validate.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from brain.canon.models import INCREMENTAL_SLICE
from brain.harvest.base import HarvestError, utc_now_iso, write_json_atomic
from brain.harvest.jira import (
    SEARCH_PATH,
    JiraConnector,
    build_jql,
    new_since_field,
    partition_window,
)

PROBE_REPORT_NAME = "incremental_probe.json"
REPORT_NAME = "incremental.json"

#: How many of the oldest issues the probe lists, and how many the run harvests
#: (spec §5.5: "add 10 items outside the slice").
SAMPLE = 10

#: Enough to answer "is this issue worth taking" without pulling `*all` for 10 issues.
PROBE_FIELDS = "key,created,updated,components,issuetype,status,summary,assignee"

#: The steps `data/reports/incremental.json` expects, in order. Named here rather than
#: discovered, so a report missing one is a **failure of the run**, visible in the file,
#: and not a step everybody forgot was supposed to happen.
PIPELINE_STEPS: tuple[str, ...] = (
    "harvest",
    "canon",
    "load",
    "chunk",
    "extract build",
    "extract merge",
    "resolve tier 1",
    "communities build",
    "communities batches",
    "communities merge",
    "index",
)

#: Spec §5.5 asks for five questions about the new items. The number is part of the
#: contract, so the validator checks it rather than trusting the runbook.
QUESTIONS = 5


# --------------------------------------------------------------------------- probe


def _components(issue: dict[str, Any]) -> list[str]:
    fields = issue.get("fields") or {}
    return [str(c.get("name") or "") for c in fields.get("components") or [] if c.get("name")]


def _changelog_entries(issue: dict[str, Any]) -> int:
    """How much history this issue has — `total` when Jira gives it, else the rows.

    `expand=changelog` returns `{"startAt", "maxResults", "total", "histories"}`; `total`
    is the honest count even when the page truncated the list.
    """
    changelog = issue.get("changelog") or {}
    total = changelog.get("total")
    if isinstance(total, int):
        return total
    return len(changelog.get("histories") or [])


def summarize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """One row of the probe's `oldest` table."""
    fields = issue.get("fields") or {}
    entries = _changelog_entries(issue)
    return {
        "key": str(issue.get("key") or ""),
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "components": _components(issue),
        "type": str((fields.get("issuetype") or {}).get("name") or ""),
        "status": str((fields.get("status") or {}).get("name") or ""),
        "summary": str(fields.get("summary") or ""),
        "has_changelog": entries > 0,
        "changelog_entries": entries,
    }


def probe_incremental(
    connector: JiraConnector,
    since: date,
    *,
    sample: int = SAMPLE,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Two read-only requests: how many issues are outside the slice, and the oldest N.

    The first asks with ``maxResults=0`` — Jira answers with `total` and no issues, which
    is the cheapest question there is. The second takes the `sample` oldest, with the
    changelog expanded, because "does this issue have history" decides whether the
    increment can exercise `StatusChange` and `ASSIGNED_TO` at all.
    """
    source = connector.source
    jql = build_jql(source, since, new=True)
    field = new_since_field(source)
    _kept, dropped = partition_window(source, field)

    counted = connector.http.get_json(SEARCH_PATH, {"jql": jql, "maxResults": 0, "fields": "key"})
    total = counted.get("total")
    echo(f"[{connector.name}] {jql}")
    echo(f"[{connector.name}] {total} issue(s) match — asking for the {sample} oldest")

    page = connector.http.get_json(
        SEARCH_PATH,
        {
            "jql": jql,
            "startAt": 0,
            "maxResults": sample,
            "fields": PROBE_FIELDS,
            "expand": "changelog",
        },
    )
    issues = page.get("issues") or []
    oldest = [summarize_issue(issue) for issue in issues]

    return {
        "step": "incremental_probe",
        "at": utc_now_iso(),
        "source": connector.name,
        "base_url": source.base_url,
        "since": since.isoformat(),
        "since_field": field,
        "jql": jql,
        "base_query": source.query,
        # What the incremental window took off the base query, so a reader can see the
        # rewrite rather than diff two JQL strings.
        "dropped_clauses": dropped,
        "total": total,
        "sample": len(oldest),
        "oldest": oldest,
        "with_changelog": sum(1 for row in oldest if row["has_changelog"]),
        "components": sorted({c for row in oldest for c in row["components"]}),
        "harvest_command": (
            f"uv run brain harvest --source {connector.name} --since {since.isoformat()} "
            f"--limit {sample} --slice incremental"
        ),
        "http": {"calls": connector.http.calls, "retries": connector.http.retries},
        "errors": connector.http.errors,
        "read_only": "two GETs; nothing was written to data/raw/ or to the graph",
    }


def write_probe(reports_dir: Path, report: dict[str, Any]) -> Path:
    path = Path(reports_dir) / PROBE_REPORT_NAME
    write_json_atomic(path, report)
    return path


def summarize_probe(report: dict[str, Any]) -> str:
    lines = [
        f"incremental probe — {report['total']} issue(s) created on/after "
        f"{report['since']} in the slice's components",
        f"  jql        {report['jql']}",
        f"  dropped    {', '.join(report['dropped_clauses']) or '(nothing)'}",
        f"  oldest {report['sample']:<4} {report['with_changelog']} of them carry a changelog",
    ]
    for row in report["oldest"]:
        lines.append(
            f"    {row['key']:<12} {str(row['created'])[:10]}  "
            f"{','.join(row['components']) or '-':<24} "
            f"changelog={'yes' if row['has_changelog'] else 'no':<3} "
            f"({row['changelog_entries']})"
        )
    lines.append(f"  next       {report['harvest_command']}")
    return "\n".join(lines)


def run_probe(
    *,
    source: str,
    since: date,
    raw_dir: Path,
    reports_dir: Path,
    sample: int = SAMPLE,
    connector: JiraConnector | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """`brain harvest --probe-since` — the whole command. Returns (report, exit code).

    `raw_dir` is handed to the connector because that is its constructor's contract; the
    probe never writes into it.
    """
    from brain.harvest.registry import get_registry

    conn = connector or JiraConnector(raw_dir, source=get_registry().source(source), new_slice=True)
    try:
        report = probe_incremental(conn, since, sample=sample, echo=echo)
    finally:
        close = getattr(getattr(conn, "http", None), "close", None)
        if close:
            close()
    path = write_probe(reports_dir, report)
    echo(summarize_probe(report))
    echo(f"report: {path}")
    return report, (0 if report.get("total") else 1)


# --------------------------------------------------------------------------- run report


@dataclass
class StepRecord:
    """One pipeline step of the incremental run."""

    step: str
    command: str = ""
    duration_s: float = 0.0
    started_at: str = ""
    report: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    #: The commit this step was measured on. Per step, not only per run: the eleven steps
    #: span days and two agent dispatches, and a duration that cannot name its tree is a
    #: number nobody can reproduce (conventions, Plan 1 close-out).
    sha: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "command": self.command,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 2),
            "sha": self.sha,
            "report": self.report,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepRecord:
        return cls(
            step=str(data.get("step") or ""),
            command=str(data.get("command") or ""),
            duration_s=float(data.get("duration_s") or 0.0),
            started_at=str(data.get("started_at") or ""),
            report=dict(data.get("report") or {}),
            notes=str(data.get("notes") or ""),
            sha=str(data.get("sha") or ""),
        )


@dataclass
class IncrementalRun:
    """`data/reports/incremental.json`, filled one step at a time.

    Every setter writes the file, because the value of this report is the *timing per
    step* and a report written only at the end has no timings for the run that failed.
    """

    reports_dir: Path
    since: str
    source: str = "jira"
    slice: str = INCREMENTAL_SLICE
    #: `git rev-parse --short HEAD` when the run started. Every step row copies it.
    sha: str = ""
    keys: list[str] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    graph: dict[str, Any] = field(default_factory=dict)
    chunks: dict[str, Any] = field(default_factory=dict)
    entities: dict[str, Any] = field(default_factory=dict)
    communities: dict[str, Any] = field(default_factory=dict)
    questions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now_iso)
    _clock: Callable[[], float] = time.perf_counter

    # -- filling -----------------------------------------------------------

    def record(
        self,
        step: str,
        *,
        command: str = "",
        duration_s: float = 0.0,
        report: dict[str, Any] | None = None,
        notes: str = "",
    ) -> StepRecord:
        """Add (or replace) one step's row and rewrite the report."""
        row = StepRecord(
            step=step,
            command=command,
            duration_s=duration_s,
            started_at=utc_now_iso(),
            report=dict(report or {}),
            notes=notes,
            sha=self.sha,
        )
        self.steps = [s for s in self.steps if s.step != step] + [row]
        self.write()
        return row

    def timed(self, step: str, *, command: str = "") -> Any:
        """`with run.timed("load", command=...) as row: row.report = ...` — times the body."""
        return _Timed(self, step, command)

    def set_graph(self, **values: Any) -> None:
        self.graph.update(values)
        self.write()

    def set_chunks(self, **values: Any) -> None:
        self.chunks.update(values)
        self.write()

    def set_entities(self, **values: Any) -> None:
        self.entities.update(values)
        self.write()

    def set_communities(self, **values: Any) -> None:
        self.communities.update(values)
        self.write()

    def add_question(self, **values: Any) -> None:
        self.questions.append(dict(values))
        self.write()

    # -- output ------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        # `at` is what the harvest family of reports calls the write time and `generated_at`
        # is what every other data/reports/*.json calls it. Same instant, both names, so
        # neither a reader nor a validator of either family has to know which file this is.
        now = utc_now_iso()
        return {
            "step": "incremental",
            "at": now,
            "generated_at": now,
            "started_at": self.started_at,
            "sha": self.sha,
            "slice": self.slice,
            "source": self.source,
            "since": self.since,
            "keys": list(self.keys),
            "steps": [s.as_dict() for s in self.steps],
            "duration_s": round(sum(s.duration_s for s in self.steps), 2),
            "graph": self.graph,
            "chunks": self.chunks,
            "entities": self.entities,
            "communities": self.communities,
            "questions": self.questions,
            "warnings": self.warnings,
            "errors": self.errors,
        }

    def write(self) -> Path:
        path = Path(self.reports_dir) / REPORT_NAME
        write_json_atomic(path, self.as_dict())
        return path

    # -- resuming ----------------------------------------------------------

    @classmethod
    def load(cls, reports_dir: Path, **defaults: Any) -> IncrementalRun:
        """Pick the run back up from `incremental.json`, or start one if it is not there.

        The eleven steps do not fit in one process: an agent runs harvest…extract build,
        the extractor agent runs, and a second dispatch finishes the pipeline. A second
        run object that started empty would `write()` a file without the first five steps'
        timings — the report's whole point. `defaults` fills the fields of a fresh run
        (`since`, `sha`, …) and is ignored when a report is already on disk.
        """
        import json

        path = Path(reports_dir) / REPORT_NAME
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if not isinstance(data, dict):
            return cls(
                reports_dir=Path(reports_dir), since=str(defaults.pop("since", "")), **defaults
            )
        run = cls(
            reports_dir=Path(reports_dir),
            since=str(data.get("since") or defaults.get("since") or ""),
            source=str(data.get("source") or defaults.get("source") or "jira"),
            slice=str(data.get("slice") or defaults.get("slice") or INCREMENTAL_SLICE),
            sha=str(data.get("sha") or defaults.get("sha") or ""),
            keys=[str(k) for k in data.get("keys") or []],
            steps=[StepRecord.from_dict(row) for row in data.get("steps") or []],
            graph=dict(data.get("graph") or {}),
            chunks=dict(data.get("chunks") or {}),
            entities=dict(data.get("entities") or {}),
            communities=dict(data.get("communities") or {}),
            questions=[dict(q) for q in data.get("questions") or []],
            warnings=[str(w) for w in data.get("warnings") or []],
            errors=[str(e) for e in data.get("errors") or []],
        )
        # The run started when the *first* dispatch started; a resumed run that restamped
        # it would report a wall-clock that excludes the work it is resuming.
        run.started_at = str(data.get("started_at") or run.started_at)
        return run


class _Timed:
    def __init__(self, run: IncrementalRun, step: str, command: str) -> None:
        self.run = run
        self.step = step
        self.command = command
        self.row = StepRecord(step=step, command=command, started_at=utc_now_iso())
        self._started = 0.0

    def __enter__(self) -> StepRecord:
        self._started = self.run._clock()
        return self.row

    def __exit__(self, *exc: object) -> None:
        self.row.duration_s = self.run._clock() - self._started
        self.run.steps = [s for s in self.run.steps if s.step != self.step] + [self.row]
        self.run.write()


def validate_incremental(report: dict[str, Any], *, expected_keys: int = SAMPLE) -> list[str]:
    """What is missing or wrong in an `incremental.json`. Empty list = the run is complete.

    This is the acceptance check of Plan 3 Task 4 expressed as code: 10 items, every
    pipeline step timed, the community delta stated, five questions asked and at least
    four of them citing something. It is deliberately strict about *presence* — a run that
    did not measure a step must say so in `notes`, not leave the row out.
    """
    problems: list[str] = []
    if report.get("step") != "incremental":
        problems.append("`step` is not 'incremental'")
    if report.get("slice") != "incremental":
        problems.append("`slice` is not 'incremental'")
    if not report.get("since"):
        problems.append("`since` is empty")

    keys = report.get("keys") or []
    if len(keys) != expected_keys:
        problems.append(f"{len(keys)} keys, expected {expected_keys}")
    if len(set(keys)) != len(keys):
        problems.append("`keys` has duplicates — the ledger did not deduplicate")

    seen = {str(s.get("step")) for s in report.get("steps") or []}
    for step in PIPELINE_STEPS:
        if step not in seen:
            problems.append(f"no row for step {step!r}")
    for row in report.get("steps") or []:
        if not isinstance(row.get("duration_s"), int | float):
            problems.append(f"step {row.get('step')!r} has no duration")

    communities = report.get("communities") or {}
    if "member_hash_changed" not in communities:
        problems.append("`communities.member_hash_changed` is missing")

    questions = report.get("questions") or []
    if len(questions) != QUESTIONS:
        problems.append(f"{len(questions)} questions, expected {QUESTIONS}")
    cited = sum(1 for q in questions if q.get("citation_valid"))
    if questions and cited < 4:
        problems.append(f"only {cited} of {len(questions)} answers cite something that resolves")
    return problems


def assert_valid(report: dict[str, Any], *, expected_keys: int = SAMPLE) -> None:
    problems = validate_incremental(report, expected_keys=expected_keys)
    if problems:
        raise HarvestError(
            "data/reports/incremental.json is not a finished run:\n  - " + "\n  - ".join(problems)
        )


def keys_from_probe(probe: dict[str, Any], *, sample: int = SAMPLE) -> Sequence[str]:
    """The issue keys the probe chose — what the run harvests and what the report lists."""
    return [str(row["key"]) for row in (probe.get("oldest") or [])[:sample]]
