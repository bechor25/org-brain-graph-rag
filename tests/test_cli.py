from brain.cli import NOT_IMPLEMENTED_EXIT, app

PIPELINE = [
    "harvest",
    "canon",
    "load",
    "chunk",
    "extract",
    "resolve",
    "communities",
    "index",
    "serve",
    "eval",
]


def test_help_lists_every_pipeline_step(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for step in PIPELINE + ["doctor", "version"]:
        assert step in result.output


def test_unimplemented_step_exits_2_and_names_plan(runner):
    result = runner.invoke(app, ["load"])
    assert result.exit_code == NOT_IMPLEMENTED_EXIT
    assert "Plan 1" in result.output


def test_version(runner):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"
