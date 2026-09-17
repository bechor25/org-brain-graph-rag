"""`brain serve --check`: the parts of the step report that can be wrong without a database.

The measuring is live (`tests/live/test_mcp_live.py` speaks the same protocol to the same
server). What is tested here is the bookkeeping around it — that merging the `mcp` and
`global` sections does not eat Task 1's report, that a check which should fail does fail,
and that the envelope is read out of whichever shape this SDK version sent it in.

A check that cannot fail is the most expensive kind of green.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.mcp import report as rep
from brain.mcp.server import PROMPT_NAMES, RESOURCE_URIS, TOOL_NAMES
from brain.retrieve.pack import BUDGET_TOKENS


def section(**overrides: Any) -> dict[str, Any]:
    """An `mcp` section where every check passes, so a test can break exactly one thing."""
    base: dict[str, Any] = {
        "tools_listed": sorted(TOOL_NAMES),
        "resources_listed": sorted(RESOURCE_URIS),
        "prompts_listed": sorted(PROMPT_NAMES),
        "tools": [
            {"tool": name, "valid_envelope": True, "error": None} for name in sorted(TOOL_NAMES)
        ],
        "truncation": {
            "truncated": True,
            "kinds_kept": ["Chunk", "WorkItem"],
            "tokens_out": 3900,
        },
    }
    base.update(overrides)
    return base


def named(checks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(c for c in checks if c["name"] == name)


# ------------------------------------------------------------------------------ checks


def test_a_healthy_server_passes_every_check() -> None:
    assert all(c["ok"] for c in rep.mcp_checks(section()))


def test_a_missing_tool_fails_the_contract_check_and_is_named() -> None:
    listed = sorted(set(TOOL_NAMES) - {"explain_edge"})
    check = named(rep.mcp_checks(section(tools_listed=listed)), "tools_list_matches_spec_4_3")
    assert not check["ok"]
    assert "explain_edge" in check["detail"]


def test_an_extra_tool_fails_too() -> None:
    listed = sorted([*TOOL_NAMES, "delete_everything"])
    check = named(rep.mcp_checks(section(tools_listed=listed)), "tools_list_matches_spec_4_3")
    assert not check["ok"]
    assert "delete_everything" in check["detail"]


def test_a_missing_resource_or_prompt_fails() -> None:
    checks = rep.mcp_checks(section(resources_listed=["brain://schema"]))
    assert not named(checks, "two_resources_and_one_prompt")["ok"]


def test_an_unparseable_envelope_fails_and_names_the_tool() -> None:
    tools = [{"tool": n, "valid_envelope": n != "impact", "error": None} for n in TOOL_NAMES]
    checks = rep.mcp_checks(section(tools=tools))
    check = named(checks, "every_tool_returns_a_valid_result_envelope")
    assert not check["ok"]
    assert check["detail"] == "impact"


def test_a_tool_that_answered_with_an_error_fails_its_own_check() -> None:
    tools = [
        {"tool": n, "valid_envelope": True, "error": "boom" if n == "run_cypher" else None}
        for n in TOOL_NAMES
    ]
    checks = rep.mcp_checks(section(tools=tools))
    assert named(checks, "every_tool_returns_a_valid_result_envelope")["ok"]
    assert not named(checks, "no_tool_answers_with_an_error")["ok"]


def test_truncation_that_kept_only_one_kind_is_not_a_pass() -> None:
    """Spec §4.4: the ceiling may drop items, never a whole kind."""
    truncation = {"truncated": True, "kinds_kept": ["Chunk"], "tokens_out": 3900}
    checks = rep.mcp_checks(section(truncation=truncation))
    assert not named(checks, "truncation_keeps_at_least_one_item_per_kind")["ok"]


def test_an_answer_that_never_hit_the_ceiling_does_not_prove_truncation() -> None:
    truncation = {"truncated": False, "kinds_kept": ["Chunk", "WorkItem"], "tokens_out": 100}
    checks = rep.mcp_checks(section(truncation=truncation))
    assert not named(checks, "truncation_keeps_at_least_one_item_per_kind")["ok"]


# ------------------------------------------------------------------------------- merge


def test_merge_adds_sections_without_losing_the_ones_task_1_wrote(tmp_path: Path) -> None:
    target = tmp_path / "retrieve.json"
    target.write_text(json.dumps({"step": "retrieve", "questions": [1, 2]}), encoding="utf-8")

    rep.merge({"global": {"question_id": "cq12"}, "mcp": {"transport": "stdio"}}, target)

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["questions"] == [1, 2]
    assert written["step"] == "retrieve"
    assert written["global"]["question_id"] == "cq12"
    assert written["mcp"]["transport"] == "stdio"


def test_merge_overwrites_its_own_sections_on_a_second_run(tmp_path: Path) -> None:
    target = tmp_path / "retrieve.json"
    rep.merge({"mcp": {"tools": ["old"]}}, target)
    rep.merge({"mcp": {"tools": ["new"]}}, target)
    assert json.loads(target.read_text(encoding="utf-8"))["mcp"]["tools"] == ["new"]


def test_merge_survives_a_corrupt_report_instead_of_refusing_to_write(tmp_path: Path) -> None:
    target = tmp_path / "retrieve.json"
    target.write_text("{not json", encoding="utf-8")
    rep.merge({"mcp": {"ok": True}}, target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["mcp"]["ok"] is True
    assert set(written) == {"mcp", "sections"}, "nothing is invented to replace what was lost"
    assert written["mcp"] == {"ok": True}


def test_merge_stamps_its_sections_and_leaves_the_others_labelled(tmp_path: Path) -> None:
    """The freshness rule lives in `brain/retrieve/report.py`; this is the wiring to it."""
    target = tmp_path / "retrieve.json"
    target.write_text(json.dumps({"questions": [1]}), encoding="utf-8")
    rep.merge({"mcp": {"transport": "stdio"}}, target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["sections"]["mcp"]["stale"] is False
    assert written["sections"]["questions"]["stale"] is True


def test_merge_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    target = tmp_path / "retrieve.json"
    rep.merge({"mcp": {}}, target)
    assert [p.name for p in tmp_path.iterdir()] == ["retrieve.json"]


# --------------------------------------------------------------------------- the reading


class Block:
    def __init__(self, text: str) -> None:
        self.text = text


class Call:
    def __init__(self, structured: Any = None, content: list[Block] | None = None) -> None:
        self.structuredContent = structured  # noqa: N815 - the SDK's attribute name
        self.content = content or []


def test_the_envelope_is_read_from_structured_content() -> None:
    assert rep._payload(Call(structured={"result": {"strategy": "s1"}})) == {"strategy": "s1"}


def test_a_structured_payload_without_the_result_wrapper_is_taken_as_is() -> None:
    assert rep._payload(Call(structured={"strategy": "s3"})) == {"strategy": "s3"}


def test_the_envelope_falls_back_to_the_text_block() -> None:
    call = Call(content=[Block("not json"), Block(json.dumps({"strategy": "s5"}))])
    assert rep._payload(call) == {"strategy": "s5"}


def test_nothing_readable_is_an_empty_envelope_that_fails_the_parse_check() -> None:
    assert rep._payload(Call()) == {}
    assert rep._parses({}) is False
    assert rep._parses({"strategy": "s1", "items": [], "latency_ms": 3}) is True


# ------------------------------------------------------------------------ the arguments


def test_the_version_window_is_two_adjacent_release_families() -> None:
    assert rep._version_pair(["3.7.0", "3.7.1", "3.8.0", "4.0.0"]) == ("3.7", "3.8")


def test_an_unusable_version_list_falls_back_instead_of_raising() -> None:
    assert rep._version_pair([]) == ("3.7", "3.8")
    assert rep._version_pair(["nightly", "future"]) == ("3.7", "3.8")


def test_the_truncation_row_reports_the_kinds_that_survived() -> None:
    envelope = {
        "truncated": True,
        "items": [
            {"kind": "Chunk", "key": "c1", "snippet": "x" * 100},
            {"kind": "WorkItem", "key": "KAFKA-1"},
        ],
    }
    row = rep._truncation_row(envelope)
    assert row["kinds_kept"] == ["Chunk", "WorkItem"]
    assert row["items"] == 2
    assert row["tokens_out"] > 0
    assert row["budget_tokens"] == BUDGET_TOKENS


def test_the_server_is_launched_the_same_way_mcp_json_launches_it() -> None:
    command = rep.server_command()
    assert command[-2:] == ["serve", "--stdio"]
    assert command[0].endswith("brain") or command[:3] == ["uv", "run", "brain"]
