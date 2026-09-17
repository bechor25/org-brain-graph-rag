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
   an attack. Every dotted callable — `CALL apoc.…` and `apoc.…()` alike, because
   `apoc.cypher.runFirstColumn` is a *function* that runs arbitrary Cypher — must be in
   the allowlist or be one of Cypher's own value functions, whatever its root: refusing
   only the roots we had heard of is what let `CALL n10s.rdf.import.fetch(…)` past this
   layer. The scan reads backticked names unmasked, because the parser does.
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

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from neo4j import Query
from neo4j.exceptions import Neo4jError

from brain.retrieve import log as retrieve_log
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.pack import SNIPPET_CHARS, clip
from brain.retrieve.types import Item, Provenance, Result

#: Spec §4.5. 10 seconds is the *server's* limit on the transaction, not a client wait.
DEFAULT_TIMEOUT_S = 10.0
#: Rows an S4 answer may carry before the packer even sees it.
DEFAULT_LIMIT = 100
#: A query longer than this is not a question, it is a payload.
MAX_CYPHER_CHARS = 4000
#: What one returned row may carry in `props` before its widest columns are dropped.
#: The 4k ceiling cannot live only in the packer: `RETURN d.body_md` is a *single* item of
#: 220,855 characters (66,289 tokens, measured on this corpus), and by the time `pack()`
#: sees one item the only move left is to drop it — which is to answer nothing. 2,000
#: characters is ~573 tokens at the corpus's measured 3.49 chars/token, so six such rows
#: still fit the budget with room for the envelope.
MAX_ROW_PROP_CHARS = 2000
#: A row whose query projected no named identifier is keyed by its first scalar; that
#: value can itself be a document body, so the key is clipped too.
MAX_ROW_KEY_CHARS = 120

#: Clause keywords that can only appear in a query that changes something. `USE` is here
#: because it selects a database (`USE system` is the door to the admin surface), and
#: `LOAD`/`CSV`/`PERIODIC` because `LOAD CSV` reads the server's filesystem.
WRITE_TOKENS: tuple[str, ...] = (
    "CREATE",
    # GQL's spelling of CREATE, accepted by 2026.06. It was missing here until the
    # `EXPLAIN` layer caught it (`write-plan`) — which is the whole argument for having a
    # third layer, and the reason `guard_cases.PLAN_BLOCKED` keeps that proof.
    "INSERT",
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

#: Cypher's own *value* functions, which are namespaced but are not procedures: pure,
#: server-local, side-effect free. They are the only dotted names outside the allowlist
#: that may be called, and the list is closed rather than a set of "known dangerous roots":
#: scanning only roots we recognised is what let `CALL n10s.rdf.import.fetch(...)` through
#: the static layer (measured), because `n10s` was in nobody's list.
BUILTIN_FUNCTION_ROOTS: frozenset[str] = frozenset(
    {
        "date",
        "datetime",
        "localdatetime",
        "localtime",
        "time",
        "duration",
        "point",
        "vector",
    }
)

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

#: The only two administrative listings a reader may ask for. `SHOW SETTINGS`,
#: `TRANSACTIONS`, `USERS`, `PRIVILEGES`, `PROCEDURES` and `FUNCTIONS` describe the server
#: and its people rather than the graph, and two of them (`TRANSACTIONS`, `SETTINGS`) are
#: reconnaissance for an attack on it. An allowlist rather than a deny-list, so a command
#: this version does not have yet is refused instead of discovered.
ALLOWED_SHOW: frozenset[str] = frozenset({"INDEX", "INDEXES", "CONSTRAINT", "CONSTRAINTS"})

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
    "show-not-allowed": "only SHOW INDEXES and SHOW CONSTRAINTS are readable; ask get_schema "
    "for labels, relationship types and properties",
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


def sanitize(cypher: str, *, keep_backticks: bool = False) -> str:
    """Mask comments, string literals and backticked identifiers, preserving every offset.

    Same-length masking is the point: the deny-list scan, the `LIMIT` detection and the
    statement-separator check all work on this string and every index still refers to the
    same character of the original, so an error can quote the query the caller sent.

    `keep_backticks=True` leaves backticked identifiers legible (comments and string
    literals are still masked). The procedure scan needs that: masking ``CALL
    `apoc`.`cypher`.`runFirstColumn`(…)`` hides the very name the scan is looking for,
    while the parser reads backticks as quoting and calls the procedure regardless.

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
            if ch == "`" and keep_backticks:
                j = cypher.find("`", i + 1)
                if j == -1:
                    raise GuardError("unbalanced-quote", f"unterminated ` at offset {i}", cypher)
                out.append(cypher[i : j + 1])
                i = j + 1
                continue
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
_SHOW = re.compile(r"(?<![\w.$])SHOW\s+(\w+)", re.IGNORECASE)


def _procedure_allowed(name: str) -> bool:
    """Allowlist first, then the verb deny-list, then Cypher's own value functions.

    Nothing is skipped for having an unfamiliar root any more: a dotted callable is either
    in `ALLOWED_CALL_PREFIXES`, or a built-in value function (`duration.between`,
    `vector.similarity.cosine`), or refused. `n10s.rdf.import.fetch`, `genai.vector.encode`
    and the next plugin nobody has installed yet all land in the third case by default.
    """
    lowered = name.lower()
    if any(lowered.startswith(p) for p in ALLOWED_CALL_PREFIXES):
        tail = lowered.rsplit(".", 1)[-1]
        return not any(verb in tail for verb in FORBIDDEN_CALL_VERBS)
    return lowered.split(".", 1)[0] in BUILTIN_FUNCTION_ROOTS


def _call_text(cypher: str) -> str:
    """The query as the *parser* reads names: comments and literals gone, backticks removed."""
    return _normalized(sanitize(cypher, keep_backticks=True)).replace("`", "")


def _check_calls(text: str, cypher: str) -> None:
    if _CALL_SUBQUERY.search(text):
        raise GuardError("call-subquery", "CALL {} subquery", cypher)
    # A bare `CALL foo` has no namespace, and every Neo4j procedure has one — so it is
    # either a typo or a probe. Either way it is not something we can allowlist.
    names = [m.group(1) for m in _CALL_PROCEDURE.finditer(text)]
    names += [m.group(1) for m in _NAMESPACED_CALL.finditer(text)]
    for name in names:
        if not _procedure_allowed(name):
            raise GuardError("procedure-not-allowed", name, cypher)


def _check_show(text: str, cypher: str) -> None:
    for match in _SHOW.finditer(text):
        word = match.group(1).upper()
        if word not in ALLOWED_SHOW:
            raise GuardError("show-not-allowed", f"SHOW {word}", cypher)


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
    _check_calls(_call_text(cypher), cypher)
    hit = _TOKENS.search(text)
    if hit:
        raise GuardError("write-verb", hit.group(1).upper(), cypher)
    # After the token scan, so `USE system SHOW DATABASES` is still reported as the `USE`
    # it leads with — the more dangerous half of that query.
    _check_show(text, cypher)
    return masked


# --------------------------------------------------------------------------- limits

_LIMIT = re.compile(r"(?<![\w.])LIMIT\b", re.IGNORECASE)
_RETURN = re.compile(r"(?<![\w.])RETURN\b", re.IGNORECASE)
_UNION = re.compile(r"(?<![\w.])UNION\b", re.IGNORECASE)
_FINISH = re.compile(r"(?<![\w.])FINISH\s*$", re.IGNORECASE)


def inject_limit(cypher: str, limit: int = DEFAULT_LIMIT) -> tuple[str, bool]:
    """Append `LIMIT n` unless the *final* `RETURN` already has one. Returns (cypher, injected).

    The `LIMIT` that matters is the one on the answer. `MATCH … WITH n LIMIT 1 MATCH …
    RETURN …` bounds an intermediate row set and can still return the whole graph, so the
    search starts at the last `RETURN` rather than anywhere in the query.

    Two shapes are left alone because appending to them is a syntax error, not a limit:
    a `UNION` (the clause belongs to each part, not to the union) and a query that ends in
    `FINISH` (which returns no rows at all). They are not unbounded as a result —
    `run_cypher` still cuts the row list at `limit` before building items — but the bound
    is applied in the client rather than by the server, and `limit_injected` says so.
    """
    body = cypher.rstrip().rstrip(";").rstrip()
    masked = sanitize(body)
    if _UNION.search(masked) or _FINISH.search(masked):
        return body, False
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
    """`EXPLAIN` the query under READ routing and hand back the plan tree.

    Catches `Neo4jError`, not only `ClientError`/`CypherSyntaxError`: planning is a
    server-side operation and it can fail as a `DatabaseError` (an internal planner
    failure) or a `TransientError` (the transaction timing out while planning a query
    designed to be expensive to plan). Those are the interesting cases, and letting them
    escape would turn a refusal into a stack trace — an MCP tool owes the caller the same
    `{error, hint}` pair whichever layer said no.
    """
    query = Query(f"EXPLAIN {cypher}", timeout=timeout_s)
    try:
        return ctx.client.explain(query, **params) or {}
    except Neo4jError as exc:
        message = str(exc).splitlines()[0]
        signal = f"{getattr(exc, 'code', '') or ''} {message}".lower()
        if "read access mode" in signal:
            raise GuardError("read-mode", message, cypher) from exc
        if any(w in signal for w in ("timedout", "timeout", "timed out", "terminated")):
            raise GuardError("timeout", message, cypher) from exc
        raise GuardError("invalid-cypher", message, cypher) from exc


# --------------------------------------------------------------------------- execution


def _clip_deep(value: Any, limit: int = SNIPPET_CHARS) -> tuple[Any, bool]:
    """Clip every string inside a row value to one snippet. Returns (value, was_clipped).

    Recursive because a row column is not always a scalar: `collect(c.text)` is a list of
    document texts and `collect({quote: …})` a list of maps, and a ceiling that only looked
    at top-level strings would miss both.
    """
    if isinstance(value, str):
        out = clip(value, limit)
        return out, out != value
    if isinstance(value, list):
        pairs = [_clip_deep(v, limit) for v in value]
        return [v for v, _ in pairs], any(c for _, c in pairs)
    if isinstance(value, dict):
        pairs = {k: _clip_deep(v, limit) for k, v in value.items()}
        return {k: v for k, (v, _) in pairs.items()}, any(c for _, c in pairs.values())
    return value, False


def _row_props(row: dict[str, Any], budget: int = MAX_ROW_PROP_CHARS) -> dict[str, Any]:
    """The row, clipped to something an agent can afford to read.

    Two ceilings, because one is not enough. Every string is clipped to `SNIPPET_CHARS`,
    the same budget every other strategy's snippet lives under — but a query can project
    fifty columns, so the row as a whole is capped too, widest column first. What went is
    named in `_clipped`: an answer that quietly lost the column the question was about is
    worse than a long one.
    """
    props: dict[str, Any] = {}
    clipped: list[str] = []
    for name, value in row.items():
        if name == "embedding":
            continue
        props[name], was_clipped = _clip_deep(value)
        if was_clipped:
            clipped.append(name)
    sizes = {k: len(json.dumps(v, ensure_ascii=False, default=str)) for k, v in props.items()}
    total = sum(sizes.values())
    while total > budget and sizes:
        widest = max(sizes, key=lambda k: (sizes[k], k))
        del props[widest]
        total -= sizes.pop(widest)
        if widest not in clipped:
            clipped.append(widest)
    if clipped:
        props["_clipped"] = sorted(clipped)
    return props


def _row_item(row: dict[str, Any], index: int) -> Item:
    """One returned row as an `Item`, so S4 packs and cites like every other strategy.

    A row is not a node — `GraphClient.read()` gives back exactly the columns the query
    projected — so the `kind` is `Row` and the key is whatever the query called an
    identifier. Nothing is invented; `props` is the row itself, clipped (`_row_props`).

    The provenance is `source_kind="row"`: a row has no chunk id and never will, and its
    audit trail is `Result.cypher_used` — paste the query, get the row back. The query is
    *referenced* rather than copied into every item, because a hundred copies of the same
    400 characters is the answer's token budget spent on saying one thing a hundred times.
    Without this entry a cite-check counts an aggregation as unciteable and the honest
    answer to "which components have failing tests" scores zero.
    """
    props = _row_props(row)
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
    snippet = ", ".join(f"{k}={v}" for k, v in props.items() if v is not None and k != "_clipped")
    return Item(
        kind="Row",
        key=clip(str(key), MAX_ROW_KEY_CHARS),
        title=clip(str(title), 160),
        snippet=clip(snippet),
        score=1.0 / (index + 1),
        props=props,
        provenance=[Provenance(source=f"row {index + 1} of cypher_used", source_kind="row")],
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
    # Two cuts the packer cannot see, because both happen before it is handed the items: a
    # wide column clipped inside the row, and rows past `limit` dropped when the query's own
    # `LIMIT` was larger than ours. `truncated` answers "is this everything?", and here the
    # answer is no.
    clipped = [i.key for i in items if "_clipped" in i.props]
    cut_rows = len(rows) > len(truncated_rows)
    trace = dict(route or {})
    trace.update(
        {
            "tool": "run_cypher",
            "rows": len(rows),
            "rows_returned": len(truncated_rows),
            "clipped_rows": len(clipped),
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
        already_truncated=bool(clipped) or cut_rows,
    )
