from __future__ import annotations

import pytest

from brain.resolve.models import Candidate
from brain.resolve.tiers import (
    ADJUDICATE_FLOOR,
    AUTO_THRESHOLD,
    band,
    band_table,
    dedupe,
    entity_tier1,
    groups,
    person_tier1,
    tier2_pairs,
)
from tests.resolve_helpers import entity, person


def rules(pairs):
    return {(p.a, p.b): p.rule for p in pairs}


def test_email_rule_merges_two_identities_that_share_an_address():
    pairs = person_tier1(
        [
            person("git:jun@example.org", "Jun Rao", email="jun@example.org"),
            person("ado:rao.jun.10001", "Rao, Jun", email="JUN@EXAMPLE.ORG"),
            person("jira:other", "Other"),
        ]
    )
    assert rules(pairs) == {("ado:rao.jun.10001", "git:jun@example.org"): "email"}


def test_jira_username_matching_the_git_author_name_is_deterministic():
    pairs = person_tier1(
        [
            person("jira:nikita-shupletsov", "Nikita Shupletsov"),
            person("git:nikita@shupletsov.ca", "nikita shupletsov"),
        ]
    )
    assert rules(pairs) == {("git:nikita@shupletsov.ca", "jira:nikita-shupletsov"): "jira_git_name"}


def test_identical_display_alone_is_not_enough_without_shared_activity():
    same_name = [
        person("jira:gharris", "Greg Harris"),
        person("jira:gharris1727", "Greg Harris"),
    ]
    assert person_tier1(same_name) == []


def test_identical_display_plus_a_shared_item_is_a_tier_one_merge():
    pairs = person_tier1(
        [
            person("jira:jrao", "Jun Rao", evidence=[("COMMENTED", "KAFKA-1", "t")]),
            person("git:jun@example.org", "Jun Rao", evidence=[("AUTHORED", "KAFKA-1", "t")]),
        ]
    )
    assert rules(pairs) == {("git:jun@example.org", "jira:jrao"): "display_and_activity"}
    assert "KAFKA-1" in pairs[0].reason


def test_a_shared_synthetic_item_is_not_activity_tier_one_may_reason_from():
    """The generator deliberately put two different "G. Harris" on ADO-20068; "both touched
    it" there is a fact about the injected noise, not about who they are."""
    pairs = person_tier1(
        [
            person("ado:a", "Greg Harris", evidence=[("ASSIGNED_TO", "ADO-20068", "t", True)]),
            person("ado:b", "Greg Harris", evidence=[("ASSIGNED_TO", "ADO-20068", "t", True)]),
        ]
    )
    assert pairs == []


def test_an_initials_only_display_never_merges_at_tier_one_either():
    pairs = person_tier1(
        [
            person("jira:a", "G. Harris", evidence=[("COMMENTED", "KAFKA-1", "t")]),
            person("git:b@x.org", "G. Harris", evidence=[("AUTHORED", "KAFKA-1", "t")]),
        ]
    )
    assert pairs == []


def test_a_real_shared_item_still_merges_when_a_synthetic_one_is_also_present():
    pairs = person_tier1(
        [
            person(
                "jira:jrao",
                "Jun Rao",
                evidence=[("COMMENTED", "ADO-1", "t", True), ("COMMENTED", "KAFKA-1", "t")],
            ),
            person(
                "git:jun@x.org",
                "Jun Rao",
                evidence=[("AUTHORED", "ADO-1", "t", True), ("AUTHORED", "KAFKA-1", "t")],
            ),
        ]
    )
    assert rules(pairs) == {("git:jun@x.org", "jira:jrao"): "display_and_activity"}
    assert "KAFKA-1" in pairs[0].reason


def test_alias_candidates_from_load_are_taken_as_deterministic():
    people = [person("jira:JIRAUSER302322", "Lucas Brutschy"), person("jira:lucasbru", "lucasbru")]
    pairs = person_tier1(
        people,
        alias_candidates=[
            {
                "changelog_identity": "jira:JIRAUSER302322",
                "field_identity": "jira:lucasbru",
                "work_items": 69,
                "examples": ["KAFKA-14624"],
            }
        ],
    )
    assert rules(pairs) == {("jira:JIRAUSER302322", "jira:lucasbru"): "alias_candidates"}
    assert "KAFKA-14624" in pairs[0].reason


def test_an_alias_candidate_naming_a_person_the_graph_does_not_have_is_skipped():
    pairs = person_tier1(
        [person("jira:lucasbru", "lucasbru")],
        alias_candidates=[
            {"changelog_identity": "jira:JIRAUSER999", "field_identity": "jira:lucasbru"}
        ],
    )
    assert pairs == []


def test_a_confluence_userkey_that_is_a_jira_username_merges():
    pairs = person_tier1([person("confluence:mjsax", "Matthias Sax"), person("jira:mjsax", "M")])
    assert rules(pairs) == {("confluence:mjsax", "jira:mjsax"): "confluence_userkey"}


def test_entity_tier1_groups_by_normalised_name_inside_one_kind_only():
    pairs = entity_tier1(
        [
            entity("Feature|rebalance protocol", "Rebalance Protocol"),
            entity("Feature|rebalance protocols", "rebalance protocol"),
            entity("Technology|rebalance protocol", "Rebalance Protocol", block="Technology"),
        ]
    )
    assert rules(pairs) == {
        ("Feature|rebalance protocol", "Feature|rebalance protocols"): "norm_name"
    }


def test_the_band_is_the_two_thresholds_the_spec_names():
    assert band(AUTO_THRESHOLD) == "auto" and band(0.99) == "auto"
    assert band(ADJUDICATE_FLOOR) == "grey" and band(0.9199) == "grey"
    assert band(0.7999) == "reject"


def test_tier2_splits_the_band_and_never_crosses_a_block():
    by_id = {
        c.id: c
        for c in [
            entity("Feature|a", "one two"),
            entity("Feature|b", "one two"),
            entity("Feature|c", "one two"),
            entity("Technology|a", "one two", block="Technology"),
        ]
    }
    auto, grey, _ = tier2_pairs(
        [
            ("Feature|a", "Feature|b", 0.95),
            ("Feature|a", "Feature|c", 0.85),
            ("Feature|a", "Technology|a", 0.99),
            ("Feature|b", "Feature|c", 0.10),
        ],
        by_id,
    )
    assert [(p.a, p.b) for p in auto] == [("Feature|a", "Feature|b")]
    assert [(p.a, p.b) for p in grey] == [("Feature|a", "Feature|c")]


def test_tier2_keeps_the_higher_score_when_the_knn_returns_a_pair_twice():
    by_id = {c.id: c for c in [entity("Feature|a", "one two"), entity("Feature|b", "one two")]}
    auto, grey, _ = tier2_pairs(
        [("Feature|a", "Feature|b", 0.85), ("Feature|b", "Feature|a", 0.93)], by_id
    )
    assert not grey and [p.score for p in auto] == [0.93]


def test_an_initials_only_pair_is_demoted_to_the_band_not_merged_and_not_lost():
    """The measured failure mode: bge-m3 scores "S. An" against "S. An" at 1.0, and they
    are two different people. Demoted, not dropped — a reader can still merge them."""
    by_id = {c.id: c for c in [person("ado:a", "S. An"), person("ado:b", "S. An")]}
    auto, grey, filtered = tier2_pairs([("ado:a", "ado:b", 1.0)], by_id)

    assert auto == []
    assert [(p.a, p.b) for p in grey] == [("ado:a", "ado:b")]
    assert filtered["demoted_by_name_guard"] == 1
    assert "initials" in grey[0].reason


def test_the_guard_can_be_turned_off_to_reproduce_the_briefed_behaviour():
    by_id = {c.id: c for c in [person("ado:a", "S. An"), person("ado:b", "S. An")]}
    auto, _grey, _ = tier2_pairs([("ado:a", "ado:b", 1.0)], by_id, guard=False)
    assert [(p.a, p.b) for p in auto] == [("ado:a", "ado:b")]


def test_blocking_drops_a_grey_pair_whose_names_share_nothing():
    by_id = {c.id: c for c in [person("jira:a", "Jun Rao"), person("jira:b", "Bruno Cadonna")]}
    _auto, grey, filtered = tier2_pairs([("jira:a", "jira:b", 0.85)], by_id)

    assert grey == [] and filtered["dropped_by_blocking"] == 1
    _auto, unblocked, _ = tier2_pairs([("jira:a", "jira:b", 0.85)], by_id, blocking=False)
    assert len(unblocked) == 1


@pytest.mark.parametrize(
    ("name_a", "name_b", "kept"),
    [
        ("S. An", "Sanghyeok An", True),  # shared surname
        ("S. An", "Shichao An", True),  # the hard pair the adjudicator exists for
        ("Cadonna, Bruno", "Bruno Cadonna", True),  # permutation
        ("Jun Rao", "Bruno Cadonna", False),  # nothing in common
    ],
)
def test_blocking_keeps_every_name_a_person_would_recognise(name_a, name_b, kept):
    by_id = {c.id: c for c in [person("jira:a", name_a), person("jira:b", name_b)]}
    _auto, grey, _ = tier2_pairs([("jira:a", "jira:b", 0.85)], by_id)
    assert bool(grey) is kept


def test_blocking_matches_on_a_username_stem_when_the_displays_do_not():
    a = person("jira:chickenchickenlove", "Sanghyeok An")
    b = person("ado:x.10115", "someone else", identities=["ado:chickenchickenlove.10115"])
    _auto, grey, _ = tier2_pairs([(a.id, b.id, 0.85)], {a.id: a, b.id: b})
    assert len(grey) == 1


def test_the_band_table_prices_every_floor_with_and_without_blocking():
    by_id = {
        c.id: c
        for c in [
            person("jira:a", "Jun Rao"),
            person("jira:b", "Bruno Cadonna"),
            person("jira:c", "Jun Rao Two"),
        ]
    }
    rows = band_table(
        [("jira:a", "jira:b", 0.84), ("jira:a", "jira:c", 0.84)], by_id, floors=(0.80, 0.85)
    )
    by = {(r["floor"], r["blocking"]): r for r in rows}
    assert by[(0.80, True)]["grey_pairs"] == 1  # the unrelated pair is blocked out
    assert by[(0.80, False)]["grey_pairs"] == 2
    assert by[(0.85, True)]["grey_pairs"] == 0  # both are under the floor


def test_dedupe_keeps_the_earlier_tier():
    from brain.resolve.models import make_pair

    kw = dict(kind="person", block="person", reason="r")
    kept = dedupe(
        [
            make_pair("a", "b", tier=2, rule="embedding_auto", score=0.99, **kw),
            make_pair("b", "a", tier=1, rule="email", score=1.0, **kw),
        ]
    )
    assert [p.tier for p in kept] == [1]


def test_groups_are_connected_components():
    from brain.resolve.models import make_pair

    kw = dict(kind="person", block="person", tier=1, rule="email", score=1.0, reason="r")
    assert groups(
        [make_pair("a", "b", **kw), make_pair("b", "c", **kw), make_pair("x", "y", **kw)]
    ) == [["a", "b", "c"], ["x", "y"]]


def test_embed_text_is_the_name_plus_capped_evidence():
    c: Candidate = person(
        "jira:jrao",
        "Jun Rao",
        evidence=[("COMMENTED", f"KAFKA-{i}", f"title {i}") for i in range(9)],
    )
    text = c.embed_text(max_evidence=5)
    assert text.splitlines()[0] == "Jun Rao"
    assert len(text.splitlines()) == 6
    assert entity("Feature|x", "X", description="a thing").embed_text() == "X — a thing"
