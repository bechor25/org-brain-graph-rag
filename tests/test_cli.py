from brain.cli import NOT_IMPLEMENTED_EXIT, app

PIPELINE = [
    "harvest",
    "canon",
    "synth",
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
    result = runner.invoke(app, ["resolve"])
    assert result.exit_code == NOT_IMPLEMENTED_EXIT
    assert "Plan 1" in result.output


def test_extract_is_a_group_of_three_commands(runner):
    """`extract` stopped being a stub in step 07; it is build / merge / sample now."""
    result = runner.invoke(app, ["extract", "--help"])
    assert result.exit_code in (0, NOT_IMPLEMENTED_EXIT)
    for command in ("build", "merge", "sample"):
        assert command in result.output


def test_extract_build_rejects_a_missing_canonical_dir_before_touching_neo4j(runner, tmp_path):
    result = runner.invoke(app, ["extract", "build", "--canonical-dir", str(tmp_path / "nope")])

    assert result.exit_code == 2
    assert "--canonical-dir" in result.output


def test_chunk_rejects_an_unknown_kind_before_touching_neo4j_or_ollama(runner):
    """The guard runs first, so a typo never opens a driver session or pulls a model."""
    result = runner.invoke(app, ["chunk", "--kinds", "doc,issues"])

    assert result.exit_code == 2
    assert "--kinds" in result.output


def test_chunk_rejects_a_missing_canonical_dir_before_touching_neo4j(runner, tmp_path):
    result = runner.invoke(app, ["chunk", "--canonical-dir", str(tmp_path / "nope")])

    assert result.exit_code == 2
    assert "--canonical-dir" in result.output


def test_load_rejects_a_missing_canonical_dir_before_touching_neo4j(runner, tmp_path):
    """The guard runs first, so a typo'd path never opens a driver session."""
    result = runner.invoke(app, ["load", "--canonical-dir", str(tmp_path / "nope")])

    assert result.exit_code == 2
    assert "--canonical-dir" in result.output


def test_version(runner):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_canon_error_goes_to_stderr(runner, tmp_path, monkeypatch):
    """A refusal to write must not land in stdout, which a caller may be piping."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "canonical").mkdir(parents=True)
    (tmp_path / "canonical" / "workitems.jsonl").write_text("{not json\n", encoding="utf-8")
    (tmp_path / "raw" / "jira").mkdir(parents=True)
    (tmp_path / "raw" / "jira" / "issues-0000.json").write_text('{"issues": []}', encoding="utf-8")
    (tmp_path / "raw" / "jira" / "checkpoint.json").write_text(
        '{"source": "jira", "files": ["issues-0000.json"], "done": true}', encoding="utf-8"
    )

    result = runner.invoke(app, ["canon", "--source", "jira"])

    assert result.exit_code == 1
    assert "canon:" not in result.stdout
    assert "canon:" in result.stderr


def test_synth_error_goes_to_stderr(runner, tmp_path, monkeypatch):
    """`brain synth merge` with nothing to merge must not print onto a piped stdout."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    result = runner.invoke(app, ["synth", "merge"])

    assert result.exit_code == 1
    assert "synth merge:" not in result.stdout
    assert "no batches to merge" in result.stderr


def test_synth_build_needs_a_canonical_slice(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    result = runner.invoke(app, ["synth", "build"])

    assert result.exit_code == 1
    assert "synth build:" in result.stderr


def test_synth_help_names_both_subcommands(runner):
    result = runner.invoke(app, ["synth", "--help"])

    assert result.exit_code == 0
    assert "build" in result.output and "merge" in result.output
