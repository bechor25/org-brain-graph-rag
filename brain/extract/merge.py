"""`brain extract merge` — the only writer of extracted facts into Neo4j.

The agents produced JSON; this is the code that decides what of it becomes graph. It runs
over *every* batch output every time, aggregates them, and MERGEs — so a second run creates
nothing, and a batch an agent regenerated overwrites its own contribution rather than
adding a second copy.

Aggregation before writing is what makes the provenance honest. The same entity appears in
five chunks across three batches; one node comes out of that with all five chunk ids in
`evidence_chunk_ids`, not five nodes and not one node whose provenance names whichever
batch happened to be written last. `extracted_at` is the earliest contributing output
file's mtime, which is when the extraction actually happened and does not move when merge
runs again.

What this module refuses to do: create a node for an endpoint the graph does not hold
(`KAFKA-99999` in a KIP is outside the harvested slice, not a new work item), and finish
while any LLM-derived edge lacks provenance. The second one is an assertion against the
live graph, not against the rows we meant to send.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.extract import graph as extract_graph
from brain.extract import names as names_mod
from brain.extract import validate as validate_mod
from brain.extract.build import SCHEMA_PATH, TASK, load_manifest, write_section
from brain.extract.models import DESCRIPTION_MAX
from brain.extract.validate import Batch, GraphFacts, Ref
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.resolve.ledger import ResolutionLedger

MAX_RETRIES = 2
RETRY_DIR = "retry"
QUARANTINE_DIR = "quarantine"
RETRY_FIELD = "_extract_retry"
LEDGER_NAME = "ledger.json"
STATUS_NAME = "status.json"
MODEL = extract_graph.MODEL


class MergeError(RuntimeError):
    """Merge cannot produce a correct graph. Never downgraded to a partial run."""


# ------------------------------------------------------------------------- aggregation


@dataclass
class Aggregate:
    """One node or edge, and every batch that evidenced it."""

    chunk_ids: set[str] = field(default_factory=set)
    batches: set[str] = field(default_factory=set)
    shards: set[str] = field(default_factory=set)
    extracted_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def add(self, *, chunk_id: str, batch: Batch) -> None:
        self.chunk_ids.add(chunk_id)
        self.batches.add(batch.batch_id)
        self.shards.add(batch.shard)
        stamp = batch.extracted_at
        if self.extracted_at is None or stamp < self.extracted_at:
            self.extracted_at = stamp

    def provenance(self) -> dict[str, Any]:
        """The four properties conventions rule 3 requires, plus the full batch list.

        `batch_id` is singular in the contract, so it names the first batch that said this;
        `batch_ids` keeps the rest, because "which batches agreed" is the question a reader
        of a merged edge actually asks.
        """
        first = min(self.batches) if self.batches else None
        return {
            "evidence_chunk_ids": sorted(self.chunk_ids),
            "batch_id": first,
            "batch_ids": sorted(self.batches),
            "shard": first.split("/")[0] if first else None,
            "model": MODEL,
            "extracted_at": self.extracted_at,
        }


def longest(descriptions: Iterable[str]) -> str:
    """The fullest sentence written about a thing, ties broken alphabetically.

    Five chunks describe the same entity five ways; the shortest is usually a restatement
    of its name. Taking the longest keeps the one that actually says something, and keeping
    `descriptions[]` beside it means `brain resolve` can still read the rest.
    """
    ranked = sorted(descriptions, key=lambda d: (-len(d), d))
    return ranked[0][:DESCRIPTION_MAX] if ranked else ""


@dataclass
class EntityAggregate(Aggregate):
    kind: str = ""
    names: Counter = field(default_factory=Counter)
    descriptions: set[str] = field(default_factory=set)

    def props(self) -> dict[str, Any]:
        # The commonest surface form is the name; ties go to the shorter, then to the
        # alphabetically first, so two runs over the same batches agree.
        ranked = sorted(self.names.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
        name = ranked[0][0]
        return {
            "kind": self.kind,
            "name": name,
            "norm_name": names_mod.norm_name(name),
            "aliases": sorted({n for n, _ in ranked if n != name}),
            "description": longest(self.descriptions),
            "descriptions": sorted(self.descriptions),
            **self.provenance(),
        }


@dataclass
class Plan:
    """Everything the writers need, decided before a single write happens."""

    entities: dict[str, EntityAggregate] = field(default_factory=dict)
    #: (chunk id, target label, target key, extracted kind). The kind is part of the key
    #: because a key-matched target has no kind of its own: a `Technology` "streams" and a
    #: `Problem` "streams" both point at the same `Component`, and one mention would lose
    #: whichever the second one said.
    mentions: dict[tuple[str, str, str, str], Aggregate] = field(default_factory=dict)
    relations: dict[tuple[str, str, str, str, str], Aggregate] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: Stamped on every node and edge this run writes. `extracted_at` says when the agent
    #: wrote the fact and never moves; this says when merge last saw a batch declare it,
    #: which is the only way to find what the current batches no longer say.
    merged_at: str = ""

    def entity_rows(self) -> list[dict[str, Any]]:
        rows = []
        for key, agg in sorted(self.entities.items()):
            props = agg.props()
            rows.append(
                {
                    "key": key,
                    "props": {
                        **{k: v for k, v in props.items() if k != "extracted_at"},
                        "merged_at": self.merged_at,
                    },
                    "on_create": {"extracted_at": props["extracted_at"]},
                }
            )
        return rows

    def mention_rows(self) -> dict[str, list[dict[str, Any]]]:
        """`MENTIONS` rows per target label.

        A mention of an `Entity` needs no more than its quote — the node carries the kind,
        the name and the description. A mention of a `Document`, `WorkItem` or `Component`
        does: that node was written by `brain load` and knows nothing about the extraction,
        so what the extractor *called* it, and as what kind, lives on the edge or nowhere.
        """
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for (chunk_id, label, key, kind), agg in sorted(self.mentions.items()):
            props: dict[str, Any] = {
                "quote": agg.payload["quote"],
                **agg.provenance(),
                "merged_at": self.merged_at,
            }
            if label != "Entity":
                props |= {
                    "kind": kind,
                    "name": agg.payload["name"],
                    "description": longest(agg.payload["descriptions"]),
                }
            grouped[label].append({"src": chunk_id, "dst": key, "props": props})
        return dict(grouped)

    def relation_rows(self) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for key, agg in sorted(self.relations.items()):
            rel_type, src_label, src_key, dst_label, dst_key = key
            props: dict[str, Any] = {**agg.provenance(), "merged_at": self.merged_at}
            if agg.payload.get("note"):
                props["note"] = agg.payload["note"][:DESCRIPTION_MAX]
            grouped[(rel_type, src_label, dst_label)].append(
                {"src": src_key, "dst": dst_key, "props": props}
            )
        return dict(grouped)


def route(ref: Ref, ledger: ResolutionLedger | None) -> Ref:
    """Send an `Entity` reference to the node `brain resolve` merged it into.

    `apoc.refactor.mergeNodes` deleted the entity an extractor named, so a re-merge of the
    same batches would `MERGE` it straight back and undo the resolution — the same trap the
    person ledger exists for, one label over. Non-entity refs (a `Document`, a `WorkItem`,
    a `Component`) are untouched: those nodes belong to `brain load`, not to resolution.
    """
    if ledger is None or ref.label != "Entity":
        return ref
    canonical = ledger.canonical("entity", ref.key)
    return ref if canonical == ref.key else Ref(label=ref.label, key=canonical)


def plan(batches: Sequence[Batch], ledger: ResolutionLedger | None = None) -> Plan:
    """Fold every screened batch into one node/edge plan, in batch-id order."""
    out = Plan()
    for batch in sorted(batches, key=lambda b: b.batch_id):
        if not batch.ok:
            continue
        for accepted in batch.entities:
            entity, ref = accepted.entity, route(accepted.ref, ledger)
            if ref.label == "Entity":
                agg = out.entities.get(ref.key)
                if agg is None:
                    agg = out.entities[ref.key] = EntityAggregate(kind=entity.kind)
                agg.names[entity.name] += 1
                agg.descriptions.add(entity.description)
                agg.add(chunk_id=entity.chunk_id, batch=batch)
            key = (entity.chunk_id, ref.label, ref.key, entity.kind)
            mention = out.mentions.get(key)
            if mention is None:
                mention = out.mentions[key] = Aggregate(
                    payload={"quote": entity.quote, "name": entity.name, "descriptions": set()}
                )
            mention.payload["descriptions"].add(entity.description)
            mention.add(chunk_id=entity.chunk_id, batch=batch)
        for accepted in batch.relations:
            rel = accepted.relation
            source, target = route(accepted.source, ledger), route(accepted.target, ledger)
            if source == target:
                # Both ends resolved to one node: the relation says a thing depends on
                # itself, which is not a fact the graph should hold.
                out.warnings.append(
                    f"{batch.batch_id}: {rel.type} dropped — both ends resolve to {source.key}"
                )
                continue
            key = (
                rel.type,
                source.label,
                source.key,
                target.label,
                target.key,
            )
            agg = out.relations.get(key)
            if agg is None:
                agg = out.relations[key] = Aggregate(payload={"note": rel.note or ""})
            elif rel.note and not agg.payload.get("note"):
                agg.payload["note"] = rel.note
            agg.add(chunk_id=rel.evidence_chunk_id, batch=batch)
    return out


SHAPES_PATH = Path(__file__).with_name("shapes.json")
SHAPES_REF = "brain/extract/shapes.json"


def load_shapes(path: Path = SHAPES_PATH) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """The endpoint table, from the one file that states it.

    It used to be a dict in this module and a sentence in the spec, which is two sources
    for one rule and exactly how `INTRODUCES_RISK` ended up with an empty source list —
    silently exempting the type from the check it was written for. `shapes.json` carries
    the spec reference it is answerable to; a test asserts it covers the closed set.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))["shapes"]
    return {
        rel: (tuple(entry.get("source") or ()), tuple(entry.get("target") or ()))
        for rel, entry in raw.items()
    }


def descriptor(ref: Ref) -> str:
    """`Ref("Entity", "Decision|x")` -> `"Entity:Decision"`; anything else is its label."""
    if ref.label != "Entity":
        return ref.label
    return f"Entity:{ref.key.split('|', 1)[0]}"


def shape_warnings(
    plan_obj: Plan, shapes: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] | None = None
) -> dict[str, Any]:
    """Relations whose endpoints are not the shape `shapes.json` describes.

    Counted, never dropped. The shapes are what the corpus turned out to say, and a
    relation outside them is a signal about the extraction, not a reason to lose an edge.
    """
    shapes = shapes if shapes is not None else load_shapes()
    off: list[dict[str, str]] = []
    for rel_type, src_label, src_key, dst_label, dst_key in plan_obj.relations:
        sources, targets = shapes.get(rel_type, ((), ()))
        src = descriptor(Ref(src_label, src_key))
        dst = descriptor(Ref(dst_label, dst_key))
        problems = []
        if sources and src not in sources:
            problems.append(f"source is {src}, shapes.json says {'/'.join(sources)}")
        if targets and dst not in targets:
            problems.append(f"target is {dst}, shapes.json says {'/'.join(targets)}")
        if problems:
            off.append(
                {
                    "type": rel_type,
                    "src": src_key,
                    "dst": dst_key,
                    "why": "; ".join(problems),
                }
            )
    counted: Counter = Counter(o["type"] for o in off)
    return {
        "shapes": SHAPES_REF,
        "off_spec_shape": len(off),
        "off_spec_by_type": dict(sorted(counted.items())),
        "off_spec_examples": off[:20],
    }


def drop_missing_chunks(plan_obj: Plan, present: set[str]) -> dict[str, Any]:
    """A mention whose chunk is gone cannot be written: `MENTIONS` starts at the chunk.

    It is a gap, not a crash — `brain chunk` marks a chunk orphaned rather than deleting it
    precisely so this provenance survives a re-chunk — so it is counted and reported.
    """
    missing = sorted({key[0] for key in plan_obj.mentions if key[0] not in present})
    for key in [k for k in plan_obj.mentions if k[0] in set(missing)]:
        del plan_obj.mentions[key]
    dangling_evidence = sorted(
        {
            c
            for agg in list(plan_obj.entities.values()) + list(plan_obj.relations.values())
            for c in agg.chunk_ids
            if c not in present
        }
    )
    return {
        "mentions_dropped_chunk_missing": len(missing),
        "chunk_ids_missing": missing[:20],
        "evidence_chunk_ids_not_in_graph": len(dangling_evidence),
    }


# ---------------------------------------------------------------------------- the run


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def handle_failure(batch: Batch, ledger_failed: dict[str, Any]) -> dict[str, Any]:
    """Copy the *input* into `retry/`, or `quarantine/` once the attempts are spent.

    The attempt counter only moves when the output actually changed: re-running merge over
    an unchanged bad batch is a re-read, not a new attempt, and two idle merges must not
    quarantine work the agent never got the chance to redo.
    """
    previous = ledger_failed.get(batch.batch_id) or {}
    attempt = int(previous.get("attempt", 0))
    if previous.get("sha256") != batch.sha256:
        attempt += 1

    shard_dir = batch.path.parent
    retry_path = shard_dir / RETRY_DIR / batch.in_path.name
    quarantine_path = shard_dir / QUARANTINE_DIR / batch.in_path.name
    payload = _read_json(batch.in_path) or {"batch_id": batch.batch_id}
    payload[RETRY_FIELD] = {
        "attempt": attempt,
        "at": utc_now_iso(),
        "output": batch.path.name,
        "output_sha256": batch.sha256,
        "reasons": batch.errors[:20],
    }
    state = "retry" if attempt <= MAX_RETRIES else "quarantine"
    if state == "retry":
        write_json_atomic(retry_path, payload)
        quarantine_path.unlink(missing_ok=True)
    else:
        payload[RETRY_FIELD]["quarantined_after"] = MAX_RETRIES
        write_json_atomic(quarantine_path, payload)
        retry_path.unlink(missing_ok=True)
    return {
        "attempt": attempt,
        "sha256": batch.sha256,
        "state": state,
        "at": payload[RETRY_FIELD]["at"],
        "reasons": batch.errors[:20],
        "path": str(retry_path if state == "retry" else quarantine_path),
    }


def clear_failure_files(batch: Batch) -> None:
    shard_dir = batch.path.parent
    (shard_dir / RETRY_DIR / batch.in_path.name).unlink(missing_ok=True)
    (shard_dir / QUARANTINE_DIR / batch.in_path.name).unlink(missing_ok=True)


def read_shard_status(root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("shard-[0-9][0-9]/" + STATUS_NAME)):
        data = _read_json(path)
        if data is not None:
            out[path.parent.name] = data
    return out


def agent_failures(status: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Batches the agent itself refused to write, with the reason it gave."""
    out: list[dict[str, Any]] = []
    for shard, data in sorted(status.items()):
        for entry in data.get("failed") or []:
            if isinstance(entry, dict):
                out.append(
                    {
                        "batch": str(entry.get("batch") or shard),
                        "reason": str(entry.get("reason") or "no reason given"),
                    }
                )
    return out


def done_without_output(status: dict[str, dict[str, Any]], batches: Sequence[Batch]) -> list[str]:
    seen = {b.batch_id for b in batches}
    out: list[str] = []
    for shard, data in sorted(status.items()):
        for name in data.get("done") or []:
            batch_id = str(name) if "/" in str(name) else f"{shard}/{name}"
            if batch_id not in seen:
                out.append(batch_id)
    return sorted(out)


def collect_facts(ctx: GraphContext, batches: Sequence[Batch]) -> GraphFacts:
    """One round trip for every key an extractor could have named. Nothing per record."""
    candidates: set[str] = set()
    for batch in batches:
        candidates |= batch.candidate_names()
    keys: dict[str, set[str]] = {"WorkItem": set(), "Document": set()}
    for name in candidates:
        label = names_mod.key_kind(name)
        if label in keys:
            keys[label].add(names_mod.canonical_key(name))
    return GraphFacts(
        workitem_keys=frozenset(
            extract_graph.existing_keys(ctx, "WorkItem", sorted(keys["WorkItem"]))
        ),
        document_keys=frozenset(
            extract_graph.existing_keys(ctx, "Document", sorted(keys["Document"]))
        ),
        components=extract_graph.component_names(ctx),
    )


def load_precision_sample(reports_dir: Path) -> dict[str, Any] | None:
    """The human verdict on `brain extract sample`, if someone has recorded one.

    It lives in `data/reports/extract_sample.json` under `judged`, written by whoever did
    the judging — never computed here. The step report carries it so the acceptance number
    and the graph it is about are one file, and so a later run cannot quietly lose it.
    """
    from brain.extract.sample import load_sample

    sheet = load_sample(reports_dir)
    judged = (sheet or {}).get("judged")
    if not isinstance(judged, dict):
        return None
    return {"seed": (sheet or {}).get("seed"), "targets": (sheet or {}).get("targets"), **judged}


def run_merge(
    *,
    ctx: GraphContext,
    batches_dir: Path,
    reports_dir: Path,
    schema_path: Path = SCHEMA_PATH,
    canonical_dir: Path | None = None,
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Validate every batch output, write what survives, audit the graph, report."""
    started = time.perf_counter()
    root = batches_dir / TASK
    if not root.is_dir():
        raise MergeError(f"no batches to merge: {root} does not exist (run `brain extract build`)")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    batches = validate_mod.discover(root)
    for batch in batches:
        validate_mod.parse_batch(batch, schema)

    facts = collect_facts(ctx, [b for b in batches if b.ok])
    for batch in batches:
        validate_mod.screen(batch, facts, schema)

    valid = [b for b in batches if b.ok]
    invalid = [b for b in batches if not b.ok]

    # Route every entity reference through the resolution ledger before anything is
    # written, so a re-merge lands on the survivor instead of recreating what it swallowed.
    ledger = ResolutionLedger.load(canonical_dir) if canonical_dir else None
    merged_at = utc_now_iso()
    plan_obj = plan(valid, ledger)
    plan_obj.merged_at = merged_at
    chunk_ids = (
        {c for agg in plan_obj.mentions.values() for c in agg.chunk_ids}
        | {c for agg in plan_obj.entities.values() for c in agg.chunk_ids}
        | {c for agg in plan_obj.relations.values() for c in agg.chunk_ids}
    )
    present = extract_graph.existing_chunk_ids(ctx, sorted(chunk_ids))
    gaps = drop_missing_chunks(plan_obj, present) | shape_warnings(plan_obj, load_shapes())

    schema_stats = extract_graph.apply_extract_schema(ctx)
    written = {
        "entities": extract_graph.write_entities(ctx, plan_obj.entity_rows()),
        "mentions": extract_graph.write_mentions(ctx, plan_obj.mention_rows()),
        "relations": extract_graph.write_relations(ctx, plan_obj.relation_rows()),
    }
    weak = extract_graph.mark_weak_decisions(ctx)

    provenance = extract_graph.provenance_gaps(ctx)
    if provenance["total"]:
        raise MergeError(
            f"{provenance['total']} LLM-derived edges carry no provenance "
            f"({provenance['by_type']}). Conventions rule 3: every extracted node and edge "
            "names its evidence, its batch, "
            "its model and when. This is a bug in the merge, not a data problem."
        )

    ledger_path = root / LEDGER_NAME
    ledger = _read_json(ledger_path) or {}
    ledger_failed: dict[str, Any] = dict(ledger.get("failed") or {})
    previously_merged = set(ledger.get("batches") or {})
    new_batches = {
        b.batch_id: {
            "sha256": b.sha256,
            "shard": b.shard,
            "merged_at": merged_at,
            "extracted_at": b.extracted_at,
            "entities": len(b.entities),
            "relations": len(b.relations),
            "rejected": len(b.rejections),
        }
        for b in valid
    }
    new_failed = {b.batch_id: handle_failure(b, ledger_failed) for b in invalid}
    for batch in valid:
        clear_failure_files(batch)
    write_json_atomic(
        ledger_path,
        {
            "step": "extract.merge",
            "updated_at": merged_at,
            "model": MODEL,
            "batches": dict(sorted(new_batches.items())),
            "failed": dict(sorted(new_failed.items())),
        },
    )

    status = read_shard_status(root)
    census = extract_graph.census(ctx)
    report = build_report(
        consolidation=extract_graph.consolidation(ctx),
        stale=extract_graph.stale(ctx, merged_at),
        precision_sample=load_precision_sample(reports_dir),
        records_seen=validate_mod.records_seen(list(batches)),
        batches=batches,
        valid=valid,
        invalid=invalid,
        plan_obj=plan_obj,
        written=written,
        weak=weak,
        gaps=gaps,
        provenance=provenance,
        census=census,
        schema_stats=schema_stats,
        counters=dict(ctx.counters),
        manifest=load_manifest(batches_dir),
        reported_failures=agent_failures(status),
        done_missing=done_without_output(status, batches),
        gone=sorted(previously_merged - {b.batch_id for b in valid}),
        duration_ms=round((time.perf_counter() - started) * 1000),
        prefix=ctx.prefix,
    )
    if write_report:
        write_section(reports_dir, "merge", report)
        echo(f"report: {reports_dir / 'extract.json'}")
    echo(summarize(report))
    return report, (1 if invalid or report["batches"]["reported_failed"] else 0)


# ------------------------------------------------------------------------------ report


def build_report(
    *,
    batches: Sequence[Batch],
    valid: Sequence[Batch],
    invalid: Sequence[Batch],
    plan_obj: Plan,
    written: dict[str, int],
    weak: dict[str, int],
    gaps: dict[str, Any],
    provenance: dict[str, Any],
    census: dict[str, Any],
    schema_stats: dict[str, Any],
    counters: dict[str, int],
    manifest: dict[str, Any] | None,
    reported_failures: Sequence[dict[str, Any]],
    done_missing: Sequence[str],
    gone: Sequence[str],
    duration_ms: int,
    prefix: str,
    consolidation: dict[str, Any],
    stale: dict[str, Any],
    precision_sample: dict[str, Any] | None,
    records_seen: int,
) -> dict[str, Any]:
    expected = {b["id"] for b in (manifest or {}).get("batches", [])}
    seen = {b.batch_id for b in batches}
    by_kind: Counter = Counter(a.kind for a in plan_obj.entities.values())
    by_type: Counter = Counter(k[0] for k in plan_obj.relations)
    by_target: Counter = Counter(k[1] for k in plan_obj.mentions)
    # Two denominators, because they answer different questions: "of what the agents wrote"
    # is the extraction quality gate (>= 90%), "of what build planned" also counts the
    # batches nobody has written yet, which is progress, not quality.
    envelope_valid = len(valid) / len(batches) if batches else 0.0
    against_expected = len(valid) / len(expected) if expected else None
    missing = sorted(expected - seen)
    rejected_records = sum(len(b.rejections) for b in batches)
    return {
        "step": "extract.merge",
        "generated_at": utc_now_iso(),
        "duration_ms": duration_ms,
        "label_prefix": prefix,
        "model": MODEL,
        "batches": {
            "found": len(batches),
            "valid": len(valid),
            "invalid": len(invalid),
            "expected": len(expected),
            # Named for what it measures. A batch is valid or not on its *envelope* alone;
            # how good the records inside it were is `record_rejection_rate`, and calling
            # the first one a "first pass rate" hid that they are different questions with
            # different denominators.
            "envelope_valid_rate": round(envelope_valid, 4),
            "envelope_valid_rate_vs_expected": (
                round(against_expected, 4) if against_expected is not None else None
            ),
            "reported_failed": len(reported_failures),
            "missing_count": len(missing),
            "missing_outputs": missing[:50],
            "unexpected_outputs": sorted(seen - expected) if expected else [],
            "previously_merged_now_absent": list(gone),
        },
        "planned": {
            "entities": len(plan_obj.entities),
            "entities_by_kind": dict(sorted(by_kind.items())),
            "mentions": len(plan_obj.mentions),
            "mentions_by_target_label": dict(sorted(by_target.items())),
            "relations": len(plan_obj.relations),
            "relations_by_type": dict(sorted(by_type.items())),
        },
        "written": written,
        "counters": counters,
        "schema": schema_stats,
        "weak_decisions": weak,
        "rejected_records": {
            "total": rejected_records,
            "records_seen": records_seen,
            "record_rejection_rate": round(rejected_records / records_seen, 6)
            if records_seen
            else 0.0,
            "by_reason": dict(sorted(validate_mod.reasons(batches).items())),
            "counted_not_enforced": validate_mod.soft_reasons(list(batches)),
            "examples": [r.row() for b in batches for r in b.rejections[:3]][:40],
        },
        "rejected_batches": [
            {
                "batch": b.batch_id,
                "source": "merge",
                "output": str(b.path),
                "errors": b.errors[:20],
                "error_count": len(b.errors),
            }
            for b in invalid
        ]
        + [
            {
                "batch": f["batch"],
                "source": "status.json",
                "output": None,
                "errors": [f["reason"]],
                "error_count": 1,
            }
            for f in reported_failures
        ],
        "warnings": {
            **gaps,
            **validate_mod.cross_batch_misses(list(batches)),
            "notes_from_agents": {b.batch_id: b.notes[:5] for b in valid if b.notes},
            "done_without_output": list(done_missing),
        },
        "provenance": {
            "required": list(extract_graph.PROVENANCE_PROPS),
            "edges_without_provenance": provenance["total"],
            "by_type": provenance["by_type"],
        },
        "census": census,
        "consolidation": consolidation,
        "stale": stale,
        "precision_sample": precision_sample,
    }


def summarize(report: dict[str, Any]) -> str:
    b, p, c = report["batches"], report["planned"], report["census"]
    lines = [
        f"extract merge: {b['valid']}/{b['found']} batches valid "
        f"({b['envelope_valid_rate'] * 100:.0f}% envelope) → "
        f"{p['entities']} entities, {p['mentions']} mentions, {p['relations']} relations",
        "entities: " + (", ".join(f"{n} {k}" for k, n in p["entities_by_kind"].items()) or "none"),
        "relations: "
        + (", ".join(f"{n} {k}" for k, n in p["relations_by_type"].items()) or "none"),
        f"graph now: {c['entities']} :Entity, {c['edges_total']} LLM edges, "
        f"{report['weak_decisions']['weak']} weak Decisions, "
        f"{report['provenance']['edges_without_provenance']} edges without provenance",
    ]
    rejected = report["rejected_records"]
    if rejected["total"]:
        lines.append(
            f"rejected records: {rejected['total']} — "
            + ", ".join(f"{n} {r}" for r, n in rejected["by_reason"].items())
        )
    if report["rejected_batches"]:
        lines.append(
            "rejected batches: "
            + ", ".join(
                f"{x['batch']} ({x['error_count']} errors via {x['source']})"
                for x in report["rejected_batches"]
            )
        )
    if b["missing_outputs"]:
        lines.append(f"missing outputs: {b['missing_count']} (first: {b['missing_outputs'][0]})")
    return "\n".join(lines)
