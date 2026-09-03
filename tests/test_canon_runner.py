"""`brain canon` end to end: the five files, the report, idempotence, partial runs."""

from __future__ import annotations

import json

import pytest

from brain.canon.io import read_jsonl
from brain.canon.models import Change, Document, Person, WorkItem
from brain.canon.runner import natural_key, resolve_sources, run_canon
from brain.cli import app
from tests.canon_helpers import commit, issue, page, plant_commits, plant_pages


@pytest.fixture
def corpus(tmp_path):
    """A miniature of the real corpus: one issue, one KIP page, one commit."""
    raw = tmp_path / "raw"
    plant_pages(
        raw,
        "jira",
        [
            issue(
                "KAFKA-100",
                description="see KAFKA-200 and KIP-848, encoded UTF-8",
                reporter={"name": "jrao", "displayName": "Jun Rao"},
                fixVersions=[{"name": "3.7.0"}],
            )
        ],
    )
    plant_pages(raw, "confluence", [page("1", "KIP-848: Rebalance", body="<p>KAFKA-100</p>")])
    plant_commits(raw, [commit("a" * 40, "KAFKA-100: fix it (#12)")])
    return tmp_path


def run(tmp_path, sources):
    return run_canon(
        sources,
        raw_dir=tmp_path / "raw",
        canonical_dir=tmp_path / "canonical",
        reports_dir=tmp_path / "reports",
        echo=lambda _: None,
    )


def files(tmp_path):
    return {p.name: p.read_bytes() for p in sorted((tmp_path / "canonical").iterdir())}


def test_resolve_sources():
    assert resolve_sources("all") == ["jira", "confluence", "git"]
    assert resolve_sources("git") == ["git"]
    with pytest.raises(ValueError, match="unknown source"):
        resolve_sources("ado")


def test_natural_key_orders_issue_numbers_like_a_human():
    assert sorted(["KAFKA-10", "KAFKA-9"], key=natural_key) == ["KAFKA-9", "KAFKA-10"]


def test_a_full_run_writes_the_five_canonical_files(corpus):
    report, code = run(corpus, ["jira", "confluence", "git"])

    assert code == 0
    assert set(files(corpus)) == {
        "workitems.jsonl",
        "documents.jsonl",
        "persons.jsonl",
        "changes.jsonl",
        "containers.jsonl",
    }
    assert report["counts"]["by_type"] == {
        "workitems": 1,
        "documents": 1,
        "persons": 4,
        "changes": 2,  # the commit and the pull request it names
        "containers": 3,  # component, version, space
    }
    assert report["counts"]["changes_by_kind"] == {"commit": 1, "pr": 1}


def test_a_rerun_is_byte_identical(corpus):
    run(corpus, ["jira", "confluence", "git"])
    first = files(corpus)
    run(corpus, ["jira", "confluence", "git"])

    assert files(corpus) == first


def test_a_partial_run_rewrites_its_own_source_and_keeps_the_others(corpus):
    run(corpus, ["jira", "confluence", "git"])
    plant_commits(corpus / "raw", [commit("b" * 40, "KAFKA-100: another (#13)")])

    run(corpus, ["git"])

    workitems = list(read_jsonl(corpus / "canonical" / "workitems.jsonl", WorkItem))
    documents = list(read_jsonl(corpus / "canonical" / "documents.jsonl", Document))
    changes = list(read_jsonl(corpus / "canonical" / "changes.jsonl", Change))
    persons = {p.id for p in read_jsonl(corpus / "canonical" / "persons.jsonl", Person)}

    assert [w.key for w in workitems] == ["KAFKA-100"]
    assert [d.key for d in documents] == ["KIP-848"]
    assert [c.id for c in changes] == ["b" * 40, "pr:13"]
    assert "jira:jrao" in persons and "git:jun@example.com" in persons


def test_synthetic_records_are_never_dropped(corpus):
    run(corpus, ["jira", "confluence", "git"])
    path = corpus / "canonical" / "workitems.jsonl"
    synthetic = WorkItem(
        id="xray:XT-1",
        key="XT-1",
        source="xray",
        type="Test",
        title="synthetic test",
        status="Open",
        created="2024-01-01T00:00:00Z",
        synthetic=True,
    )
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(synthetic.model_dump(mode="json", by_alias=True)) + "\n")

    run(corpus, ["jira", "confluence", "git"])

    assert {w.key for w in read_jsonl(path, WorkItem)} == {"KAFKA-100", "XT-1"}


def test_the_report_records_dedupe_refs_kip_decisions_and_the_acceptance_checks(corpus):
    plant_pages(
        corpus / "raw",
        "jira",
        [
            issue(
                "KAFKA-100",
                summary="newer",
                description="see KAFKA-200, encoded UTF-8",
                updated="2030-01-01T00:00:00.000+0000",
            )
        ],
        since="2025-06-01",
    )
    report, _ = run(corpus, ["jira", "confluence", "git"])

    assert report["dedupe"]["jira"]["superseded_by_incremental"] == 1
    assert report["refs"]["by_source"]["jira"]["via_link"] == 0
    assert report["refs"]["totals_by_kind"]["issue"] >= 1
    assert ["UTF-8", 1] in report["refs"]["top_removed"]
    assert report["kip_key"]["canonical_keys"] == 1
    assert report["unmapped_fields"]["jira"]["records"] == 1
    assert {c["name"] for c in report["checks"]} >= {
        "every_workitem_has_a_component",
        "every_document_has_a_body",
        "every_change_has_at",
        "text_only_issue_refs_at_least_20pct",
    }
    assert all(c["ok"] for c in report["checks"] if c["name"].startswith("unique_ids"))


def test_a_failing_acceptance_check_stays_visible_in_the_report(corpus):
    plant_pages(corpus / "raw", "jira", [issue("KAFKA-100", description="no refs here")])

    report, code = run(corpus, ["jira"])
    check = next(c for c in report["checks"] if c["name"] == "text_only_issue_refs_at_least_20pct")

    assert check["ok"] is False and check["pct"] == 0.0
    assert code == 0  # the step succeeded; the corpus is what it is


def test_the_report_is_merged_across_partial_runs(corpus):
    run(corpus, ["jira", "confluence", "git"])
    report, _ = run(corpus, ["git"])

    assert set(report["dedupe"]) == {"jira", "confluence", "git"}
    assert set(report["refs"]["by_source"]) == {"jira", "confluence", "git"}
    assert report["kip_key"]["canonical_keys"] == 1  # kept from the confluence run
    assert report["last_run"]["sources"] == ["git"]


def test_cli_runs_canon_and_writes_the_report(runner, corpus, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(corpus))

    out = runner.invoke(app, ["canon", "--source", "git"])

    assert out.exit_code == 0, out.output
    report = json.loads((corpus / "reports" / "canon.json").read_text())
    assert report["counts"]["by_type"]["changes"] == 2


def test_cli_rejects_an_unknown_source(runner):
    out = runner.invoke(app, ["canon", "--source", "ado"])

    assert out.exit_code != 0
    assert "unknown source" in out.output
