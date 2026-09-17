"""Version arithmetic, the question-set builder's pure halves, and S6 on a Document anchor."""

from __future__ import annotations

import pytest

from brain.retrieve.competency import (
    SELECTORS,
    TEMPLATES,
    _midpoint_date,
    _render,
    _version_pair,
    anchor_report,
    anchors,
)
from brain.retrieve.temporal import (
    _bound,
    _by_time,
    _merge_intervals,
    _pad,
    _status_on,
    assignees_over_time,
    changes_between,
    status_at,
    timeline,
    version_key,
)
from brain.retrieve.types import RetrieveError


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


# ------------------------------------------------------------------ the anchor selectors


class _SelectorCtx:
    """A context that answers every selector with one canned row."""

    prefix = ""

    def __init__(self, row: dict) -> None:
        self.row = row
        self.cyphers: list[str] = []

    def label(self, label: str) -> str:
        return label

    def read(self, cypher: str, **_kw) -> list[dict]:
        self.cyphers.append(cypher)
        return [self.row]


def test_the_most_tested_issue_is_one_whose_tests_have_actually_run() -> None:
    """cq01/cq16 asked for "the last execution status" of three tests that never ran."""
    cypher, rule = SELECTORS["issue_most_tests"]
    assert "HAS_RUN" in cypher
    assert "runs > 0" in cypher, "a covered-but-never-run issue must lose to a run one"
    assert cypher.index("runs > 0") < cypher.index("count(DISTINCT t) AS n") or (
        cypher.index("ORDER BY") < cypher.index("LIMIT 1")
    )
    assert "run" in rule


def test_every_selector_orders_before_it_limits_and_returns_a_key() -> None:
    for name, (cypher, rule) in SELECTORS.items():
        assert "ORDER BY" in cypher and cypher.index("ORDER BY") < cypher.index("LIMIT 1"), name
        assert " AS key" in cypher, name
        assert rule, name


def test_an_anchor_carries_the_note_its_selector_returned() -> None:
    """The report says *why* this key was chosen, not only that it was."""
    ctx = _SelectorCtx({"key": "KAFKA-1", "title": "t", "n": 3, "note": "3 tests, 5 runs"})
    found = anchors(ctx)
    assert found["issue_most_tests"].note == "3 tests, 5 runs"
    assert anchor_report(found)[0]["note"] == "3 tests, 5 runs"


# ------------------------------------------------------- S6 with a Document as the anchor
#
# Ten temporal questions in the Plan 3 sweep anchor on a KIP (`KIP-848`), and a KIP is a
# `Document`: every S6 tool used to raise "no WorkItem with key 'KIP-848'". A Document has
# no changelog of its own, so its history is derived — from the commits that implement it
# and from the work items that reference it — and every derived row has to say which edge
# it came from, because "derived" is only honest when the derivation is on the record.


class _FakeCtx:
    """A context that answers each query with the rows its marker selects. No database."""

    prefix = ""

    def __init__(self, answers: list[tuple[str, list[dict]]]) -> None:
        self.answers = answers
        self.cyphers: list[str] = []

    def label(self, name: str) -> str:
        return f"`{name}`"

    def read(self, cypher: str, **_params) -> list[dict]:
        self.cyphers.append(cypher)
        for marker, rows in self.answers:
            if marker in cypher:
                return rows
        return []


def _epoch(stamp: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(stamp).timestamp())


DOC_ROW = {
    "key": "KIP-9",
    "title": "KIP-9: Next generation protocol",
    "kind": "KIP",
    "space": "KAFKA",
    "source": "confluence",
    "status": None,
    "updated": "2024-01-05T09:00:00+00:00",
    "synthetic": False,
}
COMMIT_ROWS = [
    {
        "sha": "a1b2c3d4",
        "at": "2024-03-01T10:00:00+00:00",
        "epoch": _epoch("2024-03-01T10:00:00+00:00"),
        "author": "Dana Lee",
        "message": "KAFKA-100: client side of KIP-9 (#14001)",
        "synthetic": False,
    }
]
REF_ROWS = [
    {
        "key": "KAFKA-100",
        "title": "Implement the new protocol in clients",
        "type": "Improvement",
        "status": "Resolved",
        "created": "2024-01-10T09:00:00+00:00",
        "created_epoch": _epoch("2024-01-10T09:00:00+00:00"),
        "resolved": "2024-03-02T10:00:00+00:00",
        "resolved_epoch": _epoch("2024-03-02T10:00:00+00:00"),
        "synthetic": False,
        "changes": [
            {
                "id": "sc1",
                "at": "2024-01-15T08:00:00+00:00",
                "epoch": _epoch("2024-01-15T08:00:00+00:00"),
                "from": "Open",
                "to": "In Progress",
                "by": "dlee",
            },
            {
                "id": "sc2",
                "at": "2024-03-02T10:00:00+00:00",
                "epoch": _epoch("2024-03-02T10:00:00+00:00"),
                "from": "In Progress",
                "to": "Resolved",
                "by": "dlee",
            },
        ],
        "versions": [
            {
                "version": "3.7.0",
                "at": "2024-02-20T10:00:00+00:00",
                "epoch": _epoch("2024-02-20T10:00:00+00:00"),
            }
        ],
    }
]
CHUNK_ROWS = [
    {"key": "KIP-9", "chunks": [{"id": "chunk-1", "text": "Motivation", "kind": "section"}]}
]

#: Marker order matters: the referencing-items query also mentions `HAS_CHANGE`, and the
#: commit query also mentions `Document`.
DOC_ANSWERS = [
    ("IMPLEMENTS_KIP", COMMIT_ROWS),
    ("REFERENCES", REF_ROWS),
    ("parent_key", CHUNK_ROWS),
    ("d.key AS key", [DOC_ROW]),
]


def test_status_on_a_date_is_the_last_change_before_it() -> None:
    changes = REF_ROWS[0]["changes"]
    on = _status_on(
        changes, REF_ROWS[0]["created_epoch"], "Resolved", _epoch("2024-02-01T23:59:59+00:00")
    )
    assert on["status"] == "In Progress"
    assert on["basis"].startswith("status change at")
    assert (on["before"], on["after"]) == (1, 1)


def test_status_on_a_date_before_the_item_existed_says_so() -> None:
    changes = REF_ROWS[0]["changes"]
    on = _status_on(
        changes, REF_ROWS[0]["created_epoch"], "Resolved", _epoch("2023-01-01T23:59:59+00:00")
    )
    assert on["status"] is None and "did not exist" in on["basis"]


def test_status_on_a_date_before_the_first_transition_is_that_transitions_from() -> None:
    changes = REF_ROWS[0]["changes"]
    on = _status_on(
        changes, REF_ROWS[0]["created_epoch"], "Resolved", _epoch("2024-01-11T23:59:59+00:00")
    )
    assert on["status"] == "Open" and "initial status" in on["basis"]


def test_events_are_oldest_first_and_an_undated_one_sorts_last() -> None:
    events = [
        {"epoch": None, "key": "z"},
        {"epoch": 300, "key": "c"},
        {"epoch": 100, "key": "b"},
        {"epoch": 100, "key": "a"},
    ]
    ordered, cut = _by_time(events, 10)
    assert [e["key"] for e in ordered] == ["a", "b", "c", "z"]
    assert cut is False


def test_a_cut_timeline_says_it_was_cut() -> None:
    events = [{"epoch": i, "key": str(i)} for i in range(5)]
    ordered, cut = _by_time(events, 3)
    assert [e["key"] for e in ordered] == ["0", "1", "2"] and cut is True


def test_intervals_merge_per_person_and_an_open_one_makes_them_current() -> None:
    rows = [
        {
            "person": "jira:dlee",
            "display": "Dana Lee",
            "work_item": "KAFKA-100",
            "valid_from": "2024-01-15T08:00:00+00:00",
            "from_epoch": 100,
            "valid_to": "2024-02-01T08:00:00+00:00",
            "source": "changelog",
        },
        {
            "person": "jira:dlee",
            "display": "Dana Lee",
            "work_item": "KAFKA-101",
            "valid_from": "2024-03-01T08:00:00+00:00",
            "from_epoch": 300,
            "valid_to": None,
            "source": "field",
        },
        {
            "person": "jira:mrivera",
            "display": "M. Rivera",
            "work_item": "KAFKA-102",
            "valid_from": "2024-02-01T08:00:00+00:00",
            "from_epoch": 200,
            "valid_to": "2024-02-20T08:00:00+00:00",
            "source": "field",
        },
    ]
    merged = _merge_intervals(rows)
    assert [m["person"] for m in merged] == ["jira:dlee", "jira:mrivera"]
    dana = merged[0]
    assert dana["current"] is True and dana["valid_to"] is None
    assert dana["valid_from"] == "2024-01-15T08:00:00+00:00"
    assert dana["intervals"] == 2 and dana["work_items"] == ["KAFKA-100", "KAFKA-101"]
    assert merged[1]["current"] is False and merged[1]["valid_to"] == "2024-02-20T08:00:00+00:00"


def test_a_documents_timeline_unions_its_commits_status_changes_and_fix_versions() -> None:
    ctx = _FakeCtx(DOC_ANSWERS)
    result = timeline(ctx, "KIP-9", log=False)
    assert result.strategy == "s6"
    head = result.items[0]
    assert head.kind == "Document" and head.key == "KIP-9"
    rows = [i for i in result.items if i.kind == "Row"]
    assert [r.props["at"] for r in rows] == sorted(r.props["at"] for r in rows)
    assert [r.props["event"] for r in rows] == ["status", "fix_version", "commit", "status"]
    assert [r.key for r in rows] == ["sc1", "KAFKA-100@3.7.0", "a1b2c3d4", "sc2"]


def test_every_derived_row_names_the_edge_it_came_from() -> None:
    ctx = _FakeCtx(DOC_ANSWERS)
    rows = [i for i in timeline(ctx, "KIP-9", log=False).items if i.kind == "Row"]
    assert {r.props["via"] for r in rows} == {"IMPLEMENTS_KIP", "REFERENCES"}
    assert all(r.props["document"] == "KIP-9" for r in rows)
    assert all(p.source_kind == "row" for r in rows for p in r.provenance)
    # the row cites the node it was derived from, by key, so a reader can go and look
    assert {p.source for r in rows for p in r.provenance} == {"KAFKA-100", "a1b2c3d4"}


def test_a_key_that_is_neither_a_work_item_nor_a_document_still_raises() -> None:
    ctx = _FakeCtx([("parent_key", [])])
    with pytest.raises(RetrieveError):
        timeline(ctx, "KAFKA-999999", log=False)
    with pytest.raises(RetrieveError):
        status_at(ctx, "KAFKA-999999", "2024-01-01", log=False)


def test_status_at_of_a_document_answers_for_each_referencing_work_item() -> None:
    ctx = _FakeCtx(DOC_ANSWERS)
    result = status_at(ctx, "KIP-9", "2024-02-01", log=False)
    rows = [i for i in result.items if i.kind == "Row"]
    head = rows[0]
    assert head.key == "KIP-9@2024-02-01" and head.props["referencing"] == 1
    per_item = rows[1]
    assert per_item.key == "KAFKA-100@2024-02-01"
    assert per_item.props["status"] == "In Progress"
    assert per_item.props["via"] == "REFERENCES" and per_item.props["work_item"] == "KAFKA-100"
    assert [p.source_kind for p in per_item.provenance] == ["row"]


def test_assignees_of_a_document_are_the_referencing_items_people() -> None:
    assignments = [
        {
            "person": "jira:dlee",
            "display": "Dana Lee",
            "work_item": "KAFKA-100",
            "valid_from": "2024-01-15T08:00:00+00:00",
            "from_epoch": 100,
            "valid_to": None,
            "source": "changelog",
        }
    ]
    ctx = _FakeCtx(
        [("REFERENCES", assignments), ("parent_key", CHUNK_ROWS), ("d.key AS key", [DOC_ROW])]
    )
    result = assignees_over_time(ctx, "KIP-9", log=False)
    people = [i for i in result.items if i.kind == "Person"]
    assert [p.key for p in people] == ["jira:dlee"]
    assert people[0].props["current"] is True
    assert people[0].props["via"] == "REFERENCES"
    assert people[0].props["work_items"] == ["KAFKA-100"]
    assert any(p.source == "KAFKA-100" and p.source_kind == "row" for p in people[0].provenance)


def test_changes_between_orders_before_it_collects_and_before_python_cuts() -> None:
    """The one S6 query with no timestamp in it, and the same determinism hole.

    Its rows were unordered while Python kept the first one per work item and scored it by
    position, and its five commits were a slice of an unordered `collect` — two ways for
    one question to have two answers.
    """
    ctx = _FakeCtx([])
    changes_between(ctx, "clients", "3.6", "3.7", log=False)
    cypher = ctx.cyphers[0]
    assert cypher.index("ORDER BY commit.at, commit.sha") < cypher.index(
        "collect(DISTINCT commit.sha)"
    )
    assert cypher.rstrip().endswith("ORDER BY w.key, v.name")
