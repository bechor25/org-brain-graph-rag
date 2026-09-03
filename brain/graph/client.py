"""Thin Neo4j driver wrapper.

read()  -> RoutingControl.READ: the server rejects writes ("Writing in read access mode
           not allowed"). This is the only read-only enforcement available on Community
           Edition (no RBAC), and it is server-side, not a client-side regex.
write() -> RoutingControl.WRITE.
write_batched() -> one transaction per batch of rows, query must use `UNWIND $rows AS row`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from neo4j import GraphDatabase, RoutingControl

COUNTER_FIELDS = (
    "nodes_created",
    "nodes_deleted",
    "relationships_created",
    "relationships_deleted",
    "properties_set",
    "labels_added",
    "labels_removed",
    "indexes_added",
    "constraints_added",
)


def _counters(summary) -> dict[str, int]:
    c = summary.counters
    return {f: getattr(c, f, 0) for f in COUNTER_FIELDS}


class GraphClient:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._db = database

    def close(self) -> None:
        self._driver.close()

    def verify(self) -> None:
        self._driver.verify_connectivity()

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        result = self._driver.execute_query(
            cypher, params, database_=self._db, routing_=RoutingControl.READ
        )
        return [r.data() for r in result.records]

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        result = self._driver.execute_query(
            cypher, params, database_=self._db, routing_=RoutingControl.WRITE
        )
        return _counters(result.summary)

    def write_batched(
        self, cypher: str, rows: Iterable[dict[str, Any]], batch_size: int = 1000
    ) -> dict[str, int]:
        if "$rows" not in cypher:
            raise ValueError("write_batched query must use `UNWIND $rows AS row`")
        totals = dict.fromkeys(COUNTER_FIELDS, 0)
        batch: list[dict[str, Any]] = []

        def flush() -> None:
            if not batch:
                return
            for k, v in self.write(cypher, rows=batch).items():
                totals[k] += v
            batch.clear()

        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                flush()
        flush()
        return totals

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
