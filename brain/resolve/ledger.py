"""`data/canonical/resolution_ledger.json` — the only thing that survives a reload.

`apoc.refactor.mergeNodes` deletes the nodes it swallows. That is fine inside the graph
(their identities live on in `identity_keys` / `merged_from` on the survivor) and fatal
outside it: `data/canonical/persons.jsonl` still holds one record per identity, so the
next `brain load` would `MERGE` every swallowed id straight back into existence and undo
the whole step. Brief 08 decision 9: resolve writes identity -> canonical here, and load
routes through it *before* it merges anything.

The map is kept path-compressed on write. Tier 2 runs against a graph tier 1 already
merged, so it proposes `jira:a -> jira:b` for an `a` that other identities already point
at; storing that literally would leave a two-hop chain that a single lookup in load
would follow only halfway.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.harvest.base import utc_now_iso, write_json_atomic

LEDGER_NAME = "resolution_ledger.json"
STEP = "resolve"
#: One section per `--kinds` family. Entities land beside people, never mixed with them.
SECTIONS: tuple[str, ...] = ("persons", "entities")


class LedgerError(RuntimeError):
    """The ledger exists but is not the file `brain resolve` writes.

    Loud on purpose: treating an unreadable ledger as empty would let `brain load`
    resurrect every merged identity, silently, and the only visible symptom would be a
    Person count that crept back up between runs.
    """


@dataclass
class ResolutionLedger:
    """identity id -> {canonical, tier, rule, score, resolved_at}, per section."""

    persons: dict[str, dict[str, Any]] = field(default_factory=dict)
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None
    available: bool = False
    generated_at: str | None = None

    # ----------------------------------------------------------------- reading

    @classmethod
    def load(cls, canonical_dir: Path) -> ResolutionLedger:
        path = canonical_dir / LEDGER_NAME
        if not path.exists():
            return cls(path=path, available=False)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise LedgerError(f"{path}: expected a JSON object, got {type(raw).__name__}")
        sections: dict[str, dict[str, dict[str, Any]]] = {}
        for name in SECTIONS:
            value = raw.get(name, {})
            if not isinstance(value, dict):
                raise LedgerError(f"{path}: `{name}` must be an object, got {type(value).__name__}")
            for identity, entry in value.items():
                if not isinstance(entry, dict) or not entry.get("canonical"):
                    raise LedgerError(
                        f"{path}: `{name}.{identity}` has no `canonical` id. Re-run "
                        "`uv run brain resolve` to regenerate the ledger."
                    )
            sections[name] = value
        return cls(
            persons=sections["persons"],
            entities=sections["entities"],
            path=path,
            available=True,
            generated_at=raw.get("generated_at"),
        )

    def section(self, kind: str) -> dict[str, dict[str, Any]]:
        return self.persons if kind == "person" else self.entities

    def canonical(self, kind: str, node_id: str) -> str:
        """Where this identity lives now. An id nothing merged maps to itself."""
        entry = self.section(kind).get(node_id)
        return str(entry["canonical"]) if entry else node_id

    def merged_into(self, kind: str) -> dict[str, list[str]]:
        """canonical id -> the identities that were merged into it, sorted."""
        out: dict[str, list[str]] = {}
        for identity, entry in self.section(kind).items():
            out.setdefault(str(entry["canonical"]), []).append(identity)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def tier_of(self, kind: str, canonical_id: str) -> int | None:
        """The weakest evidence that built this node: the highest tier that fed it."""
        tiers = [
            int(e.get("tier", 0))
            for e in self.section(kind).values()
            if e.get("canonical") == canonical_id and e.get("tier") is not None
        ]
        return max(tiers) if tiers else None

    def counts(self) -> dict[str, int]:
        return {name: len(getattr(self, name)) for name in SECTIONS}

    def status(self) -> str:
        if not self.available:
            return "not_available"
        return ", ".join(f"{n}: {len(getattr(self, n))}" for n in SECTIONS)

    # ----------------------------------------------------------------- writing

    def record(
        self,
        kind: str,
        entries: Iterable[tuple[str, str, Mapping[str, Any]]],
        *,
        resolved_at: str | None = None,
    ) -> int:
        """Add `(identity, canonical, meta)` rows, then re-compress every path.

        Returns how many identities the section holds afterwards. Recording an identity
        twice is legal and normal: a later tier that moves a survivor moves everything
        that already pointed at it.
        """
        stamp = resolved_at or utc_now_iso()
        section = self.section(kind)
        for identity, canonical, meta in entries:
            if identity == canonical:
                continue
            section[identity] = {
                "canonical": canonical,
                # The node this identity merged into *at the time*, before any later tier
                # moved that node on. `canonical` is compressed and `via` is not, which is
                # what lets `brain resolve eval` score tier 1 without tier 2's hops.
                "via": canonical,
                "tier": meta.get("tier"),
                "rule": meta.get("rule"),
                "score": meta.get("score"),
                "reason": meta.get("reason"),
                "resolved_at": stamp,
            }
        _compress(section)
        return len(section)

    def to_json(self) -> dict[str, Any]:
        return {
            "step": STEP,
            "generated_at": utc_now_iso(),
            "note": (
                "identity id -> canonical id. `brain load` routes every canonical record "
                "through this map before it MERGEs, so a reload cannot resurrect an "
                "identity `apoc.refactor.mergeNodes` swallowed."
            ),
            "counts": self.counts(),
            "persons": dict(sorted(self.persons.items())),
            "entities": dict(sorted(self.entities.items())),
        }

    def write(self, canonical_dir: Path) -> Path:
        path = canonical_dir / LEDGER_NAME
        write_json_atomic(path, self.to_json())
        self.path, self.available = path, True
        return path


def _compress(section: dict[str, dict[str, Any]]) -> None:
    """Point every identity at the end of its chain, and never at itself."""
    for identity in list(section):
        seen = {identity}
        target = str(section[identity]["canonical"])
        while target in section and target not in seen:
            seen.add(target)
            target = str(section[target]["canonical"])
        if target == identity:
            # A cycle, which means a survivor was later merged into something it had
            # already swallowed. Dropping the row is the only self-consistent answer.
            del section[identity]
            continue
        section[identity]["canonical"] = target
