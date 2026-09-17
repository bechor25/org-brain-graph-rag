"""The 15 competency questions (spec §2.3), built from the graph rather than typed by hand.

Plan decision 7: the anchors are *chosen in code* — the issue with the most `TESTS`, the
KIP with the most `REJECTS`, the component with the most `RESOLVES`. Two reasons, and the
second is the important one:

1. The spec's own keys (`KAFKA-15123`, `KIP-1000`) are illustrative and several are not in
   the harvested slice. A question about a key the graph does not hold measures nothing.
2. A hand-picked key is a key someone already knows the answer for. Selecting by a graph
   superlative gives a question the *corpus* nominated, so "the retrieval found it" is a
   fact about retrieval and not about who wrote the question.

The four Hebrew rows are translations of four English rows, marked with the same `pair`,
which is what makes the cross-lingual check a comparison rather than a vibe: same anchors,
same expected strategy, different language.

`question-forger` (Plan 3) replaces and widens this file; its job is 32+ questions with
gold answers. This one's job is 19 questions with *real anchors*, today.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext

DEFAULT_PATH = Path("data/eval/competency.jsonl")

#: name -> (cypher, description). Each returns one row; `LIMIT 1` after a deterministic
#: `ORDER BY` that includes the key, so a tie does not make the question set random.
SELECTORS: dict[str, tuple[str, str]] = {
    "issue_most_tests": (
        # cq01/cq16 ask "…and what was their last execution status?", so an issue whose
        # tests were never run is an anchor whose gold answer is "nothing ran" — a real
        # fact about the corpus and a useless question about retrieval. Coverage still
        # ranks the winners; having been run decides which winners are eligible.
        "MATCH (t:{Test})-[:TESTS]->(w:{WorkItem}) "
        "OPTIONAL MATCH (:{TestExecution})-[r:HAS_RUN]->(t) "
        "WITH w, count(DISTINCT t) AS n, count(r) AS runs "
        "ORDER BY CASE WHEN runs > 0 THEN 1 ELSE 0 END DESC, n DESC, runs DESC, w.key LIMIT 1 "
        "RETURN w.key AS key, w.title AS title, n AS n, "
        "toString(n) + ' tests, ' + toString(runs) + ' recorded runs' AS note",
        "the work item the most run tests cover",
    ),
    "kip_most_commits": (
        "MATCH (c:{Commit})-[:IMPLEMENTS_KIP]->(d:{Document}) "
        "WITH d, count(c) AS n ORDER BY n DESC, d.key LIMIT 1 "
        "RETURN d.key AS key, d.title AS title, n AS n",
        "the KIP with the most implementing commits",
    ),
    "kip_most_decides": (
        "MATCH (d:{Document})-[:DECIDES]->(e:{Entity}) WHERE d.kind = 'KIP' "
        "WITH d, count(e) AS n ORDER BY n DESC, d.key LIMIT 1 "
        "RETURN d.key AS key, d.title AS title, n AS n",
        "the KIP with the most extracted decisions",
    ),
    "kip_most_rejects": (
        "MATCH (d:{Document})-[:REJECTS]->(a:{Entity}) WHERE d.kind = 'KIP' "
        "WITH d, count(a) AS n ORDER BY n DESC, d.key LIMIT 1 "
        "RETURN d.key AS key, d.title AS title, n AS n",
        "the KIP that rejects the most alternatives",
    ),
    "kip_most_motivated": (
        "MATCH (d:{Document})-[:DECIDES]->(:{Entity})-[:MOTIVATED_BY]->(p:{Entity}) "
        "WHERE d.kind = 'KIP' "
        "WITH d, count(DISTINCT p) AS n ORDER BY n DESC, d.key LIMIT 1 "
        "RETURN d.key AS key, d.title AS title, n AS n",
        "the KIP whose decisions name the most problems",
    ),
    "component_most_resolves": (
        "MATCH (c:{Commit})-[:RESOLVES]->(w:{WorkItem})-[:IN_COMPONENT]->(k:{Component}) "
        "WITH k, count(DISTINCT c) AS n ORDER BY n DESC, k.name LIMIT 1 "
        "RETURN k.name AS key, k.name AS title, n AS n",
        "the component with the most resolving commits",
    ),
    "component_most_open_bugs": (
        "MATCH (b:{Bug})-[:IN_COMPONENT]->(k:{Component}) "
        "WHERE NOT b.status IN ['Resolved', 'Closed', 'Done', 'Completed', 'Removed'] "
        "WITH k, count(b) AS n ORDER BY n DESC, k.name LIMIT 1 "
        "RETURN k.name AS key, k.name AS title, n AS n",
        "the component with the most open bugs",
    ),
    "ado_story_kip": (
        "MATCH (w:{WorkItem})-[:REFERENCES]->(d:{Document}) "
        "WHERE w.source = 'ado' AND d.kind = 'KIP' "
        "WITH d, collect(w.key) AS stories ORDER BY size(stories) DESC, d.key LIMIT 1 "
        "RETURN d.key AS key, d.title AS title, size(stories) AS n, stories AS extra",
        "the KIP with the most synthetic ADO work items pointing at it",
    ),
    "entity_most_depends": (
        "MATCH (x)-[:DEPENDS_ON]->(e:{Entity}) "
        "WITH e, count(x) AS n ORDER BY n DESC, e.id LIMIT 1 "
        "RETURN e.id AS key, e.name AS title, n AS n",
        "the entity the most things depend on",
    ),
    "issue_most_status_changes": (
        "MATCH (w:{WorkItem})-[:HAS_CHANGE]->(s:{StatusChange}) WHERE s.field = 'status' "
        "WITH w, collect(toString(s.at)) AS ats ORDER BY size(ats) DESC, w.key LIMIT 1 "
        "RETURN w.key AS key, w.title AS title, size(ats) AS n, ats AS extra",
        "the work item with the longest status history",
    ),
    "issue_most_assignees": (
        "MATCH (w:{WorkItem})-[r:ASSIGNED_TO]->(p:{Person}) "
        "WITH w, count(r) AS n ORDER BY n DESC, w.key LIMIT 1 "
        "RETURN w.key AS key, w.title AS title, n AS n",
        "the work item that changed hands most often",
    ),
    "version_window": (
        "MATCH (w:{WorkItem})-[:IN_COMPONENT]->(k:{Component}) "
        "MATCH (w)-[:FIX_VERSION]->(v:{Version}) "
        "WITH k.name AS comp, v.name AS ver, count(w) AS items "
        "WITH comp, collect(ver + '=' + toString(items)) AS versions, sum(items) AS total "
        "ORDER BY total DESC, comp LIMIT 1 "
        "RETURN comp AS key, comp AS title, total AS n, versions AS extra",
        "the component with the most released work, and its two busiest adjacent minors",
    ),
}


@dataclass
class Anchor:
    name: str
    key: str
    title: str = ""
    count: int = 0
    extra: list[str] = field(default_factory=list)
    rule: str = ""
    #: What the selector measured about this key, in words, when the count alone hides it
    #: ("3 tests, 0 recorded runs" is the difference between a good anchor and a bad one).
    note: str = ""


def _fill(cypher: str, ctx: RetrieveContext) -> str:
    """`{Label}` placeholders become this context's namespaced labels."""
    out = cypher
    for label in (
        "Test",
        "WorkItem",
        "Document",
        "Commit",
        "Component",
        "Entity",
        "Person",
        "StatusChange",
        "TestExecution",
        "Version",
        "Bug",
    ):
        out = out.replace("{" + label + "}", ctx.label(label))
    return out


def anchors(ctx: RetrieveContext) -> dict[str, Anchor]:
    """Run every selector. A selector that finds nothing is skipped, not faked."""
    found: dict[str, Anchor] = {}
    for name, (cypher, rule) in SELECTORS.items():
        rows = ctx.read(_fill(cypher, ctx))
        if not rows or not rows[0].get("key"):
            continue
        row = rows[0]
        found[name] = Anchor(
            name=name,
            key=str(row["key"]),
            title=row.get("title") or "",
            count=int(row.get("n") or 0),
            extra=[str(x) for x in (row.get("extra") or [])],
            rule=rule,
            note=str(row.get("note") or ""),
        )
    return found


def _midpoint_date(timestamps: list[str]) -> str:
    """A date the item's status history actually spans — the middle recorded change."""
    stamps = sorted(t for t in timestamps if t)
    if not stamps:
        return "2024-03-01"
    return stamps[len(stamps) // 2][:10]


def _version_pair(versions: list[str]) -> tuple[str, str]:
    """Two adjacent minor families with the most work between them, e.g. ('3.6', '3.7')."""
    counts: dict[tuple[int, int], int] = {}
    for entry in versions:
        name, _, items = entry.partition("=")
        parts = [int(p) for p in name.split(".") if p.isdigit()]
        if len(parts) < 2:
            continue
        counts[(parts[0], parts[1])] = counts.get((parts[0], parts[1]), 0) + int(items or 0)
    minors = sorted(counts)
    best, score = None, -1
    for low, high in zip(minors, minors[1:], strict=False):
        if high[0] != low[0] or high[1] != low[1] + 1:
            continue
        total = counts[low] + counts[high]
        if total > score:
            best, score = (low, high), total
    if best is None:
        return "3.6", "3.7"
    (a1, a2), (b1, b2) = best
    return f"{a1}.{a2}", f"{b1}.{b2}"


#: (id, type, lang, expected strategy, pair, template). `{name}` is an anchor key,
#: `{name!t}` its title. The templates are spec §2.3's wording with real keys in it.
TEMPLATES: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "cq01",
        "traceability",
        "en",
        "s3",
        "tests",
        "Which tests cover {issue_most_tests} and what was their last execution status?",
    ),
    (
        "cq02",
        "traceability",
        "en",
        "s3",
        "",
        "Which commits fixed the bug behind {kip_most_commits} rollout issues?",
    ),
    (
        "cq03",
        "traceability",
        "en",
        "s4",
        "",
        "Who owns component `{component_most_resolves}` (most assignments and commits)?",
    ),
    (
        "cq04",
        "traceability",
        "en",
        "s3",
        "",
        "Which ADO story delivers {ado_story_kip}?",
    ),
    (
        "cq05",
        "impact",
        "en",
        "s2",
        "change",
        "If we change `{component_most_open_bugs}`, which open issues, tests, "
        "and KIPs are affected?",
    ),
    (
        "cq06",
        "impact",
        "en",
        "s4",
        "",
        "Which components have failing tests tied to open bugs in {version_high}?",
    ),
    ("cq07", "impact", "en", "s2", "", "What depends on {entity_most_depends!t}?"),
    (
        "cq08",
        "impact",
        "en",
        "s3",
        "",
        "Which open issues reference {kip_most_commits}?",
    ),
    (
        "cq09",
        "rationale",
        "en",
        "s3",
        "why",
        "Why was the design in {kip_most_decides} chosen?",
    ),
    (
        "cq10",
        "rationale",
        "en",
        "s3",
        "",
        "What alternatives were rejected in {kip_most_rejects} and why?",
    ),
    ("cq11", "rationale", "en", "s3", "", "What problem motivated {kip_most_motivated}?"),
    (
        "cq12",
        "global",
        "en",
        "s5",
        "",
        "What are the main themes of open bugs in {component_most_open_bugs}?",
    ),
    (
        "cq13",
        "temporal",
        "en",
        "s6",
        "changed",
        "What changed in `{version_window}` between {version_low} and {version_high}?",
    ),
    (
        "cq14",
        "temporal",
        "en",
        "s6",
        "",
        "What was the status of {issue_most_status_changes} on {status_date}?",
    ),
    (
        "cq15",
        "temporal",
        "en",
        "s6",
        "",
        "Who was assigned to {issue_most_assignees} over time?",
    ),
    (
        "cq16",
        "traceability",
        "he",
        "s3",
        "tests",
        "אילו טסטים מכסים את {issue_most_tests} ומה סטטוס ההרצה האחרון שלהם?",
    ),
    (
        "cq17",
        "impact",
        "he",
        "s2",
        "change",
        "אם נשנה את `{component_most_open_bugs}` — אילו issues פתוחים, טסטים ומסמכים מושפעים?",
    ),
    ("cq18", "rationale", "he", "s3", "why", "למה נבחר התכן ב-{kip_most_decides}?"),
    (
        "cq19",
        "temporal",
        "he",
        "s6",
        "changed",
        "מה השתנה ב-`{version_window}` בין {version_low} ל-{version_high}?",
    ),
)


def build(ctx: RetrieveContext) -> list[dict[str, Any]]:
    """Render the 19 questions against this graph. Raises if an anchor is missing."""
    found = anchors(ctx)
    values: dict[str, str] = {name: a.key for name, a in found.items()}
    titles: dict[str, str] = {name: (a.title or a.key) for name, a in found.items()}

    if "issue_most_status_changes" in found:
        values["status_date"] = _midpoint_date(found["issue_most_status_changes"].extra)
    if "version_window" in found:
        low, high = _version_pair(found["version_window"].extra)
        values["version_low"], values["version_high"] = low, high

    rows: list[dict[str, Any]] = []
    for qid, qtype, lang, expected, pair, template in TEMPLATES:
        text, used = _render(template, values, titles)
        if text is None:
            continue
        rows.append(
            {
                "id": qid,
                "type": qtype,
                "lang": lang,
                "question": text,
                "expected_strategy": expected,
                "pair": pair,
                "anchors": [values[n] for n in used if n in values],
                "anchor_rules": [found[n].rule for n in used if n in found],
                "template": template,
            }
        )
    return rows


def _render(
    template: str, values: dict[str, str], titles: dict[str, str]
) -> tuple[str | None, list[str]]:
    """Substitute `{name}` (key) and `{name!t}` (title). Missing anchor -> drop the question."""
    out = template
    used: list[str] = []
    for name in sorted(set(values) | set(titles), key=len, reverse=True):
        for token, table in ((f"{{{name}!t}}", titles), (f"{{{name}}}", values)):
            if token in out and name in table:
                out = out.replace(token, table[name])
                used.append(name)
    if "{" in out:
        return None, used
    return out, used


def write(rows: list[dict[str, Any]], path: Path | None = None) -> Path:
    target = Path(path) if path is not None else DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    tmp.replace(target)
    return target


def read(path: Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path is not None else DEFAULT_PATH
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line]


def anchor_report(found: dict[str, Anchor]) -> list[dict[str, Any]]:
    return [asdict(a) | {"extra": len(a.extra)} for a in found.values()]
