"""Jira issue → `WorkItem` (+ the Persons, Components and Versions it implies).

The interesting part is `refs`. A Jira issue states some of its relationships formally
(`fields.issuelinks`) and the rest in prose — a summary that says "follow-up to
KAFKA-15123", a comment that links a KIP. Both become refs; `via` remembers which is
which, because the gap between the two is the number this whole step exists to measure.

Both ends of a formal link are kept as the source recorded them (`direction: out` on the
issue that declares it, `in` on the other). Collapsing the pair into one edge is
`brain load`'s job — canon must not lose information the source gave it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

from brain.canon.mappers.base import Bundle, FieldTracker, parse_dt
from brain.canon.mentions import ISSUE_PROJECT_ALLOWLIST, extract_refs
from brain.canon.models import ChangelogEntry, Comment, Link, WorkItem

SOURCE = "jira"

#: Raw paths a canonical field (or a Person / Container) actually represents. Everything
#: else lands in `unmapped_fields` — 92 custom fields, `resolution`, `votes`, `watches`…
MAPPED = frozenset(
    {
        "key",
        "self",
        "fields",
        "changelog",
        "fields.summary",
        "fields.description",
        "fields.issuetype",
        "fields.status",
        "fields.priority",
        "fields.created",
        "fields.updated",
        "fields.reporter",
        "fields.assignee",
        "fields.creator",
        "fields.components",
        "fields.labels",
        "fields.fixVersions",
        "fields.versions",
        "fields.parent",
        "fields.issuelinks",
        "fields.comment",
        "fields.project",
    }
)


#: Jira's own mention syntax. `extract_refs` matches `@user`, so these are invisible to
#: it — measured here rather than assumed away, because widening the regex is a change to
#: `brain/canon/mentions.py` and needs a brief.
_JIRA_USER_MENTION = re.compile(r"\[~([A-Za-z0-9_.@-]+)\]")

#: `kafka-15123` in prose. The issue-key regex is deliberately uppercase (`[A-Z]…`) so it
#: does not match every `word-123`; the cost — keys of an *allowlisted* project written in
#: any other case — is counted here.
_ANY_CASE_KEY = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,9})-\d+\b")


def raw_paths(issue: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    yield from issue.items()
    for name, value in (issue.get("fields") or {}).items():
        yield f"fields.{name}", value


def browse_url(issue: dict[str, Any]) -> str | None:
    """`…/rest/api/2/issue/13516395` → `…/browse/KAFKA-14572`, from the record itself."""
    self_url = str(issue.get("self") or "")
    key = str(issue.get("key") or "")
    if "/rest/" not in self_url or not key:
        return None
    return f"{self_url.split('/rest/', 1)[0]}/browse/{key}"


def link_type(raw: dict[str, Any]) -> str:
    """The source's own name for the relation, lowercased (`Blocker` → `blocker`).

    Both ends of a link report the same `type.name`, which is what lets `brain load`
    collapse the reciprocal pair into a single `LINKS_TO{type}`.
    """
    return str((raw.get("type") or {}).get("name") or "relates").strip().lower()


def _links(issue: dict[str, Any]) -> list[Link]:
    out: list[Link] = []
    for raw in issue["fields"].get("issuelinks") or []:
        for side, direction in (("outwardIssue", "out"), ("inwardIssue", "in")):
            target = (raw.get(side) or {}).get("key")
            if target:
                out.append(Link(type=link_type(raw), target=str(target), direction=direction))
    return out


def _comments(issue: dict[str, Any], bundle: Bundle) -> list[Comment]:
    out: list[Comment] = []
    for raw in ((issue["fields"].get("comment") or {}).get("comments")) or []:
        author = raw.get("author") or {}
        out.append(
            Comment(
                author=bundle.identity(
                    SOURCE, author.get("name"), display=author.get("displayName")
                ),
                at=parse_dt(raw.get("created")),
                body=str(raw.get("body") or ""),
            )
        )
    return out


def _changelog(issue: dict[str, Any], bundle: Bundle) -> list[ChangelogEntry]:
    out: list[ChangelogEntry] = []
    for history in (issue.get("changelog") or {}).get("histories") or []:
        at = parse_dt(history.get("created"))
        if at is None:
            continue
        author = history.get("author") or {}
        by = bundle.identity(SOURCE, author.get("name"), display=author.get("displayName"))
        for item in history.get("items") or []:
            out.append(
                ChangelogEntry(
                    field=str(item.get("field") or ""),
                    # `fromString`/`toString` are the human-readable values; the numeric
                    # `from`/`to` are Jira ids that mean nothing outside this instance.
                    from_=item.get("fromString"),
                    to=item.get("toString"),
                    at=at,
                    by=by,
                )
            )
    return out


class _MissedSignal:
    """Signal the deterministic extractor leaves on the floor, counted per source.

    Neither number is a bug to fix here: both are properties of `brain/canon/mentions.py`,
    which is shared and tested. They are in the report so the planner can decide whether
    the extra refs are worth widening the regexes for.
    """

    def __init__(self) -> None:
        self.mention_occurrences = 0
        self.mention_issues = 0
        self.mention_users: set[str] = set()
        self.lowercase_key_issues = 0

    def observe(self, text: str) -> None:
        mentions = _JIRA_USER_MENTION.findall(text)
        if mentions:
            self.mention_occurrences += len(mentions)
            self.mention_issues += 1
            self.mention_users.update(mentions)
        if any(
            project != project.upper() and project.upper() in ISSUE_PROJECT_ALLOWLIST
            for project in _ANY_CASE_KEY.findall(text)
        ):
            self.lowercase_key_issues += 1

    def report(self) -> dict[str, Any]:
        return {
            "jira_user_mentions": {
                "note": "`[~username]` is Jira's mention syntax; extract_refs matches `@user`",
                "occurrences": self.mention_occurrences,
                "issues": self.mention_issues,
                "distinct_users": len(self.mention_users),
            },
            "lowercase_issue_keys": {
                "note": "the issue-key regex is uppercase-only, so `kafka-15123` is not a ref",
                "issues": self.lowercase_key_issues,
            },
        }


def map_issues(issues: Iterable[dict[str, Any]]) -> Bundle:
    bundle = Bundle(source=SOURCE)
    tracker = FieldTracker(mapped=MAPPED)
    no_components = 0
    truncated_changelogs = 0
    missed = _MissedSignal()

    for issue in issues:
        tracker.observe(raw_paths(issue))
        fields = issue.get("fields") or {}
        key = str(issue.get("key") or "")
        created = parse_dt(fields.get("created"))
        if not key or created is None:
            bundle.warn("issue_without_key_or_created", id=issue.get("id"))
            continue

        components = [str(c.get("name")) for c in fields.get("components") or [] if c.get("name")]
        for name in components:
            bundle.container(SOURCE, "component", name)
        fix_versions = [
            str(v.get("name")) for v in fields.get("fixVersions") or [] if v.get("name")
        ]
        affects = [str(v.get("name")) for v in fields.get("versions") or [] if v.get("name")]
        for name in (*fix_versions, *affects):
            bundle.container(SOURCE, "version", name)

        reporter = fields.get("reporter") or {}
        assignee = fields.get("assignee") or {}
        creator = fields.get("creator") or {}
        bundle.identity(SOURCE, creator.get("name"), display=creator.get("displayName"))

        comments = _comments(issue, bundle)
        links = _links(issue)
        title = str(fields.get("summary") or "")
        description = str(fields.get("description") or "")
        text = "\n".join([title, description, *(c.body for c in comments)])
        missed.observe(text)

        changelog = (issue.get("changelog") or {}).get("histories") or []
        if len(changelog) < int((issue.get("changelog") or {}).get("total") or 0):
            truncated_changelogs += 1
            bundle.warn("changelog_truncated", key=key, returned=len(changelog))

        if not components:
            no_components += 1
            bundle.warn("issue_without_component", key=key)

        bundle.workitems.append(
            WorkItem(
                id=f"{SOURCE}:{key}",
                key=key,
                source=SOURCE,
                type=str((fields.get("issuetype") or {}).get("name") or "Task"),
                title=title,
                description=description,
                status=str((fields.get("status") or {}).get("name") or "Unknown"),
                priority=(fields.get("priority") or {}).get("name"),
                created=created,
                updated=parse_dt(fields.get("updated")),
                reporter=bundle.identity(
                    SOURCE, reporter.get("name"), display=reporter.get("displayName")
                ),
                assignee=bundle.identity(
                    SOURCE, assignee.get("name"), display=assignee.get("displayName")
                ),
                components=components,
                labels=[str(x) for x in fields.get("labels") or []],
                fix_versions=fix_versions,
                affects_versions=affects,
                parent=(fields.get("parent") or {}).get("key"),
                links=links,
                comments=comments,
                changelog=_changelog(issue, bundle),
                refs=bundle.refs.collect(
                    extract_refs(text), [link.target for link in links], self_keys=[key]
                ),
                raw_url=browse_url(issue),
            )
        )

    text_only = sum(
        1 for wi in bundle.workitems if any(r.kind == "issue" and r.via == "text" for r in wi.refs)
    )
    bundle.stats = {
        "issues": len(bundle.workitems),
        "with_components": len(bundle.workitems) - no_components,
        "without_components": no_components,
        "with_links": sum(1 for wi in bundle.workitems if wi.links),
        "formal_links": sum(len(wi.links) for wi in bundle.workitems),
        "with_parent": sum(1 for wi in bundle.workitems if wi.parent),
        "comments": sum(len(wi.comments) for wi in bundle.workitems),
        "changelog_entries": sum(len(wi.changelog) for wi in bundle.workitems),
        "truncated_changelogs": truncated_changelogs,
        "workitems_with_text_only_issue_ref": text_only,
        "pct_workitems_with_text_only_issue_ref": round(
            100 * text_only / (len(bundle.workitems) or 1), 1
        ),
        "unmapped_fields": tracker.report(),
        "text_signal_not_extracted": missed.report(),
    }
    return bundle
