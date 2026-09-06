"""Small candidates for the resolution tests."""

from __future__ import annotations

from typing import Any

from brain.resolve.models import Candidate, Evidence


def person(node_id: str, name: str, **kw: Any) -> Candidate:
    evidence = [
        e if isinstance(e, Evidence) else Evidence(role=e[0], key=e[1], title=e[2])
        for e in kw.pop("evidence", [])
    ]
    return Candidate(
        id=node_id,
        kind="person",
        block="person",
        name=name,
        source=node_id.split(":", 1)[0],
        evidence=evidence,
        identities=kw.pop("identities", [node_id]),
        **kw,
    )


def entity(node_id: str, name: str, block: str = "Feature", **kw: Any) -> Candidate:
    return Candidate(id=node_id, kind="entity", block=block, name=name, **kw)
