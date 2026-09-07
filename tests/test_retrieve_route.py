"""The deterministic router and the key regexes it stands on.

Twenty-two questions, English and Hebrew, covering every rule and — more importantly —
the collisions between them: a question that names a key *and* a date, a question whose
"last execution status" is not a date, a Hebrew sentence whose letters are `\\w` to `re`.
"""

from __future__ import annotations

import pytest

from brain.retrieve.keys import find_keys, key_kind
from brain.retrieve.route import route

# question, expected strategy, expected rule family
ROUTING_CASES: list[tuple[str, str, str]] = [
    # --- temporal wins over a key it shares the sentence with ------------------
    ("What was the status of KAFKA-15123 on 2024-03-01?", "s6", "iso-date"),
    ("Who was assigned to KIP-848 work over time?", "s6", "over-time"),
    ("What changed in `connect` between 3.6 and 3.7?", "s6", "changed-in"),
    ("Show me the timeline of KAFKA-14565", "s6", "over-time"),
    ("מה השתנה ב-connect בין 3.6 ל-3.7?", "s6", "he-changed"),
    ("מי היה אחראי על KIP-848 לאורך זמן?", "s6", "he-over-time"),
    ("מה היה הסטטוס של KAFKA-15123 ב-2024-03-01?", "s6", "iso-date"),
    # --- aggregation ----------------------------------------------------------
    ("Who owns component `connect` (most assignments/commits last year)?", "s4", "superlative"),
    ("How many open bugs are there in streams?", "s4", "how-many"),
    ("Which components have failing tests tied to open bugs in 3.7?", "s4", "which-have"),
    ("כמה באגים פתוחים יש ברכיב streams?", "s4", "he-count"),
    ("מי הכי הרבה תרם לרכיב connect?", "s4", "he-superlative"),
    # --- thematic -------------------------------------------------------------
    ("What are the main themes of open bugs in streams?", "s5", "themes"),
    ("Give me an overview of the consumer rebalance work", "s5", "overview"),
    ("מהם הנושאים המרכזיים של הבאגים הפתוחים ב-streams?", "s5", "he-themes"),
    # --- keys -----------------------------------------------------------------
    ("Which tests cover KAFKA-15123 and what was their last execution status?", "s3", "keys"),
    ("Why was the incremental rebalance protocol chosen in KIP-848?", "s3", "keys"),
    ("What alternatives were rejected in KIP-932 and why?", "s3", "keys"),
    ("אילו טסטים מכסים את KAFKA-15123 ומה הסטטוס האחרון שלהם?", "s3", "keys"),
    ("למה נבחר פרוטוקול ה-rebalance ב-KIP-848?", "s3", "keys"),
    ("KAFKA-15123", "lookup", "bare-key"),
    # --- default --------------------------------------------------------------
    ("What depends on the consumer rebalance protocol?", "s2", "default"),
    ("אם נשנה את GroupCoordinator — אילו issues פתוחים וטסטים מושפעים?", "s2", "default"),
]


@pytest.mark.parametrize(("question", "strategy", "rule"), ROUTING_CASES)
def test_route_is_deterministic_and_says_why(question: str, strategy: str, rule: str) -> None:
    first = route(question)
    assert first["strategy"] == strategy, f"{question!r} -> {first}"
    assert first["rule"] == rule, f"{question!r} -> {first}"
    assert first["reason"]
    assert 0.0 < first["confidence"] <= 1.0
    assert route(question) == first  # same input, same answer, always


def test_route_covers_at_least_twenty_bilingual_questions() -> None:
    hebrew = [q for q, _, _ in ROUTING_CASES if any("֐" <= ch <= "ת" for ch in q)]
    assert len(ROUTING_CASES) >= 20
    assert len(hebrew) >= 6


def test_last_execution_status_is_not_a_temporal_question() -> None:
    """ "last … status" is about the latest run, not about a date."""
    assert route("Which tests cover KAFKA-1 and their last execution status?")["strategy"] == "s3"


def test_find_keys_splits_by_what_it_can_be_looked_up_as() -> None:
    keys = find_keys(
        "KIP-848 and KAFKA-15123 and 9f8e7d6c5b4a39281706f5e4d3c2b1a098765432 "
        "by jira:mjsax on `connect` in 3.7"
    )
    assert keys.documents == ("KIP-848",)
    assert keys.workitems == ("KAFKA-15123",)
    assert keys.shas == ("9f8e7d6c5b4a39281706f5e4d3c2b1a098765432",)
    assert keys.persons == ("jira:mjsax",)
    assert keys.quoted == ("connect",)
    assert keys.versions == ("3.7",)
    assert keys.any_node_key


def test_find_keys_ignores_the_lookalikes_canon_already_learned_about() -> None:
    keys = find_keys("a UTF-8 SHA-256 COVID-19 problem")
    assert keys.workitems == ()
    assert not keys.any_node_key


def test_find_keys_survives_hebrew_word_characters() -> None:
    """`re` treats Hebrew as `\\w`; a lazy `\\w+-\\d+` would match inside a Hebrew word."""
    keys = find_keys("אילו טסטים מכסים את KAFKA-15123 ומה הסטטוס?")
    assert keys.workitems == ("KAFKA-15123",)
    keys = find_keys("הבעיה נמצאת ב-consumer ואין לה מפתח")
    assert not keys.any_node_key


def test_find_keys_reads_an_iso_date_and_two_versions() -> None:
    keys = find_keys("what changed in connect between 3.6 and 3.7 since 2024-03-01?")
    assert keys.versions == ("3.6", "3.7")
    assert keys.dates == ("2024-03-01",)


@pytest.mark.parametrize(
    ("key", "kind"),
    [
        ("KIP-848", "Document"),
        ("KAFKA-15123", "WorkItem"),
        ("ADO-77", "WorkItem"),
        ("jira:mjsax", "Person"),
        ("Decision|use incremental rebalance", "Entity"),
        ("9f8e7d6c5b4a39281706f5e4d3c2b1a098765432", "Commit"),
        ("UTF-8", None),
        ("", None),
        ("just words", None),
    ],
)
def test_key_kind(key: str, kind: str | None) -> None:
    assert key_kind(key) == kind
