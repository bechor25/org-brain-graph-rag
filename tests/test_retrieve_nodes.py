"""Projections, the `Item` envelope, and the packing/logging every strategy shares.

No database: these are the parts that decide *shape*, and shape is exactly what a fake row
can test. The live tests in `tests/live/test_retrieve_live.py` prove the Cypher matches.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from brain.retrieve.envelope import Timer, dedupe_provenance, finish
from brain.retrieve.nodes import FIELDS, ITEM_KIND, KEY_FIELD, key_case, label_case, projection
from brain.retrieve.types import ITEM_KINDS, SOURCE_KINDS, Item, ItemKind, Provenance


def test_every_projected_label_declares_a_key_and_an_item_kind() -> None:
    assert set(FIELDS) == set(ITEM_KIND) == set(KEY_FIELD)
    for label, field in KEY_FIELD.items():
        assert field in FIELDS[label], f"{label}'s key {field!r} is not projected"
    assert set(ITEM_KIND.values()) <= set(ITEM_KINDS)


def test_projection_stringifies_temporal_properties() -> None:
    """`record.data()` would hand back a `neo4j.time.DateTime` that JSON cannot serialise."""
    cypher = projection("c", "Chunk")
    assert "`at`: toString(c.`at`)" in cypher
    assert "`text`: c.`text`" in cypher
    assert "embedding" not in cypher, "1024 floats per hit is not a projection, it is a leak"


def test_projection_covers_every_label_without_raising() -> None:
    for label in FIELDS:
        assert projection("n", label).startswith("{")


def test_label_case_prefers_the_specific_label() -> None:
    cypher = label_case("n")
    assert cypher.index("WHEN n:`Commit`") < cypher.index("WHEN n:`WorkItem`")
    assert label_case("n", "_Retr").startswith("CASE WHEN n:`_RetrChunk`")


def test_key_case_uses_the_citable_property_not_coalesce() -> None:
    cypher = key_case("n")
    assert "WHEN n:`Entity` THEN toString(n.`id`)" in cypher
    assert "WHEN n:`WorkItem` THEN toString(n.`key`)" in cypher
    assert "WHEN n:`Commit` THEN toString(n.`sha`)" in cypher


def test_to_item_builds_a_chunk_with_its_own_provenance() -> None:
    from brain.retrieve.nodes import to_item

    item = to_item(
        "Chunk",
        {
            "id": "abc",
            "parent_key": "KAFKA-1",
            "parent_kind": "WorkItem",
            "kind": "description",
            "text": "  some   text  ",
        },
        0.42,
    )
    assert item.kind == "Chunk" and item.key == "abc"
    assert item.snippet == "some text"
    assert item.provenance[0].chunk_id == "abc"
    assert item.provenance[0].source == "KAFKA-1"
    assert "text" not in item.props and "id" not in item.props


def test_to_item_maps_a_commit_to_the_change_kind() -> None:
    from brain.retrieve.nodes import to_item

    item = to_item("Commit", {"sha": "deadbeef", "message": "KAFKA-1: fix\n\nbody"}, 1.0)
    assert item.kind == "Change" and item.key == "deadbeef"
    assert item.title == "KAFKA-1: fix"


def test_to_item_turns_entity_evidence_into_provenance() -> None:
    from brain.retrieve.nodes import to_item

    item = to_item(
        "Entity",
        {
            "id": "Decision|x",
            "kind": "Decision",
            "name": "x",
            "description": "why",
            "evidence_chunk_ids": ["c1", "c2", "c3", "c4"],
            "batch_id": "b1",
            "model": "m",
        },
    )
    assert [p.chunk_id for p in item.provenance] == ["c1", "c2", "c3"]
    assert item.provenance[0].batch_id == "b1"


def test_dedupe_provenance_keeps_the_quoted_entry() -> None:
    merged = dedupe_provenance(
        [
            Provenance(chunk_id="c1"),
            Provenance(chunk_id="c1", quote="the words", batch_id="b"),
            Provenance(chunk_id="c2", quote="other"),
        ]
    )
    assert len(merged) == 2
    assert merged[0].quote == "the words" and merged[0].batch_id == "b"


def test_dedupe_provenance_keeps_entries_without_a_chunk_id() -> None:
    merged = dedupe_provenance([Provenance(source="a"), Provenance(source="b")])
    assert len(merged) == 2


def test_finish_packs_dedupes_and_can_stay_out_of_the_log(tmp_path) -> None:
    items = [
        Item(kind="Chunk", key="c1", score=0.9, provenance=[Provenance(chunk_id="x")] * 3),
        Item(kind="WorkItem", key="KAFKA-1", score=0.5),
    ]
    result = finish("s1", items, question="q", timer=Timer(), cypher_used=["A", "A", ""], log=False)
    assert result.strategy == "s1"
    assert result.cypher_used == ["A"]
    assert len(result.items[0].provenance) == 1
    assert not (tmp_path / "retrieval.jsonl").exists()


# ------------------------------------------------------- the closed sets of the envelope


def test_item_kind_is_a_closed_set_the_model_enforces() -> None:
    """`ITEM_KINDS` is the packer's per-kind guarantee; a typo in it is a silent new kind."""
    assert set(ITEM_KINDS) == set(get_args(ItemKind))
    with pytest.raises(ValidationError):
        Item(kind="Sprint", key="x")  # a label, not an item kind
    with pytest.raises(ValidationError):
        Item(kind="row", key="x")  # the right word, the wrong case


def test_provenance_says_which_kind_of_evidence_it_is() -> None:
    """Task 4's cite-check counts a quoted chunk and a node's own text separately."""
    assert Provenance(chunk_id="c1").source_kind == "quote"
    assert set(SOURCE_KINDS) == {"quote", "node-text", "row"}
    for kind in SOURCE_KINDS:
        assert Provenance(chunk_id="c1", source_kind=kind).source_kind == kind
    with pytest.raises(ValidationError):
        Provenance(chunk_id="c1", source_kind="vibes")
