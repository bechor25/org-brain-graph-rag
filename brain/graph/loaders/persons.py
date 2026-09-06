"""`Person` — one node per identity until `brain resolve` says otherwise (brief 05 §7).

`brain canon` mints a Person for every identity it sees (`jira:jrao`, `git:jun@…`,
`confluence:8aa9…`), and this step keeps them apart on purpose: merging them is
`brain resolve` (Task 7), which has to be *measured* against gold pairs. Loading them
pre-merged would delete the very duplicates the next step is graded on, so an unresolved
node carries `resolved: false` and the identity it was built from.

Once resolve *has* run, its ledger is applied upstream in `load_corpus`, so the records
that reach this loader are already one per resolved person. What is left here is to
repeat, not invent, what the ledger recorded: `resolved`, `resolved_at`,
`resolution_tier`, `merged_from` and `aliases`. That is why they sit in `props` (rewritten
every run, from the ledger) while `resolved` for everyone *else* stays in `on_create` —
load must never flip a resolved person back to unresolved, and must never claim a person
is resolved that the ledger does not name.
"""

from __future__ import annotations

from typing import Any

from brain.canon.models import Person
from brain.graph.context import GraphContext
from brain.graph.corpus import Corpus
from brain.graph.cypher import node_merge
from brain.graph.provenance import SyntheticProvenance
from brain.graph.resolution import Resolution

LABEL = "Person"
KEY = "id"


def node_rows(
    persons: list[Person], prov: SyntheticProvenance, resolution: Resolution | None = None
) -> list[dict[str, Any]]:
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
        if resolution is not None:
            props.update(resolution.props(p.id))
        # `resolved` belongs to `brain resolve`. Canon always says False, so rewriting it
        # on every run would undo the merge decisions of the step after this one — except
        # where the ledger above just said otherwise, which is resolve speaking.
        rows.append({"key": p.id, "props": props, "on_create": {"resolved": p.resolved}})
    return rows


def load_nodes(
    ctx: GraphContext,
    corpus: Corpus,
    prov: SyntheticProvenance,
    resolution: Resolution | None = None,
) -> dict[str, Any]:
    rows = node_rows(corpus.persons, prov, resolution)
    ctx.write_rows(node_merge(ctx, LABEL, KEY, on_create=True), rows)
    return {
        "persons": len(rows),
        "stamped": sum(1 for r in rows if r["props"].get("batch_id")),
        "resolved": sum(1 for r in rows if r["props"].get("resolved")),
    }
