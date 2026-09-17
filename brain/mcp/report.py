"""`brain serve --check`: talk to the server the way Claude Code will, and write it down.

Two things are being measured, and neither can be measured from inside the server.

**That the contract holds over the wire.** `tools/list` has to answer with the fifteen names
of spec §4.3, two resources and one prompt, and every tool has to come back with a `Result`
that parses. Asserting that against the Python functions proves nothing about the transport;
this spawns `brain serve --stdio` as a subprocess and speaks MCP to it, which is exactly what
`.mcp.json` does.

**What a tool costs the asking agent.** `latency_ms` inside the envelope is the retrieval;
the round trip is the retrieval plus JSON, plus a pipe, plus the client. The difference is
what an agent actually waits for, so both are recorded per tool.

The arguments are read off the graph and off `data/eval/competency.jsonl` rather than typed
here: a report whose numbers come from keys that happen to exist today is a report that
starts lying the first time the corpus is rebuilt.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from brain.config import get_settings
from brain.mcp.server import PROMPT_NAMES, RESOURCE_URIS, TOOL_NAMES
from brain.retrieve.context import RetrieveContext
from brain.retrieve.global_search import LEVELS, global_search
from brain.retrieve.pack import BUDGET_TOKENS, total_tokens
from brain.retrieve.types import Item, Result

REPORT_PATH = Path("data/reports/retrieve.json")
COMPETENCY_PATH = Path("data/eval/competency.jsonl")
#: Round trips per tool. Three, because the first one pays for a cold driver and a cold
#: Ollama model and a p50 over three is what the rest of this report already uses.
REPEATS = 3
#: The thematic competency question S5 exists for.
THEMATIC_QID = "cq12"


def server_command() -> list[str]:
    """How to launch the server as a subprocess — the console script, else `uv run`."""
    script = Path(sys.executable).parent / "brain"
    if script.exists():
        return [str(script), "serve", "--stdio"]
    return ["uv", "run", "brain", "serve", "--stdio"]


# ---------------------------------------------------------------------------- arguments


def _competency() -> dict[str, dict[str, Any]]:
    if not COMPETENCY_PATH.exists():
        return {}
    rows = [
        json.loads(line)
        for line in COMPETENCY_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {row["id"]: row for row in rows}


def _first(ctx: RetrieveContext, cypher: str) -> dict[str, Any]:
    rows = ctx.read(cypher)
    return rows[0] if rows else {}


def tool_arguments(ctx: RetrieveContext) -> dict[str, dict[str, Any]]:
    """One representative call per tool, with anchors the graph actually holds."""
    questions = _competency()
    thematic = questions.get(THEMATIC_QID, {}).get(
        "question", "What are the main themes of open bugs in clients?"
    )
    rationale = questions.get("cq09", {}).get("question", "Why was the design in KIP-848 chosen?")

    dated = _first(
        ctx,
        f"MATCH (w:{ctx.label('WorkItem')})-[:HAS_CHANGE]->"
        f"(s:{ctx.label('StatusChange')} {{`field`: 'status'}})\n"
        "WITH w, min(s.at) AS first, count(s) AS n WHERE n >= 2\n"
        "RETURN w.key AS key, toString(date(first)) AS at ORDER BY n DESC, w.key LIMIT 1",
    )
    assigned = _first(
        ctx,
        f"MATCH (w:{ctx.label('WorkItem')})-[r:ASSIGNED_TO]->(:{ctx.label('Person')})\n"
        "WITH w, count(r) AS n RETURN w.key AS key ORDER BY n DESC, w.key LIMIT 1",
    )
    component = _first(
        ctx,
        f"MATCH (w:{ctx.label('WorkItem')})-[:IN_COMPONENT]->(c:{ctx.label('Component')})\n"
        "RETURN c.name AS name, count(w) AS n ORDER BY n DESC, c.name LIMIT 1",
    )
    versions = ctx.read(
        f"MATCH (:{ctx.label('WorkItem')})-[:FIX_VERSION]->(v:{ctx.label('Version')})\n"
        "RETURN v.name AS name, count(*) AS n ORDER BY n DESC LIMIT 12"
    )
    edge = _first(
        ctx,
        f"MATCH (a:{ctx.label('Document')})-[r:DECIDES]->(b:{ctx.label('Entity')})\n"
        "RETURN a.key AS src, b.id AS dst ORDER BY a.key, b.id LIMIT 1",
    )
    document = _first(
        ctx, f"MATCH (d:{ctx.label('Document')}) RETURN d.key AS key ORDER BY d.key LIMIT 1"
    )
    v1, v2 = _version_pair([str(v["name"]) for v in versions if v.get("name")])

    return {
        "search_chunks": {"query": rationale, "k": 5},
        "search_with_context": {"query": rationale, "k": 5, "hops": 1},
        "lookup": {"key": document.get("key") or "KIP-848"},
        "local_search": {"query": rationale, "k": 5},
        "get_schema": {},
        "cypher_examples": {"question_type": "rationale"},
        "run_cypher": {
            "cypher": f"MATCH (d:{ctx.label('Document')}) RETURN d.key AS key ORDER BY key",
            "limit": 5,
        },
        "global_search": {"query": thematic, "level": "any", "k": 5},
        "status_at": {"key": dated.get("key") or "", "date": dated.get("at") or "2024-03-01"},
        "timeline": {"key": dated.get("key") or ""},
        "changes_between": {"component": component.get("name") or "clients", "v1": v1, "v2": v2},
        "assignees_over_time": {"key": assigned.get("key") or dated.get("key") or ""},
        "impact": {"key_or_name": component.get("name") or "clients", "depth": 2},
        "explain_edge": {"src": edge.get("src") or "", "dst": edge.get("dst") or ""},
        "route": {"question": rationale},
    }


def _version_pair(names: list[str]) -> tuple[str, str]:
    """Two adjacent release families, so `changes_between` has a window with work in it."""

    def key(name: str) -> tuple[int, ...]:
        parts = []
        for piece in name.split("."):
            if not piece.isdigit():
                break
            parts.append(int(piece))
        return tuple(parts) or (0,)

    families = sorted({key(n)[:2] for n in names if len(key(n)) >= 2})
    for low, high in zip(families, families[1:], strict=False):
        if high[0] == low[0] and high[1] == low[1] + 1:
            return ".".join(map(str, low)), ".".join(map(str, high))
    return "3.7", "3.8"


# ------------------------------------------------------------------------- the round trip


def _payload(call_result: Any) -> dict[str, Any]:
    """The tool's envelope, whichever way this SDK version chose to send it."""
    structured = getattr(call_result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured.get("result", structured)
    for block in getattr(call_result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    return {}


async def _measure(arguments: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Everything the report knows about the server, from one stdio session."""
    command, *args = server_command()
    params = StdioServerParameters(command=command, args=args)
    out: dict[str, Any] = {}
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        resources = await session.list_resources()
        prompts = await session.list_prompts()
        out["tools_listed"] = sorted(t.name for t in listed.tools)
        out["resources_listed"] = sorted(str(r.uri) for r in resources.resources)
        out["prompts_listed"] = sorted(p.name for p in prompts.prompts)

        per_tool: list[dict[str, Any]] = []
        for name in TOOL_NAMES:
            kwargs = arguments.get(name, {})
            round_trips: list[float] = []
            envelope: dict[str, Any] = {}
            error: str | None = None
            for _ in range(REPEATS):
                started = time.perf_counter()
                try:
                    call = await session.call_tool(name, kwargs)
                except Exception as exc:  # noqa: BLE001 - a broken tool is a report line
                    error = f"{type(exc).__name__}: {exc}"
                    break
                round_trips.append((time.perf_counter() - started) * 1000)
                envelope = _payload(call)
            per_tool.append(_tool_row(name, kwargs, round_trips, envelope, error))
        out["tools"] = per_tool

        out["truncation"] = _truncation_row(
            _payload(await session.call_tool("impact", {"key_or_name": _impact_key(arguments)}))
        )
        read_schema = await session.read_resource(RESOURCE_URIS[0])
        read_stats = await session.read_resource(RESOURCE_URIS[1])
        out["resources"] = {
            RESOURCE_URIS[0]: _resource_summary(read_schema),
            RESOURCE_URIS[1]: _resource_summary(read_stats),
        }
        rendered = await session.get_prompt(PROMPT_NAMES[0], {"question": "why?"})
        out["prompt"] = {
            "name": PROMPT_NAMES[0],
            "messages": len(rendered.messages),
            "chars": sum(len(getattr(m.content, "text", "") or "") for m in rendered.messages),
        }
    return out


def _impact_key(arguments: dict[str, dict[str, Any]]) -> str:
    return str(arguments.get("impact", {}).get("key_or_name") or "clients")


def _tool_row(
    name: str,
    kwargs: dict[str, Any],
    round_trips: list[float],
    envelope: dict[str, Any],
    error: str | None,
) -> dict[str, Any]:
    items = envelope.get("items") or []
    first_error = next(
        (
            i["props"]["error"]
            for i in items
            if isinstance(i.get("props"), dict) and "error" in i["props"]
        ),
        None,
    )
    return {
        "tool": name,
        "arguments": {k: _short(v) for k, v in kwargs.items()},
        "round_trip_ms": {
            "p50": round(statistics.median(round_trips)) if round_trips else None,
            "min": round(min(round_trips)) if round_trips else None,
            "max": round(max(round_trips)) if round_trips else None,
            "samples": len(round_trips),
        },
        "latency_ms": envelope.get("latency_ms"),
        "items": len(items),
        "kinds": sorted({str(i.get("kind")) for i in items}),
        "with_provenance": sum(1 for i in items if i.get("provenance")),
        "truncated": envelope.get("truncated"),
        "cypher_used": len(envelope.get("cypher_used") or []),
        "valid_envelope": _parses(envelope),
        "error": error or first_error,
    }


def _short(value: Any, limit: int = 90) -> Any:
    text = str(value)
    return value if len(text) <= limit else text[: limit - 1] + "…"


def _parses(envelope: dict[str, Any]) -> bool:
    if not envelope:
        return False
    try:
        Result.model_validate(envelope)
    except Exception:  # noqa: BLE001 - the answer to "did it parse" is False, not a crash
        return False
    return True


def _resource_summary(read: Any) -> dict[str, Any]:
    text = ""
    for block in getattr(read, "contents", []) or []:
        text = getattr(block, "text", "") or ""
        break
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    return {
        "chars": len(text),
        "keys": sorted(payload)[:12] if isinstance(payload, dict) else [],
        "error": payload.get("error") if isinstance(payload, dict) else None,
    }


def _truncation_row(envelope: dict[str, Any]) -> dict[str, Any]:
    """One real answer past the ceiling, plus the packer's own guarantee, spelled out."""
    items = [Item.model_validate(i) for i in envelope.get("items") or []]
    return {
        "tool": "impact",
        "truncated": envelope.get("truncated"),
        "items": len(items),
        "kinds_kept": sorted({i.kind for i in items}),
        "tokens_out": total_tokens(items),
        "budget_tokens": BUDGET_TOKENS,
    }


# ----------------------------------------------------------------------------- S5 section


def global_section(ctx: RetrieveContext) -> dict[str, Any]:
    """What S5 answers for the thematic competency question, and that the dedupe holds."""
    questions = _competency()
    question = questions.get(THEMATIC_QID, {}).get(
        "question", "What are the main themes of open bugs in clients?"
    )
    census = ctx.read(
        f"MATCH (c:{ctx.label('Community')}) WHERE c.summary IS NOT NULL\n"
        "RETURN c.level AS level, c.level_name AS name, count(c) AS n, "
        "count(c.embedding) AS embedded ORDER BY level"
    )
    duplicates = ctx.read(
        f"MATCH (c:{ctx.label('Community')}) WHERE c.summary IS NOT NULL\n"
        "WITH c.member_hash AS hash, collect(c.id) AS ids, collect(c.level) AS levels\n"
        "WHERE size(ids) > 1 RETURN hash, ids, levels ORDER BY hash"
    )

    by_level = {}
    for level in ("any", "fine", "coarse"):
        result = global_search(ctx, question, level=level, k=5, log_mode="report")
        by_level[level] = _s5_row(result)

    checked = _dedupe_check(ctx, duplicates)
    top = by_level["any"]
    return {
        "question_id": THEMATIC_QID,
        "question": question,
        "index": "community_embedding",
        "levels": LEVELS,
        "summarized_by_level": {
            str(r["level"]): {"name": r["name"], "communities": r["n"], "embedded": r["embedded"]}
            for r in census
        },
        "cross_level_duplicate_pairs": len(duplicates),
        "by_level": by_level,
        "dedupe": checked,
        "checks": [
            {
                "name": "s5_answers_the_thematic_question_with_evidence",
                "ok": bool(top["items"]) and top["items_with_evidence"] >= 1,
                "detail": f"{top['items']} communities, {top['items_with_evidence']} with "
                f"≥1 evidence chunk id",
            },
            {
                "name": "no_duplicate_member_set_returns_twice",
                "ok": checked["violations"] == 0,
                "detail": f"{checked['pairs_probed']} pairs probed, "
                f"{checked['suppressed']} coarse twins suppressed, "
                f"{checked['violations']} violations",
            },
            {
                "name": "five_reports_fit_the_context_budget",
                "ok": top["truncated"] is False and top["items"] == 5,
                "detail": f"{top['items']} items, {top['tokens_out']} tokens, "
                f"truncated={top['truncated']}",
            },
        ],
    }


def _s5_row(result: Result) -> dict[str, Any]:
    return {
        "latency_ms": result.latency_ms,
        "truncated": result.truncated,
        "items": len(result.items),
        "tokens_out": total_tokens(result.items),
        "items_with_evidence": sum(
            1 for i in result.items if any(p.chunk_id for p in i.provenance)
        ),
        "communities": [
            {
                "id": i.key,
                "level": i.props.get("level"),
                "size": i.props.get("size"),
                "rank": i.props.get("rank"),
                "score": round(i.score, 4),
                "title": i.title,
                "findings": i.props.get("finding_count"),
                "evidence_chunk_ids": [p.chunk_id for p in i.provenance][:3],
                "duplicate_of": i.props.get("duplicate_of"),
            }
            for i in result.items
        ],
    }


def _dedupe_check(ctx: RetrieveContext, duplicates: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask, with each duplicated report's own title, whether both twins come back."""
    probed, suppressed, violations = 0, 0, []
    for row in duplicates[:5]:
        ids = [str(i) for i in row["ids"]]
        title = _first(
            ctx,
            f"MATCH (c:{ctx.label('Community')} {{`id`: '{ids[0]}'}}) RETURN c.title AS title",
        ).get("title")
        if not title:
            continue
        probed += 1
        returned = [i.key for i in global_search(ctx, title, level="any", k=8, log=False).items]
        hit = [i for i in ids if i in returned]
        if len(hit) > 1:
            violations.append({"hash": row["hash"], "returned": hit})
        elif hit:
            suppressed += 1
    return {
        "pairs_probed": probed,
        "suppressed": suppressed,
        "violations": len(violations),
        "examples": violations[:3],
    }


# --------------------------------------------------------------------------------- write


def merge(sections: dict[str, Any], path: Path | None = None) -> Path:
    """Add these sections to `data/reports/retrieve.json` without losing Task 1's.

    `brain competency` builds its report from scratch, so it overwrites whatever is here —
    which is why `brain serve --check` is the *last* of the two to run, and why this merges
    instead of writing a file of its own: one step, one report (conventions, "Reports").
    """
    target = Path(path) if path is not None else REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(sections)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def mcp_checks(mcp_section: dict[str, Any]) -> list[dict[str, Any]]:
    missing = sorted(set(TOOL_NAMES) - set(mcp_section["tools_listed"]))
    extra = sorted(set(mcp_section["tools_listed"]) - set(TOOL_NAMES))
    failed = [t["tool"] for t in mcp_section["tools"] if not t["valid_envelope"]]
    errored = [t["tool"] for t in mcp_section["tools"] if t["error"]]
    truncation = mcp_section["truncation"]
    return [
        {
            "name": "tools_list_matches_spec_4_3",
            "ok": not missing and not extra and len(mcp_section["tools_listed"]) == 15,
            "detail": f"{len(mcp_section['tools_listed'])} tools"
            + (f"; missing {missing}" if missing else "")
            + (f"; unexpected {extra}" if extra else ""),
        },
        {
            "name": "two_resources_and_one_prompt",
            "ok": sorted(mcp_section["resources_listed"]) == sorted(RESOURCE_URIS)
            and sorted(mcp_section["prompts_listed"]) == sorted(PROMPT_NAMES),
            "detail": f"{mcp_section['resources_listed']} + {mcp_section['prompts_listed']}",
        },
        {
            "name": "every_tool_returns_a_valid_result_envelope",
            "ok": not failed,
            "detail": ", ".join(failed) or "15/15 parse as Result",
        },
        {
            "name": "no_tool_answers_with_an_error",
            "ok": not errored,
            "detail": ", ".join(errored) or "none",
        },
        {
            "name": "truncation_keeps_at_least_one_item_per_kind",
            "ok": bool(truncation["truncated"]) and len(truncation["kinds_kept"]) > 1,
            "detail": f"truncated={truncation['truncated']}, kinds kept "
            f"{truncation['kinds_kept']}, {truncation['tokens_out']} tokens",
        },
    ]


def run(*, report_path: Path | None = None, echo=lambda _m: None) -> tuple[dict[str, Any], Path]:
    """Measure S5 in process, the server over stdio, and merge both into the step report."""
    settings = get_settings()
    with RetrieveContext.open(settings) as ctx:
        echo("global search (S5) …")
        global_part = global_section(ctx)
        arguments = tool_arguments(ctx)

    echo(f"stdio round trip: {' '.join(server_command())} …")
    measured = anyio.run(_measure, arguments)
    measured["checks"] = mcp_checks(measured)
    measured["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    measured["transport"] = "stdio"
    measured["repeats"] = REPEATS

    sections = {"global": global_part, "mcp": measured}
    path = merge(sections, report_path)

    for row in measured["tools"]:
        echo(
            f"  {row['tool']:<22} {str(row['round_trip_ms']['p50']):>5} ms round trip  "
            f"{str(row['latency_ms']):>5} ms retrieval  {row['items']:>3} items"
            + (f"  ERROR {row['error']}" if row["error"] else "")
        )
    for check in [*global_part["checks"], *measured["checks"]]:
        echo(f"  [{'ok' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
    return sections, path
