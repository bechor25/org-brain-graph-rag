"""The incremental slice at the harvest boundary: the window, the cap, and the probe.

Everything here is offline — respx serves a recorded ASF shape. The one test that really
talks to Jira is `tests/network/test_incremental_probe_network.py`, marked `network` and
deselected by default.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from brain.harvest.base import HttpFetcher
from brain.harvest.incremental import (
    PIPELINE_STEPS,
    QUESTIONS,
    REPORT_NAME,
    SAMPLE,
    IncrementalRun,
    keys_from_probe,
    probe_incremental,
    run_probe,
    summarize_probe,
    validate_incremental,
)
from brain.harvest.jira import (
    JiraConnector,
    build_jql,
    new_since_field,
    partition_window,
    split_and,
)
from brain.harvest.registry import RegistryError
from brain.harvest.runner import build_connector, run_harvest
from tests.harvest_helpers import source

JIRA = source("jira")
BASE_URL = JIRA.base_url
SEARCH = f"{BASE_URL}/rest/api/2/search"
SINCE = date(2026, 1, 1)


# --------------------------------------------------------------------------- fixtures


def make_issue(n: int, *, created: str, component: str = "streams", histories: int = 0) -> dict:
    """A raw issue in the shape `/rest/api/2/search?expand=changelog` returns."""
    return {
        "key": f"KAFKA-{n}",
        "fields": {
            "summary": f"KAFKA-{n} summary",
            "description": "body text",
            "created": created,
            "updated": created,
            "components": [{"name": component}],
            "issuetype": {"name": "Bug"},
            "status": {"name": "Open"},
            "issuelinks": [],
            "assignee": {"name": "mjsax"},
            "fixVersions": [],
            "comment": {"comments": [], "total": 0},
        },
        "changelog": {
            "total": histories,
            "histories": [
                {"id": str(i), "created": created, "items": []} for i in range(histories)
            ],
        },
    }


def paged(total: int, *, first: int = 19000, histories=lambda i: i % 2):
    """Serve `total` issues created in 2026, honouring `startAt`/`maxResults`."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        start = int(params.get("startAt", 0))
        size = int(params.get("maxResults", 50))
        issues = [
            make_issue(
                first + start + i,
                created=f"2026-01-{(start + i) % 28 + 1:02d}T09:00:00.000+0000",
                histories=histories(start + i),
            )
            for i in range(min(size, max(0, total - start)))
        ]
        return httpx.Response(
            200, json={"startAt": start, "maxResults": size, "total": total, "issues": issues}
        )

    return handler


def connector(tmp_path: Path, **kw) -> JiraConnector:
    http = HttpFetcher(BASE_URL, min_interval=0.0, sleep=lambda s: None)
    return JiraConnector(tmp_path, source=JIRA, http=http, **kw)


# --------------------------------------------------------------------------- the window


def test_split_and_does_not_cut_inside_the_component_list():
    assert split_and(JIRA.query) == [
        "project = KAFKA",
        'component in (streams, "connect", clients)',
        'created >= "2023-01-01"',
        'created <= "2025-12-31"',
    ]


def test_split_and_ignores_and_inside_a_quoted_string():
    assert split_and('summary ~ "cats AND dogs" AND project = KAFKA') == [
        'summary ~ "cats AND dogs"',
        "project = KAFKA",
    ]


def test_the_incremental_window_replaces_the_base_slices_dates():
    """The whole point: `created <= "2025-12-31"` would make the catch-up return nothing."""
    jql = build_jql(JIRA, SINCE, new=True)
    assert jql == (
        'project = KAFKA AND component in (streams, "connect", clients) '
        'AND created >= "2026-01-01" ORDER BY created ASC'
    )
    assert '<= "2025-12-31"' not in jql


def test_the_dropped_clauses_are_reported_not_silently_removed():
    _kept, dropped = partition_window(JIRA, new_since_field(JIRA))
    assert dropped == ['created >= "2023-01-01"', 'created <= "2025-12-31"']


def test_plain_since_is_untouched_by_the_new_window():
    """`--since` without `--slice` still means 'what changed inside my slice'."""
    assert build_jql(JIRA, SINCE) == (
        'project = KAFKA AND component in (streams, "connect", clients) '
        'AND created >= "2023-01-01" AND created <= "2025-12-31" '
        'AND updated >= "2026-01-01" ORDER BY created ASC'
    )


def test_a_non_date_upper_bound_is_not_a_window():
    narrow = source("jira", query='project = KAFKA AND priority <= 3 AND created <= "2025-12-31"')
    kept, dropped = partition_window(narrow, "created")
    assert kept == ["project = KAFKA", "priority <= 3"]
    assert dropped == ['created <= "2025-12-31"']


def test_a_query_that_is_all_window_is_refused_rather_than_widened():
    everything = source("jira", query='created >= "2023-01-01" AND created <= "2025-12-31"')
    with pytest.raises(Exception, match="left nothing"):
        build_jql(everything, SINCE, new=True)


def test_the_uncapped_signature_is_unchanged_by_the_new_options(tmp_path):
    """A changed signature does not fail — it silently re-fetches the whole corpus."""
    plain = connector(tmp_path)
    assert plain.signature(None) == JiraConnector(tmp_path, source=JIRA).signature(None)
    assert connector(tmp_path, max_records=10).signature(None) != plain.signature(None)
    assert connector(tmp_path, new_slice=True).signature(SINCE) != plain.signature(SINCE)


# --------------------------------------------------------------------------- --limit


@respx.mock
def test_limit_asks_for_ten_rows_and_stops(tmp_path):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return paged(1200)(request)

    respx.get(SEARCH).mock(side_effect=handler)
    result = connector(tmp_path, max_records=10, new_slice=True).run(SINCE)

    # One request, for ten rows — not a 500-row page filtered down afterwards.
    assert len(seen) == 1
    assert int(seen[0]["maxResults"]) == 10
    assert result.records == 10
    assert result.pages == 1
    # `done`, and honestly so: the cap is in the signature, so this checkpoint belongs to
    # the capped query and nothing will resume it into a full pull.
    assert result.checkpoint["done"] is True
    assert result.checkpoint["total"] == 1200


@respx.mock
def test_the_incremental_pull_lands_in_its_own_directory(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged(50))
    connector(tmp_path, max_records=10, new_slice=True).run(SINCE)

    run_dir = tmp_path / "jira" / "since-2026-01-01"
    assert run_dir.is_dir()
    assert not list((tmp_path / "jira").glob("issues-*.json"))  # the full pull is untouched
    payload = json.loads((run_dir / "issues-0000.json").read_text(encoding="utf-8"))
    assert len(payload["issues"]) == 10
    assert json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))["done"] is True


@respx.mock
def test_rerunning_the_same_capped_pull_fetches_nothing(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged(50))
    connector(tmp_path, max_records=10, new_slice=True).run(SINCE)
    again = connector(tmp_path, max_records=10, new_slice=True).run(SINCE)
    assert (again.records, again.pages) == (0, 0)


@respx.mock
def test_the_report_records_the_slice_and_the_cap(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged(50))
    reports = tmp_path / "reports"
    reports.mkdir()
    report, code = run_harvest(
        ["jira"],
        raw_dir=tmp_path / "raw",
        reports_dir=reports,
        since=SINCE,
        max_records=10,
        slice_="incremental",
        echo=lambda _m: None,
    )
    assert code == 0
    assert report["last_run"]["slice"] == "incremental"
    assert report["last_run"]["limit"] == 10
    entry = report["incremental"]["2026-01-01"]["jira"]
    assert entry["slice"] == "incremental" and entry["limit"] == 10
    # The full pull's own section is not touched by an incremental run.
    assert "jira" not in (report.get("sources") or {})


def test_a_connector_that_cannot_open_its_window_refuses_the_flag(tmp_path):
    with pytest.raises(RegistryError, match="not implemented"):
        build_connector(source("confluence"), tmp_path, new_slice=True)
    # …and is perfectly happy without it.
    assert build_connector(source("confluence"), tmp_path, max_records=5) is not None


# --------------------------------------------------------------------------- probe


@respx.mock
def test_the_probe_counts_first_and_then_reads_the_ten_oldest(tmp_path):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return paged(1234)(request)

    respx.get(SEARCH).mock(side_effect=handler)
    report = probe_incremental(connector(tmp_path, new_slice=True), SINCE, echo=lambda _m: None)

    assert [int(p["maxResults"]) for p in seen] == [0, SAMPLE]
    assert seen[0]["jql"] == seen[1]["jql"] == build_jql(JIRA, SINCE, new=True)
    assert "created ASC" in seen[1]["jql"]  # oldest first, so the ten are reproducible
    assert seen[1]["expand"] == "changelog"

    assert report["total"] == 1234
    assert report["sample"] == SAMPLE
    assert len(report["oldest"]) == SAMPLE
    assert report["with_changelog"] == 5  # histories = i % 2
    assert report["dropped_clauses"] == ['created >= "2023-01-01"', 'created <= "2025-12-31"']
    row = report["oldest"][0]
    assert set(row) >= {"key", "created", "components", "has_changelog", "changelog_entries"}
    assert row["components"] == ["streams"]
    assert "--slice incremental" in report["harvest_command"]


@respx.mock
def test_the_probe_writes_its_report_and_no_raw_pages(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged(7))
    raw = tmp_path / "raw"
    reports = tmp_path / "reports"
    report, code = run_probe(
        source="jira",
        since=SINCE,
        raw_dir=raw,
        reports_dir=reports,
        connector=connector(tmp_path, new_slice=True),
        echo=lambda _m: None,
    )
    assert code == 0
    assert (reports / "incremental_probe.json").is_file()
    assert not raw.exists(), "a probe is a read: it must not create data/raw/"
    assert keys_from_probe(report) == [row["key"] for row in report["oldest"]]
    assert "incremental probe" in summarize_probe(report)


@respx.mock
def test_a_probe_that_finds_nothing_exits_nonzero(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged(0))
    _report, code = run_probe(
        source="jira",
        since=SINCE,
        raw_dir=tmp_path / "raw",
        reports_dir=tmp_path / "reports",
        connector=connector(tmp_path, new_slice=True),
        echo=lambda _m: None,
    )
    assert code == 1


# --------------------------------------------------------------------------- run report


def finished_report(tmp_path: Path) -> dict:
    run = IncrementalRun(reports_dir=tmp_path, since="2026-01-01")
    run.keys = [f"KAFKA-{19000 + i}" for i in range(SAMPLE)]
    for step in PIPELINE_STEPS:
        run.record(step, command=f"uv run brain {step}", duration_s=1.5, report={"ok": True})
    run.set_communities(member_hash_changed=3, resummarized=3, copied=1, total=148)
    for i in range(QUESTIONS):
        run.add_question(id=f"inc-{i}", question="?", citation_valid=i < 4)
    return json.loads((tmp_path / REPORT_NAME).read_text(encoding="utf-8"))


def test_the_writer_produces_a_report_the_validator_accepts(tmp_path):
    report = finished_report(tmp_path)
    assert validate_incremental(report) == []
    assert report["slice"] == "incremental"
    assert [s["step"] for s in report["steps"]] == list(PIPELINE_STEPS)
    assert report["duration_s"] == round(1.5 * len(PIPELINE_STEPS), 2)


def test_the_report_is_on_disk_after_every_step_not_only_at_the_end(tmp_path):
    run = IncrementalRun(reports_dir=tmp_path, since="2026-01-01")
    run.record("harvest", duration_s=2.0)
    on_disk = json.loads((tmp_path / REPORT_NAME).read_text(encoding="utf-8"))
    assert [s["step"] for s in on_disk["steps"]] == ["harvest"]


def test_timed_measures_the_body_and_keeps_the_report(tmp_path):
    ticks = iter([10.0, 14.5])
    run = IncrementalRun(reports_dir=tmp_path, since="2026-01-01", _clock=lambda: next(ticks))
    with run.timed("chunk", command="uv run brain chunk") as row:
        row.report = {"chunks": 41}
    stored = json.loads((tmp_path / REPORT_NAME).read_text(encoding="utf-8"))["steps"][0]
    assert stored["duration_s"] == 4.5
    assert stored["report"] == {"chunks": 41}


def test_recording_a_step_twice_replaces_it_rather_than_duplicating(tmp_path):
    run = IncrementalRun(reports_dir=tmp_path, since="2026-01-01")
    run.record("load", duration_s=1.0)
    run.record("load", duration_s=2.0, notes="rerun after a fix")
    steps = json.loads((tmp_path / REPORT_NAME).read_text(encoding="utf-8"))["steps"]
    assert len(steps) == 1 and steps[0]["duration_s"] == 2.0


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda r: r.update(keys=r["keys"][:9]), "9 keys, expected 10"),
        (lambda r: r.update(keys=r["keys"][:9] + [r["keys"][0]]), "duplicates"),
        (lambda r: r.update(steps=r["steps"][:-1]), "no row for step 'index'"),
        (lambda r: r.update(communities={}), "member_hash_changed"),
        (lambda r: r.update(questions=r["questions"][:4]), "4 questions, expected 5"),
        (
            lambda r: [q.update(citation_valid=False) for q in r["questions"]],
            "only 0 of 5 answers cite",
        ),
    ],
)
def test_the_validator_names_what_an_unfinished_run_is_missing(tmp_path, mutate, expected):
    report = finished_report(tmp_path)
    mutate(report)
    problems = validate_incremental(report)
    assert any(expected in p for p in problems), problems
