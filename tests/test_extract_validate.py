"""What merge lets into the graph, record by record — and what fails a whole batch.

The distinction is the module's whole design: a bad *file* is not a data problem (nothing in
it can be trusted), a bad *claim* is (the rest of the batch is still work).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.extract import validate as validate_mod
from brain.extract.merge import MAX_RETRIES, handle_failure
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
CHUNK = "a" * 40
OTHER = "b" * 40

FACTS = GraphFacts(
    workitem_keys=frozenset({"KAFKA-100"}),
    document_keys=frozenset({"KIP-5"}),
    components={"streams": "streams", "connect": "connect"},
)


def run(tmp_path: Path, out: dict, inp: dict | None = None, facts: GraphFacts | None = None):
    path = written_batch(tmp_path, inp=inp or batch_input(), out=out)
    return screened(path, SCHEMA, facts if facts is not None else FACTS)


# ---------------------------------------------------------------- record-level rejection


def test_an_entity_whose_quote_is_not_in_the_chunk_is_rejected_and_the_batch_survives(tmp_path):
    """The verbatim quote is the whole audit trail: a paraphrase answers "why does the brain
    believe this" with something the chunk never said."""
    batch = run(
        tmp_path,
        batch_output(
            entities=[entity(quote="causes lengthy rebalances"), entity(name="assignment")]
        ),
    )
    assert batch.ok
    assert [r.reason for r in batch.rejections] == ["quote_not_verbatim"]
    assert [a.entity.name for a in batch.entities] == ["assignment"]


def test_a_quote_that_differs_only_in_whitespace_is_accepted(tmp_path):
    text = "The classic protocol\nmakes clients   do assignment."
    batch = run(
        tmp_path,
        batch_output(entities=[entity(quote="makes clients do assignment")]),
        inp=batch_input([chunk_context(text=text)]),
    )
    assert batch.rejections == []
    assert len(batch.entities) == 1


def test_an_entity_citing_a_chunk_from_another_batch_is_rejected(tmp_path):
    batch = run(tmp_path, batch_output(entities=[entity(chunk_id=OTHER)]))
    assert [r.reason for r in batch.rejections] == ["chunk_not_in_batch"]
    assert batch.entities == []


def test_a_relation_whose_evidence_is_not_in_the_batch_is_rejected(tmp_path):
    batch = run(
        tmp_path,
        batch_output(
            entities=[entity(), entity(kind="Decision", name="move assignment")],
            relations=[
                relation(
                    source="move assignment",
                    target="long rebalances",
                    evidence_chunk_id=OTHER,
                )
            ],
        ),
    )
    assert [r.reason for r in batch.rejections] == ["chunk_not_in_batch"]


def test_a_relation_pointing_at_nothing_this_batch_declared_is_rejected(tmp_path):
    batch = run(
        tmp_path,
        batch_output(
            entities=[entity()],
            relations=[relation(source="KAFKA-99999", target="long rebalances")],
        ),
    )
    assert [r.reason for r in batch.rejections] == ["unresolved_endpoint"]
    assert "KAFKA-99999" in batch.rejections[0].detail


def test_a_name_extracted_as_two_kinds_is_not_guessed_between(tmp_path):
    """`rebalance` as a Problem and as a Technology is two nodes. Choosing one would fuse
    them, and no later step could tell that a choice had been made."""
    both = [
        entity(kind="Problem", name="rebalance", quote="causes long rebalances"),
        entity(kind="Technology", name="rebalance", quote="causes long rebalances"),
        entity(kind="Decision", name="move assignment", quote="do assignment"),
    ]
    batch = run(
        tmp_path,
        batch_output(
            entities=both,
            relations=[relation(source="move assignment", target="rebalance")],
        ),
        inp=batch_input(
            [chunk_context(text="clients do assignment, which causes long rebalances.")]
        ),
    )
    assert len(batch.entities) == 3
    assert [r.reason for r in batch.rejections] == ["unresolved_endpoint"]


@pytest.mark.parametrize(
    ("out", "reason"),
    [
        (batch_output(entities=[entity(kind="Component")]), "entity:unknown_kind"),
        (batch_output(entities=[entity(quote="x" * 400)]), "entity:quote_too_long"),
        (batch_output(entities=[{**entity(), "confidence": 0.9}]), "entity:schema_violation"),
    ],
)
def test_a_bad_entity_costs_the_entity_and_not_the_batch(tmp_path, out, reason):
    """Forty good extractions do not deserve quarantine because the forty-first said
    `Component` where it meant `Technology`."""
    out["entities"].append(entity(name="assignment"))
    batch = run(tmp_path, out)
    assert batch.ok, batch.errors
    assert validate_mod.reasons([batch]) == {reason: 1}
    assert [a.entity.name for a in batch.entities] == ["assignment"]


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (relation(type="CAUSES"), "relation:unknown_type"),
        (relation(type="MENTIONS"), "relation:unknown_type"),
        (relation(source="long rebalances", target="Long  Rebalances"), "relation:self_loop"),
    ],
)
def test_a_bad_relation_costs_the_relation_and_not_the_batch(tmp_path, bad, reason):
    batch = run(
        tmp_path,
        batch_output(
            entities=[entity(), entity(kind="Decision", name="move assignment")],
            relations=[
                bad,
                relation(source="move assignment", target="long rebalances"),
            ],
        ),
    )
    assert batch.ok, batch.errors
    assert validate_mod.reasons([batch]) == {reason: 1}
    assert len(batch.relations) == 1


def test_two_names_that_resolve_to_one_node_are_a_self_loop_too(tmp_path):
    """`KIP-5` and `kip-5` are one Document; an edge between them is a loop on a node."""
    batch = run(
        tmp_path,
        batch_output(
            entities=[],
            relations=[relation(type="DEPENDS_ON", source="KIP-5", target="kip-5")],
        ),
    )
    assert [r.reason for r in batch.rejections] == ["self_loop"]


# ------------------------------------------------------------------- key / component match


def test_an_entity_named_like_an_existing_key_links_that_node_instead_of_minting_one(tmp_path):
    batch = run(
        tmp_path,
        batch_output(entities=[entity(kind="Feature", name="KIP-5", quote="do assignment")]),
        inp=batch_input([chunk_context(text="clients do assignment for KIP-5")]),
    )
    assert [a.ref for a in batch.entities] == [Ref("Document", "KIP-5")]


@pytest.mark.parametrize("kind", ["Technology", "Feature"])
def test_a_technology_or_feature_named_like_a_component_links_the_component(tmp_path, kind):
    batch = run(
        tmp_path,
        batch_output(entities=[entity(kind=kind, name="Streams", quote="do assignment")]),
        inp=batch_input([chunk_context(text="Streams clients do assignment")]),
    )
    assert [a.ref for a in batch.entities] == [Ref("Component", "streams")]


@pytest.mark.parametrize("kind", ["Problem", "Risk", "Alternative", "Decision"])
def test_a_problem_named_like_a_component_stays_its_own_entity(tmp_path, kind):
    """ "streams" as a Problem is a problem *with* streams. Collapsing it into the component
    would answer "what is going wrong in streams" with the component node itself."""
    batch = run(
        tmp_path,
        batch_output(entities=[entity(kind=kind, name="streams", quote="do assignment")]),
        inp=batch_input([chunk_context(text="Streams clients do assignment")]),
    )
    assert [a.ref for a in batch.entities] == [Ref("Entity", f"{kind}|stream")]


def test_a_key_still_matches_whatever_kind_it_was_extracted_as(tmp_path):
    """A key names one record. Only the *component* half of the match is kind-gated."""
    batch = run(
        tmp_path,
        batch_output(entities=[entity(kind="Problem", name="KIP-5", quote="do assignment")]),
        inp=batch_input([chunk_context(text="clients do assignment for KIP-5")]),
    )
    assert [a.ref for a in batch.entities] == [Ref("Document", "KIP-5")]


def test_a_relation_endpoint_prefers_this_batch_s_own_entity_over_the_component(tmp_path):
    """If the batch decided "streams" is a Problem, that is what its relations mean by it."""
    batch = run(
        tmp_path,
        batch_output(
            entities=[
                entity(kind="Problem", name="streams", quote="do assignment"),
                entity(kind="Decision", name="move assignment", quote="do assignment"),
            ],
            relations=[relation(source="move assignment", target="streams")],
        ),
        inp=batch_input([chunk_context(text="Streams clients do assignment")]),
    )
    assert batch.rejections == []
    assert batch.relations[0].target == Ref("Entity", "Problem|stream")


def test_a_key_the_graph_does_not_hold_becomes_an_entity_not_an_invented_node(tmp_path):
    """`KAFKA-99999` in a KIP is a reference outside the harvested slice. Minting a work
    item for it would put a node in the graph that no source ever described."""
    batch = run(
        tmp_path,
        batch_output(entities=[entity(kind="Feature", name="KAFKA-99999", quote="do assignment")]),
        inp=batch_input([chunk_context(text="clients do assignment, see KAFKA-99999")]),
    )
    assert [a.ref.label for a in batch.entities] == ["Entity"]


# ------------------------------------------------------------------- batch-level rejection


def test_unreadable_json_fails_the_batch(tmp_path):
    path = written_batch(tmp_path, inp=batch_input(), out=batch_output())
    path.write_text("{not json", encoding="utf-8")
    batch = screened(path, SCHEMA, FACTS)
    assert not batch.ok
    assert "unreadable" in batch.errors[0]


def test_a_batch_id_that_is_not_the_file_fails_the_batch(tmp_path):
    batch = run(tmp_path, batch_output(batch_id="shard-02/009"))
    assert not batch.ok
    assert "batch_id is" in batch.errors[0]


def test_an_output_with_no_input_beside_it_fails_the_batch(tmp_path):
    """Without the input there is nothing to check the quotes against, and a merge that
    skipped the check would write unverifiable provenance."""
    path = written_batch(tmp_path, inp=batch_input(), out=batch_output())
    path.with_name("001.in.json").unlink()
    batch = screened(path, SCHEMA, FACTS)
    assert not batch.ok
    assert "no input beside it" in batch.errors[0]


def test_an_entities_field_that_is_not_an_array_fails_the_batch(tmp_path):
    """The envelope is what the batch-level gate is about; a record is not."""
    batch = run(tmp_path, batch_output(entities={"kind": "Problem"}))
    assert not batch.ok
    assert any("envelope:" in e for e in batch.errors)


def test_a_missing_top_level_field_fails_the_batch(tmp_path):
    batch = run(tmp_path, {"batch_id": "shard-01/001", "entities": [entity()]})
    assert not batch.ok
    assert any("relations" in e for e in batch.errors)


def test_an_unknown_top_level_field_fails_the_batch(tmp_path):
    batch = run(tmp_path, batch_output(confidence=0.9))
    assert not batch.ok
    assert any("unexpected property" in e for e in batch.errors)


# ---------------------------------------------------------------------------- retry flow


def test_a_failing_batch_goes_to_retry_and_is_quarantined_after_two_more_tries(tmp_path):
    path = written_batch(tmp_path, inp=batch_input(), out=batch_output(batch_id="shard-02/009"))
    ledger: dict = {}
    states = []
    for attempt in range(MAX_RETRIES + 1):
        batch = screened(path, SCHEMA, FACTS)
        assert not batch.ok
        batch.sha256 = f"regenerated-{attempt}"  # the agent rewrote it and it is still bad
        entry = handle_failure(batch, ledger)
        ledger[batch.batch_id] = entry
        states.append(entry["state"])
        assert Path(entry["path"]).is_file()

    assert states == ["retry"] * MAX_RETRIES + ["quarantine"]
    assert Path(ledger["shard-01/001"]["path"]).parent.name == "quarantine"
    # the retry copy is gone: a batch is in one place, never in both
    assert not (path.parent / "retry" / "001.in.json").exists()


def test_an_unchanged_bad_batch_does_not_burn_an_attempt(tmp_path):
    """Two idle merges must not quarantine work the agent never got the chance to redo."""
    path = written_batch(tmp_path, inp=batch_input(), out=batch_output(batch_id="shard-02/009"))
    batch = screened(path, SCHEMA, FACTS)
    batch.sha256 = "same"
    first = handle_failure(batch, {})
    second = handle_failure(batch, {batch.batch_id: first})
    assert first["attempt"] == second["attempt"] == 1


def test_reasons_counts_rejections_by_record_and_reason(tmp_path):
    batch = run(tmp_path, batch_output(entities=[entity(quote="nowhere in the text at all")]))
    assert validate_mod.reasons([batch]) == {"entity:quote_not_verbatim": 1}


@pytest.mark.parametrize("name", ["001", "002"])
def test_the_canned_fixture_pair_screens_clean(name):
    """The `make smoke` fixture must have nothing to reject — a smoke run that always had
    one rejection would stop being read."""
    batch = screened(
        Path(f"tests/fixtures/extract/shard-01/{name}.out.json"),
        SCHEMA,
        GraphFacts(
            workitem_keys=frozenset({"KAFKA-100", "KAFKA-101"}),
            document_keys=frozenset({"KIP-5"}),
            components={"streams": "streams"},
        ),
    )
    assert batch.ok, batch.errors
    assert batch.rejections == [], [r.row() for r in batch.rejections]
    assert batch.entities and batch.relations


def test_cross_batch_misses_say_whether_a_second_pass_would_pay(tmp_path):
    """An endpoint one batch could not resolve but another batch declared is the argument
    for a cross-batch pass; a miss nobody declared is genuinely outside the corpus."""
    declaring = run(
        tmp_path,
        batch_output(entities=[entity(name="long rebalances")]),
    )
    missing = run(
        tmp_path,
        batch_output(
            batch_id="shard-02/001",
            entities=[entity(kind="Decision", name="move assignment")],
            relations=[
                relation(source="move assignment", target="long rebalances"),
                relation(source="move assignment", target="something nobody wrote down"),
            ],
        ),
        inp=batch_input(batch_id="shard-02/001", shard="shard-02"),
    )
    stats = validate_mod.cross_batch_misses([declaring, missing])
    assert stats["unresolved_endpoint_names"] == 2
    assert stats["unresolved_endpoint_matches_other_batch"] == 1
    assert stats["unresolved_endpoint_examples"][0]["name"] == "long rebalances"
