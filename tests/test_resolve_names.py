from __future__ import annotations

import pytest

from brain.resolve.names import (
    display_tokens,
    first_line,
    is_opaque,
    norm_display,
    survivor,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Rao, Jun", "rao jun"),
        ("danica.fine", "danica fine"),
        ("  Jun   Rao ", "jun rao"),
        ("S. An", "s an"),
        ("O'Brien", "o brien"),
        ("rao_jun", "rao jun"),
        (None, ""),
    ],
)
def test_norm_display_splits_joiners_and_folds_case(raw, expected):
    assert norm_display(raw) == expected


def test_display_tokens_drop_initials_because_an_initial_matches_too_many_people():
    assert display_tokens("S. An") == {"an"}
    assert display_tokens("Sanghyeok An") == {"sanghyeok", "an"}


def test_opaque_keys_are_the_machine_spellings_only():
    assert is_opaque("jira", "JIRAUSER302322")
    assert not is_opaque("jira", "lucasbru")
    assert is_opaque("confluence", "8aa980816ee92258016f0f72a35d00ec")
    assert not is_opaque("confluence", "rao.jun")
    assert not is_opaque("git", "jun@example.org")


def test_survivor_prefers_jira_then_git_then_confluence_then_synthetic():
    ids = ["xray:rao.10001", "confluence:rao.jun", "git:jun@example.org", "jira:jrao"]
    assert survivor("person", ids) == "jira:jrao"
    assert survivor("person", ids[:3]) == "git:jun@example.org"
    assert survivor("person", ids[:2]) == "confluence:rao.jun"


def test_survivor_prefers_a_readable_jira_key_over_the_changelog_number():
    assert survivor("person", ["jira:JIRAUSER302322", "jira:lucasbru"]) == "jira:lucasbru"


def test_entity_survivor_is_the_least_qualified_name():
    ids = ["Feature|the new consumer rebalance protocol", "Feature|consumer rebalance protocol"]
    assert survivor("entity", ids) == "Feature|consumer rebalance protocol"


def test_survivor_of_nothing_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        survivor("person", [])


def test_first_line_keeps_the_commit_subject_only():
    assert first_line("KAFKA-1: fix it\n\nlong body\nmore") == "KAFKA-1: fix it"
    assert first_line("x" * 200).endswith("…")
    assert len(first_line("x" * 200)) == 120
    assert first_line(None) == ""
