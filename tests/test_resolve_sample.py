from __future__ import annotations

from brain.canon.models import Identity, Person
from brain.resolve.gold import GOLD_NAME, write_gold
from brain.resolve.ledger import ResolutionLedger
from brain.resolve.sample import (
    canonical_displays,
    graded_pairs,
    is_ungraded,
    merged_groups,
    render,
)
from tests.resolve_helpers import person


def ledger_with(*rows) -> ResolutionLedger:
    ledger = ResolutionLedger()
    for identity, canonical, tier, rule in rows:
        ledger.record("person", [(identity, canonical, {"tier": tier, "rule": rule, "score": 1.0})])
    ledger.available = True
    return ledger


def test_merged_groups_put_the_survivor_first():
    ledger = ledger_with(("git:b", "jira:a", 1, "email"), ("ado:c", "jira:a", 2, "embedding_auto"))
    assert merged_groups(ledger, "person") == [["jira:a", "ado:c", "git:b"]]


def test_a_group_the_gold_covers_is_not_ungraded():
    graded = {("ado:c", "jira:a")}
    assert is_ungraded(["jira:a", "git:b"], graded) is True
    assert is_ungraded(["jira:a", "ado:c"], graded) is False
    assert is_ungraded(["jira:a", "git:b", "ado:c"], graded) is False


def test_graded_pairs_reads_only_the_asked_for_kind(tmp_path):
    write_gold(
        tmp_path / GOLD_NAME,
        [
            {"kind": "person", "a": "a", "b": "b", "label": "same"},
            {"kind": "entity", "a": "Feature|a", "b": "Feature|b", "label": "same"},
        ],
    )
    assert graded_pairs(tmp_path, "person") == {("a", "b")}
    assert graded_pairs(tmp_path, "entity") == {("Feature|a", "Feature|b")}


def test_displays_come_from_the_canonical_file_because_the_nodes_are_gone(tmp_path):
    people = [
        Person(id="jira:a", identities=[Identity(source="jira", key="a", display="Ann A")]),
        Person(id="git:b", identities=[Identity(source="git", key="b", display="A. Ann")]),
    ]
    (tmp_path / "persons.jsonl").write_text(
        "\n".join(p.model_dump_json() for p in people) + "\n", encoding="utf-8"
    )
    assert canonical_displays(tmp_path, "person") == {"jira:a": "Ann A", "git:b": "A. Ann"}
    assert canonical_displays(tmp_path, "entity") == {}


def test_render_shows_every_identity_its_source_its_display_and_why_it_merged():
    ledger = ledger_with(("git:b", "jira:a", 2, "embedding_auto"))
    survivor = person(
        "jira:a",
        "Ann A",
        evidence=[("AUTHORED", "KAFKA-1", "a title"), ("COMMENTED", "ADO-1", "x", True)],
    )
    text = render(["jira:a", "git:b"], {"jira:a": survivor}, ledger, {"git:b": "A. Ann"})

    assert "jira:a   ← 1 merged   [Ann A]" in text
    assert "survivor" in text
    assert "'A. Ann'" in text  # the swallowed node's display, from the canonical file
    assert "tier 2 embedding_auto" in text
    assert "KAFKA-1" in text and "a title" in text
    assert "(synthetic)" in text  # the reader is told which evidence is invented


def test_render_survives_an_identity_the_canonical_file_no_longer_names():
    ledger = ledger_with(("git:b", "jira:a", 1, "email"))
    text = render(["jira:a", "git:b"], {}, ledger, {})
    assert "(display unknown)" in text
