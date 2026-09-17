"""The two batch contracts, and the rubric that explains one of them.

`brain/common/jsonschema_mini` implements a subset of JSON Schema and refuses to validate
against a keyword it does not enforce — which is what stops a contract from silently
accepting whatever it feels like. These tests run that guard over both schema files, then
check the handful of cross-file agreements that a schema cannot state about itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.common.jsonschema_mini import unsupported_keywords
from brain.common.jsonschema_mini import validate as schema_validate
from brain.eval import answers_batches as ab
from brain.eval import judge_batches as jb
from brain.eval import judge_report as jr

ANSWER_SCHEMA = json.loads(ab.SCHEMA_PATH.read_text(encoding="utf-8"))
JUDGE_SCHEMA = json.loads(jb.SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("schema", [ANSWER_SCHEMA, JUDGE_SCHEMA], ids=["answers", "judge"])
def test_the_schema_uses_only_keywords_the_validator_enforces(schema):
    assert unsupported_keywords(schema) == set()


def test_the_schema_files_are_where_the_agents_are_told_to_look():
    assert ab.SCHEMA_REF == f"brain/eval/{ab.SCHEMA_PATH.name}"
    assert jb.SCHEMA_REF == f"brain/eval/{jb.SCHEMA_PATH.name}"
    assert jb.RUBRIC_REF == f"brain/eval/{jb.RUBRIC_PATH.name}"
    for path in (ab.SCHEMA_PATH, jb.SCHEMA_PATH, jb.RUBRIC_PATH):
        assert path.is_file()


# ----------------------------------------------------------------------------- answers


def test_a_well_formed_answer_batch_validates():
    payload = {
        "batch_id": "shard-01/003",
        "answers": [
            {
                "case_id": "q001.s3",
                "answer": "Three tests cover it [KAFKA-14649].",
                "cited_keys": ["KAFKA-14649"],
                "confidence": "high",
            }
        ],
    }
    assert schema_validate(payload, ANSWER_SCHEMA) == []


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ({"batch_id": "003"}, "does not match"),
        ({"answers": []}, "needs at least 1"),
        ({"extra": 1}, "unexpected property"),
    ],
)
def test_a_broken_answer_envelope_is_refused(mutation, expected):
    payload = {
        "batch_id": "shard-01/003",
        "answers": [{"case_id": "q001.s3", "answer": "x", "cited_keys": [], "confidence": "none"}],
    }
    payload.update(mutation)
    errors = schema_validate(payload, ANSWER_SCHEMA)
    assert any(expected in e for e in errors), errors


def test_duplicate_cited_keys_are_refused():
    payload = {
        "batch_id": "shard-01/003",
        "answers": [
            {
                "case_id": "q001.s3",
                "answer": "x [KAFKA-1]",
                "cited_keys": ["KAFKA-1", "KAFKA-1"],
                "confidence": "low",
            }
        ],
    }
    assert any("unique" in e for e in schema_validate(payload, ANSWER_SCHEMA))


# ----------------------------------------------------------------------------- judge


def test_a_well_formed_judge_batch_validates():
    payload = {
        "batch_id": "shard-02/001",
        "scores": [
            {
                "case_id": "a1b2c3d4",
                "faithfulness": 2,
                "correctness": 1,
                "citation_validity": 2,
                "relevancy": 2,
                "unsupported_claims": [],
                "justification": 'the answer names "XT-10007" and the context item says so too',
            }
        ],
        "pairs": [
            {
                "pair_id": "ffee0011",
                "winner": "b",
                "justification": 'b names "XT-10008", which the gold answer lists and a omits',
            }
        ],
    }
    assert schema_validate(payload, JUDGE_SCHEMA) == []


def test_a_null_metric_is_allowed_by_the_schema_and_ruled_on_by_the_merge():
    """The schema cannot know whether a case had a context; `check_score` can."""
    score = {
        "case_id": "a1b2c3d4",
        "faithfulness": None,
        "correctness": 2,
        "citation_validity": 2,
        "relevancy": 2,
        "unsupported_claims": [],
        "justification": 'the answer says "yes" and the gold answer says yes too',
    }
    payload = {"batch_id": "shard-02/001", "scores": [score], "pairs": []}
    assert schema_validate(payload, JUDGE_SCHEMA) == []

    _, ok = jr.check_score(score, {"context_available": False})
    assert ok == []
    _, refused = jr.check_score(score, {"context_available": True})
    assert any("is null" in problem for problem in refused)


@pytest.mark.parametrize("value", [3, -1])
def test_a_metric_outside_zero_to_two_is_refused(value):
    payload = {
        "batch_id": "shard-02/001",
        "scores": [
            {
                "case_id": "a1b2c3d4",
                "faithfulness": value,
                "correctness": 2,
                "citation_validity": 2,
                "relevancy": 2,
                "unsupported_claims": [],
                "justification": 'the answer says "yes" and the gold answer says yes too',
            }
        ],
        "pairs": [],
    }
    assert schema_validate(payload, JUDGE_SCHEMA) != []


def test_a_winner_that_is_not_a_b_or_tie_is_refused():
    payload = {
        "batch_id": "shard-02/001",
        "scores": [],
        "pairs": [
            {"pair_id": "ffee0011", "winner": "both", "justification": 'a says "x", b does not'}
        ],
    }
    assert any("not one of" in e for e in schema_validate(payload, JUDGE_SCHEMA))


# ----------------------------------------------------------------------------- the rubric


def test_the_rubric_covers_every_metric_the_schema_requires():
    text = jb.RUBRIC_PATH.read_text(encoding="utf-8")
    required = JUDGE_SCHEMA["$defs"]["score"]["required"]
    for metric in jr.METRICS:
        assert metric in required
        assert metric in text, f"the rubric does not explain {metric}"
    assert "0" in text and "2" in text
    assert "randomised" in text, "the rubric must say the pairwise order carries no information"


def test_the_judge_agent_is_pointed_at_the_rubric_and_the_schema():
    agent = Path(jb.AGENT_REF)
    if not agent.is_file():  # pragma: no cover - only in a partial checkout
        pytest.skip(f"{agent} is not in this checkout")
    text = agent.read_text(encoding="utf-8")
    assert jb.RUBRIC_REF in text
    assert jb.SCHEMA_REF in text


def test_the_answering_agent_is_pointed_at_the_citation_format():
    agent = Path(ab.AGENT_REF)
    if not agent.is_file():  # pragma: no cover - only in a partial checkout
        pytest.skip(f"{agent} is not in this checkout")
    assert ab.CITATION_REF in agent.read_text(encoding="utf-8")
