"""`docs/report/eval-report.md` — the Hebrew page, and the two paragraphs it must not eat.

Two things are tested here that a "does it render" test would miss:

* **Every section exists in both states.** A section with an input prints its numbers; a
  section without one prints `טרם נמדד` *and* the command that ends that state. Both are
  asserted for every section, because a report that silently drops its missing halves looks
  finished when it is not.
* **Regeneration keeps the planner's prose.** Spec §5.6 puts two hand-written sections at
  the end. The generator that deletes them is regenerated exactly once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brain.eval import report_build as rb
from brain.eval import report_render as rr
from tests.eval_report_helpers import (
    SHA,
    eval_answers,
    eval_retrieval,
    incremental,
    index,
    write_agentic,
    write_reports,
)


def doc(tmp_path, *, only=None, agentic: bool = False, **overrides):
    reports = write_reports(tmp_path, only=only, **overrides)
    if agentic:
        write_agentic(tmp_path)
    return rb.build(
        reports,
        eval_dir=tmp_path / "eval",
        sha=SHA,
        generated_at="2026-09-17T12:00:00+00:00",
    )


def page(tmp_path, **kwargs) -> str:
    return rr.render(doc(tmp_path, **kwargs))


#: Every section the document always has, measured or not. A report that drops its unmeasured
#: halves looks finished when it is not, so these are asserted in both states.
SECTIONS = [
    "# הערכה — דוח Plan 3",
    "## סט השאלות",
    "## שכבה 0 — harvest",
    "## שכבה 1 — מפקד הגרף",
    "### שכבה 1 — איחוד ישויות",
    "### שכבה 1 — כיסוי provenance",
    "### שכבה 1 — כיסוי קהילות",
    "## שכבה 2 — אחזור (דטרמיניסטי)",
    "## שכבה 3 — תשובות ושיפוט עיוור",
    "### מצב B (אגנטי) לצד מצב A",
    "## עדכון אינקרמנטלי (§5.5)",
    "## מתי מה",
    "## מתי לא הייתי משתמש בגרף כאן",
    "## מה הייתי משנה",
]
#: Tables inside a section. They appear with their section's input and not before it — an
#: empty table under a heading says less than the pending note that replaces both.
TABLES = [
    "### מטריצה: אסטרטגיה × סוג שאלה",
    "### עלות לפי אסטרטגיה",
    "### חוצה-שפות (EN מול HE)",
    "### עלות התשתית: reranker ו-guard",
    "### pairwise מול ה-baseline (`s1r`)",
    "### לפי שפת השאלה",
    "### הסכמה בין השופטים",
    "### שופט מול בדיקת הקוד (ציטוטים)",
    "### מה נוסף לגרף",
    "### חמש השאלות על הפריטים החדשים",
]


# ------------------------------------------------------------------------------- shape


@pytest.mark.parametrize("heading", SECTIONS + TABLES)
def test_every_section_is_present_with_every_input(tmp_path, heading):
    assert heading in page(tmp_path).splitlines()


@pytest.mark.parametrize("heading", SECTIONS)
def test_every_section_is_present_with_no_input_at_all(tmp_path, heading):
    """The document's shape is the contract; the numbers are what fills in later."""
    assert heading in page(tmp_path, only=()).splitlines()


@pytest.mark.parametrize("heading", TABLES)
def test_a_table_does_not_appear_without_its_input(tmp_path, heading):
    assert heading not in page(tmp_path, only=()).splitlines()


def test_an_empty_document_is_all_pending_and_all_actionable(tmp_path):
    text = page(tmp_path, only=())
    assert text.count("**טרם נמדד.**") >= 6
    for source in rb.INPUTS:
        assert f"`{source.command}`" in text


def test_the_header_states_the_stamp_of_every_input(tmp_path):
    text = page(tmp_path, eval_retrieval=eval_retrieval(sha="b" * 40))
    assert "HEAD `aaaaaaaa`" in text
    for source in rb.INPUTS:
        assert f"`data/reports/{source.filename}`" in text
    assert "STALE" in text
    assert "bbbbbbbb" in text


def test_a_stale_section_is_marked_where_its_numbers_are_printed(tmp_path):
    text = page(tmp_path, eval_retrieval=eval_retrieval(sha="b" * 40))
    layer2 = text.split("## שכבה 2")[1].split("## שכבה 3")[0]
    assert "**STALE**" in layer2


def test_a_present_file_missing_a_section_does_not_say_the_file_is_missing(tmp_path):
    text = page(tmp_path, eval_answers=eval_answers(judged=False))
    layer3 = text.split("## שכבה 3")[1].split("## עדכון אינקרמנטלי")[0]
    assert "קיים, אבל הסעיף הזה עדיין לא נכתב" in layer3
    assert "`brain eval judge merge`" in layer3


# ------------------------------------------------------------------------------ numbers


def test_no_number_is_typed_by_hand_layer_1(tmp_path):
    text = page(tmp_path)
    for value in ("58,622", "161,985", "1,297", "0.868", "89.41"):
        assert value in text


def test_no_number_is_typed_by_hand_layer_2(tmp_path):
    text = page(tmp_path)
    for value in ("0.61", "0.28", "4,200", "410", "1,605", "56/56"):
        assert value in text
    assert "no anchor key (1)" in text


def test_a_long_list_of_na_reasons_is_capped_but_counted(tmp_path):
    """Truncating the table is a formatting choice; dropping a reason would not be."""
    report = eval_retrieval()
    report["by_strategy"]["s3"]["na_reasons"] = {f"reason {i}": 10 - i for i in range(6)}
    text = page(tmp_path, eval_retrieval=report)
    assert "reason 0 (10)" in text and "reason 2 (8)" in text
    assert "reason 5 (5)" not in text
    assert "ועוד 3 סיבות ב-JSON" in text


def test_a_short_list_of_na_reasons_is_printed_whole(tmp_path):
    assert "no anchor key (1)" in page(tmp_path)
    assert "ועוד" not in page(tmp_path).split("### עלות לפי אסטרטגיה")[1].split("###")[0]


def test_changing_the_input_changes_the_page(tmp_path):
    """The point of generating it: nobody can edit the document into being wrong."""
    before = page(tmp_path)
    census = index()
    census["nodes"]["total"] = 12
    after = page(tmp_path, index=census)
    assert "58,622" in before and "58,622" not in after


def test_the_matrix_prints_a_row_per_strategy_and_type(tmp_path):
    matrix = page(tmp_path).split("### מטריצה: אסטרטגיה × סוג שאלה")[1].split("###")[0]
    rows = [line for line in matrix.splitlines() if line.startswith("| s")]
    assert len(rows) == 4  # two strategies x two types
    assert all("recall" not in row for row in rows)


def test_the_strategies_not_swept_yet_are_named(tmp_path):
    assert "`s1`, `s2`, `s4`, `s5`, `s6`" in page(tmp_path)


def test_the_judge_matrix_shows_mode_b_beside_mode_a(tmp_path):
    text = page(tmp_path, agentic=True)
    layer3 = text.split("## שכבה 3")[1]
    assert "| agentic |" in layer3
    assert "| s1r |" in layer3
    mode_b = layer3.split("### מצב B")[1]
    assert "2" in mode_b and "97.4" in mode_b


def test_the_judge_scores_are_broken_out_by_question_language(tmp_path):
    """A third of the set is Hebrew; the report says whether that third scores the same."""
    section = page(tmp_path).split("### לפי שפת השאלה")[1].split("###")[0]
    assert "| אנגלית |" in section


def test_mode_b_is_pending_without_agentic_answers(tmp_path):
    mode_b = page(tmp_path).split("### מצב B")[1]
    assert "טרם נמדד" in mode_b
    assert "brain eval answers merge" in mode_b


def test_mode_b_is_reported_even_when_the_judge_has_not_run(tmp_path):
    """The two are independent: mode B's answers exist before anybody scores them."""
    text = page(tmp_path, agentic=True, eval_answers=eval_answers(judged=False))
    assert "### מצב B (אגנטי) לצד מצב A" in text
    assert "97.4" in text.split("### מצב B")[1]


def test_the_incremental_section_names_an_unmeasured_step(tmp_path):
    text = page(tmp_path, incremental=incremental(complete=False))
    section = text.split("## עדכון אינקרמנטלי")[1].split("## מתי מה")[0]
    assert "| index |" in section
    assert "לא |" in section
    assert "validate_incremental" in section


def test_when_what_prints_the_baseline_beside_the_winner(tmp_path):
    section = page(tmp_path).split("## מתי מה")[1].split("## מתי לא")[0]
    line = next(line for line in section.splitlines() if "traceability" in line)
    assert "s3" in line and "0.61" in line and "0.3" in line and "38" in line


def test_when_what_says_the_correctness_columns_are_empty_until_the_judge_runs(tmp_path):
    text = page(tmp_path, eval_answers=eval_answers(judged=False))
    assert "עמודות הנכונוּת ריקות" in text.split("## מתי מה")[1]


# ----------------------------------------------------------------- the planner's blocks


def test_the_two_planner_blocks_start_empty(tmp_path):
    text = page(tmp_path)
    assert text.count(rr.PLANNER_START) == 2
    assert text.count(rr.PLANNER_END) == 2
    assert text.count(rr.EMPTY_PLANNER) == 2


def test_regenerating_keeps_both_planner_blocks(tmp_path):
    first = page(tmp_path)
    written = first.replace(rr.EMPTY_PLANNER, "אני לא הייתי משתמש בגרף ל-X.", 1)
    written = written.replace(rr.EMPTY_PLANNER, "הייתי משנה את Y.", 1)
    second = rr.render(doc(tmp_path), previous=written)
    assert "אני לא הייתי משתמש בגרף ל-X." in second
    assert "הייתי משנה את Y." in second


def test_each_planner_block_is_carried_under_its_own_heading(tmp_path):
    """Anchored on the heading, so inserting a section between them cannot swap them."""
    previous = (
        "# x\n\n## מה הייתי משנה\n\n"
        f"{rr.PLANNER_START}\nB\n{rr.PLANNER_END}\n\n"
        "## מתי לא הייתי משתמש בגרף כאן\n\n"
        f"{rr.PLANNER_START}\nA\n{rr.PLANNER_END}\n"
    )
    assert rr.carry(previous, "מתי לא הייתי משתמש בגרף כאן") == "A"
    assert rr.carry(previous, "מה הייתי משנה") == "B"


def test_a_heading_whose_markers_were_deleted_does_not_inherit_the_next_block(tmp_path):
    previous = (
        "## מתי לא הייתי משתמש בגרף כאן\n\n(אין סימנים כאן)\n\n"
        "## מה הייתי משנה\n\n"
        f"{rr.PLANNER_START}\nB\n{rr.PLANNER_END}\n"
    )
    assert rr.carry(previous, "מתי לא הייתי משתמש בגרף כאן") == ""
    assert rr.carry(previous, "מה הייתי משנה") == "B"


def test_an_untouched_placeholder_is_not_carried_as_prose(tmp_path):
    first = page(tmp_path)
    assert rr.carry(first, "מה הייתי משנה") == ""


def test_the_document_ends_with_the_two_mandatory_headings(tmp_path):
    headings = [line for line in page(tmp_path).splitlines() if line.startswith("## ")]
    assert headings[-2:] == ["## מתי לא הייתי משתמש בגרף כאן", "## מה הייתי משנה"]


def test_run_report_keeps_the_prose_across_a_real_rerun(tmp_path):
    reports = write_reports(tmp_path)
    out = tmp_path / "eval-report.md"
    kwargs = {"reports_dir": reports, "eval_dir": tmp_path / "eval", "out_path": out}
    rb.run_report(**kwargs)
    out.write_text(
        out.read_text(encoding="utf-8").replace(rr.EMPTY_PLANNER, "פסקה של המתכנן.", 1),
        encoding="utf-8",
    )
    rb.run_report(**kwargs)
    assert "פסקה של המתכנן." in out.read_text(encoding="utf-8")


def test_the_page_is_markdown_with_balanced_tables(tmp_path):
    for line in page(tmp_path).splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            assert line.endswith("|"), line


def test_the_document_path_is_the_one_the_roadmap_gate_names():
    assert rb.DOCUMENT == Path("docs/report/eval-report.md")
