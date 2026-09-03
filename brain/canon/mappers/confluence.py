"""Confluence page → `Document`, including the `KIP-N` key problem.

Two jobs, both of which the harvest report already measured as a number:

**Storage format → Markdown.** Confluence "storage format" is XHTML plus its own
`ac:`/`ri:` namespaces. Handing it to a converter unprepared silently costs refs: the
Jira macro renders `<ac:parameter ac:name="key">KAFKA-20186</ac:parameter>` glued to the
server UUID, so the regex sees `…fb15bKAFKA-20186` and finds nothing. So the markup is
normalized first — internal page links become their target title (a `KIP-N` ref!), code
macros become fenced blocks, every other `ac:`/`ri:` tag becomes a space — and only then
converted.

**`KIP-N` is not a key.** 45 pages share 21 numbers: drafts, `Copy of …`, `OLD …`,
release-notes pages, and genuinely different KIPs that reused a number. One page per
number becomes `kind=KIP, key=KIP-N`; the rest become `kind=Page,
key=confluence:<id>` labelled `kip-variant` with the canonical key in `ancestors`, so a
variant is reachable from the KIP but can never be mistaken for it. The report lists
every collision with the decision and flags the ones where the rule had to fall back on
body size (i.e. where two real KIPs share a number).
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Iterator
from typing import Any

from markdownify import markdownify

from brain.canon.mappers.base import Bundle, FieldTracker, parse_dt
from brain.canon.mentions import extract_refs
from brain.canon.models import Document
from brain.harvest.confluence import kip_key, spaced_kip_key

SOURCE = "confluence"
DEFAULT_SPACE = "KAFKA"

MAPPED = frozenset(
    {
        "id",
        "title",
        "body",
        "body.storage",
        "version",
        "version.number",
        "version.when",
        "version.by",
        "history",
        "history.createdBy",
        "history.createdDate",
        "ancestors",
        "metadata",
        "metadata.labels",
        "_links",
        "_links.webui",
        "_links.self",
    }
)

#: Title fragments that mark a page as a copy of another page's number, not its owner.
#: The planner's list: `[DRAFT]`, `Copy of`, `OLD`, release notes.
VARIANT_MARKER = re.compile(r"\[draft\]|\bdraft\b|\bcopy of\b|\bold\b|release notes", re.IGNORECASE)

_RI_PAGE = re.compile(r"<ri:page\b[^>]*?ri:content-title=\"([^\"]*)\"[^>]*?>")
_RI_ATTACHMENT = re.compile(r"<ri:attachment\b[^>]*?ri:filename=\"([^\"]*)\"[^>]*?>")
_CDATA_BODY = re.compile(
    r"<ac:plain-text-body>\s*<!\[CDATA\[(.*?)\]\]>\s*</ac:plain-text-body>", re.DOTALL
)
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_XHTML_TAG = re.compile(r"</?(?:ac|ri):[^>]*>")
_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)


def storage_to_markdown(storage: str) -> str:
    """Confluence storage XHTML → Markdown, with the wiki's own namespaces flattened."""
    if not storage:
        return ""
    text = _RI_PAGE.sub(lambda m: f" {html.unescape(m.group(1))} ", storage)
    text = _RI_ATTACHMENT.sub(lambda m: f" {html.unescape(m.group(1))} ", text)
    text = _CDATA_BODY.sub(lambda m: f"<pre>{html.escape(m.group(1))}</pre>", text)
    text = _CDATA.sub(lambda m: f" {html.escape(m.group(1))} ", text)
    # Whatever `ac:`/`ri:` markup is left is layout, not content — but it must not glue
    # its neighbours together, or `KAFKA-20186` stops being a word.
    text = _XHTML_TAG.sub(" ", text)

    md = markdownify(
        text,
        heading_style="ATX",
        bullets="*",
        # Backslash-escaping `_` and `*` would turn `group_id` into `group\_id`, which is
        # noise for the reader and for the embeddings in `brain chunk`.
        escape_underscores=False,
        escape_asterisks=False,
        escape_misc=False,
    )
    return _BLANK_RUN.sub("\n\n", _TRAILING_WS.sub("", md)).strip()


def raw_paths(page: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    """Top-level fields plus one level into the dicts the mapper reads from."""
    for name, value in page.items():
        yield name, value
        if name in ("version", "history", "metadata", "body", "_links") and isinstance(value, dict):
            for sub, sub_value in value.items():
                yield f"{name}.{sub}", sub_value


def page_url(page: dict[str, Any]) -> str | None:
    links = page.get("_links") or {}
    self_url, webui = str(links.get("self") or ""), str(links.get("webui") or "")
    if "/rest/" not in self_url or not webui:
        return None
    return f"{self_url.split('/rest/', 1)[0]}{webui}"


def page_space(page: dict[str, Any]) -> str:
    webui = str((page.get("_links") or {}).get("webui") or "")
    if m := re.search(r"/spaces/([^/]+)/", webui):
        return m.group(1)
    expandable = str((page.get("_expandable") or {}).get("space") or "")
    return expandable.rsplit("/", 1)[-1] or DEFAULT_SPACE


def body_of(page: dict[str, Any]) -> str:
    return str(((page.get("body") or {}).get("storage") or {}).get("value") or "")


def canonical_rank(page: dict[str, Any], key: str) -> tuple[int, int, int, int, int]:
    """How strongly a page claims to *be* `KIP-N`. Highest wins; every term is a tie-break.

    1. the title starts with the key (`KIP-848: …`, not `… (KIP-848) - Release Notes`)
    2. the title carries no variant marker (`[DRAFT]`, `Copy of`, `OLD`, release notes)
    3. the key is followed by a colon — the KIP template's own form
    4. the longer body — a stub or a scratch page loses to the real proposal
    5. the lower page id — pure determinism, never a judgement
    """
    title = str(page.get("title") or "")
    escaped = re.escape(key)
    return (
        1 if re.match(rf"{escaped}\b", title, re.IGNORECASE) else 0,
        0 if VARIANT_MARKER.search(title) else 1,
        1 if re.match(rf"{escaped}\s*:", title, re.IGNORECASE) else 0,
        len(body_of(page)),
        -int(str(page.get("id") or "0") or 0),
    )


def decide_keys(pages: Iterable[dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """page id → `Document.key`, plus one decision record per colliding `KIP-N`."""
    by_key: dict[str, list[dict[str, Any]]] = {}
    keys: dict[str, str] = {}
    for page in pages:
        page_id = str(page.get("id") or "")
        key = kip_key(str(page.get("title") or ""))
        if key is None:
            keys[page_id] = f"{SOURCE}:{page_id}"
        else:
            by_key.setdefault(key, []).append(page)

    decisions: list[dict[str, Any]] = []
    for key, candidates in sorted(by_key.items()):
        ranked = sorted(candidates, key=lambda p: canonical_rank(p, key), reverse=True)
        winner, *rest = ranked
        keys[str(winner["id"])] = key
        for page in rest:
            keys[str(page["id"])] = f"{SOURCE}:{page['id']}"
        if not rest:
            continue
        top, second = canonical_rank(winner, key), canonical_rank(ranked[1], key)
        decisions.append(
            {
                "key": key,
                "canonical": {"id": str(winner["id"]), "title": winner.get("title")},
                "variants": [{"id": str(p["id"]), "title": p.get("title")} for p in rest],
                # No title signal separated the top two: the number is genuinely reused by
                # two real pages and only body size decided. A human should look at these.
                "ambiguous": top[:3] == second[:3],
            }
        )
    return keys, decisions


def map_pages(pages: Iterable[dict[str, Any]]) -> Bundle:
    bundle = Bundle(source=SOURCE)
    tracker = FieldTracker(mapped=MAPPED)
    pages = list(pages)
    keys, decisions = decide_keys(pages)
    empty_bodies = 0
    space_form: list[dict[str, str]] = []

    for page in pages:
        tracker.observe(raw_paths(page))
        page_id = str(page.get("id") or "")
        title = str(page.get("title") or "")
        if not page_id:
            bundle.warn("page_without_id", title=title)
            continue

        key = keys.get(page_id, f"{SOURCE}:{page_id}")
        is_kip = key.startswith("KIP-")
        labels = [
            str(x.get("name"))
            for x in ((page.get("metadata") or {}).get("labels") or {}).get("results") or []
            if x.get("name")
        ]
        ancestors = [
            keys.get(str(a.get("id")), f"{SOURCE}:{a.get('id')}")
            for a in page.get("ancestors") or []
            if a.get("id")
        ]
        if not is_kip and (owner := kip_key(title)):
            # A variant of a number another page owns: keep the link, not the key.
            labels.append("kip-variant")
            ancestors.append(owner)
        if not is_kip and (spaced := spaced_kip_key(title)):
            # `KIP 230: …` — recoverable only by widening the parser, which would create
            # more collisions than it resolves (harvest report, `if_space_form_accepted`).
            space_form.append({"id": page_id, "title": title, "would_be_key": spaced})

        body_md = storage_to_markdown(body_of(page))
        if not body_md:
            empty_bodies += 1
            bundle.warn("empty_body", id=page_id, key=key, title=title)

        version = page.get("version") or {}
        history = page.get("history") or {}
        editor = version.get("by") or {}
        author = history.get("createdBy") or {}
        for user in (author, editor):
            bundle.identity(
                SOURCE,
                user.get("userKey") or user.get("username"),
                display=user.get("displayName"),
            )
        space = page_space(page)
        bundle.container(SOURCE, "space", space)

        bundle.documents.append(
            Document(
                id=f"{SOURCE}:{page_id}",
                key=key,
                source=SOURCE,
                kind="KIP" if is_kip else "Page",
                space=space,
                title=title,
                body_md=body_md,
                version=int(version.get("number") or 1),
                created=parse_dt(history.get("createdDate")),
                updated=parse_dt(version.get("when")),
                author=bundle.identity(
                    SOURCE,
                    author.get("userKey") or author.get("username"),
                    display=author.get("displayName"),
                ),
                ancestors=ancestors,
                labels=labels,
                refs=bundle.refs.collect(extract_refs(f"{title}\n{body_md}"), self_keys=[key]),
                raw_url=page_url(page),
            )
        )

    kips = sum(1 for d in bundle.documents if d.kind == "KIP")
    variants = sum(1 for d in bundle.documents if "kip-variant" in d.labels)
    bundle.stats = {
        "pages": len(bundle.documents),
        "kind_kip": kips,
        "kind_page": len(bundle.documents) - kips,
        "empty_bodies": empty_bodies,
        "avg_body_md_chars": round(
            sum(len(d.body_md) for d in bundle.documents) / (len(bundle.documents) or 1)
        ),
        "kip_key": {
            "canonical_keys": kips,
            "colliding_keys": len(decisions),
            "variants": variants,
            "ambiguous_collisions": sum(1 for d in decisions if d["ambiguous"]),
            "pages_without_key": len(bundle.documents) - kips - variants,
            "pages_space_form": space_form,
            "collisions": decisions,
        },
        "unmapped_fields": tracker.report(),
    }
    return bundle
