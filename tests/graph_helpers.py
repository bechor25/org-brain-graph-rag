"""Small canonical records for the graph tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from brain.canon.models import Change, Container, Document, Identity, Person, WorkItem
from brain.graph.corpus import Corpus, _index


def dt(day: int, hour: int = 0) -> datetime:
    return datetime(2024, 3, day, hour, tzinfo=UTC)


def work_item(**kw: Any) -> WorkItem:
    key = kw.pop("key", "KAFKA-1")
    source = kw.pop("source", "jira")
    base: dict[str, Any] = {
        "id": f"{source}:{key}",
        "key": key,
        "source": source,
        "type": "Bug",
        "title": f"title of {key}",
        "status": "Open",
        "created": dt(1),
    }
    return WorkItem(**{**base, **kw})


def document(**kw: Any) -> Document:
    key = kw.pop("key", "KIP-5")
    base: dict[str, Any] = {
        "id": f"confluence:{key}",
        "key": key,
        "source": "confluence",
        "kind": "KIP",
        "title": key,
        "body_md": "body",
    }
    return Document(**{**base, **kw})


def person(source: str = "jira", key: str = "dlee", **kw: Any) -> Person:
    identities = kw.pop("identities", [Identity(source=source, key=key, display=key)])
    return Person(id=f"{source}:{key}", identities=identities, **kw)


def change(**kw: Any) -> Change:
    cid = kw.pop("id", "a" * 40)
    base: dict[str, Any] = {"id": cid, "kind": "commit", "message": "m", "at": dt(2)}
    return Change(**{**base, **kw})


def container(kind: str = "component", name: str = "clients", **kw: Any) -> Container:
    return Container(id=f"jira:{kind}:{name}", source="jira", kind=kind, name=name, **kw)


def corpus(**kw: Any) -> Corpus:
    c = Corpus(
        workitems=kw.get("workitems", []),
        documents=kw.get("documents", []),
        persons=kw.get("persons", []),
        changes=kw.get("changes", []),
        containers=kw.get("containers", []),
    )
    return _index(c)
