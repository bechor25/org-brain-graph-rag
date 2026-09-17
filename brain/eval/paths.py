"""Real paths out of the live graph, and real rows out of the synthetic truth file.

A question is only worth asking if the corpus can answer it, so nothing here is written by
hand: every path is a subgraph the graph actually holds, selected by a superlative the
corpus nominated (the work item the most tests cover, the KIP that rejects the most
alternatives, the component with the most open bugs). That is the same rule
`brain/retrieve/competency.py` follows, for the same reason — a hand-picked key is a key
whose answer someone already knew.

Three things this module is deliberate about.

**Two phases per shape.** `select` finds candidate `path_key`s with a deterministic
`ORDER BY <richness> DESC, <key>`, and `detail` fetches one path by key. A single
mega-query would be faster and unreadable; these run a dozen times per build.

**Selection is seeded, not arbitrary.** Taking the top `n` by richness would hand every
shape the same famous KIPs the competency set already anchors on. So `select` returns a
pool (`POOL_FACTOR` × what is needed), and a `random.Random(seed)` picks from it — the same
seed gives the same paths, and the seed is recorded in MANIFEST.json.

**Anchors and answers are declared per shape, not guessed.** `anchor_roles` are the nodes a
question may name (its subject); `answer_roles` are the nodes it must not (its answer).
That declaration is what lets `brain eval questions merge` check leakage mechanically
instead of by taste — see `brain/eval/questions_report.py`.

The synthetic shapes (`gold_source: "truth"`) read `data/canonical/synthetic_truth.json`,
which is truth *by construction*: the generator wrote the rename, the stale state and the
text-only link on purpose, so the gold answer is known without trusting the extraction.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.pack import clip

#: Snippet budget, per the Task 1 contract. Long enough to quote, short enough that a
#: batch of twenty paths still fits the 40 KB an agent's reader handles.
SNIPPET_CHARS = 400
#: Chunks offered per parent node, and per path overall.
SNIPPETS_PER_PARENT = 2
SNIPPETS_PER_PATH = 8
#: How many candidates `select` returns per path wanted, before the seeded pick.
POOL_FACTOR = 6
DEFAULT_SEED = 2026

#: Statuses `brain/retrieve/competency.py` treats as closed, repeated here so the two
#: modules select "open bugs" the same way.
CLOSED_STATUSES: tuple[str, ...] = ("Resolved", "Closed", "Done", "Completed", "Removed")

#: Every label a shape's Cypher may name as `{Label}`; `_fill` namespaces them.
LABELS: tuple[str, ...] = (
    "Test",
    "TestExecution",
    "WorkItem",
    "Document",
    "Commit",
    "Component",
    "Entity",
    "Person",
    "StatusChange",
    "Version",
    "Bug",
    "Chunk",
    "Community",
)

TRUTH_PATH = Path("data/canonical/synthetic_truth.json")


class PathError(RuntimeError):
    """The graph cannot produce the paths this build needs."""


# ------------------------------------------------------------------------------- model


def node(role: str, label: str, key: Any, title: str = "", **props: Any) -> dict[str, Any]:
    """One node as the forger sees it. `role` is what the shape calls it in the path."""
    return {
        "role": role,
        "label": label,
        "key": "" if key is None else str(key),
        "title": clip(str(title or ""), 160),
        "props": {k: v for k, v in props.items() if v not in (None, "", [], {})},
    }


def edge(rel: str, source: str, target: str, **props: Any) -> dict[str, Any]:
    return {
        "type": rel,
        "from": str(source),
        "to": str(target),
        "props": {k: v for k, v in props.items() if v not in (None, "", [], {})},
    }


@dataclass
class PathSample:
    """One subgraph, ready to ship to `question-forger`."""

    shape: str
    question_type: str
    gold_source: str
    expected_strategy: str
    path_shape: str
    path_key: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    #: Keys the question may name.
    anchors: list[str]
    #: Keys the question must not name — they are the answer.
    answers: list[str]
    facts: dict[str, Any] = field(default_factory=dict)
    snippets: list[dict[str, Any]] = field(default_factory=list)
    truth: list[dict[str, Any]] = field(default_factory=list)
    #: Filled by the build: what the forger must write from this path.
    asks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def path_id(self) -> str:
        return f"{self.shape}:{self.path_key}"

    @property
    def spare(self) -> bool:
        return not self.asks

    def keys(self) -> set[str]:
        """Every citable id this path offers: node keys, chunk ids, truth excerpt ids."""
        out = {n["key"] for n in self.nodes if n["key"]}
        out |= {s["chunk_id"] for s in self.snippets}
        out |= {t["id"] for t in self.truth}
        return out

    def parent_keys(self) -> list[str]:
        """Node keys whose `Chunk`s are worth offering as snippets, in path order."""
        seen: dict[str, None] = {}
        for n in self.nodes:
            if n["label"] in CHUNK_PARENT_LABELS and n["key"]:
                seen.setdefault(n["key"], None)
        return list(seen)

    def evidence_chunk_ids(self) -> list[str]:
        """`Entity.evidence_chunk_ids` carried on the nodes — an entity's own provenance."""
        seen: dict[str, None] = {}
        for n in self.nodes:
            for cid in n["props"].get("evidence_chunk_ids") or []:
                seen.setdefault(str(cid), None)
        return list(seen)

    def context(self) -> dict[str, Any]:
        """The batch-input form. Field names are the question-forger's contract."""
        return {
            "path_id": self.path_id,
            "type": self.question_type,
            "shape": self.shape,
            "path_shape": self.path_shape,
            "gold_source": self.gold_source,
            "expected_strategy": self.expected_strategy,
            "anchors": list(self.anchors),
            "answers": list(self.answers),
            "facts": dict(self.facts),
            "nodes": list(self.nodes),
            "edges": list(self.edges),
            "snippets": list(self.snippets),
            "truth": list(self.truth),
            "asks": list(self.asks),
            "spare": self.spare,
        }


#: Labels that carry `Chunk` children worth quoting (measured: WorkItem, Document, Commit,
#: Test and the WorkItem subtype labels all do).
CHUNK_PARENT_LABELS: frozenset[str] = frozenset(
    {"WorkItem", "Document", "Commit", "Test", "TestExecution"}
)


@dataclass(frozen=True)
class Shape:
    """One path shape: how to find it, how to read it, and what it may not give away."""

    name: str
    question_type: str
    gold_source: str
    expected_strategy: str
    path_shape: str
    #: role -> whether a question may name that node. Roles not listed are context.
    anchor_roles: tuple[str, ...]
    answer_roles: tuple[str, ...]
    describe: str
    #: Cypher returning candidate rows with a `path_key` column, richest first.
    select: str = ""
    #: Cypher taking `$key` and returning exactly one raw row.
    detail: str = ""
    #: Raw row -> (nodes, edges, facts). Pure; unit-tested without a database.
    build: Callable[[dict[str, Any]], tuple[list[dict], list[dict], dict]] | None = None
    #: Truth shapes only: the `synthetic_truth.json` section they draw rows from.
    truth_section: str = ""
    #: Truth shapes only: row -> the node keys to look up in the graph.
    truth_keys: Callable[[dict[str, Any]], list[str]] | None = None
    #: Truth shapes only: (row, looked-up nodes) -> (nodes, edges, facts).
    truth_build: Callable[[dict, list[dict]], tuple[list[dict], list[dict], dict]] | None = None


def _fill(cypher: str, ctx: RetrieveContext) -> str:
    out = cypher
    for label in LABELS:
        out = out.replace("{" + label + "}", ctx.label(label))
    return out


def _roles(nodes: Sequence[dict[str, Any]], roles: Sequence[str]) -> list[str]:
    wanted = set(roles)
    seen: dict[str, None] = {}
    for n in nodes:
        if n["role"] in wanted and n["key"]:
            seen.setdefault(n["key"], None)
    return list(seen)


# ------------------------------------------------------------------------ graph shapes


def _build_test_fix(row: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    w = row["work_item"]
    nodes = [
        node(
            "work_item",
            "WorkItem",
            w["key"],
            w.get("title"),
            status=w.get("status"),
            type=w.get("type"),
            resolution=w.get("resolution"),
            source=w.get("source"),
        )
    ]
    edges: list[dict] = []
    for t in row.get("tests") or []:
        runs = [r for r in (t.get("runs") or []) if r.get("execution")]
        nodes.append(
            node(
                "test",
                "Test",
                t["key"],
                t.get("title"),
                source=t.get("source"),
                runs=[
                    f"{r['execution']} {r.get('status')} @ {(r.get('at') or '')[:10]}" for r in runs
                ],
            )
        )
        edges.append(edge("TESTS", t["key"], w["key"]))
        for r in runs:
            edges.append(
                edge("HAS_RUN", r["execution"], t["key"], status=r.get("status"), at=r.get("at"))
            )
    for c in row.get("commits") or []:
        nodes.append(
            node(
                "commit",
                "Commit",
                c["sha"],
                c.get("message"),
                at=c.get("at"),
                author=c.get("author"),
            )
        )
        edges.append(edge("RESOLVES", c["sha"], w["key"]))
    facts = {
        "tests": len(row.get("tests") or []),
        "commits": len(row.get("commits") or []),
        "run_statuses": sorted(
            {
                str(r.get("status"))
                for t in (row.get("tests") or [])
                for r in (t.get("runs") or [])
                if r.get("status")
            }
        ),
    }
    return nodes, edges, facts


def _build_story_kip(row: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    d = row["document"]
    nodes = [
        node(
            "document",
            "Document",
            d["key"],
            d.get("title"),
            kind=d.get("kind"),
            space=d.get("space"),
        )
    ]
    edges: list[dict] = []
    for w in row.get("items") or []:
        nodes.append(
            node(
                "work_item",
                "WorkItem",
                w["key"],
                w.get("title"),
                status=w.get("status"),
                type=w.get("type"),
                source=w.get("source"),
                area=w.get("area"),
            )
        )
        edges.append(edge("REFERENCES", w["key"], d["key"]))
    facts = {
        "referencing_items": len(row.get("items") or []),
        "sources": sorted(
            {str(w.get("source")) for w in (row.get("items") or []) if w.get("source")}
        ),
    }
    return nodes, edges, facts


def _build_decision_rejects(row: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    d = row["document"]
    nodes = [node("document", "Document", d["key"], d.get("title"), kind=d.get("kind"))]
    edges: list[dict] = []
    for e in row.get("decisions") or []:
        nodes.append(
            node(
                "decision",
                "Entity",
                e["id"],
                e.get("name"),
                kind=e.get("kind"),
                description=clip(e.get("description"), SNIPPET_CHARS),
                evidence_chunk_ids=(e.get("evidence_chunk_ids") or [])[:2],
            )
        )
        edges.append(edge("DECIDES", d["key"], e["id"]))
    for a in row.get("alternatives") or []:
        # The REJECTS edge's own provenance first: the entity's `evidence_chunk_ids` say
        # where the alternative was *named*, the edge's say where it was *turned down*.
        # "What was rejected and why" is answered by the second chunk, not the first.
        grounds = [str(x) for x in (a.get("grounds_chunk_ids") or [])][:2]
        named_in = [str(x) for x in (a.get("evidence_chunk_ids") or [])][:2]
        nodes.append(
            node(
                "alternative",
                "Entity",
                a["id"],
                a.get("name"),
                kind=a.get("kind"),
                description=clip(a.get("description"), SNIPPET_CHARS),
                evidence_chunk_ids=[*grounds, *[c for c in named_in if c not in grounds]][:3],
                grounds_chunk_ids=grounds,
            )
        )
        edges.append(
            edge("REJECTS", d["key"], a["id"], evidence_chunk_ids=grounds, note=a.get("note"))
        )
    facts = {
        "decisions_shown": len(row.get("decisions") or []),
        "rejected_alternatives_shown": len(row.get("alternatives") or []),
        "shown_cap": 4,
        "note": "Each arm is capped at four by the shape's Cypher; these are not totals.",
    }
    return nodes, edges, facts


def _build_motivation(row: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    d = row["document"]
    nodes = [node("document", "Document", d["key"], d.get("title"), kind=d.get("kind"))]
    edges: list[dict] = []
    for link in row.get("links") or []:
        src, prob = link["source"], link["problem"]
        nodes.append(
            node(
                "driver",
                "Entity",
                src["id"],
                src.get("name"),
                kind=src.get("kind"),
                description=clip(src.get("description"), SNIPPET_CHARS),
                evidence_chunk_ids=(src.get("evidence_chunk_ids") or [])[:2],
            )
        )
        nodes.append(
            node(
                "problem",
                "Entity",
                prob["id"],
                prob.get("name"),
                kind=prob.get("kind"),
                description=clip(prob.get("description"), SNIPPET_CHARS),
                evidence_chunk_ids=(prob.get("evidence_chunk_ids") or [])[:2],
            )
        )
        edges.append(edge("DECIDES", d["key"], src["id"]))
        edges.append(edge("MOTIVATED_BY", src["id"], prob["id"]))
    facts = {"problems": len({link["problem"]["id"] for link in (row.get("links") or [])})}
    return nodes, edges, facts


def _build_ownership_timeline(row: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    w = row["work_item"]
    nodes = [
        node(
            "work_item",
            "WorkItem",
            w["key"],
            w.get("title"),
            status=w.get("status"),
            created=w.get("created"),
            source=w.get("source"),
        )
    ]
    edges: list[dict] = []
    for a in row.get("assignments") or []:
        nodes.append(
            node("assignee", "Person", a["person"], a.get("display"), source=a.get("source"))
        )
        edges.append(
            edge(
                "ASSIGNED_TO",
                w["key"],
                a["person"],
                valid_from=a.get("valid_from"),
                valid_to=a.get("valid_to"),
            )
        )
    for s in row.get("changes") or []:
        nodes.append(
            node(
                "status_change",
                "StatusChange",
                s["id"],
                f"{s.get('field')}: {s.get('from')} → {s.get('to')}",
                field=s.get("field"),
                at=s.get("at"),
                by=s.get("by"),
            )
        )
        edges.append(edge("HAS_CHANGE", w["key"], s["id"], at=s.get("at")))
    stamps = sorted(str(s.get("at") or "") for s in (row.get("changes") or []) if s.get("at"))
    facts = {
        "assignments": len(row.get("assignments") or []),
        "status_changes": len(row.get("changes") or []),
        "first_change": stamps[0][:10] if stamps else "",
        "last_change": stamps[-1][:10] if stamps else "",
        "midpoint_date": stamps[len(stamps) // 2][:10] if stamps else "",
    }
    return nodes, edges, facts


def _build_release_window(row: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    comp = row["component"]
    nodes = [node("component", "Component", comp, comp)]
    edges: list[dict] = []
    per_version: dict[str, int] = {}
    for v in row.get("versions") or []:
        nodes.append(node("version", "Version", v["name"], v["name"], items=v.get("items")))
        per_version[v["name"]] = int(v.get("items") or 0)
        for w in v.get("work_items") or []:
            nodes.append(
                node(
                    "released_item",
                    "WorkItem",
                    w["key"],
                    w.get("title"),
                    status=w.get("status"),
                    type=w.get("type"),
                    resolution=w.get("resolution"),
                )
            )
            edges.append(edge("IN_COMPONENT", w["key"], comp))
            edges.append(edge("FIX_VERSION", w["key"], v["name"]))
    facts = {"component": comp, "items_per_version": per_version}
    return nodes, edges, facts


def _build_community_theme(row: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    c = row["community"]
    nodes = [
        node(
            "community",
            "Community",
            c["id"],
            c.get("title"),
            level=c.get("level"),
            rank=c.get("rank"),
            summary=clip(c.get("summary"), SNIPPET_CHARS),
            findings=[clip(f, 200) for f in (c.get("finding_statements") or [])[:4]],
            # The chunks the community REPORT cites, not the members'. A global question is
            # asked about the theme, so the text a gold answer quotes has to be the text the
            # theme was written from — and a community whose members happen to carry no
            # chunks would otherwise ship with `snippets: []`.
            evidence_chunk_ids=[str(x) for x in (c.get("evidence_chunk_ids") or [])[:4]],
        )
    ]
    edges: list[dict] = []
    for m in row.get("members") or []:
        nodes.append(
            node(
                "member",
                m["label"],
                m["key"],
                m.get("title"),
                kind=m.get("kind"),
                status=m.get("status"),
                degree=m.get("degree"),
            )
        )
        edges.append(edge("IN_COMMUNITY", m["key"], c["id"]))
    facts = {
        "size": c.get("size"),
        "members_shown": len(row.get("members") or []),
        "level": c.get("level"),
        "rank": c.get("rank"),
    }
    return nodes, edges, facts


def _build_blast_radius(row: dict[str, Any]) -> tuple[list[dict], list[dict], dict]:
    comp = row["component"]
    nodes = [node("component", "Component", comp, comp)]
    edges: list[dict] = []
    for w in row.get("items") or []:
        nodes.append(
            node(
                "open_item",
                "WorkItem",
                w["key"],
                w.get("title"),
                status=w.get("status"),
                type=w.get("type"),
                priority=w.get("priority"),
            )
        )
        edges.append(edge("IN_COMPONENT", w["key"], comp))
        for t in w.get("tests") or []:
            nodes.append(node("covering_test", "Test", t["key"], t.get("title")))
            edges.append(edge("TESTS", t["key"], w["key"]))
        for d in w.get("documents") or []:
            nodes.append(
                node("referenced_doc", "Document", d["key"], d.get("title"), kind=d.get("kind"))
            )
            edges.append(edge("REFERENCES", w["key"], d["key"]))
    facts = {
        # `<thing>_shown` vs `<thing>_total`, the convention `community_theme` already uses
        # for `members_shown` vs `size`. A forger read the old `open_items` as the
        # component's real total and said so in its batch notes; a name is cheaper than a
        # note nobody reads.
        "open_items_shown": len(row.get("items") or []),
        "open_items_total": row.get("open_items_total"),
        "covering_tests": sum(len(w.get("tests") or []) for w in (row.get("items") or [])),
        "referenced_documents": sum(
            len(w.get("documents") or []) for w in (row.get("items") or [])
        ),
    }
    return nodes, edges, facts


# --------------------------------------------------------------------- truth builders


def _lookup(nodes: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    for n in nodes:
        if str(n.get("key")) == key:
            return n
    return {}


def _truth_node(found: dict[str, Any], role: str, key: str) -> dict[str, Any]:
    return node(
        role,
        found.get("label") or "WorkItem",
        key,
        found.get("title") or "",
        status=found.get("status"),
        type=found.get("type"),
        source=found.get("source"),
        in_graph=bool(found),
    )


def _truth_stale_state(row: dict, found: list[dict]) -> tuple[list[dict], list[dict], dict]:
    ado, jira = row["ado_key"], row["jira_key"]
    nodes = [
        _truth_node(_lookup(found, ado), "mirror_item", ado),
        _truth_node(_lookup(found, jira), "source_item", jira),
    ]
    edges = [edge("REFERENCES", ado, jira, note="the ADO item mirrors the Jira issue")]
    facts = {
        "ado_status_recorded": row.get("ado_status"),
        "jira_status_recorded": row.get("jira_status"),
        "note": "The ADO mirror was left behind on purpose; the Jira status is the real one.",
    }
    return nodes, edges, facts


def _truth_renamed_test(row: dict, found: list[dict]) -> tuple[list[dict], list[dict], dict]:
    test, jira = row["test_key"], row["jira_key"]
    nodes = [
        _truth_node(_lookup(found, test), "test", test),
        _truth_node(_lookup(found, jira), "work_item", jira),
    ]
    edges = [edge("TESTS", test, jira)]
    facts = {
        "test_phrase": row.get("test_phrase"),
        "jira_phrase": row.get("jira_phrase"),
        "note": "The same thing is named one way in the test and another way in the issue.",
    }
    return nodes, edges, facts


def _truth_text_only_link(row: dict, found: list[dict]) -> tuple[list[dict], list[dict], dict]:
    src, dst = row["from_key"], row["to_key"]
    nodes = [
        _truth_node(_lookup(found, src), "mirror_item", src),
        _truth_node(_lookup(found, dst), "linked_item", dst),
    ]
    edges = [
        edge("REFERENCES", src, dst, note="stated in the text only — no structured link field")
    ]
    facts = {"note": "The link exists in prose. Only a reader of the text can find it."}
    return nodes, edges, facts


def _truth_duplicate_test(row: dict, found: list[dict]) -> tuple[list[dict], list[dict], dict]:
    a, b = row["a"], row["b"]
    nodes = [
        _truth_node(_lookup(found, a), "test", a),
        _truth_node(_lookup(found, b), "duplicate", b),
    ]
    edges = [edge("VARIANT_OF", b, a, note="same steps, different key")]
    facts = {"note": "Two test keys describe the same check."}
    return nodes, edges, facts


# ------------------------------------------------------------------------------ shapes

SHAPES: tuple[Shape, ...] = (
    Shape(
        name="test_fix",
        question_type="traceability",
        gold_source="graph",
        expected_strategy="s3",
        path_shape=(
            "Test-[:TESTS]->WorkItem<-[:RESOLVES]-Commit  (+ TestExecution-[:HAS_RUN]->Test)"
        ),
        anchor_roles=("work_item",),
        answer_roles=("test", "commit"),
        describe="what covers a fixed issue, and what closed it",
        select=(
            "MATCH (t:{Test})-[:TESTS]->(w:{WorkItem})<-[:RESOLVES]-(c:{Commit}) "
            "WITH w, count(DISTINCT t) AS tests, count(DISTINCT c) AS commits "
            "ORDER BY tests + commits DESC, tests DESC, w.key "
            "LIMIT $pool RETURN w.key AS path_key, tests + commits AS richness"
        ),
        detail=(
            "MATCH (w:{WorkItem} {key: $key}) "
            "CALL (w) { MATCH (t:{Test})-[:TESTS]->(w) "
            "  OPTIONAL MATCH (x:{TestExecution})-[r:HAS_RUN]->(t) "
            "  WITH t, collect({execution: x.key, status: r.status, "
            "                 at: toString(r.at)})[0..3] AS runs "
            "  ORDER BY t.key LIMIT 6 "
            "  RETURN collect({key: t.key, title: t.title, source: t.source, "
            "                  runs: runs}) AS tests } "
            "CALL (w) { MATCH (c:{Commit})-[:RESOLVES]->(w) WITH c ORDER BY c.at, c.sha LIMIT 4 "
            "  RETURN collect({sha: c.sha, message: c.message, at: toString(c.at), "
            "                  author: c.author_name}) AS commits } "
            "RETURN {key: w.key, title: w.title, status: w.status, type: w.type, "
            "        resolution: w.resolution, source: w.source} AS work_item, tests, commits"
        ),
        build=_build_test_fix,
    ),
    Shape(
        name="story_kip",
        question_type="traceability",
        gold_source="graph",
        expected_strategy="s3",
        path_shape="WorkItem-[:REFERENCES]->Document",
        anchor_roles=("document",),
        answer_roles=("work_item",),
        describe="which work items deliver a KIP",
        select=(
            "MATCH (w:{WorkItem})-[:REFERENCES]->(d:{Document}) WHERE d.kind = 'KIP' "
            "WITH d, count(DISTINCT w) AS items ORDER BY items DESC, d.key "
            "LIMIT $pool RETURN d.key AS path_key, items AS richness"
        ),
        detail=(
            "MATCH (d:{Document} {key: $key}) "
            "CALL (d) { MATCH (w:{WorkItem})-[:REFERENCES]->(d) WITH w ORDER BY w.key LIMIT 8 "
            "  RETURN collect({key: w.key, title: w.title, status: w.status, type: w.type, "
            "                  source: w.source}) AS items } "
            "RETURN {key: d.key, title: d.title, kind: d.kind, space: d.space} AS document, items"
        ),
        build=_build_story_kip,
    ),
    Shape(
        name="renamed_test",
        question_type="traceability",
        gold_source="truth",
        expected_strategy="s1",
        path_shape="Test-[:TESTS]->WorkItem, with the two different names the truth file records",
        anchor_roles=("test",),
        answer_roles=("work_item",),
        describe="the same thing named one way in the test and another way in the issue",
        truth_section="renames",
        truth_keys=lambda r: [r["test_key"], r["jira_key"]],
        truth_build=_truth_renamed_test,
    ),
    Shape(
        name="blast_radius",
        question_type="impact",
        gold_source="graph",
        expected_strategy="s2",
        path_shape=(
            "Component<-[:IN_COMPONENT]-WorkItem(open) (<-[:TESTS]-Test, -[:REFERENCES]->Document)"
        ),
        anchor_roles=("component",),
        answer_roles=("open_item", "covering_test", "referenced_doc"),
        describe="what a change to a component would touch",
        select=(
            "MATCH (w:{WorkItem})-[:IN_COMPONENT]->(k:{Component}) "
            "WHERE NOT coalesce(w.status, '') IN $closed "
            "WITH k, count(w) AS open_items ORDER BY open_items DESC, k.name "
            "LIMIT $pool RETURN k.name AS path_key, open_items AS richness"
        ),
        detail=(
            "MATCH (k:{Component} {name: $key}) "
            "CALL (k) { MATCH (w:{WorkItem})-[:IN_COMPONENT]->(k) "
            "  WHERE NOT coalesce(w.status, '') IN $closed RETURN count(w) AS total } "
            "CALL (k) { MATCH (w:{WorkItem})-[:IN_COMPONENT]->(k) "
            "  WHERE NOT coalesce(w.status, '') IN $closed WITH w ORDER BY w.key LIMIT 6 "
            "  OPTIONAL MATCH (t:{Test})-[:TESTS]->(w) "
            "  WITH w, collect(DISTINCT {key: t.key, title: t.title})[0..3] AS tests "
            "  OPTIONAL MATCH (w)-[:REFERENCES]->(d:{Document}) "
            "  WITH w, tests, "
            "    collect(DISTINCT {key: d.key, title: d.title, kind: d.kind})[0..3] AS docs "
            "  RETURN collect({key: w.key, title: w.title, status: w.status, type: w.type, "
            "                  priority: w.priority, tests: [t IN tests WHERE t.key IS NOT NULL], "
            "                  documents: [d IN docs WHERE d.key IS NOT NULL]}) AS items } "
            "RETURN k.name AS component, items, total AS open_items_total"
        ),
        build=_build_blast_radius,
    ),
    Shape(
        name="text_only_link",
        question_type="impact",
        gold_source="truth",
        expected_strategy="s1",
        path_shape=(
            "WorkItem(ADO mirror) → WorkItem(Jira), a link the prose states and no field does"
        ),
        anchor_roles=("mirror_item",),
        answer_roles=("linked_item",),
        describe="a dependency only a reader of the text can find",
        truth_section="text_only_links",
        truth_keys=lambda r: [r["from_key"], r["to_key"]],
        truth_build=_truth_text_only_link,
    ),
    Shape(
        name="duplicate_test",
        question_type="impact",
        gold_source="truth",
        expected_strategy="s1",
        path_shape="Test ~ Test — two keys for the same check, recorded in the truth file",
        anchor_roles=("test",),
        answer_roles=("duplicate",),
        describe="the duplicate test a change would have to update twice",
        truth_section="duplicate_tests",
        truth_keys=lambda r: [r["a"], r["b"]],
        truth_build=_truth_duplicate_test,
    ),
    Shape(
        name="decision_rejects",
        question_type="rationale",
        gold_source="graph",
        expected_strategy="s3",
        path_shape=(
            "Document-[:DECIDES]->Entity(Decision) and Document-[:REJECTS]->Entity(Alternative)"
        ),
        anchor_roles=("document",),
        answer_roles=("decision", "alternative"),
        describe="what a KIP chose and what it turned down",
        select=(
            "MATCH (d:{Document})-[:DECIDES]->(:{Entity}) "
            "WHERE d.kind = 'KIP' AND EXISTS { (d)-[:REJECTS]->(:{Entity}) } "
            "WITH d, count(*) AS decisions "
            "CALL (d) { MATCH (d)-[:REJECTS]->(a:{Entity}) RETURN count(a) AS rejects } "
            "WITH d, decisions, rejects ORDER BY decisions + rejects DESC, d.key "
            "LIMIT $pool RETURN d.key AS path_key, decisions + rejects AS richness"
        ),
        detail=(
            "MATCH (d:{Document} {key: $key}) "
            "CALL (d) { MATCH (d)-[:DECIDES]->(e:{Entity}) WITH e ORDER BY e.id LIMIT 4 "
            "  RETURN collect({id: e.id, name: e.name, kind: e.kind, description: e.description, "
            "                  evidence_chunk_ids: e.evidence_chunk_ids}) AS decisions } "
            "CALL (d) { MATCH (d)-[r:REJECTS]->(a:{Entity}) WITH a, r ORDER BY a.id LIMIT 4 "
            "  RETURN collect({id: a.id, name: a.name, kind: a.kind, description: a.description, "
            "                  evidence_chunk_ids: a.evidence_chunk_ids, "
            "                  grounds_chunk_ids: r.evidence_chunk_ids, note: r.note}) "
            "         AS alternatives } "
            "RETURN {key: d.key, title: d.title, kind: d.kind} AS document, decisions, alternatives"
        ),
        build=_build_decision_rejects,
    ),
    Shape(
        name="motivation",
        question_type="rationale",
        gold_source="graph",
        expected_strategy="s3",
        path_shape="Document-[:DECIDES]->Entity-[:MOTIVATED_BY]->Entity(Problem)",
        anchor_roles=("document",),
        answer_roles=("problem",),
        describe="the problem a design was chosen to solve",
        select=(
            "MATCH (d:{Document})-[:DECIDES]->(:{Entity})-[:MOTIVATED_BY]->(p:{Entity}) "
            "WHERE d.kind = 'KIP' "
            "WITH d, count(DISTINCT p) AS problems ORDER BY problems DESC, d.key "
            "LIMIT $pool RETURN d.key AS path_key, problems AS richness"
        ),
        detail=(
            "MATCH (d:{Document} {key: $key}) "
            "CALL (d) { MATCH (d)-[:DECIDES]->(s:{Entity})-[:MOTIVATED_BY]->(p:{Entity}) "
            "  WITH s, p ORDER BY p.id, s.id LIMIT 4 "
            "  RETURN collect({source: {id: s.id, name: s.name, kind: s.kind, "
            "                           description: s.description, "
            "                           evidence_chunk_ids: s.evidence_chunk_ids}, "
            "                  problem: {id: p.id, name: p.name, kind: p.kind, "
            "                            description: p.description, "
            "                            evidence_chunk_ids: p.evidence_chunk_ids}}) AS links } "
            "RETURN {key: d.key, title: d.title, kind: d.kind} AS document, links"
        ),
        build=_build_motivation,
    ),
    Shape(
        name="community_theme",
        question_type="global",
        gold_source="graph",
        expected_strategy="s5",
        path_shape="Community(title, summary)<-[:IN_COMMUNITY]-member",
        anchor_roles=("community",),
        answer_roles=("member",),
        describe="the theme a cluster of the corpus is about",
        select=(
            "MATCH (c:{Community}) WHERE c.title IS NOT NULL AND c.rank IS NOT NULL "
            "WITH c ORDER BY c.rank DESC, c.id "
            "LIMIT $pool RETURN c.id AS path_key, c.rank AS richness"
        ),
        detail=(
            "MATCH (c:{Community} {id: $key}) "
            "CALL (c) { MATCH (m)-[:IN_COMMUNITY]->(c) RETURN count(m) AS size } "
            "CALL (c) { MATCH (m)-[rel:IN_COMMUNITY]->(c) "
            "  WITH m, rel, coalesce(m.key, m.id, m.name) AS mkey, "
            "    CASE WHEN m:{Document} THEN 'Document' WHEN m:{Component} THEN 'Component' "
            "         WHEN m:{WorkItem} THEN 'WorkItem' ELSE 'Entity' END AS mlabel "
            # `IN_COMMUNITY.degree` is the member's degree in the projected graph — what the
            # Leiden run actually clustered on, and what `brain communities batches` ranks
            # by. Two earlier orderings were wrong for the same reason: by key alone returns
            # ten `Alternative|…` entities (an entity id sorts before `KAFKA-…`), and
            # label-first returns an alphabetical slice of documents. L1-489 shipped with
            # KIP-1, KIP-10, KIP-11 while its summary was about KIP-98 and KIP-724.
            "  ORDER BY coalesce(rel.degree, 0) DESC, mkey LIMIT 10 "
            "  RETURN collect({key: mkey, label: mlabel, degree: rel.degree, "
            "                  title: coalesce(m.title, m.name), kind: m.kind, "
            "                  status: m.status}) AS members } "
            "RETURN {id: c.id, title: c.title, summary: c.summary, rank: c.rank, level: c.level, "
            "        finding_statements: c.finding_statements, "
            "        evidence_chunk_ids: c.evidence_chunk_ids, size: size} AS community, members"
        ),
        build=_build_community_theme,
    ),
    Shape(
        name="ownership_timeline",
        question_type="temporal",
        gold_source="graph",
        expected_strategy="s6",
        path_shape=(
            "WorkItem-[:ASSIGNED_TO {valid_from, valid_to}]->Person + "
            "WorkItem-[:HAS_CHANGE]->StatusChange"
        ),
        anchor_roles=("work_item",),
        answer_roles=("assignee", "status_change"),
        describe="who held an issue when, and what its status did",
        select=(
            "MATCH (w:{WorkItem})-[r:ASSIGNED_TO]->(:{Person}) "
            "WITH w, count(r) AS holders WHERE holders > 1 "
            "CALL (w) { MATCH (w)-[:HAS_CHANGE]->(s:{StatusChange}) WHERE s.field = 'status' "
            "  RETURN count(s) AS changes } "
            "WITH w, holders, changes WHERE changes > 1 "
            "ORDER BY holders + changes DESC, w.key "
            "LIMIT $pool RETURN w.key AS path_key, holders + changes AS richness"
        ),
        detail=(
            "MATCH (w:{WorkItem} {key: $key}) "
            "CALL (w) { MATCH (w)-[r:ASSIGNED_TO]->(p:{Person}) "
            "  WITH r, p ORDER BY toString(r.valid_from), p.id LIMIT 6 "
            "  RETURN collect({person: p.id, display: p.display, source: r.source, "
            "                  valid_from: toString(r.valid_from), "
            "                  valid_to: toString(r.valid_to)}) AS assignments } "
            "CALL (w) { MATCH (w)-[:HAS_CHANGE]->(s:{StatusChange}) WHERE s.field = 'status' "
            "  WITH s ORDER BY s.at, s.id LIMIT 6 "
            "  RETURN collect({id: s.id, field: s.field, `from`: s.`from`, to: s.to, "
            "                  at: toString(s.at), by: s.by}) AS changes } "
            "RETURN {key: w.key, title: w.title, status: w.status, source: w.source, "
            "        created: toString(w.created)} AS work_item, assignments, changes"
        ),
        build=_build_ownership_timeline,
    ),
    Shape(
        name="release_window",
        question_type="temporal",
        gold_source="graph",
        expected_strategy="s6",
        path_shape=(
            "Component<-[:IN_COMPONENT]-WorkItem-[:FIX_VERSION]->Version, "
            "over two adjacent versions"
        ),
        anchor_roles=("component", "version"),
        answer_roles=("released_item",),
        describe="what shipped in a component between two releases",
        select=(
            "MATCH (w:{WorkItem})-[:IN_COMPONENT]->(k:{Component}) "
            "MATCH (w)-[:FIX_VERSION]->(v:{Version}) "
            "WITH k, count(DISTINCT v) AS versions, count(w) AS items WHERE versions > 1 "
            "ORDER BY items DESC, k.name LIMIT $pool "
            "RETURN k.name AS path_key, items AS richness"
        ),
        detail=(
            "MATCH (k:{Component} {name: $key}) "
            "CALL (k) { MATCH (w:{WorkItem})-[:IN_COMPONENT]->(k) "
            "  MATCH (w)-[:FIX_VERSION]->(v:{Version}) "
            "  WITH v, count(w) AS items ORDER BY items DESC, v.name LIMIT 2 "
            "  RETURN collect({name: v.name, items: items}) AS tops } "
            "UNWIND tops AS top "
            "CALL (k, top) { MATCH (w:{WorkItem})-[:IN_COMPONENT]->(k) "
            "  MATCH (w)-[:FIX_VERSION]->(v:{Version} {name: top.name}) "
            "  WITH w ORDER BY w.key LIMIT 4 "
            "  RETURN collect({key: w.key, title: w.title, status: w.status, type: w.type, "
            "                  resolution: w.resolution}) AS work_items } "
            "WITH k, collect({name: top.name, items: top.items, "
            "                 work_items: work_items}) AS versions "
            "RETURN k.name AS component, versions"
        ),
        build=_build_release_window,
    ),
    Shape(
        name="stale_state",
        question_type="temporal",
        gold_source="truth",
        expected_strategy="s6",
        path_shape="WorkItem(ADO mirror) vs WorkItem(Jira) — the mirror's status is out of date",
        anchor_roles=("mirror_item",),
        answer_roles=("source_item",),
        describe="which of two records of the same work is current",
        truth_section="stale_states",
        truth_keys=lambda r: [r["ado_key"], r["jira_key"]],
        truth_build=_truth_stale_state,
    ),
)

SHAPES_BY_NAME: dict[str, Shape] = {s.name: s for s in SHAPES}
QUESTION_TYPES: tuple[str, ...] = ("traceability", "impact", "rationale", "global", "temporal")


def shapes_for(question_type: str) -> list[Shape]:
    return [s for s in SHAPES if s.question_type == question_type]


# ---------------------------------------------------------------------------- sampling


def _pick(pool: Sequence[Any], want: int, seed: int, salt: str) -> list[Any]:
    """A deterministic pick from the pool. Same (seed, salt) -> same choice, always."""
    items = list(pool)
    if want >= len(items):
        return items
    rng = random.Random(f"{seed}:{salt}")
    return [items[i] for i in sorted(rng.sample(range(len(items)), want))]


def load_truth(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else TRUTH_PATH
    if not target.is_file():
        raise PathError(
            f"{target} is missing — the synthetic-truth shapes have nothing to draw on. "
            "Run `brain synth merge` first, or build with --no-truth-shapes."
        )
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PathError(f"{target} is not an object")
    return data


def sample_graph_shape(
    ctx: RetrieveContext,
    shape: Shape,
    want: int,
    *,
    seed: int = DEFAULT_SEED,
    exclude: Collection[str] = (),
) -> list[PathSample]:
    """`select` a pool, pick `want` of it deterministically, then `detail` each one.

    `exclude` is what a second pass (the per-batch spares) has already taken, so the spare
    a batch gets is never a path the batch is also asking questions about.
    """
    if shape.build is None:
        raise PathError(f"shape {shape.name} has no builder")
    rows = ctx.read(
        _fill(shape.select, ctx),
        pool=max((want + len(exclude)) * POOL_FACTOR, want),
        closed=list(CLOSED_STATUSES),
    )
    blocked = {str(x) for x in exclude}
    keys = [
        str(r["path_key"]) for r in rows if r.get("path_key") and str(r["path_key"]) not in blocked
    ]
    out: list[PathSample] = []
    for key in _pick(keys, want, seed, shape.name):
        detail = ctx.read(_fill(shape.detail, ctx), key=key, closed=list(CLOSED_STATUSES))
        if not detail:
            continue
        nodes, edges, facts = shape.build(detail[0])
        out.append(
            PathSample(
                shape=shape.name,
                question_type=shape.question_type,
                gold_source=shape.gold_source,
                expected_strategy=shape.expected_strategy,
                path_shape=shape.path_shape,
                path_key=key,
                nodes=nodes,
                edges=edges,
                anchors=_roles(nodes, shape.anchor_roles),
                answers=_roles(nodes, shape.answer_roles),
                facts=facts,
            )
        )
    return out


def sample_truth_shape(
    ctx: RetrieveContext,
    shape: Shape,
    want: int,
    truth: dict[str, Any],
    *,
    seed: int = DEFAULT_SEED,
    exclude: Collection[str] = (),
) -> list[PathSample]:
    """Rows of known truth, with whatever the graph holds for the keys they name."""
    if shape.truth_keys is None or shape.truth_build is None:
        raise PathError(f"shape {shape.name} has no truth builder")
    section = truth.get(shape.truth_section) or []
    if not isinstance(section, list):
        raise PathError(f"synthetic_truth.json/{shape.truth_section} is not an array")
    blocked = {str(x) for x in exclude}
    indexed = [
        (i, row)
        for i, row in enumerate(section)
        if "-".join(str(k) for k in shape.truth_keys(row)) not in blocked
    ]
    out: list[PathSample] = []
    for index, row in _pick(indexed, want, seed, shape.name):
        keys = [str(k) for k in shape.truth_keys(row)]
        found = _lookup_keys(ctx, keys)
        # A truth row whose keys the graph does not hold measures nothing: retrieval could
        # not find them however good it is. Skipped, not faked.
        if len(found) < len(keys):
            continue
        nodes, edges, facts = shape.truth_build(row, found)
        out.append(
            PathSample(
                shape=shape.name,
                question_type=shape.question_type,
                gold_source=shape.gold_source,
                expected_strategy=shape.expected_strategy,
                path_shape=shape.path_shape,
                path_key="-".join(keys),
                nodes=nodes,
                edges=edges,
                anchors=_roles(nodes, shape.anchor_roles),
                answers=_roles(nodes, shape.answer_roles),
                facts=facts,
                truth=[
                    {
                        "id": f"truth:{shape.truth_section}:{index}",
                        "section": shape.truth_section,
                        "index": index,
                        "record": row,
                        "note": "Ground truth by construction — the generator wrote this "
                        "on purpose.",
                    }
                ],
            )
        )
    return out


#: An Xray test is labelled `Test` *and* `WorkItem`, and `head(labels(n))` returns whichever
#: Neo4j stored first — which made every test come back as a `WorkItem`. A CASE ladder says
#: which label wins instead of leaving it to storage order.
_LOOKUP_CYPHER = (
    "MATCH (n) WHERE coalesce(n.key, n.name) IN $keys "
    "AND any(l IN labels(n) WHERE l IN ['WorkItem', 'Document', 'Test', 'Component']) "
    "RETURN coalesce(n.key, n.name) AS key, "
    "  CASE WHEN n:{Test} THEN 'Test' WHEN n:{Document} THEN 'Document' "
    "       WHEN n:{Component} THEN 'Component' ELSE 'WorkItem' END AS label, "
    "  n.title AS title, n.status AS status, n.type AS type, n.source AS source"
)


def _lookup_keys(ctx: RetrieveContext, keys: Sequence[str]) -> list[dict[str, Any]]:
    if not keys:
        return []
    rows = ctx.read(_fill(_LOOKUP_CYPHER, ctx), keys=list(keys))
    by_key = {str(r["key"]): r for r in rows}
    return [by_key[k] for k in keys if k in by_key]


# ---------------------------------------------------------------------------- snippets

_CHUNKS_BY_PARENT = (
    "MATCH (c:{Chunk}) WHERE c.parent_key IN $keys "
    "RETURN c.id AS chunk_id, c.parent_key AS parent_key, c.parent_kind AS parent_kind, "
    "  c.kind AS kind, c.heading AS heading, c.lang AS lang, c.text AS text, "
    "  coalesce(c.position, 0) AS position "
    "ORDER BY c.parent_key, position, c.id"
)

_CHUNKS_BY_ID = (
    "MATCH (c:{Chunk}) WHERE c.id IN $ids "
    "RETURN c.id AS chunk_id, c.parent_key AS parent_key, c.parent_kind AS parent_kind, "
    "  c.kind AS kind, c.heading AS heading, c.lang AS lang, c.text AS text, "
    "  coalesce(c.position, 0) AS position "
    "ORDER BY c.id"
)


def _snippet(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": str(row["chunk_id"]),
        "parent_key": row.get("parent_key") or "",
        "parent_kind": row.get("parent_kind") or "",
        "kind": row.get("kind") or "",
        "heading": clip(row.get("heading"), 120),
        "lang": row.get("lang") or "",
        "text": clip(row.get("text"), SNIPPET_CHARS),
    }


def attach_snippets(ctx: RetrieveContext, paths: Sequence[PathSample]) -> int:
    """Fill every path's `snippets[]`: the raw text a gold answer may be quoted from.

    Two sources, in this order: a chunk an `Entity` already cites as its own evidence
    (that is the extraction's provenance, and it is what the rationale shapes rest on),
    then the first chunks of the path's parent nodes. Both capped, because a batch that
    does not fit is a batch nobody reads.
    """
    parents = sorted({k for p in paths for k in p.parent_keys()})
    evidence = sorted({c for p in paths for c in p.evidence_chunk_ids()})
    by_parent: dict[str, list[dict[str, Any]]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    if parents:
        for row in ctx.read(_fill(_CHUNKS_BY_PARENT, ctx), keys=parents):
            by_parent.setdefault(str(row["parent_key"]), []).append(_snippet(row))
    if evidence:
        for row in ctx.read(_fill(_CHUNKS_BY_ID, ctx), ids=evidence):
            by_id[str(row["chunk_id"])] = _snippet(row)

    total = 0
    for path in paths:
        chosen: dict[str, dict[str, Any]] = {}
        for cid in path.evidence_chunk_ids():
            if cid in by_id and len(chosen) < SNIPPETS_PER_PATH:
                chosen.setdefault(cid, by_id[cid])
        for key in path.parent_keys():
            for snippet in by_parent.get(key, [])[:SNIPPETS_PER_PARENT]:
                if len(chosen) >= SNIPPETS_PER_PATH:
                    break
                chosen.setdefault(snippet["chunk_id"], snippet)
        path.snippets = list(chosen.values())
        total += len(path.snippets)
    return total


def sample(
    ctx: RetrieveContext,
    wanted: dict[str, int],
    *,
    seed: int = DEFAULT_SEED,
    truth: dict[str, Any] | None = None,
    exclude: Collection[str] = (),
) -> list[PathSample]:
    """`{question_type: how many paths}` -> paths, round-robin over that type's shapes.

    Round-robin rather than "fill the first shape first": a type whose eight questions all
    came from one shape measures that shape, not the type.
    """
    out: list[PathSample] = []
    for qtype in QUESTION_TYPES:
        want = int(wanted.get(qtype, 0))
        if want <= 0:
            continue
        shapes = shapes_for(qtype)
        if not shapes:
            raise PathError(f"no path shape produces {qtype} questions")
        quotas = _round_robin(want, len(shapes))
        got: list[PathSample] = []
        for shape, quota in zip(shapes, quotas, strict=True):
            if quota <= 0:
                continue
            if shape.gold_source == "truth":
                got.extend(sample_truth_shape(ctx, shape, quota, truth or load_truth(), seed=seed))
            else:
                got.extend(sample_graph_shape(ctx, shape, quota, seed=seed))
        # A shape that came up short (a truth row the graph does not hold, a select with a
        # thin pool) is topped up from the type's other shapes rather than silently missing.
        short = want - len(got)
        if short > 0:
            for shape in shapes:
                if short <= 0:
                    break
                have = {p.path_key for p in got if p.shape == shape.name}
                extra = (
                    sample_truth_shape(
                        ctx,
                        shape,
                        len(have) + short,
                        truth or load_truth(),
                        seed=seed + 1,
                        exclude=exclude,
                    )
                    if shape.gold_source == "truth"
                    else sample_graph_shape(
                        ctx, shape, len(have) + short, seed=seed + 1, exclude=exclude
                    )
                )
                for p in extra:
                    if short <= 0:
                        break
                    if p.path_key not in have:
                        got.append(p)
                        have.add(p.path_key)
                        short -= 1
        out.extend(got[:want])
    return out


def _round_robin(total: int, buckets: int) -> list[int]:
    """`total` spread over `buckets`, the remainder going to the first ones."""
    if buckets <= 0:
        return []
    base, rest = divmod(total, buckets)
    return [base + (1 if i < rest else 0) for i in range(buckets)]
