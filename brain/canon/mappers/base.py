"""Shared plumbing for the three mappers: what they produce and what they measure.

A mapper takes raw records and returns a :class:`Bundle`. Two things in here are not
bookkeeping but the point of the step:

* :class:`RefAudit` — every ref a mapper keeps or drops passes through it, so the report
  can say how much traceability came from formal links and how much from prose, and what
  the regex mistook for an issue key.
* :class:`FieldTracker` — every raw field a mapper does *not* read is counted, so
  `unmapped_fields` in the report is measured rather than remembered. That is the honest
  answer to "what did normalization lose?".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from brain.canon.mentions import filter_refs
from brain.canon.models import Change, Container, Document, Identity, Person, Ref, WorkItem


def parse_dt(value: Any) -> datetime | None:
    """Any source timestamp → an aware UTC datetime, or None.

    Normalizing to UTC keeps the JSONL byte-stable and makes `at` comparable across
    sources; the local offset a git author committed in carries no meaning we use.
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).astimezone(UTC)


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


@dataclass
class RefAudit:
    """Accumulates ref decisions across a whole source."""

    kept_by_kind: Counter = field(default_factory=Counter)
    via: Counter = field(default_factory=Counter)
    removed: Counter = field(default_factory=Counter)
    removed_by_reason: Counter = field(default_factory=Counter)

    def collect(
        self,
        text_refs: Iterable[Ref],
        link_targets: Iterable[str] = (),
        *,
        self_keys: Iterable[str] = (),
    ) -> list[Ref]:
        """Filter text refs, mark the ones a formal link also states, append link-only ones.

        Formal links are not filtered: the source recorded them, so no allowlist is needed
        to believe them. Text refs keep their first-appearance order.

        `self_keys` drops the record's reference to itself — a KIP page's body always names
        its own number, and a self-loop `REFERENCES` edge is noise in every traversal.
        """
        own = {k for k in self_keys if k}
        kept, removed = filter_refs(r for r in text_refs if r.key not in own)
        for key, reason in removed:
            self.removed[key] += 1
            self.removed_by_reason[reason] += 1

        targets = list(dict.fromkeys(t for t in link_targets if t and t not in own))
        formal = set(targets)
        out: list[Ref] = []
        seen: set[tuple[str, str]] = set()
        for ref in kept:
            via = "link" if (ref.kind == "issue" and ref.key in formal) else "text"
            out.append(Ref(kind=ref.kind, key=ref.key, via=via))
            seen.add((ref.kind, ref.key))
        for target in targets:
            if ("issue", target) not in seen:
                out.append(Ref(kind="issue", key=target, via="link"))
                seen.add(("issue", target))

        for ref in out:
            self.kept_by_kind[ref.kind] += 1
            self.via[ref.via] += 1
        return out

    def report(self, *, top: int = 20) -> dict[str, Any]:
        return {
            "kept_total": sum(self.kept_by_kind.values()),
            "kept_by_kind": dict(sorted(self.kept_by_kind.items())),
            "via_link": self.via["link"],
            "via_text": self.via["text"],
            "removed_total": sum(self.removed.values()),
            "removed_distinct_keys": len(self.removed),
            "removed_by_reason": dict(sorted(self.removed_by_reason.items())),
            "top_removed": [[k, n] for k, n in self.removed.most_common(top)],
        }


@dataclass
class FieldTracker:
    """Counts raw field names no canonical field represents."""

    mapped: frozenset[str]
    records: int = 0
    seen: Counter = field(default_factory=Counter)
    names: set[str] = field(default_factory=set)

    def observe(self, paths: Iterable[tuple[str, Any]]) -> None:
        self.records += 1
        for name, value in paths:
            if name in self.mapped:
                continue
            self.names.add(name)
            if not is_empty(value):
                self.seen[name] += 1

    def report(self) -> dict[str, Any]:
        carrying = {n: c for n, c in sorted(self.seen.items(), key=lambda kv: (-kv[1], kv[0]))}
        return {
            "records": self.records,
            "dropped_with_data": carrying,
            "dropped_always_empty": sorted(self.names - set(self.seen)),
        }


@dataclass
class Bundle:
    """Everything one mapper produced, plus its stats and warnings."""

    source: str
    workitems: list[WorkItem] = field(default_factory=list)
    documents: list[Document] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    persons: dict[str, Person] = field(default_factory=dict)
    containers: dict[str, Container] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    refs: RefAudit = field(default_factory=RefAudit)

    def identity(
        self,
        source: str,
        key: str | None,
        *,
        display: str | None = None,
        email: str | None = None,
    ) -> str | None:
        """Register one source identity as a Person and return its source key.

        Canonical records store the *source key* (`jrao`), not the person id
        (`jira:jrao`): resolution (Task 7) rewrites person ids, and a record pointing at
        a pre-resolution id would silently rot.
        """
        if not key:
            return None
        pid = f"{source}:{key}"
        person = self.persons.get(pid)
        if person is None:
            self.persons[pid] = Person(
                id=pid,
                identities=[Identity(source=source, key=key, display=display, email=email)],
            )
        else:
            ident = person.identities[0]
            ident.display = ident.display or display
            ident.email = ident.email or email
        return key

    def container(self, source: str, kind: str, name: str) -> str | None:
        if not name:
            return None
        cid = f"{source}:{kind}:{name}"
        self.containers.setdefault(cid, Container(id=cid, source=source, kind=kind, name=name))
        return cid

    def warn(self, code: str, **detail: Any) -> None:
        self.warnings.append({"source": self.source, "code": code, **detail})
