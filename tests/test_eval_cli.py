"""`brain eval` — the two commands, and the refusals that happen before a driver opens.

`eval` was the last Plan 3 stub in `brain/cli.py`. It is now a group of two, and the tests
here are the ones `make check` can run: argument validation, the no-answers path (which
must reach a report and an exit code without a graph), and `gate-report`, which reads JSON
and never touches Neo4j at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.cli import app
from tests.test_eval_render import report as sample_report


def data_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "eval").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    return tmp_path


def competency(tmp_path: Path) -> Path:
    path = tmp_path / "eval" / "competency.jsonl"
    path.write_text(
        json.dumps({"id": "cq01", "lang": "en", "type": "traceability", "question": "?"}) + "\n",
        encoding="utf-8",
    )
    return path


# ------------------------------------------------------------------------------- the group


def test_eval_is_a_group_of_two_commands(runner) -> None:
    result = runner.invoke(app, ["eval", "--help"])

    assert result.exit_code == 0
    for command in ("cite-check", "gate-report"):
        assert command in result.output


def test_eval_is_no_longer_a_not_implemented_stub(runner) -> None:
    result = runner.invoke(app, ["eval"])

    assert "not implemented" not in result.output


# ---------------------------------------------------------------------------- cite-check


def test_cite_check_rejects_a_missing_question_file_before_touching_neo4j(
    runner, tmp_path, monkeypatch
) -> None:
    data_dir(tmp_path, monkeypatch)

    result = runner.invoke(app, ["eval", "cite-check", "--questions", str(tmp_path / "nope")])

    assert result.exit_code == 2
    assert "--questions" in result.output


def test_cite_check_with_no_answers_at_all_fails_the_gate_without_a_graph(
    runner, tmp_path, monkeypatch
) -> None:
    """Nothing to verify means nothing to ask the graph — and still a report and an exit 1."""
    data_dir(tmp_path, monkeypatch)
    competency(tmp_path)

    result = runner.invoke(app, ["eval", "cite-check"])

    # `SystemExit` and nothing else: conftest's guard raises `ForbiddenConnection` the
    # moment an unmarked test opens a Neo4j driver, so this is the assertion that the
    # command reached a verdict without one.
    assert type(result.exception) is SystemExit
    assert result.exit_code == 1
    written = json.loads((tmp_path / "reports" / "plan2_gate.json").read_text(encoding="utf-8"))
    assert written["totals"]["missing"] == ["cq01"]


def test_cite_check_writes_the_answer_layout_template_next_to_the_answers(
    runner, tmp_path, monkeypatch
) -> None:
    """The contract the analyst reads and the parser that enforces it ship together."""
    from brain.eval.answers import README_TEMPLATE

    data_dir(tmp_path, monkeypatch)
    competency(tmp_path)

    runner.invoke(app, ["eval", "cite-check"])

    readme = tmp_path / "eval" / "plan2_answers" / "README.md"
    assert readme.read_text(encoding="utf-8") == README_TEMPLATE


# --------------------------------------------------------------------------- gate-report


def test_gate_report_renders_the_hebrew_page_from_the_json(runner, tmp_path, monkeypatch) -> None:
    data_dir(tmp_path, monkeypatch)
    report_path = tmp_path / "reports" / "plan2_gate.json"
    report_path.write_text(json.dumps(sample_report(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "docs" / "report" / "plan2-first-questions.md"

    result = runner.invoke(app, ["eval", "gate-report", "--out", str(out)])

    assert result.exit_code == 0
    page = out.read_text(encoding="utf-8")
    assert "| cq01 " in page and "85.71" in page


def test_gate_report_refuses_when_cite_check_has_not_run(runner, tmp_path, monkeypatch) -> None:
    data_dir(tmp_path, monkeypatch)

    result = runner.invoke(app, ["eval", "gate-report"])

    assert result.exit_code == 2
    assert "cite-check" in result.output


def test_gate_report_keeps_the_planner_paragraph_it_finds_in_the_old_page(
    runner, tmp_path, monkeypatch
) -> None:
    from brain.eval.render import PLANNER_START

    data_dir(tmp_path, monkeypatch)
    (tmp_path / "reports" / "plan2_gate.json").write_text(
        json.dumps(sample_report(), ensure_ascii=False), encoding="utf-8"
    )
    out = tmp_path / "docs" / "report" / "plan2-first-questions.md"
    runner.invoke(app, ["eval", "gate-report", "--out", str(out)])
    out.write_text(
        out.read_text(encoding="utf-8").replace(
            f"{PLANNER_START}\n", f"{PLANNER_START}\nההכרעה שלי: cq01 נכון.\n"
        ),
        encoding="utf-8",
    )

    runner.invoke(app, ["eval", "gate-report", "--out", str(out)])

    assert "ההכרעה שלי: cq01 נכון." in out.read_text(encoding="utf-8")
