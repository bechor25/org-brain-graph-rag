"""Confluence connector — ASF cwiki, anonymous, read-only (spec §3.1, probe §6).

The probe's win here: the CQL search endpoint honours ``expand=body.storage``, so 25 full
page bodies arrive per call and all ~1,391 KIP pages cost ~56 requests. We pull **all** of
them — raw HTML, unconverted. Storage is cheap; a second full crawl is not, and deciding
later to embed more pages must not mean re-fetching.

Paging follows ``_links.next`` (the server's own cursor) rather than assuming
``start += limit`` — the offset is read back out of that link.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from brain.harvest.base import (
    BaseConnector,
    Checkpoint,
    HarvestError,
    HttpFetcher,
    Page,
    ProbeResult,
    signature_of,
)

BASE_URL = "https://cwiki.apache.org/confluence"
SEARCH_PATH = "/rest/api/content/search"

SPACE = "KAFKA"
CQL = f'space={SPACE} and type=page and title ~ "KIP-"'
EXPAND = "body.storage,version,history,ancestors,metadata.labels"
LIMIT = 25  # the server trims larger limits when bodies are expanded


def build_cql(since: date | None = None) -> str:
    """`--since` narrows by CQL `lastmodified` (Confluence's own updated field)."""
    if since is None:
        return CQL
    return f'{CQL} and lastmodified >= "{since.isoformat()}"'


def next_start(payload: dict[str, Any], *, current: int, limit: int) -> int | None:
    """Read the next offset out of `_links.next`; None means the crawl is finished."""
    links = payload.get("_links") or {}
    href = links.get("next")
    if not href:
        return None
    query = parse_qs(urlparse(html.unescape(str(href))).query)
    values = query.get("start")
    if values:
        try:
            return int(values[0])
        except ValueError:
            pass
    return current + limit


class ConfluenceConnector(BaseConnector):
    name = "confluence"
    page_stem = "pages"

    def __init__(
        self,
        raw_dir: Path,
        *,
        http: HttpFetcher | None = None,
        limit: int = LIMIT,
        base_url: str = BASE_URL,
    ) -> None:
        super().__init__(raw_dir)
        self.limit = limit
        self.http = http or HttpFetcher(base_url)

    # -- identity ----------------------------------------------------------

    def query_text(self, since: date | None) -> str:
        return build_cql(since)

    def signature(self, since: date | None) -> str:
        return signature_of({"cql": build_cql(since), "expand": EXPAND, "limit": self.limit})

    # -- probe -------------------------------------------------------------

    def probe(self) -> ProbeResult:
        try:
            payload = self.http.get_json(SEARCH_PATH, {"cql": build_cql(None), "limit": 1})
        except HarvestError as exc:
            return ProbeResult(ok=False, detail=str(exc))
        total = payload.get("totalSize")
        detail = f"{total} KIP pages in space {SPACE}"
        return ProbeResult(ok=bool(total), detail=detail, total=total)

    # -- fetch -------------------------------------------------------------

    def fetch(self, since: date | None, checkpoint: Checkpoint) -> Iterator[Page]:
        if checkpoint.done:
            return
        start = int(checkpoint.cursor.get("start", 0))
        index = checkpoint.pages
        cql = build_cql(since)

        while True:
            payload = self.http.get_json(
                SEARCH_PATH,
                {"cql": cql, "expand": EXPAND, "limit": self.limit, "start": start},
            )
            results = payload.get("results") or []
            if not results:
                checkpoint.finish()
                return

            path = self.write_page(since, index, payload)
            page = Page(index=index, records=results, path=path)
            following = next_start(payload, current=start, limit=self.limit)
            checkpoint.advance(
                page=page,
                cursor={"start": following if following is not None else start + len(results)},
                total=payload.get("totalSize"),
            )
            yield page

            if following is None:
                checkpoint.finish()
                return
            start = following
            index += 1

    # -- stats -------------------------------------------------------------

    def stats(self, since: date | None) -> dict[str, Any]:
        return analyze(iter_raw_pages(self.run_dir(since)))


# --------------------------------------------------------------------------- raw analysis


def iter_raw_pages(run_dir: Path) -> Iterator[dict[str, Any]]:
    if not run_dir.exists():
        return
    for path in sorted(run_dir.glob("pages-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        yield from payload.get("results") or []


def analyze(pages: Iterator[dict[str, Any]]) -> dict[str, Any]:
    total = 0
    with_body = 0
    body_chars = 0
    ids: set[str] = set()
    for page in pages:
        total += 1
        ids.add(str(page.get("id") or ""))
        body = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
        if body:
            with_body += 1
            body_chars += len(body)
    n = total or 1
    return {
        "pages": total,
        "distinct_ids": len(ids - {""}),
        "pages_with_body_storage": with_body,
        "pct_with_body_storage": round(100 * with_body / n, 1),
        "avg_body_chars": round(body_chars / (with_body or 1)),
    }
