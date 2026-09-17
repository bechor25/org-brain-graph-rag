"""`impact(key_or_name, depth)` — the blast radius of a change, in four categories.

"If we change `GroupCoordinator`, what is affected?" is the question this POC exists to
answer, and it is the one no single index can. The answer needs the issue tracker (what is
still open around it), the test layer (what covers it, and did it pass), the docs (which
KIP describes it) and version control (which other commits touch the same files). Four
sources, one node, one call.

Each category is its own query rather than one generic expansion, and that is deliberate:
"the last execution status of the tests that cover this" is not a traversal, it is an
`ORDER BY r.at DESC` inside the traversal, and a generic walker cannot express it.

The file-neighbourhood query is the one with teeth. `TOUCHES` has 48,036 edges, so the
file set is capped before it is used — a commit that touched 300 files (a formatting run)
would otherwise make every commit in the repository "impacted", which is true and useless.

The test layer is two layers when the anchor is a `Component`, and that is the Plan 2 gate
finding. Asking "which tests cover the forty related work items this traversal surfaced" is
the right question about an issue and the wrong one about a component: `impact("clients")`
reported `0 tests` because none of the forty items it happened to surface had a covering
test, while 284 tests cover `clients` work items through `IN_COMPONENT`. Coverage of a
component is not a property of the items that are still open, so it gets its own query
(`tests_on_component`) and its own exact count, and the old layer stays as
`tests_on_open_items`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.evidence import as_provenance, own_chunks
from brain.retrieve.lookup import resolve_key
from brain.retrieve.nodes import key_case, to_item
from brain.retrieve.pack import clip
from brain.retrieve.types import Item, Provenance, Result

#: Statuses that mean "no longer in play". Everything else counts as open — including the
#: ADO vocabulary (`New`, `Active`, `Ready`) the synthetic layer introduced.
CLOSED_STATUSES: frozenset[str] = frozenset(
    {"Resolved", "Closed", "Done", "Completed", "Removed", "Cancelled", "Rejected"}
)
#: The edges a change propagates along.
IMPACT_RELS: tuple[str, ...] = (
    "REFERENCES",
    "LINKS_TO",
    "IN_COMPONENT",
    "IMPLEMENTS",
    "IMPLEMENTS_KIP",
    "MENTIONS",
    "DEPENDS_ON",
    "PARENT_OF",
)
MAX_RELATED = 40
MAX_TESTS = 20
MAX_DOCS = 10
MAX_FILES = 20
MAX_COMMITS = 10
#: How many failing component tests are named. The count is exact and unbounded; the list
#: is not, because a component with 90 failures would be the whole answer.
MAX_FAILING_TESTS = 10

#: `HAS_RUN.status` spellings that mean the run failed. Two, because the synthetic layer
#: writes `FAIL` and nothing guarantees a future connector agrees.
FAILING_STATUSES: frozenset[str] = frozenset({"FAIL", "FAILED"})
#: What a test with no recorded run is bucketed as in the histogram. Not `null`: "81 tests
#: were never run" is an answer, and a missing key reads as "nothing to say".
NEVER_RAN = "never"


def _related_work_items(ctx: RetrieveContext, label: str, key: str, depth: int):
    rels = "|".join(f"`{r}`" for r in IMPACT_RELS)
    depth = max(1, min(int(depth), 2))
    cypher = (
        f"MATCH (a:{ctx.label(label)}) WHERE {key_case('a', ctx.prefix)} = $key\n"
        "CALL (a) {\n"
        f"  MATCH (a)-[rels:{rels}*1..{depth}]-(w:{ctx.label('WorkItem')})\n"
        "  RETURN DISTINCT w, size(rels) AS hops ORDER BY hops, w.key LIMIT $limit\n"
        "}\n"
        "RETURN w.key AS key, w.title AS title, w.type AS type, w.status AS status,\n"
        "  w.resolution AS resolution, coalesce(w.synthetic, false) AS synthetic, hops\n"
        "ORDER BY hops, w.key"
    )
    return ctx.read(cypher, key=key, limit=MAX_RELATED), cypher


def _tests_with_last_run(ctx: RetrieveContext, keys: list[str]):
    if not keys:
        return [], ""
    cypher = (
        "UNWIND $keys AS k\n"
        f"MATCH (t:{ctx.label('Test')})-[:TESTS]->(w:{ctx.label('WorkItem')} {{`key`: k}})\n"
        f"OPTIONAL MATCH (x:{ctx.label('TestExecution')})-[r:HAS_RUN]->(t)\n"
        # Two total orders, not one: `r.at DESC, x.key DESC` decides which run is *the*
        # last run when two executions share a timestamp, and `t.key` decides which twenty
        # tests survive the cap. Either left open is an answer that changes between runs.
        "WITH t, k, r, x ORDER BY r.at DESC, x.key DESC\n"
        "WITH t, k, head(collect({status: r.status, at: toString(r.at), execution: x.key,\n"
        "  reason: r.reason})) AS last_run\n"
        "RETURN t.key AS test, t.title AS title, t.status AS status, k AS covers, last_run,\n"
        "  coalesce(t.synthetic, false) AS synthetic ORDER BY t.key LIMIT $limit"
    )
    return ctx.read(cypher, keys=keys, limit=MAX_TESTS), cypher


def _tests_on_component(ctx: RetrieveContext, name: str):
    """Every test covering a work item in this component, grouped by its last run's status.

    Keyed on the component name and never on `$keys`: this is deliberately *not* a slice of
    the related-work-items list, because that list is capped at forty and the count has to
    be exact — `284 tests, 12 failing` is the claim, and a claim made from a sample of a
    sample is the bug this query exists to fix.

    Only the failing list is capped, per status bucket. Cutting per bucket and then again
    globally is safe: both cuts use the same order and the global one is no larger, so the
    ten failures named here are the ten newest failures on the component.
    """
    cypher = (
        f"MATCH (k:{ctx.label('Component')} {{`name`: $name}})\n"
        f"MATCH (t:{ctx.label('Test')})-[:TESTS]->"
        f"(:{ctx.label('WorkItem')})-[:IN_COMPONENT]->(k)\n"
        # A test that covers two items of the same component is one test, not two.
        "WITH DISTINCT t\n"
        f"OPTIONAL MATCH (x:{ctx.label('TestExecution')})-[r:HAS_RUN]->(t)\n"
        # The same total order the open-items layer uses: `r.at DESC, x.key DESC` decides
        # which run is *the* last run when two executions share a timestamp.
        "WITH t, r, x ORDER BY r.at DESC, x.key DESC\n"
        "WITH t, head(collect({status: r.status, at: toString(r.at), execution: x.key,\n"
        "  reason: r.reason})) AS last_run\n"
        f"WITH t, last_run, coalesce(last_run.status, '{NEVER_RAN}') AS last_status\n"
        # Ordered before it is cut: newest failure first, `t.key` to break the tie, so the
        # failures an answer names are the same ones on every run.
        "ORDER BY coalesce(last_run.at, '') DESC, t.key\n"
        "WITH last_status, count(*) AS n, collect({test: t.key, title: t.title,\n"
        "  status: t.status, last_run: last_run,\n"
        "  synthetic: coalesce(t.synthetic, false)})[..$limit] AS sample\n"
        "RETURN last_status AS status, n,\n"
        "  CASE WHEN toUpper(last_status) IN $failing THEN sample ELSE [] END AS failing\n"
        "ORDER BY last_status"
    )
    rows = ctx.read(cypher, name=name, limit=MAX_FAILING_TESTS, failing=sorted(FAILING_STATUSES))
    return rows, cypher


def _component_test_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`{total, by_status, failing_total, failing}` from the grouped rows."""
    failing: list[dict[str, Any]] = [t for r in rows for t in (r["failing"] or [])]
    # The buckets arrive grouped by status, so the merged list has to be put back into the
    # one order the Cypher used. Two stable sorts rather than one key, because a string
    # cannot be reversed inside a sort key: least significant first, then `at` descending.
    failing.sort(key=lambda t: t.get("test") or "")
    failing.sort(key=lambda t: (t.get("last_run") or {}).get("at") or "", reverse=True)
    return {
        "total": sum(int(r["n"]) for r in rows),
        "by_status": {str(r["status"]): int(r["n"]) for r in rows},
        "failing_total": sum(
            int(r["n"]) for r in rows if str(r["status"]).upper() in FAILING_STATUSES
        ),
        "failing": failing[:MAX_FAILING_TESTS],
    }


def _documents(ctx: RetrieveContext, label: str, key: str, depth: int):
    rels = "|".join(f"`{r}`" for r in IMPACT_RELS)
    depth = max(1, min(int(depth), 2))
    cypher = (
        f"MATCH (a:{ctx.label(label)}) WHERE {key_case('a', ctx.prefix)} = $key\n"
        "CALL (a) {\n"
        f"  MATCH (a)-[rels:{rels}*1..{depth}]-(d:{ctx.label('Document')})\n"
        "  RETURN DISTINCT d, size(rels) AS hops ORDER BY hops, d.key LIMIT $limit\n"
        "}\n"
        "RETURN d.key AS key, d.title AS title, d.kind AS kind, hops,\n"
        "  coalesce(d.synthetic, false) AS synthetic ORDER BY hops, d.key"
    )
    return ctx.read(cypher, key=key, limit=MAX_DOCS), cypher


def _commits_on_the_same_files(ctx: RetrieveContext, keys: list[str]):
    """Commits that resolved these items, then the other commits touching the same files."""
    if not keys:
        return [], [], ""
    cypher = (
        "UNWIND $keys AS k\n"
        f"MATCH (c:{ctx.label('Commit')})-[:RESOLVES]->"
        f"(w:{ctx.label('WorkItem')} {{`key`: k}})\n"
        f"MATCH (c)-[:TOUCHES]->(f:{ctx.label('File')})\n"
        # The cap is what keeps a 300-file formatting commit from making the whole
        # repository "impacted" — but a slice of an unordered collect is a different
        # twenty files on every run, so the rows are ordered before they are cut.
        "WITH DISTINCT f.path AS path, c.sha AS sha\n"
        "ORDER BY path, sha\n"
        "WITH collect(DISTINCT path)[..$files] AS paths, collect(DISTINCT sha) AS fixes\n"
        f"MATCH (o:{ctx.label('Commit')})-[:TOUCHES]->(f2:{ctx.label('File')})\n"
        "WHERE f2.path IN paths\n"
        "WITH paths, fixes, o, count(DISTINCT f2) AS shared_files\n"
        "RETURN paths, fixes, o.sha AS sha, o.message AS message,\n"
        "  toString(o.at) AS at, shared_files, coalesce(o.synthetic, false) AS synthetic\n"
        "ORDER BY shared_files DESC, o.at DESC, o.sha LIMIT $limit"
    )
    rows = ctx.read(cypher, keys=keys, files=MAX_FILES, limit=MAX_COMMITS)
    paths = rows[0]["paths"] if rows else []
    return rows, paths, cypher


def _test_item(
    row: dict[str, Any],
    score: float,
    *,
    category: str = "test",
    scope: str | None = None,
) -> Item:
    """One test and its last execution, in whichever layer asked for it.

    `kind="Row"`, not `WorkItem`: an `XT-` test is not an issue, and calling it one puts a
    key in the answer that `lookup` cannot resolve and the packer protects as if it were the
    only work item in the answer. Its provenance is the execution — `source_kind="row"`,
    because the evidence is a tuple the graph recorded, not words anyone wrote about it.

    `scope` is why this test is in the answer, and it differs by layer: "covers KAFKA-100"
    for a test reached through an open item, "in component clients" for one reached through
    `IN_COMPONENT`. Leaving it implicit is how the two layers became indistinguishable.
    """
    last = row["last_run"] or {}
    ran = bool(last.get("status"))
    props: dict[str, Any] = {
        "category": category,
        "test_status": row["status"],
        "last_run_status": last.get("status"),
        "last_run_at": last.get("at"),
        "last_run_execution": last.get("execution"),
        "last_run_reason": last.get("reason"),
        "synthetic": row["synthetic"],
    }
    if row.get("covers"):
        props["covers"] = row["covers"]
    if scope is None:
        scope = f"covers {row.get('covers')}"
    return Item(
        kind="Row",
        key=row["test"],
        title=row["title"] or row["test"],
        snippet=(
            f"{scope}; last run "
            + (f"{last.get('status')} at {last.get('at')}" if ran else "never")
        ),
        score=score,
        props=props,
        provenance=[
            Provenance(
                source=last.get("execution") or row["test"],
                source_kind="row",
                quote=f"{last.get('status')} at {last.get('at')}" if ran else "never run",
            )
        ],
    )


def _component_tests_item(name: str, counts: dict[str, Any], score: float) -> Item:
    """The component's whole test layer in one row: how many, and what their last run said.

    A `Row` like the tests under it, and for the same reason: `tests:clients` is not a key
    `lookup` resolves. Its provenance is `source_kind="row"` — the evidence is a count the
    graph produced, and the query that produced it is in `Result.cypher_used`.
    """
    histogram = ", ".join(f"{n} {status}" for status, n in counts["by_status"].items())
    summary = f"{counts['total']} tests cover work items in {name}; last run: " + (
        histogram or "no tests"
    )
    return Item(
        kind="Row",
        key=f"tests:{name}",
        title=f"tests covering {name} work items",
        snippet=summary,
        score=score,
        props={
            "category": "tests_on_component",
            "component": name,
            "tests": counts["total"],
            "failing": counts["failing_total"],
            "by_last_run_status": counts["by_status"],
            # The count above is exact; this says how much of it the answer names.
            "failing_listed": len(counts["failing"]),
        },
        provenance=[Provenance(source=name, source_kind="row", quote=summary)],
    )


def impact(
    ctx: RetrieveContext,
    key_or_name: str,
    depth: int = 2,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Open issues, tests with their last run, documents and commits on the same files.

    On a `Component` the tests come in two layers: every test covering the component's work
    items (counted whole), and the tests covering the open items this traversal surfaced.
    """
    timer = Timer()
    label, row, resolve_cypher = resolve_key(ctx, key_or_name)
    anchor_key = row["key"]
    cyphers = [resolve_cypher]

    related, cypher = _related_work_items(ctx, label, anchor_key, depth)
    cyphers.append(cypher)
    work_keys = [r["key"] for r in related if r["key"]]
    if label == "WorkItem":
        work_keys = [anchor_key, *work_keys]

    tests, cypher = _tests_with_last_run(ctx, work_keys[:MAX_RELATED])
    if cypher:
        cyphers.append(cypher)
    # Only for a component: for a work item or a document, "the tests that cover the items
    # around this one" is the whole of the question, and a second layer would be noise.
    component_tests: dict[str, Any] | None = None
    if label == "Component":
        rows, cypher = _tests_on_component(ctx, anchor_key)
        cyphers.append(cypher)
        component_tests = _component_test_counts(rows)
    docs, cypher = _documents(ctx, label, anchor_key, depth)
    cyphers.append(cypher)
    commits, paths, cypher = _commits_on_the_same_files(ctx, work_keys[:MAX_RELATED])
    if cypher:
        cyphers.append(cypher)

    open_items = [r for r in related if (r["status"] or "") not in CLOSED_STATUSES]
    # A traversal produces no quote. Hand back the text each node is made of instead, so
    # every item an answer can cite carries a chunk id a reviewer can look up.
    evidence, cypher = own_chunks(
        ctx, [anchor_key, *[r["key"] for r in open_items], *[d["key"] for d in docs]]
    )
    if cypher:
        cyphers.append(cypher)
    # `tests_on_open_items`, never a bare `tests`: the gate read `tests: 0` on a component
    # with 284 covering tests as "no tests exist", and a name that does not say which
    # population it counted is what made that reading reasonable.
    test_line = f"{len(tests)} tests on those items"
    props: dict[str, Any] = {
        "anchor": anchor_key,
        "anchor_label": label,
        "depth": depth,
        "open_work_items": len(open_items),
        "related_work_items": len(related),
        "tests_on_open_items": len(tests),
        "documents": len(docs),
        "commits": len(commits),
        "files": paths[:MAX_FILES],
    }
    if component_tests is not None:
        test_line = (
            f"{component_tests['total']} tests on the component "
            f"({component_tests['failing_total']} failing), {test_line}"
        )
        props.update(
            {
                "tests_on_component": component_tests["total"],
                "tests_on_component_failing": component_tests["failing_total"],
                "tests_on_component_by_status": component_tests["by_status"],
            }
        )
    items: list[Item] = [
        Item(
            kind="Row",
            key=f"impact:{anchor_key}",
            title=f"impact of {anchor_key}",
            snippet=(
                f"{len(open_items)} open work items, {test_line}, {len(docs)} documents, "
                f"{len(commits)} commits over {len(paths)} shared files (depth {depth})"
            ),
            score=1.0,
            provenance=as_provenance(evidence.get(anchor_key, []), anchor_key),
            props=props,
        )
    ]
    if component_tests is not None:
        # Above every open issue (0.9) and below the head: a component whose tests are
        # failing is the first thing the answer to "what breaks" should carry, and the
        # packer decides what survives the budget by score.
        items.append(_component_tests_item(anchor_key, component_tests, 0.95))
        for index, t in enumerate(component_tests["failing"]):
            items.append(
                _test_item(
                    t,
                    round(0.85 - index / 200, 4),
                    category="failing_test_on_component",
                    scope=f"in component {anchor_key}",
                )
            )

    for index, r in enumerate(open_items):
        item = to_item("WorkItem", r, round(0.9 - index / 200, 4))
        item.props.update({"hops": r["hops"], "category": "open_issue"})
        item.provenance.extend(as_provenance(evidence.get(r["key"], []), r["key"]))
        items.append(item)

    for index, t in enumerate(tests):
        items.append(_test_item(t, round(0.8 - index / 200, 4)))

    for index, d in enumerate(docs):
        item = to_item("Document", d, round(0.7 - index / 200, 4))
        item.props.update({"hops": d["hops"], "category": "document"})
        item.provenance.extend(as_provenance(evidence.get(d["key"], []), d["key"]))
        items.append(item)

    for index, c in enumerate(commits):
        items.append(
            Item(
                kind="Change",
                key=c["sha"],
                title=clip((c["message"] or "").splitlines()[0] if c["message"] else c["sha"], 120),
                snippet=clip(c["message"]),
                score=round(0.6 - index / 200, 4),
                props={
                    "category": "commit_on_same_files",
                    "shared_files": c["shared_files"],
                    "at": c["at"],
                    "fixes_anchor": c["sha"] in (c["fixes"] or []),
                    "synthetic": c["synthetic"],
                },
                provenance=[
                    Provenance(
                        source=c["sha"],
                        source_kind="node-text",
                        quote=clip(c["message"], 160),
                    )
                ],
            )
        )

    return finish(
        "s3",
        items,
        question=f"impact({key_or_name}, depth={depth})",
        timer=timer,
        cypher_used=cyphers,
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )
