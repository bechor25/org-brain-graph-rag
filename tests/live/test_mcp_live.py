"""The server as Claude Code will actually meet it: a subprocess, a pipe, and JSON-RPC.

`tests/test_mcp_contract.py` asserts the contract against the Python objects, which proves
nothing about the transport — a tool can be registered and still fail to serialise, and a
`GuardError` raised over stdio is a protocol fault at the agent rather than an answer it can
read. So this module spawns `brain serve --stdio` exactly the way `.mcp.json` does, speaks
MCP to it, and asserts on what comes back over the wire.

One session, module-scoped, because starting the server costs seconds and every assertion
here is about the same session. The graph it reads is the loaded one (the label prefix is a
constructor argument, not an environment variable, so a subprocess cannot be pointed at the
`_Retr` mini namespace) — anchors are therefore derived from the graph over the wire, and
the one hard-coded key, `KIP-848`, skips rather than fails if the corpus lacks it.

The HTTP half is the same protocol on the transport `docker compose` runs, plus `/healthz`,
which is what the compose healthcheck polls.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from brain.mcp.report import _payload, server_command
from brain.mcp.server import PROMPT_NAMES, RESOURCE_URIS, TOOL_NAMES
from brain.retrieve.types import Result

pytestmark = pytest.mark.live

COMPETENCY = Path("data/eval/competency.jsonl")
THEMATIC_QID = "cq12"
RATIONALE = "Why was the design in KIP-848 chosen?"
WRITE_ATTEMPT = "MATCH (n) DETACH DELETE n"


def competency_question(qid: str, fallback: str) -> str:
    if not COMPETENCY.exists():
        return fallback
    for line in COMPETENCY.read_text(encoding="utf-8").splitlines():
        if line.strip() and json.loads(line).get("id") == qid:
            return str(json.loads(line)["question"])
    return fallback


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One tool call, with the round trip the agent waits for alongside the retrieval's own."""
    started = time.perf_counter()
    payload = _payload(await session.call_tool(name, arguments))
    payload["_round_trip_ms"] = round((time.perf_counter() - started) * 1000)
    return payload


async def _collect() -> dict[str, Any]:
    command, *args = server_command()
    params = StdioServerParameters(command=command, args=args)
    out: dict[str, Any] = {}
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        out["server_name"] = init.serverInfo.name
        out["instructions"] = init.instructions or ""
        out["tools"] = sorted(t.name for t in (await session.list_tools()).tools)
        out["resources"] = sorted(str(r.uri) for r in (await session.list_resources()).resources)
        out["prompts"] = sorted(p.name for p in (await session.list_prompts()).prompts)

        out["lookup"] = await _call(session, "lookup", {"key": "KIP-848"})
        out["local_search"] = await _call(session, "local_search", {"query": RATIONALE, "k": 5})
        out["global_search"] = await _call(
            session,
            "global_search",
            {"query": competency_question(THEMATIC_QID, "main themes of open bugs"), "k": 5},
        )
        out["refusal"] = await _call(session, "run_cypher", {"cypher": WRITE_ATTEMPT})

        # An answer built to overflow: the busiest component's impact, which is the widest
        # traversal any tool does. The anchor is read off the graph through the guard, so
        # this stays true when the corpus is rebuilt.
        busiest = await _call(
            session,
            "run_cypher",
            {
                "cypher": "MATCH (w:WorkItem)-[:IN_COMPONENT]->(c:Component) "
                "RETURN c.name AS name, count(w) AS n ORDER BY n DESC, name LIMIT 1"
            },
        )
        items = busiest.get("items") or []
        component = str(items[0]["props"]["name"]) if items else "clients"
        out["component"] = component
        out["impact"] = await _call(session, "impact", {"key_or_name": component, "depth": 2})

        stats = await session.read_resource(RESOURCE_URIS[1])
        out["stats"] = json.loads(stats.contents[0].text)
        rendered = await session.get_prompt(PROMPT_NAMES[0], {"question": RATIONALE})
        out["prompt_text"] = "\n".join(m.content.text for m in rendered.messages)
    return out


@pytest.fixture(scope="module")
def wire() -> dict[str, Any]:
    return anyio.run(_collect)


def envelope(payload: dict[str, Any]) -> Result:
    """Every answer must parse as the one envelope of spec §4.4, whatever it says."""
    return Result.model_validate({k: v for k, v in payload.items() if not k.startswith("_")})


# ------------------------------------------------------------------------------ the list


def test_tools_list_over_stdio_is_the_fifteen_of_spec_4_3(wire: dict[str, Any]) -> None:
    assert wire["server_name"] == "brain"
    assert wire["tools"] == sorted(TOOL_NAMES)
    assert len(wire["tools"]) == 15


def test_the_resources_and_the_prompt_are_served_too(wire: dict[str, Any]) -> None:
    assert wire["resources"] == sorted(RESOURCE_URIS)
    assert wire["prompts"] == sorted(PROMPT_NAMES)
    assert "chunk_id" in wire["prompt_text"]


def test_the_client_is_told_how_to_choose_a_tool_before_it_calls_one(
    wire: dict[str, Any],
) -> None:
    assert "global_search" in wire["instructions"]


# -------------------------------------------------------------------------- round trips


def test_lookup_finds_a_named_key_with_its_neighbourhood(wire: dict[str, Any]) -> None:
    result = envelope(wire["lookup"])
    if not result.items:
        pytest.skip("this graph holds no KIP-848 — load the corpus to run this assertion")
    document = next(i for i in result.items if i.key == "KIP-848")
    assert document.kind == "Document"
    assert document.props["neighbor_count"] > 0
    assert any(p.chunk_id for i in result.items for p in i.provenance)
    assert result.latency_ms >= 0


def test_local_search_answers_a_rationale_question_with_quotes(wire: dict[str, Any]) -> None:
    result = envelope(wire["local_search"])
    assert result.strategy == "s3"
    assert result.items
    assert any(p.chunk_id for i in result.items for p in i.provenance)


def test_global_search_returns_community_reports_with_evidence(wire: dict[str, Any]) -> None:
    """The gate's thematic question (cq12) — S5's reason to exist."""
    result = envelope(wire["global_search"])
    assert result.strategy == "s5"
    assert result.items
    assert all(i.kind == "Community" for i in result.items)
    assert sum(1 for i in result.items if any(p.chunk_id for p in i.provenance)) >= 1
    assert all(i.title for i in result.items)
    # Decision: one member set, one slot — never the same theme twice at two Leiden levels.
    assert len({str(i.props.get("member_hash") or i.key) for i in result.items}) == len(
        result.items
    )


def test_a_write_is_refused_as_an_answer_and_not_as_a_protocol_fault(
    wire: dict[str, Any],
) -> None:
    """The guard's refusal has to survive the wire, or the agent sees a broken tool."""
    result = envelope(wire["refusal"])
    props = result.items[0].props
    assert props["error"]
    assert props["hint"]


def test_the_stats_resource_says_how_much_of_the_corpus_is_loaded(wire: dict[str, Any]) -> None:
    stats = wire["stats"]
    assert "error" not in stats
    assert stats["embedding"]["model"]
    assert stats["graph"]


# ------------------------------------------------------------------------- the ceiling


def test_an_oversized_answer_is_truncated_and_keeps_one_item_per_kind(
    wire: dict[str, Any],
) -> None:
    """Spec §4.4: trim by score, but never drop a whole kind out of the answer."""
    result = envelope(wire["impact"])
    if not result.truncated:
        pytest.skip(f"impact on {wire['component']!r} fits the budget on this corpus")
    kinds = [i.kind for i in result.items]
    assert len(set(kinds)) > 1
    assert len(kinds) == len(result.items)


# ----------------------------------------------------------------------------- over HTTP


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _over_http(url: str) -> list[str]:
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return sorted(t.name for t in (await session.list_tools()).tools)


def test_the_same_server_answers_on_http_at_slash_mcp() -> None:
    """The transport compose runs. `/healthz` is what its healthcheck polls."""
    import urllib.error
    import urllib.request

    port = free_port()
    # The same launcher `.mcp.json` uses, with the other transport.
    command = [*server_command()[:-1], "--http", "--port", str(port)]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        health: dict[str, Any] = {}
        for _ in range(100):
            try:
                with urllib.request.urlopen(  # noqa: S310 - literal http://127.0.0.1
                    f"http://127.0.0.1:{port}/healthz", timeout=2
                ) as response:
                    health = json.loads(response.read())
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.2)
        assert health.get("status") == "ok", "the HTTP server never came up"
        assert health["tools"] == 15
        assert anyio.run(_over_http, f"http://127.0.0.1:{port}/mcp") == sorted(TOOL_NAMES)
    finally:
        process.terminate()
        process.wait(timeout=20)
