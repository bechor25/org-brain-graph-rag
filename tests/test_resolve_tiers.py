from __future__ import annotations

from brain.resolve.models import Candidate
from brain.resolve.tiers import (
    ADJUDICATE_FLOOR,
    AUTO_THRESHOLD,
    band,
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
            entity("Feature|a", "a"),
            entity("Feature|b", "b"),
            entity("Feature|c", "c"),
            entity("Technology|a", "a", block="Technology"),
        ]
    }
    auto, grey = tier2_pairs(
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
    by_id = {c.id: c for c in [entity("Feature|a", "a"), entity("Feature|b", "b")]}
    auto, grey = tier2_pairs(
        [("Feature|a", "Feature|b", 0.85), ("Feature|b", "Feature|a", 0.93)], by_id
    )
    assert not grey and [p.score for p in auto] == [0.93]


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
