"""`Person` — one node per identity, unresolved (step brief §05 decision 7).

`brain canon` mints a Person for every identity it sees (`jira:jrao`, `git:jun@…`,
`confluence:8aa9…`), and this step keeps them apart on purpose: merging them is
`brain resolve` (Task 7), which has to be *measured* against gold pairs. Loading them
pre-merged would delete the very duplicates the next step is graded on, so every node
here carries `resolved: false` and the identities it was built from.
"""

from __future__ import annotations

from typing import Any

from brain.canon.models import Person
from brain.graph.context import GraphContext
from brain.graph.corpus import Corpus
from brain.graph.cypher import node_merge
from brain.graph.provenance import SyntheticProvenance

LABEL = "Person"
KEY = "id"


def node_rows(persons: list[Person], prov: SyntheticProvenance) -> list[dict[str, Any]]:
    rows = []
    for p in persons:
        primary = p.identities[0]
        props: dict[str, Any] = {
            "id": p.id,
            "source": primary.source,
            "display": next((i.display for i in p.identities if i.display), None),
            "email": next((i.email for i in p.identities if i.email), None),
            "identity_keys": [f"{i.source}:{i.key}" for i in p.identities],
            "synthetic": p.synthetic,
        }
        props.update(prov.props(p.id))
        # `resolved` belongs to `brain resolve`. Canon always says False, so rewriting it
        # on every run would undo the merge decisions of the step after this one.
        rows.append({"key": p.id, "props": props, "on_create": {"resolved": p.resolved}})
    return rows


def load_nodes(ctx: GraphContext, corpus: Corpus, prov: SyntheticProvenance) -> dict[str, Any]:
    rows = node_rows(corpus.persons, prov)
    ctx.write_rows(node_merge(ctx, LABEL, KEY, on_create=True), rows)
    return {
        "persons": len(rows),
        "stamped": sum(1 for r in rows if r["props"].get("batch_id")),
    }
