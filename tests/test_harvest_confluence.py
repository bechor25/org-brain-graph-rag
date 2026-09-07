"""Confluence connector: CQL shape, `_links.next` paging, resume, idempotency."""

from __future__ import annotations

import json
from datetime import date

import httpx
import respx

from brain.harvest.base import HttpFetcher
from brain.harvest.confluence import (
    ConfluenceConnector,
    analyze,
    build_cql,
    next_start,
)
from tests.harvest_helpers import source  # noqa: E402

CONFLUENCE = source("confluence")
BASE_URL = CONFLUENCE.base_url
SEARCH = f"{BASE_URL}/rest/api/content/search"


def make_page(n: int, body: str = "<p>KIP body</p>") -> dict:
    return {
        "id": str(1000 + n),
        "title": f"KIP-{n}: something",
        "type": "page",
        "body": {"storage": {"value": body, "representation": "storage"}},
        "version": {"number": 3},
        "history": {"createdBy": {"username": "mjsax"}},
        "ancestors": [{"title": "Kafka Improvement Proposals"}],
        "metadata": {"labels": {"results": []}},
    }


def paged_transport(total: int, limit: int, seen: list[dict] | None = None):
    """Serve `total` pages, advertising the next offset only through `_links.next`."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if seen is not None:
            seen.append(params)
        start = int(params.get("start", 0))
        results = [make_page(start + i) for i in range(min(limit, max(0, total - start)))]
        body: dict = {
            "results": results,
            "start": start,
            "limit": limit,
            "size": len(results),
            "totalSize": total,
            "_links": {"base": BASE_URL, "self": SEARCH},
        }
        if start + limit < total:
            body["_links"]["next"] = (
                f"/rest/api/content/search?cql=x&limit={limit}&start={start + limit}"
            )
        return httpx.Response(200, json=body)

    return handler


def connector(tmp_path, limit: int = 25) -> ConfluenceConnector:
    http = HttpFetcher(BASE_URL, min_interval=0.0, sleep=lambda s: None)
    return ConfluenceConnector(
        tmp_path, source=source("confluence", options={"limit": limit}), http=http
    )


# --------------------------------------------------------------------------- CQL


def test_cql_targets_kip_pages_in_the_kafka_space():
    assert build_cql(CONFLUENCE) == 'space=KAFKA and type=page and title ~ "KIP-"'


def test_since_narrows_by_lastmodified():
    assert build_cql(CONFLUENCE, date(2025, 6, 1)).endswith('and lastmodified >= "2025-06-01"')


# --------------------------------------------------------------------------- next link


def test_next_start_reads_the_offset_out_of_the_link():
    payload = {"_links": {"next": "/rest/api/content/search?cql=x&limit=25&start=50"}}
    assert next_start(payload, current=25, limit=25) == 50


def test_next_start_handles_an_html_escaped_link():
    payload = {"_links": {"next": "/rest/api/content/search?cql=x&amp;limit=25&amp;start=50"}}
    assert next_start(payload, current=25, limit=25) == 50


def test_next_start_falls_back_to_start_plus_limit_when_the_link_has_no_start():
    payload = {"_links": {"next": "/rest/api/content/search?cql=x"}}
    assert next_start(payload, current=25, limit=25) == 50


def test_no_next_link_means_the_crawl_is_done():
    assert next_start({"_links": {"self": SEARCH}}, current=25, limit=25) is None


# --------------------------------------------------------------------------- paging


@respx.mock
def test_pagination_follows_the_next_link_across_four_pages(tmp_path):
    seen: list[dict] = []
    respx.get(SEARCH).mock(side_effect=paged_transport(85, 25, seen))

    result = connector(tmp_path).run()

    assert result.pages == 4
    assert result.records == 85
    assert [int(p["start"]) for p in seen] == [0, 25, 50, 75]
    assert {p["expand"] for p in seen} == {"body.storage,version,history,ancestors,metadata.labels"}
    assert result.checkpoint["done"] is True
    assert result.stats["pages_with_body_storage"] == 85
    assert sorted(f.name for f in (tmp_path / "confluence").glob("pages-*.json")) == [
        "pages-0000.json",
        "pages-0001.json",
        "pages-0002.json",
        "pages-0003.json",
    ]


@respx.mock
def test_the_server_cursor_wins_over_arithmetic(tmp_path):
    """If `_links.next` disagrees with start+limit, we follow the server."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        start = int(params.get("start", 0))
        links: dict = {"base": BASE_URL}
        if start == 0:
            links["next"] = "/rest/api/content/search?cql=x&limit=25&start=7"
        return httpx.Response(
            200, json={"results": [make_page(start)], "totalSize": 2, "_links": links}
        )

    respx.get(SEARCH).mock(side_effect=handler)
    connector(tmp_path).run()
    assert [int(p["start"]) for p in seen] == [0, 7]


@respx.mock
def test_raw_html_is_kept_verbatim(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged_transport(1, 25))
    connector(tmp_path).run()
    payload = json.loads((tmp_path / "confluence" / "pages-0000.json").read_text())
    assert payload["results"][0]["body"]["storage"]["value"] == "<p>KIP body</p>"


@respx.mock
def test_second_run_fetches_zero_pages(tmp_path):
    route = respx.get(SEARCH).mock(side_effect=paged_transport(85, 25))
    connector(tmp_path).run()
    calls = route.call_count

    second = connector(tmp_path).run()

    assert second.pages == 0
    assert route.call_count == calls


@respx.mock
def test_resume_after_a_crash_mid_run(tmp_path):
    seen: list[dict] = []
    respx.get(SEARCH).mock(side_effect=paged_transport(85, 25, seen))

    crashed = connector(tmp_path)
    checkpoint = crashed.checkpoint(None)
    pages = crashed.fetch(None, checkpoint)
    next(pages)
    next(pages)
    pages.close()

    assert checkpoint.pages == 2 and checkpoint.done is False

    resumed = connector(tmp_path).run()

    assert resumed.pages == 2
    assert [int(p["start"]) for p in seen] == [0, 25, 50, 75]
    assert resumed.checkpoint["records"] == 85
    assert resumed.checkpoint["done"] is True


@respx.mock
def test_since_run_is_isolated_from_the_full_pull(tmp_path):
    seen: list[dict] = []
    respx.get(SEARCH).mock(side_effect=paged_transport(30, 25, seen))
    connector(tmp_path).run()
    seen.clear()

    result = connector(tmp_path).run(since=date(2025, 6, 1))

    assert 'lastmodified >= "2025-06-01"' in seen[0]["cql"]
    assert (tmp_path / "confluence" / "since-2025-06-01" / "pages-0000.json").exists()
    assert result.records > 0


def test_analyze_counts_bodies():
    stats = analyze(iter([make_page(1), make_page(2, body=""), make_page(3)]))
    assert stats["pages"] == 3
    assert stats["pages_with_body_storage"] == 2
    assert stats["pct_with_body_storage"] == 66.7


@respx.mock
def test_a_cursor_that_does_not_advance_is_overridden(tmp_path):
    """A `_links.next` pointing at or behind the current start is an infinite crawl."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        start = int(params.get("start", 0))
        links: dict = {"base": BASE_URL}
        if start < 4:
            # the server keeps handing back the same offset
            links["next"] = f"/rest/api/content/search?cql=x&limit=2&start={start}"
        return httpx.Response(
            200,
            json={
                "results": [make_page(start), make_page(start + 1)],
                "totalSize": 6,
                "_links": links,
            },
        )

    respx.get(SEARCH).mock(side_effect=handler)
    result = connector(tmp_path, limit=2).run()

    assert [int(p["start"]) for p in seen] == [0, 2, 4]  # advanced by results received
    assert result.pages == 3
    assert any(e["kind"] == "cursor" for e in result.errors)


@respx.mock
def test_a_query_change_deletes_the_previous_query_pages(tmp_path):
    respx.get(SEARCH).mock(side_effect=paged_transport(85, 25))
    connector(tmp_path).run()
    assert len(list((tmp_path / "confluence").glob("pages-*.json"))) == 4

    respx.get(SEARCH).mock(side_effect=paged_transport(85, 100))
    second = connector(tmp_path, limit=100).run()

    assert sorted(f.name for f in (tmp_path / "confluence").glob("pages-*.json")) == [
        "pages-0000.json"
    ]
    assert second.stats["pages"] == 85


# --------------------------------------------------------------------------- kip keys


def titled(page_id: str, title: str) -> dict:
    page = make_page(1)
    page["id"] = page_id
    page["title"] = title
    return page


def test_kip_key_parsing():
    from brain.harvest.confluence import kip_key

    assert kip_key("KIP-848: The Next Generation of the Consumer Rebalance Protocol") == "KIP-848"
    assert kip_key("KIP-929 : Observer Replicas") == "KIP-929"
    assert kip_key("[DRAFT] KIP-1027 Add MockFixedKeyProcessorContext") == "KIP-1027"
    assert kip_key("Copy of KIP-848") == "KIP-848"
    assert kip_key("KIP Release Notes") is None
    assert kip_key("") is None


def test_kip_key_block_reports_collisions_and_unparseable_titles():
    stats = analyze(
        iter(
            [
                titled("1", "KIP-848: The Next Generation"),
                titled("2", "Copy of KIP-848: The Next Generation"),
                titled("3", "[DRAFT] KIP-848"),
                titled("4", "KIP-932: Queues for Kafka"),
                titled("5", "Kafka Improvement Proposals"),
                titled("6", "KIP index"),
                titled("7", "KIP 156 Add option dry run"),
            ]
        )
    )
    block = stats["kip_key"]

    assert block["distinct_keys"] == 2
    assert block["pages_without_key"] == 3  # 2 keyless + 1 space-separated
    assert block["pages_unparseable"] == 2
    assert [p["title"] for p in block["unparseable"]] == [
        "Kafka Improvement Proposals",
        "KIP index",
    ]
    assert block["colliding_keys"] == 1
    assert block["pages_in_collisions"] == 3
    assert [p["id"] for p in block["collisions"]["KIP-848"]] == ["1", "2", "3"]
    assert "KIP-932" not in block["collisions"]


def test_a_space_separated_title_is_recoverable_not_keyless():
    """ "KIP 156 …" carries a real number; canon should see it as a choice, not a loss."""
    from brain.harvest.confluence import spaced_kip_key

    assert spaced_kip_key("KIP 156 Add option dry run") == "KIP-156"
    assert spaced_kip_key("Kafka Improvement Proposals") is None

    stats = analyze(iter([titled("7", "KIP 141 - ProducerRecord: Add timestamp constructors")]))
    block = stats["kip_key"]
    assert block["pages_space_separated"] == 1
    assert block["space_separated"][0]["would_be_key"] == "KIP-141"
    assert block["unparseable"] == []


def test_the_cost_of_accepting_the_space_form_is_reported():
    """Widening the parser recovers pages but can walk into numbers already taken."""
    block = analyze(
        iter(
            [
                titled("1", "KIP-156: Add option dry run"),
                titled("2", "KIP 156 Add option dry run"),
                titled("3", "KIP 771: KRaft brokers"),
            ]
        )
    )["kip_key"]

    assert block["colliding_keys"] == 0  # strict: one page per key
    assert block["distinct_keys"] == 1
    assert block["if_space_form_accepted"]["distinct_keys"] == 2  # KIP-156 + KIP-771
    assert block["if_space_form_accepted"]["colliding_keys"] == 1  # …but KIP-156 now clashes
    assert block["if_space_form_accepted"]["pages_in_collisions"] == 2
