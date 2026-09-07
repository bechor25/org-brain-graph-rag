"""`brain extract merge` must land on the entities `brain resolve` kept.

The same trap the person ledger exists for, one label over: `apoc.refactor.mergeNodes`
deleted the entity an extractor named, so re-merging the same batch outputs would `MERGE`
it straight back and silently undo the resolution. `plan()` routes every `Entity` reference
through the ledger before a single row is written.
"""

from __future__ import annotations

from brain.extract.merge import route
from brain.extract.validate import Ref
from brain.resolve.ledger import ResolutionLedger


def ledger_with(*rows) -> ResolutionLedger:
    ledger = ResolutionLedger()
    ledger.record(
        "entity",
        [(a, b, {"tier": 2, "rule": "embedding_auto", "score": 0.97}) for a, b in rows],
    )
    ledger.available = True
    return ledger


MERGED = ledger_with(
    ("Decision|introduce a new stopped state", "Decision|introduce a stopped state")
)


def test_a_merged_entity_reference_lands_on_the_survivor():
    ref = Ref(label="Entity", key="Decision|introduce a new stopped state")
    assert route(ref, MERGED) == Ref(label="Entity", key="Decision|introduce a stopped state")


def test_an_entity_nothing_merged_is_untouched():
    ref = Ref(label="Entity", key="Decision|something else")
    assert route(ref, MERGED) is ref


def test_nodes_brain_load_owns_are_never_rerouted():
    """A `Document`, `WorkItem` or `Component` is not resolution's to move."""
    for label, key in (("Document", "KIP-848"), ("WorkItem", "KAFKA-1"), ("Component", "clients")):
        ref = Ref(label=label, key=key)
        assert route(ref, MERGED) is ref


def test_without_a_ledger_nothing_moves():
    ref = Ref(label="Entity", key="Decision|introduce a new stopped state")
    assert route(ref, None) is ref
    assert route(ref, ResolutionLedger()) == ref


def test_routing_follows_the_compressed_chain_not_one_hop():
    """Tier 2 merged a into b, tier 3 later merged b into c: a reference to a is a c."""
    ledger = ledger_with(("Feature|a", "Feature|b"))
    ledger.record("entity", [("Feature|b", "Feature|c", {"tier": 3, "rule": "adjudicator_same"})])
    assert route(Ref(label="Entity", key="Feature|a"), ledger).key == "Feature|c"
