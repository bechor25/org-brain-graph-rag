"""Real-network smoke tests, one per remote source. Excluded by default (`-m network`).

These are the only tests that leave the machine. They exist because every offline test
agrees with a fake server by construction — the assumptions worth guarding (expand really
returns the changelog, CQL really returns bodies) can only be checked against ASF itself.

Run with:  uv run pytest -q -m network
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.harvest.confluence import ConfluenceConnector
from brain.harvest.git import GitConnector
from brain.harvest.jira import JiraConnector
from tests.harvest_helpers import source

pytestmark = pytest.mark.network


def test_jira_probe_sees_the_whole_slice():
    probe = JiraConnector(Path("/tmp/brain-net")).probe()
    assert probe.ok, probe.detail
    assert probe.total is not None and probe.total >= 1400, probe.detail


def test_jira_page_carries_changelog_and_comments(tmp_path):
    connector = JiraConnector(tmp_path, source=source("jira", options={"page_size": 10}))
    checkpoint = connector.checkpoint(None)
    page = next(connector.fetch(None, checkpoint))
    connector.http.close()

    assert len(page.records) == 10
    assert all("changelog" in issue for issue in page.records)
    assert all("comment" in issue["fields"] for issue in page.records)
    assert checkpoint.total >= 1400


def test_confluence_probe_sees_the_kip_pages():
    probe = ConfluenceConnector(Path("/tmp/brain-net")).probe()
    assert probe.ok, probe.detail
    assert probe.total is not None and probe.total >= 1350, probe.detail


def test_confluence_page_carries_body_storage_and_a_next_link(tmp_path):
    connector = ConfluenceConnector(tmp_path, source=source("confluence", options={"limit": 5}))
    checkpoint = connector.checkpoint(None)
    page = next(connector.fetch(None, checkpoint))
    connector.http.close()

    assert len(page.records) == 5
    assert all(p["body"]["storage"]["value"] for p in page.records)
    assert all(p["title"].startswith("KIP-") for p in page.records)
    assert checkpoint.cursor["start"] == 5  # `_links.next` was followed


def test_git_clone_is_present_and_readable():
    """git is 'remote' only once — the clone. Afterwards everything is local."""
    from brain.config import get_settings

    probe = GitConnector(get_settings().raw_dir).probe()
    assert probe.ok, probe.detail
