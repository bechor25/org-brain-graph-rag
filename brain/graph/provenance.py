"""Provenance for the LLM-authored half of the corpus (conventions rule 3).

The synthetic Xray/ADO records are written by `synthetic-org-generator` agents, so every
node they produce must carry `batch_id`, `model` and `extracted_at` — the same rule the
`brain extract` merge will follow for entities. The ledger `brain synth merge` leaves
behind (`data/canonical/synthetic_merged.json`: `key → {batch_id, shard, merged_at}`) is
the only place that says which batch a record came from, because the canonical record
itself only knows it is `synthetic: true`.

The ledger is optional on purpose: until the synthetic layer merges, `brain load` runs on
real records alone and the report says `synthetic_provenance: not_available` rather than
inventing provenance nobody can check.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEDGER_FILENAME = "synthetic_merged.json"

#: What produced the synthetic layer. One string, not a per-batch value: the agents all
#: run the same definition, and the batch is what tells them apart.
SYNTHETIC_MODEL = "opus:synthetic-org-generator"

NOT_AVAILABLE = "not_available"


@dataclass(frozen=True)
class SyntheticProvenance:
    """Key → provenance properties, or an empty ledger that stamps nothing."""

    entries: dict[str, dict[str, Any]]
    path: Path | None = None
    available: bool = False

    @classmethod
    def load(cls, canonical_dir: Path) -> SyntheticProvenance:
        path = canonical_dir / LEDGER_FILENAME
        if not path.exists():
            return cls(entries={}, path=path, available=False)
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries: dict[str, dict[str, Any]] = {}
        for key, meta in (raw or {}).items():
            meta = meta or {}
            entries[str(key)] = {
                "batch_id": meta.get("batch_id"),
                "model": SYNTHETIC_MODEL,
                "extracted_at": meta.get("merged_at"),
                "shard": meta.get("shard"),
            }
        return cls(entries=entries, path=path, available=True)

    def props(self, key: str | None) -> dict[str, Any]:
        """The provenance properties for `key`, or `{}` when the ledger does not know it."""
        if not key:
            return {}
        return dict(self.entries.get(str(key), {}))

    def status(self) -> str:
        return f"{len(self.entries)} keys" if self.available else NOT_AVAILABLE

    def report(self, stamped: int) -> dict[str, Any]:
        return {
            "ledger": str(self.path) if self.path else None,
            "available": self.available,
            "keys_in_ledger": len(self.entries),
            "nodes_stamped": stamped,
            "model": SYNTHETIC_MODEL if self.available else None,
            "status": self.status(),
        }
