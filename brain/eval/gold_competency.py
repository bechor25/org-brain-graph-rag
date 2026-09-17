"""`brain eval questions gold-competency` — the gold for Plan 2's 19 questions, from code.

The 19 competency questions were never typed by hand: `brain/retrieve/competency.py` picked
each anchor with a Cypher superlative ("the work item the most run tests cover", "the KIP
that rejects the most alternatives"). Their gold answers are derived the same way, and for
the same reason — a gold answer someone wrote from memory is a fact about that person, and
an evaluation graded against it measures agreement with them rather than with the corpus.

Three rules this module holds itself to.

**Every answer carries the query that produced it.** `gold_query` is the Cypher, or the name
of the deterministic retrieval function reused (`brain.retrieve.temporal.status_at` and its
two siblings are already exactly this: a fixed query over the changelog). The planner can
re-run it and get the same answer, which is what makes a hand-audit cheap.

**An empty answer stays empty.** A question whose derivation finds nothing keeps
`gold_source: "pending"` and gets a `gold_note` saying what was looked for and what came
back. "No component has a failing test on an open 3.8 bug" is a real answer and is written
as one; "the query returned no rows" is not an answer and is not dressed up as one.

**Anchor drift is refused, not papered over.** The anchors are re-selected from the live
graph before anything is derived. If the graph now nominates a different key than the
question names — the corpus moved, or `brain competency` has not been re-run — deriving
against the new key would produce a gold answer to a different question. Those rows stay
pending with a note naming both keys.

Hebrew twins get the *same facts*, rendered in Hebrew: the cross-lingual comparison is only
a comparison if both halves are graded against the same truth.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from brain.retrieve import competency
from brain.retrieve import temporal as temporal_mod
from brain.retrieve.context import RetrieveContext
from brain.retrieve.pack import clip
from brain.retrieve.types import Result, RetrieveError

#: Statuses the corpus treats as closed, as `brain/retrieve/competency.py` does.
CLOSED_STATUSES: tuple[str, ...] = ("Resolved", "Closed", "Done", "Completed", "Removed")

#: The impact questions ask "what is affected" — a list of everything would be a corpus
#: dump, not an answer. Ten, ordered by key, with the honest total beside it.
IMPACT_CAP = 10
#: Rationale: how many decisions / alternatives / problems a gold answer names.
RATIONALE_CAP = 4
#: Global: the brief's top three community titles.
THEME_CAP = 3
#: Traceability / temporal lists.
LIST_CAP = 6
#: A gold answer is a compact fact list, not a report.
QUOTE_CHARS = 160

GOLD_SOURCE = "graph"
DERIVED_BY = "code"


class GoldError(RuntimeError):
    """The gold for the competency set cannot be derived from this graph."""


# ------------------------------------------------------------------------------- model


@dataclass(frozen=True)
class Fact:
    """One derived fact in both languages. The Hebrew twin is graded on the same fact."""

    en: str
    he: str


@dataclass
class Derivation:
    """What one recipe found: facts, the ids they rest on, and the query that found them."""

    facts: list[Fact] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    query: str = ""
    #: Why there is nothing to say, when there is nothing to say.
    note: str = ""

    @property
    def empty(self) -> bool:
        return not self.facts

    def render(self, lang: str) -> str:
        return " ".join((f.he if lang == "he" else f.en) for f in self.facts)


@dataclass
class Gold:
    """One row's gold, ready to be written beside the question."""

    id: str
    gold_answer: str | None
    gold_evidence: list[str]
    gold_source: str
    gold_derived_by: str = DERIVED_BY
    gold_query: str = ""
    gold_note: str = ""

    @property
    def pending(self) -> bool:
        return self.gold_source == "pending"

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "gold_answer": self.gold_answer,
            "gold_evidence": list(self.gold_evidence),
            "gold_source": self.gold_source,
            "gold_derived_by": self.gold_derived_by,
        }
        if self.gold_query:
            out["gold_query"] = self.gold_query
        if self.gold_note:
            out["gold_note"] = self.gold_note
        return out


def _dedup(values: Sequence[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        if v:
            seen.setdefault(str(v), None)
    return list(seen)


def _more(shown: int, total: int, *, en_noun: str, he_noun: str) -> Fact | None:
    if total <= shown:
        return None
    # "יש עוד N …" takes no adjective, so one phrasing is correct for every noun's gender.
    return Fact(
        en=f"{total - shown} further {en_noun} are not listed.",
        he=f"יש עוד {total - shown} {he_noun} מעבר לרשימה.",
    )


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return clip(text.splitlines()[0], QUOTE_CHARS)


# ----------------------------------------------------------------------------- recipes
# Each takes the named anchors it needs and returns a Derivation. Pure apart from `ctx.read`,
# which is READ-routed: the server refuses a write, so a recipe cannot change what it measures.


def _tests_last_run(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """Which tests cover the issue, and what their most recent recorded run said."""
    key = v["issue_most_tests"]
    cypher = (
        f"MATCH (t:{ctx.label('Test')})-[:TESTS]->(w:{ctx.label('WorkItem')} {{`key`: $key}}) "
        f"OPTIONAL MATCH (x:{ctx.label('TestExecution')})-[r:HAS_RUN]->(t) "
        "WITH t, r, x ORDER BY r.at DESC "
        "WITH t, head(collect({status: r.status, at: toString(r.at), execution: x.key})) AS last "
        "RETURN t.key AS test, t.title AS title, last ORDER BY t.key"
    )
    rows = ctx.read(cypher, key=key)
    if not rows:
        return Derivation(query=cypher, note=f"no Test is linked by TESTS to {key}")
    facts: list[Fact] = []
    evidence = [key]
    ran = [r for r in rows if (r.get("last") or {}).get("status")]
    facts.append(
        Fact(
            en=f"{len(rows)} test(s) cover {key}: {', '.join(r['test'] for r in rows[:LIST_CAP])}.",
            he=f"{len(rows)} טסטים מכסים את {key}: "
            f"{', '.join(r['test'] for r in rows[:LIST_CAP])}.",
        )
    )
    for row in rows[:LIST_CAP]:
        evidence.append(row["test"])
        last = row.get("last") or {}
        if last.get("status"):
            evidence.append(str(last.get("execution") or ""))
            facts.append(
                Fact(
                    en=f"{row['test']} last ran {last['status']} on {str(last['at'])[:10]} "
                    f"in {last['execution']}.",
                    he=f"{row['test']} רץ לאחרונה {last['status']} בתאריך "
                    f"{str(last['at'])[:10]} במסגרת {last['execution']}.",
                )
            )
        else:
            facts.append(
                Fact(
                    en=f"{row['test']} has no recorded execution.",
                    he=f"ל-{row['test']} אין הרצה מתועדת.",
                )
            )
    if not ran:
        facts.append(
            Fact(
                en="No execution status is recorded for any of them.",
                he="לא מתועד סטטוס הרצה לאף אחד מהם.",
            )
        )
    return Derivation(facts=facts, evidence=_dedup(evidence), query=cypher)


def _commits_for_kip(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """The commits that say they implement the KIP."""
    key = v["kip_most_commits"]
    cypher = (
        f"MATCH (c:{ctx.label('Commit')})-[:IMPLEMENTS_KIP]->"
        f"(d:{ctx.label('Document')} {{`key`: $key}}) "
        "RETURN c.sha AS sha, c.message AS message, toString(c.at) AS at "
        "ORDER BY c.at, c.sha"
    )
    rows = ctx.read(cypher, key=key)
    if not rows:
        return Derivation(query=cypher, note=f"no Commit carries IMPLEMENTS_KIP to {key}")
    shown = rows[:LIST_CAP]
    facts = [
        Fact(
            en=f"{len(rows)} commits implement {key}.",
            he=f"{len(rows)} קומיטים מממשים את {key}.",
        )
    ]
    for row in shown:
        facts.append(
            Fact(
                en=f"{row['sha'][:12]} ({str(row['at'])[:10]}): {_first_line(row['message'])}",
                he=f"{row['sha'][:12]} ({str(row['at'])[:10]}): {_first_line(row['message'])}",
            )
        )
    extra = _more(len(shown), len(rows), en_noun="commits", he_noun="קומיטים")
    if extra:
        facts.append(extra)
    return Derivation(facts=facts, evidence=_dedup([key, *(r["sha"] for r in shown)]), query=cypher)


def _component_owner(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """Who owns a component: assignments on its work items plus commits that resolved them."""
    name = v["component_most_resolves"]
    assign_cypher = (
        f"MATCH (w:{ctx.label('WorkItem')})-[:ASSIGNED_TO]->(p:{ctx.label('Person')}) "
        f"MATCH (w)-[:IN_COMPONENT]->(:{ctx.label('Component')} {{`name`: $name}}) "
        "RETURN p.id AS person, p.display AS display, count(DISTINCT w) AS n"
    )
    commit_cypher = (
        f"MATCH (a:{ctx.label('Person')})-[:AUTHORED]->(c:{ctx.label('Commit')})"
        f"-[:RESOLVES]->(w:{ctx.label('WorkItem')}) "
        f"MATCH (w)-[:IN_COMPONENT]->(:{ctx.label('Component')} {{`name`: $name}}) "
        "RETURN a.id AS person, a.display AS display, count(DISTINCT c) AS n"
    )
    tally: dict[str, dict[str, Any]] = {}
    for cypher, field_name in ((assign_cypher, "assignments"), (commit_cypher, "commits")):
        for row in ctx.read(cypher, name=name):
            entry = tally.setdefault(
                str(row["person"]),
                {"display": row.get("display") or row["person"], "assignments": 0, "commits": 0},
            )
            entry[field_name] = int(row["n"])
            entry["display"] = entry["display"] or row.get("display")
    query = f"{assign_cypher}  ||  {commit_cypher}"
    if not tally:
        return Derivation(
            query=query, note=f"no person is assigned to or has a resolving commit in {name}"
        )
    ranked = sorted(
        tally.items(), key=lambda kv: (-(kv[1]["assignments"] + kv[1]["commits"]), kv[0])
    )
    top = ranked[:THEME_CAP]
    first, stats = top[0]
    facts = [
        Fact(
            en=f"{stats['display']} ({first}) owns `{name}` on the numbers: "
            f"{stats['assignments']} assignments and {stats['commits']} resolving commits.",
            he=f"{stats['display']} ({first}) הוא הבעלים של `{name}` לפי המספרים: "
            f"{stats['assignments']} שיוכים ו-{stats['commits']} קומיטים פותרים.",
        )
    ]
    for person, entry in top[1:]:
        facts.append(
            Fact(
                en=f"Next: {entry['display']} ({person}) with {entry['assignments']} "
                f"assignments and {entry['commits']} commits.",
                he=f"אחריו: {entry['display']} ({person}) עם {entry['assignments']} "
                f"שיוכים ו-{entry['commits']} קומיטים.",
            )
        )
    return Derivation(facts=facts, evidence=_dedup([name, *(p for p, _ in top)]), query=query)


def _ado_items_for_kip(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """ADO items that deliver the KIP — directly, or through the Jira issue they link to."""
    key = v["ado_story_kip"]
    cypher = (
        f"MATCH (w:{ctx.label('WorkItem')}) WHERE w.source = 'ado' "
        f"OPTIONAL MATCH (w)-[:REFERENCES]->(direct:{ctx.label('Document')} {{`key`: $key}}) "
        f"OPTIONAL MATCH (w)-[:LINKS_TO]->(j:{ctx.label('WorkItem')})"
        f"-[:REFERENCES]->(via:{ctx.label('Document')} {{`key`: $key}}) "
        "WITH w, direct, collect(DISTINCT j.key)[0..2] AS through "
        "WHERE direct IS NOT NULL OR size(through) > 0 "
        "RETURN w.key AS key, w.title AS title, w.type AS type, w.status AS status, "
        "  direct IS NOT NULL AS direct, through ORDER BY w.key"
    )
    rows = ctx.read(cypher, key=key)
    if not rows:
        return Derivation(
            query=cypher,
            note=f"no ADO work item references {key} directly or through a linked Jira issue",
        )
    shown = rows[:LIST_CAP]
    facts = [
        Fact(
            en=f"{len(rows)} ADO work item(s) deliver {key}.",
            he=f"{len(rows)} פריטי עבודה ב-ADO מממשים את {key}.",
        )
    ]
    evidence = [key]
    for row in shown:
        evidence.append(row["key"])
        evidence.extend(row.get("through") or [])
        route_en = "references it directly" if row["direct"] else f"via {', '.join(row['through'])}"
        route_he = "מפנה אליו ישירות" if row["direct"] else f"דרך {', '.join(row['through'])}"
        facts.append(
            Fact(
                en=f"{row['key']} ({row['type']}, {row['status']}) {route_en}: "
                f"{clip(row['title'], QUOTE_CHARS)}",
                he=f"{row['key']} ({row['type']}, {row['status']}) {route_he}: "
                f"{clip(row['title'], QUOTE_CHARS)}",
            )
        )
    extra = _more(len(shown), len(rows), en_noun="ADO items", he_noun="פריטי ADO")
    if extra:
        facts.append(extra)
    return Derivation(facts=facts, evidence=_dedup(evidence), query=cypher)


def _component_blast_radius(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """Open work in a component, the tests on it, and the KIPs it references. Capped at ten."""
    name = v["component_most_open_bugs"]
    cypher = (
        f"MATCH (w:{ctx.label('WorkItem')})-[:IN_COMPONENT]->"
        f"(:{ctx.label('Component')} {{`name`: $name}}) "
        "WHERE NOT coalesce(w.status, '') IN $closed "
        "WITH w ORDER BY w.key "
        "WITH collect(w) AS all_items "
        "WITH all_items, size(all_items) AS total, all_items[0..$cap] AS items "
        "UNWIND items AS w "
        f"OPTIONAL MATCH (t:{ctx.label('Test')})-[:TESTS]->(w) "
        f"OPTIONAL MATCH (w)-[:REFERENCES]->(d:{ctx.label('Document')}) WHERE d.kind = 'KIP' "
        "WITH total, w, collect(DISTINCT t.key) AS tests, collect(DISTINCT d.key) AS kips "
        "RETURN total, w.key AS key, w.title AS title, w.type AS type, w.status AS status, "
        "  tests, kips ORDER BY key"
    )
    rows = ctx.read(cypher, name=name, closed=list(CLOSED_STATUSES), cap=IMPACT_CAP)
    if not rows:
        return Derivation(query=cypher, note=f"`{name}` has no open work item")
    total = int(rows[0]["total"])
    tests = _dedup([t for r in rows for t in (r["tests"] or [])])
    kips = _dedup([k for r in rows for k in (r["kips"] or [])])
    facts = [
        Fact(
            en=f"`{name}` has {total} open work items; the first {len(rows)} by key are "
            f"{', '.join(r['key'] for r in rows)}.",
            he=f"ל-`{name}` יש {total} פריטי עבודה פתוחים; {len(rows)} הראשונים לפי מפתח הם "
            f"{', '.join(r['key'] for r in rows)}.",
        ),
        Fact(
            en=f"Tests covering them: {', '.join(tests) if tests else 'none'}.",
            he=f"טסטים שמכסים אותם: {', '.join(tests) if tests else 'אין'}.",
        ),
        Fact(
            en=f"KIPs they reference: {', '.join(kips) if kips else 'none'}.",
            he=f"מסמכי KIP שהם מפנים אליהם: {', '.join(kips) if kips else 'אין'}.",
        ),
    ]
    return Derivation(
        facts=facts,
        evidence=_dedup([name, *(r["key"] for r in rows), *tests, *kips])[:20],
        query=cypher,
    )


def _components_failing_tests_in_version(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """Components with open bugs fixed for a version, and whether any covering test failed."""
    version = v["version_high"]
    cypher = (
        f"MATCH (b:{ctx.label('Bug')})-[:FIX_VERSION]->(ver:{ctx.label('Version')}) "
        "WHERE ver.name STARTS WITH $version AND NOT coalesce(b.status, '') IN $closed "
        f"MATCH (b)-[:IN_COMPONENT]->(k:{ctx.label('Component')}) "
        f"OPTIONAL MATCH (t:{ctx.label('Test')})-[:TESTS]->(b) "
        f"OPTIONAL MATCH (:{ctx.label('TestExecution')})-[r:HAS_RUN]->(t) "
        "RETURN k.name AS component, count(DISTINCT b) AS open_bugs, "
        "  collect(DISTINCT t.key) AS tests, collect(DISTINCT r.status) AS statuses, "
        "  collect(DISTINCT b.key)[0..3] AS bugs "
        "ORDER BY open_bugs DESC, component"
    )
    rows = ctx.read(cypher, version=version, closed=list(CLOSED_STATUSES))
    if not rows:
        return Derivation(
            query=cypher, note=f"no component has an open bug with a {version}.x fix version"
        )
    failing = [r for r in rows if any(str(s).upper() in ("FAIL", "FAILED") for s in r["statuses"])]
    # Deliberately not `version`: the anchor is the family "3.8", and `Version.name` is
    # "3.8.0". A citable key has to be one the graph holds.
    evidence: list[str] = []
    if not failing:
        covered = [r for r in rows if r["tests"]]
        facts = [
            Fact(
                en=f"No component has a failing test tied to an open {version} bug.",
                he=f"אין רכיב עם טסט נכשל שקשור לבאג פתוח ב-{version}.",
            ),
            Fact(
                en=f"{len(rows)} components have open {version} bugs "
                f"({', '.join(r['component'] for r in rows[:LIST_CAP])}); "
                f"{len(covered)} of them have any covering test at all.",
                he=f"ל-{len(rows)} רכיבים יש באגים פתוחים ב-{version} "
                f"({', '.join(r['component'] for r in rows[:LIST_CAP])}); "
                f"ל-{len(covered)} מהם יש בכלל טסט מכסה.",
            ),
        ]
        evidence.extend(r["component"] for r in rows[:LIST_CAP])
        return Derivation(facts=facts, evidence=_dedup(evidence), query=cypher)
    facts = [
        Fact(
            en=f"{len(failing)} component(s) have a failing test tied to an open {version} bug.",
            he=f"ל-{len(failing)} רכיבים יש טסט נכשל שקשור לבאג פתוח ב-{version}.",
        )
    ]
    for row in failing[:LIST_CAP]:
        evidence.append(row["component"])
        evidence.extend(row["bugs"])
        facts.append(
            Fact(
                en=f"`{row['component']}`: {row['open_bugs']} open bugs "
                f"(e.g. {', '.join(row['bugs'])}), tests {', '.join(row['tests'][:3])}.",
                he=f"`{row['component']}`: {row['open_bugs']} באגים פתוחים "
                f"(למשל {', '.join(row['bugs'])}), טסטים {', '.join(row['tests'][:3])}.",
            )
        )
    return Derivation(facts=facts, evidence=_dedup(evidence)[:20], query=cypher)


def _entity_dependents(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """What the graph says depends on the anchor entity."""
    entity_id = v["entity_most_depends"]
    cypher = (
        f"MATCH (x)-[:DEPENDS_ON]->(e:{ctx.label('Entity')} {{`id`: $id}}) "
        "RETURN coalesce(x.key, x.id, x.name) AS key, coalesce(x.title, x.name) AS title, "
        "  head(labels(x)) AS label, coalesce(x.evidence_chunk_ids, [])[0..1] AS evidence "
        "ORDER BY key"
    )
    rows = ctx.read(cypher, id=entity_id)
    if not rows:
        return Derivation(query=cypher, note=f"nothing carries DEPENDS_ON to {entity_id}")
    shown = rows[:LIST_CAP]
    name = entity_id.split("|", 1)[-1]
    facts = [
        Fact(
            en=f"{len(rows)} thing(s) depend on “{name}”.",
            he=f"{len(rows)} דברים תלויים ב”{name}“.",
        )
    ]
    evidence = [entity_id]
    for row in shown:
        evidence.append(row["key"])
        evidence.extend(row["evidence"] or [])
        facts.append(
            Fact(
                en=f"{row['label']} {row['key']}: {clip(row['title'], QUOTE_CHARS)}",
                he=f"{row['label']} {row['key']}: {clip(row['title'], QUOTE_CHARS)}",
            )
        )
    extra = _more(len(shown), len(rows), en_noun="dependents", he_noun="תלויות")
    if extra:
        facts.append(extra)
    return Derivation(facts=facts, evidence=_dedup(evidence)[:20], query=cypher)


def _open_issues_referencing_kip(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """Open work items that reference the KIP."""
    key = v["kip_most_commits"]
    cypher = (
        f"MATCH (w:{ctx.label('WorkItem')})-[:REFERENCES]->"
        f"(d:{ctx.label('Document')} {{`key`: $key}}) "
        "WHERE NOT coalesce(w.status, '') IN $closed "
        "RETURN w.key AS key, w.title AS title, w.status AS status, w.type AS type, "
        "  w.source AS source ORDER BY w.key"
    )
    rows = ctx.read(cypher, key=key, closed=list(CLOSED_STATUSES))
    if not rows:
        return Derivation(
            query=cypher,
            note=f"every work item referencing {key} is closed — the honest answer is 'none', "
            "but a question with an empty gold measures nothing, so it stays pending",
        )
    shown = rows[:IMPACT_CAP]
    facts = [
        Fact(
            en=f"{len(rows)} open work item(s) reference {key}.",
            he=f"{len(rows)} פריטי עבודה פתוחים מפנים אל {key}.",
        )
    ]
    for row in shown:
        facts.append(
            Fact(
                en=f"{row['key']} ({row['type']}, {row['status']}, {row['source']}): "
                f"{clip(row['title'], QUOTE_CHARS)}",
                he=f"{row['key']} ({row['type']}, {row['status']}, {row['source']}): "
                f"{clip(row['title'], QUOTE_CHARS)}",
            )
        )
    extra = _more(len(shown), len(rows), en_noun="open items", he_noun="פריטים פתוחים")
    if extra:
        facts.append(extra)
    return Derivation(
        facts=facts, evidence=_dedup([key, *(r["key"] for r in shown)])[:20], query=cypher
    )


def _rationale(
    ctx: RetrieveContext,
    key: str,
    *,
    pattern: str,
    role_en: str,
    role_he: str,
    head_en: str,
    head_he: str,
) -> Derivation:
    """Shared body of the three rationale recipes: entities, their evidence, their quotes.

    Plan decision 8: a `weak` entity is one the extraction was not confident in, so its own
    description is not enough to grade an answer against. For those the `MENTIONS` quote —
    verbatim text from a chunk of the same document — is carried into the gold, which is
    what makes the answer checkable against the source rather than against the extraction.
    """
    cypher = (
        f"MATCH (d:{ctx.label('Document')} {{`key`: $key}}){pattern} "
        f"OPTIONAL MATCH (c:{ctx.label('Chunk')})-[m:MENTIONS]->(e) WHERE c.parent_key = $key "
        "WITH e, collect({chunk: c.id, quote: m.quote})[0..1] AS mentions "
        "RETURN e.id AS id, e.name AS name, e.description AS description, "
        "  coalesce(e.weak, false) AS weak, "
        "  coalesce(e.evidence_chunk_ids, [])[0..2] AS evidence, mentions "
        "ORDER BY coalesce(e.weak, false), e.id"
    )
    rows = ctx.read(cypher, key=key)
    if not rows:
        return Derivation(query=cypher, note=f"{key} has no {role_en} in the graph")
    shown = rows[:RATIONALE_CAP]
    # Format strings, not "lead + space + key": Hebrew attaches the prefix with a maqaf
    # ("ב-KIP-932"), and a space there reads as a typo to anyone who reads Hebrew.
    facts = [
        Fact(
            en=head_en.format(key=key, n=len(rows), role=role_en),
            he=head_he.format(key=key, n=len(rows), role=role_he),
        )
    ]
    evidence = [key]
    for row in shown:
        evidence.append(row["id"])
        evidence.extend(row["evidence"] or [])
        detail = clip(row.get("description") or "", QUOTE_CHARS)
        mention = (row.get("mentions") or [{}])[0]
        if row["weak"] and mention.get("quote"):
            evidence.append(str(mention["chunk"]))
            detail = f"“{clip(mention['quote'], QUOTE_CHARS)}”"
        facts.append(
            Fact(
                en=f"{row['name']}{f' — {detail}' if detail else ''}",
                he=f"{row['name']}{f' — {detail}' if detail else ''}",
            )
        )
    extra = _more(len(shown), len(rows), en_noun=role_en, he_noun=role_he)
    if extra:
        facts.append(extra)
    return Derivation(facts=facts, evidence=_dedup(evidence)[:20], query=cypher)


def _kip_decisions(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    return _rationale(
        ctx,
        v["kip_most_decides"],
        pattern=f"-[:DECIDES]->(e:{ctx.label('Entity')})",
        role_en="decisions",
        role_he="החלטות",
        head_en="{key} records {n} {role}.",
        head_he="ב-{key} מתועדות {n} {role}.",
    )


def _kip_rejected_alternatives(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    return _rationale(
        ctx,
        v["kip_most_rejects"],
        pattern=f"-[:REJECTS]->(e:{ctx.label('Entity')})",
        role_en="rejected alternatives",
        role_he="חלופות שנדחו",
        head_en="{key} rejects {n} alternatives on the record.",
        head_he="{key} דוחה {n} חלופות מתועדות.",
    )


def _kip_motivating_problems(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    return _rationale(
        ctx,
        v["kip_most_motivated"],
        pattern=(
            f"-[:DECIDES]->(:{ctx.label('Entity')})-[:MOTIVATED_BY]->(e:{ctx.label('Entity')})"
        ),
        role_en="motivating problems",
        role_he="בעיות מניעות",
        head_en="{n} {role} drive {key}.",
        head_he="{n} {role} עומדות מאחורי {key}.",
    )


def _community_themes_for_component(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """The highest-ranked community reports whose members include the component's open bugs."""
    name = v["component_most_open_bugs"]
    cypher = (
        f"MATCH (b:{ctx.label('Bug')})-[:IN_COMPONENT]->"
        f"(:{ctx.label('Component')} {{`name`: $name}}) "
        "WHERE NOT coalesce(b.status, '') IN $closed "
        f"MATCH (b)-[:IN_COMMUNITY]->(c:{ctx.label('Community')}) WHERE c.title IS NOT NULL "
        "RETURN c.id AS id, c.title AS title, c.rank AS rank, c.level AS level, "
        "  count(DISTINCT b) AS bugs, collect(DISTINCT b.key)[0..3] AS examples "
        "ORDER BY c.rank DESC, bugs DESC, c.id"
    )
    rows = ctx.read(cypher, name=name, closed=list(CLOSED_STATUSES))
    if not rows:
        return Derivation(
            query=cypher,
            note=f"no summarised community holds an open bug of `{name}` "
            "(the partition may not have been re-summarised)",
        )
    shown = rows[:THEME_CAP]
    facts = [
        Fact(
            en=f"The open bugs in `{name}` fall into {len(rows)} summarised communities; "
            f"the top {len(shown)} by rank are:",
            he=f"הבאגים הפתוחים ב-`{name}` נופלים ל-{len(rows)} קהילות מסוכמות; "
            f"{len(shown)} המובילות לפי דירוג הן:",
        )
    ]
    evidence = [name]
    for row in shown:
        evidence.append(row["id"])
        evidence.extend(row["examples"])
        facts.append(
            Fact(
                en=f"{row['id']} (rank {row['rank']}) “{row['title']}” — "
                f"{row['bugs']} open bugs, e.g. {', '.join(row['examples'])}.",
                he=f"{row['id']} (דירוג {row['rank']}) ”{row['title']}“ — "
                f"{row['bugs']} באגים פתוחים, למשל {', '.join(row['examples'])}.",
            )
        )
    return Derivation(facts=facts, evidence=_dedup(evidence)[:20], query=cypher)


# ------------------------------------------------------- temporal: reuse, do not re-derive


def _result_keys(result: Result, limit: int) -> list[str]:
    return _dedup([item.key for item in result.items][:limit])


def _changes_between_versions(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    """`brain/retrieve/temporal.py` already answers this deterministically. Reuse it."""
    component, low, high = v["version_window"], v["version_low"], v["version_high"]
    query = f"brain.retrieve.temporal.changes_between({component!r}, {low!r}, {high!r})"
    try:
        result = temporal_mod.changes_between(ctx, component, low, high, log=False)
    except RetrieveError as exc:
        return Derivation(query=query, note=str(exc))
    # `changes_between` heads its items with a window summary row that is not a work item.
    items = [i for i in result.items if i.props.get("fix_version")]
    if not items:
        return Derivation(
            query=query, note=f"no work item in `{component}` shipped in ({low}, {high}]"
        )
    shown = items[:IMPACT_CAP]
    facts = [
        Fact(
            en=f"{len(items)} work item(s) in `{component}` shipped after {low} and up to {high}.",
            he=f"{len(items)} פריטי עבודה ב-`{component}` נשלחו אחרי {low} ועד {high}.",
        )
    ]
    for item in shown:
        version = item.props.get("fix_version")
        facts.append(
            Fact(
                en=f"{item.key} ({version}): {clip(item.title, QUOTE_CHARS)}",
                he=f"{item.key} ({version}): {clip(item.title, QUOTE_CHARS)}",
            )
        )
    extra = _more(len(shown), len(items), en_noun="items", he_noun="פריטים")
    if extra:
        facts.append(extra)
    # The versions cited are the ones the items actually carry ("3.8.0"), never the family
    # bounds the question names ("3.8") — those are not `Version.name` and not citable.
    versions = _dedup([str(i.props.get("fix_version") or "") for i in shown])
    return Derivation(
        facts=facts,
        evidence=_dedup([component, *versions, *(i.key for i in shown)])[:20],
        query=query,
    )


def _status_on_date(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    key, date = v["issue_most_status_changes"], v["status_date"]
    query = f"brain.retrieve.temporal.status_at({key!r}, {date!r})"
    try:
        result = temporal_mod.status_at(ctx, key, date, log=False)
    except RetrieveError as exc:
        return Derivation(query=query, note=str(exc))
    if not result.items:
        return Derivation(query=query, note=f"status_at returned nothing for {key} on {date}")
    head = result.items[0]
    status = head.props.get("status")
    basis = head.props.get("basis") or head.snippet
    if not status:
        return Derivation(
            query=query,
            note=f"the changelog cannot place {key} on {date}: {basis}",
        )
    facts = [
        Fact(
            en=f"On {date}, {key} was {status} ({basis}).",
            he=f"בתאריך {date} הסטטוס של {key} היה {status} ({basis}).",
        )
    ]
    # `status_at`'s item key is a synthetic `<key>@<date>` label, not a node. What is citable
    # is the work item and whatever chunk the result carries as provenance.
    chunks = [p.chunk_id for item in result.items for p in item.provenance if p.chunk_id]
    return Derivation(facts=facts, evidence=_dedup([key, *chunks])[:20], query=query)


def _assignees_over_time(ctx: RetrieveContext, v: Mapping[str, str]) -> Derivation:
    key = v["issue_most_assignees"]
    query = f"brain.retrieve.temporal.assignees_over_time({key!r})"
    result = temporal_mod.assignees_over_time(ctx, key, log=False)
    intervals = sorted(
        (item for item in result.items if item.kind == "Person"),
        key=lambda i: str(i.props.get("valid_from") or ""),
    )
    if not intervals:
        return Derivation(query=query, note=f"the changelog records no assignee for {key}")
    facts = [
        Fact(
            en=f"{key} was held by {len(intervals)} assignee(s), oldest first:",
            he=f"{key} הוחזק על ידי {len(intervals)} משויכים, מהישן לחדש:",
        )
    ]
    for item in intervals[:LIST_CAP]:
        window = item.snippet
        facts.append(
            Fact(
                en=f"{item.title} ({item.key}) {window}.",
                he=f"{item.title} ({item.key}) {window}.",
            )
        )
    return Derivation(
        facts=facts,
        evidence=_dedup([key, *(i.key for i in intervals[:LIST_CAP])])[:20],
        query=query,
    )


# ----------------------------------------------------------------------------- the table

Recipe = Callable[[RetrieveContext, Mapping[str, str]], Derivation]


@dataclass(frozen=True)
class Plan:
    """One recipe, and the named anchors it needs — the same names `competency.py` selects."""

    recipe: Recipe
    anchors: tuple[str, ...]


#: question id -> how its gold is derived. Keyed by id because `brain competency` writes
#: fixed ids; `test_eval_questions_gold.py` asserts this table covers exactly the ids in
#: `competency.TEMPLATES`, so a change there fails a test rather than skipping a question.
PLANS: dict[str, Plan] = {
    "cq01": Plan(_tests_last_run, ("issue_most_tests",)),
    "cq02": Plan(_commits_for_kip, ("kip_most_commits",)),
    "cq03": Plan(_component_owner, ("component_most_resolves",)),
    "cq04": Plan(_ado_items_for_kip, ("ado_story_kip",)),
    "cq05": Plan(_component_blast_radius, ("component_most_open_bugs",)),
    "cq06": Plan(_components_failing_tests_in_version, ("version_high",)),
    "cq07": Plan(_entity_dependents, ("entity_most_depends",)),
    "cq08": Plan(_open_issues_referencing_kip, ("kip_most_commits",)),
    "cq09": Plan(_kip_decisions, ("kip_most_decides",)),
    "cq10": Plan(_kip_rejected_alternatives, ("kip_most_rejects",)),
    "cq11": Plan(_kip_motivating_problems, ("kip_most_motivated",)),
    "cq12": Plan(_community_themes_for_component, ("component_most_open_bugs",)),
    "cq13": Plan(_changes_between_versions, ("version_window", "version_low", "version_high")),
    "cq14": Plan(_status_on_date, ("issue_most_status_changes", "status_date")),
    "cq15": Plan(_assignees_over_time, ("issue_most_assignees",)),
    "cq16": Plan(_tests_last_run, ("issue_most_tests",)),
    "cq17": Plan(_component_blast_radius, ("component_most_open_bugs",)),
    "cq18": Plan(_kip_decisions, ("kip_most_decides",)),
    "cq19": Plan(_changes_between_versions, ("version_window", "version_low", "version_high")),
}


def anchor_values(ctx: RetrieveContext) -> dict[str, str]:
    """Re-select every anchor the way `brain competency` did, plus the two derived ones."""
    found = competency.anchors(ctx)
    values = {name: a.key for name, a in found.items()}
    if "issue_most_status_changes" in found:
        values["status_date"] = competency._midpoint_date(found["issue_most_status_changes"].extra)
    if "version_window" in found:
        low, high = competency._version_pair(found["version_window"].extra)
        values["version_low"], values["version_high"] = low, high
    return values


def derive(
    ctx: RetrieveContext,
    rows: Sequence[Mapping[str, Any]],
    *,
    values: Mapping[str, str] | None = None,
) -> list[Gold]:
    """One `Gold` per competency row, in the order they were given."""
    resolved = dict(values) if values is not None else anchor_values(ctx)
    out: list[Gold] = []
    for row in rows:
        qid = str(row.get("id"))
        lang = str(row.get("lang") or "en")
        plan = PLANS.get(qid)
        if plan is None:
            out.append(_pending(qid, f"no derivation is registered for {qid}"))
            continue
        missing = [name for name in plan.anchors if name not in resolved]
        if missing:
            out.append(
                _pending(qid, f"the graph no longer selects an anchor for {', '.join(missing)}")
            )
            continue
        drift = _drift(row, plan, resolved)
        if drift:
            out.append(_pending(qid, drift))
            continue
        derivation = plan.recipe(ctx, resolved)
        if derivation.empty:
            out.append(_pending(qid, derivation.note or "the derivation found nothing"))
            continue
        out.append(
            Gold(
                id=qid,
                gold_answer=derivation.render(lang),
                gold_evidence=list(derivation.evidence),
                gold_source=GOLD_SOURCE,
                gold_query=derivation.query,
            )
        )
    return out


def _pending(qid: str, note: str) -> Gold:
    return Gold(
        id=qid,
        gold_answer=None,
        gold_evidence=[],
        gold_source="pending",
        gold_derived_by=DERIVED_BY,
        gold_note=note,
    )


def _drift(row: Mapping[str, Any], plan: Plan, values: Mapping[str, str]) -> str:
    """Refuse to derive a gold answer to a question the anchors no longer ask.

    `brain competency` rendered the question text around the anchors the graph nominated
    *then*. If the graph now nominates a different key, deriving against the new one would
    produce a correct answer to a question nobody asked.
    """
    named = {str(a) for a in (row.get("anchors") or [])}
    if not named:
        return ""
    for name in plan.anchors:
        value = values[name]
        if value not in named:
            return (
                f"anchor drift: the graph now selects {value!r} for {name}, but the question "
                f"names {sorted(named)} — re-run `brain competency` before deriving gold"
            )
    return ""


# ------------------------------------------------------------------------------ runner

#: Where the derived gold lives. A sidecar rather than an edit of `questions.jsonl`, because
#: `brain eval questions merge` rewrites that file from the batches every time it runs.
GOLD_FILE = "competency_gold.jsonl"
#: `$defs/row` caps `gold_answer`; a derivation that would overrun it is clipped and says so.
ANSWER_MAX = 1200


def verify(ctx: RetrieveContext, golds: Sequence[Gold]) -> list[Gold]:
    """Every cited key must exist. One that does not takes its row back to pending.

    This is the same check `brain eval questions merge` runs on the forged questions, applied
    to the derived gold before it is written — so `gold_evidence` is 100 % resolvable for all
    32 rows, not only the 13 an agent wrote.
    """
    from brain.eval.questions_report import existing_keys

    cited = {e for g in golds if not g.pending for e in g.gold_evidence}
    known = existing_keys(ctx, cited) if cited else set()
    out: list[Gold] = []
    for gold in golds:
        missing = [e for e in gold.gold_evidence if e not in known]
        if gold.pending or not missing:
            out.append(gold)
            continue
        out.append(
            _pending(
                gold.id,
                f"derived gold cited {len(missing)} key(s) the graph does not hold "
                f"({', '.join(missing[:3])}) — the recipe is wrong, not the question",
            )
        )
    return out


def read_gold(path: Any) -> dict[str, dict[str, Any]]:
    """The sidecar as `{id: row}`. Missing file is not an error: gold is optional."""
    import json
    from pathlib import Path

    target = Path(path)
    if not target.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and row.get("id"):
            out[str(row["id"])] = row
    return out


def run_gold(
    *,
    ctx: RetrieveContext,
    competency_rows: Sequence[Mapping[str, Any]],
    eval_dir: Any,
    reports_dir: Any,
) -> dict[str, Any]:
    """Derive, verify, clip, write the sidecar and the `gold_competency` report section."""
    import json
    from pathlib import Path

    from brain.eval.questions import write_section
    from brain.harvest.base import utc_now_iso

    values = anchor_values(ctx)
    golds = verify(ctx, derive(ctx, competency_rows, values=values))
    clipped: list[str] = []
    for gold in golds:
        if gold.gold_answer and len(gold.gold_answer) > ANSWER_MAX:
            gold.gold_answer = clip(gold.gold_answer, ANSWER_MAX)
            clipped.append(gold.id)

    path = Path(eval_dir) / GOLD_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(g.as_dict(), ensure_ascii=False) + "\n" for g in golds)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

    import hashlib

    derived = [g for g in golds if not g.pending]
    report = {
        "step": "eval.questions.gold-competency",
        "generated_at": utc_now_iso(),
        "path": str(path),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "anchors": dict(sorted(values.items())),
        "totals": {
            "rows": len(golds),
            "derived": len(derived),
            "pending": len(golds) - len(derived),
            "evidence_ids": sum(len(g.gold_evidence) for g in derived),
            "clipped_answers": len(clipped),
        },
        "rows": [
            {
                "id": g.id,
                "gold_source": g.gold_source,
                "evidence": len(g.gold_evidence),
                "answer_chars": len(g.gold_answer or ""),
                "gold_query": clip(g.gold_query, 300),
                "gold_note": g.gold_note,
            }
            for g in golds
        ],
        "pending": [{"id": g.id, "reason": g.gold_note} for g in golds if g.pending],
    }
    write_section(Path(reports_dir), "gold_competency", report)
    return report


def summarize_gold(report: Mapping[str, Any]) -> str:
    t = report["totals"]
    lines = [
        f"gold-competency: {t['derived']}/{t['rows']} derived from the graph "
        f"({t['pending']} pending), {t['evidence_ids']} evidence ids"
    ]
    for row in report["rows"]:
        mark = "OK " if row["gold_source"] != "pending" else "PEND"
        detail = row["gold_note"] if row["gold_source"] == "pending" else row["gold_query"]
        lines.append(f"  [{mark}] {row['id']} {row['evidence']:>2} ev  {clip(detail, 90)}")
    lines.append(f"  {report['path']}  sha256 {report['sha256'][:12]}")
    return "\n".join(lines)
