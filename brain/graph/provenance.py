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


class ProvenanceError(RuntimeError):
    """The ledger exists but is not the file `brain synth merge` writes.

    Loud on purpose: silently treating it as empty would load the synthetic layer with no
    provenance at all, which conventions rule 3 forbids and which nothing downstream could
    detect — the nodes would simply look like they had never been LLM-authored.
    """


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
        """Read the ledger `brain synth merge` writes:

        ``{"step", "updated_at", "provenance": {<canonical id>: {batch_id, shard,
        merged_at}}, "batches": {...}, "failed": {...}}``

        The keys of `provenance` are canonical **ids** (`xray:XT-10007`,
        `xray:testplan:3.7.0 regression`, `ado:rao.jun`), not `key`/`name` — one id space
        covers work items, containers and persons alike.
        """
        path = canonical_dir / LEDGER_FILENAME
        if not path.exists():
            return cls(entries={}, path=path, available=False)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProvenanceError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ProvenanceError(f"{path}: expected a JSON object, got {type(raw).__name__}")
        provenance = raw.get("provenance")
        if provenance is None:
            raise ProvenanceError(
                f"{path}: no `provenance` block. Expected the ledger `brain synth merge` "
                "writes; re-run `uv run brain synth merge` to regenerate it."
            )
        if not isinstance(provenance, dict):
            raise ProvenanceError(
                f"{path}: `provenance` must be an object keyed by canonical id, got "
                f"{type(provenance).__name__}"
            )
        entries: dict[str, dict[str, Any]] = {}
        for record_id, meta in provenance.items():
            if not isinstance(meta, dict):
                raise ProvenanceError(
                    f"{path}: provenance[{record_id!r}] must be an object, got "
                    f"{type(meta).__name__}"
                )
            entries[str(record_id)] = {
                "batch_id": meta.get("batch_id"),
                "model": SYNTHETIC_MODEL,
                "extracted_at": meta.get("merged_at"),
                "shard": meta.get("shard"),
            }
        return cls(entries=entries, path=path, available=True)

    def props(self, record_id: str | None) -> dict[str, Any]:
        """Provenance for a canonical **id**, or `{}` when the ledger does not know it."""
        if not record_id:
            return {}
        return dict(self.entries.get(str(record_id), {}))

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
