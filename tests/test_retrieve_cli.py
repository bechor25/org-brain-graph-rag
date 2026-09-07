"""`brain ask` and `brain competency` are registered and documented. No database."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from brain.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_ask_is_registered_with_the_documented_flags(runner) -> None:
    result = runner.invoke(app, ["ask", "--help"])
    assert result.exit_code == 0
    for flag in ("--strategy", "--k", "--rerank", "--json", "--synthetic"):
        assert flag in result.output


def test_competency_is_registered(runner) -> None:
    result = runner.invoke(app, ["competency", "--help"])
    assert result.exit_code == 0
    assert "--rebuild" in result.output
    assert "--repeats" in result.output


def test_ask_appears_in_the_top_level_help(runner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ask" in result.output
