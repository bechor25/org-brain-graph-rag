"""The only place model-written text reaches the database, and what stands in front of it.

S4 is the strategy where an LLM writes the query. Everything else in `brain/retrieve/` is
Cypher we wrote; this is Cypher a model wrote, possibly from a question a stranger asked,
possibly from a document a stranger wrote. Spec §4.5 gives it four layers, and the order
matters because each one covers the previous one's blind spot:

1. **`RoutingControl.READ`** (`GraphClient.read()`). Server-side, and the only real
   enforcement available on Community Edition, which has no read-only role. Everything
   below is defence in depth on top of it.
2. **A deny-list over a masked query.** Comments and string literals are masked *first*
   (same length, so every offset still points where it did), which is what makes
   `WHERE c.text CONTAINS 'CREATE TABLE'` a legal question and `// harmless\\nCREATE (n)`
   an attack. Namespaced calls — `CALL apoc.…` and `apoc.…()` alike, because
   `apoc.cypher.runFirstColumn` is a *function* that runs arbitrary Cypher — must be in
   the allowlist.
3. **`EXPLAIN` before running.** The parser knows things a regex does not. If the plan the
   planner produces contains a write operator (`Create`, `SetProperty`, `DetachDelete`,
   `SubqueryForeach`, …), the query is refused whatever its text looked like. This is not
   theoretical: `EXPLAIN CREATE (n)` under `RoutingControl.READ` is *accepted* by the
   server (measured on 2026.06.0), so the plan has to be read, not merely requested.
4. **A transaction timeout and an injected `LIMIT`.** A read-only query can still be an
   outage: a cartesian product over 13,846 chunks, or a `LIMIT`-less return of the whole
   graph into an agent's context. `neo4j.Query(text, timeout=…)` carries the timeout to
   the server, so the *server* kills the query — a client-side `wait()` would only stop
   waiting for it.

Every rejection is a `GuardError` with a `reason` (a short stable token, so the report can
count them) and a `hint` (what to do instead), and every rejection is logged to the same
JSONL trace as a successful call. An attack that is refused silently teaches nobody.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from neo4j import Query
from neo4j.exceptions import ClientError, CypherSyntaxError, Neo4jError

from brain.retrieve import log as retrieve_log
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.pack import clip
from brain.retrieve.types import Item, Result

#: Spec §4.5. 10 seconds is the *server's* limit on the transaction, not a client wait.
DEFAULT_TIMEOUT_S = 10.0
#: Rows an S4 answer may carry before the packer even sees it.
DEFAULT_LIMIT = 100
#: A query longer than this is not a question, it is a payload.
MAX_CYPHER_CHARS = 4000

#: Clause keywords that can only appear in a query that changes something. `USE` is here
#: because it selects a database (`USE system` is the door to the admin surface), and
#: `LOAD`/`CSV`/`PERIODIC` because `LOAD CSV` reads the server's filesystem.
WRITE_TOKENS: tuple[str, ...] = (
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "DROP",
    "FOREACH",
    "LOAD",
    "CSV",
    "PERIODIC",
    "ALTER",
    "GRANT",
    "REVOKE",
    "DENY",
    "TERMINATE",
    "USE",
)

#: Namespaces whose members are procedure/function calls rather than property access.
CALL_ROOTS: frozenset[str] = frozenset({"apoc", "gds", "db", "dbms", "sys", "cypher", "tx"})

#: Plan decision: `db.index.*`, `apoc.meta.*`, `apoc.text.*`. Nothing else, and in
#: particular no `gds.*` (`gds.*.stream` included: a projection is a write on the graph
#: catalog) and no `apoc.cypher.*` (it runs Cypher we never guarded).
ALLOWED_CALL_PREFIXES: tuple[str, ...] = ("db.index.", "apoc.meta.", "apoc.text.")

#: …intersected with a verb deny-list, because a prefix is a coarse instrument:
#: `db.index.fulltext.createNodeIndex` sits inside an allowed prefix and creates an index.
FORBIDDEN_CALL_VERBS: tuple[str, ...] = (
    "create",
    "drop",
    "delete",
    "remove",
    "set",
    "add",
    "update",
    "merge",
    "install",
    "restore",
    "import",
    "export",
    "load",
)

#: Plan operators that write. Matched on the operator name with the `@database` suffix
#: stripped; prefix matching catches the family (`SetProperty`, `SetLabels`, `SetNodeProperties`).
WRITE_OPERATOR_PREFIXES: tuple[str, ...] = (
    "Create",
    "Merge",
    "Delete",
    "DetachDelete",
    "Set",
    "Remove",
    "Foreach",
    "SubqueryForeach",
    "Drop",
    "LoadCSV",
    "Grant",
    "Revoke",
    "Deny",
    "Alter",
    "Rename",
    "StartDatabase",
    "StopDatabase",
    "Terminate",
)

HINTS: dict[str, str] = {
    "empty": "send a Cypher statement",
    "too-long": f"keep the query under {MAX_CYPHER_CHARS} characters",
    "multiple-statements": "send one statement; `;` cannot separate two",
    "escape-sequence": "no `\\u` escapes — pass values as parameters instead",
    "unbalanced-quote": "close every quote and backtick",
    "write-verb": "read-only Cypher only: MATCH / OPTIONAL MATCH / WITH / WHERE / RETURN / "
    "ORDER BY / LIMIT / UNWIND",
    "call-subquery": "CALL {} subqueries are refused; express it with MATCH/WITH/OPTIONAL MATCH",
    "procedure-not-allowed": "the only callable namespaces are " + ", ".join(ALLOWED_CALL_PREFIXES),
    "write-plan": "the planner says this query writes; rewrite it as a read",
    "invalid-cypher": "the query did not parse or plan — check labels, types and parameters",
    "timeout": f"the query exceeded the {DEFAULT_TIMEOUT_S:g}s budget; add a LIMIT or an anchor",
    "read-mode": "the server refused this in READ mode",
}


class GuardError(RuntimeError):
    """A refusal, with a stable `reason` for the report and a `hint` for the caller."""

    def __init__(self, reason: str, detail: str = "", cypher: str = "") -> None:
        self.reason = reason
        self.detail = detail
        self.cypher = cypher
        self.hint = HINTS.get(reason, "")
        super().__init__(f"{reason}: {detail}" if detail else reason)

    def as_dict(self) -> dict[str, str]:
        """What an MCP tool returns instead of raising (spec §4.4: `{error, hint}`)."""
        return {"error": str(self), "hint": self.hint, "reason": self.reason}


# --------------------------------------------------------------------------- masking


def sanitize(cypher: str) -> str:
    """Mask comments, string literals and backticked identifiers, preserving every offset.

    Same-length masking is the point: the deny-list scan, the `LIMIT` detection and the
    statement-separator check all work on this string and every index still refers to the
    same character of the original, so an error can quote the query the caller sent.

    Raises `GuardError('unbalanced-quote')` on an unterminated literal — otherwise the
    mask would swallow the rest of the query and hide whatever came after it.
    """
    out: list[str] = []
    i, n = 0, len(cypher)
    while i < n:
        ch = cypher[i]
        if ch == "/" and i + 1 < n and cypher[i + 1] == "/":
            j = cypher.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if ch == "/" and i + 1 < n and cypher[i + 1] == "*":
            j = cypher.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
            continue
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if cypher[j] == "\\":
                    j += 2
                    continue
                if cypher[j] == ch:
                    break
                j += 1
            if j >= n:
                raise GuardError("unbalanced-quote", f"unterminated {ch} at offset {i}", cypher)
            out.append(ch + "x" * (j - i - 1) + ch)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _normalized(masked: str) -> str:
    """NFKC, so `ＣＲＥＡＴＥ` is `CREATE` to the scanner as it is to the parser."""
    return unicodedata.normalize("NFKC", masked)


def _token_pattern() -> re.Pattern[str]:
    #: `(?<![\w.$])` keeps `n.set`, `$create` and `TestSet` out of the scan; `(?!\s*:)`
    #: keeps a map key (`{use: 1}`) out of it. Both are false-positive classes we met.
    body = "|".join(WRITE_TOKENS)
    return re.compile(rf"(?<![\w.$])({body})\b(?!\s*:)", re.IGNORECASE)


_TOKENS = _token_pattern()
_CALL_SUBQUERY = re.compile(r"(?<![\w.])CALL\s*(\([^)]*\))?\s*\{", re.IGNORECASE)
_CALL_PROCEDURE = re.compile(r"(?<![\w.])CALL\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_NAMESPACED_CALL = re.compile(r"(?<![\w.$])([A-Za-z_]\w*(?:\.\w+)+)\s*\(")
_ESCAPE = re.compile(r"\\[uU]")


def _procedure_allowed(name: str) -> bool:
    lowered = name.lower()
    if not any(lowered.startswith(p) for p in ALLOWED_CALL_PREFIXES):
        return False
    tail = lowered.rsplit(".", 1)[-1]
    return not any(verb in tail for verb in FORBIDDEN_CALL_VERBS)


def _check_calls(text: str, cypher: str) -> None:
    if _CALL_SUBQUERY.search(text):
        raise GuardError("call-subquery", "CALL {} subquery", cypher)
    names = [m.group(1) for m in _CALL_PROCEDURE.finditer(text)]
    names += [m.group(1) for m in _NAMESPACED_CALL.finditer(text)]
    for name in names:
        if name.split(".", 1)[0].lower() not in CALL_ROOTS:
            continue
        if not _procedure_allowed(name):
            raise GuardError("procedure-not-allowed", name, cypher)


def check(cypher: str) -> str:
    """Refuse anything that is not a single read-only statement. Returns the masked query.

    Static analysis only — no database. `run_cypher` runs this before it opens a session,
    which is what makes "the guard never let a write reach the driver" a testable claim.
    """
    if not cypher or not cypher.strip():
        raise GuardError("empty", "no query", cypher)
    if len(cypher) > MAX_CYPHER_CHARS:
        raise GuardError("too-long", f"{len(cypher)} characters", cypher)
    if _ESCAPE.search(cypher):
        raise GuardError("escape-sequence", "\\u escape", cypher)

    masked = sanitize(cypher)
    text = _normalized(masked)
    if ";" in text.rstrip().rstrip(";"):
        raise GuardError("multiple-statements", "more than one statement", cypher)

    # Calls are checked before the token scan so that `CALL { CREATE … }` is reported as
    # what it is — a subquery — rather than as the `CREATE` inside it. Both are refusals;
    # the report counts reasons, so the more specific one is the more useful one.
    _check_calls(text, cypher)
    hit = _TOKENS.search(text)
    if hit:
        raise GuardError("write-verb", hit.group(1).upper(), cypher)
    return masked


# --------------------------------------------------------------------------- limits

_LIMIT = re.compile(r"(?<![\w.])LIMIT\b", re.IGNORECASE)
_RETURN = re.compile(r"(?<![\w.])RETURN\b", re.IGNORECASE)


def inject_limit(cypher: str, limit: int = DEFAULT_LIMIT) -> tuple[str, bool]:
    """Append `LIMIT n` unless the *final* `RETURN` already has one. Returns (cypher, injected).

    The `LIMIT` that matters is the one on the answer. `MATCH … WITH n LIMIT 1 MATCH …
    RETURN …` bounds an intermediate row set and can still return the whole graph, so the
    search starts at the last `RETURN` rather than anywhere in the query.
    """
    body = cypher.rstrip().rstrip(";").rstrip()
    masked = sanitize(body)
    start = 0
    for match in _RETURN.finditer(masked):
        start = match.end()
    if _LIMIT.search(masked, start):
        return body, False
    joiner = "\n" if "\n" in body else " "
    return f"{body}{joiner}LIMIT {int(limit)}", True


@dataclass
class PreparedCypher:
    """What the static guard produced: the query that will run, and what it changed."""

    cypher: str
    original: str
    limit: int
    limit_injected: bool


def prepare(cypher: str, limit: int = DEFAULT_LIMIT) -> PreparedCypher:
    check(cypher)
    final, injected = inject_limit(cypher, limit)
    return PreparedCypher(cypher=final, original=cypher, limit=limit, limit_injected=injected)


# --------------------------------------------------------------------------- EXPLAIN


def plan_write_operators(plan: dict[str, Any] | None) -> list[str]:
    """Every write operator in an `EXPLAIN` plan tree, in plan order.

    The plan is the parser's opinion, which beats the scanner's: `EXPLAIN CALL { CREATE
    (n) } RETURN 1` plans a `SubqueryForeach` over a `Create` no matter how the text was
    spelled, obfuscated or commented.
    """
    found: list[str] = []
    stack = [plan] if plan else []
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        name = str(node.get("operatorType", "")).split("@", 1)[0]
        if any(name.startswith(p) for p in WRITE_OPERATOR_PREFIXES):
            found.append(name)
        children = node.get("children") or []
        stack.extend(list(children))
    return found


def explain(ctx: Any, cypher: str, params: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    """`EXPLAIN` the query under READ routing and hand back the plan tree."""
    query = Query(f"EXPLAIN {cypher}", timeout=timeout_s)
    try:
        return ctx.client.explain(query, **params) or {}
    except (CypherSyntaxError, ClientError) as exc:
        message = str(exc).splitlines()[0]
        if "read access mode" in message.lower():
            raise GuardError("read-mode", message, cypher) from exc
        raise GuardError("invalid-cypher", message, cypher) from exc


# --------------------------------------------------------------------------- execution


def _row_item(row: dict[str, Any], index: int) -> Item:
    """One returned row as an `Item`, so S4 packs and cites like every other strategy.

    A row is not a node — `GraphClient.read()` gives back exactly the columns the query
    projected — so the `kind` is `Row` and the key is whatever the query called an
    identifier. Nothing is invented: `props` is the row itself.
    """
    key = ""
    for name in ("key", "id", "name", "component", "sha", "test", "person", "title"):
        value = row.get(name)
        if isinstance(value, str) and value:
            key = value
            break
    if not key:
        key = next(
            (str(v) for v in row.values() if isinstance(v, str | int | float) and str(v)),
            f"row-{index + 1}",
        )
    title = row.get("title") or row.get("name") or ""
    snippet = ", ".join(f"{k}={v}" for k, v in row.items() if v is not None)
    return Item(
        kind="Row",
        key=str(key),
        title=clip(str(title), 160),
        snippet=clip(snippet),
        score=1.0 / (index + 1),
        props={k: v for k, v in row.items() if k != "embedding"},
    )


def _log_rejection(
    question: str,
    error: GuardError,
    *,
    cypher: str,
    latency_ms: int,
    mode: str,
    log_path: Path | None,
) -> None:
    record = retrieve_log.entry(
        question,
        Result(strategy="s4", latency_ms=latency_ms, cypher_used=[cypher]),
        mode=mode,
        extra={"rejected": error.reason, "hint": error.hint, "detail": error.detail},
    )
    retrieve_log.append(record, log_path)


def run_cypher(
    ctx: Any,
    cypher: str,
    params: dict[str, Any] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    limit: int = DEFAULT_LIMIT,
    *,
    question: str = "",
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Guard, `EXPLAIN`, run under READ with a server-side timeout, return the envelope.

    Raises `GuardError` — with `reason` and `hint` — for anything refused, and logs the
    refusal to the same trace file successful calls use.
    """
    timer = Timer()
    params = dict(params or {})
    asked = question or cypher

    def _reject(err: GuardError) -> GuardError:
        if log:
            _log_rejection(
                asked,
                err,
                cypher=cypher,
                latency_ms=timer.ms,
                mode=log_mode,
                log_path=log_path,
            )
        return err

    try:
        prepared = prepare(cypher, limit)
    except GuardError as err:
        raise _reject(err) from None

    try:
        plan = explain(ctx, prepared.cypher, params, timeout_s)
    except GuardError as err:
        raise _reject(err) from None
    writes = plan_write_operators(plan)
    if writes:
        raise _reject(GuardError("write-plan", ", ".join(sorted(set(writes))), cypher)) from None

    started = time.perf_counter()
    try:
        rows = ctx.read(Query(prepared.cypher, timeout=timeout_s), **params)
    except Neo4jError as exc:
        message = str(exc).splitlines()[0]
        code = getattr(exc, "code", "") or ""
        # 2026.06 reports the transaction timeout as
        # `Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration` with a
        # message about termination — match both spellings and both places.
        signal = f"{code} {message}".lower()
        if any(w in signal for w in ("timedout", "timeout", "timed out", "terminated")):
            raise _reject(GuardError("timeout", message, cypher)) from None
        if "read access mode" in message.lower():
            raise _reject(GuardError("read-mode", message, cypher)) from None
        raise _reject(GuardError("invalid-cypher", message, cypher)) from None
    query_ms = int((time.perf_counter() - started) * 1000)

    truncated_rows = rows[:limit]
    items = [_row_item(row, i) for i, row in enumerate(truncated_rows)]
    trace = dict(route or {})
    trace.update(
        {
            "tool": "run_cypher",
            "rows": len(rows),
            "limit_injected": prepared.limit_injected,
            "query_ms": query_ms,
        }
    )
    return finish(
        "s4",
        items,
        question=asked,
        timer=timer,
        cypher_used=[prepared.cypher],
        route=trace,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )
