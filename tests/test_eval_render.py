"""`brain eval gate-report` — the Hebrew page, generated from the JSON and nothing else.

Same rule as the Plan 1 census (`brain/index/render.py`): no number in the document is
typed by hand, so a rerun updates it instead of leaving it quietly wrong. The one thing a
person does write is the verdict paragraph, and the test that matters most here is that
regenerating the page does not delete it.
"""

from __future__ import annotations

from typing import Any

from brain.eval.render import PLANNER_END, PLANNER_HEADING, PLANNER_START, render


def report(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "step": "plan2-gate",
        "generated_at": "2026-09-17T12:00:00+00:00",
        "sha": "0123456789abcdef0123456789abcdef01234567",
        "answers_dir": "data/eval/plan2_answers",
        "questions_path": "data/eval/competency.jsonl",
        "report_path": "data/reports/plan2_gate.json",
        "questions": [
            {
                "id": "cq01",
                "question": "Which tests cover KAFKA-100?",
                "type": "traceability",
                "answered": True,
                "lang": "en",
                "expected_lang": "en",
                "lang_ok": True,
                "tools": ["route", "lookup"],
                "latency_ms": 42000,
                "answer_chars": 320,
                "citations": {
                    "found": 5,
                    "valid": 5,
                    "invalid": 0,
                    "invalid_list": [],
                    "by_kind": {"workitem": 1},
                },
                "sentences": {
                    "total": 3,
                    "with_citation": 3,
                    "without_citation": 0,
                    "pct_with_citation": 100.0,
                },
                "problems": [],
            },
            {
                "id": "cq02",
                "question": "Which commits fixed it?",
                "type": "traceability",
                "answered": True,
                "lang": "en",
                "expected_lang": "en",
                "lang_ok": True,
                "tools": [],
                "latency_ms": None,
                "answer_chars": 120,
                "citations": {
                    "found": 2,
                    "valid": 1,
                    "invalid": 1,
                    "invalid_list": [
                        {
                            "text": "chunk:deadbeefdead",
                            "kind": "chunk",
                            "reason": "no Chunk id starts with it",
                        }
                    ],
                    "by_kind": {"chunk": 2},
                },
                "sentences": {
                    "total": 2,
                    "with_citation": 1,
                    "without_citation": 1,
                    "pct_with_citation": 50.0,
                },
                "problems": ["no `strategy:` line"],
            },
            {
                "id": "cq16",
                "question": "אילו טסטים מכסים?",
                "type": "traceability",
                "answered": False,
                "lang": None,
                "expected_lang": "he",
                "lang_ok": False,
                "tools": [],
                "latency_ms": None,
                "answer_chars": 0,
                "citations": {
                    "found": 0,
                    "valid": 0,
                    "invalid": 0,
                    "invalid_list": [],
                    "by_kind": {},
                },
                "sentences": {
                    "total": 0,
                    "with_citation": 0,
                    "without_citation": 0,
                    "pct_with_citation": 0.0,
                },
                "problems": ["no answer file"],
            },
        ],
        "totals": {
            "questions": 3,
            "answered": 2,
            "missing": ["cq16"],
            "with_at_least_one_valid_citation": 2,
            "citations_found": 7,
            "citations_valid": 6,
            "citations_invalid": 1,
            "validity_rate": 85.71,
            "sentences_total": 5,
            "sentences_with_citation": 4,
            "sentences_without_citation": 1,
            "tools_used": {"route": 1, "lookup": 1},
            "by_lang": {"en": 2, "he": 0},
            "latency_ms": {"p50": 42000, "min": 42000, "max": 42000, "measured": 1},
        },
        "gate": {
            "ok": False,
            "criteria": [
                {
                    "name": "every_question_answered",
                    "requirement": "19/19",
                    "value": "2/3",
                    "ok": False,
                    "gate": True,
                    "detail": "missing: cq16",
                },
                {
                    "name": "citation_validity_rate_at_least_90",
                    "requirement": "≥90%",
                    "value": "85.71%",
                    "ok": False,
                    "gate": False,
                    "detail": "6/7",
                },
            ],
        },
        "planner_verdicts": {"cq01": "correct", "cq02": "partial"},
        "planner_verdicts_note": {"values": ["correct", "partial", "wrong"], "carried_from": None},
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------------- the page


def test_the_page_is_hebrew_and_names_its_source() -> None:
    page = render(report())

    assert page.startswith("# ")
    assert "נוצר אוטומטית" in page
    assert "data/reports/plan2_gate.json" in page


def section(page: str, heading: str) -> list[str]:
    """The lines under one `##` heading — the invalid-citations table also starts rows `| cq`."""
    lines = page.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"## {heading}"))
    rest = lines[start + 1 :]
    end = next((i for i, ln in enumerate(rest) if ln.startswith("## ")), len(rest))
    return rest[:end]


def test_there_is_one_table_row_per_question_including_the_missing_one() -> None:
    rows = [ln for ln in section(render(report()), "שאלה אחרי שאלה") if ln.startswith("| cq")]

    assert len(rows) == 3
    assert any(ln.startswith("| cq16 ") for ln in rows)


def test_a_row_carries_language_tools_citations_latency_and_the_verdict() -> None:
    row = next(ln for ln in render(report()).splitlines() if ln.startswith("| cq01 "))

    assert "en" in row
    assert "route" in row and "lookup" in row
    assert "5/5" in row
    assert "42000" in row or "42,000" in row
    assert "נכון" in row


def test_an_unjudged_question_shows_a_dash_rather_than_a_guess() -> None:
    page = render(report(planner_verdicts={}))
    row = next(ln for ln in page.splitlines() if ln.startswith("| cq01 "))

    assert "—" in row


def test_a_missing_latency_is_a_dash_not_a_zero() -> None:
    row = next(ln for ln in render(report()).splitlines() if ln.startswith("| cq02 "))

    assert "| — |" in row
    assert " 0 " not in row


def test_the_invalid_citations_are_listed_with_their_reason() -> None:
    page = render(report())

    assert "chunk:deadbeefdead" in page
    assert "no Chunk id starts with it" in page


def test_the_totals_and_the_gate_criteria_are_both_on_the_page() -> None:
    page = render(report())

    assert "85.71" in page
    assert "every_question_answered" in page
    assert "לא עבר" in page


def test_a_question_with_no_answer_file_says_so_rather_than_showing_zeroes_only() -> None:
    page = render(report())
    row = next(ln for ln in page.splitlines() if ln.startswith("| cq16 "))

    assert "אין תשובה" in row


# ------------------------------------------------------------------- the planner's paragraph


def test_the_page_holds_a_fixed_heading_and_an_empty_block_for_the_planner() -> None:
    page = render(report())

    assert PLANNER_HEADING in page
    assert PLANNER_START in page and PLANNER_END in page


def test_regenerating_the_page_keeps_what_the_planner_wrote_in_that_block() -> None:
    first = render(report())
    edited = first.replace(
        f"{PLANNER_START}\n", f"{PLANNER_START}\ncq01 נכון, cq02 חלקי — הראיה מספקת.\n"
    )

    again = render(report(totals={**report()["totals"], "answered": 3}), previous=edited)

    assert "cq01 נכון, cq02 חלקי — הראיה מספקת." in again
    assert again.count(PLANNER_START) == 1


def test_a_previous_page_without_the_markers_does_not_break_the_render() -> None:
    assert PLANNER_HEADING in render(report(), previous="# something else entirely\n")
