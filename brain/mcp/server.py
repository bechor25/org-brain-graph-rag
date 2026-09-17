"""The fifteen tools of spec §4.3, as a FastMCP server. An adapter, not a strategy.

Plan decision 1 in one sentence: `brain/retrieve/` is callable from Python — which is how
Plan 3 runs every strategy against every question under identical conditions — and this file
exposes the same functions one-to-one over MCP. Every tool body here is three lines: bind the
arguments, call the library, return `Result.model_dump()`. A behaviour that exists only when
the caller happens to be an agent is a behaviour the evaluation cannot measure.

Three things the adapter *is* responsible for, because they are transport concerns:

* **Never raising at the agent.** A `GuardError`, an unknown key or a dead Ollama comes back
  as the same envelope with an `error` and a `hint` in a `Row` item, so the agent can read
  what went wrong and try something else instead of seeing a protocol fault. Everything is
  still logged to `data/logs/retrieval.jsonl`, refusals included.
* **Not blocking the loop.** Every tool is `async` and hands the blocking Neo4j/Ollama work
  to a worker thread. Over stdio there is one client and it would not matter; over
  streamable HTTP two sessions would otherwise queue behind each other.
* **JSON that survives the wire.** `Item.props` holds whatever a projection returned, so the
  envelope goes through `json.dumps(..., default=str)` once before it leaves. A
  `neo4j.time.DateTime` that slipped past a projection becomes a string instead of a
  serialisation failure in the middle of an answer.

**The S4 trio** (`get_schema`, `cypher_examples`, `run_cypher`) is imported lazily, inside
the call. Task 2 owns those modules; importing them at module scope would mean this server
could not start until that task landed, and a server that will not start cannot be tested.
"""

from __future__ import annotations

import functools
import json
import threading
from collections.abc import Callable
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from brain import retrieve
from brain.config import get_settings
from brain.retrieve.context import RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.types import Item, Result

#: Spec §4.3, in the order the table lists them. The test that counts these is the one that
#: catches a tool renamed on one side of the contract and not the other.
TOOL_NAMES: tuple[str, ...] = (
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
)

RESOURCE_URIS: tuple[str, ...] = ("brain://schema", "brain://stats")
PROMPT_NAMES: tuple[str, ...] = ("answer_with_citations",)

INSTRUCTIONS = """\
The organizational brain: a Neo4j graph built from Jira, Confluence/KIPs, git and a
synthetic Xray/ADO layer, with chunk, entity and community embeddings.

Pick a tool by what the question is:
  a key is named (KAFKA-…, KIP-…, a sha, a person id) → lookup, then local_search
  rationale / impact ("why", "what depends on")       → local_search
  fuzzy traceability                                  → search_with_context
  a count, a superlative, an aggregate                → get_schema + cypher_examples + run_cypher
  themes, an overview of the corpus                   → global_search
  a date, a version window, "over time"               → status_at / timeline / changes_between /
                                                        assignees_over_time
  "why does the brain believe this edge"              → explain_edge
`route(question)` gives a deterministic suggestion; it is advice, not an instruction.

Every tool answers with the same envelope: items[] each with kind, key, title, snippet,
score, props and provenance[], plus cypher_used, latency_ms and truncated. `truncated` means
the ~4k token ceiling was reached and lower-scoring items were dropped — ask again, narrower.
Cite `provenance[].chunk_id` and item keys for every factual claim."""

mcp: FastMCP = FastMCP("brain", instructions=INSTRUCTIONS)

# --------------------------------------------------------------------------- one context

_lock = threading.Lock()
_ctx: RetrieveContext | None = None


def context() -> RetrieveContext:
    """The process-wide read-only graph + embedder.

    One driver and one HTTP client for the life of the server: they are the two expensive
    objects in a retrieval and building them per tool call would put a connection handshake
    inside every latency measurement.
    """
    global _ctx
    with _lock:
        if _ctx is None:
            _ctx = RetrieveContext.open(get_settings())
        return _ctx


def close_context() -> None:
    global _ctx
    with _lock:
        if _ctx is not None:
            _ctx.close()
            _ctx = None


# ------------------------------------------------------------------------- the envelope

#: What a caller should do about each failure, by exception type. A tool that says only
#: "it failed" makes an agent retry the same call; a hint makes it try a different one.
HINTS: dict[str, str] = {
    "RetrieveError": "check the key with `lookup`, or ask `get_schema` what exists",
    "EmbedModelMismatch": "the index was built with a different embedding model — re-embed "
    "(`brain chunk` / `brain resolve` / `brain communities`) or restore EMBED_MODEL",
    "ValueError": "one of the arguments is outside its allowed set — see the tool's schema",
    "ModuleNotFoundError": "this tool arrives with Plan 2 Task 2 (guarded Text2Cypher)",
    "ImportError": "this tool arrives with Plan 2 Task 2 (guarded Text2Cypher)",
}


def already_logged(exc: BaseException) -> bool:
    """Did the library write this failure to the trace before it raised?

    `cypher_guard.run_cypher` logs every refusal with its `reason` and the Cypher that was
    refused, and *then* raises. The adapter turning that into an envelope must not append a
    second, poorer line: one call, one trace line, or the refusal rate in the Plan 2 report
    counts every rejection twice. `as_dict` is the guard's own contract — an exception that
    can describe itself is one that has already been recorded.
    """
    return callable(getattr(exc, "as_dict", None))


def error_result(
    strategy: str,
    question: str,
    exc: BaseException,
    *,
    route: dict[str, Any] | None = None,
    log: bool | None = None,
) -> Result:
    """A failure in the shape of an answer: one `Row` item carrying `error` and `hint`.

    Raising through MCP gives the agent a protocol fault with no envelope, no trace line and
    nothing to reason about. `GuardError` already knows how to describe itself (Task 2's
    `as_dict`); everything else is described here.
    """
    as_dict = getattr(exc, "as_dict", None)
    detail = as_dict() if callable(as_dict) else {}
    props: dict[str, Any] = {
        "error": detail.get("error") or f"{type(exc).__name__}: {exc}",
        "hint": detail.get("hint") or HINTS.get(type(exc).__name__, ""),
    }
    if detail.get("reason"):
        props["reason"] = detail["reason"]
    item = Item(kind="Row", key=f"{strategy}:error", title="the tool could not answer", props=props)
    trace = {**(route or {}), "error": props["error"]}
    return finish(
        strategy,
        [item],
        question=question,
        timer=Timer(),
        route=trace,
        mode="mcp",
        log=not already_logged(exc) if log is None else log,
    )


def jsonable(result: Result) -> dict[str, Any]:
    """`Result.model_dump()`, forced through JSON once. See the module docstring."""
    return json.loads(json.dumps(result.model_dump(), ensure_ascii=False, default=str))


def _blocking(strategy: str, question: str, call: Callable[[RetrieveContext], Result]) -> dict:
    try:
        result = call(context())
    except Exception as exc:  # noqa: BLE001 - the agent gets an envelope, never a fault
        result = error_result(strategy, question, exc)
    return jsonable(result)


async def _tool(strategy: str, question: str, call: Callable[[RetrieveContext], Result]) -> dict:
    """Run one library call off the event loop and return the envelope as plain JSON."""
    return await anyio.to_thread.run_sync(functools.partial(_blocking, strategy, question, call))


def _pure(strategy: str, question: str, call: Callable[[], Result]) -> dict:
    """The same envelope for a tool that touches neither Neo4j nor Ollama."""
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001 - the agent gets an envelope, never a fault
        result = error_result(strategy, question, exc)
    return jsonable(result)


# ------------------------------------------------------------------------------- S1 / S2


@mcp.tool()
async def search_chunks(
    query: str, k: int = 10, mode: str = "hybrid", rerank: bool = False
) -> dict[str, Any]:
    """S1 — the text baseline. Top-k chunks for a question, by vector, fulltext or both.

    `mode`: `hybrid` (reciprocal-rank fusion of the two, the default), `vector` (bge-m3
    cosine, works across Hebrew and English), `fulltext` (Lucene over the chunk text, exact
    on keys and identifiers). Use it when you want the source text itself; use
    `search_with_context` when you want to know what the text is attached to.
    """
    return await _tool(
        "s1",
        query,
        lambda ctx: retrieve.search_chunks(
            ctx, query, k=k, mode=mode, rerank=rerank, log_mode="mcp"
        ),
    )


@mcp.tool()
async def search_with_context(query: str, k: int = 8, hops: int = 1) -> dict[str, Any]:
    """S2 — the default for a fuzzy question. Chunks, resolved to their parents plus neighbours.

    Finds chunks by vector, then returns the issue, KIP or commit each belongs to with its
    structured neighbourhood in `props.neighbors` (references, links, tests, runs, resolves,
    implements, current assignee, component). `hops` is 1 or 2.
    """
    return await _tool(
        "s2",
        query,
        lambda ctx: retrieve.search_with_context(ctx, query, k=k, hops=hops, log_mode="mcp"),
    )


# ------------------------------------------------------------------------ lookup / S3


@mcp.tool()
async def lookup(key: str) -> dict[str, Any]:
    """One node by key, with its neighbourhood and a few evidence chunks.

    Accepts `KAFKA-15123`, `KIP-848`, a commit sha, a person id (`jira:mjsax`), an entity id
    (`Decision|…`) or a component name. Start here whenever the question names something.
    """
    return await _tool("lookup", key, lambda ctx: retrieve.lookup(ctx, key, log_mode="mcp"))


@mcp.tool()
async def local_search(
    query: str, kinds: list[str] | None = None, depth: int = 2, k: int = 10
) -> dict[str, Any]:
    """S3 — entity-anchored local search. The strategy for "why" and "what depends on".

    Anchors on the keys the question names (or, failing that, on the nearest entities), walks
    up to `depth` hops over DECIDES / MOTIVATED_BY / REJECTS / IMPLEMENTS / DEPENDS_ON /
    INTRODUCES_RISK / TESTS / RESOLVES / MENTIONS, and ranks by degree × edge weight ×
    similarity to the question. Every item carries the quotes it was extracted from.
    `kinds` restricts entities to Feature, Decision, Problem, Alternative, Risk, Technology.
    """
    return await _tool(
        "s3",
        query,
        lambda ctx: retrieve.local_search(
            ctx, query, kinds=kinds, depth=depth, k=k, log_mode="mcp"
        ),
    )


# ------------------------------------------------------------------------------- S4


def _s4(name: str, question: str, call: Callable[[RetrieveContext], Result]) -> Result:
    """Run one Text2Cypher tool, or explain that Task 2 has not landed it yet."""
    try:
        return call(context())
    except (ImportError, ModuleNotFoundError) as exc:
        return error_result(
            "s4",
            question,
            exc,
            route={
                "strategy": "s4",
                "executed": "none",
                "fallback_from": "s4",
                "fallback_reason": f"{name} is built in Plan 2 Task 2 and is not importable here",
                "tool": name,
            },
        )
    except Exception as exc:  # noqa: BLE001 - a guard refusal is an answer, not a fault
        return error_result("s4", question, exc, route={"strategy": "s4", "tool": name})


@mcp.tool()
async def get_schema(refresh: bool = False) -> dict[str, Any]:
    """S4 step 1 — the labels, relationship types and indexes this graph actually has.

    One item per label and per relationship type, biggest first, so the 4k ceiling keeps the
    parts of the schema a query is most likely to need. The whole schema, untrimmed, is the
    `brain://schema` resource. Read this before writing Cypher: the closed schema is the
    difference between a query that runs and a query that invents a label.
    """

    def _call(ctx: RetrieveContext) -> Result:
        from brain.retrieve.schema import compact_schema
        from brain.retrieve.schema import get_schema as read_schema

        timer = Timer()
        schema = compact_schema(read_schema(ctx, refresh=refresh))
        return finish(
            "s4",
            _schema_items(schema),
            question="get_schema",
            timer=timer,
            route={"strategy": "s4", "tool": "get_schema"},
            mode="mcp",
        )

    return jsonable(await anyio.to_thread.run_sync(lambda: _s4("get_schema", "get_schema", _call)))


def _schema_items(schema: dict[str, Any]) -> list[Item]:
    """The compact schema as items, scored by size so truncation drops the rare labels."""
    items = [
        Item(
            kind="Row",
            key=label["label"],
            title=f"(:{label['label']}) · {label['count']}",
            snippet=", ".join(f"{k}: {v}" for k, v in label["properties"].items()),
            score=float(label["count"]),
            props={"count": label["count"], "indexed": label["indexed"], "of": "label"},
        )
        for label in schema["labels"]
    ]
    items += [
        Item(
            kind="Row",
            key=f"[:{rel['type']}]",
            title=f"[:{rel['type']}] · {rel['count']}",
            snippet=" ; ".join(rel["patterns"]),
            score=float(rel["count"]),
            props={"count": rel["count"], "properties": rel["properties"], "of": "relationship"},
        )
        for rel in schema["relationships"]
    ]
    items.append(
        Item(
            kind="Row",
            key="__indexes",
            title="search indexes and the models behind them",
            snippet=" ; ".join(schema["search_indexes"]),
            # Above every label count, because a query that does not know which vector index
            # exists cannot be written at all.
            score=float(schema["node_count"] + 1),
            props={
                "index_meta": schema["index_meta"],
                "allowed_procedures": schema["allowed_procedures"],
                "node_count": schema["node_count"],
                "relationship_count": schema["relationship_count"],
                "of": "indexes",
            },
        )
    )
    return items


@mcp.tool()
async def cypher_examples(question_type: str | None = None) -> dict[str, Any]:
    """S4 step 2 — verified read-only Cypher for one kind of question, as few-shot examples.

    `question_type` is one of `traceability`, `impact`, `rationale`, `temporal` (all four
    when omitted). Every example in the bank was run against this graph and returned rows.
    """

    def _call(_ctx: RetrieveContext) -> Result:
        from brain.retrieve.examples import cypher_examples as read_examples

        timer = Timer()
        rows = read_examples(question_type)
        items = [
            Item(
                kind="Row",
                key=str(row.get("id") or f"example-{index}"),
                title=str(row.get("question") or ""),
                snippet=str(row.get("cypher") or ""),
                score=float(len(rows) - index),
                props={k: v for k, v in row.items() if k not in ("question", "cypher", "id")},
            )
            for index, row in enumerate(rows)
        ]
        return finish(
            "s4",
            items,
            question=f"cypher_examples({question_type})",
            timer=timer,
            route={"strategy": "s4", "tool": "cypher_examples", "question_type": question_type},
            mode="mcp",
        )

    return jsonable(
        await anyio.to_thread.run_sync(
            lambda: _s4("cypher_examples", f"cypher_examples({question_type})", _call)
        )
    )


@mcp.tool()
async def run_cypher(
    cypher: str,
    params: dict[str, Any] | None = None,
    limit: int = 100,
    timeout_s: float = 10.0,
) -> dict[str, Any]:
    """S4 step 3 — run read-only Cypher against the graph, behind the guard.

    Refused, with `error` and `hint` in the answer: any write verb, `LOAD CSV`, a `CALL` to a
    procedure outside the allowlist, a plan the planner says writes, more than one statement,
    and anything slower than `timeout_s`. A missing `LIMIT` is injected rather than refused.
    Write `get_schema` first and copy a shape from `cypher_examples`.
    """

    def _call(ctx: RetrieveContext) -> Result:
        from brain.retrieve.cypher_guard import run_cypher as guarded

        return guarded(ctx, cypher, params or {}, timeout_s=timeout_s, limit=limit, log_mode="mcp")

    return jsonable(await anyio.to_thread.run_sync(lambda: _s4("run_cypher", cypher, _call)))


# ------------------------------------------------------------------------------- S5


@mcp.tool()
async def global_search(query: str, level: str = "any", k: int = 5) -> dict[str, Any]:
    """S5 — themes, not documents. The nearest community reports for a thematic question.

    Answers "what are the main themes of…", "give me an overview of…" — questions whose
    answer is a shape of the corpus rather than a passage in it. Each item is one Leiden
    community's report: a title, a summary, findings with the chunk ids behind them, a rank
    and the community's size. `level` is `fine` (smaller, more specific communities),
    `coarse` (larger) or `any`. You do the reduce; this returns the material for it.
    """
    return await _tool(
        "s5",
        query,
        lambda ctx: retrieve.global_search(ctx, query, level=level, k=k, log_mode="mcp"),
    )


# ------------------------------------------------------------------------------- S6


@mcp.tool()
async def status_at(key: str, date: str) -> dict[str, Any]:
    """S6 — what a work item or document held on a date (`YYYY-MM-DD`), from the changelog.

    Deterministic: it replays `StatusChange` events up to the end of that day, so it answers
    "what did we know then", not "what is true now". A document (a KIP) has no status of its
    own, so it is answered derived: the status on that date of each work item that
    references it, one row each, plus the page's own status when it carries one.
    """
    return await _tool(
        "s6", f"{key} @ {date}", lambda ctx: retrieve.status_at(ctx, key, date, log_mode="mcp")
    )


@mcp.tool()
async def timeline(key: str, limit: int = 60) -> dict[str, Any]:
    """S6 — every recorded change on a work item, oldest first: status, assignee, version.

    Takes a work item key or a document key. A document (a KIP) has no changelog of its own
    and is answered derived: the commits that implement it, plus the status changes and fix
    versions of the work items that reference it, in one time order. Every derived row says
    which edge it came from in `props.via` (`IMPLEMENTS_KIP` / `REFERENCES`) and cites the
    node it was derived from.
    """
    return await _tool(
        "s6", key, lambda ctx: retrieve.timeline(ctx, key, limit=limit, log_mode="mcp")
    )


@mcp.tool()
async def changes_between(component: str, v1: str, v2: str) -> dict[str, Any]:
    """S6 — work items in a component fixed after `v1` and up to `v2`, with their commits.

    `component` is a Kafka component name (`clients`, `streams`, `core`); versions are
    release numbers (`3.7`, `3.8.0`). Two-part versions cover the whole family.
    """
    return await _tool(
        "s6",
        f"{component} {v1}..{v2}",
        lambda ctx: retrieve.changes_between(ctx, component, v1, v2, log_mode="mcp"),
    )


@mcp.tool()
async def assignees_over_time(key: str) -> dict[str, Any]:
    """S6 — every assignment interval on a work item, oldest first. An open interval is current.

    Takes a work item key or a document key. A document (a KIP) has no assignee of its own
    and is answered derived: the people who held the work items that reference it, merged
    into one entry per person with the span they actually held.
    """
    return await _tool(
        "s6", key, lambda ctx: retrieve.assignees_over_time(ctx, key, log_mode="mcp")
    )


# --------------------------------------------------------------- impact / explain / route


@mcp.tool()
async def impact(key_or_name: str, depth: int = 2) -> dict[str, Any]:
    """ "If we change this, what breaks" — open issues, tests with their last run, docs, commits.

    Takes a component name, a work item key, a KIP or a commit sha. The commits are the ones
    that touched the same files, which is the part a traversal of the issue graph misses.
    """
    return await _tool(
        "s3",
        key_or_name,
        lambda ctx: retrieve.impact(ctx, key_or_name, depth=depth, log_mode="mcp"),
    )


@mcp.tool()
async def explain_edge(src: str, dst: str) -> dict[str, Any]:
    """Why the brain believes two things are connected: every edge between them, with provenance.

    Returns the verbatim quote, the chunk id, the batch, the model and the extraction time
    behind each edge — and for a `SAME_AS`, the tier and score that merged them. Use it
    before stating anything surprising.
    """
    return await _tool(
        "lookup",
        f"{src} -> {dst}",
        lambda ctx: retrieve.explain_edge(ctx, src, dst, log_mode="mcp"),
    )


@mcp.tool()
async def route(question: str) -> dict[str, Any]:
    """The deterministic pre-router's suggestion for a question: which strategy, and why.

    Advice, not an instruction (spec §4.2): you choose the tools. It exists so that a trace
    records what a rule-based router *would* have done next to what you did. Pure text: it
    reads no graph, so it answers even when the database does not.
    """

    def _call() -> Result:
        timer = Timer()
        suggestion = retrieve.route(question)
        item = Item(
            kind="Row",
            key=str(suggestion["strategy"]),
            title=f"suggested strategy: {suggestion['strategy']}",
            snippet=str(suggestion["reason"]),
            score=float(suggestion["confidence"]),
            props=dict(suggestion),
        )
        return finish("route", [item], question=question, timer=timer, route=suggestion, mode="mcp")

    # Not `_tool`: `route()` is a regex over the question, and opening the process-wide
    # graph context for it would make the one tool that cannot fail fail whenever Neo4j is
    # down — and would put a connection handshake in front of a 0 ms answer. It stays on the
    # event loop for the same reason: there is nothing to block on.
    return _pure("route", question, _call)


# --------------------------------------------------------------------------- resources


@mcp.resource("brain://schema", mime_type="application/json")
async def schema_resource() -> str:
    """The whole reduced schema: labels, properties, relationship patterns, indexes, models.

    Deliberately not trimmed to the 4k answer ceiling the way the `get_schema` tool is — a
    resource is read on purpose, once, and a half-schema is what makes a model invent a label.
    """

    def _read() -> str:
        try:
            from brain.retrieve.schema import compact_schema
            from brain.retrieve.schema import get_schema as read_schema

            payload: dict[str, Any] = compact_schema(read_schema(context()))
        except Exception as exc:  # noqa: BLE001 - a resource answers or explains itself
            payload = {
                "error": f"{type(exc).__name__}: {exc}",
                "hint": HINTS.get(type(exc).__name__, ""),
            }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    return await anyio.to_thread.run_sync(_read)


@mcp.resource("brain://stats", mime_type="application/json")
async def stats_resource() -> str:
    """How much of the corpus is actually in the graph: chunks, entities, communities, indexes.

    Read it before believing an empty answer — "no results" and "that layer was never built"
    look identical from inside a tool.
    """

    def _read() -> str:
        try:
            from brain.community.graph import census as community_census
            from brain.retrieve.report import graph_stats

            ctx = context()
            settings = ctx.settings
            payload: dict[str, Any] = {
                "graph": graph_stats(ctx),
                "communities": community_census(ctx.graph),
                "embedding": {"model": settings.embed_model, "dim": settings.embed_dim},
                "database": settings.neo4j_database,
                "label_prefix": ctx.prefix,
                "include_synthetic": ctx.include_synthetic,
            }
        except Exception as exc:  # noqa: BLE001 - a resource answers or explains itself
            payload = {
                "error": f"{type(exc).__name__}: {exc}",
                "hint": HINTS.get(type(exc).__name__, ""),
            }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    return await anyio.to_thread.run_sync(_read)


# ------------------------------------------------------------------------------ prompt


@mcp.prompt(name="answer_with_citations")
def answer_with_citations(question: str) -> str:
    """How to answer an organizational question from this graph so the answer can be checked."""
    return (
        f"Answer this question using only the `brain` MCP tools:\n\n{question}\n\n"
        "Method:\n"
        "1. Call `route` and read its suggestion. You may override it — say why.\n"
        "2. If the question names a key (KAFKA-…, KIP-…, a sha, a person id), start with "
        "`lookup`.\n"
        "3. Then choose: `local_search` for rationale and impact; `search_with_context` for "
        "fuzzy traceability; `get_schema` + `cypher_examples` + `run_cypher` for counts and "
        "superlatives; `global_search` for themes; `status_at` / `timeline` / "
        "`changes_between` / `assignees_over_time` for time; `impact` for change impact.\n"
        "4. Verify anything surprising with `explain_edge` before stating it.\n\n"
        "Rules:\n"
        "- Answer in the language of the question.\n"
        "- End every factual sentence with its citations: `[KAFKA-15123]`, `[KIP-848]`, "
        "`[chunk:ab12…]` from `provenance[].chunk_id`.\n"
        "- If the tools return nothing relevant, say so. Never fill a gap from prior "
        "knowledge about Kafka — an uncited claim is the one failure this system exists to "
        "prevent.\n"
        "- If a result says `truncated: true`, say that the answer is partial, or ask again "
        "with a narrower query.\n"
        "- Finish with one line: `strategy: <tools you used, in order>`."
    )


# ----------------------------------------------------------------------------- healthz


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_request: Any) -> Any:
    """Liveness for the compose healthcheck. It answers before Neo4j is touched, on purpose:
    an MCP server that is up and a graph that is loaded are two different facts."""
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok", "tools": len(TOOL_NAMES)})


# ------------------------------------------------------------------------------- run


def build_server() -> FastMCP:
    """The configured server. A function so a test can hold it without importing side effects."""
    return mcp


def serve_stdio() -> None:
    """`brain serve --stdio` — the transport Claude Code launches from `.mcp.json`."""
    try:
        mcp.run(transport="stdio")
    finally:
        close_context()


def serve_http(host: str = "127.0.0.1", port: int = 8765) -> None:
    """`brain serve --http` — streamable HTTP at `/mcp`, for compose and for `curl`."""
    mcp.settings.host = host
    mcp.settings.port = port
    try:
        mcp.run(transport="streamable-http")
    finally:
        close_context()
