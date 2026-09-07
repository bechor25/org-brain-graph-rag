"""The retrieval trace: every call, one line, and never a raise."""

from __future__ import annotations

from pathlib import Path

from brain.retrieve import log as retrieve_log
from brain.retrieve.types import Item, Result


def result() -> Result:
    return Result(
        strategy="s1",
        items=[Item(kind="Chunk", key="c1", score=0.9), Item(kind="WorkItem", key="KAFKA-1")],
        cypher_used=["MATCH (c:Chunk) RETURN c"],
        latency_ms=42,
        route={"strategy": "s1", "reason": "test", "confidence": 1.0},
    )


def test_log_call_writes_the_spec_fields(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "retrieval.jsonl"
    record = retrieve_log.log_call("why?", result(), mode="cli", tokens_out=123, path=path)
    assert set(record) >= {
        "ts",
        "question",
        "strategy",
        "cypher",
        "latency_ms",
        "hit_ids",
        "tokens_out",
        "mode",
    }
    assert record["hit_ids"] == ["Chunk:c1", "WorkItem:KAFKA-1"]
    assert record["route"]["strategy"] == "s1"
    (line,) = retrieve_log.read_log(path)
    assert line == record


def test_appends_rather_than_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "retrieval.jsonl"
    for i in range(3):
        retrieve_log.log_call(f"q{i}", result(), path=path)
    assert [r["question"] for r in retrieve_log.read_log(path)] == ["q0", "q1", "q2"]


def test_hebrew_is_written_readable(tmp_path: Path) -> None:
    path = tmp_path / "retrieval.jsonl"
    retrieve_log.log_call("למה נבחר?", result(), path=path)
    assert "למה נבחר?" in path.read_text(encoding="utf-8")


def test_an_unwritable_log_is_not_a_retrieval_failure(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    assert retrieve_log.append({"a": 1}, blocked / "retrieval.jsonl") is False
    assert retrieve_log.last_error


def test_read_log_skips_a_half_written_line(tmp_path: Path) -> None:
    path = tmp_path / "retrieval.jsonl"
    path.write_text('{"question": "ok"}\n{"question": "trunc\n', encoding="utf-8")
    assert [r["question"] for r in retrieve_log.read_log(path)] == ["ok"]


def test_missing_log_reads_empty(tmp_path: Path) -> None:
    assert retrieve_log.read_log(tmp_path / "nope.jsonl") == []
