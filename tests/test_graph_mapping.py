"""The closed vocabulary of `brain load`, tested without a database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from brain.canon.models import ChangelogEntry, Link, WorkItem
from brain.graph.mapping import (
    DEDICATED_LINK_RELS,
    LINK_TYPE_MAP,
    assignment_intervals,
    container_label,
    dedupe_link_edges,
    is_known_link_type,
    link_edge,
    pr_number,
    statuschange_id,
    workitem_label,
)


def dt(day: int, hour: int = 0) -> datetime:
    return datetime(2024, 3, day, hour, tzinfo=UTC)


def issue(**kw) -> WorkItem:
    base = {
        "id": "jira:KAFKA-1",
        "key": "KAFKA-1",
        "source": "jira",
        "type": "Bug",
        "title": "t",
        "status": "Open",
        "created": dt(1),
    }
    return WorkItem(**{**base, **kw})


def entry(field: str, **kw) -> ChangelogEntry:
    return ChangelogEntry(field=field, at=kw.pop("at", dt(2)), **kw)


# ------------------------------------------------------------------ labels


@pytest.mark.parametrize(
    ("raw", "source", "expected"),
    [
        ("Bug", "jira", "Bug"),
        ("Sub-task", "jira", "SubTask"),
        ("New Feature", "jira", "NewFeature"),
        ("Wish", "jira", "Wish"),
        ("User Story", "ado", "Story"),
        ("TestExecution", "xray", "TestExecution"),
        ("Test Plan", "xray", "TestPlan"),
        ("", "jira", None),
    ],
)
def test_workitem_label_normalizes_source_spellings(raw, source, expected):
    assert workitem_label(raw, source) == expected


def test_jira_test_and_xray_test_are_different_labels():
    """123 Jira issues have type `Test`; they are not Xray test cases (brief §05.2)."""
    assert workitem_label("Test", "jira") == "JiraTest"
    assert workitem_label("Test", "xray") == "Test"


def test_container_label_is_closed():
    assert container_label("component") == "Component"
    assert container_label("AREA") == "Area"
    assert container_label("testplan") is None


# ------------------------------------------------------------------- links


@pytest.mark.parametrize(
    ("raw", "expected"),
    sorted(LINK_TYPE_MAP.items()),
)
def test_link_type_map_covers_every_real_kafka_type(raw, expected):
    e = link_edge("KAFKA-1", Link(type=raw, target="KAFKA-2", direction="out"))
    assert e is not None
    assert e.rel == "LINKS_TO"
    assert e.type == expected
    assert e.raw_type == raw


def test_unknown_link_type_falls_back_to_relates_and_keeps_the_raw_name():
    e = link_edge("KAFKA-1", Link(type="Completes", target="KAFKA-2", direction="out"))
    assert (e.rel, e.type, e.raw_type) == ("LINKS_TO", "relates", "completes")
    assert is_known_link_type("completes") is False
    assert is_known_link_type("Blocker") is True


def test_blocker_points_from_the_blocker_to_the_blocked_issue():
    """Jira's own outward description for `Blocker` is "blocks"."""
    out = link_edge("KAFKA-100", Link(type="blocker", target="KAFKA-101", direction="out"))
    assert (out.src, out.dst, out.type) == ("KAFKA-100", "KAFKA-101", "blocks")


def test_reciprocal_declarations_collapse_to_one_edge():
    """Both issues carry the same link; canon keeps both, the graph must not."""
    a = link_edge("KAFKA-100", Link(type="blocker", target="KAFKA-101", direction="out"))
    b = link_edge("KAFKA-101", Link(type="blocker", target="KAFKA-100", direction="in"))
    assert a == b
    assert len(dedupe_link_edges([a, b])) == 1


def test_symmetric_links_are_ordered_lexicographically():
    a = link_edge("KAFKA-9", Link(type="reference", target="KAFKA-2", direction="out"))
    b = link_edge("KAFKA-2", Link(type="related", target="KAFKA-9", direction="out"))
    assert (a.src, a.dst) == ("KAFKA-2", "KAFKA-9")
    assert (b.src, b.dst) == ("KAFKA-2", "KAFKA-9")
    merged = dedupe_link_edges([a, b])
    assert len(merged) == 1
    # deterministic pick, not file order
    assert merged[0].raw_type == "reference"
    assert dedupe_link_edges([b, a])[0].raw_type == "reference"


def test_two_link_types_between_the_same_pair_stay_two_edges_when_they_differ():
    blocks = link_edge("KAFKA-1", Link(type="blocker", target="KAFKA-2", direction="out"))
    dupes = link_edge("KAFKA-1", Link(type="duplicate", target="KAFKA-2", direction="out"))
    assert len(dedupe_link_edges([blocks, dupes])) == 2


def test_self_link_is_dropped():
    assert link_edge("KAFKA-1", Link(type="blocker", target="KAFKA-1", direction="out")) is None


def test_dedicated_relationships_are_not_links_to():
    tests = link_edge("XT-1", Link(type="tests", target="KAFKA-100", direction="out"))
    assert (tests.rel, tests.src, tests.dst) == ("TESTS", "XT-1", "KAFKA-100")
    # an execution declares `executes → XT-1`; the graph says XT-1 EXECUTED_IN XE-1
    executes = link_edge("XE-1", Link(type="executes", target="XT-1", direction="out"))
    assert (executes.rel, executes.src, executes.dst) == ("EXECUTED_IN", "XT-1", "XE-1")
    parent = link_edge("KAFKA-2", Link(type="parent", target="KAFKA-1", direction="out"))
    assert (parent.rel, parent.src, parent.dst) == ("PARENT_OF", "KAFKA-1", "KAFKA-2")
    assert set(DEDICATED_LINK_RELS) == {"tests", "executes", "parent"}


# ----------------------------------------------------------- status changes


def test_statuschange_id_is_deterministic_and_content_addressed():
    e = entry("status", **{"from": "Open"}, to="Resolved")
    assert statuschange_id("KAFKA-1", e) == statuschange_id("KAFKA-1", e)
    assert statuschange_id("KAFKA-1", e) != statuschange_id("KAFKA-2", e)
    assert len(statuschange_id("KAFKA-1", e)) == 40


def test_statuschange_id_separates_rows_that_differ_only_in_from():
    """Dropping two fix versions in one edit gives rows with the same key/field/at/to."""
    a = entry("Fix Version", **{"from": "3.6.2"}, to=None)
    b = entry("Fix Version", **{"from": "3.8.0"}, to=None)
    assert statuschange_id("KAFKA-16423", a) != statuschange_id("KAFKA-16423", b)


# --------------------------------------------------------- assignee history


def test_no_changelog_means_one_open_interval_from_created():
    w = issue(assignee="dlee")
    (only,) = assignment_intervals(w)
    assert (only.person_key, only.valid_from, only.valid_to) == ("dlee", w.created, None)


def test_unassigned_item_without_changelog_has_no_interval():
    assert assignment_intervals(issue()) == []


def test_transitions_become_closed_intervals_and_one_open_tail():
    w = issue(
        assignee="mrivera",
        changelog=[
            entry("assignee", to_id="dlee", at=dt(2)),
            entry("assignee", from_id="dlee", to_id="mrivera", at=dt(5)),
        ],
    )
    first, second = assignment_intervals(w)
    assert (first.person_key, first.valid_from, first.valid_to) == ("dlee", dt(2), dt(5))
    assert (second.person_key, second.valid_from, second.valid_to) == ("mrivera", dt(5), None)


def test_a_predecessor_named_by_the_first_transition_starts_at_created():
    w = issue(
        assignee="dlee",
        changelog=[entry("assignee", from_id="jrao", to_id="dlee", at=dt(4))],
    )
    first, second = assignment_intervals(w)
    assert (first.person_key, first.valid_from, first.valid_to) == ("jrao", w.created, dt(4))
    assert (second.person_key, second.valid_to) == ("dlee", None)


def test_identity_keys_are_used_and_display_names_ignored():
    """`to` is "Dana Lee" and `to_id` is "dlee"; only the id can identify anyone."""
    w = issue(assignee="dlee", changelog=[entry("assignee", to="Dana Lee", to_id="dlee")])
    (only,) = assignment_intervals(w)
    assert only.person_key == "dlee"


def test_current_assignee_always_holds_the_open_interval():
    """Jira spells the same person `kirktrue` in the issue and `JIRAUSER1` in the log."""
    w = issue(
        assignee="kirktrue",
        changelog=[entry("assignee", to="Kirk True", to_id="JIRAUSER1", at=dt(4))],
    )
    intervals = assignment_intervals(w)
    open_ones = [a for a in intervals if a.valid_to is None]
    assert [a.person_key for a in open_ones] == ["kirktrue"]
    assert open_ones[0].valid_from == dt(4)
    assert open_ones[0].from_field is True
    # the changelog interval is closed at the moment the field-derived one opens
    stale = next(a for a in intervals if a.person_key == "JIRAUSER1")
    assert stale.valid_to == dt(4)


def test_unassignment_leaves_no_open_interval():
    w = issue(
        changelog=[
            entry("assignee", to_id="dlee", at=dt(2)),
            entry("assignee", from_id="dlee", to_id=None, at=dt(6)),
        ]
    )
    (only,) = assignment_intervals(w)
    assert (only.person_key, only.valid_to) == ("dlee", dt(6))


def test_intervals_are_deduplicated_by_person_and_start():
    w = issue(
        assignee="dlee",
        changelog=[
            entry("assignee", to_id="dlee", at=dt(2)),
            entry("assignee", to_id="dlee", at=dt(2)),
        ],
    )
    assert len(assignment_intervals(w)) == 1


# ------------------------------------------------------------- pr numbers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("pr:14001", 14001), ("14001", 14001), ("000000", 0), ("abc", None), (None, None)],
)
def test_pr_number(raw, expected):
    assert pr_number(raw) == expected
