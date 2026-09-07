"""One entry point — `ask()` — that turns a question into a `Result`, whichever strategy runs.

`brain ask` and (in Task 3) every MCP tool that takes a whole question go through here, so
"what the router suggested", "what actually ran" and "why" are recorded in one place and
in the same words. Plan decision 1: no dispatch logic that exists only in the server.

Two things this file does that `route()` deliberately does not:

* **Fallback.** The router is faithful to spec §4.2 and will suggest S4 (Text2Cypher) for an
  aggregation question, which needs a Cypher author rather than a retrieval.
  Rather than weaken the router so today's questions land on today's strategies — which
  would make the router a report of what is implemented instead of what is right — an
  unimplemented suggestion is executed by its nearest neighbour and *both* are recorded.
  `Result.route` carries `strategy` (suggested) and `executed`.
* **Binding S6's arguments.** `status_at(key, date)` needs a key and a date; the router
  only says "this is temporal". Pulling `KAFKA-15123` and `2024-03-01` out of the sentence
  is parsing, not routing, and it belongs next to the dispatch that needs it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.global_search import global_search
from brain.retrieve.graph_vector import search_with_context
from brain.retrieve.hybrid import search_chunks
from brain.retrieve.impact import impact
from brain.retrieve.keys import find_keys
from brain.retrieve.local import local_search
from brain.retrieve.lookup import lookup, resolve_key
from brain.retrieve.route import route
from brain.retrieve.temporal import assignees_over_time, changes_between, status_at, timeline
from brain.retrieve.types import Result, RetrieveError

#: What runs when the router suggests a strategy this plan step has not built yet.
#: S4 (aggregation) falls back to `impact` when the question names a node the graph holds
#: and to S3 otherwise — because the aggregation questions in this corpus are about a
#: component or a KIP, and S3's relation set (`MENTIONS/DECIDES/…`) does not include
#: `IN_COMPONENT`, so a component anchor walks nowhere. Every fallback is recorded in
#: `route.fallback_from`, never silent. S5 was here until Task 3 built `global_search`; a
#: thematic question now reaches the community reports instead of the hybrid baseline.
FALLBACKS: dict[str, str] = {"s4": "s3"}
IMPLEMENTED: tuple[str, ...] = ("s1", "s2", "s3", "s5", "s6", "lookup", "impact")


def ask(
    ctx: RetrieveContext,
    question: str,
    strategy: str = "auto",
    *,
    k: int = 10,
    hops: int = 1,
    depth: int = 2,
    rerank: bool = False,
    mode: str = "hybrid",
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
) -> Result:
    """Route (or obey `strategy`), execute, and return the uniform envelope."""
    suggestion = route(question)
    chosen = suggestion["strategy"] if strategy in ("auto", "", None) else strategy
    executed = chosen if chosen in IMPLEMENTED else _fallback(ctx, question, chosen)
    trace: dict[str, Any] = {**suggestion, "executed": executed, "requested": strategy}
    if executed != chosen:
        trace["fallback_from"] = chosen
        trace["fallback_reason"] = f"{chosen} is not implemented in this plan step"

    common = {"log_mode": log_mode, "log_path": log_path, "log": log, "route": trace}
    if executed == "s1":
        return search_chunks(ctx, question, k=k, mode=mode, rerank=rerank, **common)
    if executed == "s2":
        return search_with_context(ctx, question, k=min(k, 8), hops=hops, **common)
    if executed == "s3":
        return local_search(ctx, question, depth=depth, k=k, **common)
    if executed == "s5":
        # Five reports is what a reduce can hold inside the 4k ceiling; `--k 10` on a
        # thematic question asks for more themes than the budget can carry.
        return global_search(ctx, question, k=min(k, 5), **common)
    if executed == "lookup":
        keys = find_keys(question).all_keys()
        if not keys:
            return search_with_context(ctx, question, k=min(k, 8), hops=hops, **common)
        return lookup(ctx, keys[0], **common)
    if executed == "impact":
        trace["tool"] = "impact"
        anchor = _resolvable_anchor(ctx, question)
        return impact(ctx, anchor or question, depth=depth, **common)
    if executed == "s6":
        return _temporal(ctx, question, k=k, hops=hops, **common)
    raise RetrieveError(f"unknown strategy {executed!r}; expected one of {IMPLEMENTED}")


def _fallback(ctx: RetrieveContext, question: str, chosen: str) -> str:
    if chosen == "s4" and _resolvable_anchor(ctx, question):
        return "impact"
    return FALLBACKS.get(chosen, "s2")


def _resolvable_anchor(ctx: RetrieveContext, question: str) -> str | None:
    """The first thing the question names that the graph actually holds, or `None`."""
    keys = find_keys(question)
    for candidate in (*keys.all_keys(), *keys.quoted):
        try:
            resolve_key(ctx, candidate)
        except RetrieveError:
            continue
        return candidate
    return None


def _components(ctx: RetrieveContext) -> dict[str, str]:
    rows = ctx.read(f"MATCH (c:{ctx.label('Component')}) RETURN c.name AS name")
    return {r["name"].casefold(): r["name"] for r in rows if r["name"]}


def _component_in(question: str, known: dict[str, str]) -> str | None:
    """The component this question is about, matched against the graph's own names."""
    keys = find_keys(question)
    for candidate in keys.quoted:
        if candidate.casefold() in known:
            return known[candidate.casefold()]
    for token in question.replace("?", " ").replace(",", " ").split():
        cleaned = token.strip("`'\"“”‘’.,;:()[]").casefold()
        if cleaned in known:
            return known[cleaned]
    return None


def _temporal(
    ctx: RetrieveContext,
    question: str,
    *,
    k: int,
    hops: int,
    log_mode: str,
    log_path: Path | None,
    log: bool,
    route: dict[str, Any],
) -> Result:
    """Bind the temporal tool the sentence is actually asking for. Falls back to S2."""
    keys = find_keys(question)
    item_key = (keys.workitems or keys.documents or ("",))[0]
    tool_args = {"log_mode": log_mode, "log_path": log_path, "log": log, "route": route}

    if keys.dates and item_key:
        route["tool"] = "status_at"
        return status_at(ctx, item_key, keys.dates[0], **tool_args)
    if len(keys.versions) >= 2:
        component = _component_in(question, _components(ctx))
        if component:
            route["tool"] = "changes_between"
            return changes_between(ctx, component, keys.versions[0], keys.versions[1], **tool_args)
    if item_key and _asks_about_people(question):
        route["tool"] = "assignees_over_time"
        return assignees_over_time(ctx, item_key, **tool_args)
    if item_key:
        route["tool"] = "timeline"
        return timeline(ctx, item_key, **tool_args)

    route["tool"] = "search_with_context"
    route["fallback_from"] = "s6"
    route["fallback_reason"] = "temporal wording but no key/date/version pair to bind"
    return search_with_context(
        ctx,
        question,
        k=min(k, 8),
        hops=hops,
        log_mode=log_mode,
        log_path=log_path,
        log=log,
        route=route,
    )


_PEOPLE_WORDS = ("assign", "owner", "who ", "responsible", "אחראי", "מי ", "הוקצה", "בעלים")


def _asks_about_people(question: str) -> bool:
    lowered = question.casefold()
    return any(w in lowered for w in _PEOPLE_WORDS)


def render(result: Result, question: str = "") -> str:
    """The human-readable form `brain ask` prints. JSON is `--json`."""
    lines: list[str] = []
    trace = result.route or {}
    if question:
        lines.append(f"question: {question}")
    header = f"strategy: {result.strategy}"
    if trace.get("strategy") and trace["strategy"] != result.strategy:
        header += f" (router suggested {trace['strategy']})"
    if trace.get("tool"):
        header += f" · tool: {trace['tool']}"
    lines.append(header)
    if trace.get("reason"):
        lines.append(f"why: {trace['reason']}")
    if trace.get("fallback_reason"):
        lines.append(f"note: {trace['fallback_reason']}")
    lines.append(
        f"{len(result.items)} items · {result.latency_ms} ms"
        + (" · truncated to the 4k-token budget" if result.truncated else "")
    )
    lines.append("")
    for index, item in enumerate(result.items, start=1):
        lines.append(f"{index:>2}. [{item.kind}] {item.key}  (score {item.score:.4f})")
        if item.title:
            lines.append(f"    {item.title}")
        if item.snippet:
            lines.append(f"    {item.snippet[:300]}")
        for prov in item.provenance[:2]:
            bits = [b for b in (prov.chunk_id, prov.source) if b]
            if bits:
                lines.append(f"    ↳ evidence: {' · '.join(bits)}")
            if prov.quote:
                lines.append(f'      "{prov.quote[:180]}"')
        lines.append("")
    if result.cypher_used:
        lines.append(f"cypher: {len(result.cypher_used)} statement(s); --json to see them")
    return "\n".join(lines)
