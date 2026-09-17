"""`brain chunk --stamp-slice`: the backfill that makes `brain reset --slice` exact.

`brain chunk` writes `Chunk.slice` when it writes a chunk — and it does not rewrite a
chunk whose text is byte-identical (`ChunkState.is_current`), which is the right call for
13,846 nodes and 14 MB of text. The consequence measured on the live graph on 2026-09-17
is that after the first incremental run only the 71 chunks that were written carried the
property and the other 13,846 carried nothing at all. Plan 3 decision 6 says a node always
carries `slice`, for the same reason `synthetic` does: a property that is not written is a
property `brain reset` cannot match on.

Same fake graph as the `--stamp-synthetic` tests, for the same reason: it applies the
writes, so "the value actually moved" is asserted against node state.
"""

from __future__ import annotations

from typing import Any

import pytest

from brain.canon.models import BASE_SLICE, INCREMENTAL_SLICE
from brain.chunk import graph as chunk_graph
from brain.chunk.slices import plan, stamp_slice
from tests.reset_helpers import FakeGraph, ctx


class SliceGraph(FakeGraph):
    """`FakeGraph` plus the derive-and-stamp shape `--stamp-slice` emits."""

    def _chunk_slice(self, index: int) -> str:
        parents = [e["src"] for e in self.edges if e["dst"] == index and e["type"] == "HAS_CHUNK"]
        if parents and all(
            self.nodes[a]["props"].get("slice", BASE_SLICE) == INCREMENTAL_SLICE for a in parents
        ):
            return INCREMENTAL_SLICE
        return BASE_SLICE

    def _rows(self) -> list[tuple[dict[str, Any], str]]:
        return [
            (node, self._chunk_slice(i))
            for i, node in enumerate(self.nodes)
            if not node.get("deleted") and node["label"] == "Chunk"
        ]

    def _handles(self, cypher: str) -> bool:
        return "AS sl" in cypher

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if not self._handles(cypher):
            return super().read(cypher, **params)
        self.queries.append(cypher)
        rows = self._rows()
        return [
            {
                "total": len(rows),
                "null_flag": sum(1 for n, _ in rows if n["props"].get("slice") is None),
                "wrong": sum(
                    1
                    for n, sl in rows
                    if n["props"].get("slice") is not None and n["props"]["slice"] != sl
                ),
                "incremental": sum(1 for _n, sl in rows if sl == INCREMENTAL_SLICE),
            }
        ]

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        if not self._handles(cypher):
            return super().write(cypher, **params)
        self.queries.append(cypher)
        written = 0
        for node, sl in self._rows():
            if node["props"].get("slice") != sl:
                node["props"]["slice"] = sl
                written += 1
        counters = dict.fromkeys(("nodes_deleted", "relationships_deleted", "properties_set"), 0)
        counters["properties_set"] = written
        return counters


@pytest.fixture
def graph() -> SliceGraph:
    """A base work item, an incremental one, their chunks, and one parentless chunk.

    Every chunk starts with no `slice` at all — the state the live graph was in after the
    first incremental run.
    """
    return SliceGraph(
        nodes=[
            ("WorkItem", "KAFKA-1", {"slice": BASE_SLICE}),  # 0
            ("WorkItem", "KAFKA-20035", {"slice": INCREMENTAL_SLICE}),  # 1
            ("Chunk", "base-1", {}),  # 2
            ("Chunk", "inc-1", {}),  # 3
            ("Chunk", "loose", {}),  # 4 - no parent at all
        ],
        edges=[(0, 2, "HAS_CHUNK"), (1, 3, "HAS_CHUNK")],
    )


def props(graph: SliceGraph, key: str) -> dict[str, Any]:
    return next(n["props"] for n in graph.nodes if n["key"] == key)


def test_a_chunk_inherits_the_slice_of_its_parent(graph):
    stamp_slice(ctx(graph), echo=lambda _m: None)
    assert props(graph, "base-1")["slice"] == BASE_SLICE
    assert props(graph, "inc-1")["slice"] == INCREMENTAL_SLICE


def test_a_parentless_chunk_comes_out_base_rather_than_deletable(graph):
    """Conservative in the same direction as `--stamp-synthetic`: an unattributable chunk
    survives `brain reset --slice incremental` and can be removed later, never the other
    way around."""
    stamp_slice(ctx(graph), echo=lambda _m: None)
    assert props(graph, "loose")["slice"] == BASE_SLICE


def test_a_chunk_of_a_base_parent_and_an_incremental_one_is_base(graph):
    """`widest_slice` in the file, once more in the graph: one base parent makes it base."""
    graph.edges.append({"src": 0, "dst": 3, "type": "HAS_CHUNK", "props": {}})
    stamp_slice(ctx(graph), echo=lambda _m: None)
    assert props(graph, "inc-1")["slice"] == BASE_SLICE


def test_a_second_run_stamps_nothing(graph):
    c = ctx(graph)
    first = stamp_slice(c, echo=lambda _m: None)
    again = stamp_slice(c, echo=lambda _m: None)
    assert first["stamped"]["chunks"] == 3
    assert again["stamped"]["chunks"] == 0
    assert again["after"]["chunks"]["null"] == 0


def test_a_dry_run_writes_nothing_and_still_says_what_it_would_do(graph):
    section = stamp_slice(ctx(graph), apply=False, echo=lambda _m: None)
    assert section["applied"] is False
    assert section["before"]["chunks"]["null"] == 3
    assert section["stamped"]["chunks"] == 0
    assert all(n["props"].get("slice") is None for n in graph.nodes if n["label"] == "Chunk")


def test_a_reparented_chunk_is_corrected_not_only_the_nulls(graph):
    stamp_slice(ctx(graph), echo=lambda _m: None)
    props(graph, "inc-1")["slice"] = BASE_SLICE  # something re-ran and wrote the wrong one
    section = stamp_slice(ctx(graph), echo=lambda _m: None)
    assert section["before"]["chunks"]["wrong"] == 1
    assert section["stamped"]["chunks"] == 1
    assert props(graph, "inc-1")["slice"] == INCREMENTAL_SLICE


def test_the_label_follows_the_namespace_prefix(graph):
    c = ctx(graph, prefix="_Scratch")
    chunk_graph.slice_drift(c)
    assert all("_ScratchChunk" in q for q in graph.queries)


def test_plan_reads_and_never_writes(graph):
    before = [dict(n["props"]) for n in graph.nodes]
    assert plan(ctx(graph))["chunks"]["null"] == 3
    assert [dict(n["props"]) for n in graph.nodes] == before
