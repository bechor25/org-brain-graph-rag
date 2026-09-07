"""Folding many batches into one graph plan — and the provenance that has to survive it.

Nothing here touches Neo4j: `plan()` produces the rows, and the rows are where "every
LLM-derived node and edge carries its evidence" is either true or not.
"""

from __future__ import annotations

import json
from pathlib import Path

from brain.extract.graph import MODEL, PROVENANCE_PROPS
from brain.extract.merge import drop_missing_chunks, plan
from brain.extract.models import KINDS, RELATION_TYPES
from brain.extract.validate import GraphFacts, Ref
from tests.extract_helpers import (
    batch_input,
    batch_output,
    chunk_context,
    entity,
    relation,
    screened,
    written_batch,
)

SCHEMA = json.loads(Path("brain/extract/schema.json").read_text(encoding="utf-8"))
A, B = "a" * 40, "b" * 40
TEXT = "Move assignment to the group coordinator; clients no longer do assignment."
FACTS = GraphFacts(document_keys=frozenset({"KIP-5"}), components={"streams": "streams"})


def two_batches(tmp_path: Path):
    """The same Decision, spelled differently, in two batches of two shards.

    The two spellings differ only in case and whitespace, which is exactly what `norm_name`
    forgives. A real synonym ("the coordinator") is a *different* node here on purpose —
    unifying those is `brain resolve`, which can weigh evidence this step does not have.
    """
    first = written_batch(
        tmp_path,
        inp=batch_input([chunk_context(chunk_id=A, text=TEXT)], batch_id="shard-01/001"),
        out=batch_output(
            batch_id="shard-01/001",
            entities=[
                entity(
                    kind="Decision",
                    name="Move assignment to the group coordinator",
                    quote="Move assignment to the group coordinator",
                    chunk_id=A,
                ),
                entity(
                    kind="Problem",
                    name="clients do assignment",
                    quote="clients no longer do assignment",
                    chunk_id=A,
                ),
            ],
            relations=[
                relation(
                    type="MOTIVATED_BY",
                    source="Move assignment to the group coordinator",
                    target="clients do assignment",
                    evidence_chunk_id=A,
                )
            ],
        ),
    )
    second = written_batch(
        tmp_path,
        inp=batch_input(
            [chunk_context(chunk_id=B, text=TEXT)],
            batch_id="shard-02/001",
            shard="shard-02",
            index=1,
        ),
        out=batch_output(
            batch_id="shard-02/001",
            entities=[
                entity(
                    kind="Decision",
                    name="Move  assignment to the Group Coordinator",
                    quote="Move assignment to the group coordinator",
                    chunk_id=B,
                )
            ],
            relations=[
                relation(
                    type="DECIDES",
                    source="KIP-5",
                    target="Move  assignment to the Group Coordinator",
                    evidence_chunk_id=B,
                )
            ],
        ),
    )
    return [screened(first, SCHEMA, FACTS), screened(second, SCHEMA, FACTS)]


def test_the_same_entity_in_two_batches_becomes_one_node_with_both_evidences(tmp_path):
    batches = two_batches(tmp_path)
    plan_obj = plan(batches)
    ids = sorted(plan_obj.entities)
    assert ids == [
        "Decision|move assignment to the group coordinator",
        "Problem|client do assignment",
    ]
    decision = plan_obj.entities["Decision|move assignment to the group coordinator"]
    assert decision.chunk_ids == {A, B}
    assert decision.batches == {"shard-01/001", "shard-02/001"}


def test_a_merged_entity_keeps_the_other_surface_form_as_an_alias(tmp_path):
    """`brain resolve` is the step that unifies names; it needs to know what was written."""
    props = (
        plan(two_batches(tmp_path))
        .entities["Decision|move assignment to the group coordinator"]
        .props()
    )
    assert props["name"] in {
        "Move assignment to the group coordinator",
        "Move  assignment to the Group Coordinator",
    }
    assert props["aliases"] and props["name"] not in props["aliases"]
    assert props["norm_name"] == "move assignment to the group coordinator"


def test_batch_id_names_the_first_batch_and_batch_ids_keeps_the_rest(tmp_path):
    provenance = (
        plan(two_batches(tmp_path))
        .entities["Decision|move assignment to the group coordinator"]
        .provenance()
    )
    assert provenance["batch_id"] == "shard-01/001"
    assert provenance["batch_ids"] == ["shard-01/001", "shard-02/001"]
    assert provenance["shard"] == "shard-01"
    assert provenance["evidence_chunk_ids"] == sorted([A, B])
    assert provenance["model"] == MODEL


def test_every_row_this_step_writes_carries_the_four_provenance_properties(tmp_path):
    """Conventions rule 3, checked on the rows — the live test checks it on the graph."""
    plan_obj = plan(two_batches(tmp_path))
    rows = [{**r["props"], **r["on_create"]} for r in plan_obj.entity_rows()]
    rows += [r["props"] for group in plan_obj.mention_rows().values() for r in group]
    rows += [r["props"] for group in plan_obj.relation_rows().values() for r in group]
    assert rows
    for row in rows:
        missing = [p for p in PROVENANCE_PROPS if not row.get(p)]
        assert not missing, (missing, row)


def test_mentions_are_grouped_by_the_label_they_point_at(tmp_path):
    batches = two_batches(tmp_path)
    grouped = plan(batches).mention_rows()
    assert set(grouped) == {"Entity"}
    assert all(r["props"]["quote"] for r in grouped["Entity"])
    assert {r["src"] for r in grouped["Entity"]} == {A, B}


def test_relations_are_grouped_by_type_and_by_both_endpoint_labels(tmp_path):
    grouped = plan(two_batches(tmp_path)).relation_rows()
    assert ("DECIDES", "Document", "Entity") in grouped
    assert ("MOTIVATED_BY", "Entity", "Entity") in grouped
    decides = grouped[("DECIDES", "Document", "Entity")][0]
    assert decides["src"] == "KIP-5"
    assert decides["dst"] == "Decision|move assignment to the group coordinator"


def test_a_relation_note_survives_onto_the_edge(tmp_path):
    path = written_batch(
        tmp_path,
        inp=batch_input([chunk_context(chunk_id=A, text=TEXT)]),
        out=batch_output(
            entities=[
                entity(
                    kind="Decision",
                    name="move assignment",
                    quote="Move assignment",
                    chunk_id=A,
                ),
                entity(
                    kind="Alternative",
                    name="client assignment",
                    quote="clients no longer do assignment",
                    chunk_id=A,
                ),
            ],
            relations=[
                relation(
                    type="REJECTS",
                    source="move assignment",
                    target="client assignment",
                    evidence_chunk_id=A,
                    note="it does not fix coordinator-side fencing",
                )
            ],
        ),
    )
    rows = plan([screened(path, SCHEMA, FACTS)]).relation_rows()
    props = rows[("REJECTS", "Entity", "Entity")][0]["props"]
    assert props["note"] == "it does not fix coordinator-side fencing"


def test_a_mention_whose_chunk_is_gone_is_dropped_and_counted(tmp_path):
    """`MENTIONS` starts at the chunk. A chunk the graph no longer has is a gap to report,
    not a reason to invent one."""
    plan_obj = plan(two_batches(tmp_path))
    before = len(plan_obj.mentions)
    gaps = drop_missing_chunks(plan_obj, {A})
    assert gaps["mentions_dropped_chunk_missing"] == before - len(plan_obj.mentions)
    assert gaps["chunk_ids_missing"] == [B]
    assert all(key[0] == A for key in plan_obj.mentions)


def test_an_entity_that_resolved_to_an_existing_node_mints_no_entity(tmp_path):
    path = written_batch(
        tmp_path,
        inp=batch_input([chunk_context(chunk_id=A, text="Streams clients do assignment")]),
        out=batch_output(
            entities=[
                entity(kind="Technology", name="streams", quote="Streams clients", chunk_id=A)
            ]
        ),
    )
    plan_obj = plan([screened(path, SCHEMA, FACTS)])
    assert plan_obj.entities == {}
    assert list(plan_obj.mentions) == [(A, "Component", "streams", "Technology")]
    row = plan_obj.mention_rows()["Component"][0]
    assert row["dst"] == "streams"
    # the Component node was written by `brain load` and knows nothing about extraction,
    # so what the extractor called it, and as what kind, lives on the edge or nowhere
    assert row["props"]["kind"] == "Technology"
    assert row["props"]["name"] == "streams"
    assert row["props"]["description"]


def test_planning_is_order_independent(tmp_path):
    """Two agents finishing in either order must produce the same graph."""
    batches = two_batches(tmp_path)
    forward = plan(batches)
    backward = plan(list(reversed(batches)))
    assert forward.entity_rows() == backward.entity_rows()
    assert forward.relation_rows() == backward.relation_rows()
    assert forward.mention_rows() == backward.mention_rows()


def test_a_rejected_batch_contributes_nothing(tmp_path):
    good, bad = two_batches(tmp_path)
    bad.errors.append("schema: something")
    assert plan([good, bad]).entities.keys() == plan([good]).entities.keys()


def test_ref_is_hashable_so_endpoints_can_be_compared():
    assert Ref("Entity", "Problem|x") == Ref("Entity", "Problem|x")
    assert len({Ref("Entity", "a"), Ref("Entity", "a"), Ref("Document", "a")}) == 2


def test_a_relation_outside_its_spec_shape_is_counted_not_dropped(tmp_path):
    """`MOTIVATED_BY` answers a Problem. Pointing it at a Feature is probably a mis-kind —
    worth a line in the report, not worth silently dropping the only edge that says why."""
    from brain.extract.merge import shape_warnings

    path = written_batch(
        tmp_path,
        inp=batch_input([chunk_context(chunk_id=A, text=TEXT)]),
        out=batch_output(
            entities=[
                entity(
                    kind="Decision",
                    name="move assignment",
                    quote="Move assignment",
                    chunk_id=A,
                ),
                entity(
                    kind="Feature",
                    name="coordinator side assignment",
                    quote="clients no longer do assignment",
                    chunk_id=A,
                ),
            ],
            relations=[
                relation(
                    type="MOTIVATED_BY",
                    source="move assignment",
                    target="coordinator side assignment",
                    evidence_chunk_id=A,
                )
            ],
        ),
    )
    plan_obj = plan([screened(path, SCHEMA, FACTS)])
    assert len(plan_obj.relations) == 1  # merged anyway
    warnings = shape_warnings(plan_obj)
    assert warnings["off_spec_shape"] == 1
    assert warnings["off_spec_by_type"] == {"MOTIVATED_BY": 1}
    assert "target is Entity:Feature" in warnings["off_spec_examples"][0]["why"]


def test_a_kip_rejecting_an_alternative_from_the_document_is_in_shape(tmp_path):
    """A "Rejected Alternatives" section often lists what was turned down without restating
    the decision. The KIP is the decider there, and spec 2.4's Decision->Alternative is the
    common case, not the only legal one."""
    from brain.extract.merge import shape_warnings

    path = written_batch(
        tmp_path,
        inp=batch_input([chunk_context(chunk_id=A, text=TEXT)]),
        out=batch_output(
            entities=[
                entity(
                    kind="Alternative",
                    name="client side assignment",
                    quote="clients no longer do assignment",
                    chunk_id=A,
                )
            ],
            relations=[
                relation(
                    type="REJECTS",
                    source="KIP-5",
                    target="client side assignment",
                    evidence_chunk_id=A,
                )
            ],
        ),
    )
    assert shape_warnings(plan([screened(path, SCHEMA, FACTS)]))["off_spec_shape"] == 0


def test_a_relation_in_its_spec_shape_raises_no_warning(tmp_path):
    from brain.extract.merge import shape_warnings

    assert shape_warnings(plan(two_batches(tmp_path)))["off_spec_shape"] == 0


def test_two_kinds_matching_one_component_stay_two_mentions(tmp_path):
    """A Technology "streams" and a Feature "streams" are both the component, but they are
    not the same claim. One mention would silently keep whichever was written second."""
    path = written_batch(
        tmp_path,
        inp=batch_input([chunk_context(chunk_id=A, text="Streams clients do assignment")]),
        out=batch_output(
            entities=[
                entity(
                    kind="Technology",
                    name="streams",
                    description="The Kafka Streams library.",
                    quote="Streams clients",
                    chunk_id=A,
                ),
                entity(
                    kind="Feature",
                    name="streams",
                    description="The streaming capability the clients use.",
                    quote="Streams clients do assignment",
                    chunk_id=A,
                ),
            ]
        ),
    )
    plan_obj = plan([screened(path, SCHEMA, FACTS)])
    assert sorted(plan_obj.mentions) == [
        (A, "Component", "streams", "Feature"),
        (A, "Component", "streams", "Technology"),
    ]
    assert {r["props"]["kind"] for r in plan_obj.mention_rows()["Component"]} == {
        "Technology",
        "Feature",
    }


def test_an_entity_keeps_every_description_and_shows_the_fullest(tmp_path):
    """The shortest description is usually a restatement of the name; `brain resolve` wants
    all of them and a reader wants the one that says something."""
    props = (
        plan(two_batches(tmp_path))
        .entities["Decision|move assignment to the group coordinator"]
        .props()
    )
    assert props["descriptions"] == sorted(props["descriptions"])
    assert props["description"] == max(props["descriptions"], key=len)


def _one_relation(tmp_path, src_kind, dst_kind, rel_type, note=None):
    out = batch_output(
        entities=[
            entity(kind=src_kind, name="assignment", quote="do assignment", chunk_id=A),
            entity(
                kind=dst_kind,
                name="group coordinator",
                quote="Move assignment to the group coordinator",
                chunk_id=A,
            ),
        ],
        relations=[
            relation(
                type=rel_type,
                source="assignment",
                target="group coordinator",
                evidence_chunk_id=A,
                **({"note": note} if note else {}),
            )
        ],
    )
    path = written_batch(tmp_path, inp=batch_input([chunk_context(chunk_id=A, text=TEXT)]), out=out)
    return plan([screened(path, SCHEMA, FACTS)])


def test_depends_on_between_two_technologies_is_in_shape(tmp_path):
    """Planner ruling, 2026-09-07: a dependency is stated between whatever two things the
    sentence names, and 235 of the first merge's 254 DEPENDS_ON were Technology to
    Technology. The shape follows the corpus."""
    from brain.extract.merge import shape_warnings

    plan_obj = _one_relation(tmp_path, "Technology", "Technology", "DEPENDS_ON")
    assert shape_warnings(plan_obj)["off_spec_shape"] == 0


def test_a_relation_outside_the_shapes_table_is_counted_not_dropped(tmp_path):
    """`INTRODUCES_RISK` must land on a Risk. Its source list used to be empty, which
    silently exempted the whole type from the check it exists for."""
    from brain.extract.merge import shape_warnings

    plan_obj = _one_relation(tmp_path, "Decision", "Technology", "INTRODUCES_RISK")
    assert len(plan_obj.relations) == 1  # merged anyway
    warnings = shape_warnings(plan_obj)
    assert warnings["off_spec_by_type"] == {"INTRODUCES_RISK": 1}
    assert "target is Entity:Technology" in warnings["off_spec_examples"][0]["why"]
    assert warnings["shapes"] == "brain/extract/shapes.json"


def test_a_relation_in_its_shape_raises_no_warning(tmp_path):
    from brain.extract.merge import shape_warnings

    assert shape_warnings(plan(two_batches(tmp_path)))["off_spec_shape"] == 0


# ------------------------------------------------------------------------- shapes table


def test_the_shapes_table_covers_the_closed_set_and_leaves_no_type_unchecked():
    """One source for the rule. An empty list means "any", which is how INTRODUCES_RISK was
    exempted by accident; every type now states both ends."""
    from brain.extract.merge import SHAPES_PATH, load_shapes

    raw = json.loads(SHAPES_PATH.read_text(encoding="utf-8"))
    assert "§2.4" in raw["spec"]
    shapes = load_shapes()
    assert sorted(shapes) == sorted(RELATION_TYPES)
    for rel_type, (sources, targets) in shapes.items():
        assert sources, f"{rel_type} has no source shape — it would never be checked"
        assert targets, f"{rel_type} has no target shape"
        assert raw["shapes"][rel_type]["note"], rel_type


def test_every_descriptor_in_the_shapes_table_is_one_this_step_can_produce():
    from brain.extract.graph import NODE_KEYS
    from brain.extract.merge import load_shapes

    allowed = set(NODE_KEYS) | {"Commit"} | {f"Entity:{k}" for k in KINDS}
    used = {d for shape in load_shapes().values() for side in shape for d in side}
    assert used <= allowed, used - allowed


# ------------------------------------------------------------------------- merged_at


def test_every_row_carries_the_merge_stamp_that_finds_withdrawn_facts(tmp_path):
    """`merged_at` is how the stale sweep sees what the current batches no longer declare;
    a row written without it would be invisible to that check forever."""
    plan_obj = plan(two_batches(tmp_path))
    plan_obj.merged_at = "2026-09-07T10:00:00+00:00"
    rows = [r["props"] for r in plan_obj.entity_rows()]
    rows += [r["props"] for g in plan_obj.mention_rows().values() for r in g]
    rows += [r["props"] for g in plan_obj.relation_rows().values() for r in g]
    assert rows
    assert all(r["merged_at"] == "2026-09-07T10:00:00+00:00" for r in rows)
    # and it is not the same field as extracted_at, which never moves
    created = {r["on_create"]["extracted_at"] for r in plan_obj.entity_rows()}
    assert plan_obj.merged_at not in created


# --------------------------------------------------------------------- precision sample


def test_the_judged_sample_is_read_from_the_sheet_never_computed(tmp_path):
    from brain.extract.merge import load_precision_sample

    assert load_precision_sample(tmp_path) is None
    (tmp_path / "extract_sample.json").write_text(
        json.dumps(
            {
                "seed": 7,
                "targets": "entity",
                "sample": [],
                "judged": {"n": 50, "supported": 46, "partial": 4, "not": 0},
            }
        ),
        encoding="utf-8",
    )
    judged = load_precision_sample(tmp_path)
    assert judged == {
        "seed": 7,
        "targets": "entity",
        "n": 50,
        "supported": 46,
        "partial": 4,
        "not": 0,
    }
