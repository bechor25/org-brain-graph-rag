"""Canonical vocabulary → graph vocabulary. Pure functions, no driver, no I/O.

Everything here is a *closed* decision the step brief made (§05, planner decisions 2, 3,
6): which secondary label a work item type earns, which relationship a Jira link type
becomes, how a changelog row gets a stable identity, and how a sequence of assignee
changes becomes intervals. They live apart from the loaders so the decisions can be
unit-tested without a database, and so a reader can audit the whole vocabulary in one
file.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from brain.canon.models import ChangelogEntry, Link, WorkItem

# --------------------------------------------------------------------------- labels

#: Work item types whose graph label is not simply the CamelCase of the source's spelling.
#: `User Story` is what Azure DevOps calls what the spec's schema calls a `Story`.
TYPE_ALIASES: dict[str, str] = {
    "sub-task": "SubTask",
    "subtask": "SubTask",
    "new feature": "NewFeature",
    "user story": "Story",
    "test execution": "TestExecution",
    "test plan": "TestPlan",
    "test set": "TestSet",
}

#: Jira's `Test` issue type (123 issues in this corpus) is not Xray's `Test`. One is "a
#: Jira issue about testing", the other is "a test case with runs and results", and a
#: query for the second must not return the first. Only the source can tell them apart.
JIRA_TEST_LABEL = "JiraTest"

_NON_LABEL = re.compile(r"[^A-Za-z0-9]")


def _camel(token: str) -> str:
    """`bug` → `Bug`, but `TestExecution` stays `TestExecution`."""
    return token if any(c.isupper() for c in token[1:]) else token.capitalize()


def workitem_label(item_type: str, source: str) -> str | None:
    """The secondary label for a work item, or None if the type is unusable.

    None is never silently dropped: the caller counts it under `unknown_workitem_types`.
    """
    raw = " ".join(str(item_type or "").split())
    if not raw:
        return None
    alias = TYPE_ALIASES.get(raw.lower())
    label = alias or "".join(_camel(t) for t in _NON_LABEL.sub(" ", raw).split())
    if not label or label[0].isdigit():
        return None
    if source == "jira" and label == "Test":
        return JIRA_TEST_LABEL
    return label


#: Container kind → node label. A kind with no label here is reported, never guessed at.
CONTAINER_LABELS: dict[str, str] = {
    "component": "Component",
    "version": "Version",
    "sprint": "Sprint",
    "area": "Area",
    "space": "Space",
}

#: Container kinds that are deliberately not loaded (planner decision). The synthetic
#: layer states a test plan twice — as the work item `XP-12` and as a container
#: `xray:testplan:<name>` — and the work item is the node: it has the links, the
#: hierarchy and the key everything else points at. Loading the container too would put
#: two different nodes behind `MATCH (p:TestPlan)`. They are counted, never silent.
SKIPPED_CONTAINER_KINDS: frozenset[str] = frozenset({"testplan", "testset"})


def is_skipped_container(kind: str) -> bool:
    return str(kind or "").lower() in SKIPPED_CONTAINER_KINDS


#: The property each container label merges on. All of them are the container's name.
CONTAINER_KEY = "name"


def container_label(kind: str) -> str | None:
    return CONTAINER_LABELS.get(str(kind or "").lower())


# ---------------------------------------------------------------------------- links

#: Jira link type name (lowercased, as `brain canon` records it) → the closed
#: `LINKS_TO.type` vocabulary. Every value is oriented *from the issue that declared the
#: link to the issue it named*, which is what Jira's own outward description says:
#: `Blocker` reads "blocks", `Cloners` reads "is a clone of", `Required` reads "requires".
#: The nine real Kafka types are from `data/reports/canon.json` → `link_types`; `related`,
#: `defect`, `blocks` and `duplicates` are the synthetic layer's spellings — it states the
#: relation the way the graph does, not the way Jira names the link type, and every value
#: of the link enum in `brain/synth/schema.json` has to land somewhere on purpose.
LINK_TYPE_MAP: dict[str, str] = {
    "reference": "relates",
    "related": "relates",
    "duplicate": "duplicates",
    "duplicates": "duplicates",
    "blocker": "blocks",
    "blocks": "blocks",
    "problem/incident": "causes",
    "cloners": "clones",
    "supercedes": "supersedes",
    "issue split": "splits",
    "dependent": "depends_on",
    "required": "depends_on",
    "defect": "defect",
}

#: Anything the map does not know becomes this, with the source's own name kept in
#: `raw_type` so the loss is visible in the graph and counted in the report.
DEFAULT_LINK_TYPE = "relates"

#: Types whose two ends mean the same thing ("is related to" both ways). Their endpoints
#: are ordered lexicographically so that A→B and B→A collapse into one edge instead of
#: two edges that say the same thing in opposite directions.
SYMMETRIC_LINK_TYPES = frozenset({DEFAULT_LINK_TYPE})

#: Link types that are not `LINKS_TO` at all but a relationship of their own (spec §2.4).
#: `reverse` means the edge runs from the *named* issue to the *declaring* one: a test
#: execution declares `executes → XT-1`, and the graph says `XT-1 EXECUTED_IN XE-1`.
DEDICATED_LINK_RELS: dict[str, tuple[str, Literal["forward", "reverse"]]] = {
    "tests": ("TESTS", "forward"),
    "executes": ("EXECUTED_IN", "reverse"),
    "parent": ("PARENT_OF", "reverse"),
}

#: The work item label that turns a `tests` link into membership instead of coverage.
#: A `Test` that declares `tests → KAFKA-100` covers that issue; a `TestPlan` that
#: declares `tests → XT-10007` contains that test, which is `IN_PLAN`, not `TESTS`.
#: `IN_PLAN` therefore always targets a work item, by key — never a container.
TEST_PLAN_LABEL = "TestPlan"


@dataclass(frozen=True)
class LinkEdge:
    """One directed edge a formal link asks for, before duplicates are collapsed."""

    rel: str  # LINKS_TO | TESTS | EXECUTED_IN | IN_PLAN | PARENT_OF
    src: str
    dst: str
    type: str | None = None  # LINKS_TO only
    raw_type: str = ""


def link_edge(
    owner_key: str,
    link: Link,
    owner_label: str | None = None,
    target_label: str | None = None,
) -> LinkEdge | None:
    """The edge a single canonical `Link` asks for, or None for a self-link.

    Both ends of a Jira link reach us as separate records — the declaring issue with
    `direction="out"`, the named issue with `direction="in"` — and both produce the same
    ordered pair here. Collapsing the pair is :func:`dedupe_link_edges`.

    The labels are needed only because `tests` means two different things depending on
    which kind of item is at each end (see `TEST_PLAN_LABEL`); both are passed so the
    routing is the same whichever side declared the link.
    """
    raw = str(link.type or "").strip().lower()
    src, dst = (owner_key, link.target) if link.direction == "out" else (link.target, owner_key)
    if src == dst:
        return None
    if raw == "tests" and TEST_PLAN_LABEL in (owner_label, target_label):
        # the plan names its tests; the graph says test IN_PLAN plan
        plan, member = (
            (owner_key, link.target)
            if owner_label == TEST_PLAN_LABEL
            else (
                link.target,
                owner_key,
            )
        )
        return LinkEdge(rel="IN_PLAN", src=member, dst=plan, raw_type=raw)
    if dedicated := DEDICATED_LINK_RELS.get(raw):
        rel, orientation = dedicated
        if orientation == "reverse":
            src, dst = dst, src
        return LinkEdge(rel=rel, src=src, dst=dst, raw_type=raw)
    norm = LINK_TYPE_MAP.get(raw, DEFAULT_LINK_TYPE)
    if norm in SYMMETRIC_LINK_TYPES and dst < src:
        src, dst = dst, src
    return LinkEdge(rel="LINKS_TO", src=src, dst=dst, type=norm, raw_type=raw)


def is_known_link_type(raw_type: str) -> bool:
    raw = str(raw_type or "").strip().lower()
    return raw in LINK_TYPE_MAP or raw in DEDICATED_LINK_RELS


def dedupe_link_edges(edges: Iterable[LinkEdge]) -> list[LinkEdge]:
    """One edge per (relationship, endpoints, type) — reciprocal declarations collapse.

    When two different source link types normalize onto the same edge (this corpus has 6
    such pairs, e.g. `blocked` and `blocker` between the same two issues), `raw_type`
    keeps the lexicographically first one so the result does not depend on file order.
    """
    merged: dict[tuple[str, str, str, str | None], set[str]] = {}
    for e in edges:
        merged.setdefault((e.rel, e.src, e.dst, e.type), set()).add(e.raw_type)
    return [
        LinkEdge(rel=rel, src=src, dst=dst, type=type_, raw_type=sorted(raws)[0])
        for (rel, src, dst, type_), raws in merged.items()
    ]


# --------------------------------------------------------------------- status changes

#: Changelog fields that become `StatusChange` event nodes. `RemoteIssueLink` (17,718
#: rows — 59% of the whole changelog) and `Link` are deliberately absent: they record
#: that *a link was edited*, which the `LINKS_TO`/`REFERENCES` edges already state better.
STATUS_CHANGE_FIELDS: frozenset[str] = frozenset(
    {"status", "assignee", "resolution", "Fix Version", "Component", "priority"}
)

#: Fields the brief names as noise to drop without counting them as a surprise.
IGNORED_CHANGELOG_FIELDS: frozenset[str] = frozenset({"RemoteIssueLink", "Link"})


def statuschange_id(item_key: str, entry: ChangelogEntry) -> str:
    """Stable identity of one changelog row: `sha1(key|field|at|from|to)`.

    DEVIATION from brief §6, which specifies `sha1(key|field|at|to)`. Measured on the
    real corpus that formula collapses 26 rows into 10 ids: removing several fix versions
    or components in one edit produces rows that share key, field, timestamp *and* a null
    `to`, and differ only in `from` (e.g. KAFKA-16423 dropping 3.6.2, 3.8.0 and 3.7.1 at
    2024-03-26T12:11:59.791Z). Adding `from` makes all 7,607 rows distinct and keeps the
    id deterministic and content-addressed, which is what the decision was for.
    """
    at = entry.at.isoformat()
    payload = f"{item_key}|{entry.field}|{at}|{entry.from_}|{entry.to}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()  # noqa: S324 - id, not a secret


# ------------------------------------------------------------------ assignee history


#: Where an interval's identity came from. `field` means `WorkItem.assignee`, which is
#: the only place the *current* owner is spelled the way `Person.id` spells it.
AssignmentSource = Literal["changelog", "field"]


@dataclass(frozen=True)
class Assignment:
    """One `ASSIGNED_TO{valid_from, valid_to}` interval. `valid_to=None` = still open."""

    person_key: str  # identity key in the source, e.g. the Jira username
    valid_from: datetime
    valid_to: datetime | None = None
    source: AssignmentSource = "changelog"


@dataclass(frozen=True)
class AliasCandidate:
    """Two spellings of what is almost certainly one person, for `brain resolve`.

    Produced when the changelog's last assignee identity and `fields.assignee` disagree:
    Jira wrote `JIRAUSER298607` in the history and `kirktrue` in the issue, and the
    interval they describe is the same one. This step does not merge them — resolution is
    measured against gold pairs and must not be handed silent merges — it only records the
    pair with the evidence that produced it.
    """

    changelog_key: str
    field_key: str
    work_item: str


@dataclass(frozen=True)
class AssignmentHistory:
    intervals: list[Assignment]
    alias_candidates: list[AliasCandidate]
    zero_length_dropped: int = 0


def assignee_events(item: WorkItem) -> list[ChangelogEntry]:
    """Assignee changelog rows, oldest first, order-stable for equal timestamps."""
    rows = [e for e in item.changelog if e.field == "assignee"]
    return sorted(rows, key=lambda e: e.at)


def assignment_history(item: WorkItem) -> AssignmentHistory:
    """Who held this work item, and when — from identity keys only.

    Built from `from_id`/`to_id`, never from the display strings: 9 display names in this
    corpus are shared by 18 different people, so `to` cannot identify anyone.

    Four rules, in order:
    1. If the first transition names a predecessor, that person held the item from
       `created` until that transition.
    2. Every transition that names a new assignee opens an interval, closed by the next
       transition (or left open).
    3. If the open interval's identity is not the one `WorkItem.assignee` names, the two
       are the same tenure spelled two ways (`JIRAUSER298607` vs `kirktrue`), so the
       *interval is replaced*, not closed and re-opened: closing it would leave a
       zero-length edge asserting a hand-over that never happened. The displaced pair
       leaves as an `AliasCandidate`.
    4. An interval that still ends where it starts is dropped and counted; nothing in this
       graph should claim somebody owned an item for no time at all.
    """
    events = assignee_events(item)
    intervals: list[Assignment] = []
    if events:
        if events[0].from_id:
            intervals.append(Assignment(events[0].from_id, item.created, events[0].at))
        for i, e in enumerate(events):
            if not e.to_id:
                continue
            end = events[i + 1].at if i + 1 < len(events) else None
            intervals.append(Assignment(e.to_id, e.at, end))
    elif item.assignee:
        intervals.append(Assignment(item.assignee, item.created, None, source="field"))

    aliases: list[AliasCandidate] = []
    current = item.assignee
    if current and not any(a.person_key == current and a.valid_to is None for a in intervals):
        open_idx = next((i for i, a in enumerate(intervals) if a.valid_to is None), None)
        if open_idx is None:
            # the history ends with an *un*assignment, so the field opens a new interval
            start = events[-1].at if events else item.created
            intervals.append(Assignment(current, start, None, source="field"))
        else:
            displaced = intervals[open_idx]
            intervals[open_idx] = Assignment(current, displaced.valid_from, None, source="field")
            aliases.append(AliasCandidate(displaced.person_key, current, item.key))

    seen: set[tuple[str, datetime]] = set()
    out: list[Assignment] = []
    dropped = 0
    for a in intervals:
        if a.valid_to is not None and a.valid_to <= a.valid_from:
            dropped += 1
            continue
        k = (a.person_key, a.valid_from)
        if k in seen:
            continue
        seen.add(k)
        out.append(a)
    return AssignmentHistory(intervals=out, alias_candidates=aliases, zero_length_dropped=dropped)


def assignment_intervals(item: WorkItem) -> list[Assignment]:
    """Just the intervals — see :func:`assignment_history` for the rest."""
    return assignment_history(item).intervals


# ------------------------------------------------------------------------- test runs

#: One line of an Xray execution's comments: `"XT-10007: FAIL (assignment never arrived)"`.
#: The synthetic layer states runs this way (`brain/canon/synthetic_spec.md`), so reading
#: them is deterministic parsing of a stated format, not extraction — no LLM is involved.
_RUN_LINE = re.compile(
    r"^\s*(?P<key>(?:XT)-\d+)\s*:\s*(?P<status>PASS|FAIL)\s*(?:\((?P<reason>[^)]*)\))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TestRun:
    test_key: str
    status: str  # PASS | FAIL
    reason: str | None = None


def parse_test_runs(body: str) -> list[TestRun]:
    """Every `"<XT-n>: PASS|FAIL (<reason>)"` line in an execution comment, in order.

    A line that does not match the format is not a run and is left alone: the comment is
    also where a human writes prose, and guessing at prose is what `brain extract` is for.
    """
    runs: list[TestRun] = []
    for line in (body or "").splitlines():
        m = _RUN_LINE.match(line)
        if not m:
            continue
        reason = (m.group("reason") or "").strip() or None
        runs.append(TestRun(m.group("key").upper(), m.group("status").upper(), reason))
    return runs


# ------------------------------------------------------------------------- pr numbers


def pr_number(value: str | None) -> int | None:
    """`"pr:14001"` or `"14001"` → `14001`; anything else → None.

    `#000000` appears three times in this corpus (a hex colour in a code block that the
    ref regex read as a pull request). It normalizes to 0, matches no pull request, and
    is counted as a dangling ref rather than silently attached to something.
    """
    raw = str(value or "")
    raw = raw[3:] if raw.startswith("pr:") else raw
    return int(raw) if raw.isdigit() else None


# ------------------------------------------------------------------------ references

#: Ref kinds that become a `REFERENCES` edge (`user` becomes `MENTIONS_PERSON`, `url`
#: has no node to point at and is only counted).
REFERENCE_KINDS: frozenset[str] = frozenset({"issue", "kip", "pr"})


def merge_via(current: str, incoming: str) -> str:
    """`link` beats `text`: the formal statement is the stronger evidence."""
    return "link" if "link" in (current, incoming) else "text"
