"""Asking the graph whether a citation exists. Reads only, one round trip per kind.

This is the cheapest correctness check the POC has and the only one that is not an opinion:
`lookup` on a key an answer cited either returns a node or it does not. Everything here
goes through `RetrieveContext.read()` → `GraphClient.read()` → `RoutingControl.READ`, which
the *server* enforces; nothing in this module can write even if a query were wrong.

Two design notes worth the space:

* **Batched per kind, not per citation.** Nineteen answers carry a few hundred citations.
  `UNWIND $values AS v` turns that into five queries, and the properties are all constrained
  (`WorkItem.key`, `Document.key`, `Person.id`, `Commit.sha`, `Chunk.id`), so both the exact
  matches and the `STARTS WITH` prefixes are index-backed.
* **Explicit projection everywhere.** `GraphClient.read()` returns `record.data()`, which
  turns a node into a plain property dict with its labels dropped. A query that returned `n`
  would hand back something that cannot say what it is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from brain.eval.citations import Citation
from brain.retrieve.context import RetrieveContext

#: citation kind -> (label, key property, the property worth showing in the report)
EXACT_KINDS: dict[str, tuple[str, str, str]] = {
    "workitem": ("WorkItem", "key", "title"),
    "document": ("Document", "key", "title"),
    "person": ("Person", "id", "display"),
    "community": ("Community", "id", "title"),
}
#: citation kind -> (label, key property, the property worth showing) matched by prefix
PREFIX_KINDS: dict[str, tuple[str, str, str]] = {
    "commit": ("Commit", "sha", "message"),
    "chunk": ("Chunk", "id", "parent_key"),
}


@dataclass(frozen=True)
class Verdict:
    """What the graph said about one citation."""

    ok: bool
    resolved_as: str | None = None
    matches: int = 0
    title: str = ""
    reason: str | None = None
    #: Something true about the match that is not a failure: an ambiguous prefix, an
    #: orphaned chunk, a person matched without its source prefix. Reported, never hidden.
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.ok,
            "resolved_as": self.resolved_as,
            "matches": self.matches,
            "title": self.title,
            "reason": self.reason,
            "note": self.note,
        }


class GraphVerifier:
    """Resolve citations against one graph. Callable, and caches across questions."""

    def __init__(self, ctx: RetrieveContext) -> None:
        self.ctx = ctx
        self._cache: dict[str, Verdict] = {}

    # ---------------------------------------------------------------------- queries

    def _exact(self, kind: str, values: list[str]) -> dict[str, dict[str, Any]]:
        label, key_prop, title_prop = EXACT_KINDS[kind]
        cypher = (
            "UNWIND $values AS v\n"
            f"MATCH (n:{self.ctx.label(label)}) WHERE n.`{key_prop}` = v\n"
            f"RETURN v AS cited, n.`{key_prop}` AS resolved, "
            f"coalesce(n.`{title_prop}`, n.title, n.name, '') AS title"
        )
        return {row["cited"]: row for row in self.ctx.read(cypher, values=values)}

    def _prefix(self, kind: str, values: list[str]) -> dict[str, dict[str, Any]]:
        label, key_prop, title_prop = PREFIX_KINDS[kind]
        orphan = (
            ", head(collect(coalesce(n.`orphaned`, false))) AS orphaned" if kind == "chunk" else ""
        )
        cypher = (
            "UNWIND $values AS v\n"
            f"MATCH (n:{self.ctx.label(label)}) WHERE n.`{key_prop}` STARTS WITH v\n"
            f"WITH v, collect(n.`{key_prop}`)[..3] AS resolved, count(n) AS matches,\n"
            f"  head(collect(coalesce(n.`{title_prop}`, ''))) AS title{orphan}\n"
            f"RETURN v AS cited, resolved, matches, title{', orphaned' if orphan else ''}"
        )
        return {row["cited"]: row for row in self.ctx.read(cypher, values=values)}

    def _person_by_suffix(self, values: list[str]) -> dict[str, dict[str, Any]]:
        """`[person:mjsax]` without its source prefix, accepted only when it is unambiguous.

        `Person.id` is `<source>:<identity>`, and the analyst is told to cite the whole id.
        A bare identity that matches exactly one person is still a checkable citation; one
        that matches two is not, and is refused rather than guessed.
        """
        cypher = (
            "UNWIND $values AS v\n"
            f"MATCH (n:{self.ctx.label('Person')}) WHERE n.`id` ENDS WITH ':' + v\n"
            "WITH v, collect(n.`id`)[..3] AS resolved, count(n) AS matches\n"
            "RETURN v AS cited, resolved, matches"
        )
        return {row["cited"]: row for row in self.ctx.read(cypher, values=values)}

    # ---------------------------------------------------------------------- verdicts

    def __call__(self, citations: Iterable[Citation]) -> dict[str, Verdict]:
        """A verdict per distinct citation id. One query per kind that has anything to ask."""
        citations = list(citations)
        todo: dict[str, list[str]] = {}
        pending: dict[str, Citation] = {}
        for citation in citations:
            if citation.id in self._cache or citation.id in pending:
                continue
            if citation.problem:
                self._cache[citation.id] = Verdict(False, reason=citation.problem)
                continue
            pending[citation.id] = citation
            todo.setdefault(citation.kind, []).append(citation.value)

        for kind, values in todo.items():
            if kind in EXACT_KINDS:
                self._resolve_exact(kind, values)
            elif kind in PREFIX_KINDS:
                self._resolve_prefix(kind, values)
            else:  # pragma: no cover - CITATION_KINDS is closed and covered above
                for value in values:
                    self._cache[f"{kind}:{value}"] = Verdict(
                        False, reason=f"no check is defined for a {kind} citation"
                    )

        return {c.id: self._cache[c.id] for c in citations if c.id in self._cache}

    def _resolve_exact(self, kind: str, values: list[str]) -> None:
        label, key_prop, _ = EXACT_KINDS[kind]
        rows = self._exact(kind, values)
        bare = [v for v in values if v not in rows and ":" not in v] if kind == "person" else []
        suffix = self._person_by_suffix(bare) if bare else {}
        for value in values:
            row = rows.get(value)
            if row:
                self._cache[f"{kind}:{value}"] = Verdict(
                    True, resolved_as=row["resolved"], matches=1, title=row.get("title") or ""
                )
                continue
            hit = suffix.get(value)
            if hit and hit["matches"] == 1:
                self._cache[f"{kind}:{value}"] = Verdict(
                    True,
                    resolved_as=hit["resolved"][0],
                    matches=1,
                    note=f"matched by unique id suffix — the citation omitted the source prefix "
                    f"({hit['resolved'][0]})",
                )
                continue
            reason = f"no {label} has {key_prop} = {value!r}"
            if hit and hit["matches"] > 1:
                reason = (
                    f"{hit['matches']} people share the identity {value!r} "
                    f"({', '.join(hit['resolved'])}); cite the full Person.id"
                )
            self._cache[f"{kind}:{value}"] = Verdict(False, reason=reason)

    def _resolve_prefix(self, kind: str, values: list[str]) -> None:
        label, key_prop, _ = PREFIX_KINDS[kind]
        rows = self._prefix(kind, values)
        for value in values:
            row = rows.get(value)
            if not row:
                self._cache[f"{kind}:{value}"] = Verdict(
                    False, reason=f"no {label} {key_prop} starts with {value!r}"
                )
                continue
            notes = []
            if row["matches"] > 1:
                notes.append(
                    f"ambiguous prefix — {row['matches']} nodes start with {value!r} "
                    f"({', '.join(row['resolved'])})"
                )
            if row.get("orphaned"):
                notes.append("the chunk is orphaned: its text was replaced by a later run")
            self._cache[f"{kind}:{value}"] = Verdict(
                True,
                resolved_as=row["resolved"][0],
                matches=row["matches"],
                title=row.get("title") or "",
                note="; ".join(notes) or None,
            )


def graph_verify(citations: Iterable[Citation]) -> dict[str, Verdict]:
    """Open a read-only context, verify, close. The default for `brain eval cite-check`.

    Nothing to check means nothing to connect to: with no answers on disk this returns
    without ever opening a driver, so the "no answers yet" run is a report and an exit code
    rather than a connection error.
    """
    citations = list(citations)
    if not citations:
        return {}
    with RetrieveContext.open() as ctx:
        return GraphVerifier(ctx)(citations)
