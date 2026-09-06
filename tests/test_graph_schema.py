"""Schema statements and the Cypher builders, without a database."""

from __future__ import annotations

import pytest

from brain.graph.context import GraphContext
from brain.graph.cypher import edge_merge, node_merge
from brain.graph.schema import RANGE_INDEXES, UNIQUE_KEYS, schema_statements

BRIEF_KEYS = {
    ("WorkItem", "key"),
    ("Document", "key"),
    ("Person", "id"),
    ("Commit", "sha"),
    ("PullRequest", "number"),
    ("Component", "name"),
    ("Version", "name"),
    ("Sprint", "name"),
    ("File", "path"),
    ("Chunk", "id"),
    ("StatusChange", "id"),
    ("Community", "id"),
    ("Entity", "id"),
}


@pytest.fixture
def ctx() -> GraphContext:
    return GraphContext(client=None, prefix="")  # type: ignore[arg-type]


def test_every_key_the_brief_lists_has_a_constraint():
    assert BRIEF_KEYS <= set(UNIQUE_KEYS)


def test_statements_are_idempotent_and_named():
    names = []
    for name, cypher in schema_statements(GraphContext(client=None, prefix="")):  # type: ignore[arg-type]
        assert "IF NOT EXISTS" in cypher
        names.append(name)
    assert len(names) == len(set(names)) == len(UNIQUE_KEYS) + len(RANGE_INDEXES)


def test_prefix_namespaces_labels_and_object_names():
    scratch = GraphContext(client=None, prefix="_Smoke")  # type: ignore[arg-type]
    cypher = dict(schema_statements(scratch))["brain_workitem_key_key"]
    assert "`_SmokeWorkItem`" in cypher
    assert "`_Smokebrain_workitem_key_key`" in cypher


def test_node_merge_uses_merge_and_never_create(ctx):
    cypher = node_merge(ctx, "WorkItem", "key", extra_labels=("Bug",))
    assert cypher.startswith("UNWIND $rows AS row")
    assert "MERGE (n:`WorkItem` {`key`: row.key})" in cypher
    assert "SET n:`Bug`" in cypher
    assert "CREATE" not in cypher


def test_edge_merge_matches_both_endpoints_and_creates_no_node(ctx):
    cypher = edge_merge(ctx, ("WorkItem", "key"), "IN_COMPONENT", ("Component", "name"))
    assert cypher.count("MATCH") == 2
    assert "MERGE (a)-[r:`IN_COMPONENT`]->(b)" in cypher
    assert "CREATE" not in cypher


def test_edge_merge_key_props_go_into_the_merge_pattern(ctx):
    cypher = edge_merge(
        ctx,
        ("WorkItem", "key"),
        "ASSIGNED_TO",
        ("Person", "id"),
        key_props=("valid_from",),
        set_props=True,
    )
    assert "MERGE (a)-[r:`ASSIGNED_TO` {`valid_from`: row.valid_from}]->(b)" in cypher
    assert cypher.endswith("SET r += row.props")
