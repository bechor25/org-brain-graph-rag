"""The batch-output contract: the schema itself, and the validator that enforces it."""

from __future__ import annotations

import json

import pytest

from brain.synth.build import SCHEMA_PATH
from brain.synth.jsonschema_mini import SchemaError, unsupported_keywords, validate
from brain.synth.models import BatchOutput
from tests.synth_helpers import ado_item, batch_output, synthetic_person, truth, xray_test

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def errors(payload) -> list[str]:
    return validate(payload, SCHEMA)


# ------------------------------------------------------------------ the validator itself


def test_the_schema_uses_nothing_the_validator_silently_ignores():
    """The guard that makes a hand-rolled validator safe: no unimplemented keyword."""
    assert unsupported_keywords(SCHEMA) == set()


def test_an_unimplemented_keyword_raises_rather_than_passing_quietly():
    with pytest.raises(SchemaError, match=r"oneOf"):
        validate({}, {"type": "object", "oneOf": [{"type": "object"}]})


@pytest.mark.parametrize(
    ("value", "schema", "expected"),
    [
        (1, {"type": "string"}, "expected string, got integer"),
        (True, {"type": "integer"}, "expected integer, got boolean"),
        ("x", {"type": "string", "minLength": 2}, "shorter than 2"),
        ("x", {"type": "string", "pattern": "^y"}, "does not match"),
        ("c", {"enum": ["a", "b"]}, "is not one of"),
        (False, {"const": True}, "must be True"),
        ([1], {"type": "array", "minItems": 2}, "at least 2 items"),
        ([1, 1], {"type": "array", "uniqueItems": True}, "must be unique"),
        ({"a": 1}, {"type": "object", "additionalProperties": False}, "unexpected property 'a'"),
        ({}, {"type": "object", "required": ["a"]}, "missing required property 'a'"),
        (0, {"type": "integer", "minimum": 1}, "< minimum 1"),
    ],
)
def test_each_supported_keyword_is_enforced(value, schema, expected):
    assert any(expected in e for e in validate(value, schema))


def test_null_is_accepted_where_the_type_list_allows_it():
    assert validate(None, {"type": ["string", "null"]}) == []


def test_every_violation_is_reported_not_just_the_first():
    schema = {"type": "object", "required": ["a", "b", "c"]}

    assert len(validate({}, schema)) == 3


# --------------------------------------------------------------------------- happy path


def test_a_well_formed_batch_passes_schema_and_model():
    payload = batch_output()

    assert errors(payload) == []
    assert BatchOutput.model_validate(payload).batch_id == "shard-01/001"


def test_the_schema_and_the_pydantic_model_require_the_same_top_level_fields():
    required = set(SCHEMA["required"])
    model_required = {n for n, f in BatchOutput.model_fields.items() if f.is_required()}

    assert model_required <= required
    assert required <= set(BatchOutput.model_fields)


def test_the_truth_block_requires_all_five_kinds_of_evidence():
    assert set(SCHEMA["$defs"]["truth"]["required"]) == {
        "identity_map",
        "text_only_links",
        "stale_states",
        "renames",
        "duplicate_tests",
    }


# --------------------------------------------------------------------------- rejections


def test_a_batch_whose_records_are_not_flagged_synthetic_is_rejected():
    payload = batch_output(workitems=[xray_test("XT-10001", synthetic=False)])

    assert any("synthetic" in e for e in errors(payload))


def test_a_key_outside_the_prefix_allowlist_is_rejected():
    item = xray_test("XT-10001") | {"key": "KAFKA-999", "id": "xray:KAFKA-999"}

    assert any("does not match" in e for e in errors(batch_output(workitems=[item])))


def test_the_schema_does_not_pair_a_prefix_with_its_source_merge_does():
    """`ado:XT-1` is well-formed JSON and a wrong record. Only merge knows the pairing."""
    payload = batch_output(workitems=[xray_test("XT-10001", id="ado:XT-10001", source="ado")])

    assert errors(payload) == []


def test_an_unknown_work_item_type_is_rejected():
    payload = batch_output(workitems=[xray_test("XT-10001", type="Spike")])

    assert any("is not one of" in e for e in errors(payload))


def test_an_invented_email_is_rejected():
    person = synthetic_person("rao.jun", "jira:jrao", "Rao, Jun")
    person["identities"][0]["email"] = "jun@example.com"

    assert any("email" in e for e in errors(batch_output(persons=[person])))


def test_a_truth_block_missing_a_kind_of_evidence_is_rejected():
    payload = batch_output(truth={"identity_map": {}, "text_only_links": []})

    assert any("stale_states" in e for e in errors(payload))


def test_the_spec_spelling_of_a_text_only_link_does_not_pass_the_schema_unnormalised():
    """The schema is the unified pair; `merge.normalise_truth` is what accepts the other."""
    payload = batch_output(
        truth=truth(text_only_links=[{"ado_key": "ADO-10001", "jira_key": "KAFKA-100"}])
    )

    assert any("from_key" in e for e in errors(payload))


def test_an_unexpected_top_level_key_is_rejected():
    payload = batch_output(extra="surprise")

    assert any("unexpected property 'extra'" in e for e in errors(payload))


def test_a_batch_id_that_is_not_a_shard_path_is_rejected():
    assert any("batch_id" in e for e in errors(batch_output(batch_id="001")))


def test_a_container_id_that_disagrees_with_its_kind_is_rejected():
    payload = batch_output(
        containers=[
            {
                "id": "ado:component:clients",
                "source": "ado",
                "kind": "sprint",
                "name": "Sprint 2024-01",
                "synthetic": True,
            }
        ]
    )

    assert any("$.containers[0].id" in e for e in errors(payload))


def test_an_ado_item_still_needs_a_description():
    payload = batch_output(workitems=[ado_item("ADO-10001", description="")])

    assert any("description" in e for e in errors(payload))
