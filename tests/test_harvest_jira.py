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
    assert density["streams"]["pct_with_history"] == 50.0

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
    assert stats["pct_changelog_expanded"] == 50.0
    assert stats["pct_with_comment_field"] == 50.0


@respx.mock
def test_retries_surface_in_the_run_result(tmp_path):
    """The retry happened on the HTTP layer — the report must still hear about it."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return paged_transport(3, 100)(request)

    respx.get(SEARCH).mock(side_effect=handler)
    result = connector(tmp_path, page_size=100).run()

    assert result.records == 3
    assert [e["kind"] for e in result.errors] == ["retry"]
    assert not any(e["fatal"] for e in result.errors)


@respx.mock
def test_a_query_change_deletes_the_previous_query_pages(tmp_path):
    """Page indexes restart at 0, so the old run's tail must not survive as duplicates."""
    respx.get(SEARCH).mock(side_effect=paged_transport(250, 100))
    connector(tmp_path, page_size=100).run()
    assert len(list((tmp_path / "jira").glob("issues-*.json"))) == 3

    # a different page size is a different signature -> a fresh, shorter pull
    respx.get(SEARCH).mock(side_effect=paged_transport(250, 250))
    second = connector(tmp_path, page_size=250).run()

    assert second.records == 250
    assert sorted(f.name for f in (tmp_path / "jira").glob("issues-*.json")) == ["issues-0000.json"]
    assert second.stats["issues"] == 250  # not 250 + the 200 stranded in pages 1 and 2
    assert any(e["kind"] == "reset" for e in second.errors)


@respx.mock
def test_analysis_ignores_a_page_the_checkpoint_does_not_list(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged_transport(150, 100))
    connector(tmp_path, page_size=100).run()

    # an orphan from some earlier run, higher-numbered than anything current
    (tmp_path / "jira" / "issues-0009.json").write_text(
        json.dumps({"issues": [make_issue(999)]}), encoding="utf-8"
    )

    from brain.harvest.jira import analyze, iter_raw_issues

    assert analyze(iter_raw_issues(tmp_path / "jira"))["issues"] == 150


@respx.mock
def test_resume_overwrites_a_page_the_checkpoint_never_recorded(tmp_path):
    """Crash between writing a page and saving the checkpoint: refetch, never append."""
    respx.get(SEARCH).mock(side_effect=paged_transport(250, 100))

    run_dir = tmp_path / "jira"
    run_dir.mkdir(parents=True)
    (run_dir / "issues-0000.json").write_text(
        json.dumps({"issues": [make_issue(9001)], "total": 250}), encoding="utf-8"
    )

    result = connector(tmp_path, page_size=100).run()

    assert result.pages == 3
    assert result.records == 250
    assert sorted(f.name for f in run_dir.glob("issues-*.json")) == [
        "issues-0000.json",
        "issues-0001.json",
        "issues-0002.json",
    ]
    keys = [i["key"] for i in json.loads((run_dir / "issues-0000.json").read_text())["issues"]]
    assert "KAFKA-9001" not in keys  # the planted page was overwritten, not kept
    assert result.stats["issues"] == 250
