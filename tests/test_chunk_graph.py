"""The write surface without a database: which Cypher is generated, and with which rows.

A fake `GraphContext` records the (cypher, rows) pairs. The point is not to re-test Neo4j
— `tests/live/test_chunk_live.py` does that — but to catch the two mistakes this layer can
make silently: a query that creates the far end of an edge, and a query built against a
`brain.graph.cypher` helper whose signature has moved under it.
"""

from __future__ import annotations

from typing import Any

import pytest

from brain.chunk import graph as chunk_graph
from brain.chunk.chunker import Chunk
from brain.graph.context import GraphContext


class FakeClient:
    def __init__(self, reads: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.reads = reads or {}

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        self.writes.append((cypher, params))
        return dict.fromkeys(
            ("nodes_created", "relationships_created", "properties_set", "labels_added"), 0
        )

    def write_batched(self, cypher: str, rows, batch_size: int = 1000) -> dict[str, int]:
        return self.write(cypher, rows=list(rows))

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        for fragment, rows in self.reads.items():
            if fragment in cypher:
                return rows
        return []


def ctx(prefix: str = "", **kw) -> GraphContext:
    return GraphContext(FakeClient(**kw), prefix=prefix)


def chunk(parent_kind: str = "Document", parent_key: str = "KIP-848", text: str = "x" * 100):
    return Chunk(
        parent_key=parent_key, parent_kind=parent_kind, kind="section", position=0, text=text
    )


def test_nodes_are_merged_on_the_id_and_never_created():
    c = ctx()
    chunk_graph.write_nodes(c, [chunk()])
    cypher, params = c.client.writes[0]
    assert cypher.startswith("UNWIND $rows AS row")
    assert "MERGE (n:`Chunk` {`id`: row.key})" in cypher
    assert "CREATE" not in cypher
    assert params["rows"][0]["key"] == chunk().id
    assert params["rows"][0]["props"]["parent_key"] == "KIP-848"


def test_has_chunk_matches_both_ends_so_it_can_never_invent_a_parent():
    c = ctx()
    counts = chunk_graph.write_edges(
        c,
        [
            chunk("Document", "KIP-848"),
            chunk("WorkItem", "KAFKA-1"),
            chunk("Commit", "a" * 40),
        ],
    )
    assert counts == {"Commit": 1, "Document": 1, "WorkItem": 1}
    queries = [q for q, _ in c.client.writes]
    assert any("MATCH (a:`Document` {`key`: row.src})" in q for q in queries)
    assert any("MATCH (a:`WorkItem` {`key`: row.src})" in q for q in queries)
    assert any("MATCH (a:`Commit` {`sha`: row.src})" in q for q in queries)
    for q in queries:
        assert "MERGE (a)-[r:`HAS_CHUNK`]->(b)" in q
        assert q.count("MATCH") == 2  # both endpoints, neither created


def test_an_unknown_parent_kind_is_an_error_not_a_dropped_chunk():
    c = ctx()
    with pytest.raises(ValueError, match="Sprint"):
        chunk_graph.write_edges(c, [chunk("Sprint", "sprint-1")])


def test_labels_and_index_names_follow_the_prefix_namespace():
    c = ctx(prefix="_Smoke")
    schema = chunk_graph.apply_chunk_schema(c, 1024)
    cyphers = " ".join(q for q, _ in c.client.writes)
    assert "`_SmokeChunk`" in cyphers and "`_SmokeIndexMeta`" in cyphers
    assert "CREATE VECTOR INDEX `_Smokechunk_embedding`" in cyphers
    assert "`vector.dimensions`: 1024" in cyphers
    assert "'cosine'" in cyphers
    assert schema["vector_index"] == "_Smokechunk_embedding"
    assert chunk_graph.index_name(c) == "_Smokechunk_embedding"


def test_a_bad_dimension_is_refused_before_it_reaches_the_index_options():
    with pytest.raises(ValueError, match="positive int"):
        chunk_graph.apply_chunk_schema(ctx(), 0)
    with pytest.raises(ValueError, match="positive int"):
        chunk_graph.apply_chunk_schema(ctx(), "1024")


def test_embeddings_go_through_the_vector_property_procedure():
    c = ctx()
    assert chunk_graph.write_embeddings(c, []) == 0
    assert not c.client.writes
    chunk_graph.write_embeddings(c, [{"id": "abc", "vector": [0.1, 0.2]}])
    cypher, params = c.client.writes[0]
    assert "db.create.setNodeVectorProperty(c, 'embedding', row.vector)" in cypher
    assert params["rows"] == [{"id": "abc", "vector": [0.1, 0.2]}]


def test_state_reads_hashes_and_which_ids_already_carry_a_vector():
    c = ctx(
        reads={
            "RETURN c.id AS id, c.hash AS hash": [
                {"id": "a", "hash": "h1", "embedded": True},
                {"id": "b", "hash": "h2", "embedded": False},
            ]
        }
    )
    state = chunk_graph.state(c)
    assert state.hashes == {"a": "h1", "b": "h2"}
    assert state.embedded == {"a"}


def test_needs_embedding_is_true_for_a_new_id_a_new_hash_or_a_missing_vector():
    fresh = chunk()
    state = chunk_graph.ChunkState(hashes={fresh.id: fresh.hash}, embedded={fresh.id})
    assert state.needs_embedding(fresh) is False
    assert chunk_graph.ChunkState({}, set()).needs_embedding(fresh) is True
    assert chunk_graph.ChunkState({fresh.id: "stale"}, {fresh.id}).needs_embedding(fresh) is True
    assert chunk_graph.ChunkState({fresh.id: fresh.hash}, set()).needs_embedding(fresh) is True


def test_marking_no_orphans_writes_nothing():
    c = ctx()
    assert chunk_graph.mark_orphans(c, []) == 0
    assert not c.client.writes


def test_marking_orphans_sets_the_flag_and_deletes_nothing():
    c = ctx()
    assert chunk_graph.mark_orphans(c, ["a", "b"]) == 2
    cypher, params = c.client.writes[0]
    assert "SET c.orphaned = true" in cypher
    assert "DELETE" not in cypher
    assert params["rows"] == [{"id": "a"}, {"id": "b"}]


def test_a_missing_vector_index_reports_missing_rather_than_raising():
    assert chunk_graph.index_status(ctx())["state"] == "MISSING"
