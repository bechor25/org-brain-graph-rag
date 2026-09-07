"""`brain resolve reset` — what it says before it deletes, and what it says after.

The message is the whole safety of the command. A reset is not reversible and the graph is
only whole again once the right rebuild has been run; naming the wrong one sends a reader
to a command that rebuilds nothing and leaves a layer missing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from brain.graph.context import GraphContext
from brain.resolve.ledger import LEDGER_NAME
from brain.resolve.reset import run_reset


class FakeClient:
    """Counts down: the first `DETACH DELETE` deletes, the second finds nothing."""

    def __init__(self, nodes: int, edges: int) -> None:
        self.nodes, self.edges = nodes, edges
        self.deleted = False
        self.writes: list[str] = []

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        self.writes.append(cypher)
        if "DETACH DELETE" in cypher and not self.deleted:
            self.deleted = True
            return {"nodes_deleted": self.nodes}
        return {}

    def write_batched(self, cypher: str, rows, batch_size: int = 1000) -> dict[str, int]:
        return self.write(cypher, rows=list(rows))

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if "count(*) AS edges" in cypher:
            return [{"edges": self.edges}]
        return [{"nodes": 0 if self.deleted else self.nodes}]


def reset(tmp_path, kind: str, *, confirmed: bool, nodes: int = 3, edges: int = 7):
    lines: list[str] = []
    ctx = GraphContext(FakeClient(nodes, edges))
    plan, code = run_reset(
        ctx, canonical_dir=tmp_path, kind=kind, confirmed=confirmed, echo=lines.append
    )
    return plan, code, lines


def test_a_person_reset_points_at_the_canonical_file_that_can_rebuild_it(tmp_path):
    plan, code, lines = reset(tmp_path, "person", confirmed=False)

    assert code == 0 and plan["applied"] is False
    assert plan["recovery"] == "brain load"
    assert "persons.jsonl" in lines[0] and "`brain load`" in lines[0]
    assert "nothing done" in lines[1]


def test_an_entity_reset_says_extract_merge_because_no_file_holds_the_entities(tmp_path):
    """Entities are extracted from chunks, not loaded from `data/canonical`. `brain load`
    would run happily and rebuild none of them."""
    plan, code, lines = reset(tmp_path, "entity", confirmed=False)

    assert plan["recovery"] == "brain extract merge"
    assert "`brain extract merge`" in lines[0]
    assert "no canonical file holds them" in lines[0]
    assert "persons.jsonl" not in lines[0] and "entitys.jsonl" not in lines[0]


@pytest.mark.parametrize(
    ("kind", "next_step"), [("person", "brain load"), ("entity", "brain extract merge")]
)
def test_the_confirmed_reset_names_the_same_command_it_promised(tmp_path, kind, next_step):
    (tmp_path / LEDGER_NAME).write_text(
        json.dumps({"persons": {"git:x": {"canonical": "jira:a"}}, "entities": {}}),
        encoding="utf-8",
    )
    plan, code, lines = reset(tmp_path, kind, confirmed=True)

    assert code == 0 and plan["applied"] is True
    assert f"Run `{next_step}` next." in lines[-1]
    # …and only this kind's ledger section was dropped
    kept = json.loads((tmp_path / LEDGER_NAME).read_text(encoding="utf-8"))
    assert (kept["persons"] == {}) is (kind == "person")
