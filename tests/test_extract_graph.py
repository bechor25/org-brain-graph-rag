"""The extract write surface without a database — specifically, what it waits for.

`tests/live/test_extract_live.py` proves the merge against a real Neo4j. What this file
catches is the thing that layer cannot: *which* indexes this step asks the server about.
`CALL db.awaitIndexes(300)` is database-wide, so a step that used it to wait for its own
three indexes would sit behind — and fail on — every other namespace's half-built one.
"""

from __future__ import annotations

from typing import Any

from brain.extract import graph as extract_graph
from brain.graph.context import GraphContext


class FakeClient:
    def __init__(self, reads: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.reads_seen: list[tuple[str, dict[str, Any]]] = []
        self.reads = reads or {}

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        self.writes.append((cypher, params))
        return dict.fromkeys(("properties_set", "nodes_created"), 0)

    def write_batched(self, cypher: str, rows, batch_size: int = 1000) -> dict[str, int]:
        return self.write(cypher, rows=list(rows))

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.reads_seen.append((cypher, params))
        for fragment, rows in self.reads.items():
            if fragment in cypher:
                return rows
        return []


class SequencedClient(FakeClient):
    """`SHOW INDEXES` answers differently each time it is asked."""

    def __init__(self, rounds: list[dict[str, str]]) -> None:
        super().__init__()
        self.rounds = list(rounds)
        self.asked = 0

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.reads_seen.append((cypher, params))
        if "SHOW INDEXES" not in cypher:
            return []
        self.asked += 1
        states = self.rounds.pop(0) if self.rounds else {}
        return [{"name": n, "state": s} for n, s in states.items() if n in params["names"]]


def prefixed(prefix: str = "") -> list[str]:
    return [f"{prefix}{name}" for name in extract_graph.SCHEMA_NAMES]


def all_online(prefix: str = "") -> dict[str, str]:
    return dict.fromkeys(prefixed(prefix), "ONLINE")


def test_the_wait_asks_about_this_step_s_three_names_and_no_others():
    c = GraphContext(SequencedClient([all_online("_X")]), prefix="_X")

    extract_graph.await_extract_schema(c, sleep_s=0)

    cypher, params = c.client.reads_seen[-1]
    assert "SHOW INDEXES" in cypher
    assert "$names" in cypher
    assert params["names"] == prefixed("_X")
    assert not c.client.writes  # it asks, it never calls a procedure


def test_the_wait_polls_until_every_one_of_them_is_online():
    rounds = [
        {},
        {prefixed()[0]: "ONLINE", prefixed()[1]: "POPULATING", prefixed()[2]: "ONLINE"},
        all_online(),
    ]
    c = GraphContext(SequencedClient(rounds))

    result = extract_graph.await_extract_schema(c, sleep_s=0)

    assert c.client.asked == 3
    assert result["ready"] is True
    assert result["timed_out"] is False
    assert result["states"] == all_online()


def test_an_index_that_never_comes_online_is_reported_not_raised():
    """Unlike `brain resolve`, which reads its index with `db.index.vector.queryNodes` and
    gets a wrong answer from a half-built one, this step's indexes are lookups: a
    POPULATING range index costs a scan, and the uniqueness constraint is enforced from
    the moment it exists. So a slow index is a line in the report, not a dead step."""
    stuck = {prefixed()[0]: "ONLINE", prefixed()[1]: "POPULATING", prefixed()[2]: "FAILED"}
    c = GraphContext(SequencedClient([stuck] * 5))

    result = extract_graph.await_extract_schema(c, timeout_s=0, sleep_s=0)

    assert result["timed_out"] is True
    assert result["ready"] is False
    assert result["states"] == stuck
    assert result["not_online"] == sorted([prefixed()[1], prefixed()[2]])
    assert "POPULATING" in result["warning"] and "FAILED" in result["warning"]


def test_an_index_the_server_has_never_heard_of_reads_as_missing():
    c = GraphContext(SequencedClient([{prefixed()[0]: "ONLINE"}] * 3))

    result = extract_graph.await_extract_schema(c, timeout_s=0, sleep_s=0)

    assert result["states"][prefixed()[1]] == "MISSING"
    assert result["online"] == 1


def test_creating_the_schema_never_waits_on_the_whole_database():
    """The rule from the Plan 1 closing review: no database-wide operation in step code."""
    c = GraphContext(SequencedClient([all_online("_X")]), prefix="_X")

    stats = extract_graph.apply_extract_schema(c)

    assert stats["constraints"] == 1 and stats["indexes"] == 2
    assert stats["await"]["ready"] is True
    written = " ".join(cypher for cypher, _ in c.client.writes)
    assert "db.awaitIndexes" not in written
    assert not any("db.awaitIndexes" in cypher for cypher, _ in c.client.reads_seen)
