"""Deriving the competency gold from the graph, on synthetic rows.

Every recipe is a function from Cypher rows to a bilingual fact list, so a fake `read` that
returns the rows a real query would is enough to test all of it — and it is the only way to
test the shapes the live corpus happens not to have today (a test that never ran, a version
with no failing test, an item nobody was ever assigned).

The two rules worth pinning here are the ones that protect the *measurement*: a Hebrew twin
is graded on the same facts as its English pair, and a derivation that finds nothing leaves
the row pending with a reason instead of inventing a sentence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.eval import gold_competency as G
from brain.eval.questions_report import competency_row

SCHEMA = json.loads(Path("brain/eval/question_schema.json").read_text(encoding="utf-8"))

ANCHORS = {
    "issue_most_tests": "KAFKA-1",
    "kip_most_commits": "KIP-1",
    "component_most_resolves": "streams",
    "ado_story_kip": "KIP-1",
    "component_most_open_bugs": "clients",
    "entity_most_depends": "Technology|versioned state store",
    "kip_most_decides": "KIP-2",
    "kip_most_rejects": "KIP-3",
    "kip_most_motivated": "KIP-4",
    "issue_most_status_changes": "KAFKA-2",
    "issue_most_assignees": "KAFKA-3",
    "version_window": "clients",
    "version_low": "3.7",
    "version_high": "3.8",
    "status_date": "2024-01-16",
}


class FakeCtx:
    """Answers `read` from a queue of row lists, and records what it was asked."""

    prefix = ""

    def __init__(self, *responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def label(self, name: str) -> str:
        return f"`{name}`"

    def read(self, cypher: str, **params):
        self.calls.append((cypher, params))
        return self.responses.pop(0) if self.responses else []


# ---------------------------------------------------------------------------- the table


def test_every_competency_question_has_a_derivation_registered():
    """If `brain competency` grows a question, this fails rather than skipping its gold."""
    from brain.retrieve.competency import TEMPLATES

    assert set(G.PLANS) == {row[0] for row in TEMPLATES}


def test_every_plan_names_anchors_the_selectors_actually_produce():
    from brain.retrieve.competency import SELECTORS

    derived = {"status_date", "version_low", "version_high"}
    for qid, plan in G.PLANS.items():
        for name in plan.anchors:
            assert name in SELECTORS or name in derived, f"{qid} wants unknown anchor {name}"


def test_a_hebrew_twin_is_derived_by_the_same_recipe_as_its_english_pair():
    for he, en in (("cq16", "cq01"), ("cq17", "cq05"), ("cq18", "cq09"), ("cq19", "cq13")):
        assert G.PLANS[he].recipe is G.PLANS[en].recipe
        assert G.PLANS[he].anchors == G.PLANS[en].anchors


# ---------------------------------------------------------------------------- recipes


def test_tests_and_their_last_run_are_reported_with_the_execution_that_ran_them():
    ctx = FakeCtx(
        [
            {
                "test": "XT-1",
                "title": "t",
                "last": {"status": "FAIL", "at": "2024-05-02T00:00:00Z", "execution": "XE-1"},
            },
            {"test": "XT-2", "title": "t", "last": {"status": None, "at": None, "execution": None}},
        ]
    )
    d = G._tests_last_run(ctx, ANCHORS)
    assert "XT-1 last ran FAIL on 2024-05-02 in XE-1." in d.render("en")
    assert "XT-2 has no recorded execution." in d.render("en")
    assert d.evidence == ["KAFKA-1", "XT-1", "XE-1", "XT-2"]
    assert "XE-1" in d.render("he")  # identifiers stay untranslated


def test_a_test_that_never_ran_says_so_rather_than_implying_it_passed():
    ctx = FakeCtx([{"test": "XT-1", "title": "t", "last": {"status": None}}])
    d = G._tests_last_run(ctx, ANCHORS)
    assert "No execution status is recorded" in d.render("en")
    assert "לא מתועד סטטוס הרצה" in d.render("he")


def test_an_issue_no_test_covers_leaves_the_row_pending_with_the_reason():
    d = G._tests_last_run(FakeCtx([]), ANCHORS)
    assert d.empty
    assert "no Test is linked by TESTS to KAFKA-1" in d.note


def test_the_owner_of_a_component_is_ranked_on_assignments_plus_commits():
    ctx = FakeCtx(
        [
            {"person": "jira:a", "display": "A", "n": 5},
            {"person": "jira:b", "display": "B", "n": 9},
        ],
        [
            {"person": "jira:a", "display": "A", "n": 20},
            {"person": "jira:b", "display": "B", "n": 1},
        ],
    )
    d = G._component_owner(ctx, ANCHORS)
    assert d.render("en").startswith("A (jira:a) owns `streams`")
    assert "5 assignments and 20 resolving commits" in d.render("en")
    assert d.evidence[:2] == ["streams", "jira:a"]


def test_the_owner_query_asks_for_assignments_and_commits_separately():
    ctx = FakeCtx([], [])
    d = G._component_owner(ctx, ANCHORS)
    assert d.empty
    assert "ASSIGNED_TO" in ctx.calls[0][0]
    assert "AUTHORED" in ctx.calls[1][0] and "RESOLVES" in ctx.calls[1][0]


def test_an_ado_item_says_whether_it_reaches_the_kip_directly_or_through_a_jira_issue():
    ctx = FakeCtx(
        [
            {
                "key": "ADO-1",
                "title": "t",
                "type": "User Story",
                "status": "Active",
                "direct": True,
                "through": [],
            },
            {
                "key": "ADO-2",
                "title": "t",
                "type": "Feature",
                "status": "New",
                "direct": False,
                "through": ["KAFKA-9"],
            },
        ]
    )
    d = G._ado_items_for_kip(ctx, ANCHORS)
    assert "references it directly" in d.render("en")
    assert "via KAFKA-9" in d.render("en")
    assert "KAFKA-9" in d.evidence


def test_the_blast_radius_is_capped_and_says_the_honest_total():
    rows = [
        {
            "total": 716,
            "key": f"ADO-{i}",
            "title": "t",
            "type": "Bug",
            "status": "New",
            "tests": ["XT-1"] if i == 0 else [],
            "kips": ["KIP-848"] if i == 1 else [],
        }
        for i in range(G.IMPACT_CAP)
    ]
    d = G._component_blast_radius(FakeCtx(rows), ANCHORS)
    text = d.render("en")
    assert "has 716 open work items; the first 10 by key" in text
    assert "Tests covering them: XT-1." in text
    assert "KIPs they reference: KIP-848." in text
    assert len(d.evidence) <= 20


def test_a_component_with_no_covering_test_says_none_rather_than_omitting_the_line():
    rows = [
        {
            "total": 1,
            "key": "K-1",
            "title": "t",
            "type": "Bug",
            "status": "Open",
            "tests": [],
            "kips": [],
        }
    ]
    text = G._component_blast_radius(FakeCtx(rows), ANCHORS).render("en")
    assert "Tests covering them: none." in text
    assert "KIPs they reference: none." in text


def test_no_failing_test_is_an_answer_not_an_empty_derivation():
    """ "Nothing is failing" is a fact about the corpus; a blank gold would hide it."""
    rows = [
        {"component": "clients", "open_bugs": 4, "tests": [], "statuses": [], "bugs": ["K-1"]},
        {
            "component": "streams",
            "open_bugs": 2,
            "tests": ["XT-9"],
            "statuses": ["PASS"],
            "bugs": ["K-2"],
        },
    ]
    d = G._components_failing_tests_in_version(FakeCtx(rows), ANCHORS)
    text = d.render("en")
    assert text.startswith("No component has a failing test tied to an open 3.8 bug.")
    assert "1 of them have any covering test at all" in text
    assert "3.8" not in d.evidence, "the version family is not a Version.name and is not citable"
    assert d.evidence == ["clients", "streams"]


def test_a_failing_test_is_named_with_the_bug_it_is_tied_to():
    rows = [
        {
            "component": "clients",
            "open_bugs": 3,
            "tests": ["XT-1"],
            "statuses": ["PASS", "FAIL"],
            "bugs": ["K-1"],
        }
    ]
    d = G._components_failing_tests_in_version(FakeCtx(rows), ANCHORS)
    assert "1 component(s) have a failing test" in d.render("en")
    assert "`clients`: 3 open bugs (e.g. K-1), tests XT-1." in d.render("en")
    assert set(d.evidence) == {"clients", "K-1"}


def test_a_weak_decision_is_backed_by_its_mentions_quote_not_by_its_description():
    """Plan decision 8: a weak extraction's own words are not gradeable; the source's are."""
    rows = [
        {
            "id": "Decision|strong",
            "name": "strong one",
            "description": "the extraction's description",
            "weak": False,
            "evidence": ["c1"],
            "mentions": [{"chunk": "c9", "quote": "quoted text"}],
        },
        {
            "id": "Decision|weak",
            "name": "weak one",
            "description": "a description nobody trusts",
            "weak": True,
            "evidence": ["c2"],
            "mentions": [{"chunk": "c3", "quote": "the sentence the KIP actually wrote"}],
        },
    ]
    d = G._kip_decisions(FakeCtx(rows), ANCHORS)
    text = d.render("en")
    assert "strong one — the extraction's description" in text
    assert "weak one — “the sentence the KIP actually wrote”" in text
    assert "a description nobody trusts" not in text
    assert "c3" in d.evidence, "the quoted chunk has to be citable"
    assert "c9" not in d.evidence, "a strong entity cites its own evidence, not a mention"


def test_the_rationale_heading_attaches_the_hebrew_prefix_without_a_space():
    rows = [
        {
            "id": "Decision|x",
            "name": "x",
            "description": "",
            "weak": False,
            "evidence": [],
            "mentions": [],
        }
    ]
    assert G._kip_decisions(FakeCtx(rows), ANCHORS).render("he").startswith("ב-KIP-2 מתועדות 1")


def test_the_top_communities_are_taken_by_rank_and_capped_at_three():
    rows = [
        {
            "id": f"L0-{i}",
            "title": f"theme {i}",
            "rank": 9 - i,
            "level": 0,
            "bugs": 3,
            "examples": ["K-1"],
        }
        for i in range(5)
    ]
    d = G._community_themes_for_component(FakeCtx(rows), ANCHORS)
    text = d.render("en")
    assert "fall into 5 summarised communities; the top 3 by rank" in text
    assert "L0-3" not in text
    assert d.evidence[0] == "clients"


# --------------------------------------------------------------- temporal, reused as-is


def _result(items):
    from brain.retrieve.types import Item, Result

    return Result(strategy="s6", items=[Item(**i) for i in items])


def test_the_version_window_drops_the_summary_row_and_cites_the_real_version_names(monkeypatch):
    monkeypatch.setattr(
        G.temporal_mod,
        "changes_between",
        lambda *a, **k: _result(
            [
                {"kind": "Row", "key": "clients:3.7..3.8", "title": "window", "props": {}},
                {"kind": "WorkItem", "key": "K-1", "title": "a", "props": {"fix_version": "3.8.0"}},
            ]
        ),
    )
    d = G._changes_between_versions(FakeCtx(), ANCHORS)
    assert "1 work item(s) in `clients` shipped after 3.7 and up to 3.8." in d.render("en")
    assert "clients:3.7..3.8" not in d.render("en")
    assert d.evidence == ["clients", "3.8.0", "K-1"]
    assert "3.7" not in d.evidence


def test_an_empty_version_window_is_pending_with_the_window_in_the_reason(monkeypatch):
    monkeypatch.setattr(G.temporal_mod, "changes_between", lambda *a, **k: _result([]))
    d = G._changes_between_versions(FakeCtx(), ANCHORS)
    assert d.empty
    assert "(3.7, 3.8]" in d.note
    assert d.query.startswith("brain.retrieve.temporal.changes_between(")


def test_a_version_window_the_temporal_tool_refuses_is_pending_not_a_crash(monkeypatch):
    from brain.retrieve.types import RetrieveError

    def boom(*a, **k):
        raise RetrieveError("version window is empty")

    monkeypatch.setattr(G.temporal_mod, "changes_between", boom)
    d = G._changes_between_versions(FakeCtx(), ANCHORS)
    assert d.empty
    assert "version window is empty" in d.note


def test_the_status_on_a_date_cites_the_work_item_and_its_chunk_not_the_synthetic_item_key(
    monkeypatch,
):
    monkeypatch.setattr(
        G.temporal_mod,
        "status_at",
        lambda *a, **k: _result(
            [
                {
                    "kind": "Row",
                    "key": "KAFKA-2@2024-01-16",
                    "title": "t",
                    "props": {"status": "Reopened", "basis": "status change at 2024-01-16"},
                    "provenance": [{"chunk_id": "c1"}],
                }
            ]
        ),
    )
    d = G._status_on_date(FakeCtx(), ANCHORS)
    assert d.render("en") == "On 2024-01-16, KAFKA-2 was Reopened (status change at 2024-01-16)."
    assert d.evidence == ["KAFKA-2", "c1"]


def test_a_date_before_the_item_existed_is_pending_with_what_the_changelog_said(monkeypatch):
    monkeypatch.setattr(
        G.temporal_mod,
        "status_at",
        lambda *a, **k: _result(
            [{"kind": "Row", "key": "x", "props": {"status": None, "basis": "did not exist yet"}}]
        ),
    )
    d = G._status_on_date(FakeCtx(), ANCHORS)
    assert d.empty
    assert "did not exist yet" in d.note


def test_assignees_are_reported_oldest_first_whatever_order_the_tool_ranked_them(monkeypatch):
    monkeypatch.setattr(
        G.temporal_mod,
        "assignees_over_time",
        lambda *a, **k: _result(
            [
                {
                    "kind": "Person",
                    "key": "jira:new",
                    "title": "New",
                    "snippet": "2025-01-01 → now",
                    "props": {"valid_from": "2025-01-01"},
                },
                {
                    "kind": "Person",
                    "key": "jira:old",
                    "title": "Old",
                    "snippet": "2023-01-01 → 2025-01-01",
                    "props": {"valid_from": "2023-01-01"},
                },
            ]
        ),
    )
    d = G._assignees_over_time(FakeCtx(), ANCHORS)
    assert d.evidence == ["KAFKA-3", "jira:old", "jira:new"]
    assert d.render("en").index("Old") < d.render("en").index("New")


def test_an_item_nobody_was_assigned_is_pending(monkeypatch):
    monkeypatch.setattr(G.temporal_mod, "assignees_over_time", lambda *a, **k: _result([]))
    d = G._assignees_over_time(FakeCtx(), ANCHORS)
    assert d.empty
    assert "no assignee" in d.note


# ----------------------------------------------------------------------------- derive


def _rows(*ids):
    return [
        {"id": i, "lang": "he" if i in ("cq16", "cq17", "cq18", "cq19") else "en", "anchors": []}
        for i in ids
    ]


def test_derive_refuses_when_the_graph_now_nominates_a_different_anchor():
    row = {"id": "cq01", "lang": "en", "anchors": ["KAFKA-OLD"]}
    gold = G.derive(FakeCtx(), [row], values=ANCHORS)[0]
    assert gold.pending
    assert "anchor drift" in gold.gold_note
    assert "KAFKA-1" in gold.gold_note and "KAFKA-OLD" in gold.gold_note


def test_derive_runs_when_the_question_names_the_anchor_the_graph_still_selects():
    row = {"id": "cq01", "lang": "en", "anchors": ["KAFKA-1"]}
    ctx = FakeCtx(
        [
            {
                "test": "XT-1",
                "title": "t",
                "last": {"status": "PASS", "at": "2024-01-01", "execution": "XE-1"},
            }
        ]
    )
    gold = G.derive(ctx, [row], values=ANCHORS)[0]
    assert not gold.pending
    assert gold.gold_derived_by == "code"
    assert gold.gold_source == "graph"
    assert gold.gold_query


def test_a_question_with_no_registered_derivation_is_pending_not_skipped():
    gold = G.derive(FakeCtx(), [{"id": "cq99", "lang": "en"}], values=ANCHORS)[0]
    assert gold.pending
    assert "no derivation is registered for cq99" in gold.gold_note


def test_a_missing_anchor_is_named_in_the_reason():
    gold = G.derive(FakeCtx(), [{"id": "cq01", "lang": "en"}], values={})[0]
    assert gold.pending
    assert "issue_most_tests" in gold.gold_note


def test_verify_takes_a_row_back_to_pending_when_it_cites_a_key_the_graph_lacks(monkeypatch):
    import brain.eval.questions_report as qr

    monkeypatch.setattr(qr, "existing_keys", lambda ctx, keys: {"KAFKA-1"})
    good = G.Gold("cq01", "an answer", ["KAFKA-1"], "graph")
    bad = G.Gold("cq02", "an answer", ["KAFKA-1", "GHOST-9"], "graph")
    out = G.verify(FakeCtx(), [good, bad])
    assert not out[0].pending
    assert out[1].pending
    assert "GHOST-9" in out[1].gold_note
    assert "the recipe is wrong, not the question" in out[1].gold_note


# ------------------------------------------------------------------ into questions.jsonl


COMPETENCY = {
    "id": "cq01",
    "type": "traceability",
    "lang": "en",
    "question": "Which tests cover KAFKA-1?",
    "expected_strategy": "s3",
    "anchors": ["KAFKA-1"],
    "pair": "tests",
}


def test_a_derived_gold_lands_on_the_row_with_its_query_for_audit():
    row = competency_row(
        COMPETENCY,
        {
            "id": "cq01",
            "gold_answer": "XT-1 covers it.",
            "gold_evidence": ["KAFKA-1", "XT-1"],
            "gold_source": "graph",
            "gold_derived_by": "code",
            "gold_query": "MATCH (t:Test)-[:TESTS]->(w) RETURN t",
        },
    )
    assert row["gold_source"] == "graph"
    assert row["gold_answer"] == "XT-1 covers it."
    assert row["gold_evidence"] == ["KAFKA-1", "XT-1"]
    assert row["gold_derived_by"] == "code"
    assert row["gold_query"].startswith("MATCH")


def test_a_pending_gold_leaves_the_row_pending_and_keeps_the_reason():
    row = competency_row(
        COMPETENCY,
        {"id": "cq01", "gold_source": "pending", "gold_answer": None, "gold_note": "nothing ran"},
    )
    assert row["gold_source"] == "pending"
    assert row["gold_answer"] is None
    assert row["gold_evidence"] == []
    assert row["gold_note"] == "nothing ran"


def test_a_row_with_no_gold_at_all_is_exactly_what_it_was_before():
    assert competency_row(COMPETENCY)["gold_source"] == "pending"
    assert "gold_derived_by" not in competency_row(COMPETENCY)


@pytest.mark.parametrize("gold_source", ["graph", "pending"])
def test_the_row_validates_against_the_schema_either_way(gold_source: str):
    from brain.common.jsonschema_mini import validate

    gold = {
        "id": "cq01",
        "gold_answer": "XT-1 covers it." if gold_source == "graph" else None,
        "gold_evidence": ["KAFKA-1"] if gold_source == "graph" else [],
        "gold_source": gold_source,
        "gold_derived_by": "code",
        "gold_query": "MATCH (n) RETURN n",
        "gold_note": "" if gold_source == "graph" else "nothing ran",
    }
    row = competency_row(COMPETENCY, gold)
    assert validate(row, {**SCHEMA["$defs"]["row"], "$defs": SCHEMA["$defs"]}) == []


def test_reading_a_missing_sidecar_is_empty_not_an_error(tmp_path: Path):
    assert G.read_gold(tmp_path / "nope.jsonl") == {}


def test_the_sidecar_round_trips_by_id(tmp_path: Path):
    path = tmp_path / "competency_gold.jsonl"
    path.write_text(
        json.dumps(G.Gold("cq01", "a", ["K-1"], "graph", gold_query="MATCH").as_dict()) + "\n",
        encoding="utf-8",
    )
    loaded = G.read_gold(path)
    assert loaded["cq01"]["gold_evidence"] == ["K-1"]
    assert loaded["cq01"]["gold_query"] == "MATCH"


def test_a_pending_gold_never_carries_a_query_it_did_not_run():
    assert "gold_query" not in G.Gold("cq01", None, [], "pending", gold_note="why").as_dict()
