"""`brain eval run` — every refusal that must happen before a Neo4j driver opens.

The sweep itself needs a graph and an embedder and lives in `tests/live/`. What `make check`
can prove is that a wrong flag, a missing question set or an incomplete one costs nothing:
the command exits with a reason, and `_no_real_connections` in `conftest.py` is what would
fail this file loudly if any of these paths reached a driver.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.cli import app
from brain.eval import cli_runs, runs


def data_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "eval").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    return tmp_path


def questions_file(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    path = tmp_path / "eval" / "questions.jsonl"
    rows = rows or [
        {
            "id": "cq01",
            "type": "traceability",
            "lang": "en",
            "question": "Which tests cover KAFKA-14649?",
            "gold_evidence": [],
            "gold_source": "pending",
            "origin": "competency",
        }
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def merge_report(tmp_path: Path, *, complete: bool) -> Path:
    path = tmp_path / "reports" / "eval_questions.json"
    path.write_text(json.dumps({"merge": {"complete": complete}}), encoding="utf-8")
    return path


def test_run_is_mounted_on_the_eval_group(runner) -> None:
    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output


def test_the_help_text_lists_the_same_strategies_the_sweep_runs() -> None:
    assert cli_runs.DEFAULT_STRATEGIES == ",".join(runs.STRATEGIES)


def test_the_agentic_mode_is_refused_by_name_and_says_where_it_lives(runner, tmp_path, monkeypatch):
    data_dir(tmp_path, monkeypatch)
    questions_file(tmp_path)
    result = runner.invoke(app, ["eval", "run", "--mode", "agentic"])
    assert result.exit_code == 2
    assert "Task 3" in result.output


def test_an_unknown_mode_names_the_modes_that_exist(runner, tmp_path, monkeypatch):
    data_dir(tmp_path, monkeypatch)
    result = runner.invoke(app, ["eval", "run", "--mode", "turbo"])
    assert result.exit_code == 2
    assert "fixed" in result.output


def test_an_unknown_strategy_is_refused_before_anything_runs(runner, tmp_path, monkeypatch):
    data_dir(tmp_path, monkeypatch)
    questions_file(tmp_path)
    result = runner.invoke(app, ["eval", "run", "--strategies", "s1,s9"])
    assert result.exit_code == 2
    assert "s9" in result.output


def test_a_missing_question_set_is_named(runner, tmp_path, monkeypatch):
    data_dir(tmp_path, monkeypatch)
    result = runner.invoke(app, ["eval", "run"])
    assert result.exit_code == 2
    assert "questions.jsonl" in result.output


def test_only_complete_refuses_an_unfinished_question_set(runner, tmp_path, monkeypatch):
    data_dir(tmp_path, monkeypatch)
    questions_file(tmp_path)
    merge_report(tmp_path, complete=False)
    result = runner.invoke(app, ["eval", "run", "--only-complete"])
    assert result.exit_code == 2
    assert "merge.complete" in result.output


def test_an_absent_task_one_report_reads_as_not_complete(tmp_path):
    """Absent, unreadable or not-yet-written all mean the same thing: do not claim complete."""
    assert cli_runs._questions_complete(tmp_path / "nope.json") is False
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert cli_runs._questions_complete(broken) is False


def test_a_complete_flag_is_read_from_the_task_one_report(tmp_path, monkeypatch):
    data_dir(tmp_path, monkeypatch)
    path = merge_report(tmp_path, complete=True)
    assert cli_runs._questions_complete(path) is True


def test_a_bad_s5_scope_is_refused(runner, tmp_path, monkeypatch):
    data_dir(tmp_path, monkeypatch)
    questions_file(tmp_path)
    result = runner.invoke(app, ["eval", "run", "--s5-scope", "sometimes"])
    assert result.exit_code == 2
    assert "thematic" in result.output
