"""Tier 1 and tier 2 for entities, and the username-stem rule for people."""

from __future__ import annotations

import pytest

from brain.resolve.models import Evidence, make_pair
from brain.resolve.names import GENERIC_STEMS, username_stem
from brain.resolve.tiers import (
    cap_band,
    entity_auto_ok,
    entity_tier1,
    generic_stems_seen,
    kip_title_links,
    person_tier1,
    tier2_pairs,
)
from tests.resolve_helpers import entity, person


def with_parent(node_id: str, name: str, parent: str, block: str = "Feature", **kw):
    c = entity(node_id, name, block, **kw)
    c.evidence = [
        Evidence(role="MENTIONS", key="c" * 40, title="a quote"),
        Evidence(role="PARENT", key=parent, title=f"title of {parent}"),
    ]
    return c


# ------------------------------------------------------------------- username stem


def test_the_same_username_in_three_systems_is_one_stem():
    assert (
        username_stem("ado:danica.fine.10077")
        == username_stem("git:danica.fine@gmail.com")
        == username_stem("jira:danicafine")
        == "danicafine"
    )
    assert username_stem("git:51072200+frankvicky@users.noreply.github.com") == "frankvicky"
    assert username_stem("ado:kfk2.gharris") == "gharris"


@pytest.mark.parametrize(
    "node_id",
    [
        "jira:LSK",  # under five characters
        "confluence:8aa980816ee92258016f0f72a35d00ec",  # holds digits: an id, not a name
        "jira:kafka",  # a generic word
        "ado:s3.10115",  # nothing left after the namespace and the id
    ],
)
def test_a_stem_that_is_not_a_name_is_refused(node_id):
    assert username_stem(node_id) is None


def test_the_stem_rule_merges_across_systems_only():
    pairs = person_tier1(
        [
            person("jira:danicafine", "Danica Fine"),
            person("ado:danica.fine.10077", "danica.fine"),
            person("git:danica.fine@gmail.com", "Danica Fine"),
        ]
    )
    assert {p.rule for p in pairs} == {"username_stem"}
    assert len(pairs) == 3
    assert "danicafine" in pairs[0].reason


def test_two_identities_of_one_system_never_merge_on_a_stem():
    """Within one system a username is already unique, so a collision means over-stemming."""
    assert person_tier1([person("jira:a.b.cdef", "X"), person("jira:ab.cdef", "Y")]) == []


def test_a_generic_stem_is_refused_and_named_in_the_report():
    people = [person("jira:kafka", "Kafka Bot"), person("git:kafka@apache.org", "Kafka Bot")]
    assert person_tier1(people) == []
    assert generic_stems_seen(people) == {"kafka": 2}
    assert "kafka" in GENERIC_STEMS


# --------------------------------------------------------------------- entity tier 1


def test_a_feature_named_after_a_kip_links_to_the_document_and_does_not_merge():
    candidates = [
        entity("Feature|versioned state store", "Versioned State Stores"),
        entity("Technology|versioned state store", "Versioned state stores", block="Technology"),
        entity("Decision|versioned state store", "Versioned state stores", block="Decision"),
    ]
    titles = {"KIP-889: Versioned State Stores": "KIP-889"}
    links = kip_title_links(candidates, titles)

    assert [(r["src"], r["dst"]) for r in links] == [
        ("Feature|versioned state store", "KIP-889"),
        ("Technology|versioned state store", "KIP-889"),
    ]
    assert links[0]["props"]["rule"] == "kip_title_alias"
    # …and none of it is a merge
    assert entity_tier1(candidates) == []


def test_the_kip_number_prefix_is_not_part_of_the_name():
    links = kip_title_links(
        [
            entity(
                "Feature|next generation consumer rebalance protocol",
                "next generation consumer rebalance protocol",
            )
        ],
        {"KIP-848: The Next Generation of the Consumer Rebalance Protocol": "KIP-848"},
    )
    assert [r["dst"] for r in links] == ["KIP-848"]


# --------------------------------------------------------------------- entity tier 2


def test_two_entities_merge_unasked_only_when_the_names_are_the_same_words():
    a = with_parent(
        "Decision|introduce a new stopped state", "introduce a new stopped state", "KIP-1"
    )
    b = with_parent("Decision|introduce a stopped state", "introduce a stopped state", "KIP-1")
    assert entity_auto_ok(a, b) is True


def test_a_discriminating_word_stops_an_automatic_merge():
    """`rebalance latency avg` and `rebalance latency max` are two metrics, and bge-m3
    scores them above 0.92. A dry run chained eight of them into one node."""
    a = with_parent("Technology|rebalance latency avg", "rebalance latency avg", "KIP-1")
    b = with_parent("Technology|rebalance latency max", "rebalance latency max", "KIP-1")
    assert entity_auto_ok(a, b) is False

    auto, grey, filtered = tier2_pairs(
        [(a.id, b.id, 0.97)], {a.id: a, b.id: b}, auto_extra=entity_auto_ok
    )
    assert auto == [] and len(grey) == 1
    assert filtered["demoted_by_auto_extra"] == 1
    assert "no shared word and no shared parent" in grey[0].reason


def test_similarity_with_nothing_in_common_never_merges():
    a = with_parent("Decision|use a queue", "use a queue", "KIP-1")
    b = with_parent("Decision|use a stack", "use a stack", "KIP-2")
    assert entity_auto_ok(a, b) is False


def test_people_are_not_subject_to_the_entity_gate():
    a, b = person("jira:a", "Jun Rao"), person("git:b@x.org", "Jun Rao")
    auto, _grey, _ = tier2_pairs([(a.id, b.id, 0.99)], {a.id: a, b.id: b})
    assert len(auto) == 1


# ------------------------------------------------------------------------ band cap


def kw(score):
    return dict(
        kind="entity", block="Feature", tier=2, rule="embedding_grey", score=score, reason="r"
    )


def test_the_cap_keeps_the_most_answerable_pairs_not_the_highest_scoring():
    a = with_parent("Feature|consumer rebalance protocol", "consumer rebalance protocol", "KIP-1")
    b = with_parent(
        "Feature|the consumer rebalance protocol", "the consumer rebalance protocol", "KIP-1"
    )
    c = with_parent("Feature|zookeeper removal", "zookeeper removal", "KIP-9")
    d = with_parent("Feature|tiered storage", "tiered storage", "KIP-8")
    by_id = {x.id: x for x in (a, b, c, d)}
    shared = make_pair(a.id, b.id, **kw(0.84))
    unrelated = make_pair(c.id, d.id, **kw(0.91))

    kept, cut_off = cap_band([unrelated, shared], by_id, limit=1)
    assert [p.key for p in kept] == [shared.key]  # 0.84 with three shared words wins
    assert cut_off is not None


def test_a_band_under_the_limit_is_untouched_and_reports_no_cut_off():
    a, b = entity("Feature|a", "a b"), entity("Feature|b", "a b")
    pair = make_pair(a.id, b.id, **kw(0.85))
    kept, cut_off = cap_band([pair], {a.id: a, b.id: b}, limit=10)
    assert kept == [pair] and cut_off is None
