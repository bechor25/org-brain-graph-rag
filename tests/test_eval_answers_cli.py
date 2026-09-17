"""`brain eval answers` and `brain eval judge`: mounted, import-light, and not drifting.

A CLI option's default is documentation, and documentation that lives in two files drifts.
These tests are the seam: every constant repeated in a `cli_*` module (so that `brain --help`
does not import neo4j) is asserted equal to the module that actually uses it.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from brain.cli import app
from brain.eval import answers_batches as ab
from brain.eval import cli_answers, cli_judge
from brain.eval import judge_batches as jb
from brain.eval import runs as runs_mod

runner = CliRunner()


def test_the_answers_defaults_are_the_ones_the_sweep_uses():
    assert cli_answers.DEFAULT_STRATEGIES == ",".join(runs_mod.STRATEGIES)
    assert cli_answers.DEFAULT_SHARDS == ab.DEFAULT_SHARDS
    assert cli_answers.DEFAULT_BATCH_SIZE == ab.DEFAULT_BATCH_SIZE


def test_the_judge_defaults_are_the_ones_the_build_uses():
    assert cli_judge.DEFAULT_SHARDS == jb.DEFAULT_SHARDS
    assert cli_judge.DEFAULT_BATCH_SIZE == jb.DEFAULT_BATCH_SIZE
    assert cli_judge.DEFAULT_OVERLAP == jb.DEFAULT_OVERLAP
    assert cli_judge.DEFAULT_SEED == jb.DEFAULT_SEED
    assert cli_judge.DEFAULT_BASELINE == jb.BASELINE
    assert cli_judge.DEFAULT_PAIR_WITH == ",".join(jb.PAIR_WITH)


def test_the_baseline_is_the_one_the_runs_module_measures_against():
    assert jb.BASELINE == runs_mod.BASELINE


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["eval", "answers", "--help"], ["build", "merge"]),
        (["eval", "judge", "--help"], ["build", "merge", "sample"]),
    ],
)
def test_the_sub_apps_are_mounted(argv, expected):
    result = runner.invoke(app, argv)
    assert result.exit_code == 0
    for name in expected:
        assert name in result.stdout


def test_help_does_not_import_the_retrieval_stack():
    """`brain --help` must stay cheap: the heavy imports live inside the command bodies."""
    import ast
    from pathlib import Path

    for module in (cli_answers, cli_judge):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        top_level = [
            node
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
        ]
        names = {
            alias.name if isinstance(node, ast.Import) else str(node.module)
            for node in top_level
            for alias in node.names
        }
        assert names <= {"typer", "pathlib", "Path"}, f"{module.__name__} imports {names}"


def test_answers_build_without_a_question_set_says_which_command_to_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["eval", "answers", "build"])
    assert result.exit_code == 2
    assert "brain eval questions merge" in result.output


def test_judge_sample_refuses_a_nonsense_share(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "eval").mkdir(parents=True)
    (tmp_path / "data" / "eval" / "questions.jsonl").write_text(
        '{"id": "q001"}\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["eval", "judge", "sample", "--n", "nope"])
    assert result.exit_code == 2
    assert "--n" in result.output
