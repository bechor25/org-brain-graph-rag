"""The incremental probe against the real ASF Jira. Excluded by default (`-m network`).

Read-only, anonymous, two GETs. It exists for the one thing no fake can check: that the
rewritten JQL — the base slice's `created` window taken off and replaced — is JQL the ASF
server actually accepts, and that there really are issues on the other side of the corpus
window. A 400 here is the failure mode a mocked test cannot see.

Run with:  uv run pytest -q -m network tests/network/test_incremental_probe_network.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from brain.harvest.incremental import SAMPLE, probe_incremental
from brain.harvest.jira import JiraConnector
from tests.harvest_helpers import source

pytestmark = pytest.mark.network

SINCE = date(2026, 1, 1)


def connector() -> JiraConnector:
    return JiraConnector(Path("/tmp/brain-net"), source=source("jira"), new_slice=True)


def test_the_incremental_jql_is_accepted_and_the_slice_has_a_future():
    conn = connector()
    try:
        report = probe_incremental(conn, SINCE, echo=lambda _m: None)
    finally:
        conn.http.close()

    # The rewrite happened: no end date from the corpus definition survived.
    assert '<= "2025-12-31"' not in report["jql"]
    assert 'created >= "2026-01-01"' in report["jql"]
    assert report["dropped_clauses"], "the base window was supposed to come off"

    # There are issues outside the slice, and at least the ten the run needs.
    assert report["total"] is not None and report["total"] >= SAMPLE, report["jql"]
    assert len(report["oldest"]) == SAMPLE

    for row in report["oldest"]:
        assert row["key"].startswith("KAFKA-")
        assert row["created"] >= "2026-01-01", "ORDER BY created ASC returned the wrong window"
        assert row["components"], "the component filter survived the rewrite"

    # Oldest-first, so the chosen ten are the same ten tomorrow.
    assert [r["created"] for r in report["oldest"]] == sorted(
        r["created"] for r in report["oldest"]
    )


def test_the_probe_writes_nothing_into_the_raw_directory(tmp_path):
    conn = JiraConnector(tmp_path, source=source("jira"), new_slice=True)
    try:
        probe_incremental(conn, SINCE, sample=1, echo=lambda _m: None)
    finally:
        conn.http.close()
    assert not any(tmp_path.iterdir())
