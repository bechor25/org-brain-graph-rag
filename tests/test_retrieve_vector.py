"""Lucene escaping and RRF — the two pieces of S1 that are pure functions."""

from __future__ import annotations

import pytest

from brain.retrieve.vector import KEY_BOOST, RRF_K, lucene_escape, rrf

SPECIALS = set('+-!(){}[]^"~*?:\\/&|')


def unescaped(text: str) -> list[str]:
    """Every Lucene metacharacter in `text` that is not preceded by a backslash."""
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char in SPECIALS:
            out.append(char)
        index += 1
    return out


@pytest.mark.parametrize(
    "raw",
    [
        "KAFKA-15123",
        'a "quote that never closes',
        "wildcards? and stars* and (parens)",
        "AND OR NOT && ||",
        "path/to/file.java",
        "^caret ~tilde :colon",
        "back\\slash",
        "{braces} [brackets] +plus -minus !bang",
        "מה השתנה ב-connect בין 3.6 ל-3.7?",
    ],
)
def test_no_lucene_metacharacter_ever_reaches_the_parser_unescaped(raw: str) -> None:
    """A safety test, not a syntax test: an unescaped `-` is NOT and a lone `"` is a crash."""
    assert unescaped(lucene_escape(raw, boost_keys=False)) == []


def test_bare_boolean_operators_are_dropped_rather_than_rewriting_the_query() -> None:
    assert lucene_escape("AND OR NOT", boost_keys=False) == ""
    assert lucene_escape("cats AND dogs", boost_keys=False) == "cats OR dogs"


def test_a_key_is_boosted_even_when_hebrew_glues_a_prefix_to_it() -> None:
    assert f"(KIP\\-848)^{KEY_BOOST}" in lucene_escape("למה נבחר ב-KIP-848?")
    assert f"(KIP\\-848)^{KEY_BOOST}" in lucene_escape("why KIP-848?")


def test_trailing_punctuation_is_not_part_of_the_term() -> None:
    assert "\\?" not in lucene_escape("why KIP-848?")


def test_an_empty_or_punctuation_only_query_produces_nothing() -> None:
    assert lucene_escape("") == ""
    assert lucene_escape("   ") == ""
    assert lucene_escape("?? !!") == ""


def test_boosting_can_be_switched_off() -> None:
    assert "^" not in lucene_escape("KIP-848", boost_keys=False)


def test_rrf_prefers_what_both_lists_agree_on() -> None:
    """The whole point of RRF: agreement beats one list's first place."""
    scores = rrf([["a", "shared"], ["b", "shared"]])
    assert scores["shared"] > scores["a"] == scores["b"]


def test_rrf_uses_rank_not_score_and_starts_at_one() -> None:
    scores = rrf([["only"]])
    assert scores["only"] == pytest.approx(1 / (RRF_K + 1))


def test_rrf_weights_a_list_down() -> None:
    scores = rrf([["a"], ["b"]], weights=[1.0, 0.1])
    assert scores["a"] > scores["b"]


def test_rrf_of_nothing_is_nothing() -> None:
    assert rrf([]) == {}
    assert rrf([[], []]) == {}
