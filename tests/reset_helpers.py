"""An in-memory stand-in for Neo4j, understanding exactly the queries `brain reset` emits.

`brain reset` is a *deleting* step, so the offline test has to see nodes actually
disappear — a client that records queries and returns `[]` would let a broken batching
loop, a missed label or a lost protection rule pass. The fake therefore keeps real nodes
and edges and applies the deletes.

It understands only the shapes `brain/reset.py` writes. That is the point: if reset starts
emitting a different query, this raises instead of silently matching nothing.
"""

from __future__ import annotations

import re
from typing import Any

from brain.graph.client import COUNTER_FIELDS
from brain.graph.context import GraphContext

_LABEL = re.compile(r"MATCH \(n?:`(?P<label>[^`]+)`\)")
_LIMIT = re.compile(r"WITH n LIMIT (?P<limit>\d+)")


class FakeGraph:
    """Nodes are `(label, key, props)`; edges are `(src_index, dst_index)`."""

    def __init__(self, nodes: list[tuple[str, str, dict[str, Any]]], edges=()) -> None:
        self.nodes = [
            {"label": label, "key": key, "props": dict(props)} for label, key, props in nodes
        ]
        self.edges = [tuple(e) for e in edges]
        self.queries: list[str] = []

    # -- helpers -----------------------------------------------------------

    def _live(self) -> list[dict[str, Any]]:
        return [n for n in self.nodes if not n.get("deleted")]

    def _match(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        m = _LABEL.search(cypher)
        if m is None:
            raise AssertionError(f"FakeGraph does not understand: {cypher}")
        label = m.group("label")
        rows = [n for n in self._live() if n["label"] == label]
        if "n.synthetic = true" in cypher:
            rows = [n for n in rows if n["props"].get("synthetic") is True]
        keep = set(params.get("keep") or [])
        if "NOT n.`name` IN $keep" in cypher:
            rows = [n for n in rows if n["key"] not in keep]
        elif "n.`name` IN $keep" in cypher:
            rows = [n for n in rows if n["key"] in keep]
        return rows

    # -- client surface ----------------------------------------------------

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.queries.append(cypher)
        if "db.labels()" in cypher:
            return [{"labels": sorted({n["label"] for n in self._live()})}]
        if "-[r]-()" in cypher:
            m = _LABEL.search(cypher)
            label = m.group("label") if m else ""
            indexes = {
                i for i, n in enumerate(self.nodes) if n["label"] == label and not n.get("deleted")
            }
            hits = sum(1 for a, b in self.edges if a in indexes or b in indexes)
            return [{"c": hits}]
        if "RETURN count(n) AS c" in cypher:
            return [{"c": len(self._match(cypher, params))}]
        raise AssertionError(f"FakeGraph does not understand: {cypher}")

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        self.queries.append(cypher)
        counters = dict.fromkeys(COUNTER_FIELDS, 0)
        rows = self._match(cypher, params)
        if "DETACH DELETE" in cypher:
            limit = (
                int(_LIMIT.search(cypher).group("limit")) if _LIMIT.search(cypher) else len(rows)
            )
            doomed = rows[:limit]
            indexes = {self.nodes.index(n) for n in doomed}
            kept = [e for e in self.edges if e[0] not in indexes and e[1] not in indexes]
            counters["relationships_deleted"] = len(self.edges) - len(kept)
            self.edges = kept
            for node in doomed:
                node["deleted"] = True
            counters["nodes_deleted"] = len(doomed)
            return counters
        if "SET n.synthetic = false" in cypher:
            for node in rows:
                node["props"]["synthetic"] = False
            counters["properties_set"] = len(rows)
            return counters
        raise AssertionError(f"FakeGraph does not understand: {cypher}")

    def write_batched(self, cypher: str, rows, batch_size: int = 1000) -> dict[str, int]:
        return self.write(cypher, rows=list(rows))

    # -- assertions --------------------------------------------------------

    def labels(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for node in self._live():
            out[node["label"]] = out.get(node["label"], 0) + 1
        return out

    def prop(self, label: str, key: str, name: str) -> Any:
        for node in self._live():
            if node["label"] == label and node["key"] == key:
                return node["props"].get(name)
        return None


def ctx(graph: FakeGraph, prefix: str = "") -> GraphContext:
    return GraphContext(graph, prefix=prefix)  # type: ignore[arg-type]
