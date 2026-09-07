"""The index set `brain index` guarantees, without a database.

The point is not to re-test Neo4j — `tests/live/test_index_live.py` does that — but to
pin the three things this layer can get wrong silently: an index created without
`IF NOT EXISTS` (a rerun that errors instead of doing nothing), a range index created
under a *new* name beside the identical one `brain load` already made (two indexes, one
schema, twice the write cost), and a spec that drifts from the name the census reports.
"""

from __future__ import annotations

from typing import Any

import pytest

from brain.graph.context import GraphContext
from brain.index import schema as index_schema


class FakeClient:
    def __init__(self, reads: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.reads_seen: list[str] = []
        self.reads = reads or {}
        self.added = 0

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        self.writes.append((cypher, params))
        added = self.added if cypher.lstrip().upper().startswith("CREATE") else 0
        return {"indexes_added": added, "constraints_added": 0}

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.reads_seen.append(cypher)
        for fragment, rows in self.reads.items():
            if fragment in cypher:
                return rows
        return []


def ctx(prefix: str = "", **kw) -> GraphContext:
    return GraphContext(FakeClient(**kw), prefix=prefix)


def cyphers(c: GraphContext) -> list[str]:
    return [w[0] for w in c.client.writes]


# ------------------------------------------------------------------------------- the set


def test_every_index_the_planner_asked_for_is_declared():
    names = {s.name for s in index_schema.MANAGED}
    assert names == {
        "chunk_embedding",
        "entity_embedding",
        "community_embedding",
        "workitem_text",
        "document_text",
        "entity_text",
        "person_text",
        "chunk_text",
        "brain_statuschange_at_idx",
        "brain_workitem_created_idx",
        "brain_commit_at_idx",
    }


def test_fulltext_specs_carry_the_properties_from_the_brief():
    by_name = {s.name: s for s in index_schema.MANAGED}
    assert by_name["workitem_text"].properties == ("title", "description")
    assert by_name["document_text"].properties == ("title", "body_md")
    assert by_name["entity_text"].properties == ("name", "description")
    assert by_name["person_text"].properties == ("display", "aliases")
    assert by_name["chunk_text"].properties == ("text",)


def test_range_specs_reuse_the_names_brain_load_already_created():
    """A second index over the same (label, property) is pure cost. `brain load`'s schema
    owns these names; repeating them is what makes `brain index` a no-op on a loaded graph."""
    from brain.graph.schema import _index_name

    for label, prop in (("StatusChange", "at"), ("WorkItem", "created"), ("Commit", "at")):
        assert any(s.name == _index_name(label, prop) for s in index_schema.MANAGED)


def test_person_embedding_is_observed_but_never_created_here():
    """Decision 1 does not list it; `brain resolve` owns it. The census still reports it."""
    assert "person_embedding" not in {s.name for s in index_schema.MANAGED}
    assert "person_embedding" in {s.name for s in index_schema.OBSERVED}


# ------------------------------------------------------------------------------- the DDL


def test_every_statement_is_idempotent():
    for _spec, cypher in index_schema.statements(ctx(), dim=1024):
        assert "IF NOT EXISTS" in cypher


def test_fulltext_uses_on_each_over_every_property():
    stmts = dict((s.name, c) for s, c in index_schema.statements(ctx(), dim=8))
    assert (
        "CREATE FULLTEXT INDEX `workitem_text` IF NOT EXISTS "
        "FOR (n:`WorkItem`) ON EACH [n.`title`, n.`description`]" == stmts["workitem_text"]
    )
    assert "ON EACH [n.`text`]" in stmts["chunk_text"]


def test_vector_statements_carry_the_dimension_and_cosine():
    stmts = dict((s.name, c) for s, c in index_schema.statements(ctx(), dim=1024))
    assert "`vector.dimensions`: 1024" in stmts["chunk_embedding"]
    assert "`vector.similarity_function`: 'cosine'" in stmts["community_embedding"]


def test_a_bad_dimension_is_a_hard_error_not_a_broken_index():
    with pytest.raises(ValueError):
        index_schema.statements(ctx(), dim=0)
    with pytest.raises(ValueError):
        index_schema.statements(ctx(), dim="1024")  # type: ignore[arg-type]


def test_the_namespace_reaches_both_the_index_name_and_the_label():
    stmts = dict((s.name, c) for s, c in index_schema.statements(ctx(prefix="_T"), dim=8))
    assert "`_Tentity_text`" in stmts["entity_text"]
    assert "(n:`_TEntity`)" in stmts["entity_text"]


# ----------------------------------------------------------------------------- apply/wait


def online_rows() -> dict[str, list[dict[str, Any]]]:
    return {
        "SHOW INDEXES YIELD name, state": [
            {"name": s.name, "state": "ONLINE"} for s in index_schema.MANAGED
        ]
    }


def test_apply_creates_then_waits_and_reports_which_were_new():
    c = ctx(reads=online_rows())
    c.client.added = 1
    out = index_schema.apply_indexes(c, dim=1024)
    assert out["created"] == len(index_schema.MANAGED)
    assert out["existing"] == 0
    assert out["await"]["timed_out"] is False
    assert out["await"]["not_online"] == {}


def test_a_rerun_creates_nothing():
    c = ctx(reads=online_rows())
    c.client.added = 0
    out = index_schema.apply_indexes(c, dim=1024)
    assert out["created"] == 0
    assert out["existing"] == len(index_schema.MANAGED)
    assert [name for name, was_new in out["by_index"].items() if was_new] == []


# ----------------------------------------------------------------------------------- wait


def test_the_wait_never_calls_the_database_wide_await():
    """`db.awaitIndexes()` waits for — and raises on — every index in the database,
    including the ones other steps' live tests are half-way through building."""
    c = ctx(reads=online_rows())
    index_schema.apply_indexes(c, dim=1024)
    assert not any("awaitIndexes" in q for q in cyphers(c))
    assert not any("awaitIndexes" in q for q in c.client.reads_seen)


def test_the_wait_polls_until_every_managed_index_is_online():
    states = ["POPULATING", "POPULATING", "ONLINE"]
    slept: list[float] = []

    class Eventually(FakeClient):
        def read(self, cypher: str, **params: Any):
            state = states.pop(0) if len(states) > 1 else states[0]
            return [{"name": s.name, "state": state} for s in index_schema.MANAGED]

    out = index_schema.await_indexes(GraphContext(Eventually()), poll_s=0.01, sleep=slept.append)
    assert out["timed_out"] is False
    assert out["not_online"] == {}
    assert slept == [0.01, 0.01]


def test_a_wait_that_times_out_reports_the_state_instead_of_raising():
    c = ctx(reads={"SHOW INDEXES YIELD name, state": [{"name": "chunk_text", "state": "FAILED"}]})
    out = index_schema.await_indexes(c, timeout_s=0, sleep=lambda _s: None)
    assert out["timed_out"] is True
    assert out["not_online"]["chunk_text"] == "FAILED"
    assert out["not_online"]["workitem_text"] == "MISSING"


def test_the_wait_only_looks_at_this_namespace():
    rows = [{"name": f"_T{s.name}", "state": "ONLINE"} for s in index_schema.MANAGED]
    rows.append({"name": "_ResetTestbrain_statuschange_id_key", "state": "POPULATING"})
    c = ctx(prefix="_T", reads={"SHOW INDEXES YIELD name, state": rows})
    assert index_schema.await_indexes(c, sleep=lambda _s: None)["not_online"] == {}


def test_status_reads_the_server_and_reports_a_missing_index_as_absent():
    rows = [
        {
            "name": "chunk_embedding",
            "type": "VECTOR",
            "state": "ONLINE",
            "populationPercent": 100.0,
            "entityType": "NODE",
            "labelsOrTypes": ["Chunk"],
            "properties": ["embedding"],
            "options": {"indexConfig": {"vector.dimensions": 1024}},
        }
    ]
    c = ctx(reads={"SHOW INDEXES": rows})
    status = index_schema.index_status(c)
    by_name = {row["name"]: row for row in status}
    assert by_name["chunk_embedding"]["state"] == "ONLINE"
    assert by_name["chunk_embedding"]["population_percent"] == 100.0
    assert by_name["entity_text"]["state"] == "MISSING"
    assert by_name["entity_text"]["present"] is False


def test_status_keeps_indexes_the_graph_has_that_the_spec_does_not_manage():
    rows = [
        {
            "name": "person_embedding",
            "type": "VECTOR",
            "state": "ONLINE",
            "populationPercent": 100.0,
            "entityType": "NODE",
            "labelsOrTypes": ["Person"],
            "properties": ["embedding"],
            "options": {},
        },
        {
            "name": "brain_entity_kind_idx",
            "type": "RANGE",
            "state": "ONLINE",
            "populationPercent": 100.0,
            "entityType": "NODE",
            "labelsOrTypes": ["Entity"],
            "properties": ["kind"],
            "options": {},
        },
    ]
    c = ctx(reads={"SHOW INDEXES": rows})
    by_name = {row["name"]: row for row in index_schema.index_status(c)}
    assert by_name["person_embedding"]["owner"] == "resolve"
    assert by_name["brain_entity_kind_idx"]["owner"] == "other"
    assert by_name["brain_entity_kind_idx"]["managed"] is False


def test_status_ignores_another_namespaces_indexes():
    rows = [
        {
            "name": "_Commchunk_embedding",
            "type": "VECTOR",
            "state": "ONLINE",
            "populationPercent": 100.0,
            "entityType": "NODE",
            "labelsOrTypes": ["_CommChunk"],
            "properties": ["embedding"],
            "options": {},
        }
    ]
    c = ctx(reads={"SHOW INDEXES": rows})
    assert "_Commchunk_embedding" not in {row["name"] for row in index_schema.index_status(c)}


def test_offline_indexes_are_what_the_gate_reads():
    rows = [
        {
            "name": "chunk_text",
            "type": "FULLTEXT",
            "state": "POPULATING",
            "populationPercent": 41.2,
            "entityType": "NODE",
            "labelsOrTypes": ["Chunk"],
            "properties": ["text"],
            "options": {},
        }
    ]
    status = index_schema.index_status(ctx(reads={"SHOW INDEXES": rows}))
    offline = index_schema.not_online(status)
    assert "chunk_text" in offline
    # every managed index that is MISSING counts as not-online too
    assert "workitem_text" in offline
