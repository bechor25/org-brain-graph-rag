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
from collections.abc import Iterable, Iterator, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from brain.harvest.auth import credentials_for
from brain.harvest.base import (
    BaseConnector,
    Checkpoint,
    HarvestError,
    HttpFetcher,
    Page,
    ProbeResult,
    checkpoint_pages,
    signature_of,
)
from brain.harvest.registry import SourceConfig, get_registry

SEARCH_PATH = "/rest/api/2/search"

#: Protocol defaults. A `sources.yaml` entry may override any of them under `options`;
#: the base URL, the JQL and the project keys have no default at all — they are the
#: organisation, and the organisation lives in the registry.
PAGE_SIZE = 500  # server hard cap is 1000; 500 keeps a page under ~25MB
FIELDS = "*all"
EXPAND = "changelog"
ORDER_BY = "created ASC"
SINCE_FIELD = "updated"

#: Components the link-density table groups by. Only a report grouping — the slice itself
#: is whatever `query` says.
COMPONENTS: tuple[str, ...] = ()

_KIP = re.compile(r"KIP-\d+", re.IGNORECASE)


def build_jql(source: SourceConfig, since: date | None = None) -> str:
    """The slice, as JQL. `--since` narrows by `updated` — ORDER BY must stay last."""
    clauses = [source.query]
    if since is not None:
        clauses.append(f'{source.option("since_field", SINCE_FIELD)} >= "{since.isoformat()}"')
    return " AND ".join(clauses) + f" ORDER BY {source.option('order_by', ORDER_BY)}"


class JiraConnector(BaseConnector):
    name = "jira"
    page_stem = "issues"

    def __init__(
        self,
        raw_dir: Path,
        *,
        source: SourceConfig | None = None,
        http: HttpFetcher | None = None,
    ) -> None:
        super().__init__(raw_dir)
        self.source = source or get_registry().source(self.name)
        self.name = self.source.name
        self.page_size = self.source.int_option("page_size", PAGE_SIZE)
        self.fields = str(self.source.option("fields", FIELDS))
        self.expand = str(self.source.option("expand", EXPAND))
        self.components = tuple(self.source.option("components", COMPONENTS) or ())
        self.credentials = credentials_for(self.source)
        self.http = http or HttpFetcher(
            self.source.base_url,
            headers=self.credentials.headers(),
            secrets=self.credentials.secrets,
        )

    # -- identity ----------------------------------------------------------

    def query_text(self, since: date | None) -> str:
        return build_jql(self.source, since)

    def signature(self, since: date | None) -> str:
        return signature_of(
            {
                "jql": build_jql(self.source, since),
                "fields": self.fields,
                "expand": self.expand,
                "page_size": self.page_size,
            }
        )

    # -- probe -------------------------------------------------------------

    def probe(self) -> ProbeResult:
        try:
            payload = self.http.get_json(
                SEARCH_PATH,
                {"jql": build_jql(self.source, None), "maxResults": 0, "fields": "key"},
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
        jql = build_jql(self.source, since)

        while True:
            payload = self.http.get_json(
                SEARCH_PATH,
                {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": self.page_size,
                    "fields": self.fields,
                    "expand": self.expand,
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
        return analyze(iter_raw_issues(self.run_dir(since)), components=self.components)


# --------------------------------------------------------------------------- raw analysis


def iter_raw_issues(run_dir: Path) -> Iterator[dict[str, Any]]:
    """Re-read the raw pages from disk. Analysis never re-fetches.

    Pages come from the checkpoint's file list, never from a glob — see
    `brain.harvest.base.checkpoint_pages`.
    """
    for path in checkpoint_pages(run_dir):
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
        "_history": 0,
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
        "pct_with_history": round(100 * b["_history"] / n, 1),
        "avg_comments": round(b["_comments"] / n, 2),
        "pct_assignee": round(100 * b["_assignee"] / n, 1),
        "pct_fix_versions": round(100 * b["_fix_versions"] / n, 1),
    }


def analyze(
    issues: Iterable[dict[str, Any]], *, components: Sequence[str] = COMPONENTS
) -> dict[str, Any]:
    """Acceptance stats + the per-component link-density table, in one pass over raw."""
    buckets: dict[str, dict[str, Any]] = {c: _blank_bucket() for c in components}
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
        # Two different questions: did `expand=changelog` come back at all (a harvest
        # correctness check), and does this issue actually have history (a corpus fact —
        # an issue created and never touched legitimately has none).
        has_changelog = "changelog" in issue and "histories" in changelog
        has_histories = bool(changelog.get("histories"))
        comment = fields.get("comment")
        comments = (comment or {}).get("comments") or []

        with_changelog += 1 if has_changelog else 0
        with_comment_field += 1 if comment is not None else 0

        flags = {
            "_formal_links": bool(fields.get("issuelinks")),
            "_kip_mention": bool(_KIP.search(_issue_text(issue))),
            "_history": has_histories,
            "_assignee": fields.get("assignee") is not None,
            "_fix_versions": bool(fields.get("fixVersions")),
        }
        names = {str((c or {}).get("name") or "").lower() for c in (fields.get("components") or [])}
        targets = [buckets[c] for c in components if c in names] or [other]
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
        "pct_changelog_expanded": round(100 * with_changelog / n, 1),
        "pct_with_comment_field": round(100 * with_comment_field / n, 1),
        "link_density": density,
    }
