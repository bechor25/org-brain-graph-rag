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
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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
    utc_now_iso,
)
from brain.harvest.registry import DocumentKeySpec, SourceConfig, get_registry

SEARCH_PATH = "/rest/api/content/search"

#: Protocol defaults; `sources.yaml` overrides them under `options`. The space, the CQL
#: and the title pattern are organisation-specific and have no default in code.
EXPAND = "body.storage,version,history,ancestors,metadata.labels"
LIMIT = 25  # the server trims larger limits when bodies are expanded
SINCE_FIELD = "lastmodified"


@lru_cache(maxsize=1)
def default_document_spec() -> DocumentKeySpec:
    """The title→key rule of the registry's Confluence source.

    Cached because `brain canon` calls :func:`kip_key` once per page and the pattern is
    a compiled regex either way. `brain.harvest.registry.reset_caches` clears it.
    """
    registry = get_registry()
    for source in registry.sources:
        if source.type == "confluence":
            return source.document
    return DocumentKeySpec()


def build_cql(source: SourceConfig, since: date | None = None) -> str:
    """`--since` narrows by CQL `lastmodified` (Confluence's own updated field)."""
    if since is None:
        return source.query
    field = source.option("since_field", SINCE_FIELD)
    return f'{source.query} and {field} >= "{since.isoformat()}"'


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
        source: SourceConfig | None = None,
        http: HttpFetcher | None = None,
    ) -> None:
        super().__init__(raw_dir)
        self.source = source or get_registry().source(self.name)
        self.name = self.source.name
        self.limit = self.source.int_option("limit", LIMIT)
        self.expand = str(self.source.option("expand", EXPAND))
        self.credentials = credentials_for(self.source)
        self.http = http or HttpFetcher(
            self.source.base_url,
            headers=self.credentials.headers(),
            secrets=self.credentials.secrets,
        )

    # -- identity ----------------------------------------------------------

    def query_text(self, since: date | None) -> str:
        return build_cql(self.source, since)

    def signature(self, since: date | None) -> str:
        return signature_of(
            {"cql": build_cql(self.source, since), "expand": self.expand, "limit": self.limit}
        )

    # -- probe -------------------------------------------------------------

    def probe(self) -> ProbeResult:
        try:
            payload = self.http.get_json(
                SEARCH_PATH, {"cql": build_cql(self.source, None), "limit": 1}
            )
        except HarvestError as exc:
            return ProbeResult(ok=False, detail=str(exc))
        total = payload.get("totalSize")
        detail = f"{total} pages match {self.source.query}"
        return ProbeResult(ok=bool(total), detail=detail, total=total)

    # -- fetch -------------------------------------------------------------

    def fetch(self, since: date | None, checkpoint: Checkpoint) -> Iterator[Page]:
        if checkpoint.done:
            return
        start = int(checkpoint.cursor.get("start", 0))
        index = checkpoint.pages
        cql = build_cql(self.source, since)

        while True:
            payload = self.http.get_json(
                SEARCH_PATH,
                {"cql": cql, "expand": self.expand, "limit": self.limit, "start": start},
            )
            results = payload.get("results") or []
            if not results:
                checkpoint.finish()
                return

            path = self.write_page(since, index, payload)
            page = Page(index=index, records=results, path=path)
            following = next_start(payload, current=start, limit=self.limit)
            if following is not None and following <= start:
                # A cursor that does not move forward is an infinite loop that looks like
                # a working crawl. Trust the page we just got instead.
                self.errors.append(
                    {
                        "when": utc_now_iso(),
                        "kind": "cursor",
                        "detail": f"_links.next pointed at start={following} from start="
                        f"{start}; advancing by the {len(results)} results received",
                        "fatal": False,
                    }
                )
                following = start + len(results)
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
        return analyze(iter_raw_pages(self.run_dir(since)), spec=self.source.document)


# --------------------------------------------------------------------------- raw analysis


def iter_raw_pages(run_dir: Path) -> Iterator[dict[str, Any]]:
    """Pages come from the checkpoint's file list, never from a glob (see base)."""
    for path in checkpoint_pages(run_dir):
        payload = json.loads(path.read_text(encoding="utf-8"))
        yield from payload.get("results") or []


def kip_key(title: str, spec: DocumentKeySpec | None = None) -> str | None:
    """`KIP-N` parsed out of a page title, or None if the title carries no number.

    The pattern is `sources.yaml` → `sources[confluence].document.title_pattern`; an org
    whose design docs are called `RFC-12` changes that line, not this function.
    """
    return (spec or default_document_spec()).key(title)


def spaced_kip_key(title: str, spec: DocumentKeySpec | None = None) -> str | None:
    """The key a `KIP 156 …` title *would* have if the loose form were accepted."""
    return (spec or default_document_spec()).loose_key(title)


def analyze(
    pages: Iterator[dict[str, Any]], *, spec: DocumentKeySpec | None = None
) -> dict[str, Any]:
    """Coverage plus the `KIP-N` key problem canon has to solve.

    `Document.key = KIP-N` parsed from the title is not injective on this space: drafts,
    "Copy of …" pages and release-notes pages carry a number that another page already
    owns, and some titles carry no number at all. Counting it here means canon meets the
    collisions as a documented number rather than as a MERGE that silently unified two
    different pages.

    Pages whose title writes the number with a space ("KIP 156 Add option …") are reported
    apart from the genuinely keyless ones: they are recoverable if canon widens the parser,
    and folding them in would create *more* collisions, not fewer.
    """
    spec = spec or default_document_spec()
    total = 0
    with_body = 0
    body_chars = 0
    ids: set[str] = set()
    unparseable: list[dict[str, str]] = []
    space_separated: list[dict[str, str]] = []
    by_key: dict[str, list[dict[str, str]]] = {}

    for page in pages:
        total += 1
        page_id = str(page.get("id") or "")
        title = str(page.get("title") or "")
        ids.add(page_id)
        body = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
        if body:
            with_body += 1
            body_chars += len(body)

        key = spec.key(title)
        if key is not None:
            by_key.setdefault(key, []).append({"id": page_id, "title": title})
            continue
        spaced = spec.loose_key(title)
        if spaced is not None:
            space_separated.append({"id": page_id, "title": title, "would_be_key": spaced})
        else:
            unparseable.append({"id": page_id, "title": title})

    collisions = {k: v for k, v in sorted(by_key.items()) if len(v) > 1}

    # What accepting the space form would cost: it recovers pages, but some of the numbers
    # it recovers are already taken. Canon should see both totals before choosing a parser.
    widened = {k: list(v) for k, v in by_key.items()}
    for page in space_separated:
        widened.setdefault(page["would_be_key"], []).append(page)
    widened_collisions = {k: v for k, v in widened.items() if len(v) > 1}

    n = total or 1
    return {
        "pages": total,
        "distinct_ids": len(ids - {""}),
        "pages_with_body_storage": with_body,
        "pct_with_body_storage": round(100 * with_body / n, 1),
        "avg_body_chars": round(body_chars / (with_body or 1)),
        "kip_key": {
            "distinct_keys": len(by_key),
            "pages_without_key": len(unparseable) + len(space_separated),
            "pages_unparseable": len(unparseable),
            "pages_space_separated": len(space_separated),
            "colliding_keys": len(collisions),
            "pages_in_collisions": sum(len(v) for v in collisions.values()),
            "if_space_form_accepted": {
                "distinct_keys": len(widened),
                "colliding_keys": len(widened_collisions),
                "pages_in_collisions": sum(len(v) for v in widened_collisions.values()),
            },
            "unparseable": unparseable,
            "space_separated": space_separated,
            "collisions": collisions,
        },
    }
