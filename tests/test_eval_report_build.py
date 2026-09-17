"""`brain eval report` — the reading half: what the document says it knows, and what it does not.

Plan 3 decision 8 says every number in `docs/report/eval-report.md` is generated from JSON.
The tests that matter here are therefore about *absence*: a section whose input was never
written must come out as pending with the command that writes it, and a section whose input
was written at another commit must come out marked, not silently republished.
"""

from __future__ import annotations

import json

import pytest

from brain.eval import report_build as rb
from tests.eval_report_helpers import (
    OTHER_SHA,
    SHA,
    eval_answers,
    eval_retrieval,
    write_agentic,
    write_reports,
)


def build(tmp_path, *, only=None, sha: str = SHA, **overrides):
    reports = write_reports(tmp_path, only=only, **overrides)
    return rb.build(
        reports, eval_dir=tmp_path / "eval", sha=sha, generated_at="2026-09-17T12:00:00+00:00"
    )


# ------------------------------------------------------------------------------ the inputs


def test_every_input_has_a_producing_command():
    """A pending section is useless without the command that ends the pending state."""
    assert len(rb.INPUTS) == 9
    for source in rb.INPUTS:
        assert source.command.startswith("brain "), source
        assert source.brings, source


def test_a_missing_reports_directory_is_an_error(tmp_path):
    with pytest.raises(rb.ReportError):
        rb.build(tmp_path / "nope", eval_dir=tmp_path / "eval")


def test_a_corrupt_input_is_an_error_not_a_silent_skip(tmp_path):
    reports = write_reports(tmp_path, only=["index"])
    (reports / "index.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(rb.ReportError):
        rb.build(reports, eval_dir=tmp_path / "eval", sha=SHA)


def test_an_empty_reports_directory_still_builds_a_whole_document(tmp_path):
    doc = build(tmp_path, only=())
    assert [row["present"] for row in doc["inputs"]] == [False] * 9
    for row in doc["inputs"]:
        assert row["note"] == "הקובץ לא קיים"
    assert doc["layer2"]["present"] is False
    assert doc["layer3"]["present"] is False
    assert doc["incremental"]["present"] is False
    assert doc["when_what"]["present"] is False


def test_an_input_measured_at_another_commit_is_stale(tmp_path):
    doc = build(tmp_path, eval_retrieval=eval_retrieval(sha=OTHER_SHA))
    row = next(r for r in doc["inputs"] if r["key"] == "eval_retrieval")
    assert row["stale"] is True
    assert OTHER_SHA[:8] in row["note"] and SHA[:8] in row["note"]


def test_an_abbreviated_sha_is_not_a_different_commit(tmp_path):
    """`incremental.json` stamps `git rev-parse --short`; the report asks `git rev-parse`."""
    from tests.eval_report_helpers import incremental as incremental_fixture

    run = incremental_fixture()
    run["sha"] = SHA[:7]
    doc = build(tmp_path, incremental=run)
    row = next(r for r in doc["inputs"] if r["key"] == "incremental")
    assert row["stale"] is False
    assert row["note"] == ""


@pytest.mark.parametrize(
    ("measured", "sha", "same"),
    [
        ("a" * 40, "a" * 40, True),
        ("a" * 7, "a" * 40, True),
        ("a" * 40, "a" * 7, True),
        ("A" * 7, "a" * 40, True),
        ("a" * 7 + "b", "a" * 7 + "c", False),
        ("a" * 6, "a" * 40, False),  # too short to mean anything
        ("", "a" * 40, False),
        ("a" * 40, "", False),
    ],
)
def test_same_commit(measured, sha, same):
    assert rb.same_commit(measured, sha) is same


def test_an_input_without_a_sha_cannot_prove_it_is_fresh(tmp_path):
    doc = build(tmp_path, only=["index"])
    row = next(r for r in doc["inputs"] if r["key"] == "index")
    assert row["stale"] is None
    assert "sha" in row["note"]


def test_a_stale_subsection_marks_the_file_even_at_head(tmp_path):
    """`eval_answers.json` is written by two steps; one of them can lag behind the other."""
    report = eval_answers()
    report["sections"]["answers_merge"]["stale"] = True
    doc = build(tmp_path, eval_answers=report)
    row = next(r for r in doc["inputs"] if r["key"] == "eval_answers")
    assert row["stale"] is True
    assert row["stale_sections"] == ["answers_merge"]


def test_a_file_that_moved_does_not_also_list_all_of_its_subsections(tmp_path):
    """Every section of a stale file is stale; naming all 26 buries the one fact that matters."""
    report = eval_answers(sha=OTHER_SHA)
    for stamp in report["sections"].values():
        stamp["stale"] = True
    doc = build(tmp_path, eval_answers=report)
    row = next(r for r in doc["inputs"] if r["key"] == "eval_answers")
    assert row["stale"] is True
    assert len(row["stale_sections"]) > 1  # the data is kept
    assert "סעיפים ישנים" not in row["note"]  # the sentence is not
    assert OTHER_SHA[:8] in row["note"]


def test_a_present_file_with_an_absent_section_does_not_claim_the_file_is_missing(tmp_path):
    """The judge sections arrive after the answers do — into the same file."""
    doc = build(tmp_path, eval_answers=eval_answers(judged=False))
    state = doc["layer3"]["state"]
    assert state["present"] is False
    assert state["file_present"] is True
    assert "הקובץ לא קיים" not in state["note"]
    assert state["command"] == "brain eval judge merge"
    assert "184" in state["note"]


# -------------------------------------------------------------------------- layers 0 and 1


def test_layer_0_and_1_read_their_numbers_from_the_step_reports(tmp_path):
    layer01 = build(tmp_path)["layer01"]
    assert {r["source"] for r in layer01["harvest"]["sources"]} == {"jira", "git"}
    assert layer01["harvest"]["sources"][1]["records"] == 1416  # jira, from the checkpoint
    assert layer01["census"]["nodes_total"] == 58622
    assert layer01["census"]["gate"]["failed_names"] == ["person_resolution_recall"]
    assert layer01["provenance"]["edges_pct"] == 100.0
    assert layer01["communities"]["pct_members_in_a_summarised_community"] == 89.41


def test_resolution_prefers_the_census_summary(tmp_path):
    rows = build(tmp_path)["layer01"]["resolution"]["rows"]
    assert [r["kind"] for r in rows] == ["person"]
    assert rows[0]["f1"] == 0.868


def test_resolution_falls_back_to_resolve_json_when_the_census_has_none(tmp_path):
    """`brain index` summarises it, but `brain resolve eval` is where it is measured."""
    from tests.eval_report_helpers import index as index_fixture

    census = index_fixture()
    census["resolution"] = {"available": False, "source": "data/reports/resolve.json"}
    rows = build(tmp_path, index=census)["layer01"]["resolution"]["rows"]
    assert [r["kind"] for r in rows] == ["entity"]
    assert rows[0]["recall"] == 0.98


def test_a_graph_without_communities_is_pending_not_empty(tmp_path):
    from tests.eval_report_helpers import index as index_fixture

    census = index_fixture()
    census["communities"] = {"present": False, "note": "GDS never ran"}
    part = build(tmp_path, index=census)["layer01"]["communities"]
    assert part["state"]["present"] is False
    assert part["state"]["command"] == "brain communities"


# ------------------------------------------------------------------------------- layer 2


def test_layer_2_orders_strategies_and_names_the_ones_not_swept(tmp_path):
    layer2 = build(tmp_path)["layer2"]
    assert layer2["strategies"] == ["s1r", "s3"]
    assert layer2["missing_strategies"] == ["s1", "s2", "s4", "s5", "s6"]
    assert layer2["baseline"] == "s1r"
    assert layer2["budget_tokens"] == 4000


def test_order_strategies_keeps_an_unknown_column_instead_of_dropping_it():
    assert rb.order_strategies({"s3", "s1r", "zz"}) == ["s1r", "s3", "zz"]


def test_the_cost_table_carries_the_reasons_a_cell_is_na(tmp_path):
    cost = {r["strategy"]: r for r in build(tmp_path)["layer2"]["cost"]}
    assert cost["s3"]["na"] == 1
    assert cost["s3"]["na_reasons"] == ["no anchor key (1)"]
    assert cost["s3"]["latency_p95_ms"] == 410
    assert cost["s1r"]["na_reasons"] == []


def test_the_na_reasons_are_ordered_by_how_often_they_happened(tmp_path):
    """S5 spells the router rule into its reason, so the commonest must sort first."""
    report = eval_retrieval()
    report["by_strategy"]["s3"]["na_reasons"] = {"rare": 1, "common": 9, "middling": 4}
    cost = {r["strategy"]: r for r in build(tmp_path, eval_retrieval=report)["layer2"]["cost"]}
    assert cost["s3"]["na_reasons"] == ["common (9)", "middling (4)", "rare (1)"]


def test_the_retrieval_stack_cost_comes_from_retrieve_json(tmp_path):
    infra = build(tmp_path)["layer2"]["infra"]
    assert infra["rerank"]["cost_p50_ms"] == 1605
    assert infra["guard"]["refused"] == 56 and infra["guard"]["leaked"] == 0


def test_layer_2_is_pending_without_its_report(tmp_path):
    doc = build(tmp_path, only=["index", "retrieve"])
    assert doc["layer2"]["present"] is False
    assert doc["layer2"]["state"]["command"] == "brain eval run --mode fixed"
    # The stack's own cost does not depend on the sweep, so it survives the pending section.
    assert doc["layer2"]["infra"]["present"] is True


# ------------------------------------------------------------------------------- layer 3


def test_layer_3_reads_what_judge_merge_writes(tmp_path):
    layer3 = build(tmp_path)["layer3"]
    assert layer3["present"] is True
    assert layer3["metrics"] == ("faithfulness", "correctness", "citation_validity", "relevancy")
    assert layer3["matrix"]["s3"]["traceability"]["correctness"] == 2.0
    assert layer3["pairwise"]["by_challenger"]["s3"]["win_rate"] == 0.5
    assert layer3["agreement"]["overlap_cases"] == 1
    assert layer3["agreement"]["by_metric"]["faithfulness"]["within_1"] == 1
    assert layer3["crosscheck"]["compared"] == 2


def test_the_code_citation_check_is_counted_not_copied(tmp_path):
    code = build(tmp_path)["layer3"]["code_check"]
    assert code == {
        "cases": 2,
        "code_valid": 1,
        "code_invalid": 1,
        "no_citation": 0,
        "not_in_context": 1,
        "not_in_graph": 0,
    }


def test_mode_b_is_on_exactly_when_the_agentic_answers_exist(tmp_path):
    assert build(tmp_path)["layer3"]["mode_b"]["present"] is False
    write_agentic(tmp_path)
    mode_b = build(tmp_path)["layer3"]["mode_b"]
    assert mode_b["present"] is True
    assert mode_b["answers"] == 2 and mode_b["qids"] == ["cq01", "cq02"]
    assert mode_b["gate"]["validity_rate"] == 97.4


def test_the_agentic_column_survives_into_the_judge_matrix(tmp_path):
    """Mode B sits beside mode A: one more column, never folded into the mode A means."""
    layer3 = build(tmp_path)["layer3"]
    assert layer3["strategies"] == ["s1r", "s3", "agentic"]
    agentic = layer3["matrix"]["agentic"]["traceability"]
    assert agentic["correctness"] == 2.0
    # No packed context was recorded, so faithfulness is missing rather than zero.
    assert agentic["faithfulness"] is None and agentic["faithfulness_n"] == 0


def test_layer_3_is_pending_before_the_judges_return(tmp_path):
    doc = build(tmp_path, eval_answers=eval_answers(judged=False))
    assert doc["layer3"]["present"] is False
    assert doc["layer3"]["matrix"] == {}
    assert doc["layer3"]["answers"]["accepted"] == 184


# ---------------------------------------------------------------------------- incremental


def test_the_incremental_section_keeps_a_row_for_a_step_that_was_not_measured(tmp_path):
    from tests.eval_report_helpers import incremental as incremental_fixture

    doc = build(tmp_path, incremental=incremental_fixture(complete=False))
    part = doc["incremental"]
    measured = {r["step"]: r["measured"] for r in part["steps"]}
    assert measured["harvest"] is True
    assert measured["index"] is False
    assert any("index" in p for p in part["problems"])


def test_a_complete_incremental_run_reports_no_problems(tmp_path):
    part = build(tmp_path)["incremental"]
    assert part["present"] is True
    assert part["problems"] == []
    assert len(part["keys"]) == 10
    assert part["communities"]["member_hash_changed"] == 7


# ------------------------------------------------------------------------------- "מתי מה"


def test_when_what_picks_a_winner_per_type_and_prices_the_win(tmp_path):
    rows = {r["type"]: r for r in build(tmp_path)["when_what"]["rows"]}
    trace = rows["traceability"]
    assert trace["best_recall"] == {"strategy": "s3", "value": 0.61}
    assert trace["baseline_recall"] == 0.30
    assert trace["latency_delta_ms"] == 38
    assert trace["tokens_delta"] == 1200
    assert trace["cypher_delta"] == 14
    # The baseline wins rationale, so the delta against itself is zero, not blank.
    assert rows["rationale"]["best_recall"]["strategy"] == "s1r"
    assert rows["rationale"]["latency_delta_ms"] == 0


def test_when_what_uses_the_judge_for_the_correctness_column(tmp_path):
    rows = {r["type"]: r for r in build(tmp_path)["when_what"]["rows"]}
    assert rows["traceability"]["best_correctness"]["strategy"] == "s3"
    assert rows["traceability"]["baseline_correctness"] == 1.0


def test_when_what_says_so_when_the_baseline_did_not_run_on_a_type(tmp_path):
    report = eval_retrieval()
    del report["matrix"]["s1r"]["traceability"]
    rows = {r["type"]: r for r in build(tmp_path, eval_retrieval=report)["when_what"]["rows"]}
    assert rows["traceability"]["baseline_recall"] is None
    assert "baseline" in rows["traceability"]["note"]
    assert rows["traceability"]["latency_delta_ms"] is None


def test_when_what_breaks_a_tie_by_strategy_order_not_by_dict_order():
    layer2 = {
        "matrix": {
            "s3": {"traceability": {"scored": 3, "recall": 0.5}},
            "s1r": {"traceability": {"scored": 3, "recall": 0.5}},
        },
        "types": ["traceability"],
        "baseline": "s1r",
    }
    row = rb.when_what(layer2, {"matrix": {}})["rows"][0]
    assert row["best_recall"]["strategy"] == "s1r"


def test_a_cell_nobody_scored_never_wins():
    layer2 = {
        "matrix": {"s3": {"global": {"scored": 0, "recall": None, "na": 6}}},
        "types": ["global"],
        "baseline": "s1r",
    }
    row = rb.when_what(layer2, {"matrix": {}})["rows"][0]
    assert row["best_recall"] is None
    assert "אף אסטרטגיה" in row["note"]


# ------------------------------------------------------------------------------- the run


def test_run_report_writes_the_document_and_reports_what_is_pending(tmp_path):
    reports = write_reports(tmp_path, only=["index", "eval_retrieval", "retrieve"])
    out = tmp_path / "docs" / "report" / "eval-report.md"
    path, doc = rb.run_report(reports_dir=reports, eval_dir=tmp_path / "eval", out_path=out)
    assert path == out and out.is_file()
    lines = rb.summary_lines(doc)
    assert any("MISSING" in line and "incremental.json" in line for line in lines)
    assert lines[-1].startswith("pending sections:")
    assert "layer 3" in lines[-1]


def test_rerunning_the_report_is_idempotent(tmp_path):
    reports = write_reports(tmp_path)
    out = tmp_path / "eval-report.md"
    kwargs = {"reports_dir": reports, "eval_dir": tmp_path / "eval", "out_path": out}
    rb.run_report(**kwargs)
    first = out.read_text(encoding="utf-8")
    rb.run_report(**kwargs)
    second = out.read_text(encoding="utf-8")
    # Only the generation stamp may move; the tables must not.
    assert _without_stamp(first) == _without_stamp(second)


def _without_stamp(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if "נוצר אוטומטית" not in line)


def test_the_cli_exits_2_when_there_are_no_reports_at_all(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from brain.cli import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    result = CliRunner().invoke(app, ["eval", "report", "--out", str(tmp_path / "out.md")])
    assert result.exit_code == 2
    assert "eval report" in result.output


def test_the_cli_writes_the_document(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from brain.cli import app

    data = tmp_path / "data"
    write_reports(
        data, only=["index", "eval_retrieval"]
    )  # -> <data>/reports, == settings.reports_dir
    monkeypatch.setenv("DATA_DIR", str(data))
    out = tmp_path / "out.md"
    result = CliRunner().invoke(app, ["eval", "report", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert "מתי לא הייתי משתמש בגרף כאן" in out.read_text(encoding="utf-8")
    assert json.loads((data / "reports" / "index.json").read_text())["nodes"]["total"] == 58622
