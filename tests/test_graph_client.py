"""`GraphClient.explain()` without a database: the contract the Cypher guard depends on.

The live half (a real plan from a real server) is in `tests/live/test_graph_client_live.py`
and in the guard's live suite. What is asserted here is the shape of the seam: the plan is
a dict or `None`, and a driver failure arrives as a `Neo4jError` rather than as something
the guard has to guess at — which is what makes `except Neo4jError` in the guard the right
catch rather than a superstition.
"""

from __future__ import annotations

import pytest
from neo4j import Query
from neo4j.exceptions import DatabaseError

from brain.graph.client import GraphClient


class _FakeSummary:
    def __init__(self, plan: object) -> None:
        self.plan = plan


class _FakeResult:
    def __init__(self, plan: object) -> None:
        self.summary = _FakeSummary(plan)
        self.records: list[object] = []


class _FakeDriver:
    """Records what it was asked and returns what the test wants."""

    def __init__(self, plan: object = None, error: Exception | None = None) -> None:
        self.plan = plan
        self.error = error
        self.calls: list[tuple[object, dict, object]] = []

    def execute_query(self, cypher, params, database_=None, routing_=None):
        self.calls.append((cypher, params, routing_))
        if self.error is not None:
            raise self.error
        return _FakeResult(self.plan)


def _client(driver: _FakeDriver) -> GraphClient:
    client = object.__new__(GraphClient)
    client._driver = driver  # noqa: SLF001 - constructing without opening a connection
    client._db = "neo4j"  # noqa: SLF001
    return client


def test_explain_returns_the_plan_under_read_routing() -> None:
    from neo4j import RoutingControl

    driver = _FakeDriver(plan={"operatorType": "ProduceResults@neo4j", "children": []})
    plan = _client(driver).explain(Query("EXPLAIN MATCH (n) RETURN n", timeout=10.0), key="KAFKA-1")
    assert plan == {"operatorType": "ProduceResults@neo4j", "children": []}
    cypher, params, routing = driver.calls[0]
    assert params == {"key": "KAFKA-1"}
    assert routing is RoutingControl.READ
    assert getattr(cypher, "timeout", None) == 10.0, "the timeout reaches the server"


def test_a_statement_without_a_plan_is_none_not_a_crash() -> None:
    """Some administrative statements plan to nothing; the guard reads that as an abstention."""
    assert _client(_FakeDriver(plan=None)).explain("EXPLAIN SHOW INDEXES") is None


def test_a_planner_failure_stays_a_neo4j_error() -> None:
    """Not swallowed here: the guard turns it into `{error, hint}`, and only it knows how."""
    driver = _FakeDriver(error=DatabaseError("Neo.DatabaseError.Statement.ExecutionFailed"))
    with pytest.raises(DatabaseError):
        _client(driver).explain("EXPLAIN MATCH (n) RETURN n")
