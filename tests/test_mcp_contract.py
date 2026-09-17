"""The MCP surface as a contract: fifteen tools, two resources, one prompt, no logic.

None of this needs a database. That is the point — the adapter is supposed to be thin
enough that everything worth asserting about it can be asserted without one, and anything
here that *did* need Neo4j would be a retrieval rule that leaked out of `brain/retrieve/`
and into the server (plan decision 1).

The wiring files are tested here too (`.mcp.json`, `brain-analyst.md`). A tool renamed on
one side of the contract and not the other is exactly the failure a session notices as
"the agent has no tools", hours later, with no error anywhere.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest

from brain import retrieve
from brain.mcp import server as srv
from brain.retrieve import log as retrieve_log
from brain.retrieve.types import Item, Provenance, Result, RetrieveError

MCP_JSON = Path(".mcp.json")
ANALYST = Path(".claude/agents/brain-analyst.md")

#: Spec §4.3's table, retyped by hand. If this and `TOOL_NAMES` are edited together by
#: accident the test is worthless — so it is written from the spec, not imported from it.
SPEC_4_3 = {
    "search_chunks",
    "search_with_context",
    "lookup",
    "local_search",
    "get_schema",
    "cypher_examples",
    "run_cypher",
    "global_search",
    "status_at",
    "timeline",
    "changes_between",
    "assignees_over_time",
    "impact",
    "explain_edge",
    "route",
}

#: The twelve tools whose implementation is a function of the same name in the library.
#: The S4 trio lives in `brain/retrieve/{schema,examples,cypher_guard}.py` and is imported
#: inside the call, so it is checked separately.
LIBRARY_TOOLS = SPEC_4_3 - {"get_schema", "cypher_examples", "run_cypher"}


def listed_tools() -> list[Any]:
    return anyio.run(srv.mcp.list_tools)


# ------------------------------------------------------------------------ the tool table


def test_the_server_lists_exactly_the_fifteen_tools_of_spec_4_3() -> None:
    names = {t.name for t in listed_tools()}
    assert names == SPEC_4_3
    assert len(names) == 15


def test_tool_names_constant_matches_what_the_server_registers() -> None:
    """`TOOL_NAMES` is what the report and the analyst's tool list are generated from."""
    assert set(srv.TOOL_NAMES) == SPEC_4_3
    assert len(srv.TOOL_NAMES) == len(set(srv.TOOL_NAMES)) == 15


def test_every_tool_is_the_library_function_of_the_same_name() -> None:
    """One-to-one, by name. A tool called `find_stuff` would be a second vocabulary."""
    for name in LIBRARY_TOOLS:
        assert callable(getattr(retrieve, name, None)), f"{name} is not in brain.retrieve"


def test_the_s4_trio_resolves_to_the_task_2_modules() -> None:
    """Imported lazily by the server, so it is asserted lazily here too.

    `importorskip` and not a plain import: the S4 modules are Task 2's, and a server that
    cannot start until a sibling task lands is a server nobody can test. The skip is the
    same statement the tool makes at runtime — `{error, hint}` naming Task 2.
    """
    guard = pytest.importorskip("brain.retrieve.cypher_guard")
    examples = pytest.importorskip("brain.retrieve.examples")
    schema = pytest.importorskip("brain.retrieve.schema")
    assert callable(guard.run_cypher)
    assert callable(examples.cypher_examples)
    assert callable(schema.get_schema)


def test_every_tool_is_async_so_one_session_cannot_block_another() -> None:
    for name in srv.TOOL_NAMES:
        fn = getattr(srv, name)
        assert inspect.iscoroutinefunction(fn), f"{name} would block the event loop"


def test_every_tool_describes_itself_well_enough_to_be_chosen() -> None:
    """The description *is* the agentic router (spec §4.2 layer 2)."""
    descriptions = {}
    for tool in listed_tools():
        assert tool.description and len(tool.description) > 60, tool.name
        descriptions[tool.name] = tool.description.strip()
    # Two tools that read the same are two tools the agent picks between by coin flip.
    assert len(set(descriptions.values())) == 15


def test_the_instructions_name_every_strategy_family() -> None:
    text = srv.INSTRUCTIONS
    for hint in ("lookup", "local_search", "global_search", "run_cypher", "route"):
        assert hint in text


# ------------------------------------------------------------------ resources and prompt


def test_two_resources_and_one_prompt() -> None:
    resources = {str(r.uri) for r in anyio.run(srv.mcp.list_resources)}
    prompts = {p.name for p in anyio.run(srv.mcp.list_prompts)}
    assert resources == set(srv.RESOURCE_URIS) == {"brain://schema", "brain://stats"}
    assert prompts == set(srv.PROMPT_NAMES) == {"answer_with_citations"}


def test_the_prompt_demands_a_citation_and_forbids_prior_knowledge() -> None:
    text = srv.answer_with_citations("why was KIP-848 chosen?")
    assert "why was KIP-848 chosen?" in text
    assert "chunk:" in text and "provenance" in text
    assert "language of the question" in text
    assert "prior" in text and "knowledge" in text
    assert "strategy:" in text


# ------------------------------------------------------------------------- no logic here


def test_the_adapter_contains_no_cypher() -> None:
    """Plan decision 1: retrieval logic that lives only in the server cannot be evaluated."""
    source = Path(srv.__file__).read_text(encoding="utf-8")
    for verb in ("MATCH (", "RETURN ", "MERGE (", "db.index.vector"):
        assert verb not in source, f"{verb!r} in the MCP layer belongs in brain/retrieve/"


# --------------------------------------------------------------- failures are still answers


class Boom(RuntimeError):
    pass


def test_a_failure_comes_back_as_an_envelope_not_a_protocol_fault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(retrieve_log, "DEFAULT_LOG", tmp_path / "retrieval.jsonl")
    monkeypatch.setattr(srv, "context", lambda: (_ for _ in ()).throw(Boom("no driver")))

    payload = anyio.run(lambda: srv.lookup("KAFKA-1"))
    result = Result.model_validate(payload)
    assert len(result.items) == 1
    props = result.items[0].props
    assert result.items[0].kind == "Row"
    assert "Boom: no driver" in props["error"]
    assert "hint" in props


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RetrieveError("no such key"), "lookup"),
        (ValueError("mode must be one of …"), "allowed set"),
    ],
)
def test_a_known_failure_carries_a_hint_about_what_to_try_next(
    exc: Exception, expected: str
) -> None:
    result = srv.error_result("s1", "q", exc)
    assert expected in result.items[0].props["hint"]


def test_a_guard_refusal_describes_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`GuardError.as_dict()` (Task 2) wins over the adapter's generic wording."""
    monkeypatch.setattr(retrieve_log, "DEFAULT_LOG", tmp_path / "retrieval.jsonl")

    class Refusal(Exception):
        def as_dict(self) -> dict[str, str]:
            return {"error": "write verb CREATE", "hint": "read-only", "reason": "deny_list"}

    result = srv.error_result("s4", "CREATE (n)", Refusal())
    props = result.items[0].props
    assert props == {"error": "write verb CREATE", "hint": "read-only", "reason": "deny_list"}
    assert result.route and result.route["error"] == "write verb CREATE"


# ----------------------------------------------------------------------- over the wire


def test_the_envelope_survives_a_value_json_cannot_serialise() -> None:
    """A `neo4j.time.DateTime` that slipped past a projection must not break an answer."""
    result = Result(
        strategy="s1",
        items=[Item(kind="Chunk", key="c1", props={"at": datetime(2026, 9, 7, tzinfo=UTC)})],
    )
    payload = srv.jsonable(result)
    assert isinstance(payload["items"][0]["props"]["at"], str)
    json.dumps(payload)  # the wire does exactly this


def test_every_mcp_call_appends_one_trace_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plan decision 3: `question, strategy, cypher, latency_ms, hit_ids, tokens_out`."""
    log_path = tmp_path / "retrieval.jsonl"
    monkeypatch.setattr(retrieve_log, "DEFAULT_LOG", log_path)
    monkeypatch.setattr(srv, "context", lambda: object())

    def fake_local_search(_ctx: Any, query: str, **kwargs: Any) -> Result:
        from brain.retrieve.envelope import Timer, finish

        item = Item(
            kind="Entity",
            key="Decision|x",
            snippet="…",
            provenance=[Provenance(chunk_id="abc123")],
        )
        return finish("s3", [item], question=query, timer=Timer(), mode=kwargs["log_mode"])

    monkeypatch.setattr(retrieve, "local_search", fake_local_search)
    payload = anyio.run(lambda: srv.local_search("why?"))

    assert payload["items"][0]["provenance"][0]["chunk_id"] == "abc123"
    lines = retrieve_log.read_log(log_path)
    assert len(lines) == 1
    record = lines[0]
    assert record["mode"] == "mcp"
    assert record["question"] == "why?"
    assert record["strategy"] == "s3"
    assert record["hit_ids"] == ["Entity:Decision|x"]
    assert record["tokens_out"] > 0
    assert set(record) >= {"ts", "question", "strategy", "cypher", "latency_ms", "hit_ids"}


# ------------------------------------------------------------------------- the wiring


def test_mcp_json_launches_the_stdio_server() -> None:
    config = json.loads(MCP_JSON.read_text(encoding="utf-8"))
    assert set(config["mcpServers"]) == {"brain"}
    brain = config["mcpServers"]["brain"]
    assert [brain["command"], *brain["args"]] == ["uv", "run", "brain", "serve", "--stdio"]


def test_the_analyst_is_wired_to_every_tool_and_nothing_else() -> None:
    line = next(
        raw for raw in ANALYST.read_text(encoding="utf-8").splitlines() if raw.startswith("tools:")
    )
    tools = [t.strip() for t in line.split(":", 1)[1].split(",")]
    assert set(tools) == {f"mcp__brain__{name}" for name in srv.TOOL_NAMES} | {"Read"}
    assert len(tools) == 16
