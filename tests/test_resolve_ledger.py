from __future__ import annotations

import json

import pytest

from brain.resolve.ledger import LEDGER_NAME, LedgerError, ResolutionLedger


def meta(tier=1, rule="email", score=1.0):
    return {"tier": tier, "rule": rule, "score": score, "reason": "r"}


def test_a_missing_ledger_is_empty_not_an_error(tmp_path):
    ledger = ResolutionLedger.load(tmp_path)
    assert not ledger.available
    assert ledger.canonical("person", "jira:jrao") == "jira:jrao"
    assert ledger.status() == "not_available"


def test_an_unreadable_ledger_is_loud_because_load_would_resurrect_everything(tmp_path):
    (tmp_path / LEDGER_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(LedgerError):
        ResolutionLedger.load(tmp_path)


def test_a_row_without_a_canonical_id_is_refused(tmp_path):
    (tmp_path / LEDGER_NAME).write_text(
        json.dumps({"persons": {"git:x": {"tier": 1}}, "entities": {}}), encoding="utf-8"
    )
    with pytest.raises(LedgerError):
        ResolutionLedger.load(tmp_path)


def test_a_later_tier_that_moves_a_survivor_moves_everything_pointing_at_it(tmp_path):
    ledger = ResolutionLedger()
    ledger.record("person", [("ado:x", "jira:a", meta())])
    ledger.record("person", [("jira:a", "jira:b", meta(tier=2, rule="embedding_auto"))])

    assert ledger.canonical("person", "ado:x") == "jira:b"
    assert ledger.canonical("person", "jira:a") == "jira:b"
    # `via` keeps the hop each tier actually made, so tier 1 can still be scored alone.
    assert ledger.persons["ado:x"]["via"] == "jira:a"
    assert ledger.persons["ado:x"]["tier"] == 1
    assert ledger.persons["jira:a"]["tier"] == 2


def test_recording_an_identity_as_its_own_canonical_is_a_no_op(tmp_path):
    ledger = ResolutionLedger()
    ledger.record("person", [("jira:a", "jira:a", meta())])
    assert ledger.persons == {}


def test_a_cycle_is_dropped_rather_than_followed_forever():
    ledger = ResolutionLedger()
    ledger.record("person", [("a", "b", meta())])
    ledger.record("person", [("b", "a", meta(tier=2))])
    # Whatever survives, nothing points at itself and lookup terminates.
    for identity, entry in ledger.persons.items():
        assert entry["canonical"] != identity


def test_round_trip_through_disk_keeps_every_field(tmp_path):
    ledger = ResolutionLedger()
    ledger.record("person", [("ado:x", "jira:a", meta())])
    ledger.record("entity", [("Feature|b", "Feature|a", meta(tier=2, rule="embedding_auto"))])
    ledger.write(tmp_path)

    again = ResolutionLedger.load(tmp_path)
    assert again.available
    assert again.counts() == {"persons": 1, "entities": 1}
    assert again.canonical("entity", "Feature|b") == "Feature|a"
    assert again.merged_into("person") == {"jira:a": ["ado:x"]}
    assert again.tier_of("person", "jira:a") == 1


def test_tier_of_a_survivor_is_the_weakest_evidence_that_built_it():
    ledger = ResolutionLedger()
    ledger.record("person", [("ado:x", "jira:a", meta(tier=1))])
    ledger.record("person", [("git:y", "jira:a", meta(tier=3, rule="adjudicator_same"))])
    assert ledger.tier_of("person", "jira:a") == 3
