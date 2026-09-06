"""`brain resolve reset` — undo resolution so it can be run again from a clean load.

Resolution is destructive by construction: `apoc.refactor.mergeNodes` deletes the nodes it
swallows, so there is no "unmerge". The only way back is to drop the resolved layer and let
`brain load` rebuild it from the canonical files, which still hold one record per identity.

That is two steps and both have to happen, in this order:

1. the ledger section is removed, or the next `brain load` would fold the identities
   straight back into the survivors it is supposed to be rebuilding;
2. the label's nodes are deleted with `DETACH DELETE`, because a merged survivor carries
   edges from identities that no longer exist and `MERGE` cannot take those back apart.

Scoped to one label and one ledger section. Everything else in the graph — work items,
documents, commits, chunks, entities — is untouched, and `brain load` recreates the
person layer and every edge into it from `data/canonical/persons.jsonl`.

This exists because a threshold change means a re-run from the beginning: tier 2 cannot
un-auto-merge a pair a new guard would have demoted.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.graph.context import GraphContext
from brain.resolve.graph import KEY_PROPS, LABELS_BY_KIND
from brain.resolve.ledger import LEDGER_NAME, ResolutionLedger

#: Rows per `DETACH DELETE` transaction. The person layer carries ~30k edges; one
#: transaction for all of them is a heap the server does not need to hold.
BATCH_ROWS = 1000


class ResetError(RuntimeError):
    """The reset cannot be done safely."""


def run_reset(
    ctx: GraphContext,
    *,
    canonical_dir: Path,
    kind: str,
    confirmed: bool,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    label = LABELS_BY_KIND[kind]
    before = ctx.read(
        f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS nodes",
    )
    nodes = int(before[0]["nodes"]) if before else 0
    edges = ctx.read(f"MATCH (:{ctx.label(label)})--() RETURN count(*) AS edges")
    edge_count = int(edges[0]["edges"]) if edges else 0
    ledger = ResolutionLedger.load(canonical_dir)
    merged = len(ledger.section(kind))

    plan = {
        "kind": kind,
        "label": label,
        "nodes_to_delete": nodes,
        "edges_to_delete": edge_count,
        "ledger_rows_to_drop": merged,
        "ledger": str(canonical_dir / LEDGER_NAME),
        "confirmed": confirmed,
    }
    echo(
        f"reset {kind}: would delete {nodes} {label} nodes and {edge_count} edges into them, "
        f"and drop {merged} ledger rows. `brain load` rebuilds them from "
        f"{canonical_dir / (kind + 's.jsonl')}."
    )
    if not confirmed:
        echo("reset: nothing done — pass --yes to go ahead.")
        return {**plan, "applied": False}, 0

    # Ledger first: a load between the two steps must not re-fold what is being rebuilt.
    if ledger.available:
        setattr(ledger, "persons" if kind == "person" else "entities", {})
        ledger.write(canonical_dir)

    # `CALL { … } IN TRANSACTIONS` needs an implicit transaction and `GraphClient` runs
    # explicit ones, so the batching is a loop here instead: delete a slice per
    # transaction until none is left. Same effect, no second way to open a session.
    counters: dict[str, int] = {}
    while True:
        slice_counters = ctx.write(
            f"MATCH (n:{ctx.label(label)}) WITH n LIMIT {BATCH_ROWS} DETACH DELETE n"
        )
        for key, value in slice_counters.items():
            counters[key] = counters.get(key, 0) + value
        if not slice_counters.get("nodes_deleted"):
            break
    after = ctx.read(f"MATCH (n:{ctx.label(label)}) RETURN count(n) AS nodes")
    remaining = int(after[0]["nodes"]) if after else 0
    if remaining:
        raise ResetError(f"{remaining} {label} nodes survived the reset; the graph is half-reset")
    echo(f"reset {kind}: {nodes} nodes and {edge_count} edges gone. Run `brain load` next.")
    return {
        **plan,
        "applied": True,
        "counters": counters,
        "remaining": remaining,
    }, 0


#: Re-exported so the CLI does not have to know the label mapping.
__all__ = ["BATCH_ROWS", "KEY_PROPS", "ResetError", "run_reset"]
