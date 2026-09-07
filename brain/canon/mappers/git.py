"""Commit → `Change`, and the pull requests the commit subjects imply.

98.7% of Kafka commit subjects end in `(#21175)`, so PullRequest nodes are free: one
`Change{kind:"pr"}` per distinct number, derived from the commit that carries it. There
is no GitHub API call here — 60 anonymous requests/hour would make ~6,000 PRs a hundred
hour job for metadata the subject line already gives us.

Known limitation, recorded in the report: `Change.id = "pr:<N>"` has no repo in it. The
POC has exactly one repo; a second one would need `pr:<owner>/<repo>#<N>`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

from brain.canon.mappers.base import Bundle, FieldTracker, parse_dt
from brain.canon.mentions import extract_refs
from brain.canon.models import Change
from brain.harvest.registry import get_registry

SOURCE = "git"


def commit_base_url() -> str:
    """The repository `Change.raw_url` points into — `sources.yaml`, not a constant."""
    for source in get_registry().enabled():
        if source.type == "git":
            return source.base_url.rstrip("/")
    return ""


MAPPED = frozenset(
    {"sha", "author_name", "author_email", "authored_at", "subject", "body", "files"}
)

#: `(#21175)` in a squashed-merge subject. Bare `#21175` in a body is a text mention (it
#: becomes a ref), not evidence that this commit *is* that PR.
PR_IN_SUBJECT = re.compile(r"\(#(\d{1,7})\)")

#: The *trailing* one is the merge GitHub performed; an earlier `(#N)` in the same subject
#: is the author quoting another PR. Only the trailing one becomes `Change.pr`.
PR_AT_END = re.compile(r"\(#(\d{1,7})\)\s*$")


def raw_paths(commit: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    yield from commit.items()


def commit_message(commit: dict[str, Any]) -> str:
    subject = str(commit.get("subject") or "")
    body = str(commit.get("body") or "").strip()
    return f"{subject}\n\n{body}" if body else subject


def map_commits(commits: Iterable[dict[str, Any]], *, base_url: str | None = None) -> Bundle:
    # Resolved once per run, not once per commit: `Change.raw_url` is the only canonical
    # field that carries the repository's address.
    base_url = (base_url if base_url is not None else commit_base_url()).rstrip("/")
    bundle = Bundle(source=SOURCE)
    tracker = FieldTracker(mapped=MAPPED)
    # pr number -> the commit that owns it. A backport carries the same `(#N)`, so the
    # earliest commit wins and the choice does not depend on git log order.
    pull_requests: dict[str, dict[str, Any]] = {}
    shared_prs = 0
    multi_pr_subjects = 0

    for commit in commits:
        tracker.observe(raw_paths(commit))
        sha = str(commit.get("sha") or "")
        at = parse_dt(commit.get("authored_at"))
        if not sha or at is None:
            bundle.warn("commit_without_sha_or_date", sha=sha or None)
            continue

        email = str(commit.get("author_email") or "").lower()
        name = str(commit.get("author_name") or "")
        bundle.identity(SOURCE, email, display=name, email=email)
        message = commit_message(commit)
        subject = str(commit.get("subject") or "")
        own_pr = PR_AT_END.search(subject)
        pr_numbers = list(dict.fromkeys(PR_IN_SUBJECT.findall(subject)))
        if len(pr_numbers) > 1:
            multi_pr_subjects += 1

        bundle.changes.append(
            Change(
                id=sha,
                kind="commit",
                pr=f"pr:{own_pr.group(1)}" if own_pr else None,
                message=message,
                author_name=name or None,
                author_email=email or None,
                at=at,
                files=[str(f) for f in commit.get("files") or []],
                # The commit's own PR is on `pr`, not in `refs`: an edge to the pull
                # request it *is* is not the same fact as a mention of another one.
                refs=bundle.refs.collect(
                    extract_refs(message), self_keys=[own_pr.group(1)] if own_pr else []
                ),
                raw_url=f"{base_url}/commit/{sha}",
            )
        )

        for number in pr_numbers:
            current = pull_requests.get(number)
            if current is None:
                pull_requests[number] = commit
            else:
                shared_prs += 1
                if (at, sha) < (parse_dt(current["authored_at"]), str(current["sha"])):
                    pull_requests[number] = commit

    for number, commit in pull_requests.items():
        subject = str(commit.get("subject") or "")
        bundle.changes.append(
            Change(
                id=f"pr:{number}",
                kind="pr",
                message=subject,
                author_name=str(commit.get("author_name") or "") or None,
                author_email=str(commit.get("author_email") or "").lower() or None,
                at=parse_dt(commit.get("authored_at")),
                # The files belong to the commit; duplicating them here would double every
                # `TOUCHES` edge in the graph.
                files=[],
                refs=bundle.refs.collect(extract_refs(subject), self_keys=[number]),
                raw_url=f"{base_url}/pull/{number}",
            )
        )

    commits_n = sum(1 for c in bundle.changes if c.kind == "commit")
    keyed = sum(
        1 for c in bundle.changes if c.kind == "commit" and any(r.kind == "issue" for r in c.refs)
    )
    bundle.stats = {
        "commits": commits_n,
        "pull_requests": len(pull_requests),
        "commits_with_pr": sum(1 for c in bundle.changes if c.kind == "commit" and c.pr),
        "subjects_naming_more_than_one_pr": multi_pr_subjects,
        "commits_with_issue_ref": keyed,
        "pct_commits_with_issue_ref": round(100 * keyed / (commits_n or 1), 1),
        "prs_claimed_by_more_than_one_commit": shared_prs,
        "files_touched": sum(len(c.files) for c in bundle.changes),
        "unmapped_fields": tracker.report(),
        "limitations": [
            "Change.id for a pull request is 'pr:<N>' with no repository in it: the POC "
            "harvests exactly one repo (apache/kafka). A second repo would collide."
        ],
    }
    return bundle
