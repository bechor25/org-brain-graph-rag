"""Reading a batch output and deciding, record by record, what may enter the graph.

Nothing in this module touches Neo4j; it takes the graph's answers as data (which keys and
chunk ids exist) so the whole decision surface is testable without a database.

Two levels of rejection, and the difference is the point:

* **The batch** is rejected — `retry/`, then `quarantine/` — when the file is not the
  contract: unreadable JSON, a schema or pydantic violation, a `batch_id` that is not the
  file's, a missing `.in.json`. Nothing in it can be trusted, including the parts that look
  fine.
* **One record** is rejected, counted by reason, and the rest of the batch merges — when
  the file is the contract but one claim is not supported: a `chunk_id` that is not in this
  batch, a quote that is not in that chunk's text, an endpoint that is neither an entity of
  this batch nor a node the graph holds. Quarantining a whole shard's work for one bad
  quote would cost more than the quote is worth.

The verbatim check is the one that earns its place. `MENTIONS{quote}` is what makes the
graph auditable — "why does the brain believe this" is answered by a span of real text —
and a quote that is not in the chunk is a fabricated answer to that question.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from brain.extract import names as names_mod
from brain.extract.models import QUOTE_MAX, BatchInput, BatchOutput, Entity, Relation
from brain.synth.jsonschema_mini import validate as schema_validate

#: Reused rather than copied: the same subset of JSON Schema, the same guard against a
#: keyword it does not implement. It belongs in a shared module the day a third task needs
#: it; `brain/synth` is where it was written and moving it is another step's diff.
__schema_validator__ = schema_validate

MAX_ERRORS = 50


@dataclass(frozen=True)
class Ref:
    """What a name resolved to: a node label and the value of that label's key property."""

    label: str
    key: str

    def as_tuple(self) -> tuple[str, str]:
        return (self.label, self.key)


@dataclass(frozen=True)
class AcceptedEntity:
    """An entity that passed screening, and the node its name resolved to."""

    entity: Entity
    ref: Ref


@dataclass(frozen=True)
class AcceptedRelation:
    """A relation whose evidence is in the batch and whose two ends are real nodes."""

    relation: Relation
    source: Ref
    target: Ref


@dataclass
class Rejection:
    batch_id: str
    record: str  # "entity" | "relation"
    reason: str
    detail: str

    def row(self) -> dict[str, str]:
        return {
            "batch": self.batch_id,
            "record": self.record,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class Batch:
    """One `NNN.out.json`, its input, and everything validation concluded about it."""

    batch_id: str
    shard: str
    index: int
    path: Path
    sha256: str
    raw: dict[str, Any] | None = None
    output: BatchOutput | None = None
    batch_input: BatchInput | None = None
    entities: list[AcceptedEntity] = field(default_factory=list)
    relations: list[AcceptedRelation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.output is not None and self.batch_input is not None

    @property
    def in_path(self) -> Path:
        return self.path.with_name(f"{self.index:03d}.in.json")

    @property
    def extracted_at(self) -> str:
        """When the agent wrote this file, in UTC.

        The output carries no timestamp of its own and the merge run's clock is the wrong
        answer: it moves on every re-merge, so `extracted_at` would say the extraction
        happened again. The file's mtime is when the extraction actually landed and it does
        not move unless the agent rewrites the batch.
        """
        stamp = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)
        return stamp.replace(microsecond=0).isoformat().replace("+00:00", "+00:00")

    def chunk_texts(self) -> dict[str, str]:
        if self.batch_input is None:
            return {}
        return {c.chunk_id: c.text for c in self.batch_input.chunks}


def discover(root: Path) -> list[Batch]:
    """Every `<shard>/NNN.out.json`, in shard then batch order."""
    found: list[Batch] = []
    for path in sorted(root.glob("shard-[0-9][0-9]/[0-9][0-9][0-9].out.json")):
        shard = path.parent.name
        index = int(path.name.split(".", 1)[0])
        found.append(
            Batch(
                batch_id=f"{shard}/{index:03d}",
                shard=shard,
                index=index,
                path=path,
                sha256=_sha256(path),
            )
        )
    return found


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_batch(batch: Batch, schema: dict[str, Any]) -> None:
    """JSON → schema → pydantic → the input it answers. Every stage's complaints."""
    try:
        raw = json.loads(batch.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        batch.errors.append(f"unreadable: {exc}")
        return
    if not isinstance(raw, dict):
        batch.errors.append(f"top level must be an object, got {type(raw).__name__}")
        return
    batch.raw = raw

    if raw.get("batch_id") != batch.batch_id:
        batch.errors.append(f"batch_id is {raw.get('batch_id')!r}, file says {batch.batch_id!r}")

    problems = schema_validate(raw, schema)
    batch.errors.extend(f"schema: {p}" for p in problems[:MAX_ERRORS])
    if problems:
        return
    try:
        batch.output = BatchOutput.model_validate(raw)
    except ValidationError as exc:
        batch.errors.extend(f"model: {e['loc']}: {e['msg']}" for e in exc.errors()[:MAX_ERRORS])
        return

    if not batch.in_path.is_file():
        batch.errors.append(
            f"no input beside it ({batch.in_path.name}); the quotes cannot be checked "
            "against anything. Re-run `brain extract build`."
        )
        return
    try:
        batch.batch_input = BatchInput.model_validate_json(
            batch.in_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        batch.errors.append(f"input {batch.in_path.name} is unusable: {exc}")


# --------------------------------------------------------------------------- resolution


@dataclass(frozen=True)
class GraphFacts:
    """What the graph says exists. Passed in so this module never opens a connection."""

    workitem_keys: frozenset[str] = frozenset()
    document_keys: frozenset[str] = frozenset()
    components: dict[str, str] = field(default_factory=dict)  # casefolded -> real name

    def key_ref(self, name: str) -> Ref | None:
        """A name that IS an existing key or component: link that node, mint no Entity."""
        label = names_mod.key_kind(name)
        if label is not None:
            key = names_mod.canonical_key(name)
            if label == "Document" and key in self.document_keys:
                return Ref("Document", key)
            if label == "WorkItem" and key in self.workitem_keys:
                return Ref("WorkItem", key)
            return None
        real = self.components.get(name.strip().casefold())
        if real is not None and len(real) >= 3:
            return Ref("Component", real)
        return None


def screen(batch: Batch, facts: GraphFacts) -> None:
    """Reject the records that are not supported; keep the batch.

    Fills `entities` (accepted, each with the node it resolved to) and `relations`
    (accepted, with both endpoints resolved). Everything else lands in `rejections`.
    """
    if not batch.ok or batch.output is None:
        return
    texts = batch.chunk_texts()

    for entity in batch.output.entities:
        reason = _entity_problem(entity, texts)
        if reason is not None:
            batch.rejections.append(
                Rejection(batch.batch_id, "entity", reason, f"{entity.kind} {entity.name!r}")
            )
            continue
        ref = facts.key_ref(entity.name) or Ref(
            "Entity", names_mod.entity_id(entity.kind, entity.name)
        )
        batch.entities.append(AcceptedEntity(entity=entity, ref=ref))

    declared = _declared(batch.entities)
    for relation in batch.output.relations:
        if relation.evidence_chunk_id not in texts:
            batch.rejections.append(
                Rejection(
                    batch.batch_id,
                    "relation",
                    "chunk_not_in_batch",
                    f"{relation.type} {relation.source!r} -> {relation.target!r}",
                )
            )
            continue
        src = _endpoint(relation.source, facts, declared)
        dst = _endpoint(relation.target, facts, declared)
        unresolved = [
            (side, name)
            for side, name, ref in (
                ("source", relation.source, src),
                ("target", relation.target, dst),
            )
            if ref is None
        ]
        if unresolved:
            batch.rejections.append(
                Rejection(
                    batch.batch_id,
                    "relation",
                    "unresolved_endpoint",
                    f"{relation.type}: "
                    + ", ".join(f"{side} {name!r}" for side, name in unresolved)
                    + " — neither an entity of this batch nor a node the graph holds",
                )
            )
            continue
        assert src is not None and dst is not None
        if src == dst:
            batch.rejections.append(
                Rejection(
                    batch.batch_id,
                    "relation",
                    "self_loop_after_resolution",
                    f"{relation.type}: {relation.source!r} and {relation.target!r} are one node",
                )
            )
            continue
        batch.relations.append(AcceptedRelation(relation=relation, source=src, target=dst))


def _entity_problem(entity: Entity, texts: dict[str, str]) -> str | None:
    if entity.chunk_id not in texts:
        return "chunk_not_in_batch"
    if len(entity.quote) > QUOTE_MAX:
        return "quote_too_long"
    if not names_mod.quote_found(entity.quote, texts[entity.chunk_id]):
        return "quote_not_verbatim"
    try:
        names_mod.norm_name(entity.name)
    except ValueError:
        return "name_normalises_to_nothing"
    return None


def _declared(accepted: list[AcceptedEntity]) -> dict[str, set[Ref]]:
    """Casefolded surface name -> every node the batch resolved that name to."""
    out: dict[str, set[Ref]] = {}
    for item in accepted:
        out.setdefault(item.entity.name.strip().casefold(), set()).add(item.ref)
    return out


def _endpoint(name: str, facts: GraphFacts, declared: dict[str, set[Ref]]) -> Ref | None:
    """An existing key or component first, then an entity this batch declared.

    Never invents a node, and never guesses between two: the same word extracted as a
    Problem and as a Feature is two nodes, and picking one would fuse them.
    """
    ref = facts.key_ref(name)
    if ref is not None:
        return ref
    candidates = declared.get(name.strip().casefold()) or set()
    return next(iter(candidates)) if len(candidates) == 1 else None


def reasons(batches: list[Batch]) -> Counter:
    counted: Counter = Counter()
    for batch in batches:
        for rejection in batch.rejections:
            counted[f"{rejection.record}:{rejection.reason}"] += 1
    return counted
