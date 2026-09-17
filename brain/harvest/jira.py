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

#: The field an **incremental slice** pull compares against. `--since` on its own asks
#: "what changed inside my slice" and that is `updated`; `--slice incremental` asks the
#: different question "what does my slice not have", and the answer is by creation date.
#: Overridable per source (`options.new_since_field`) because not every tracker calls it
#: `created`.
NEW_SINCE_FIELD = "created"

#: A date literal on the right-hand side of a comparison, which is what makes a clause a
#: *window* rather than a filter on some other field that happens to use `<=`.
_DATE_LITERAL = re.compile(r'^["\']?\d{4}-\d{2}-\d{2}')
_COMPARISON = re.compile(
    r"^(?P<field>[A-Za-z_][A-Za-z0-9_.]*)\s*(?P<op>>=|<=|!=|>|<|=)\s*(?P<rhs>.+)$"
)

#: Components the link-density table groups by. Only a report grouping — the slice itself
#: is whatever `query` says.
COMPONENTS: tuple[str, ...] = ()


def split_and(jql: str) -> list[str]:
    """Split a JQL string on top-level ` AND `, respecting quotes and parentheses.

    A regex would be wrong on exactly the clause this corpus has:
    ``component in (streams, "connect", clients)``. The scanner is tiny, and it is the only
    thing between "open the window" and "delete half the query".
    """
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    i = 0
    while i < len(jql):
        ch = jql[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(jql):
                buf.append(jql[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and jql[i : i + 5].upper() == " AND ":
            out.append("".join(buf).strip())
            buf = []
            i += 5
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [c for c in out if c]


def _caps_the_window(clause: str, field: str) -> bool:
    """Would this clause keep an incremental catch-up out of its own window?

    Two cases, and only two: a comparison on the field the catch-up filters by (its old
    window, in either direction), and an *upper* bound on any date (`created <=
    "2025-12-31"` — the end of the base slice, which a catch-up must never stop at). A
    `priority <= 3` is left alone because its right-hand side is not a date.
    """
    m = _COMPARISON.match(clause.strip())
    if m is None:
        return False
    if m.group("field").lower() == field.lower():
        return True
    return m.group("op") in ("<", "<=") and bool(_DATE_LITERAL.match(m.group("rhs").strip()))


def partition_window(source: SourceConfig, field: str) -> tuple[list[str], list[str]]:
    """`(kept, dropped)` — the registry query with its date window taken off.

    Dropped is returned rather than discarded so the probe and the harvest report can show
    what the incremental window removed instead of asking a reader to diff two JQL strings.
    """
    kept: list[str] = []
    dropped: list[str] = []
    for clause in split_and(source.query):
        (dropped if _caps_the_window(clause, field) else kept).append(clause)
    return kept, dropped


def new_since_field(source: SourceConfig) -> str:
    return str(source.option("new_since_field", NEW_SINCE_FIELD))


def build_jql(source: SourceConfig, since: date | None = None, *, new: bool = False) -> str:
    """The slice, as JQL. ORDER BY must stay last.

    Without `since` this is the registry's `query`. With `since` and `new=False` it is the
    same query narrowed by `updated` — "what changed inside my slice", unchanged. With
    `new=True` it is the **incremental slice**: the base window comes off the query and is
    replaced by `created >= <since>`, because a catch-up that inherits
    ``created <= "2025-12-31"`` from the corpus definition returns nothing. The git
    connector has said the same thing since Plan 1 — `--since` overrides the start and
    drops the end, because a catch-up must not stop at the end of the old slice.
    """
    order = f" ORDER BY {source.option('order_by', ORDER_BY)}"
    if since is None:
        return source.query + order
    if not new:
        field = str(source.option("since_field", SINCE_FIELD))
        return f'{source.query} AND {field} >= "{since.isoformat()}"' + order

    field = new_since_field(source)
    kept, _dropped = partition_window(source, field)
    if not kept:
        raise HarvestError(
            f"the incremental window left nothing of sources[{source.id}].query "
            f"({source.query!r}): every clause bounded a date. Narrowing by project or "
            "component has to survive, or the pull is the whole tracker."
        )
    return " AND ".join([*kept, f'{field} >= "{since.isoformat()}"']) + order


class JiraConnector(BaseConnector):
    name = "jira"
    page_stem = "issues"
    supports_new_slice = True

    def __init__(
        self,
        raw_dir: Path,
        *,
        source: SourceConfig | None = None,
        http: HttpFetcher | None = None,
        max_records: int | None = None,
        new_slice: bool = False,
    ) -> None:
        super().__init__(raw_dir, max_records=max_records)
        #: Pull the records the base slice does not have, rather than the ones inside it
        #: that changed. Set by `brain harvest --slice incremental`.
        self.new_slice = bool(new_slice)
        self.source = source or get_registry().source(self.name)
        # The registry id, not the type: `data/raw/<id>/` and the report key.
        self.name = self.source.id
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
        return build_jql(self.source, since, new=self.new_slice)

    def signature(self, since: date | None) -> str:
        payload: dict[str, Any] = {
            "jql": self.query_text(since),
            "fields": self.fields,
            "expand": self.expand,
            "page_size": self.page_size,
        }
        # A capped pull is a different result set, so it gets a different checkpoint:
        # re-running without `--limit` restarts rather than resuming into a run that
        # stopped on purpose. Added **only** when there is a cap — a key that always
        # serialized would change every existing checkpoint's signature, and a stale
        # signature does not fail, it silently re-fetches 19,266 records and deletes the
        # pages already on disk as "stale". `tests/test_registry.py` records the answer.
        if self.max_records is not None:
            payload["max_records"] = self.max_records
        return signature_of(payload)

    # -- probe -------------------------------------------------------------

    def probe(self) -> ProbeResult:
        try:
            payload = self.http.get_json(
                SEARCH_PATH,
                {"jql": self.query_text(None), "maxResults": 0, "fields": "key"},
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
        jql = self.query_text(since)

        while True:
            # With `--limit 10` the page is 10 rows, not 500: the cap belongs in the
            # request, not in a filter applied to a 25 MB answer we asked for anyway.
            page_size = self.page_size
            if self.max_records is not None:
                page_size = max(1, min(page_size, self.max_records - checkpoint.records))
            payload = self.http.get_json(
                SEARCH_PATH,
                {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": page_size,
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
            if self.max_records is not None and checkpoint.records >= self.max_records:
                checkpoint.finish()
                return
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


def _kip_pattern() -> re.Pattern[str]:
    """`pct_kip_mention` asks "does this issue name a design doc". Which prefix that is
    belongs to the wiki source in `sources.yaml`, not to the Jira connector."""
    from brain.harvest.confluence import default_document_spec

    return default_document_spec().mention_pattern


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
    kip = _kip_pattern()
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
            "_kip_mention": bool(kip.search(_issue_text(issue))),
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
