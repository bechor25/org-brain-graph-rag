"""`norm_name`, the verbatim quote check, and what counts as an existing key."""

from __future__ import annotations

import pytest

from brain.extract.names import (
    canonical_key,
    entity_id,
    key_kind,
    norm_name,
    normalise_quote,
    quote_found,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Group Coordinator", "group coordinator"),
        ("group.coordinator.rebalance", "group coordinator rebalance"),
        ("  Long   rebalances  ", "long rebalance"),
        ("Consumer's offsets", "consumer offset"),
        ("KIP-848: the protocol", "kip 848 the protocol"),
        ("Brokers", "broker"),
        ("Metrics", "metrics"),
        ("Status", "status"),
        ("Retries", "retry"),
        ("classes", "class"),
    ],
)
def test_norm_name_is_lowercase_unpunctuated_and_naively_singular(raw, expected):
    assert norm_name(raw) == expected


def test_norm_name_refuses_a_name_that_normalises_to_nothing():
    with pytest.raises(ValueError):
        norm_name("...")


def test_entity_id_is_kind_and_norm_name():
    """Spec 2.4: `Entity` merges on `(kind, norm_name)`, and the id is how that is spelled."""
    assert entity_id("Problem", "Long Rebalances") == "Problem|long rebalance"
    assert entity_id("Feature", "long rebalances") == "Feature|long rebalance"
    assert entity_id("Problem", "long rebalance") == entity_id("Problem", "Long  Rebalances")


TEXT = "The classic protocol makes clients\ndo   assignment, which causes long rebalances."


@pytest.mark.parametrize(
    "quote",
    [
        "causes long rebalances",
        "makes clients do assignment",  # the newline and the double space are forgiven
        "The classic protocol",
    ],
)
def test_a_quote_is_found_across_whitespace(quote):
    assert quote_found(quote, TEXT)


@pytest.mark.parametrize(
    "quote",
    [
        "causes lengthy rebalances",  # a paraphrase, not a quote
        "makes Clients do assignment",  # case is identity, not whitespace
        "causes long rebalances in streams",  # stitched from somewhere else
        "",
    ],
)
def test_a_quote_that_is_not_there_is_not_found(quote):
    assert not quote_found(quote, TEXT)


def test_normalise_quote_collapses_every_kind_of_space():
    assert normalise_quote(" a b\n\tc  ") == "a b c"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("KIP-848", "Document"),
        ("kip-848", "Document"),
        ("KAFKA-16046", "WorkItem"),
        ("XT-10007", "WorkItem"),
        ("ADO-42", "WorkItem"),
        ("group coordinator", None),
        ("UTF-8", "WorkItem"),  # shape only; whether the node exists is the graph's answer
        ("consumer-1", None),
    ],
)
def test_key_kind_says_which_lookup_a_name_deserves(name, expected):
    assert key_kind(name) == expected


def test_canonical_key_upper_cases_the_way_the_graph_spells_it():
    assert canonical_key(" kip-848 ") == "KIP-848"
