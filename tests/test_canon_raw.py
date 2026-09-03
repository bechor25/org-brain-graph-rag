"""The `data/raw/` read contract: checkpoint-driven enumeration and dedupe."""

from __future__ import annotations

import json

from brain.canon.raw import iter_dir, load_source, run_dirs
from tests.canon_helpers import issue, page, plant_commits, plant_pages


def test_run_dirs_puts_the_authoritative_directory_first_then_since_by_date(tmp_path):
    plant_pages(tmp_path, "jira", [issue("KAFKA-1")])
    plant_pages(tmp_path, "jira", [issue("KAFKA-1")], since="2025-06-01")
    plant_pages(tmp_path, "jira", [issue("KAFKA-1")], since="2024-01-01")

    assert [p.name for p in run_dirs(tmp_path, "jira")] == [
        "jira",
        "since-2024-01-01",
        "since-2025-06-01",
    ]


def test_pages_come_from_the_checkpoint_not_from_a_glob(tmp_path):
    run_dir = plant_pages(tmp_path, "jira", [issue("KAFKA-1")])
    # A page left behind by an older run with a different query signature. Globbing
    # `issues-*.json` would hand canon a record the current query never returned.
    (run_dir / "issues-0001.json").write_text(json.dumps({"issues": [issue("KAFKA-999")]}))

    assert [r["key"] for r in iter_dir(run_dir, "jira")] == ["KAFKA-1"]


def test_incremental_pull_supersedes_the_full_pull_when_it_is_newer(tmp_path):
    plant_pages(
        tmp_path,
        "jira",
        [issue("KAFKA-1", updated="2024-01-01T00:00:00.000+0000", status={"name": "Open"})],
    )
    plant_pages(
        tmp_path,
        "jira",
        [issue("KAFKA-1", updated="2025-07-01T00:00:00.000+0000", status={"name": "Resolved"})],
        since="2025-06-01",
    )

    raw = load_source(tmp_path, "jira")

    assert [r["key"] for r in raw.records] == ["KAFKA-1"]
    assert raw.records[0]["fields"]["status"]["name"] == "Resolved"
    assert raw.stats()["records_read"] == 2
    assert raw.stats()["duplicates"] == 1
    assert raw.stats()["superseded_by_incremental"] == 1


def test_a_stale_incremental_copy_does_not_win(tmp_path):
    plant_pages(tmp_path, "jira", [issue("KAFKA-1", updated="2025-07-01T00:00:00.000+0000")])
    plant_pages(
        tmp_path,
        "jira",
        [issue("KAFKA-1", updated="2024-01-01T00:00:00.000+0000", summary="stale")],
        since="2025-06-01",
    )

    raw = load_source(tmp_path, "jira")

    assert raw.records[0]["fields"]["summary"] != "stale"
    assert raw.stats()["superseded_by_incremental"] == 0


def test_confluence_dedupes_on_id_by_version_number(tmp_path):
    plant_pages(tmp_path, "confluence", [page("1", "KIP-1: a", version={"number": 3})])
    plant_pages(
        tmp_path,
        "confluence",
        [page("1", "KIP-1: b", version={"number": 9})],
        since="2026-08-01",
    )

    raw = load_source(tmp_path, "confluence")

    assert [p["title"] for p in raw.records] == ["KIP-1: b"]
    assert raw.stats()["duplicates"] == 1


def test_git_dedupes_on_sha_and_keeps_the_first_copy(tmp_path):
    from tests.canon_helpers import commit

    plant_commits(tmp_path, [commit("aaa", "one"), commit("bbb", "two")])
    plant_commits(tmp_path, [commit("aaa", "one again")], since="2025-01-01")

    raw = load_source(tmp_path, "git")

    assert [c["sha"] for c in raw.records] == ["aaa", "bbb"]
    assert raw.records[0]["subject"] == "one"  # a sha is content-addressed: no tie-break
    assert raw.stats()["duplicates"] == 1


def test_a_missing_source_directory_is_empty_not_an_error(tmp_path):
    assert load_source(tmp_path, "jira").records == []
