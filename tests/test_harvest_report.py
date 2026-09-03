"""The harvest report and the CLI wiring around it."""

from __future__ import annotations

import json
from datetime import date

import pytest

from brain.cli import app
from brain.harvest.base import HarvestResult, Page
from brain.harvest.report import build_report, summarize, write_report
from brain.harvest.runner import resolve_sources, run_harvest


def result(source: str, **over) -> HarvestResult:
    base = {
        "records": 10,
        "pages": 2,
        "duration_s": 1.5,
        "errors": [],
        "checkpoint": {"records": 10, "pages": 2, "done": True},
        "stats": {},
    }
    base.update(over)
    return HarvestResult(source=source, **base)


class FakeConnector:
    """Stands in for a real connector: writes nothing, reports fixed numbers."""

    calls: list[tuple[str, date | None]] = []

    def __init__(self, raw_dir, name="fake", pages=1, records=5, errors=None):
        self.raw_dir = raw_dir
        self.name = name
        self._pages = pages
        self._records = records
        self._errors = errors or []

    def query_text(self, since):
        return f"{self.name} query since={since}"

    def run(self, since=None):
        FakeConnector.calls.append((self.name, since))
        return HarvestResult(
            source=self.name,
            records=self._records,
            pages=self._pages,
            duration_s=0.1,
            errors=self._errors,
            checkpoint={"records": self._records, "pages": self._pages, "done": True},
            stats={"issues": self._records, "pages": self._records, "commits": self._records},
        )


def factory(name, **kw):
    return lambda raw_dir: FakeConnector(raw_dir, name=name, **kw)


@pytest.fixture(autouse=True)
def _reset_calls():
    FakeConnector.calls = []
    yield


# --------------------------------------------------------------------------- report shape


def test_report_carries_per_source_counts_errors_and_checkpoint(tmp_path):
    report = build_report(
        {"jira": result("jira"), "git": result("git", records=3, pages=1)},
        raw_dir=tmp_path,
        since=None,
        duration_s=12.3,
    )
    assert report["step"] == "harvest"
    assert report["last_run"]["new_records"] == 13
    assert set(report["sources"]) == {"jira", "git"}
    for entry in report["sources"].values():
        assert set(entry) >= {"records", "pages", "duration_s", "errors", "checkpoint", "since"}


def test_report_merge_keeps_sources_that_did_not_run(tmp_path):
    first = build_report({"jira": result("jira")}, raw_dir=tmp_path, since=None, duration_s=1.0)
    second = build_report(
        {"git": result("git")}, raw_dir=tmp_path, since=None, duration_s=1.0, existing=first
    )
    assert set(second["sources"]) == {"jira", "git"}
    assert second["last_run"]["sources"] == ["git"]


def test_link_density_comes_from_the_jira_stats_on_a_full_run(tmp_path):
    density = {"streams": {"n": 5, "pct_formal_links": 40.0}}
    report = build_report(
        {"jira": result("jira", stats={"link_density": density})},
        raw_dir=tmp_path,
        since=None,
        duration_s=1.0,
    )
    assert report["link_density"] == density


def test_link_density_is_recomputed_from_raw_when_jira_did_not_run(tmp_path):
    page = {
        "issues": [
            {
                "key": "KAFKA-1",
                "fields": {
                    "summary": "s",
                    "components": [{"name": "clients"}],
                    "issuelinks": [{"type": {}}],
                    "comment": {"comments": []},
                },
                "changelog": {"histories": [{"id": "1"}]},
            }
        ]
    }
    (tmp_path / "jira").mkdir()
    (tmp_path / "jira" / "issues-0000.json").write_text(json.dumps(page), encoding="utf-8")

    report = build_report({"git": result("git")}, raw_dir=tmp_path, since=None, duration_s=1.0)
    assert report["link_density"]["clients"]["pct_formal_links"] == 100.0


def test_a_since_run_does_not_overwrite_density_with_the_narrow_slice(tmp_path):
    (tmp_path / "jira").mkdir()
    (tmp_path / "jira" / "issues-0000.json").write_text(
        json.dumps({"issues": [{"key": "K-1", "fields": {"components": [{"name": "streams"}]}}]}),
        encoding="utf-8",
    )
    report = build_report(
        {"jira": result("jira", stats={"link_density": {"streams": {"n": 999}}})},
        raw_dir=tmp_path,
        since=date(2025, 6, 1),
        duration_s=1.0,
    )
    assert report["link_density"]["streams"]["n"] == 1  # from the full pull, not the slice


def test_summary_mentions_every_source_and_the_density_table(tmp_path):
    report = build_report(
        {
            "jira": result("jira", stats={"issues": 1416, "pct_with_changelog": 100.0}),
            "confluence": result("confluence", stats={"pages": 1391}),
            "git": result("git", stats={"commits": 6107, "with_issue_key": 4069}),
        },
        raw_dir=tmp_path,
        since=None,
        duration_s=1.0,
    )
    report["link_density"] = {"streams": {"n": 509, "pct_formal_links": 38.0}}
    text = summarize(report)
    assert "jira" in text and "1416 issues" in text
    assert "1391 pages" in text
    assert "4069 keyed" in text
    assert "link density" in text and "streams" in text


def test_write_report_is_atomic_json(tmp_path):
    path = write_report(tmp_path / "reports" / "harvest.json", {"step": "harvest"})
    assert json.loads(path.read_text())["step"] == "harvest"
    assert not list(path.parent.glob("*.tmp"))


# --------------------------------------------------------------------------- runner


def test_resolve_sources():
    assert resolve_sources("all") == ["jira", "confluence", "git"]
    assert resolve_sources("git") == ["git"]
    with pytest.raises(ValueError, match="unknown source"):
        resolve_sources("ado")


def test_run_harvest_runs_every_source_and_writes_the_report(tmp_path):
    report, code = run_harvest(
        ["jira", "confluence", "git"],
        raw_dir=tmp_path / "raw",
        reports_dir=tmp_path / "reports",
        factories={n: factory(n) for n in ("jira", "confluence", "git")},
        echo=lambda _: None,
    )
    assert code == 0
    assert [n for n, _ in FakeConnector.calls] == ["jira", "confluence", "git"]
    assert (tmp_path / "reports" / "harvest.json").exists()
    assert report["last_run"]["new_records"] == 15


def test_a_fatal_error_in_one_source_still_runs_the_others_and_exits_1(tmp_path):
    factories = {
        "jira": factory("jira", errors=[{"kind": "http", "detail": "boom", "fatal": True}]),
        "confluence": factory("confluence"),
        "git": factory("git"),
    }
    report, code = run_harvest(
        ["jira", "confluence", "git"],
        raw_dir=tmp_path / "raw",
        reports_dir=tmp_path / "reports",
        factories=factories,
        echo=lambda _: None,
    )
    assert code == 1
    assert len(FakeConnector.calls) == 3
    assert report["sources"]["jira"]["errors"][0]["detail"] == "boom"


# --------------------------------------------------------------------------- cli


def test_cli_rejects_an_unknown_source(runner):
    out = runner.invoke(app, ["harvest", "--source", "ado"])
    assert out.exit_code != 0
    assert "unknown source" in out.output


def test_cli_rejects_a_bad_since(runner):
    out = runner.invoke(app, ["harvest", "--since", "yesterday"])
    assert out.exit_code != 0
    assert "not YYYY-MM-DD" in out.output


def test_cli_help_documents_the_flags(runner):
    out = runner.invoke(app, ["harvest", "--help"])
    assert out.exit_code == 0
    assert "--source" in out.output and "--since" in out.output


def test_cli_runs_a_source_and_reports(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr("brain.harvest.runner.CONNECTORS", {"git": factory("git")})
    out = runner.invoke(app, ["harvest", "--source", "git"])
    assert out.exit_code == 0, out.output
    report = json.loads((tmp_path / "reports" / "harvest.json").read_text())
    assert report["sources"]["git"]["records"] == 5


def test_page_len_counts_records():
    assert len(Page(index=0, records=[{}, {}])) == 2
