"""`Commit`, `PullRequest`, `File` — the delivery half of the graph.

`Change` is one canonical type and two labels, because the two answer different questions:
a commit touched files at a moment, a pull request is the unit people reviewed. The edge
between them comes from `Change.pr` — the `(#21175)` a squashed merge leaves in the
subject, which says *this commit is that pull request* — and never from `refs`, where a
`#N` is only somebody talking about a pull request.

`RESOLVES` and `IMPLEMENTS_KIP` are the traceability this whole POC exists to test: 4,074
of the 6,107 commits name a Jira key in their message, and that text is the only thing
connecting the code to the decision.
"""

from __future__ import annotations

from typing import Any

from brain.canon.models import Change
from brain.graph.context import GraphContext
from brain.graph.corpus import Corpus
from brain.graph.cypher import node_merge
from brain.graph.loaders import emit
from brain.graph.mapping import pr_number
from brain.graph.provenance import SyntheticProvenance

COMMIT: tuple[str, str] = ("Commit", "sha")
PULL_REQUEST: tuple[str, str] = ("PullRequest", "number")
FILE: tuple[str, str] = ("File", "path")
PERSON: tuple[str, str] = ("Person", "id")
WORK_ITEM: tuple[str, str] = ("WorkItem", "key")
DOCUMENT: tuple[str, str] = ("Document", "key")


def _base_props(c: Change) -> dict[str, Any]:
    return {
        "id": c.id,
        "message": c.message,
        "author_name": c.author_name,
        "author_email": c.author_email,
        "at": c.at,
        "synthetic": c.synthetic,
        "raw_url": c.raw_url,
    }


def commit_rows(changes: list[Change], prov: SyntheticProvenance) -> list[dict[str, Any]]:
    rows = []
    for c in changes:
        if c.kind != "commit":
            continue
        props = _base_props(c) | {"sha": c.id, "pr": c.pr, "file_count": len(c.files)}
        props.update(prov.props(c.id))
        rows.append({"key": c.id, "props": props})
    return rows


def pr_rows(changes: list[Change], prov: SyntheticProvenance) -> tuple[list[dict[str, Any]], int]:
    rows = []
    unusable = 0
    for c in changes:
        if c.kind != "pr":
            continue
        number = pr_number(c.id)
        if number is None:
            unusable += 1
            continue
        props = _base_props(c) | {"number": number}
        props.update(prov.props(c.id))
        rows.append({"key": number, "props": props})
    return rows, unusable


def file_rows(changes: list[Change]) -> list[dict[str, Any]]:
    paths = {p for c in changes for p in c.files}
    return [{"key": p, "props": {"path": p}} for p in sorted(paths)]


def load_nodes(ctx: GraphContext, corpus: Corpus, prov: SyntheticProvenance) -> dict[str, Any]:
    commits = commit_rows(corpus.changes, prov)
    prs, unusable = pr_rows(corpus.changes, prov)
    files = file_rows(corpus.changes)
    ctx.write_rows(node_merge(ctx, "Commit", "sha"), commits)
    ctx.write_rows(node_merge(ctx, "PullRequest", "number"), prs)
    ctx.write_rows(node_merge(ctx, "File", "path"), files)
    return {
        "commits": len(commits),
        "pull_requests": len(prs),
        "pull_requests_unusable_id": unusable,
        "files": len(files),
        "stamped": sum(1 for r in (*commits, *prs) if r["props"].get("batch_id")),
    }


def load_edges(ctx: GraphContext, corpus: Corpus) -> dict[str, Any]:
    authored_commit: list[dict[str, Any]] = []
    authored_pr: list[dict[str, Any]] = []
    touches: list[dict[str, Any]] = []
    has_commit: list[dict[str, Any]] = []
    resolves: list[dict[str, Any]] = []
    implements: list[dict[str, Any]] = []
    stats = {
        "authors_unknown": 0,
        "has_commit_dangling": 0,
        "resolves_dangling": 0,
        "implements_kip_dangling": 0,
    }
    seen_touches: set[tuple[str, str]] = set()

    for c in corpus.changes:
        person = corpus.person("git", c.author_email)
        if c.author_email and not person:
            stats["authors_unknown"] += 1
        if c.kind == "pr":
            number = pr_number(c.id)
            if number is not None and person:
                authored_pr.append({"src": person, "dst": number})
            continue

        if person:
            authored_commit.append({"src": person, "dst": c.id})
        for path in c.files:
            k = (c.id, path)
            if k in seen_touches:
                continue
            seen_touches.add(k)
            touches.append({"src": c.id, "dst": path})

        number = pr_number(c.pr)
        if c.pr:
            if number is not None and number in corpus.pr_numbers:
                has_commit.append({"src": number, "dst": c.id})
            else:
                stats["has_commit_dangling"] += 1

        for ref in c.refs:
            if ref.kind == "issue":
                if ref.key in corpus.workitem_keys:
                    resolves.append({"src": c.id, "dst": ref.key})
                else:
                    stats["resolves_dangling"] += 1
            elif ref.kind == "kip":
                if corpus.has_document(ref.key):
                    implements.append({"src": c.id, "dst": ref.key})
                else:
                    stats["implements_kip_dangling"] += 1

    return {
        **stats,
        "edges": {
            "AUTHORED": (
                emit(ctx, PERSON, "AUTHORED", COMMIT, authored_commit)
                + emit(ctx, PERSON, "AUTHORED", PULL_REQUEST, authored_pr)
            ),
            "TOUCHES": emit(ctx, COMMIT, "TOUCHES", FILE, touches),
            "HAS_COMMIT": emit(ctx, PULL_REQUEST, "HAS_COMMIT", COMMIT, has_commit),
            "RESOLVES": emit(ctx, COMMIT, "RESOLVES", WORK_ITEM, resolves),
            "IMPLEMENTS_KIP": emit(ctx, COMMIT, "IMPLEMENTS_KIP", DOCUMENT, implements),
        },
    }
