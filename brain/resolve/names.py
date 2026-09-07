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
from collections.abc import Callable, Iterable

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


def survivor(kind: str, ids: Iterable[str], weight: Callable[[str], int] | None = None) -> str:
    """The node that survives a merge of `ids`. `kind` is `"person"` or `"entity"`.

    For an entity the survivor is the one with the most evidence behind it (coordinator's
    decision (d)): the id every other name will be read as should be the one the corpus
    actually says most about. `weight` supplies that count; without it the fallback is the
    least-qualified name, which is deterministic but blind. People do not use `weight` —
    their survivor is the canonical *system* (jira, then git, then confluence), because an
    id is what every edge in the graph points at and a Jira key is what questions name.
    """
    candidates = sorted(set(ids))
    if not candidates:
        raise ValueError("a merge group needs at least one id")
    if kind == "person":
        return min(candidates, key=person_sort_key)
    if weight is None:
        return min(candidates, key=entity_sort_key)
    return min(candidates, key=lambda i: (-weight(i), *entity_sort_key(i)))


def first_line(text: str | None, limit: int = 120) -> str:
    """A commit message is a paragraph; only its subject line identifies the work."""
    if not text:
        return ""
    line = _SPACE.sub(" ", text.strip().splitlines()[0] if text.strip() else "").strip()
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


#: Namespaces the synthetic ADO/Xray generator prefixes onto a username: `kfk2.gharris`,
#: `s3.b.bejeck`. They name the *system*, never the person, so they come off first.
_SYNTHETIC_NAMESPACE = re.compile(r"^(kfk2|s3)\.")
#: The numeric id the generator appends: `danica.fine.10077`.
_SYNTHETIC_ID = re.compile(r"\.\d{4,}\Z")
#: GitHub's noreply prefix: `51072200+frankvicky@users.noreply.github.com`.
_GITHUB_ID = re.compile(r"^\d+\+")

#: Shortest stem that identifies a person rather than a coincidence. Four-character stems
#: collide (`chen`, `wang`, `test`); five rarely do.
MIN_STEM_CHARS = 5

#: Words that are not a person even when someone types them as a username. Deliberately
#: short: on this corpus it rejects nothing (every stem found is a real username), and a
#: list long enough to be "a dictionary" would start rejecting surnames. Every stem it
#: blocks is named in the report, so a wrong entry here is visible rather than silent.
GENERIC_STEMS: frozenset[str] = frozenset(
    {
        "admin",
        "apache",
        "automation",
        "broker",
        "build",
        "builder",
        "client",
        "cluster",
        "confluent",
        "connect",
        "consumer",
        "developer",
        "github",
        "jenkins",
        "kafka",
        "kafkabot",
        "maintainer",
        "noreply",
        "producer",
        "release",
        "reviewer",
        "robot",
        "server",
        "service",
        "streams",
        "support",
        "system",
        "tester",
        "unknown",
        "users",
    }
)


def _bare_username(node_id: str) -> str:
    """The username inside an identity id, with everything systemic stripped off."""
    source, key = source_of(node_id), key_of(node_id)
    if source == "git":
        key = _GITHUB_ID.sub("", key.split("@", 1)[0])
    key = _SYNTHETIC_ID.sub("", _SYNTHETIC_NAMESPACE.sub("", key))
    return norm_display(key).replace(" ", "")


def username_stem(node_id: str) -> str | None:
    """The comparable username inside an identity id, or `None` when there is not one.

    `ado:danica.fine.10077`, `git:danica.fine@gmail.com` and `jira:danicafine` all give
    `danicafine`: the synthetic namespace and id come off, an email keeps its local part,
    GitHub's numeric prefix goes, and the separators that differ between systems are
    removed rather than split on — the point is a *byte-identical* comparison of what the
    human typed, not a token overlap, which is what tier 2 already does.

    `None` for anything shorter than `MIN_STEM_CHARS`, anything holding a digit (that is
    an id, not a name) and anything in `GENERIC_STEMS`.
    """
    stem = _bare_username(node_id)
    if len(stem) < MIN_STEM_CHARS or any(c.isdigit() for c in stem):
        return None
    return None if stem in GENERIC_STEMS else stem


def rejected_stem(node_id: str) -> str | None:
    """The stem `username_stem` refused because it is a generic word — for the report."""
    stem = _bare_username(node_id)
    return stem if stem in GENERIC_STEMS else None
