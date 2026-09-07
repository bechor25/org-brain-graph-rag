"""Recognising the things a question already names, without asking the model.

Half of the competency questions hand us the answer's anchor in the text: `KAFKA-15123`,
`KIP-848`, a 40-hex sha, a component in backticks. Spotting those with a regex is free,
exact and language-independent — the Hebrew and English forms of the same question carry
the same `KIP-848`, which is most of why the cross-lingual check works at all.

The patterns are deliberately narrow. `re` treats Hebrew letters as `\\w`, so a greedy
`\\w+-\\d+` would match inside Hebrew words; every pattern here anchors on an ASCII
uppercase project key or on hex, and `UTF-8`/`SHA-256`/`COVID-19` are excluded by name
because `brain canon` already learned that lesson.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Not a project key, however much it looks like one. `brain canon` found these in the
#: real corpus; the list is short because the project-key pattern below is already strict.
NOT_KEYS: frozenset[str] = frozenset(
    {"UTF-8", "UTF-16", "SHA-1", "SHA-256", "SHA-512", "COVID-19", "ISO-8601", "RFC-2119"}
)

DOCUMENT_KEY_RE = re.compile(r"\bKIP-\d{1,5}\b")
#: `KAFKA-123`, `ADO-77`, `XT-12` — 2+ ASCII uppercase letters, a dash, digits.
WORKITEM_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
#: `Person.id` is `<source>:<identity>` — `jira:mjsax`, `ado:s3.l.shibu`, `git:a@b.com`.
PERSON_ID_RE = re.compile(r"\b(?:jira|confluence|git|ado|xray):[A-Za-z0-9._@+\-]+\b")
#: `Entity.id` is `Kind|norm_name`. Free text can only be scanned for the space-free
#: form (nothing says where the name ends); a whole string can be matched with spaces.
ENTITY_KINDS = "Feature|Decision|Problem|Alternative|Risk|Technology"
ENTITY_ID_RE = re.compile(rf"\b(?:{ENTITY_KINDS})\|[^\s\"']+")
ENTITY_ID_FULL_RE = re.compile(rf"(?:{ENTITY_KINDS})\|.+", re.DOTALL)
#: A name in backticks or quotes — how the competency questions name a component or class.
QUOTED_RE = re.compile(r"[`'\"“‘]([A-Za-z][A-Za-z0-9_.\-]{2,60})[`'\"”’]")
VERSION_RE = re.compile(r"\b\d+\.\d+(?:\.\d+){0,2}\b")
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


@dataclass(frozen=True)
class Keys:
    """Every anchor a question hands us, already split by what it can be looked up as."""

    documents: tuple[str, ...] = ()
    workitems: tuple[str, ...] = ()
    shas: tuple[str, ...] = ()
    persons: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    quoted: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()

    @property
    def any_node_key(self) -> bool:
        """True when the question names something the graph can be asked for by key."""
        return bool(self.documents or self.workitems or self.shas or self.persons or self.entities)

    def all_keys(self) -> tuple[str, ...]:
        return (*self.documents, *self.workitems, *self.shas, *self.persons, *self.entities)


def _dedup(values: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for v in values:
        seen.setdefault(v, None)
    return tuple(seen)


def find_keys(text: str) -> Keys:
    """Pull every anchor out of a question. Order-preserving, de-duplicated, never raises."""
    text = text or ""
    docs = _dedup(DOCUMENT_KEY_RE.findall(text))
    work = _dedup(
        [
            m
            for m in WORKITEM_KEY_RE.findall(text)
            if m not in NOT_KEYS and not DOCUMENT_KEY_RE.fullmatch(m)
        ]
    )
    persons = _dedup(PERSON_ID_RE.findall(text))
    # A sha pattern also matches the digits of a version and the tail of a person id;
    # only accept a token that stands alone and is not already claimed.
    claimed = " ".join(persons)
    shas = _dedup([m for m in SHA_RE.findall(text) if len(m) >= 7 and m not in claimed])
    return Keys(
        documents=docs,
        workitems=work,
        shas=shas,
        persons=persons,
        entities=_dedup(ENTITY_ID_RE.findall(text)),
        quoted=_dedup(QUOTED_RE.findall(text)),
        versions=_dedup(VERSION_RE.findall(text)),
        dates=_dedup(["-".join(m) for m in ISO_DATE_RE.findall(text)]),
    )


def key_kind(key: str) -> str | None:
    """Which label a bare key belongs to, or `None` when nothing recognises it."""
    key = (key or "").strip()
    if not key:
        return None
    if ENTITY_ID_FULL_RE.fullmatch(key):
        return "Entity"
    if DOCUMENT_KEY_RE.fullmatch(key):
        return "Document"
    if PERSON_ID_RE.fullmatch(key):
        return "Person"
    if WORKITEM_KEY_RE.fullmatch(key) and key not in NOT_KEYS:
        return "WorkItem"
    if re.fullmatch(r"[0-9a-f]{7,40}", key):
        return "Commit"
    return None
