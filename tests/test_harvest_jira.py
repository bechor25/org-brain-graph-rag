"""Jira connector: JQL shape, paging, resume, idempotency, link density."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
import respx

from brain.harvest.base import HttpFetcher
from brain.harvest.jira import BASE_URL, JiraConnector, analyze, build_jql

SEARCH = f"{BASE_URL}/rest/api/2/search"


def make_issue(n: int, component: str = "streams", **over) -> dict:
    fields = {
        "summary": f"KAFKA-{n} summary",
        "description": "body text",
        "components": [{"name": component}],
        "issuelinks": [],
        "assignee": {"name": "mjsax"},
        "fixVersions": [],
        "comment": {"comments": [], "total": 0},
    }
    fields.update(over.pop("fields", {}))
    issue = {"key": f"KAFKA-{n}", "fields": fields, "changelog": {"histories": []}}
    issue.update(over)
    return issue


def paged_transport(total: int, page_size: int, seen: list[dict] | None = None):
    """Serve `total` issues in pages of `page_size`, recording the query params."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if seen is not None:
            seen.append(params)
        start = int(params.get("startAt", 0))
        size = int(params.get("maxResults", page_size))
        issues = [make_issue(start + i) for i in range(min(size, max(0, total - start)))]
        return httpx.Response(
            200,
            json={"startAt": start, "maxResults": size, "total": total, "issues": issues},
        )

    return handler


def connector(tmp_path, page_size: int = 500) -> JiraConnector:
    http = HttpFetcher(BASE_URL, min_interval=0.0, sleep=lambda s: None)
    return JiraConnector(tmp_path, http=http, page_size=page_size)


# --------------------------------------------------------------------------- JQL


def test_jql_quotes_the_reserved_word_connect():
    jql = build_jql(None)
    assert 'component in (streams, "connect", clients)' in jql
    assert "component in (streams, connect, clients)" not in jql


def test_jql_matches_the_agreed_slice_and_ends_with_order_by():
    jql = build_jql(None)
    assert jql == (
        'project = KAFKA AND component in (streams, "connect", clients) '
        'AND created >= "2023-01-01" AND created <= "2025-12-31" ORDER BY created ASC'
    )


def test_since_adds_updated_clause_before_order_by():
    jql = build_jql(date(2025, 6, 1))
    assert 'AND updated >= "2025-06-01" ORDER BY created ASC' in jql
    assert jql.index('updated >= "2025-06-01"') < jql.index("ORDER BY")


# --------------------------------------------------------------------------- paging


@respx.mock
def test_pagination_across_three_pages(tmp_path):
    seen: list[dict] = []
    respx.get(SEARCH).mock(side_effect=paged_transport(250, 100, seen))

    result = connector(tmp_path, page_size=100).run()

    assert result.pages == 3
    assert result.records == 250
    assert [int(p["startAt"]) for p in seen] == [0, 100, 200]
    assert {int(p["maxResults"]) for p in seen} == {100}
    assert {p["expand"] for p in seen} == {"changelog"}
    assert {p["fields"] for p in seen} == {"*all"}

    files = sorted(f.name for f in (tmp_path / "jira").glob("issues-*.json"))
    assert files == ["issues-0000.json", "issues-0001.json", "issues-0002.json"]
    assert result.checkpoint["done"] is True
    assert result.checkpoint["records"] == 250
    assert result.stats["issues"] == 250


@respx.mock
def test_raw_pages_keep_the_server_response_verbatim(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged_transport(3, 100))
    connector(tmp_path, page_size=100).run()
    payload = json.loads((tmp_path / "jira" / "issues-0000.json").read_text())
    assert payload["total"] == 3
    assert payload["issues"][0]["changelog"] == {"histories": []}


@respx.mock
def test_second_run_fetches_zero_pages(tmp_path):
    route = respx.get(SEARCH).mock(side_effect=paged_transport(250, 100))
    first = connector(tmp_path, page_size=100).run()
    calls_after_first = route.call_count

    second = connector(tmp_path, page_size=100).run()

    assert first.pages == 3
    assert second.pages == 0
    assert second.records == 0
    assert route.call_count == calls_after_first  # not a single extra request
    assert second.checkpoint["records"] == 250  # the corpus is still there


@respx.mock
def test_resume_after_a_crash_mid_run(tmp_path):
    seen: list[dict] = []
    respx.get(SEARCH).mock(side_effect=paged_transport(250, 100, seen))

    # crash: the caller dies after consuming exactly one page
    crashed = connector(tmp_path, page_size=100)
    checkpoint = crashed.checkpoint(None)
    pages = crashed.fetch(None, checkpoint)
    next(pages)
    pages.close()

    assert checkpoint.pages == 1 and checkpoint.done is False
    assert json.loads((tmp_path / "jira" / "checkpoint.json").read_text())["cursor"] == {
        "start_at": 100
    }

    resumed = connector(tmp_path, page_size=100).run()

    assert resumed.pages == 2  # only the missing two
    assert [int(p["startAt"]) for p in seen] == [0, 100, 200]  # no page refetched
    assert resumed.checkpoint["records"] == 250
    assert resumed.checkpoint["done"] is True
    assert sorted(f.name for f in (tmp_path / "jira").glob("issues-*.json")) == [
        "issues-0000.json",
        "issues-0001.json",
        "issues-0002.json",
    ]


@respx.mock
def test_since_run_is_isolated_from_the_full_pull(tmp_path):
    seen: list[dict] = []
    respx.get(SEARCH).mock(side_effect=paged_transport(120, 100, seen))
    connector(tmp_path, page_size=100).run()
    seen.clear()

    respx.get(SEARCH).mock(side_effect=paged_transport(10, 100, seen))
    incremental = connector(tmp_path, page_size=100).run(since=date(2025, 6, 1))

    assert incremental.records == 10
    assert 'updated >= "2025-06-01"' in seen[0]["jql"]
    assert (tmp_path / "jira" / "since-2025-06-01" / "issues-0000.json").exists()
    assert (tmp_path / "jira" / "since-2025-06-01" / "checkpoint.json").exists()
    # the full pull is untouched
    assert (tmp_path / "jira" / "issues-0000.json").exists()
    assert json.loads((tmp_path / "jira" / "checkpoint.json").read_text())["records"] == 120


@respx.mock
def test_an_empty_result_set_closes_the_checkpoint(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged_transport(0, 100))
    result = connector(tmp_path, page_size=100).run()
    assert result.pages == 0
    assert result.checkpoint["done"] is True


@respx.mock
def test_a_400_is_fatal_and_recorded_not_swallowed(tmp_path):
    respx.get(SEARCH).mock(
        return_value=httpx.Response(400, text="'connect' is a reserved JQL word")
    )
    result = connector(tmp_path).run()
    assert result.pages == 0
    assert any(e["fatal"] and "reserved JQL word" in e["detail"] for e in result.errors)


@respx.mock
def test_probe_reports_the_slice_size(tmp_path):
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"total": 1416, "issues": []}))
    probe = connector(tmp_path).probe()
    assert probe.ok and probe.total == 1416


# --------------------------------------------------------------------------- density


@pytest.fixture
def density_issues() -> list[dict]:
    return [
        make_issue(
            1,
            "streams",
            fields={
                "issuelinks": [{"type": {"name": "Reference"}}],
                "summary": "fix KIP-848 rebalance",
                "comment": {"comments": [{"body": "a"}, {"body": "b"}]},
                "fixVersions": [{"name": "3.7.0"}],
            },
        ),
        make_issue(2, "streams", fields={"assignee": None}, changelog={"histories": [{"id": "1"}]}),
        make_issue(3, "connect", fields={"issuelinks": [{"type": {"name": "Blocker"}}]}),
        make_issue(4, "clients"),
    ]


def test_link_density_is_computed_per_component(density_issues):
    density = analyze(density_issues)["link_density"]

    assert density["streams"]["n"] == 2
    assert density["streams"]["pct_formal_links"] == 50.0
    assert density["streams"]["pct_kip_mention"] == 50.0
    assert density["streams"]["avg_comments"] == 1.0
    assert density["streams"]["pct_assignee"] == 50.0
    assert density["streams"]["pct_fix_versions"] == 50.0
    assert density["streams"]["pct_changelog_present"] == 50.0

    assert density["connect"]["pct_formal_links"] == 100.0
    assert density["clients"]["pct_formal_links"] == 0.0
    assert density["all"]["n"] == 4
    assert density["all"]["pct_formal_links"] == 50.0


def test_an_issue_with_two_components_counts_in_both_but_once_in_all():
    issues = [make_issue(1, fields={"components": [{"name": "streams"}, {"name": "clients"}]})]
    density = analyze(issues)["link_density"]
    assert density["streams"]["n"] == 1
    assert density["clients"]["n"] == 1
    assert density["all"]["n"] == 1


def test_stats_count_changelog_and_comment_coverage():
    issues = [
        make_issue(1),
        {"key": "KAFKA-2", "fields": {"summary": "no changelog", "comment": None}},
    ]
    stats = analyze(issues)
    assert stats["issues"] == 2
    assert stats["pct_with_changelog"] == 50.0
    assert stats["pct_with_comment_field"] == 50.0
