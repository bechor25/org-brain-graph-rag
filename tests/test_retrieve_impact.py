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
    MAX_FAILING_TESTS,
    _commits_on_the_same_files,
    _component_test_counts,
    _documents,
    _related_work_items,
    _test_item,
    _tests_on_component,
    _tests_with_last_run,
    impact,
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


# ------------------------------------------------------------------ tests on a component


def test_the_component_test_layer_is_not_a_slice_of_the_open_items() -> None:
    """The Plan 2 gate bug: `impact("clients")` said `0 tests` while 284 cover the component.

    The open-items layer asks "which tests cover the forty work items this traversal
    surfaced", so for a component it answers about a sample of a sample. Coverage of a
    component is its own question, and this is its own query: keyed on the component name,
    never on `$keys`.
    """
    _rows, cypher = _tests_on_component(StubCtx(), "clients")
    assert "$name" in cypher
    assert "$keys" not in cypher
    assert "-[:IN_COMPONENT]->(k)" in cypher
    assert "(t:Test)-[:TESTS]->" in cypher
    # a test covering two items of the same component is one test, not two
    assert "WITH DISTINCT t" in cypher


def test_the_failing_component_tests_are_ordered_before_they_are_cut() -> None:
    _rows, cypher = _tests_on_component(StubCtx(), "clients")
    order = "ORDER BY coalesce(last_run.at, '') DESC, t.key"
    assert order in cypher
    assert cypher.index(order) < cypher.index("[..$limit]")
    assert "ORDER BY r.at DESC, x.key DESC" in cypher


def test_the_component_counts_are_exact_and_the_failing_list_is_one_total_order() -> None:
    """Counts come off a grouped `count(*)`; only the failing list is capped."""
    rows = [
        {
            "status": "FAIL",
            "n": 3,
            "failing": [
                {"test": "XT-9", "last_run": {"at": "2024-03-01T10:00:00Z"}},
                {"test": "XT-2", "last_run": {"at": "2024-03-02T10:00:00Z"}},
                {"test": "XT-1", "last_run": {"at": "2024-03-02T10:00:00Z"}},
            ],
        },
        {"status": "PASS", "n": 200, "failing": []},
        {"status": "never", "n": 81, "failing": []},
    ]
    counts = _component_test_counts(rows)
    assert counts["total"] == 284
    assert counts["failing_total"] == 3
    assert counts["by_status"] == {"FAIL": 3, "PASS": 200, "never": 81}
    # newest failure first, `test` to break the tie — the order the Cypher used, restored
    # after the status buckets were merged
    assert [t["test"] for t in counts["failing"]] == ["XT-1", "XT-2", "XT-9"]


def test_the_failing_list_is_capped() -> None:
    rows = [
        {
            "status": "FAIL",
            "n": 50,
            "failing": [
                {"test": f"XT-{i:03d}", "last_run": {"at": "2024-03-01T10:00:00Z"}}
                for i in range(50)
            ],
        }
    ]
    assert len(_component_test_counts(rows)["failing"]) == MAX_FAILING_TESTS


def test_a_failing_component_test_is_a_row_that_cites_its_run() -> None:
    item = _test_item(
        _row(covers=None),
        0.85,
        category="failing_test_on_component",
        scope="in component clients",
    )
    assert item.kind == "Row"
    assert item.props["category"] == "failing_test_on_component"
    assert "covers" not in item.props
    assert item.snippet.startswith("in component clients; last run FAIL")
    assert item.provenance[0].source_kind == "row"
    assert item.provenance[0].source == "XE-3"


# ------------------------------------------------------------------------- the fake graph


class FakeGraph:
    """A graph small enough to state in full, answering each query by its own shape.

    `impact` runs six or seven queries and the interesting behaviour is what it does *with*
    the rows — which layer a count lands in, which items exist, what the head row claims.
    An unrecognised query raises rather than returning `[]`: a layer that silently answers
    "nothing" is precisely the failure this fix is about.
    """

    prefix = ""

    def __init__(self, anchor: str = "component") -> None:
        self.anchor = anchor
        self.cyphers: list[str] = []

    def label(self, label: str) -> str:
        return label

    # -- rows ---------------------------------------------------------------------------

    ANCHOR = {
        "component": {"label": "Component", "key": "clients", "title": "clients"},
        "workitem": {"label": "WorkItem", "key": "KAFKA-100", "title": "new protocol"},
    }
    RELATED = [
        {
            "key": "KAFKA-101",
            "title": "rebalance storm after coordinator restart",
            "type": "Bug",
            "status": "Open",
            "resolution": None,
            "synthetic": False,
            "hops": 1,
        }
    ]
    OPEN_ITEM_TESTS = [
        {
            "test": "XT-12",
            "title": "rebalance smoke",
            "status": "Active",
            "covers": "KAFKA-101",
            "last_run": {"status": "PASS", "at": "2024-04-01T09:00:00Z", "execution": "XE-8"},
            "synthetic": True,
        }
    ]
    COMPONENT_TESTS = [
        {
            "status": "FAIL",
            "n": 2,
            "failing": [
                {
                    "test": "XT-70",
                    "title": "consumer fetch session",
                    "status": "Active",
                    "last_run": {
                        "status": "FAIL",
                        "at": "2024-04-02T09:00:00Z",
                        "execution": "XE-9",
                    },
                    "synthetic": True,
                },
                {
                    "test": "XT-12",
                    "title": "rebalance smoke",
                    "status": "Active",
                    "last_run": {
                        "status": "FAIL",
                        "at": "2024-04-02T09:00:00Z",
                        "execution": "XE-9",
                    },
                    "synthetic": True,
                },
            ],
        },
        {"status": "PASS", "n": 201, "failing": []},
        {"status": "never", "n": 81, "failing": []},
    ]
    DOCS = [
        {
            "key": "KIP-5",
            "title": "new consumer group protocol",
            "kind": "KIP",
            "hops": 2,
            "synthetic": False,
        }
    ]
    CHUNKS = [
        {"key": "KAFKA-101", "chunks": [{"id": "c1", "text": "storm", "kind": "description"}]}
    ]

    # -- dispatch -----------------------------------------------------------------------

    def read(self, cypher: str, **_kwargs: Any) -> list[dict[str, Any]]:
        self.cyphers.append(cypher)
        want = self.ANCHOR[self.anchor]
        if cypher.startswith("MATCH (n:"):
            return [] if f"MATCH (n:{want['label']} " not in cypher else [{**want}]
        if "-[:IN_COMPONENT]->(k)" in cypher:
            return self.COMPONENT_TESTS
        if "AS hops" in cypher and "(w:WorkItem)" in cypher:
            return self.RELATED
        if "AS hops" in cypher and "(d:Document)" in cypher:
            return self.DOCS
        if "[:TESTS]->(w:WorkItem {`key`: k})" in cypher:
            return self.OPEN_ITEM_TESTS
        if ":RESOLVES]->" in cypher:
            return []
        if "`parent_key`: k" in cypher:
            return self.CHUNKS
        raise AssertionError(f"no fake rows for this query:\n{cypher}")


def _categories(result: Any) -> list[str]:
    return [i.props.get("category") for i in result.items]


def test_a_component_anchor_reports_both_test_layers() -> None:
    graph = FakeGraph("component")
    result = impact(graph, "clients", depth=2, log=False)
    head = result.items[0].props
    assert head["tests_on_component"] == 284
    assert head["tests_on_component_failing"] == 2
    assert head["tests_on_component_by_status"] == {"FAIL": 2, "PASS": 201, "never": 81}
    assert head["tests_on_open_items"] == 1
    assert "284 tests on the component" in result.items[0].snippet
    categories = _categories(result)
    assert categories.count("tests_on_component") == 1
    assert categories.count("failing_test_on_component") == 2
    assert categories.count("test") == 1, "the open-items layer is unchanged"


def test_the_failing_component_tests_keep_their_order_in_the_items() -> None:
    result = impact(FakeGraph("component"), "clients", depth=2, log=False)
    failing = [i for i in result.items if i.props.get("category") == "failing_test_on_component"]
    assert [i.key for i in failing] == ["XT-12", "XT-70"]
    assert all(p.source_kind == "row" for i in failing for p in i.provenance)


def test_a_work_item_anchor_keeps_the_current_semantics() -> None:
    graph = FakeGraph("workitem")
    result = impact(graph, "KAFKA-100", depth=2, log=False)
    assert not any("-[:IN_COMPONENT]->(k)" in c for c in graph.cyphers)
    head = result.items[0].props
    assert "tests_on_component" not in head
    assert head["tests_on_open_items"] == 1
    assert "tests_on_component" not in _categories(result)
    assert "test" in _categories(result)
