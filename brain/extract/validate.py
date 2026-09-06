"""Reading a batch output and deciding, record by record, what may enter the graph.

Nothing in this module touches Neo4j; it takes the graph's answers as data (which keys and
chunk ids exist) so the whole decision surface is testable without a database.

Two levels of rejection, and where the line sits is a decision, not an accident:

* **The batch** is rejected — `retry/`, then `quarantine/` — only when the *envelope* is not
  the contract: unreadable JSON, a top level that is not an object, a missing or misspelled
  `batch_id`, `entities`/`relations` that are not arrays, a missing `.in.json` to check the
  quotes against. Nothing in such a file can be trusted, including the parts that look fine.
* **One record** is rejected, counted by reason, and the rest of the batch merges — for
  everything else. A kind outside the closed set, a relation type that is not one of the
  six, a relation from a thing to itself, a `chunk_id` from another batch, a quote that is
  not in the text, an endpoint that resolves to nothing: all of those cost one entity or one
  relation. Forty good extractions do not deserve to be quarantined because the forty-first
  said `Component` where it meant `Technology`.

The verbatim check is the one that earns its place. `MENTIONS{quote}` is what makes the
graph auditable — "why does the brain believe this" is answered by a span of real text —
and a quote that is not in the chunk is a fabricated answer to that question.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from brain.common.jsonschema_mini import validate as schema_validate
from brain.extract import names as names_mod
from brain.extract.models import QUOTE_MAX, BatchInput, Entity, Relation

MAX_ERRORS = 50

#: Entity kinds whose name may collapse into an existing `Component`. A `Technology` or a
#: `Feature` called "streams" is the component; a `Problem` called "streams" is a problem
#: *with* it, and merging the two would answer "what is going wrong in streams" with the
#: component node itself. Brief 07 review, decision 2.
COMPONENT_KINDS: frozenset[str] = frozenset({"Technology", "Feature"})


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
    #: The names this rejection turned on. `unresolved_endpoint` fills it so the report can
    #: say how many misses would have resolved against *another* batch's entities.
    names: tuple[str, ...] = ()

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
    batch_input: BatchInput | None = None
    notes: list[str] = field(default_factory=list)
    entities: list[AcceptedEntity] = field(default_factory=list)
    relations: list[AcceptedRelation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.raw is not None and self.batch_input is not None

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
        return stamp.replace(microsecond=0).isoformat()

    def chunk_texts(self) -> dict[str, str]:
        if self.batch_input is None:
            return {}
        return {c.chunk_id: c.text for c in self.batch_input.chunks}

    def raw_records(self, name: str) -> list[Any]:
        """`entities` or `relations` as the file wrote them, before any validation."""
        if self.raw is None:
            return []
        value = self.raw.get(name)
        return value if isinstance(value, list) else []

    def candidate_names(self) -> set[str]:
        """Every name the file mentions, for one key lookup per merge instead of per record."""
        found: set[str] = set()
        for record in self.raw_records("entities"):
            if isinstance(record, dict) and isinstance(record.get("name"), str):
                found.add(record["name"])
        for record in self.raw_records("relations"):
            if not isinstance(record, dict):
                continue
            for side in ("source", "target"):
                if isinstance(record.get(side), str):
                    found.add(record[side])
        return found


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
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------------ schema slicing


def envelope_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema.json` with the record shapes replaced by "an object".

    Derived rather than hand-written so the envelope rules — which fields are required, that
    `batch_id` is `shard-NN/NNN`, that no unknown top-level field is allowed — have exactly
    one definition. What it stops checking is precisely what is now a record-level concern.
    """
    envelope = json.loads(json.dumps(schema))
    for name in ("entities", "relations"):
        envelope["properties"][name] = {
            "type": "array",
            "items": {"type": "object"},
            "description": envelope["properties"][name].get("description", ""),
        }
    return envelope


def record_schema(schema: dict[str, Any], name: str) -> dict[str, Any]:
    """The `entity` or `relation` subschema, standalone, with `$defs` still reachable."""
    return {"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}


def parse_batch(batch: Batch, schema: dict[str, Any]) -> None:
    """Envelope only. Whether a record is any good is `screen`'s question, one at a time."""
    try:
        raw = json.loads(batch.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        batch.errors.append(f"unreadable: {exc}")
        return
    if not isinstance(raw, dict):
        batch.errors.append(f"top level must be an object, got {type(raw).__name__}")
        return

    if raw.get("batch_id") != batch.batch_id:
        batch.errors.append(f"batch_id is {raw.get('batch_id')!r}, file says {batch.batch_id!r}")

    problems = schema_validate(raw, envelope_schema(schema))
    batch.errors.extend(f"envelope: {p}" for p in problems[:MAX_ERRORS])
    if batch.errors:
        return

    batch.raw = raw
    notes = raw.get("notes")
    batch.notes = [n for n in notes if isinstance(n, str)] if isinstance(notes, list) else []

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

    def key_ref(self, name: str, kind: str | None = None) -> Ref | None:
        """A name that IS an existing node: link it, mint no Entity.

        `kind` gates the *component* half only. A key (`KIP-848`, `KAFKA-16046`) is that
        record whatever kind it was extracted as — the key names one thing. A component name
        is an ordinary English word, so it collapses only for the kinds where the word is
        the component: `Technology` and `Feature`. `kind=None` (a bare relation endpoint,
        which carries no kind) keeps the old behaviour: there is nothing else it could be.
        """
        label = names_mod.key_kind(name)
        if label is not None:
            key = names_mod.canonical_key(name)
            if label == "Document" and key in self.document_keys:
                return Ref("Document", key)
            if label == "WorkItem" and key in self.workitem_keys:
                return Ref("WorkItem", key)
            return None
        if kind is not None and kind not in COMPONENT_KINDS:
            return None
        real = self.components.get(name.strip().casefold())
        if real is not None and len(real) >= 3:
            return Ref("Component", real)
        return None


def screen(batch: Batch, facts: GraphFacts, schema: dict[str, Any] | None = None) -> None:
    """Validate and resolve every record on its own. Fills `entities` and `relations`.

    `schema` is the already-parsed `schema.json`; omitting it re-reads the file, which is
    what a one-off call in a test wants and what a 193-batch merge must not do.
    """
    if not batch.ok:
        return
    schema = schema if schema is not None else _load_schema()
    texts = batch.chunk_texts()

    for record in batch.raw_records("entities"):
        entity = _parse_record(batch, record, "entity", schema)
        if entity is None:
            continue
        reason = _entity_problem(entity, texts)
        if reason is not None:
            batch.rejections.append(
                Rejection(batch.batch_id, "entity", reason, f"{entity.kind} {entity.name!r}")
            )
            continue
        ref = facts.key_ref(entity.name, entity.kind) or Ref(
            "Entity", names_mod.entity_id(entity.kind, entity.name)
        )
        batch.entities.append(AcceptedEntity(entity=entity, ref=ref))

    declared = _declared(batch.entities)
    for record in batch.raw_records("relations"):
        relation = _parse_record(batch, record, "relation", schema)
        if relation is None:
            continue
        label = f"{relation.type} {relation.source!r} -> {relation.target!r}"
        if _same_name(relation.source, relation.target):
            batch.rejections.append(Rejection(batch.batch_id, "relation", "self_loop", label))
            continue
        if relation.evidence_chunk_id not in texts:
            batch.rejections.append(
                Rejection(batch.batch_id, "relation", "chunk_not_in_batch", label)
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
                    names=tuple(name for _side, name in unresolved),
                )
            )
            continue
        assert src is not None and dst is not None
        if src == dst:
            batch.rejections.append(
                Rejection(batch.batch_id, "relation", "self_loop_after_resolution", label)
            )
            continue
        batch.relations.append(AcceptedRelation(relation=relation, source=src, target=dst))


def _load_schema() -> dict[str, Any]:
    from brain.extract.build import SCHEMA_PATH

    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _parse_record(batch: Batch, record: Any, kind: str, schema: dict[str, Any]) -> Any | None:
    """One entity or relation: schema, then pydantic. A failure costs the record only."""
    problems = schema_validate(record, record_schema(schema, kind))
    if problems:
        batch.rejections.append(
            Rejection(batch.batch_id, kind, _schema_reason(problems, kind), "; ".join(problems[:3]))
        )
        return None
    model = Entity if kind == "entity" else Relation
    try:
        return model.model_validate(record)
    except ValidationError as exc:
        batch.rejections.append(
            Rejection(
                batch.batch_id,
                kind,
                _model_reason(exc, kind),
                "; ".join(f"{e['loc']}: {e['msg']}" for e in exc.errors()[:3]),
            )
        )
        return None


def _schema_reason(problems: list[str], kind: str) -> str:
    for problem in problems:
        if kind == "entity" and problem.startswith("$.kind:") and "not one of" in problem:
            return "unknown_kind"
        if kind == "relation" and problem.startswith("$.type:") and "not one of" in problem:
            return "unknown_type"
        if problem.startswith("$.quote:") and "longer than" in problem:
            return "quote_too_long"
    return "schema_violation"


def _model_reason(exc: ValidationError, kind: str) -> str:
    fields = {str(e["loc"][0]) for e in exc.errors() if e["loc"]}
    if kind == "entity" and "kind" in fields:
        return "unknown_kind"
    if kind == "relation" and "type" in fields:
        return "unknown_type"
    return "schema_violation"


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


def _same_name(a: str, b: str) -> str | bool:
    """Case and whitespace, the two differences that are never a different thing.

    Not `norm_name`: that also singularises, and "rebalance depends on rebalances" is a
    sentence someone could mean. If those two do turn out to be one node, the resolved
    check catches it as `self_loop_after_resolution` — with the node it collapsed to.
    """
    return names_mod.normalise_quote(a).casefold() == names_mod.normalise_quote(b).casefold()


def _declared(accepted: list[AcceptedEntity]) -> dict[str, set[Ref]]:
    """Casefolded surface name -> every node the batch resolved that name to."""
    out: dict[str, set[Ref]] = {}
    for item in accepted:
        out.setdefault(item.entity.name.strip().casefold(), set()).add(item.ref)
    return out


def _endpoint(name: str, facts: GraphFacts, declared: dict[str, set[Ref]]) -> Ref | None:
    """This batch's own entity first, then an existing key or component.

    The order matters since component matching became kind-aware: if the batch extracted
    "streams" as a `Problem`, that is the node this batch means by "streams", and falling
    through to the component would put the relation on a node the entity deliberately is not.

    Never invents a node, and never guesses between two: the same word extracted as a
    Problem and as a Feature is two nodes, and picking one would fuse them.
    """
    candidates = declared.get(name.strip().casefold()) or set()
    if len(candidates) == 1:
        return next(iter(candidates))
    if candidates:
        return None  # declared twice under different kinds: the relation does not say which
    return facts.key_ref(name)


# ------------------------------------------------------------------------------- reports


def reasons(batches: list[Batch]) -> Counter:
    counted: Counter = Counter()
    for batch in batches:
        for rejection in batch.rejections:
            counted[f"{rejection.record}:{rejection.reason}"] += 1
    return counted


def cross_batch_misses(batches: list[Batch]) -> dict[str, Any]:
    """Unresolved endpoints that another batch *did* declare.

    A high count is the argument for a cross-batch resolution pass: the extractors agree
    about a thing, they just met it in different batches. A low count says the misses are
    genuinely outside the corpus and the pass would buy nothing.
    """
    declared: set[str] = {a.entity.name.strip().casefold() for b in batches for a in b.entities}
    hits: list[dict[str, str]] = []
    total = 0
    for batch in batches:
        for rejection in batch.rejections:
            if rejection.reason != "unresolved_endpoint":
                continue
            for name in rejection.names:
                total += 1
                if name.strip().casefold() in declared:
                    hits.append({"batch": rejection.batch_id, "name": name})
    return {
        "unresolved_endpoint_names": total,
        "unresolved_endpoint_matches_other_batch": len(hits),
        "unresolved_endpoint_examples": hits[:20],
    }
