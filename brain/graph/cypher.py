"""Every write this step makes, generated in one place.

Three shapes, no fourth: merge a node on its key, merge an edge between two nodes that
already exist, merge an edge that carries a key property. All of them are `UNWIND $rows
… MERGE`; none of them is `CREATE`, and none of them creates the *other* end of an edge.
That second rule is what makes the counts in `data/reports/load.json` mean something: a
reference to KAFKA-9999 (outside the harvested slice) is counted as dangling instead of
quietly minting an empty `WorkItem` node with nothing but a key.
"""

from __future__ import annotations

from collections.abc import Sequence

from brain.graph.context import GraphContext


def node_merge(
    ctx: GraphContext, label: str, key_prop: str, extra_labels: Sequence[str] = ()
) -> str:
    """`MERGE (n:Label {key: row.key}) SET n += row.props` (+ optional extra labels)."""
    extra = "".join(f"\nSET n:{ctx.label(x)}" for x in extra_labels)
    return (
        "UNWIND $rows AS row\n"
        f"MERGE (n:{ctx.label(label)} {{`{key_prop}`: row.key}})\n"
        "SET n += row.props"
        f"{extra}"
    )


def edge_merge(
    ctx: GraphContext,
    src: tuple[str, str],
    rel: str,
    dst: tuple[str, str],
    *,
    key_props: Sequence[str] = (),
    set_props: bool = False,
) -> str:
    """`MATCH` both endpoints, `MERGE` the edge. Never creates a node.

    `key_props` become part of the `MERGE` pattern, which is what makes repeated edges
    between the same pair distinct and still idempotent: one `ASSIGNED_TO` per
    `valid_from`, one `COMMENTED` per `at`, one `LINKS_TO` per `type`.
    """
    src_label, src_key = src
    dst_label, dst_key = dst
    keys = ", ".join(f"`{p}`: row.{p}" for p in key_props)
    pattern = f"[r:`{rel}`" + (f" {{{keys}}}" if keys else "") + "]"
    tail = "\nSET r += row.props" if set_props else ""
    return (
        "UNWIND $rows AS row\n"
        f"MATCH (a:{ctx.label(src_label)} {{`{src_key}`: row.src}})\n"
        f"MATCH (b:{ctx.label(dst_label)} {{`{dst_key}`: row.dst}})\n"
        f"MERGE (a)-{pattern}->(b)"
        f"{tail}"
    )
