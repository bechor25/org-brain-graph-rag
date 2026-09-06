"""The write surface every loader shares: a label namespace and a counter tally.

`prefix` exists for tests. `make smoke` loads the mini fixture into a *label space* of
its own (`_SmokeWorkItem`, `_SmokePerson`, …) so a smoke run neither reads nor destroys
the real graph; the production load uses the empty prefix. Relationship types are not
namespaced — every query reaches them through prefixed labels, so the two spaces never
meet.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from brain.graph.client import COUNTER_FIELDS, GraphClient

BATCH_SIZE = 1000


class GraphContext:
    def __init__(self, client: GraphClient, prefix: str = "", batch_size: int = BATCH_SIZE) -> None:
        self.client = client
        self.prefix = prefix
        self.batch_size = batch_size
        self.counters: dict[str, int] = dict.fromkeys(COUNTER_FIELDS, 0)

    def label(self, name: str) -> str:
        """A backticked, namespaced label ready to interpolate into Cypher."""
        return f"`{self.prefix}{name}`"

    def name(self, name: str) -> str:
        """A backticked constraint/index name in the same namespace."""
        return f"`{self.prefix}{name}`"

    def _tally(self, counters: dict[str, int]) -> dict[str, int]:
        for k, v in counters.items():
            self.counters[k] += v
        return counters

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        return self._tally(self.client.write(cypher, **params))

    def write_rows(self, cypher: str, rows: Sequence[dict[str, Any]] | Iterable[dict[str, Any]]):
        """`UNWIND $rows MERGE …` in batches. Returns the counters of this call only."""
        rows = list(rows)
        if not rows:
            return dict.fromkeys(COUNTER_FIELDS, 0)
        return self._tally(self.client.write_batched(cypher, rows, batch_size=self.batch_size))

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        return self.client.read(cypher, **params)
