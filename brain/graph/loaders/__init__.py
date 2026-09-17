"""One loader per canonical type (spec §2.2 → §2.4).

Each module exposes `load_nodes(...)` and `load_edges(...)` returning a stats dict that
goes into `data/reports/load.json` unchanged. Nodes for the whole corpus are written
before any edge, because an edge is only written when both of its endpoints already
exist.

Two properties every node carries from its canonical record, and both exist so a *subset*
of the graph can be deleted later: `synthetic` (the Xray/ADO layer) and `slice` (which
pull the record came from — `base`, or `incremental` for a `--since` catch-up). Both are
written **unconditionally**, including their default value, because `MERGE` only sets the
keys it is given: a property left out on a reload keeps whatever the previous load put
there, and a node still claiming `slice: "incremental"` after it became part of the base
corpus is a node the next `brain reset --slice` would delete.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from brain.graph.context import GraphContext
from brain.graph.cypher import edge_merge

NodeKey = tuple[str, str]  # (label, key property)


def emit(
    ctx: GraphContext,
    src: NodeKey,
    rel: str,
    dst: NodeKey,
    rows: Sequence[dict[str, Any]],
    *,
    key_props: Sequence[str] = (),
    set_props: bool = False,
) -> int:
    """Write one edge kind and return how many rows were sent."""
    if not rows:
        return 0
    ctx.write_rows(edge_merge(ctx, src, rel, dst, key_props=key_props, set_props=set_props), rows)
    return len(rows)
