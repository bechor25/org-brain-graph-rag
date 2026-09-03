"""Jira connector — ASF Jira, anonymous, read-only (spec §3.1, probe 2026-09-03 §3).

Two probe findings shape this connector:

* ``/rest/api/2/search`` with ``expand=changelog`` returns the **complete** changelog and
  comment list — verified against ``/issue/{key}`` on a 113-history issue. So the whole
  slice is 3 requests, not 1,416.
* ``connect`` is a reserved JQL word: ``component = connect`` is HTTP 400. It must be quoted.
  The quoting lives in one constant below and is covered by a test, because getting it wrong
  fails loudly only on the first real call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from brain.harvest.base import (
    BaseConnector,
    Checkpoint,
    HarvestError,
    HttpFetcher,
    Page,
    ProbeResult,
    signature_of,
)

BASE_URL = "https://issues.apache.org/jira"
SEARCH_PATH = "/rest/api/2/search"

PROJECT = "KAFKA"
# `connect` is a reserved JQL word — it MUST stay quoted (probe §3, gotchas).
COMPONENTS_JQL = 'component in (streams, "connect", clients)'
COMPONENTS = ("streams", "connect", "clients")
WINDOW_START = "2023-01-01"
WINDOW_END = "2025-12-31"

PAGE_SIZE = 500  # server hard cap is 1000; 500 keeps a page under ~25MB
FIELDS = "*all"
EXPAND = "changelog"

_KIP = re.compile(r"KIP-\d+", re.IGNORECASE)


def build_jql(since: date | None = None) -> str:
    """The slice, as JQL. `--since` narrows by `updated` — ORDER BY must stay last."""
    clauses = [
        f"project = {PROJECT}",
        COMPONENTS_JQL,
        f'created >= "{WINDOW_START}"',
        f'created <= "{WINDOW_END}"',
    ]
    if since is not None:
        clauses.append(f'updated >= "{since.isoformat()}"')
    return " AND ".join(clauses) + " ORDER BY created ASC"


class JiraConnector(BaseConnector):
    name = "jira"
    page_stem = "issues"

    def __init__(
        self,
        raw_dir: Path,
        *,
        http: HttpFetcher | None = None,
        page_size: int = PAGE_SIZE,
        base_url: str = BASE_URL,
    ) -> None:
        super().__init__(raw_dir)
        self.page_size = page_size
        self.http = http or HttpFetcher(base_url)

    # -- identity ----------------------------------------------------------

    def query_text(self, since: date | None) -> str:
        return build_jql(since)

    def signature(self, since: date | None) -> str:
        return signature_of(
            {
                "jql": build_jql(since),
                "fields": FIELDS,
                "expand": EXPAND,
                "page_size": self.page_size,
            }
        )

    # -- probe -------------------------------------------------------------

    def probe(self) -> ProbeResult:
        try:
            payload = self.http.get_json(
                SEARCH_PATH, {"jql": build_jql(None), "maxResults": 0, "fields": "key"}
            )
        except HarvestError as exc:
            return ProbeResult(ok=False, detail=str(exc))
        total = payload.get("total")
        return ProbeResult(ok=bool(total), detail=f"{total} issues match the slice", total=total)

    # -- fetch -------------------------------------------------------------

    def fetch(self, since: date | None, checkpoint: Checkpoint) -> Iterator[Page]:
        if checkpoint.done:
            return
        start_at = int(checkpoint.cursor.get("start_at", 0))
        index = checkpoint.pages
        jql = build_jql(since)

        while True:
            payload = self.http.get_json(
                SEARCH_PATH,
                {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": self.page_size,
                    "fields": FIELDS,
                    "expand": EXPAND,
                },
            )
            issues = payload.get("issues") or []
            total = payload.get("total")
            if not issues:
                checkpoint.finish()
                return

            path = self.write_page(since, index, payload)
            page = Page(index=index, records=issues, path=path)
            start_at += len(issues)
            checkpoint.advance(page=page, cursor={"start_at": start_at}, total=total)
            yield page

            index += 1
            if total is not None and start_at >= int(total):
                checkpoint.finish()
                return

    # -- stats -------------------------------------------------------------

    def stats(self, since: date | None) -> dict[str, Any]:
        return analyze(iter_raw_issues(self.run_dir(since)))


# --------------------------------------------------------------------------- raw analysis


def iter_raw_issues(run_dir: Path) -> Iterator[dict[str, Any]]:
    """Re-read the raw pages from disk. Analysis never re-fetches."""
    if not run_dir.exists():
        return
    for path in sorted(run_dir.glob("issues-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        yield from payload.get("issues") or []


def _issue_text(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    parts = [str(fields.get("summary") or ""), str(fields.get("description") or "")]
    comment = fields.get("comment") or {}
    for c in comment.get("comments") or []:
        parts.append(str(c.get("body") or ""))
    return "\n".join(parts)


def _blank_bucket() -> dict[str, Any]:
    return {
        "n": 0,
        "_formal_links": 0,
        "_kip_mention": 0,
        "_changelog": 0,
        "_comments": 0,
        "_assignee": 0,
        "_fix_versions": 0,
    }


def _finish_bucket(b: dict[str, Any]) -> dict[str, Any]:
    n = b["n"] or 1
    return {
        "n": b["n"],
        "pct_formal_links": round(100 * b["_formal_links"] / n, 1),
        "pct_kip_mention": round(100 * b["_kip_mention"] / n, 1),
        "pct_changelog_present": round(100 * b["_changelog"] / n, 1),
        "avg_comments": round(b["_comments"] / n, 2),
        "pct_assignee": round(100 * b["_assignee"] / n, 1),
        "pct_fix_versions": round(100 * b["_fix_versions"] / n, 1),
    }


def analyze(issues: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Acceptance stats + the per-component link-density table, in one pass over raw."""
    buckets: dict[str, dict[str, Any]] = {c: _blank_bucket() for c in COMPONENTS}
    buckets["all"] = _blank_bucket()
    other = _blank_bucket()

    total = 0
    with_changelog = 0
    with_comment_field = 0
    keys: set[str] = set()

    for issue in issues:
        total += 1
        keys.add(str(issue.get("key") or ""))
        fields = issue.get("fields") or {}
        changelog = issue.get("changelog") or {}
        has_changelog = "changelog" in issue and "histories" in changelog
        has_histories = bool(changelog.get("histories"))
        comment = fields.get("comment")
        comments = (comment or {}).get("comments") or []

        with_changelog += 1 if has_changelog else 0
        with_comment_field += 1 if comment is not None else 0

        flags = {
            "_formal_links": bool(fields.get("issuelinks")),
            "_kip_mention": bool(_KIP.search(_issue_text(issue))),
            "_changelog": has_histories,
            "_assignee": fields.get("assignee") is not None,
            "_fix_versions": bool(fields.get("fixVersions")),
        }
        names = {str((c or {}).get("name") or "").lower() for c in (fields.get("components") or [])}
        targets = [buckets[c] for c in COMPONENTS if c in names] or [other]
        targets.append(buckets["all"])
        for bucket in targets:
            bucket["n"] += 1
            bucket["_comments"] += len(comments)
            for k, v in flags.items():
                bucket[k] += 1 if v else 0

    density = {name: _finish_bucket(b) for name, b in buckets.items()}
    if other["n"]:
        density["other"] = _finish_bucket(other)

    n = total or 1
    return {
        "issues": total,
        "distinct_keys": len(keys - {""}),
        "pct_with_changelog": round(100 * with_changelog / n, 1),
        "pct_with_comment_field": round(100 * with_comment_field / n, 1),
        "link_density": density,
    }
