"""What `brain load` does with `data/canonical/resolution_ledger.json`.

`apoc.refactor.mergeNodes` deletes the nodes it swallows, but `data/canonical/
persons.jsonl` still holds one record per identity. Without this module the next
`brain load` would `MERGE (:Person {id: "ado:cadonna.bruno.10007"})` and put back, in
silence, every duplicate `brain resolve` spent an embedding pass removing — and every
edge would point at the resurrected node rather than at the survivor.

So the ledger is applied *before* anything is written, and before the corpus is indexed:
the canonical records of one resolved person are folded into a single record under the
canonical id, and `person_by_identity` then routes every assignee, author and mention to
it. The fold happens in memory over the canonical files; nothing here reads the graph.

`brain resolve` owns `Person.resolved`, so load only ever *repeats* what the ledger says —
`resolved`, `resolved_at`, `resolution_tier`, `merged_from` and `aliases` are rewritten
from the ledger, never invented, and a person the ledger says nothing about keeps the
`resolved: false` load has always written on create.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from brain.canon.models import Person
from brain.resolve.ledger import ResolutionLedger


@dataclass
class Resolution:
    """The ledger, applied — plus the properties `persons.load_nodes` stamps from it."""

    available: bool = False
    merged_from: dict[str, list[str]] = field(default_factory=dict)
    aliases: dict[str, list[str]] = field(default_factory=dict)
    tiers: dict[str, int] = field(default_factory=dict)
    resolved_at: dict[str, str] = field(default_factory=dict)
    folded: int = 0
    canonical_nodes: int = 0
    unknown_canonical: list[str] = field(default_factory=list)
    unknown_identities: list[str] = field(default_factory=list)
    generated_at: str | None = None

    def props(self, person_id: str) -> dict[str, Any]:
        """What load writes on a person the ledger resolved. Empty for everyone else."""
        if person_id not in self.merged_from:
            return {}
        return {
            "resolved": True,
            "resolved_at": self.resolved_at.get(person_id) or None,
            "resolution_tier": self.tiers.get(person_id),
            "merged_from": self.merged_from[person_id],
            "aliases": self.aliases.get(person_id, []),
        }

    def report(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "ledger_generated_at": self.generated_at,
            "identities_folded": self.folded,
            "canonical_persons": self.canonical_nodes,
            "unknown_canonical": self.unknown_canonical[:20],
            "unknown_identities": self.unknown_identities[:20],
            "note": (
                "identities_folded is how many canonical Person records were merged into "
                "another before a single MERGE ran. Without it a reload would recreate "
                "them and undo `brain resolve`."
            ),
        }

    def status(self) -> str:
        if not self.available:
            return "not_available"
        return f"{self.folded} identities folded into {self.canonical_nodes} persons"


def fold_persons(
    persons: list[Person], ledger: ResolutionLedger
) -> tuple[list[Person], Resolution]:
    """One record per resolved person instead of one per identity.

    An identity whose canonical id is not itself a canonical record is left alone and
    named in `unknown_canonical`: dropping it would delete a person from the graph, and
    inventing the survivor would create a node no canonical file describes.
    """
    if not ledger.available:
        return persons, Resolution(available=False, canonical_nodes=len(persons))

    by_id: dict[str, Person] = {p.id: p for p in persons}
    groups: dict[str, list[str]] = defaultdict(list)
    unknown_canonical: list[str] = []
    unknown_identities: list[str] = []
    for identity, entry in sorted(ledger.persons.items()):
        canonical = str(entry["canonical"])
        if identity not in by_id:
            unknown_identities.append(identity)
        elif canonical not in by_id:
            unknown_canonical.append(f"{identity} -> {canonical}")
        else:
            groups[canonical].append(identity)

    resolution = Resolution(
        available=True,
        unknown_canonical=sorted(unknown_canonical),
        unknown_identities=sorted(unknown_identities),
        generated_at=ledger.generated_at,
    )
    swallowed: set[str] = set()
    for canonical, members in sorted(groups.items()):
        keeper = by_id[canonical]
        identities = list(keeper.identities)
        seen = {(i.source, i.key) for i in identities}
        own = {i.display for i in identities if i.display}
        aliases: set[str] = set()
        for member in sorted(members):
            for identity in by_id[member].identities:
                if (identity.source, identity.key) not in seen:
                    seen.add((identity.source, identity.key))
                    identities.append(identity)
                if identity.display:
                    aliases.add(identity.display)
            swallowed.add(member)
        keeper.identities = identities
        keeper.resolved = True
        resolution.merged_from[canonical] = sorted(members)
        resolution.aliases[canonical] = sorted(aliases - own)
        resolution.tiers[canonical] = max(
            (int(ledger.persons[m].get("tier") or 0) for m in members), default=0
        )
        resolution.resolved_at[canonical] = max(
            (str(ledger.persons[m].get("resolved_at") or "") for m in members), default=""
        )

    kept = [p for p in persons if p.id not in swallowed]
    resolution.folded = len(swallowed)
    resolution.canonical_nodes = len(kept)
    return kept, resolution
