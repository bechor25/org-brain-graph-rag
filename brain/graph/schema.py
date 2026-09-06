"""Constraints and indexes (step brief §05 decision 1, spec §2.4).

Constraints come first and for a reason: every loader is `MERGE`-on-key, and `MERGE` on
a property with no unique constraint is both slow (a full label scan per row) and unsafe
(two concurrent transactions can create two nodes with the same key). The constraint's
backing index is what makes the second run of `brain load` cheap and what makes it
create nothing.

Labels that no loader writes yet — `Chunk`, `Community`, `Entity` — get their keys here
anyway, so steps 06–08 inherit a schema instead of inventing one.
"""

from __future__ import annotations

from brain.graph.context import GraphContext

#: (label, property) pairs that must be unique. `Entity.id` is the `kind|norm_name` key
#: from the brief; `Chunk.id` and `Community.id` belong to later steps.
UNIQUE_KEYS: tuple[tuple[str, str], ...] = (
    ("WorkItem", "key"),
    ("Document", "key"),
    ("Person", "id"),
    ("Commit", "sha"),
    ("PullRequest", "number"),
    ("Component", "name"),
    ("Version", "name"),
    ("Sprint", "name"),
    # Not in the brief's list, added for the same reason as the rest: `brain canon`'s
    # `Container` model can emit `area` and `space` containers (the synthetic ADO layer
    # emits areas), and a MERGE on an unconstrained name is the duplicate-node bug this
    # whole list exists to prevent.
    ("Area", "name"),
    ("Space", "name"),
    ("File", "path"),
    ("Chunk", "id"),
    ("StatusChange", "id"),
    ("Community", "id"),
    ("Entity", "id"),
)

#: Range indexes for the properties this POC filters and orders by. Not correctness —
#: the temporal questions ("what was the status on date D", "commits in this window")
#: scan `StatusChange.at` and `Commit.at`, and a label scan over 48k events is the
#: difference between a demo and a wait.
RANGE_INDEXES: tuple[tuple[str, str], ...] = (
    ("StatusChange", "at"),
    ("StatusChange", "field"),
    ("WorkItem", "created"),
    ("WorkItem", "status"),
    ("WorkItem", "source"),
    ("Document", "kind"),
    ("Commit", "at"),
    ("Person", "resolved"),
)


def _constraint_name(label: str, prop: str) -> str:
    return f"brain_{label.lower()}_{prop.lower()}_key"


def _index_name(label: str, prop: str) -> str:
    return f"brain_{label.lower()}_{prop.lower()}_idx"


def schema_statements(ctx: GraphContext) -> list[tuple[str, str]]:
    """(name, cypher) for every constraint and index, in creation order."""
    out: list[tuple[str, str]] = []
    for label, prop in UNIQUE_KEYS:
        name = _constraint_name(label, prop)
        out.append(
            (
                name,
                f"CREATE CONSTRAINT {ctx.name(name)} IF NOT EXISTS "
                f"FOR (n:{ctx.label(label)}) REQUIRE n.`{prop}` IS UNIQUE",
            )
        )
    for label, prop in RANGE_INDEXES:
        name = _index_name(label, prop)
        out.append(
            (
                name,
                f"CREATE INDEX {ctx.name(name)} IF NOT EXISTS "
                f"FOR (n:{ctx.label(label)}) ON (n.`{prop}`)",
            )
        )
    return out


def apply_schema(ctx: GraphContext) -> dict[str, int]:
    """Create everything, then wait for it: a MERGE that races an index build is slow."""
    for _, cypher in schema_statements(ctx):
        ctx.write(cypher)
    ctx.client.write("CALL db.awaitIndexes(300)")
    return {
        "constraints": len(UNIQUE_KEYS),
        "indexes": len(RANGE_INDEXES),
        "constraints_added": ctx.counters["constraints_added"],
        "indexes_added": ctx.counters["indexes_added"],
    }


def drop_schema(ctx: GraphContext) -> None:
    """Only for tests: remove the namespaced constraints/indexes a scratch run created."""
    for name, _ in schema_statements(ctx):
        ctx.client.write(f"DROP CONSTRAINT {ctx.name(name)} IF EXISTS")
        ctx.client.write(f"DROP INDEX {ctx.name(name)} IF EXISTS")
