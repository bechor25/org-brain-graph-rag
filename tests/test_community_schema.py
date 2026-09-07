"""`brain/community/schema.json` is the contract; this is what makes it one.

Two ways it could quietly stop being a contract: a keyword `jsonschema_mini` does not
implement (which would be *ignored*, not rejected), and a rule the schema states that the
pydantic model does not — or the other way round. Both are asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from brain.common.jsonschema_mini import unsupported_keywords
from brain.common.jsonschema_mini import validate as schema_validate
from brain.community.models import BatchOutput, Report
from tests.community_helpers import A, batch_output, report

SCHEMA_PATH = Path("brain/community/schema.json")
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_the_validator_implements_every_keyword_the_schema_uses():
    """An unimplemented keyword would be silently ignored — a hole, not an error."""
    assert unsupported_keywords(SCHEMA) == set()


def test_the_schema_and_the_model_agree_on_a_valid_batch():
    payload = batch_output()
    assert schema_validate(payload, SCHEMA) == []
    assert BatchOutput.model_validate(payload).reports[0].community_id == "L0-1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("community_id", "L0"),
        ("community_id", "misc"),
        ("rank", -1),
        ("rank", 11),
        ("title", "   "),
    ],
)
def test_both_gates_reject_the_same_broken_report(field, value):
    """Identity, range and blankness are checked twice — they are not style questions."""
    payload = batch_output(reports=[report(**{field: value})])
    assert schema_validate(payload, SCHEMA), f"schema accepted {field}={value!r}"
    with pytest.raises(ValidationError):
        Report.model_validate(report(**{field: value}))


@pytest.mark.parametrize(("field", "value"), [("title", "x"), ("rank_reason", "no")])
def test_lengths_are_the_schemas_job_alone_and_merge_runs_the_schema_first(field, value):
    """A one-character title is a style rule; stating it twice is two places to drift.

    It is still enforced: `parse_batch` validates against the schema *before* pydantic and
    returns on the first failure, so nothing this short reaches a node.
    """
    payload = batch_output(reports=[report(**{field: value})])
    assert schema_validate(payload, SCHEMA), f"schema accepted {field}={value!r}"
    assert Report.model_validate(report(**{field: value}))


def test_the_schema_rejects_a_chunk_id_that_is_not_forty_hex_characters():
    payload = batch_output(
        reports=[report(findings=[{"statement": "a claim", "evidence_chunk_ids": ["KAFKA-1"]}])]
    )
    assert any("evidence_chunk_ids" in p for p in schema_validate(payload, SCHEMA))


def test_the_schema_rejects_the_same_chunk_cited_twice_in_one_finding():
    payload = batch_output(
        reports=[report(findings=[{"statement": "a claim about it", "evidence_chunk_ids": [A, A]}])]
    )
    assert any("unique" in p for p in schema_validate(payload, SCHEMA))


def test_the_schema_forbids_a_batch_with_no_reports_at_all():
    """A file with an empty `reports` is an agent silently skipping work."""
    assert schema_validate(batch_output(reports=[]), SCHEMA)


def test_the_schema_names_the_agent_contract_it_belongs_to():
    """The agent reads prose, not code; the description has to say the record-level rules."""
    text = SCHEMA["description"]
    assert "evidence" in text and "community_id" in text
    assert "brain/community/schema.json" == "brain/community/schema.json"


def test_the_agent_definition_points_at_this_schema():
    agent = Path(".claude/agents/community-summarizer.md").read_text(encoding="utf-8")
    assert "brain/community/schema.json" in agent
    for field in ("community_id", "title", "summary", "findings", "rank", "rank_reason"):
        assert field in agent, field
