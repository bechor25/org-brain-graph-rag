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

from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

from brain.canon.mappers.base import Bundle, FieldTracker, parse_dt
from brain.canon.mentions import extract_refs, filter_refs
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
        "fields.resolution",
        "fields.resolutiondate",
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


def _raw_container_names(fields: dict[str, Any]) -> Iterator[str]:
    for group in ("components", "fixVersions", "versions"):
        for item in fields.get(group) or []:
            if item.get("name"):
                yield str(item["name"])


def container_name(raw: str) -> str:
    """`"producer "` → `"producer"`.

    Jira component and version names are free text and carry whatever whitespace the
    person who created them typed. A trailing space survives every MERGE and produces a
    `Component` node no query and no reader will ever match.
    """
    return " ".join(str(raw).split())


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


def map_issues(issues: Iterable[dict[str, Any]]) -> Bundle:
    bundle = Bundle(source=SOURCE)
    tracker = FieldTracker(mapped=MAPPED)
    no_components = 0
    truncated_changelogs = 0
    renamed: Counter = Counter()
    any_text_mention = 0
    text_only = 0

    for issue in issues:
        tracker.observe(raw_paths(issue))
        fields = issue.get("fields") or {}
        key = str(issue.get("key") or "")
        created = parse_dt(fields.get("created"))
        if not key or created is None:
            bundle.warn("issue_without_key_or_created", id=issue.get("id"))
            continue

        components = [
            container_name(c["name"]) for c in fields.get("components") or [] if c.get("name")
        ]
        for name in components:
            bundle.container(SOURCE, "component", name)
        fix_versions = [
            container_name(v["name"]) for v in fields.get("fixVersions") or [] if v.get("name")
        ]
        affects = [container_name(v["name"]) for v in fields.get("versions") or [] if v.get("name")]
        for raw_name in _raw_container_names(fields):
            if (clean := container_name(raw_name)) != raw_name:
                renamed[f"{raw_name!r} -> {clean!r}"] += 1
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
        text_refs = extract_refs(text)
        # What the *text* alone claims, before a formal link gets the credit for it. The
        # two percentages this feeds are different questions: "does prose carry refs at
        # all" and "does prose carry refs Jira does not already state".
        mentioned = {
            r.key for r in filter_refs(t for t in text_refs if t.key != key)[0] if r.kind == "issue"
        }
        any_text_mention += 1 if mentioned else 0
        text_only += 1 if mentioned - {link.target for link in links} else 0

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
                resolution=(fields.get("resolution") or {}).get("name"),
                resolved_at=parse_dt(fields.get("resolutiondate")),
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
                    text_refs, [link.target for link in links], self_keys=[key]
                ),
                raw_url=browse_url(issue),
            )
        )

    n = len(bundle.workitems) or 1
    bundle.stats = {
        "issues": len(bundle.workitems),
        "with_components": len(bundle.workitems) - no_components,
        "without_components": no_components,
        "normalized_container_names": {
            "names_changed": len(renamed),
            "occurrences": sum(renamed.values()),
            "changes": dict(renamed.most_common()),
        },
        "with_links": sum(1 for wi in bundle.workitems if wi.links),
        "formal_links": sum(len(wi.links) for wi in bundle.workitems),
        "with_parent": sum(1 for wi in bundle.workitems if wi.parent),
        "with_resolution": sum(1 for wi in bundle.workitems if wi.resolution),
        "comments": sum(len(wi.comments) for wi in bundle.workitems),
        "changelog_entries": sum(len(wi.changelog) for wi in bundle.workitems),
        "truncated_changelogs": truncated_changelogs,
        "workitems_with_any_text_issue_mention": any_text_mention,
        "pct_workitems_with_any_text_issue_mention": round(100 * any_text_mention / n, 1),
        "workitems_with_text_only_issue_ref": text_only,
        "pct_workitems_with_text_only_issue_ref": round(100 * text_only / n, 1),
        "unmapped_fields": tracker.report(),
    }
    return bundle
