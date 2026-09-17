"""`brain eval questions` is wired, documented, and cheap to ask for help from."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from brain.cli import app
from brain.eval import cli_questions
from brain.eval import questions as Q


def test_the_sub_app_is_mounted_under_brain_eval(runner: CliRunner):
    result = runner.invoke(app, ["eval", "questions", "--help"])
    assert result.exit_code == 0
    assert "build" in result.stdout
    assert "merge" in result.stdout


@pytest.mark.parametrize("command", ["build", "merge"])
def test_both_commands_document_themselves(runner: CliRunner, command: str):
    """Asserted on the docstring, not the rendered help: Rich rewraps at the terminal width."""
    result = runner.invoke(app, ["eval", "questions", command, "--help"])
    assert result.exit_code == 0
    doc = getattr(cli_questions, command).__doc__ or ""
    assert "[Plan 3]" in doc


def test_the_cli_defaults_are_the_module_defaults():
    """The CLI repeats them to keep `--help` free of the retrieval stack; they must agree."""
    assert cli_questions.DEFAULT_NEW_TOTAL == Q.DEFAULT_NEW_TOTAL
    assert cli_questions.DEFAULT_SHARDS == Q.DEFAULT_SHARDS
    assert cli_questions.DEFAULT_HEBREW_MIN == Q.DEFAULT_HEBREW_MIN
    assert cli_questions.DEFAULT_SLACK == Q.DEFAULT_SLACK
    assert cli_questions.DEFAULT_SPARES == Q.DEFAULT_SPARES


def test_the_seed_default_is_the_one_the_sampler_uses():
    from brain.eval import paths as paths_mod

    assert cli_questions.DEFAULT_SEED == paths_mod.DEFAULT_SEED


def test_asking_for_help_never_opens_a_database_or_an_embedder(runner: CliRunner):
    """The autouse guard in conftest raises on a real driver; reaching exit 0 is the proof."""
    assert runner.invoke(app, ["eval", "questions", "build", "--help"]).exit_code == 0


def test_a_missing_competency_file_is_a_usage_error_naming_the_command_that_writes_it(
    runner: CliRunner, tmp_path
):
    result = runner.invoke(
        app,
        ["eval", "questions", "build", "--competency", str(tmp_path / "nope.jsonl")],
    )
    assert result.exit_code != 0
    assert "competency" in result.output
