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

_LABEL = re.compile(r"MATCH \([a-z]?:`(?P<label>[^`]+)`\)")
_LIMIT = re.compile(r"WITH n LIMIT (?P<limit>\d+)")


class FakeGraph:
    """Nodes are `(label, key, props)`; edges are `(src_index, dst_index[, type])`."""

    def __init__(self, nodes: list[tuple[str, str, dict[str, Any]]], edges=()) -> None:
        self.nodes = [
            {"label": label, "key": key, "props": dict(props)} for label, key, props in nodes
        ]
        self.edges = [(e[0], e[1], e[2] if len(e) > 2 else "REL") for e in edges]
        self.queries: list[str] = []

    def _incoming(self, index: int, rel: str) -> bool:
        return any(b == index and t == rel for _a, b, t in self.edges)

    # -- helpers -----------------------------------------------------------

    def _live(self) -> list[dict[str, Any]]:
        return [n for n in self.nodes if not n.get("deleted")]

    def _match(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        m = _LABEL.search(cypher)
        if m is None:
            raise AssertionError(f"FakeGraph does not understand: {cypher}")
        label = m.group("label")
        rows = [n for n in self._live() if n["label"] == label]
        if "NOT ()-[:HAS_CHUNK]->(c)" in cypher:
            rows = [n for n in rows if not self._incoming(self.nodes.index(n), "HAS_CHUNK")]
        if "n.synthetic = true" in cypher:
            rows = [n for n in rows if n["props"].get("synthetic") is True]
        if "n.slice = $slice" in cypher:
            rows = [n for n in rows if n["props"].get("slice") == params.get("slice")]
        if "n.id IN $ids" in cypher:
            wanted = set(params.get("ids") or [])
            rows = [n for n in rows if n["props"].get("id", n["key"]) in wanted]
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
        if "all(p IN parents WHERE p.synthetic = true)" in cypher:
            # The dry-run prediction: chunks with no parent, or whose every parent is
            # about to be deleted for being synthetic — minus the ones the label sweep
            # already claims on their own flag, which the query excludes up front.
            out = 0
            for i, node in enumerate(self.nodes):
                if node.get("deleted") or node["label"] != "Chunk":
                    continue
                if "NOT coalesce(c.synthetic, false)" in cypher and node["props"].get("synthetic"):
                    continue
                parents = [a for a, b, t in self.edges if b == i and t == "HAS_CHUNK"]
                if not parents or all(
                    self.nodes[a]["props"].get("synthetic") is True for a in parents
                ):
                    out += 1
            return [{"c": out}]
        if "sum(CASE WHEN c.slice = $slice" in cypher:
            # The entity sweep of `--slice`: every *surviving* evidence chunk is in the
            # slice. Asked before anything is deleted, so `alive` is the real list.
            chunks = {n["key"]: n for n in self._live() if n["label"] == "Chunk"}
            out = []
            for node in self._live():
                if node["label"] != "Entity":
                    continue
                ids = node["props"].get("evidence_chunk_ids")
                if ids is None:
                    continue
                alive = [chunks[i] for i in ids if i in chunks]
                if alive and all(c["props"].get("slice") == params.get("slice") for c in alive):
                    out.append({"id": node["props"].get("id", node["key"])})
            return sorted(out, key=lambda row: row["id"])
        if "all(p IN parents WHERE p.slice = $slice)" in cypher:
            # The `--slice` dry-run prediction, mirroring the synthetic one above.
            wanted = params.get("slice")
            out_n = 0
            for i, node in enumerate(self.nodes):
                if node.get("deleted") or node["label"] != "Chunk":
                    continue
                if node["props"].get("slice", "base") == wanted:
                    continue
                parents = [a for a, b, t in self.edges if b == i and t == "HAS_CHUNK"]
                if not parents or all(
                    self.nodes[a]["props"].get("slice") == wanted for a in parents
                ):
                    out_n += 1
            return [{"c": out_n}]
        if "WITH DISTINCT r RETURN count(r) AS c" in cypher:
            # `MATCH (n)-[r]-() WHERE n:`A` OR n:`B` …` — every edge with one of our ends.
            labels = set(re.findall(r"n:`([^`]+)`", cypher))
            ours = {
                i for i, n in enumerate(self.nodes) if n["label"] in labels and not n.get("deleted")
            }
            return [{"c": sum(1 for a, b, _t in self.edges if a in ours or b in ours)}]
        if "e.evidence_chunk_ids" in cypher:
            chunks = {n["key"] for n in self._live() if n["label"] == "Chunk"}
            stranded = [
                n
                for n in self._live()
                if n["label"] == "Entity"
                and n["props"].get("evidence_chunk_ids") is not None
                and not (set(n["props"]["evidence_chunk_ids"]) & chunks)
            ]
            return [{"c": len(stranded)}]
        if "RETURN count(n) AS c" in cypher or "RETURN count(c) AS c" in cypher:
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
