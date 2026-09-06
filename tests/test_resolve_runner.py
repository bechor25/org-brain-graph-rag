from __future__ import annotations

import json

import pytest

from brain.resolve.ledger import ResolutionLedger
from brain.resolve.runner import (
    _merge_report,
    derived_baseline,
    load_alias_candidates,
    merges_by,
    resolve_kinds,
    resolve_tiers,
    survivor_rows,
)
from tests.resolve_helpers import person


def ledger_with(*rows):
    ledger = ResolutionLedger()
    for identity, canonical, tier, rule in rows:
        ledger.record("person", [(identity, canonical, {"tier": tier, "rule": rule, "score": 1.0})])
    ledger.available = True
    return ledger


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("all", ["person", "entity"]),
        ("person", ["person"]),
        ("entity,person", ["person", "entity"]),
    ],
)
def test_resolve_kinds_accepts_the_closed_set(value, expected):
    assert resolve_kinds(value) == expected


@pytest.mark.parametrize("value", ["", "people", "person,entities"])
def test_resolve_kinds_refuses_anything_else(value):
    with pytest.raises(ValueError):
        resolve_kinds(value)


def test_resolve_tiers_are_sorted_and_bounded():
    assert resolve_tiers("all") == [1, 2, 3]
    assert resolve_tiers("2,1,1") == [1, 2]
    for bad in ("4", "0", "two", ""):
        with pytest.raises(ValueError):
            resolve_tiers(bad)


def test_merges_by_counts_the_whole_ledger_not_one_run():
    ledger = ledger_with(
        ("a", "x", 1, "email"), ("b", "x", 1, "email"), ("c", "x", 2, "embedding_auto")
    )
    assert merges_by(ledger, "person", "tier") == {"1": 2, "2": 1}
    assert merges_by(ledger, "person", "rule") == {"email": 2, "embedding_auto": 1}
    assert merges_by(ledger, "entity", "tier") == {}


def test_the_baseline_is_reconstructed_from_the_ledger_and_says_so():
    before = {"nodes": 1425, "identities": 2187, "key": "id"}
    ledger = ledger_with(*[(f"i{n}", "x", 1, "email") for n in range(762)])
    baseline = derived_baseline(before, ledger, "person")

    assert baseline["nodes"] == 2187
    assert baseline["identities_per_node"] == 1.0
    assert baseline["duplicate_rate"] == 0.0
    assert baseline["derived"] is True


def test_an_untouched_graph_is_its_own_baseline():
    before = {"nodes": 10, "identities": 10, "key": "id"}
    assert derived_baseline(before, ResolutionLedger(), "person") == {**before, "derived": False}


def test_survivor_rows_keep_what_an_earlier_tier_already_merged():
    """Tier 3 extends the list tier 1 wrote; it does not replace it."""
    members = {
        "jira:jrao": person(
            "jira:jrao",
            "Jun Rao",
            identities=["jira:jrao", "git:junrao@example.org"],
            merged_from=["git:junrao@example.org"],
            resolution_tier=1,
        ),
        "confluence:rao.jun": person(
            "confluence:rao.jun", "Rao, Jun", identities=["confluence:rao.jun"]
        ),
    }
    keep, props = survivor_rows(
        "person", sorted(members), members, tier=3, stamp="2026-09-06T00:00:00+00:00"
    )

    assert keep == "jira:jrao"
    assert props["merged_from"] == ["confluence:rao.jun", "git:junrao@example.org"]
    assert props["identity_keys"] == ["confluence:rao.jun", "git:junrao@example.org", "jira:jrao"]
    assert props["aliases"] == ["Rao, Jun"]
    assert props["resolution_tier"] == 3


def test_the_tier_of_a_survivor_is_the_weakest_evidence_in_its_history():
    """A person tier 3 assembled and tier 1 then extended is still a tier-3 person."""
    members = {
        "jira:a": person("jira:a", "A", resolution_tier=3),
        "git:b@x.org": person("git:b@x.org", "A"),
    }
    _, props = survivor_rows("person", sorted(members), members, tier=1, stamp="s")
    assert props["resolution_tier"] == 3


def test_alias_candidates_come_from_the_load_report_and_survive_its_absence(tmp_path):
    assert load_alias_candidates(tmp_path) == []
    (tmp_path / "load.json").write_text("{not json", encoding="utf-8")
    assert load_alias_candidates(tmp_path) == []
    (tmp_path / "load.json").write_text(
        json.dumps({"alias_candidates": [{"changelog_identity": "a", "field_identity": "b"}, 7]}),
        encoding="utf-8",
    )
    assert load_alias_candidates(tmp_path) == [{"changelog_identity": "a", "field_identity": "b"}]


def test_the_report_remembers_earlier_runs_and_replaces_only_this_ones_keys(tmp_path):
    path = tmp_path / "resolve.json"
    _write = lambda payload, history: path.write_text(  # noqa: E731
        json.dumps(_merge_report(path, payload, history)), encoding="utf-8"
    )
    _write({"step": "resolve", "person": {"tier1": 1}, "eval": {"kept": True}}, {"at": "1"})
    _write({"step": "resolve", "person": {"tier2": 2}}, {"at": "2"})

    final = json.loads(path.read_text(encoding="utf-8"))
    assert [r["at"] for r in final["runs"]] == ["1", "2"]
    assert final["person"] == {"tier2": 2}  # the section is this run's
    assert final["eval"] == {"kept": True}  # another command's section is untouched
