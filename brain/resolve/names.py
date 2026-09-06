"""Normalisation and the survivor rule — the two decisions a merge cannot take back.

`norm_display` is deliberately weaker than `brain.extract.names.norm_name`: it does not
singularise (a surname is not a plural) and it splits `danica.fine` and `rao_jun` into
words, because a person's identity key in one system is their display name in another.

`survivor` decides which node survives `apoc.refactor.mergeNodes` and therefore which id
every edge in the graph ends up pointing at. Brief 08 decision 5 fixes the order for
people — jira, then git, then confluence, then the synthetic sources — and this adds one
tie-break the brief does not name but the corpus needs: an *opaque* key loses to a
readable one of the same source. Jira spells the same human `JIRAUSER302322` in a
changelog and `lucasbru` in an issue field; surviving as the number would leave the graph
keyed on a string no question ever asks for.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

#: Brief 08 decision 5: the canonical identity of a person, best first.
SOURCE_RANK: tuple[str, ...] = ("jira", "git", "confluence", "ado", "xray")

#: `JIRAUSER302322` — the changelog spelling of a Jira account.
_OPAQUE_JIRA = re.compile(r"^JIRAUSER\d+\Z")
#: `8aa980816ee92258016f0f72a35d00ec` — a Confluence userKey.
_OPAQUE_HEX = re.compile(r"^[0-9a-f]{16,}\Z", re.IGNORECASE)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")
#: Word characters that separate name parts inside one token: `danica.fine`, `rao_jun`.
_JOINERS = re.compile(r"[._+\-/\\]+")


def norm_display(text: str | None) -> str:
    """`"Rao, Jun"` -> `"rao jun"`, `"danica.fine"` -> `"danica fine"`. Empty stays empty."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _SPACE.sub(" ", _PUNCT.sub(" ", _JOINERS.sub(" ", folded))).strip()


def display_tokens(text: str | None) -> frozenset[str]:
    """The word set of a display name, initials dropped.

    A one-letter token is an initial (`"S. An"`), and an initial matches too many people
    to be evidence of anything. Dropping it here means `"S. An"` blocks on `an` alone and
    the decision of whether that is the same person is left to a tier that reads context.
    """
    return frozenset(w for w in norm_display(text).split(" ") if len(w) > 1)


def source_of(node_id: str) -> str:
    """`"jira:jrao"` -> `"jira"`. An id without a source reads as an empty source."""
    return node_id.split(":", 1)[0] if ":" in node_id else ""


def key_of(node_id: str) -> str:
    """`"git:jun@example.org"` -> `"jun@example.org"`."""
    return node_id.split(":", 1)[1] if ":" in node_id else node_id


def is_opaque(source: str, key: str) -> bool:
    """A machine id a human never types: `JIRAUSER…`, a Confluence userKey."""
    if source == "jira":
        return bool(_OPAQUE_JIRA.match(key))
    if source == "confluence":
        return bool(_OPAQUE_HEX.match(key))
    return False


def person_sort_key(node_id: str) -> tuple[int, int, str, str]:
    """Order that puts the canonical identity first. Total, so `min` is deterministic."""
    source, key = source_of(node_id), key_of(node_id)
    rank = SOURCE_RANK.index(source) if source in SOURCE_RANK else len(SOURCE_RANK)
    return (rank, int(is_opaque(source, key)), key.casefold(), node_id)


def entity_sort_key(node_id: str) -> tuple[int, str]:
    """Entity ids are `kind|norm_name`; the shortest name is the least qualified one.

    "consumer rebalance protocol" survives "the new consumer rebalance protocol": the
    alias list keeps the longer spelling, and the shorter name is what other text reuses.
    """
    name = node_id.split("|", 1)[1] if "|" in node_id else node_id
    return (len(name), node_id)


def survivor(kind: str, ids: Iterable[str]) -> str:
    """The node that survives a merge of `ids`. `kind` is `"person"` or `"entity"`."""
    candidates = sorted(set(ids))
    if not candidates:
        raise ValueError("a merge group needs at least one id")
    key = person_sort_key if kind == "person" else entity_sort_key
    return min(candidates, key=key)  # type: ignore[arg-type]


def first_line(text: str | None, limit: int = 120) -> str:
    """A commit message is a paragraph; only its subject line identifies the work."""
    if not text:
        return ""
    line = _SPACE.sub(" ", text.strip().splitlines()[0] if text.strip() else "").strip()
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
