"""`brain chunk --stamp-synthetic`: the backfill that makes `brain reset --synthetic` exact.

The fake graph from the reset tests is reused deliberately — it keeps real nodes and edges
and applies the writes, so "the flag actually moved" is asserted against node state rather
than against a query string. What it cannot check is that the Cypher is valid, which is
`tests/live/test_reset_live.py`'s job.
"""

from __future__ import annotations

from typing import Any

import pytest

from brain.chunk import graph as chunk_graph
from brain.chunk.synthetic import plan, stamp_synthetic
from brain.extract import graph as extract_graph
from tests.reset_helpers import FakeGraph, ctx


class StampGraph(FakeGraph):
    """`FakeGraph` plus the two derive-and-stamp shapes `--stamp-synthetic` emits."""

    def _chunk_syn(self, index: int) -> bool:
        parents = [a for a, b, t in self.edges if b == index and t == "HAS_CHUNK"]
        return bool(parents) and all(
            self.nodes[a]["props"].get("synthetic") is True for a in parents
        )

    def _entity_syn(self, node: dict[str, Any]) -> bool:
        ids = node["props"].get("evidence_chunk_ids") or []
        live = [
            i
            for i, n in enumerate(self.nodes)
            if n["label"] == "Chunk" and not n.get("deleted") and n["key"] in ids
        ]
        return bool(live) and all(self._chunk_syn(i) for i in live)

    def _drift(self, label: str, derive) -> list[tuple[dict[str, Any], bool]]:
        out = []
        for i, node in enumerate(self.nodes):
            if node.get("deleted") or node["label"] != label:
                continue
            out.append((node, derive(i if label == "Chunk" else node)))
        return out

    def _handles(self, cypher: str) -> str | None:
        """Which label a derive-and-stamp query is *about*.

        Not by looking for `:`Chunk``, which the entity query also contains — it reads the
        chunks to derive from. The driving `MATCH` is what decides.
        """
        if "AS syn" not in cypher:
            return None
        return "Chunk" if cypher.startswith("MATCH (c:") else "Entity"

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        label = self._handles(cypher)
        if label is None:
            return super().read(cypher, **params)
        self.queries.append(cypher)
        rows = self._drift(label, self._chunk_syn if label == "Chunk" else self._entity_syn)
        return [
            {
                "total": len(rows),
                "null_flag": sum(1 for n, _ in rows if n["props"].get("synthetic") is None),
                "wrong": sum(
                    1
                    for n, syn in rows
                    if n["props"].get("synthetic") is not None and n["props"]["synthetic"] != syn
                ),
                "synthetic": sum(1 for _n, syn in rows if syn),
            }
        ]

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        label = self._handles(cypher)
        if label is None:
            return super().write(cypher, **params)
        self.queries.append(cypher)
        rows = self._drift(label, self._chunk_syn if label == "Chunk" else self._entity_syn)
        written = 0
        for node, syn in rows:
            if node["props"].get("synthetic") != syn:
                node["props"]["synthetic"] = syn
                written += 1
        counters = dict.fromkeys(("nodes_deleted", "relationships_deleted", "properties_set"), 0)
        counters["properties_set"] = written
        return counters


@pytest.fixture
def graph() -> StampGraph:
    """One real work item, one synthetic one, their chunks, and an entity on each side.

    Every chunk starts with a null flag — the state the live graph was actually in after
    the corpus was chunked before the property existed.
    """
    return StampGraph(
        nodes=[
            ("WorkItem", "KAFKA-1", {"synthetic": False}),  # 0
            ("WorkItem", "ADO-7", {"synthetic": True}),  # 1
            ("Chunk", "real-1", {}),  # 2
            ("Chunk", "syn-1", {}),  # 3
            ("Chunk", "syn-2", {}),  # 4
            ("Chunk", "loose", {}),  # 5 - no parent at all
            ("Entity", "Feature|real", {"synthetic": False, "evidence_chunk_ids": ["real-1"]}),
            ("Entity", "Feature|syn", {"synthetic": False, "evidence_chunk_ids": ["syn-1"]}),
            (
                "Entity",
                "Feature|both",
                {"synthetic": False, "evidence_chunk_ids": ["real-1", "syn-2"]},
            ),
            ("Entity", "Feature|bare", {"synthetic": False, "evidence_chunk_ids": []}),
        ],
        edges=[(0, 2, "HAS_CHUNK"), (1, 3, "HAS_CHUNK"), (1, 4, "HAS_CHUNK")],
    )


def test_a_chunk_inherits_the_flag_of_its_parent(graph):
    stamp_synthetic(ctx(graph), echo=lambda _m: None)
    assert graph.prop("Chunk", "real-1", "synthetic") is False
    assert graph.prop("Chunk", "syn-1", "synthetic") is True
    assert graph.prop("Chunk", "syn-2", "synthetic") is True


def test_a_parentless_chunk_comes_out_real_rather_than_guessed(graph):
    """It cannot be attributed. `brain reset` deletes it for being parentless, which is
    the honest reason — guessing `true` would delete text a real page still owns."""
    stamp_synthetic(ctx(graph), echo=lambda _m: None)
    assert graph.prop("Chunk", "loose", "synthetic") is False


def test_an_entity_is_synthetic_only_when_every_evidence_chunk_is(graph):
    stamp_synthetic(ctx(graph), echo=lambda _m: None)
    assert graph.prop("Entity", "Feature|syn", "synthetic") is True
    assert graph.prop("Entity", "Feature|real", "synthetic") is False
    # one real chunk saying the same thing keeps the entity in the real corpus
    assert graph.prop("Entity", "Feature|both", "synthetic") is False
    # no evidence at all is a provenance defect, not a claim of synthetic-ness
    assert graph.prop("Entity", "Feature|bare", "synthetic") is False


def test_entities_are_stamped_after_chunks_never_before(graph):
    """The entity rule reads `Chunk.synthetic`. Stamping entities first would write `false`
    on every one of them — which is exactly how the live graph got 9,038 wrong flags."""
    section = stamp_synthetic(ctx(graph), echo=lambda _m: None)
    assert section["stamped"] == {"chunks": 4, "entities": 1}
    assert section["after"]["chunks"]["null"] == 0
    assert section["after"]["entities"]["synthetic_after"] == 1


def test_a_second_run_stamps_nothing(graph):
    c = ctx(graph)
    stamp_synthetic(c, echo=lambda _m: None)
    again = stamp_synthetic(c, echo=lambda _m: None)
    assert again["stamped"] == {"chunks": 0, "entities": 0}
    assert again["before"]["chunks"]["null"] == again["before"]["chunks"]["wrong"] == 0


def test_a_dry_run_writes_nothing_and_still_says_what_it_would_do(graph):
    section = stamp_synthetic(ctx(graph), apply=False, echo=lambda _m: None)
    assert section["applied"] is False
    assert section["stamped"] == {"chunks": 0, "entities": 0}
    assert section["before"]["chunks"]["null"] == 4
    assert graph.prop("Chunk", "syn-1", "synthetic") is None


def test_a_changed_parent_is_corrected_not_only_the_nulls(graph):
    """`--stamp-synthetic` re-derives; it does not only fill blanks. A chunk whose parent
    switched sides carries a flag that is wrong, and wrong is worse than missing."""
    stamp_synthetic(ctx(graph), echo=lambda _m: None)
    graph.prop("Chunk", "syn-1", "synthetic")
    for node in graph.nodes:
        if node["key"] == "ADO-7":
            node["props"]["synthetic"] = False
    section = stamp_synthetic(ctx(graph), echo=lambda _m: None)
    assert section["before"]["chunks"]["wrong"] == 2
    assert graph.prop("Chunk", "syn-1", "synthetic") is False


def test_the_labels_follow_the_namespace_prefix(graph):
    """A smoke run stamps `_SmokeChunk`, never the real graph's `Chunk`."""
    c = ctx(graph, prefix="_Smoke")
    chunk_graph.synthetic_drift(c)
    extract_graph.synthetic_entity_drift(c)
    assert all("`_SmokeChunk`" in q or "`_SmokeEntity`" in q for q in graph.queries)


def test_plan_reads_and_never_writes(graph):
    before = [dict(n["props"]) for n in graph.nodes]
    plan(ctx(graph))
    assert [dict(n["props"]) for n in graph.nodes] == before
