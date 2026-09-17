"""The stratification arithmetic: how many questions to ask for, of what type, in what language.

This is the part of Task 1 that is pure arithmetic and therefore the part that must be
tested rather than eyeballed. The plan asks for "8 per type and ≥11 Hebrew" out of 32; five
types × 8 is 40, so 8-per-type is not reachable with 13 new questions, and the deficit
table is what the planner reads to see that. These tests pin the rule that replaces it —
water-filling, lowest type first, canonical order on a tie — so a later change to the
target shows up as a failing test and not as a quietly unbalanced question set.
"""

from __future__ import annotations

import pytest

from brain.eval.paths import QUESTION_TYPES
from brain.eval.questions import (
    DEFAULT_HEBREW_MIN,
    DEFAULT_SLACK,
    Demand,
    count_existing,
    plan_demand,
    water_fill,
)

#: The 19 competency questions, as `brain competency` builds them: 5/5/4/1/4 by type,
#: four of them Hebrew.
COMPETENCY = [
    {"type": "traceability", "lang": "en"},
    {"type": "traceability", "lang": "en"},
    {"type": "traceability", "lang": "en"},
    {"type": "traceability", "lang": "en"},
    {"type": "impact", "lang": "en"},
    {"type": "impact", "lang": "en"},
    {"type": "impact", "lang": "en"},
    {"type": "impact", "lang": "en"},
    {"type": "rationale", "lang": "en"},
    {"type": "rationale", "lang": "en"},
    {"type": "rationale", "lang": "en"},
    {"type": "global", "lang": "en"},
    {"type": "temporal", "lang": "en"},
    {"type": "temporal", "lang": "en"},
    {"type": "temporal", "lang": "en"},
    {"type": "traceability", "lang": "he"},
    {"type": "impact", "lang": "he"},
    {"type": "rationale", "lang": "he"},
    {"type": "temporal", "lang": "he"},
]


def test_water_fill_raises_the_lowest_bucket_first():
    assert water_fill([5, 5, 4, 1, 4], 13) == [7, 7, 6, 6, 6]


def test_water_fill_breaks_ties_by_position_so_the_plan_is_reproducible():
    assert water_fill([0, 0, 0], 4) == [2, 1, 1]


def test_water_fill_respects_a_per_bucket_capacity():
    """Hebrew demand cannot exceed the number of new questions a type is getting."""
    assert water_fill([1, 1, 1, 0, 1], 7, capacity=[2, 2, 2, 5, 2]) == [3, 2, 2, 2, 2]


def test_water_fill_stops_when_every_bucket_is_full_rather_than_looping():
    assert water_fill([0, 0], 10, capacity=[1, 1]) == [1, 1]


def test_counting_the_existing_set_splits_by_type_and_language():
    counts = count_existing(COMPETENCY)
    assert counts[("traceability", "en")] == 4
    assert counts[("traceability", "he")] == 1
    assert counts[("global", "en")] == 1
    assert counts[("global", "he")] == 0
    assert sum(counts.values()) == 19


@pytest.fixture
def demand() -> Demand:
    return plan_demand(COMPETENCY, new_total=13)


def test_the_new_questions_are_spread_so_no_type_is_left_at_one(demand):
    """`global` starts at 1 of 19 and takes the largest share — that is the whole point."""
    assert demand.need_by_type == {
        "traceability": 2,
        "impact": 2,
        "rationale": 2,
        "global": 5,
        "temporal": 2,
    }
    assert sum(demand.need_by_type.values()) == 13


def test_the_final_set_is_32_questions_as_even_per_type_as_13_new_ones_allow(demand):
    assert sum(demand.final_by_type.values()) == 32
    assert sorted(demand.final_by_type.values()) == [6, 6, 6, 7, 7]


def test_the_unreachable_eight_per_type_goal_is_reported_and_not_silently_dropped(demand):
    """32 questions over five types cannot be 8 each; the deficit table has to say so."""
    assert demand.per_type_goal == 8
    assert demand.goal_reachable is False
    assert demand.questions_for_goal == 40
    assert any("8 per type" in note for note in demand.notes)


def test_hebrew_reaches_the_floor_and_every_type_gets_at_least_one_hebrew_question(demand):
    he = {t: demand.final_by_cell[(t, "he")] for t in QUESTION_TYPES}
    assert sum(he.values()) >= DEFAULT_HEBREW_MIN
    assert min(he.values()) >= 1


def test_the_request_carries_slack_so_a_rejected_question_does_not_cost_a_cell(demand):
    requested = sum(c.request for c in demand.cells)
    assert requested == 17  # ceil(13 * 1.3)
    assert requested >= sum(c.need for c in demand.cells)
    for cell in demand.cells:
        assert cell.request >= cell.need


def test_slack_never_shrinks_a_cell_below_what_it_needs():
    tight = plan_demand(COMPETENCY, new_total=13, slack=0.0)
    assert sum(c.request for c in tight.cells) == 13
    assert all(c.request == c.need for c in tight.cells)


def test_the_plan_is_deterministic(demand):
    again = plan_demand(COMPETENCY, new_total=13)
    assert [c.as_dict() for c in again.cells] == [c.as_dict() for c in demand.cells]


def test_raising_the_hebrew_floor_moves_questions_into_hebrew_not_into_existence():
    more = plan_demand(COMPETENCY, new_total=13, hebrew_min=13)
    assert sum(c.need for c in more.cells) == 13
    assert sum(c.need for c in more.cells if c.lang == "he") == 9


def test_a_hebrew_floor_the_new_questions_cannot_reach_is_reported_not_faked():
    """13 new questions cannot make 25 of 32 Hebrew when only 4 already are."""
    impossible = plan_demand(COMPETENCY, new_total=13, hebrew_min=25)
    assert sum(c.need for c in impossible.cells) == 13
    assert impossible.hebrew_final == 17
    assert any("Hebrew" in note for note in impossible.notes)


def test_paths_wanted_covers_every_requested_question_plus_a_spare_per_type(demand):
    wanted = demand.paths_wanted(spares=1)
    for qtype in QUESTION_TYPES:
        asked = sum(c.request for c in demand.cells if c.type == qtype)
        assert wanted[qtype] == asked + 1
    assert sum(wanted.values()) == 17 + len(QUESTION_TYPES)


def test_the_defaults_are_the_plan_decision_not_a_magic_number():
    assert DEFAULT_HEBREW_MIN == 11
    assert DEFAULT_SLACK == pytest.approx(0.30)
