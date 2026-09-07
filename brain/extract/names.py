"""Two normalisations and one lookup, all of them merge-critical.

`norm_name` is the Entity key (`kind|norm_name`, spec 2.4). It is deliberately *weak* —
lowercase, punctuation out, whitespace collapsed, a naive singular — because `brain
resolve` is the step that unifies "consumer rebalance protocol" with "the new rebalance
protocol". Making it clever here would merge two things the evidence does not say are one,
and no later step could tell.

`normalise_quote` is the whitespace rule of the verbatim check. A KIP page round-trips
through Confluence storage format, markdownify and the chunker; a quote an agent copied by
eye differs from the stored text by newlines and runs of spaces and by nothing else that
may be forgiven. Case, punctuation and words are compared exactly.

`key_kind` decides whether a name IS an existing node rather than a new Entity: `KAFKA-16046`
is a WorkItem the graph already holds, `KIP-848` a Document. Whether that node actually
exists is a question for the graph, not for a regex — this only says which lookup to try.
"""

from __future__ import annotations

import re
import unicodedata

#: `KAFKA-16046`, `XT-10007`, `ADO-42` — a work item key. Same shape `brain canon` mints.
WORKITEM_KEY = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d+\Z")
#: `KIP-848`. Case-insensitive on input, upper-cased on the way out.
KIP_KEY = re.compile(r"^KIP-\d+\Z", re.IGNORECASE)

#: A possessive, removed *before* the punctuation pass. Stripping the apostrophe first
#: would leave a stray `s` word: `Consumer's offsets` -> `consumer s offset`.
_POSSESSIVE = re.compile(r"['\u2019]s\b", re.IGNORECASE)
#: Punctuation that carries no identity: `group.coordinator`, `KIP-848:`, `(new)`.
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")

#: Plurals the rules below get wrong. `-ses` after a consonant is the awkward case:
#: `aliases`, `statuses` and `buses` all lose only the `es`, but so would `cases` under a
#: generic rule, and `case` is not `cas`. An explicit list is shorter than a rule that is
#: right about English.
_IRREGULAR_PLURAL: dict[str, str] = {
    "aliases": "alias",
    "statuses": "status",
    "buses": "bus",
    "busses": "bus",
    "analyses": "analysis",
    "indices": "index",
    "indexes": "index",
    "matrices": "matrix",
    "schemas": "schema",
    "schemata": "schema",
}

#: Words a naive singular must not touch: stripping the `s` makes a different word.
_KEEP_PLURAL = frozenset(
    {
        "status",
        "bus",
        "class",
        "process",
        "access",
        "loss",
        "less",
        "https",
        "kafka",
        "alias",
        "consensus",
        "analysis",
        "metrics",
        "series",
        "always",
    }
)


def singular(word: str) -> str:
    """`brokers` -> `broker`, `metrics` -> `metrics`. Deliberately naive (spec 3.6)."""
    if word in _IRREGULAR_PLURAL:
        return _IRREGULAR_PLURAL[word]
    if len(word) <= 3 or word in _KEEP_PLURAL:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses") or word.endswith("shes") or word.endswith("ches"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and not word.endswith("us"):
        return word[:-1]
    return word


def norm_name(name: str) -> str:
    """The `norm_name` half of `Entity.id`. Empty input raises: an id needs a name."""
    folded = _POSSESSIVE.sub("", unicodedata.normalize("NFKC", name).casefold())
    words = _SPACE.sub(" ", _PUNCT.sub(" ", folded)).strip().split(" ")
    out = " ".join(singular(w) for w in words if w)
    if not out:
        raise ValueError(f"{name!r} normalises to nothing")
    return out


def entity_id(kind: str, name: str) -> str:
    """`Entity.id`, spec 2.4: the kind and the normalised name, pipe separated."""
    return f"{kind}|{norm_name(name)}"


def normalise_quote(text: str) -> str:
    """Collapse every run of whitespace to one space and trim. Nothing else."""
    return _SPACE.sub(" ", text).strip()


def quote_found(quote: str, chunk_text: str) -> bool:
    """Is `quote` verbatim in `chunk_text`, forgiving whitespace only?"""
    needle = normalise_quote(quote)
    return bool(needle) and needle in normalise_quote(chunk_text)


def quote_boundary_ok(quote: str, chunk_text: str) -> bool:
    """Does the quote begin and end where a word does?

    `quote_found` is satisfied by any substring, so an extractor that stopped counting at
    300 characters produces "...the listeners configuratio" — verbatim, and evidence of
    nothing. The rule is only about *word* edges: a quote may start or end on punctuation
    (`[KIP-889|`, `# Motivation`) because the text does too. It is a cut mid-word that
    means the span was measured rather than read.

    True when the quote is not found at all: that is `quote_not_verbatim`'s business, and
    reporting one fault under two names would double-count it.
    """
    needle = normalise_quote(quote)
    body = normalise_quote(chunk_text)
    at = body.find(needle)
    if not needle or at < 0:
        return True
    before = body[at - 1] if at > 0 else " "
    after = body[at + len(needle)] if at + len(needle) < len(body) else " "
    starts_clean = not (needle[0].isalnum() and (before.isalnum() or before == "_"))
    ends_clean = not (needle[-1].isalnum() and (after.isalnum() or after == "_"))
    return starts_clean and ends_clean


def key_kind(name: str) -> str | None:
    """`"KIP-848"` -> `"Document"`, `"KAFKA-16046"` -> `"WorkItem"`, anything else -> None."""
    candidate = name.strip()
    if KIP_KEY.match(candidate):
        return "Document"
    if WORKITEM_KEY.match(candidate):
        return "WorkItem"
    return None


def canonical_key(name: str) -> str:
    """The key as the graph spells it: `kip-848` -> `KIP-848`, `KAFKA-1` unchanged."""
    return name.strip().upper()
