"""`Component` / `Version` / `Sprint` / `Area` / `Space` — the org's own groupings.

Containers merge on `name`, not on the canonical id, because the same component is called
`clients` by Jira and by the synthetic ADO layer and must be one node: "which team owns
the consumer rebalance work" is a question about the component, not about the system that
named it. `brain canon` already trimmed the whitespace Jira allows in those names.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from brain.canon.models import Container
from brain.graph.context import GraphContext
from brain.graph.corpus import Corpus
from brain.graph.cypher import node_merge
from brain.graph.mapping import CONTAINER_KEY, container_label
from brain.graph.provenance import SyntheticProvenance


def node_rows(
    containers: list[Container], prov: SyntheticProvenance
) -> tuple[dict[str, list[dict[str, Any]]], Counter]:
    """Rows grouped by node label, plus the kinds that have no label."""
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    unknown: Counter = Counter()
    for c in containers:
        label = container_label(c.kind)
        if label is None:
            unknown[c.kind] += 1
            continue
        props: dict[str, Any] = {
            "name": c.name,
            "kind": c.kind,
            "source": c.source,
            "parent": c.parent,
            "synthetic": c.synthetic,
        }
        props.update(prov.props(c.name))
        # Two sources naming the same component produce one node; last writer wins on the
        # descriptive properties, and the name is the identity either way.
        grouped.setdefault(label, {})[c.name] = {"key": c.name, "props": props}
    return {label: list(rows.values()) for label, rows in grouped.items()}, unknown


def load_nodes(ctx: GraphContext, corpus: Corpus, prov: SyntheticProvenance) -> dict[str, Any]:
    grouped, unknown = node_rows(corpus.containers, prov)
    counts: dict[str, int] = {}
    stamped = 0
    for label, rows in sorted(grouped.items()):
        ctx.write_rows(node_merge(ctx, label, CONTAINER_KEY), rows)
        counts[label] = len(rows)
        stamped += sum(1 for r in rows if r["props"].get("batch_id"))
    return {
        "by_label": counts,
        "unknown_kinds": dict(unknown),
        "stamped": stamped,
    }
