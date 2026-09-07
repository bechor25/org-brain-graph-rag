"""Version arithmetic and the question-set builder's pure halves."""

from __future__ import annotations

import pytest

from brain.retrieve.competency import TEMPLATES, _midpoint_date, _render, _version_pair
from brain.retrieve.temporal import _bound, _pad, version_key


def test_versions_sort_numerically_not_lexicographically() -> None:
    """`"3.10.0" < "3.9.0"` as strings, which is the wrong window for changes_between."""
    assert version_key("3.10.0") > version_key("3.9.0")
    assert sorted(["3.10.0", "3.9.0", "3.9.1"], key=version_key) == ["3.9.0", "3.9.1", "3.10.0"]


def test_a_two_part_bound_covers_its_whole_family() -> None:
    low, high = _pad(_bound("3.6", ceiling=True)), _pad(_bound("3.7", ceiling=True))
    assert not low < _pad(version_key("3.6.2")) <= high
    assert low < _pad(version_key("3.7.0")) <= high
    assert not low < _pad(version_key("3.8.0")) <= high


def test_an_exact_three_part_bound_is_exclusive_then_inclusive() -> None:
    low, high = _pad(_bound("3.6.0", ceiling=True)), _pad(_bound("3.7.0", ceiling=True))
    assert not low < _pad(version_key("3.6.0")) <= high
    assert low < _pad(version_key("3.6.1")) <= high
    assert low < _pad(version_key("3.7.0")) <= high


def test_version_key_of_nonsense_is_not_a_crash() -> None:
    assert version_key("") == (0,)
    assert version_key("trunk") == (0,)


def test_version_pair_picks_the_busiest_adjacent_minors() -> None:
    assert _version_pair(["3.6.0=10", "3.7.0=90", "4.0.0=5"]) == ("3.6", "3.7")
    assert _version_pair(["2.1.0=1", "2.2.0=1", "3.6.0=50", "3.7.0=50"]) == ("3.6", "3.7")


def test_version_pair_falls_back_when_no_two_minors_are_adjacent() -> None:
    assert _version_pair(["1.0.0=5", "9.9.0=5"]) == ("3.6", "3.7")
    assert _version_pair([]) == ("3.6", "3.7")


def test_midpoint_date_lands_inside_the_history() -> None:
    stamps = ["2024-03-02T10:00:00Z", "2024-01-15T08:00:00Z", "2024-02-01T00:00:00Z"]
    assert _midpoint_date(stamps) == "2024-02-01"
    assert _midpoint_date([]) == "2024-03-01"


def test_render_substitutes_keys_and_titles() -> None:
    text, used = _render("Why {kip} ({kip!t})?", {"kip": "KIP-848"}, {"kip": "The Next Generation"})
    assert text == "Why KIP-848 (The Next Generation)?"
    assert used == ["kip", "kip"]


def test_render_drops_a_question_whose_anchor_is_missing() -> None:
    text, _ = _render("Why {absent}?", {"kip": "KIP-1"}, {})
    assert text is None


@pytest.mark.parametrize(("qid", "qtype", "lang", "strategy", "pair", "template"), TEMPLATES)
def test_every_template_is_well_formed(qid, qtype, lang, strategy, pair, template) -> None:
    assert qid.startswith("cq")
    assert qtype in {"traceability", "impact", "rationale", "temporal", "global"}
    assert lang in {"en", "he"}
    assert strategy in {"s1", "s2", "s3", "s4", "s5", "s6", "lookup"}
    assert "{" in template, "a competency question with no anchor is a hand-written question"


def test_the_set_is_fifteen_english_and_four_hebrew_with_matched_pairs() -> None:
    english = [t for t in TEMPLATES if t[2] == "en"]
    hebrew = [t for t in TEMPLATES if t[2] == "he"]
    assert len(english) == 15 and len(hebrew) == 4
    assert {t[4] for t in hebrew} == {t[4] for t in english if t[4]}
    assert len({t[0] for t in TEMPLATES}) == len(TEMPLATES)
