"""S3's ranking: degree × edge weight × (1 + cos(question, entity)).

Before the semantic factor, two different questions that named the same key returned
byte-identical lists — the anchor decided everything and the *question* decided nothing:

    "Why was the design in KIP-848 chosen?"            → KIP-932, Problem|…, KIP-848, …
    "Which commits fixed the bug behind KIP-848 …?"    → KIP-932, Problem|…, KIP-848, …

That is a retrieval that cannot be evaluated: no ranking metric can tell the two apart, and
neither can a reader. The neighbourhood is still chosen by the anchor and the edges — which
is what keeps the Hebrew and English forms of one question on the same subgraph — and the
question's embedding only decides the *order* inside it.

These tests are over the pure ranking function, so they need no database: `sim` is what the
graph would have returned from `vector.similarity.cosine(entity.embedding, $question)`.
"""

from __future__ import annotations

import pytest

from brain.retrieve.local import (
    MIN_FACTOR,
    NEUTRAL_FACTOR,
    rank_neighbourhood,
    semantic_factor,
)

ANCHOR = [{"key": "KIP-848", "label": "Document", "title": "The Next Generation"}]


def _neighbour(key: str, path: list[str], sim: float | None, hops: int = 1) -> dict:
    return {
        "anchor": "KIP-848",
        "key": key,
        "label": "Entity",
        "hops": hops,
        "path": path,
        "sim": sim,
    }


# ------------------------------------------------------------------------------- factor


def test_neo4j_normalised_cosine_becomes_the_one_plus_cos_of_the_formula() -> None:
    """`vector.similarity.cosine` returns `(1+cos)/2`, so the factor is twice it."""
    assert semantic_factor(1.0) == pytest.approx(2.0)  # cos = 1
    assert semantic_factor(0.5) == pytest.approx(1.0)  # cos = 0
    assert semantic_factor(0.0) == pytest.approx(MIN_FACTOR)  # cos = -1, floored


def test_recentring_makes_the_factor_independent_of_the_absolute_cosine_level() -> None:
    """A Hebrew question sits lower on the same band; only the order may survive that."""
    english = [0.70, 0.60, 0.50]
    hebrew = [0.62, 0.52, 0.42]
    en = [semantic_factor(s, sum(english) / 3) for s in english]
    he = [semantic_factor(s, sum(hebrew) / 3) for s in hebrew]
    assert en == pytest.approx(he)


def test_an_average_node_is_worth_exactly_its_graph_evidence() -> None:
    assert semantic_factor(0.6, center=0.6) == pytest.approx(NEUTRAL_FACTOR)


def test_a_node_with_no_embedding_is_neither_boosted_nor_punished() -> None:
    """Only `Entity` carries a vector; a WorkItem must not lose for having no opinion."""
    assert semantic_factor(None) == NEUTRAL_FACTOR == 1.0


# ------------------------------------------------------------------------------ ranking


def test_two_questions_on_one_key_no_longer_return_the_same_top_three() -> None:
    """The regression this fix exists for (planner decision, Plan 2 Task 3)."""
    rationale = [
        _neighbour("Decision|move assignment", ["DECIDES"], 0.90),
        _neighbour("Alternative|client side", ["REJECTS"], 0.85),
        _neighbour("Feature|fencing", ["MENTIONS"], 0.30),
        _neighbour("Technology|coordinator", ["MENTIONS"], 0.32),
        _neighbour("Problem|long rebalances", ["MOTIVATED_BY"], 0.80),
    ]
    traceability = [
        {**row, "sim": sim}
        for row, sim in zip(rationale, [0.30, 0.28, 0.88, 0.86, 0.31], strict=True)
    ]
    a = [key for key, _ in rank_neighbourhood(ANCHOR, rationale, k=3)]
    b = [key for key, _ in rank_neighbourhood(ANCHOR, traceability, k=3)]
    assert a != b, "the question still decides nothing"


def test_the_neighbourhood_itself_does_not_change_only_its_order() -> None:
    """Cross-lingual behaviour lives here: the anchor and the edges pick the subgraph."""
    rows = [
        _neighbour("Decision|a", ["DECIDES"], 0.90),
        _neighbour("Alternative|b", ["REJECTS"], 0.20),
    ]
    other = [{**r, "sim": 1.0 - (r["sim"] or 0)} for r in rows]
    assert {k for k, _ in rank_neighbourhood(ANCHOR, rows, k=10)} == {
        k for k, _ in rank_neighbourhood(ANCHOR, other, k=10)
    }


def test_edge_weight_still_beats_a_slightly_better_cosine() -> None:
    """A `MENTIONS` co-occurrence must not outrank a `DECIDES` on 0.05 of cosine."""
    rows = [
        _neighbour("Decision|strong", ["DECIDES"], 0.60),
        _neighbour("Entity|mentioned", ["MENTIONS"], 0.65),
    ]
    ranked = dict(rank_neighbourhood(ANCHOR, rows, k=10))
    assert ranked["Decision|strong"]["score"] > ranked["Entity|mentioned"]["score"]


def test_degree_still_counts_every_path_that_reaches_the_node() -> None:
    rows = [
        _neighbour("Entity|agreed", ["MENTIONS"], 0.50),
        {**_neighbour("Entity|agreed", ["MENTIONS"], 0.50), "anchor": "KAFKA-1"},
        _neighbour("Entity|once", ["MENTIONS"], 0.50),
    ]
    ranked = dict(rank_neighbourhood(ANCHOR, rows, k=10))
    assert ranked["Entity|agreed"]["score"] > ranked["Entity|once"]["score"]
    assert ranked["Entity|agreed"]["path_count"] == 2


def test_the_scored_entry_shows_both_halves_of_the_product() -> None:
    """A ranking nobody can read is a ranking nobody can debug."""
    rows = [_neighbour("Entity|x", ["DECIDES"], 0.75), _neighbour("Entity|y", ["DECIDES"], 0.65)]
    ranked = dict(rank_neighbourhood(ANCHOR, rows, k=5))
    entry = ranked["Entity|x"]
    assert entry["degree_score"] == pytest.approx(1.0)
    assert entry["semantic"] == pytest.approx(1.1)  # centre is the pair's mean, 0.70
    assert entry["score"] == pytest.approx(1.1)
    assert ranked["Entity|y"]["semantic"] == pytest.approx(0.9)


def test_an_unembedded_node_sits_where_the_average_embedded_one_does() -> None:
    """Not at `cos = 0`: that would be a 50% bonus for carrying a vector at all."""
    rows = [
        _neighbour("Entity|close", ["MENTIONS"], 0.80),
        _neighbour("Entity|far", ["MENTIONS"], 0.40),
        _neighbour("KAFKA-1", ["MENTIONS"], None),
    ]
    ranked = dict(rank_neighbourhood(ANCHOR, rows, k=10))
    assert ranked["KAFKA-1"]["semantic"] == pytest.approx(NEUTRAL_FACTOR)
    assert ranked["Entity|close"]["score"] > ranked["KAFKA-1"]["score"]
    assert ranked["KAFKA-1"]["score"] > ranked["Entity|far"]["score"]


def test_ranking_is_deterministic_when_scores_tie() -> None:
    rows = [_neighbour("Entity|b", ["MENTIONS"], 0.5), _neighbour("Entity|a", ["MENTIONS"], 0.5)]
    assert [k for k, _ in rank_neighbourhood(ANCHOR, rows, k=10)] == [
        k for k, _ in rank_neighbourhood(ANCHOR, list(reversed(rows)), k=10)
    ]


def test_without_a_question_vector_the_ranking_is_the_old_degree_times_weight() -> None:
    """Ollama being down degrades S3's order; it must not empty S3's answer."""
    rows = [
        _neighbour("Decision|a", ["DECIDES"], None),
        _neighbour("Entity|b", ["MENTIONS"], None),
    ]
    ranked = dict(rank_neighbourhood(ANCHOR, rows, k=10))
    assert ranked["Decision|a"]["score"] == pytest.approx(1.0)
    assert ranked["Entity|b"]["score"] == pytest.approx(0.35)
