"""Connector plumbing: pacing, backoff, checkpoint persistence and resume."""

from __future__ import annotations

import json

import httpx
import pytest

from brain.harvest.base import (
    Checkpoint,
    HarvestError,
    HttpFetcher,
    Page,
    RetryPolicy,
    signature_of,
)


class FakeClock:
    """Deterministic monotonic clock that advances only when someone sleeps."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _fetcher(handler, clock: FakeClock, **kwargs) -> HttpFetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://example.test")
    return HttpFetcher(
        "https://example.test",
        client=client,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        policy=RetryPolicy(max_attempts=4, base_delay=2.0, jitter=0.0),
        **kwargs,
    )


def test_backoff_on_429_then_success():
    clock = FakeClock()
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(len(seen))
        if len(seen) <= 2:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json={"ok": True})

    f = _fetcher(handler, clock)
    assert f.get_json("/x") == {"ok": True}
    assert f.calls == 3
    assert f.retries == 2
    # exponential: 2s then 4s (jitter disabled); each backoff already covers the 1s pacing
    assert clock.slept == [2.0, 4.0]
    assert [e["kind"] for e in f.errors] == ["retry", "retry"]


def test_backoff_honours_retry_after_header():
    clock = FakeClock()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"ok": 1})

    f = _fetcher(handler, clock)
    assert f.get_json("/x") == {"ok": 1}
    assert clock.slept[0] == 7.0


def test_retry_on_timeout_then_success():
    clock = FakeClock()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("too slow", request=request)
        return httpx.Response(200, json={"ok": 1})

    f = _fetcher(handler, clock)
    assert f.get_json("/x") == {"ok": 1}
    assert f.retries == 1


def test_gives_up_after_max_attempts_and_records_a_fatal_error():
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    f = _fetcher(handler, clock)
    with pytest.raises(HarvestError, match="gave up after 4 attempts"):
        f.get_json("/x")
    assert f.calls == 4
    assert any(e["fatal"] for e in f.errors)


def test_4xx_is_not_retried():
    clock = FakeClock()
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="'connect' is a reserved JQL word")

    f = _fetcher(handler, clock)
    with pytest.raises(HarvestError, match="reserved JQL word"):
        f.get_json("/x")
    assert len(calls) == 1


def test_pacing_keeps_at_least_one_second_between_calls():
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": 1})

    f = _fetcher(handler, clock)
    f.get_json("/a")
    f.get_json("/b")
    f.get_json("/c")
    assert clock.slept == [1.0, 1.0]  # no sleep before the first call


# --------------------------------------------------------------------------- checkpoint


def test_checkpoint_round_trip_and_resume(tmp_path):
    path = tmp_path / "checkpoint.json"
    cp = Checkpoint.load(path, source="jira", signature="abc", query="jql")
    assert cp.pages == 0 and cp.cursor == {}

    page = Page(index=0, records=[{"k": 1}, {"k": 2}], path=tmp_path / "issues-0000.json")
    cp.advance(page=page, cursor={"start_at": 2}, total=10)
    assert json.loads(path.read_text())["cursor"] == {"start_at": 2}

    resumed = Checkpoint.load(path, source="jira", signature="abc", query="jql")
    assert resumed.pages == 1
    assert resumed.records == 2
    assert resumed.cursor == {"start_at": 2}
    assert resumed.total == 10
    assert resumed.files == ["issues-0000.json"]
    assert resumed.done is False


def test_checkpoint_with_a_different_signature_starts_over(tmp_path):
    path = tmp_path / "checkpoint.json"
    cp = Checkpoint.load(path, source="jira", signature="abc")
    cp.advance(page=Page(index=0, records=[{}]), cursor={"start_at": 1})
    cp.finish()

    other = Checkpoint.load(path, source="jira", signature="different")
    assert other.pages == 0
    assert other.done is False
    assert other.cursor == {}


def test_checkpoint_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "checkpoint.json"
    path.write_text("{not json", encoding="utf-8")
    cp = Checkpoint.load(path, source="git", signature="abc")
    assert cp.pages == 0


def test_signature_is_stable_and_query_sensitive():
    a = signature_of({"jql": "project = KAFKA", "max": 500})
    b = signature_of({"max": 500, "jql": "project = KAFKA"})
    c = signature_of({"jql": "project = FLINK", "max": 500})
    assert a == b
    assert a != c


def test_every_connector_satisfies_the_protocol(tmp_path):
    """The Connector protocol is the contract; keep it load-bearing, not decorative."""
    from brain.harvest.base import Connector
    from brain.harvest.confluence import ConfluenceConnector
    from brain.harvest.git import GitConnector
    from brain.harvest.jira import JiraConnector

    for cls in (JiraConnector, ConfluenceConnector, GitConnector):
        assert isinstance(cls(tmp_path), Connector), cls.__name__


# --------------------------------------------------------------------------- stale pages


def test_checkpoint_pages_reads_the_file_list_not_a_glob(tmp_path):
    from brain.harvest.base import checkpoint_pages

    for name in ("issues-0000.json", "issues-0001.json", "issues-0002.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    (tmp_path / "checkpoint.json").write_text(
        json.dumps({"files": ["issues-0000.json", "issues-0001.json"]}), encoding="utf-8"
    )

    # issues-0002.json is on disk but not in the checkpoint: it belongs to an older query
    assert [p.name for p in checkpoint_pages(tmp_path)] == [
        "issues-0000.json",
        "issues-0001.json",
    ]


def test_checkpoint_pages_is_empty_without_a_checkpoint(tmp_path):
    from brain.harvest.base import checkpoint_pages

    (tmp_path / "issues-0000.json").write_text("{}", encoding="utf-8")
    assert checkpoint_pages(tmp_path) == []


def test_checkpoint_pages_skips_files_the_checkpoint_names_but_disk_lost(tmp_path):
    from brain.harvest.base import checkpoint_pages

    (tmp_path / "checkpoint.json").write_text(
        json.dumps({"files": ["issues-0000.json"]}), encoding="utf-8"
    )
    assert checkpoint_pages(tmp_path) == []


def test_a_signature_change_hands_back_the_old_pages_as_stale(tmp_path):
    path = tmp_path / "checkpoint.json"
    cp = Checkpoint.load(path, source="jira", signature="abc")
    cp.advance(page=Page(index=0, records=[{}], path=tmp_path / "issues-0000.json"), cursor={})
    cp.advance(page=Page(index=1, records=[{}], path=tmp_path / "issues-0001.json"), cursor={})

    fresh = Checkpoint.load(path, source="jira", signature="different")
    assert fresh.stale_files == ["issues-0000.json", "issues-0001.json"]
    assert fresh.files == []
