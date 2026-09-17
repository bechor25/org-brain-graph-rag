"""Reading one analyst answer file: front matter, body, the `strategy:` line, latency.

The file layout is a contract between three parties that never meet — the analyst agent
writing it, this parser, and the planner reading the report. `README_TEMPLATE` is that
contract written down, so the test that it names every field the parser reads is the test
that stops the contract from drifting away from the code.
"""

from __future__ import annotations

from pathlib import Path

from brain.eval.answers import KNOWN_TOOLS, README_TEMPLATE, parse_answer, read_answers

FULL = """---
question_id: cq01
lang: en
started_at: 2026-09-17T10:00:00+00:00
finished_at: 2026-09-17T10:00:42+00:00
---

Two tests cover the issue [KAFKA-100]. Neither has ever been executed [chunk:ab12cd34ef56].

strategy: route, lookup, search_with_context
"""


def write(dir_path: Path, name: str, text: str) -> Path:
    path = dir_path / name
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------------- one whole file


def test_a_complete_answer_parses_into_front_matter_body_and_tools(tmp_path: Path) -> None:
    answer = parse_answer(write(tmp_path, "cq01.md", FULL))

    assert answer.qid == "cq01"
    assert answer.front["lang"] == "en"
    assert answer.front["started_at"] == "2026-09-17T10:00:00+00:00"
    assert answer.tools == ("route", "lookup", "search_with_context")
    assert "Two tests cover" in answer.body
    assert "question_id" not in answer.body
    assert "strategy:" not in answer.body
    assert answer.problems == []


def test_the_qid_comes_from_the_filename_and_a_mismatch_is_reported(tmp_path: Path) -> None:
    """The filename is what the report is keyed on; front matter that disagrees is a bug."""
    answer = parse_answer(write(tmp_path, "cq02.md", FULL))

    assert answer.qid == "cq02"
    assert any("question_id" in p for p in answer.problems)


# ---------------------------------------------------------------------------- strategy


def test_a_missing_strategy_line_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    answer = parse_answer(write(tmp_path, "cq03.md", FULL.replace("strategy: route, lookup,", "")))

    assert answer.strategy_line is None
    assert answer.tools == ()
    assert any("strategy" in p for p in answer.problems)


def test_a_strategy_line_written_as_prose_still_yields_its_tools(tmp_path: Path) -> None:
    text = FULL.replace(
        "strategy: route, lookup, search_with_context",
        "strategy: called route() first, then local_search and finally run_cypher",
    )
    assert parse_answer(write(tmp_path, "cq04.md", text)).tools == (
        "route",
        "local_search",
        "run_cypher",
    )


def test_a_strategy_line_that_names_nothing_known_says_so(tmp_path: Path) -> None:
    text = FULL.replace("route, lookup, search_with_context", "thought about it")
    answer = parse_answer(write(tmp_path, "cq05.md", text))

    assert answer.strategy_line is not None
    assert answer.tools == ()
    assert any("no known tool" in p for p in answer.problems)


def test_strategy_ids_are_collected_apart_from_tool_names(tmp_path: Path) -> None:
    text = FULL.replace("route, lookup, search_with_context", "s3 via local_search")
    answer = parse_answer(write(tmp_path, "cq06.md", text))

    assert answer.tools == ("local_search",)
    assert answer.strategies == ("s3",)


def test_the_known_tool_list_is_the_mcp_servers_list(tmp_path: Path) -> None:
    """Two hard-coded copies of the tool list is one copy too many; this is the seam."""
    from brain.mcp.server import TOOL_NAMES

    assert set(KNOWN_TOOLS) == set(TOOL_NAMES)


# ----------------------------------------------------------------------------- latency


def test_an_explicit_latency_line_wins_and_says_where_it_came_from(tmp_path: Path) -> None:
    text = FULL.replace("strategy:", "latency: 12400ms\nstrategy:")
    answer = parse_answer(write(tmp_path, "cq07.md", text))

    assert (answer.latency_ms, answer.latency_source) == (12400, "line")
    assert "latency:" not in answer.body


def test_latency_is_read_in_seconds_too(tmp_path: Path) -> None:
    text = FULL.replace("strategy:", "latency: 4.2s\nstrategy:")
    assert parse_answer(write(tmp_path, "cq08.md", text)).latency_ms == 4200


def test_without_a_latency_line_the_timestamps_answer_instead(tmp_path: Path) -> None:
    answer = parse_answer(write(tmp_path, "cq09.md", FULL))

    assert (answer.latency_ms, answer.latency_source) == (42000, "timestamps")


def test_with_neither_a_line_nor_timestamps_latency_is_unknown(tmp_path: Path) -> None:
    text = "\n".join(ln for ln in FULL.splitlines() if not ln.startswith(("started", "finished")))
    answer = parse_answer(write(tmp_path, "cq10.md", text))

    assert (answer.latency_ms, answer.latency_source) == (None, None)


# ------------------------------------------------------------------------------ language


def test_the_language_is_measured_from_the_body_not_only_declared(tmp_path: Path) -> None:
    """A Hebrew answer declared `en` is a real failure mode, so both values are kept."""
    text = FULL.replace("lang: en", "lang: en").replace(
        "Two tests cover the issue", "שני טסטים מכסים את הנושא"
    )
    answer = parse_answer(write(tmp_path, "cq11.md", text))

    assert answer.front["lang"] == "en"
    assert answer.detected_lang == "he"


def test_an_english_body_detects_as_english(tmp_path: Path) -> None:
    assert parse_answer(write(tmp_path, "cq12.md", FULL)).detected_lang == "en"


# -------------------------------------------------------------------------- the directory


def test_the_directory_reader_keys_by_qid_and_ignores_the_readme(tmp_path: Path) -> None:
    write(tmp_path, "cq01.md", FULL)
    write(tmp_path, "cq02.md", FULL.replace("cq01", "cq02"))
    write(tmp_path, "README.md", README_TEMPLATE)
    write(tmp_path, "notes.txt", "ignored")

    answers = read_answers(tmp_path)

    assert sorted(answers) == ["cq01", "cq02"]
    assert answers["cq01"].modified_at.endswith("+00:00")


def test_a_missing_directory_reads_as_no_answers_rather_than_an_error(tmp_path: Path) -> None:
    assert read_answers(tmp_path / "nope") == {}


def test_the_template_documents_every_field_the_parser_reads() -> None:
    for field in ("question_id", "lang", "started_at", "finished_at", "strategy:"):
        assert field in README_TEMPLATE
