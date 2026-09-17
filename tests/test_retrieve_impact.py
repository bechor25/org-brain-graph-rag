"""`impact` without a database: the queries' determinism and what a test row claims to be.

Two failures the Plan 2 review found are visible in the text of the Cypher and in the shape
of one item, which is exactly what a stub context can hold still:

* the file slice `[..$files]` was taken from an *unordered* `collect`, so two runs of the
  same question could pick two different twenty-file neighbourhoods;
* a `Test` node came back as `kind="WorkItem"` carrying a test execution key as its
  provenance — a citation that looks like a quoted work item and is neither.

`tests/live/test_retrieve_live.py` proves the same queries run; this proves they are the
queries we meant.
"""

from __future__ import annotations

from typing import Any

from brain.retrieve.impact import (
    _commits_on_the_same_files,
    _documents,
    _related_work_items,
    _test_item,
    _tests_with_last_run,
)


class StubCtx:
    """Enough `RetrieveContext` for a query builder: labels, a prefix and a recorder."""

    prefix = ""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.cyphers: list[str] = []

    def label(self, label: str) -> str:
        return label

    def read(self, cypher: str, **_kwargs: Any) -> list[dict[str, Any]]:
        self.cyphers.append(cypher)
        return self.rows


# ------------------------------------------------------------------------- determinism


def test_the_file_slice_is_taken_from_an_ordered_collect() -> None:
    ctx = StubCtx()
    _rows, _paths, cypher = _commits_on_the_same_files(ctx, ["KAFKA-1"])
    assert "ORDER BY path" in cypher
    assert cypher.index("ORDER BY path") < cypher.index("[..$files]")
    assert "ORDER BY shared_files DESC, o.at DESC, o.sha" in cypher


def test_every_capped_query_orders_by_a_key_before_it_cuts() -> None:
    """A `LIMIT` over rows Neo4j promises no order for is a coin toss with a cap on it."""
    ctx = StubCtx()
    for cypher in (
        _related_work_items(ctx, "WorkItem", "KAFKA-1", 2)[1],
        _documents(ctx, "WorkItem", "KAFKA-1", 2)[1],
        _tests_with_last_run(ctx, ["KAFKA-1"])[1],
    ):
        head, _, tail = cypher.rpartition("LIMIT")
        assert "ORDER BY" in head, cypher
        assert tail.strip().startswith("$limit")


def test_the_last_run_of_a_test_is_picked_by_a_total_order() -> None:
    _rows, cypher = _tests_with_last_run(StubCtx(), ["KAFKA-1"])
    assert "ORDER BY r.at DESC, x.key DESC" in cypher


# ------------------------------------------------------------------------- the test row


def _row(**kw: Any) -> dict[str, Any]:
    base = {
        "test": "XT-12",
        "title": "consumer rebalance smoke",
        "status": "Ready",
        "covers": "KAFKA-1",
        "last_run": {"status": "FAIL", "at": "2024-03-01T10:00:00Z", "execution": "XE-3"},
        "synthetic": True,
    }
    return {**base, **kw}


def test_a_test_execution_is_a_row_and_says_so() -> None:
    item = _test_item(_row(), 0.8)
    assert item.kind == "Row"
    assert item.key == "XT-12"
    assert item.props["category"] == "test"
    assert item.props["last_run_status"] == "FAIL"
    assert item.props["covers"] == "KAFKA-1"


def test_a_test_rows_provenance_is_the_run_not_a_quoted_chunk() -> None:
    """Task 4 counts row-backed citations apart from quoted ones; this is one of them."""
    provenance = _test_item(_row(), 0.8).provenance
    assert len(provenance) == 1
    assert provenance[0].source_kind == "row"
    assert provenance[0].chunk_id is None
    assert provenance[0].source == "XE-3"
    assert provenance[0].quote == "FAIL at 2024-03-01T10:00:00Z"


def test_a_test_that_never_ran_says_never_and_cites_itself() -> None:
    item = _test_item(_row(last_run=None), 0.5)
    assert item.provenance[0].quote == "never run"
    assert item.provenance[0].source == "XT-12"
    assert item.props["last_run_status"] is None
    assert "never" in item.snippet
