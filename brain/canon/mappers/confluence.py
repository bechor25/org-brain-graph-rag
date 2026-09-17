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
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from markdownify import markdownify

from brain.canon.mappers.base import Bundle, FieldTracker, parse_dt
from brain.canon.mentions import extract_refs
from brain.canon.models import BASE_SLICE, Document
from brain.harvest.confluence import default_document_spec, kip_key, spaced_kip_key
from brain.harvest.registry import DocumentKeySpec, get_registry

SOURCE = "confluence"


def default_space() -> str:
    """The space a page falls back to when its own `_links` do not name one.

    From `sources.yaml` (`options.space`), not a constant: it is the organisation's wiki
    space, and the CQL in `query` already names it. Empty when nothing is configured —
    `Document.space` is optional, and inventing a space name is worse than leaving it out.

    The *first* enabled wiki, which is only right when there is one. `map_pages` is handed
    the space of the source it is mapping (`brain.canon.runner.mapper_kwargs`); this
    remains the answer for a caller that has no source in hand, such as a golden test.
    """
    for source in get_registry().enabled():
        if source.type == "confluence":
            return str(source.option("space", "") or "")
    return ""


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
_RI_USER = re.compile(r"<ri:user\b[^>]*?ri:userkey=\"([^\"]*)\"[^>]*?>")
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
    # The storage format stores a person as a key; the rendered page shows a name. `@key`
    # is the one form the mention regexes already understand.
    text = _RI_USER.sub(lambda m: f" @{html.unescape(m.group(1))} ", text)
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


def page_space(page: dict[str, Any], fallback: str | None = None) -> str:
    webui = str((page.get("_links") or {}).get("webui") or "")
    if m := re.search(r"/spaces/([^/]+)/", webui):
        return m.group(1)
    expandable = str((page.get("_expandable") or {}).get("space") or "")
    return expandable.rsplit("/", 1)[-1] or (default_space() if fallback is None else fallback)


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


#: The rank terms, in the order `canonical_rank` returns them.
RANK_TERMS = ("starts_with_key", "no_variant_marker", "colon_form", "body_length", "page_id")


def decided_by(winner: tuple[int, ...], runner_up: tuple[int, ...]) -> str:
    """Which term of the rank actually separated the top two."""
    for term, mine, theirs in zip(RANK_TERMS, winner, runner_up, strict=True):
        if mine != theirs:
            return term
    return "tie"


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
                "decided_by": decided_by(top, second),
                "runner_up_id": str(ranked[1]["id"]),
            }
        )
    return keys, decisions


def map_pages(
    pages: Iterable[dict[str, Any]],
    *,
    document: DocumentKeySpec | None = None,
    space: str | None = None,
    slice_of: Mapping[str, str] | None = None,
) -> Bundle:
    """`slice_of` maps a raw page **id** to the slice its run directory says it came from
    (see :func:`brain.canon.mappers.jira.map_issues`); absent, every page is `base`."""
    slices = slice_of or {}
    # Resolved once per run, and from *this* source's registry entry: the title→key rule
    # (and therefore the test for "is this key one we minted", `KIP-848`, versus "this page
    # has no key of its own", a page id), and the space a page falls back to. Two wikis in
    # `sources.yaml` are two different answers, so neither may be looked up globally.
    spec = document or default_document_spec()
    bundle = Bundle(source=SOURCE)
    tracker = FieldTracker(mapped=MAPPED)
    pages = list(pages)
    keys, decisions = decide_keys(pages)
    ambiguous_ids = {d["runner_up_id"] for d in decisions if d["ambiguous"]}
    empty_bodies = 0
    space_form: list[dict[str, str]] = []

    for page in pages:
        tracker.observe(raw_paths(page))
        page_id = str(page.get("id") or "")
        # Before the space container and the author identity are minted.
        bundle.slice = slices.get(page_id, BASE_SLICE)
        title = str(page.get("title") or "")
        if not page_id:
            bundle.warn("page_without_id", title=title)
            continue

        key = keys.get(page_id, f"{SOURCE}:{page_id}")
        is_kip = spec.owns(key)
        labels = [
            str(x.get("name"))
            for x in ((page.get("metadata") or {}).get("labels") or {}).get("results") or []
            if x.get("name")
        ]
        ancestors = list(
            dict.fromkeys(
                keys.get(str(a.get("id")), f"{SOURCE}:{a.get('id')}")
                for a in page.get("ancestors") or []
                if a.get("id")
            )
        )
        kip_of = None
        if not is_kip and (owner := kip_key(title)):
            # A variant of a number another page owns. `ancestors` stays the page tree.
            kip_of = owner
            labels.append("kip-variant")
            if page_id in ambiguous_ids:
                # Only body size separated this page from the canonical one — it may be a
                # real KIP that reused the number, so chunk/extract should still see it.
                labels.append("ambiguous-kip")
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
        space = page_space(page, space)
        bundle.container(SOURCE, "space", space)

        bundle.documents.append(
            Document(
                id=f"{SOURCE}:{page_id}",
                key=key,
                source=SOURCE,
                slice=bundle.slice,
                kind=spec.kind if is_kip else "Page",
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
                kip_of=kip_of,
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
            "ambiguous_kips": [
                {
                    "key": d["key"],
                    "canonical_page_id": d["canonical"]["id"],
                    "demoted_page_id": d["runner_up_id"],
                    "titles": {
                        "canonical": d["canonical"]["title"],
                        "demoted": next(
                            v["title"] for v in d["variants"] if v["id"] == d["runner_up_id"]
                        ),
                    },
                }
                for d in decisions
                if d["ambiguous"]
            ],
            "pages_without_key": len(bundle.documents) - kips - variants,
            "pages_space_form": space_form,
            "collisions": decisions,
        },
        "unmapped_fields": tracker.report(),
    }
    return bundle
