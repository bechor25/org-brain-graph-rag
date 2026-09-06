"""The output contract: what `brain/extract/schema.json` accepts, and what it must not.

The first test is the one that keeps the rest honest. `jsonschema_mini` implements a subset
of JSON Schema and would *silently ignore* a keyword it does not know, so a schema using one
would validate less than it looks like it validates. `unsupported_keywords` turns that into
a failing test instead of a hole.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from brain.extract.models import KINDS, RELATION_TYPES, BatchOutput
from brain.synth.jsonschema_mini import unsupported_keywords
from brain.synth.jsonschema_mini import validate as schema_validate
from tests.extract_helpers import batch_output, entity, relation

SCHEMA_PATH = Path("brain/extract/schema.json")
FIXTURES = Path("tests/fixtures/extract/shard-01")


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_the_validator_implements_every_keyword_the_schema_uses(schema):
    assert unsupported_keywords(schema) == set()


def test_the_closed_sets_are_the_same_in_the_schema_and_in_the_models(schema):
    """A kind the schema allows and pydantic rejects would fail a batch at the second gate
    with a message about the first one. They are one contract in two languages."""
    assert schema["$defs"]["kind"]["enum"] == list(KINDS)
    assert schema["$defs"]["type"]["enum"] == list(RELATION_TYPES)
    assert "MENTIONS" not in schema["$defs"]["type"]["enum"]


@pytest.mark.parametrize("name", ["001.out.json", "002.out.json"])
def test_the_canned_agent_outputs_are_valid(schema, name):
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert schema_validate(payload, schema) == []
    assert BatchOutput.model_validate(payload).batch_id == payload["batch_id"]


def test_an_empty_batch_is_valid_output(schema):
    """A batch of boilerplate yields nothing, and that is an answer, not a failure."""
    payload = {"batch_id": "shard-02/007", "entities": [], "relations": []}
    assert schema_validate(payload, schema) == []


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (batch_output(entities=[entity(kind="Component")]), "not one of"),
        (batch_output(relations=[relation(type="CAUSES")]), "not one of"),
        (batch_output(relations=[relation(type="MENTIONS")]), "not one of"),
        (batch_output(entities=[entity(quote="x" * 301)]), "longer than 300"),
        (batch_output(entities=[entity(quote="short")]), "shorter than 8"),
        (batch_output(entities=[entity(name="x")]), "shorter than 2"),
        (batch_output(entities=[entity(chunk_id="not-a-sha")]), "does not match"),
        (batch_output(batch_id="shard-1/1"), "does not match"),
        (batch_output(entities=[{**entity(), "confidence": 0.9}]), "unexpected property"),
        (batch_output(entities=[{k: v for k, v in entity().items() if k != "quote"}]), "missing"),
    ],
)
def test_the_schema_rejects_a_batch_that_breaks_the_contract(schema, payload, expected):
    problems = schema_validate(payload, schema)
    assert problems, payload
    assert any(expected in p for p in problems), problems


def test_pydantic_says_why_mentions_is_not_an_agent_relation():
    with pytest.raises(ValidationError) as exc:
        BatchOutput.model_validate(batch_output(relations=[relation(type="MENTIONS")]))
    assert "minted by merge" in str(exc.value)


def test_pydantic_rejects_a_relation_from_a_thing_to_itself():
    with pytest.raises(ValidationError):
        BatchOutput.model_validate(
            batch_output(relations=[relation(source="Rebalance", target="rebalance ")])
        )
