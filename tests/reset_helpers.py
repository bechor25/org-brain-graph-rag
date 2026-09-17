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
    """Nodes are `(label, key, props)`; edges are `(src_index, dst_index[, type[, props]])`.

    Edge properties matter since `--slice` learned to take its own provenance back out of
    edges between two surviving nodes: the rule is about `batch_ids` and
    `evidence_chunk_ids`, not about which nodes an edge happens to join.
    """

    def __init__(self, nodes: list[tuple[str, str, dict[str, Any]]], edges=()) -> None:
        self.nodes = [
            {"label": label, "key": key, "props": dict(props)} for label, key, props in nodes
        ]
        self.edges = [
            {
                "src": e[0],
                "dst": e[1],
                "type": e[2] if len(e) > 2 else "REL",
                "props": dict(e[3]) if len(e) > 3 else {},
            }
            for e in edges
        ]
        self.queries: list[str] = []

    def _incoming(self, index: int, rel: str) -> bool:
        return any(e["dst"] == index and e["type"] == rel for e in self.edges)

    # -- the provenance sweep of `--slice` ---------------------------------

    def _survives(self, index: int, params: dict[str, Any]) -> bool:
        """The clause `brain.reset._survives` writes, in Python."""
        node = self.nodes[index]
        if node.get("deleted"):
            return False
        if node["props"].get("slice", "base") == params.get("slice"):
            return False
        doomed = set(params.get("doomed") or [])
        return not (node["label"] == "Entity" and node["props"].get("id", node["key"]) in doomed)

    def _provenance_edges(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Edges in scope for the sweep: our types, our labels, evidenced by this slice."""
        types = set(params.get("types") or [])
        labels = set(re.findall(r"a:`([^`]+)`", cypher))
        prefix = params.get("prefix") or ""
        out = []
        for edge in self.edges:
            batches = edge["props"].get("batch_ids")
            if edge["type"] not in types or self.nodes[edge["src"]]["label"] not in labels:
                continue
            if not batches or not any(b.startswith(prefix) for b in batches):
                continue
            if not (self._survives(edge["src"], params) and self._survives(edge["dst"], params)):
                continue
            out.append(edge)
        return out

    def _mixed_entities(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        prefix = params.get("prefix") or ""
        doomed = set(params.get("doomed") or [])
        out = []
        for node in self._live():
            batches = node["props"].get("batch_ids")
            if node["label"] != "Entity" or not batches:
                continue
            if node["props"].get("id", node["key"]) in doomed:
                continue
            if any(b.startswith(prefix) for b in batches) and any(
                not b.startswith(prefix) for b in batches
            ):
                out.append(node)
        return out

    @staticmethod
    def _strip(props: dict[str, Any], params: dict[str, Any]) -> None:
        """`brain.reset._STRIP`, in Python: keep what is not the slice's, re-derive first-seen."""
        prefix = params.get("prefix") or ""
        kept = [b for b in props["batch_ids"] if not b.startswith(prefix)]
        chunks = set(params.get("chunks") or [])
        props["batch_ids"] = kept
        props["evidence_chunk_ids"] = [
            c for c in props.get("evidence_chunk_ids") or [] if c not in chunks
        ]
        props["batch_id"] = kept[0] if kept else None
        props["shard"] = kept[0].split("/")[0] if kept else None

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
                parents = [
                    e["src"] for e in self.edges if e["dst"] == i and e["type"] == "HAS_CHUNK"
                ]
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
                parents = [
                    e["src"] for e in self.edges if e["dst"] == i and e["type"] == "HAS_CHUNK"
                ]
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
            return [{"c": sum(1 for e in self.edges if e["src"] in ours or e["dst"] in ours)}]
        if "RETURN c.id AS id ORDER BY id" in cypher:
            # the slice's chunk ids, read before the sweep deletes them
            return sorted(
                (
                    {"id": n["props"].get("id", n["key"])}
                    for n in self._live()
                    if n["label"] == "Chunk" and n["props"].get("slice") == params.get("slice")
                ),
                key=lambda row: row["id"],
            )
        if "AS only" in cypher and "AS mixed" in cypher:
            prefix = params.get("prefix") or ""
            found = self._provenance_edges(cypher, params)
            mixed = [
                e for e in found if any(not b.startswith(prefix) for b in e["props"]["batch_ids"])
            ]
            return [{"only": len(found) - len(mixed), "mixed": len(mixed)}]
        if "any(x IN e.`batch_ids`" in cypher and "RETURN count(e) AS c" in cypher:
            return [{"c": len(self._mixed_entities(params))}]
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
        if "DELETE r" in cypher and "DETACH" not in cypher:
            prefix = params.get("prefix") or ""
            doomed = [
                e
                for e in self._provenance_edges(cypher, params)
                if all(b.startswith(prefix) for b in e["props"]["batch_ids"])
            ]
            self.edges = [e for e in self.edges if e not in doomed]
            counters["relationships_deleted"] = len(doomed)
            return counters
        if "SET r.`batch_ids` = kept" in cypher:
            prefix = params.get("prefix") or ""
            mixed = [
                e
                for e in self._provenance_edges(cypher, params)
                if any(not b.startswith(prefix) for b in e["props"]["batch_ids"])
            ]
            for edge in mixed:
                self._strip(edge["props"], params)
            counters["properties_set"] = 4 * len(mixed)
            return counters
        if "SET e.`batch_ids` = kept" in cypher:
            mixed = self._mixed_entities(params)
            for node in mixed:
                self._strip(node["props"], params)
            counters["properties_set"] = 4 * len(mixed)
            return counters
        rows = self._match(cypher, params)
        if "DETACH DELETE" in cypher:
            limit = (
                int(_LIMIT.search(cypher).group("limit")) if _LIMIT.search(cypher) else len(rows)
            )
            doomed = rows[:limit]
            indexes = {self.nodes.index(n) for n in doomed}
            kept = [e for e in self.edges if e["src"] not in indexes and e["dst"] not in indexes]
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
