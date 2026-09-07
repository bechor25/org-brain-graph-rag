"""The resolve write surface without a database: which Cypher, with which rows.

`tests/live/test_resolve_live.py` proves the merges against a real Neo4j. What this file
catches is the pair of mistakes that layer can make in silence: a `SAME_AS` edge that
records a decision an agent made without naming the agent, and an `IndexMeta` node whose
`name` does not match the index it claims to describe.
"""

from __future__ import annotations

from typing import Any

from brain.graph.context import GraphContext
from brain.resolve import graph as resolve_graph
from brain.resolve.models import make_pair


class FakeClient:
    def __init__(self, reads: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.reads = reads or {}

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        self.writes.append((cypher, params))
        return dict.fromkeys(("properties_set", "relationships_deleted", "nodes_deleted"), 0)

    def write_batched(self, cypher: str, rows, batch_size: int = 1000) -> dict[str, int]:
        return self.write(cypher, rows=list(rows))

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        for fragment, rows in self.reads.items():
            if fragment in cypher:
                return rows
        return []


def ctx(prefix: str = "", **kw) -> GraphContext:
    return GraphContext(FakeClient(**kw), prefix=prefix)


def pair(tier: int, **kw: Any):
    return make_pair(
        "jira:a",
        "jira:b",
        kind="person",
        block="person",
        tier=tier,
        rule="adjudicator_same" if tier == 3 else "email",
        score=0.9,
        reason="r",
        **kw,
    )


# ------------------------------------------------------------------------- SAME_AS


def test_a_same_as_an_agent_decided_names_the_batch_the_model_and_the_time():
    """Conventions rule 3: an LLM-derived edge carries its provenance or it is a bug."""
    c = ctx()
    resolve_graph.write_same_as(
        c,
        "Person",
        [pair(3, batch_id="shard-02/007", model="opus:entity-adjudicator")],
        stamp="2026-09-07T00:00:00+00:00",
    )
    rows = c.client.writes[-1][1]["rows"]

    assert rows[0]["props"]["batch_id"] == "shard-02/007"
    assert rows[0]["props"]["model"] == "opus:entity-adjudicator"
    assert rows[0]["props"]["extracted_at"] == "2026-09-07T00:00:00+00:00"


def test_a_same_as_code_decided_carries_no_model_and_says_so_by_omission():
    c = ctx()
    resolve_graph.write_same_as(c, "Person", [pair(1)], stamp="2026-09-07T00:00:00+00:00")
    props = c.client.writes[-1][1]["rows"][0]["props"]

    assert props["tier"] == 1 and props["rule"] == "email"
    assert "model" not in props and "batch_id" not in props


# ----------------------------------------------------------------------- IndexMeta


def test_the_index_meta_node_is_named_after_the_index_in_this_namespace():
    c = ctx("_Res", reads={"IS NOT NULL": [{"live": 42}]})
    meta = resolve_graph.write_index_meta(
        c, "Person", model="bge-m3", dim=1024, now="2026-09-07T00:00:00+00:00"
    )

    assert meta["name"] == "_Resperson_embedding"
    assert c.client.writes[-1][1]["name"] == "_Resperson_embedding"
    assert "`_ResIndexMeta`" in c.client.writes[-1][0]


def test_the_index_meta_counts_the_vectors_that_are_there_now():
    c = ctx(
        reads={
            "IS NOT NULL": [{"live": 1229}],
            "SHOW INDEXES": [{"name": "entity_embedding", "state": "ONLINE"}],
        }
    )
    meta = resolve_graph.write_index_meta(
        c, "Entity", model="bge-m3", dim=1024, now="2026-09-07T00:00:00+00:00"
    )

    assert meta == {
        "name": "entity_embedding",
        "label": "Entity",
        "model": "bge-m3",
        "dim": 1024,
        "similarity": "cosine",
        "live": 1229,
        "state": "ONLINE",
        "updated_at": "2026-09-07T00:00:00+00:00",
    }


def test_dropping_the_resolve_schema_takes_the_index_meta_with_it():
    """A scratch namespace that leaves an `IndexMeta` behind makes the next smoke run
    describe an index that no longer exists."""
    c = ctx("_Res")
    resolve_graph.drop_resolve_schema(c)
    written = " ".join(cypher for cypher, _ in c.client.writes)

    assert "`_ResIndexMeta`" in written
    assert "DROP INDEX `_Resperson_embedding`" in written
    assert "DROP INDEX `_Resentity_embedding`" in written


def test_an_index_that_does_not_exist_reads_as_none_and_asks_the_server_only_about_itself():
    """`db.awaitIndexes` waits for every index in the database, so a resolve run that used
    it to check its own index would fail on another namespace's half-built one."""
    c = ctx("_Res")
    assert resolve_graph.index_status(c, "Person") is None

    c = ctx("_Res", reads={"SHOW INDEXES": [{"name": "_Resperson_embedding", "state": "ONLINE"}]})
    assert resolve_graph.index_status(c, "Person")["state"] == "ONLINE"


class SequencedClient(FakeClient):
    """`SHOW INDEXES` answers differently each time it is asked."""

    def __init__(self, states: list[str]) -> None:
        super().__init__()
        self.states = list(states)
        self.asked = 0

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if "SHOW INDEXES" not in cypher:
            return []
        self.asked += 1
        state = self.states.pop(0) if self.states else "ONLINE"
        return [] if state == "MISSING" else [{"name": params["name"], "state": state}]


def test_the_wait_polls_this_index_alone_until_it_is_online():
    """`db.awaitIndexes` waits for every index in the database — including the ones another
    agent's live suite left at POPULATING, which is a wait this step can neither shorten
    nor be right about."""
    c = GraphContext(SequencedClient(["POPULATING", "POPULATING", "ONLINE"]))
    assert resolve_graph.await_index(c, "Person", sleep_s=0) == "ONLINE"
    assert c.client.asked == 3
    assert not c.client.writes  # it asks, it never calls a procedure


def test_a_stuck_index_is_raised_not_read_from():
    import pytest

    c = GraphContext(SequencedClient(["POPULATING"] * 50))
    with pytest.raises(resolve_graph.ResolveGraphError, match="POPULATING"):
        resolve_graph.await_index(c, "Entity", timeout_s=0, sleep_s=0)


def test_creating_the_schema_reports_the_state_it_waited_for():
    c = GraphContext(SequencedClient(["ONLINE"]))
    assert resolve_graph.apply_resolve_schema(c, "Person", 1024) == {
        "index": "person_embedding",
        "dim": 1024,
        "state": "ONLINE",
    }
    assert "db.awaitIndexes" not in " ".join(cypher for cypher, _ in c.client.writes)
