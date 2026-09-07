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


def _related_work_items(ctx: RetrieveContext, label: str, key: str, depth: int):
    rels = "|".join(f"`{r}`" for r in IMPACT_RELS)
    depth = max(1, min(int(depth), 2))
    cypher = (
        f"MATCH (a:{ctx.label(label)}) WHERE {key_case('a', ctx.prefix)} = $key\n"
        "CALL (a) {\n"
        f"  MATCH (a)-[rels:{rels}*1..{depth}]-(w:{ctx.label('WorkItem')})\n"
        "  RETURN DISTINCT w, size(rels) AS hops ORDER BY hops LIMIT $limit\n"
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
        "WITH t, k, r, x ORDER BY r.at DESC\n"
        "WITH t, k, head(collect({status: r.status, at: toString(r.at), execution: x.key,\n"
        "  reason: r.reason})) AS last_run\n"
        "RETURN t.key AS test, t.title AS title, t.status AS status, k AS covers, last_run,\n"
        "  coalesce(t.synthetic, false) AS synthetic LIMIT $limit"
    )
    return ctx.read(cypher, keys=keys, limit=MAX_TESTS), cypher


def _documents(ctx: RetrieveContext, label: str, key: str, depth: int):
    rels = "|".join(f"`{r}`" for r in IMPACT_RELS)
    depth = max(1, min(int(depth), 2))
    cypher = (
        f"MATCH (a:{ctx.label(label)}) WHERE {key_case('a', ctx.prefix)} = $key\n"
        "CALL (a) {\n"
        f"  MATCH (a)-[rels:{rels}*1..{depth}]-(d:{ctx.label('Document')})\n"
        "  RETURN DISTINCT d, size(rels) AS hops ORDER BY hops LIMIT $limit\n"
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
        "WITH collect(DISTINCT f.path)[..$files] AS paths, collect(DISTINCT c.sha) AS fixes\n"
        f"MATCH (o:{ctx.label('Commit')})-[:TOUCHES]->(f2:{ctx.label('File')})\n"
        "WHERE f2.path IN paths\n"
        "WITH paths, fixes, o, count(DISTINCT f2) AS shared_files\n"
        "RETURN paths, fixes, o.sha AS sha, o.message AS message,\n"
        "  toString(o.at) AS at, shared_files, coalesce(o.synthetic, false) AS synthetic\n"
        "ORDER BY shared_files DESC, o.at DESC LIMIT $limit"
    )
    rows = ctx.read(cypher, keys=keys, files=MAX_FILES, limit=MAX_COMMITS)
    paths = rows[0]["paths"] if rows else []
    return rows, paths, cypher


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
    """Open issues, tests with their last run, documents and commits on the same files."""
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
    items: list[Item] = [
        Item(
            kind="Row",
            key=f"impact:{anchor_key}",
            title=f"impact of {anchor_key}",
            snippet=(
                f"{len(open_items)} open work items, {len(tests)} tests, {len(docs)} documents, "
                f"{len(commits)} commits over {len(paths)} shared files (depth {depth})"
            ),
            score=1.0,
            provenance=as_provenance(evidence.get(anchor_key, []), anchor_key),
            props={
                "anchor": anchor_key,
                "anchor_label": label,
                "depth": depth,
                "open_work_items": len(open_items),
                "related_work_items": len(related),
                "tests": len(tests),
                "documents": len(docs),
                "commits": len(commits),
                "files": paths[:MAX_FILES],
            },
        )
    ]

    for index, r in enumerate(open_items):
        item = to_item("WorkItem", r, round(0.9 - index / 200, 4))
        item.props.update({"hops": r["hops"], "category": "open_issue"})
        item.provenance.extend(as_provenance(evidence.get(r["key"], []), r["key"]))
        items.append(item)

    for index, t in enumerate(tests):
        last = t["last_run"] or {}
        items.append(
            Item(
                kind="WorkItem",
                key=t["test"],
                title=t["title"] or t["test"],
                snippet=(
                    f"covers {t['covers']}; last run "
                    + (
                        f"{last.get('status')} at {last.get('at')}"
                        if last.get("status")
                        else "never"
                    )
                ),
                score=round(0.8 - index / 200, 4),
                props={
                    "category": "test",
                    "covers": t["covers"],
                    "last_run_status": last.get("status"),
                    "last_run_at": last.get("at"),
                    "last_run_execution": last.get("execution"),
                    "synthetic": t["synthetic"],
                },
                provenance=[Provenance(source=last.get("execution") or t["test"])],
            )
        )

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
                provenance=[Provenance(source=c["sha"], quote=clip(c["message"], 160))],
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
