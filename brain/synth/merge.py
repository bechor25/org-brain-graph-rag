"""`brain synth merge` — the only writer of the synthetic layer into `data/canonical/`.

It is a *rewrite*, not an append, and that single decision buys the two properties the
brief asks for. Merge reads every `NNN.out.json` that exists, removes from the canonical
files every record its own ledger (`data/canonical/synthetic_merged.json`) says it put
there, and writes back exactly the records the currently-valid batches contain. So:

* re-running merge cannot duplicate — the second run removes what the first added before
  adding it again, and `write_jsonl` is temp+rename;
* a batch that was merged and has since been regenerated *worse* does not leave orphans
  behind: its old ids go with the ledger entry.

Records that a human added to `data/canonical/` by hand are untouched: only ids the ledger
claims, or ids the current batches carry, are ever removed.

What fails a batch and what merely warns
----------------------------------------
Fail (the batch goes to `retry/`, then `quarantine/`): unreadable JSON, a schema or
pydantic violation, `synthetic` not true, a key outside the `XT|XE|XP|XS|ADO` allowlist or
outside the shard's reserved number block, a key or Person id that already exists (real or
in an earlier batch), and a `truth` entry about a synthetic key the layer does not contain
— a truth claim with no record is worse than no claim.

Warn (counted, merged anyway): a link, ref or truth entry pointing at a *real* key that is
not in the corpus. The slice is a scoped subset of Jira, so a plausible reference to an
issue outside it is expected noise, not a defect — but the count is a quality signal and
belongs in the report. Also a warning: a `next_ids` that would make the next batch collide,
and a valid batch's link into a batch this same run rejected (`orphaned_by_rejection`) —
rejecting the citing batch too would cascade over a whole shard for one bad file.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from brain.canon.io import read_jsonl, write_jsonl
from brain.canon.models import Container, Document, Person, WorkItem
from brain.canon.runner import sort_key
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.synth import ratios as ratios_mod
from brain.synth.build import (
    RESERVED_MAX,
    SCHEMA_PATH,
    load_manifest,
    select_items,
    sha256_of,
    shard_range,
)
from brain.synth.jsonschema_mini import validate as schema_validate
from brain.synth.models import PREFIX_SOURCE, BatchOutput, Truth

MAX_RETRIES = 2
LEDGER_NAME = "synthetic_merged.json"
TRUTH_NAME = "synthetic_truth.json"
RETRY_DIR = "retry"
QUARANTINE_DIR = "quarantine"
RETRY_FIELD = "_synth_retry"

KEY_RE = re.compile(r"^(XT|XE|XP|XS|ADO)-(\d+)$")
BATCH_ID_RE = re.compile(r"^shard-(\d{2})/(\d{3})$")
CONTAINER_ID_RE = re.compile(r"^(xray|ado):(testplan|testset|sprint|area):.+$")

#: The canonical files the synthetic layer contributes to. `documents` and `changes` are
#: untouched: the layer produces neither KIPs nor commits.
TARGET_FILES: dict[str, type[BaseModel]] = {
    "workitems": WorkItem,
    "containers": Container,
    "persons": Person,
}

#: `synthetic_spec.md` names the text-only link's *source* field per case (`ado_key` for a
#: Story, `test_key` for a Test); the target is `jira_key` in both. The schema unifies the
#: pair as `from_key`/`to_key`, and `normalise_truth` accepts the spec's spelling.
LEGACY_SOURCE_FIELDS = ("ado_key", "test_key")
LEGACY_TARGET_FIELD = "jira_key"


class MergeError(RuntimeError):
    """Merge cannot produce a correct canonical file. Never downgraded to a partial run."""


# --------------------------------------------------------------------------- batch state


@dataclass
class Batch:
    """One `NNN.out.json` and everything merge concluded about it."""

    batch_id: str
    shard: str
    index: int
    path: Path
    sha256: str
    raw: dict[str, Any] | None = None
    output: BatchOutput | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.output is not None

    @property
    def in_path(self) -> Path:
        return self.path.with_name(f"{self.index:03d}.in.json")

    def ids(self) -> dict[str, list[str]]:
        if self.output is None:
            return {name: [] for name in TARGET_FILES}
        return {
            "workitems": [w.id for w in self.output.workitems],
            "containers": [c.id for c in self.output.containers],
            "persons": [p.id for p in self.output.persons],
        }


def discover(root: Path) -> list[Batch]:
    """Every `<shard>/NNN.out.json`, in shard then batch order."""
    found: list[Batch] = []
    for path in sorted(root.glob("shard-[0-9][0-9]/[0-9][0-9][0-9].out.json")):
        shard = path.parent.name
        # `Path("001.out.json").stem` is `"001.out"`, not `"001"` — two suffixes.
        index = int(path.name.split(".", 1)[0])
        found.append(
            Batch(
                batch_id=f"{shard}/{index:03d}",
                shard=shard,
                index=index,
                path=path,
                sha256=sha256_of(path),
            )
        )
    return found


# --------------------------------------------------------------------------- validation


def normalise_truth(raw: dict[str, Any]) -> list[str]:
    """Rewrite the spec's per-case field names into the schema's single pair. In place.

    `synthetic_spec.md` writes `{ado_key, jira_key}` for a Story and `{test_key, jira_key}`
    for a Test; the schema unifies both as `{from_key, to_key}` because they are the same
    fact. An agent that followed the spec's spelling literally is not worth quarantining.
    """
    truth = raw.get("truth")
    if not isinstance(truth, dict):
        return []
    links = truth.get("text_only_links")
    if not isinstance(links, list):
        return []
    converted = 0
    for link in links:
        if not isinstance(link, dict) or "from_key" in link:
            continue
        source = next((link.pop(f) for f in LEGACY_SOURCE_FIELDS if f in link), None)
        target = link.pop(LEGACY_TARGET_FIELD, None)
        if source is not None and target is not None:
            link["from_key"] = source
            link["to_key"] = target
            converted += 1
    if converted:
        return [
            f"{converted} text_only_links used the spec's per-case field names "
            "(ado_key/test_key + jira_key) and were normalised to from_key/to_key"
        ]
    return []


def parse_batch(batch: Batch, schema: dict[str, Any]) -> None:
    """JSON → schema → pydantic. Every stage's complaints, not just the first stage's."""
    try:
        raw = json.loads(batch.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        batch.errors.append(f"unreadable: {exc}")
        return
    if not isinstance(raw, dict):
        batch.errors.append(f"top level must be an object, got {type(raw).__name__}")
        return
    batch.raw = raw
    batch.notes.extend(normalise_truth(raw))

    if raw.get("batch_id") != batch.batch_id:
        batch.errors.append(f"batch_id is {raw.get('batch_id')!r}, file says {batch.batch_id!r}")

    problems = schema_validate(raw, schema)
    batch.errors.extend(f"schema: {p}" for p in problems[:50])
    if problems:
        return
    try:
        batch.output = BatchOutput.model_validate(raw)
    except ValidationError as exc:
        batch.errors.extend(f"model: {e['loc']}: {e['msg']}" for e in exc.errors()[:50])


def check_keys(batch: Batch) -> None:
    """Prefix allowlist, source/prefix agreement, id form, and the shard's number block."""
    output = batch.output
    if output is None:
        return
    match = BATCH_ID_RE.match(batch.batch_id)
    shard_index = int(match.group(1)) - 1 if match else 0
    low, high = shard_range(shard_index)

    for item in output.workitems:
        key_match = KEY_RE.match(item.key)
        if not key_match:
            batch.errors.append(f"{item.key}: key prefix not in XT|XE|XP|XS|ADO")
            continue
        prefix, number = key_match.group(1), int(key_match.group(2))
        expected_source = PREFIX_SOURCE[prefix]
        if item.source != expected_source:
            batch.errors.append(f"{item.key}: prefix {prefix} belongs to {expected_source!r}")
        if item.id != f"{item.source}:{item.key}":
            batch.errors.append(f"{item.key}: id must be {item.source}:{item.key}, got {item.id!r}")
        if not item.synthetic:
            batch.errors.append(f"{item.key}: synthetic must be true")
        in_reserved = 1 <= number <= RESERVED_MAX
        if not (in_reserved or low <= number <= high):
            batch.errors.append(
                f"{item.key}: number outside this shard's block {low}-{high} "
                f"(and outside the build-reserved 1-{RESERVED_MAX})"
            )

    for container in output.containers:
        if not container.synthetic:
            batch.errors.append(f"{container.id}: synthetic must be true")
        if not CONTAINER_ID_RE.match(container.id):
            batch.errors.append(f"{container.id}: id must be <xray|ado>:<kind>:<name>")
        if container.id != f"{container.source}:{container.kind}:{container.name}":
            batch.errors.append(f"{container.id}: id does not match source/kind/name")

    for person in output.persons:
        if not person.synthetic:
            batch.errors.append(f"{person.id}: synthetic must be true")
        if len(person.identities) != 1:
            batch.errors.append(f"{person.id}: a synthetic person carries exactly one identity")
            continue
        identity = person.identities[0]
        if identity.source not in {"ado", "xray"}:
            batch.errors.append(f"{person.id}: identity source must be ado|xray")
        if person.id != f"{identity.source}:{identity.key}":
            batch.errors.append(f"{person.id}: id must be {identity.source}:{identity.key}")
        if identity.email:
            batch.errors.append(f"{person.id}: never invent an email for a real person")

    check_next_ids(batch, low, high)


def check_next_ids(batch: Batch, low: int, high: int) -> None:
    """`next_ids` must be past every key this batch minted, and inside the shard's block.

    A warning, not an error: the batch itself is correct either way. What a wrong value
    breaks is the *next* batch, which would re-mint a key merge then rejects for being a
    duplicate — a failure two batches downstream of its cause. Saying it here is cheaper.
    """
    output = batch.output
    if output is None or not output.next_ids:
        return
    highest: dict[str, int] = {}
    for item in output.workitems:
        match = KEY_RE.match(item.key)
        if match:
            prefix, number = match.group(1), int(match.group(2))
            if number > RESERVED_MAX:  # a reserved Epic key says nothing about the shard's cursor
                highest[prefix] = max(highest.get(prefix, 0), number)
    for prefix, value in sorted(output.next_ids.items()):
        if prefix not in PREFIX_SOURCE:
            batch.warnings.append(f"next_ids: {prefix} is not one of {sorted(PREFIX_SOURCE)}")
        elif value <= highest.get(prefix, 0):
            batch.warnings.append(
                f"next_ids[{prefix}]={value} is not past {highest[prefix]}, the highest "
                f"{prefix} this batch used — the next batch would collide"
            )
        elif not low <= value <= high + 1:
            batch.warnings.append(
                f"next_ids[{prefix}]={value} is outside this shard's block {low}-{high}"
            )


@dataclass
class RealCorpus:
    """What "already exists" means when merge checks a key or a target."""

    workitem_keys: set[str] = field(default_factory=set)
    document_keys: set[str] = field(default_factory=set)
    person_ids: set[str] = field(default_factory=set)
    container_ids: set[str] = field(default_factory=set)
    eligible: list[WorkItem] = field(default_factory=list)
    persons_by_real_id: dict[str, str] = field(default_factory=dict)


def load_real(canonical_dir: Path, *, synthetic_ids: set[str]) -> RealCorpus:
    """The non-synthetic canonical records, plus the eligible slice the ratios need.

    A record already in `data/canonical/` that this run is about to rewrite is *not* "an
    existing key" — otherwise every re-merge would collide with itself.
    """
    real = RealCorpus()
    items = list(read_jsonl(canonical_dir / "workitems.jsonl", WorkItem))
    for item in items:
        if item.synthetic or item.id in synthetic_ids:
            continue
        real.workitem_keys.add(item.key)
    real.eligible = select_items(items)

    for name, model, sink in (
        ("documents", Document, "document_keys"),
        ("persons", Person, "person_ids"),
        ("containers", Container, "container_ids"),
    ):
        path = canonical_dir / f"{name}.jsonl"
        if not path.is_file():
            continue
        for record in read_jsonl(path, model):
            if record.synthetic or record.id in synthetic_ids:
                continue
            field_name = "key" if sink == "document_keys" else "id"
            getattr(real, sink).add(getattr(record, field_name))
            if sink == "person_ids":
                real.persons_by_real_id[record.id] = record.id
    return real


def check_uniqueness(batches: Sequence[Batch], real: RealCorpus) -> None:
    """Global key/id uniqueness, first batch wins. Deterministic because order is sorted."""
    seen_keys: dict[str, str] = {}
    seen_ids: dict[str, str] = {}
    for batch in batches:
        if batch.output is None:
            continue
        for item in batch.output.workitems:
            if item.key in real.workitem_keys:
                batch.errors.append(f"{item.key}: a real work item already has this key")
            elif item.key in seen_keys:
                batch.errors.append(f"{item.key}: already minted by {seen_keys[item.key]}")
            else:
                seen_keys[item.key] = batch.batch_id
        for person in batch.output.persons:
            if person.id in real.person_ids:
                batch.errors.append(f"{person.id}: a real person already has this id")
            elif person.id in seen_ids:
                batch.errors.append(f"{person.id}: already minted by {seen_ids[person.id]}")
            else:
                seen_ids[person.id] = batch.batch_id
        for container in batch.output.containers:
            if container.id in real.container_ids:
                batch.errors.append(f"{container.id}: a real container already has this id")
            elif container.id in seen_ids:
                batch.errors.append(f"{container.id}: already minted by {seen_ids[container.id]}")
            else:
                seen_ids[container.id] = batch.batch_id


def check_targets(batches: Sequence[Batch], real: RealCorpus) -> Counter:
    """Links, refs and truth entries: synthetic targets must exist, real ones may not.

    Returns the dangling counter the report prints. A dangling *synthetic* key is an error
    (the layer is self-contained and the writer controls both ends); a dangling real key is
    a warning (the corpus is a scoped slice of Jira).
    """
    all_synthetic_keys = {item.key for b in batches if b.output for item in b.output.workitems}
    known = real.workitem_keys | all_synthetic_keys
    dangling: Counter = Counter()

    def note(batch: Batch, kind: str, target: str, owner: str) -> None:
        if KEY_RE.match(target):
            if target not in all_synthetic_keys:
                batch.errors.append(f"{owner}: {kind} target {target} is not in the layer")
            return
        if target not in known:
            dangling[target] += 1
            batch.warnings.append(f"{owner}: {kind} target {target} is outside the slice")

    for batch in batches:
        if batch.output is None:
            continue
        for item in batch.output.workitems:
            for link in item.links:
                note(batch, "link", link.target, item.key)
            for ref in item.refs:
                if ref.kind == "issue":
                    note(batch, "ref", ref.key, item.key)
                elif ref.kind == "kip" and ref.key not in real.document_keys:
                    dangling[ref.key] += 1
                    batch.warnings.append(f"{item.key}: KIP ref {ref.key} is not in the corpus")
        _check_truth(batch, batch.output.truth, real, all_synthetic_keys, dangling)
    return dangling


def _check_truth(
    batch: Batch,
    truth: Truth,
    real: RealCorpus,
    all_synthetic_keys: set[str],
    dangling: Counter,
) -> None:
    batch_person_ids = {p.id for p in batch.output.persons} if batch.output else set()
    for new_id, real_id in truth.identity_map.items():
        if new_id not in batch_person_ids:
            batch.errors.append(f"truth.identity_map: {new_id} is not a person this batch emits")
        if real_id not in real.person_ids:
            batch.errors.append(f"truth.identity_map: {real_id} is not a real person id")

    def synthetic_side(value: str, where: str) -> None:
        if value not in all_synthetic_keys:
            batch.errors.append(f"truth.{where}: {value} is not a record this layer contains")

    def real_side(value: str, where: str) -> None:
        if KEY_RE.match(value):
            synthetic_side(value, where)
        elif value not in real.workitem_keys:
            dangling[value] += 1
            batch.warnings.append(f"truth.{where}: {value} is outside the slice")

    for link in truth.text_only_links:
        synthetic_side(link.from_key, "text_only_links")
        real_side(link.to_key, "text_only_links")
    for stale in truth.stale_states:
        synthetic_side(stale.ado_key, "stale_states")
        real_side(stale.jira_key, "stale_states")
    for rename in truth.renames:
        synthetic_side(rename.test_key, "renames")
        real_side(rename.jira_key, "renames")
    for pair in truth.duplicate_tests:
        synthetic_side(pair.a, "duplicate_tests")
        synthetic_side(pair.b, "duplicate_tests")


def orphans_of_rejected_batches(
    valid: Sequence[Batch], merged: Sequence[BaseModel]
) -> dict[str, list[str]]:
    """Synthetic targets a merged record names that a *rejected* batch was going to write.

    `check_targets` runs before the verdict is in, so it judges a cross-batch link against
    every batch on disk. When one of those batches is then rejected, a link that was
    correct at check time points at nothing. Rejecting the citing batch too would cascade
    unpredictably (and could empty a whole shard over one bad file), so the merge reports
    it instead: this is the list `brain load` would otherwise turn into missing edges.
    """
    present = {getattr(r, "key", "") for r in merged}
    orphans: dict[str, list[str]] = defaultdict(list)
    for batch in valid:
        if batch.output is None:
            continue
        for item in batch.output.workitems:
            targets = {link.target for link in item.links}
            targets |= {ref.key for ref in item.refs if ref.kind == "issue"}
            for target in sorted(targets):
                if KEY_RE.match(target) and target not in present:
                    orphans[batch.batch_id].append(f"{item.key} -> {target}")
    return dict(sorted(orphans.items()))


# --------------------------------------------------------------------------- retry flow


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

    The attempt counter only moves when the output actually changed. Re-running merge over
    an unchanged bad batch is a re-read, not a new attempt — otherwise two idle merges
    would quarantine work the agent never got the chance to redo.
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


# --------------------------------------------------------------------------- the merge


def merge_truth(batches: Iterable[Batch]) -> dict[str, Any]:
    """One truth file from every valid batch. Rebuilt, not appended — so it cannot drift."""
    identity_map: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []
    text_only: set[tuple[str, str]] = set()
    stale: set[tuple[str, str, str, str]] = set()
    renames: set[tuple[str, str, str, str]] = set()
    duplicates: set[tuple[str, str]] = set()
    sources: list[str] = []

    for batch in batches:
        if not batch.ok or batch.output is None:
            continue
        sources.append(batch.batch_id)
        truth = batch.output.truth
        for new_id, real_id in truth.identity_map.items():
            if identity_map.setdefault(new_id, real_id) != real_id:
                conflicts.append(
                    {"identity": new_id, "kept": identity_map[new_id], "also": real_id}
                )
        text_only.update((link.from_key, link.to_key) for link in truth.text_only_links)
        stale.update(
            (s.ado_key, s.jira_key, s.ado_status, s.jira_status) for s in truth.stale_states
        )
        renames.update(
            (r.test_key, r.jira_key, r.test_phrase, r.jira_phrase) for r in truth.renames
        )
        # symmetric: (XT-9, XT-4) and (XT-4, XT-9) are one duplicate, recorded once
        duplicates.update(tuple(sorted((p.a, p.b))) for p in truth.duplicate_tests)  # type: ignore[misc]

    return {
        "generated_at": utc_now_iso(),
        "note": "Ground truth by construction. Evaluation only — never an input to the pipeline.",
        "batches": sorted(sources),
        "identity_map": dict(sorted(identity_map.items())),
        "identity_conflicts": conflicts,
        "text_only_links": [{"from_key": a, "to_key": b} for a, b in sorted(text_only)],
        "stale_states": [
            {"ado_key": a, "jira_key": j, "ado_status": s, "jira_status": t}
            for a, j, s, t in sorted(stale)
        ],
        "renames": [
            {"test_key": t, "jira_key": j, "test_phrase": tp, "jira_phrase": jp}
            for t, j, tp, jp in sorted(renames)
        ],
        "duplicate_tests": [{"a": a, "b": b} for a, b in sorted(duplicates)],
        "counts": {
            "identity_map": len(identity_map),
            "text_only_links": len(text_only),
            "stale_states": len(stale),
            "renames": len(renames),
            "duplicate_tests": len(duplicates),
        },
    }


def rewrite_canonical(
    canonical_dir: Path, *, records: dict[str, list[BaseModel]], drop_ids: set[str]
) -> dict[str, dict[str, int]]:
    """Replace the synthetic slice of each canonical file. Atomic, sorted, idempotent."""
    written: dict[str, dict[str, int]] = {}
    for name, model in TARGET_FILES.items():
        path = canonical_dir / f"{name}.jsonl"
        existing = list(read_jsonl(path, model)) if path.is_file() else []
        fresh = records.get(name, [])
        fresh_ids = {r.id for r in fresh}
        kept = [r for r in existing if r.id not in drop_ids and r.id not in fresh_ids]
        merged = kept + fresh
        merged.sort(key=sort_key)
        written[name] = {
            "total": write_jsonl(path, merged),
            "kept": len(kept),
            "synthetic": len(fresh),
            "removed": len(existing) - len(kept),
        }
    return written


def run_merge(
    *,
    canonical_dir: Path,
    batches_dir: Path,
    reports_dir: Path,
    schema_path: Path = SCHEMA_PATH,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Validate every batch output, rewrite the synthetic slice, write truth and report."""
    root = batches_dir / "synthetic"
    if not root.is_dir():
        raise MergeError(f"no batches to merge: {root} does not exist (run `brain synth build`)")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    batches = discover(root)
    for batch in batches:
        parse_batch(batch, schema)
        check_keys(batch)

    ledger = _read_json(canonical_dir / LEDGER_NAME) or {}
    ledger_batches: dict[str, Any] = dict(ledger.get("batches") or {})
    ledger_failed: dict[str, Any] = dict(ledger.get("failed") or {})
    claimed_ids = {
        rid
        for entry in ledger_batches.values()
        for name in TARGET_FILES
        for rid in (entry.get(name) or [])
    }
    current_ids = {rid for b in batches for ids in b.ids().values() for rid in ids}

    real = load_real(canonical_dir, synthetic_ids=claimed_ids | current_ids)
    check_uniqueness(batches, real)
    dangling = check_targets(batches, real)

    valid = [b for b in batches if b.ok]
    invalid = [b for b in batches if not b.ok]

    records: dict[str, list[BaseModel]] = {name: [] for name in TARGET_FILES}
    for batch in valid:
        assert batch.output is not None
        records["workitems"].extend(batch.output.workitems)
        records["containers"].extend(batch.output.containers)
        records["persons"].extend(batch.output.persons)

    orphaned = orphans_of_rejected_batches(valid, records["workitems"])
    written = rewrite_canonical(canonical_dir, records=records, drop_ids=claimed_ids)

    truth = merge_truth(valid)
    write_json_atomic(canonical_dir / TRUTH_NAME, truth)

    new_ledger_batches: dict[str, Any] = {}
    for batch in valid:
        new_ledger_batches[batch.batch_id] = {
            "sha256": batch.sha256,
            "merged_at": utc_now_iso(),
            **batch.ids(),
        }
        clear_failure_files(batch)
    new_failed: dict[str, Any] = {}
    for batch in invalid:
        new_failed[batch.batch_id] = handle_failure(batch, ledger_failed)

    write_json_atomic(
        canonical_dir / LEDGER_NAME,
        {
            "step": "synth.merge",
            "updated_at": utc_now_iso(),
            "batches": dict(sorted(new_ledger_batches.items())),
            "failed": dict(sorted(new_failed.items())),
        },
    )

    persons_by_id: dict[str, list[str]] = defaultdict(list)
    for person in records["persons"]:
        assert isinstance(person, Person)
        persons_by_id[person.id] = [i.display or "" for i in person.identities]
    merged_truth = Truth.model_validate(
        {k: truth[k] for k in Truth.model_fields}  # the merged view, not any single batch's
    )
    measured = ratios_mod.compute(
        real=real.eligible,
        synthetic=[w for w in records["workitems"] if isinstance(w, WorkItem)],
        truth=merged_truth,
        persons_by_id=dict(persons_by_id),
    )

    report = build_report(
        batches=batches,
        valid=valid,
        invalid=invalid,
        written=written,
        truth=truth,
        dangling=dangling,
        orphaned=orphaned,
        ratios=measured,
        manifest=load_manifest(batches_dir),
        real=real,
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(reports_dir / "synth.json", report)

    echo(summarize(report))
    echo(f"report: {reports_dir / 'synth.json'}")
    return report, (1 if invalid else 0)


# --------------------------------------------------------------------------- reporting


def build_report(
    *,
    batches: Sequence[Batch],
    valid: Sequence[Batch],
    invalid: Sequence[Batch],
    written: dict[str, dict[str, int]],
    truth: dict[str, Any],
    dangling: Counter,
    orphaned: dict[str, list[str]],
    ratios: Sequence[ratios_mod.Ratio],
    manifest: dict[str, Any] | None,
    real: RealCorpus,
) -> dict[str, Any]:
    items = [w for b in valid if b.output for w in b.output.workitems]
    expected = {b["id"] for b in (manifest or {}).get("batches", [])}
    seen = {b.batch_id for b in batches}
    return {
        "step": "synth",
        "generated_at": utc_now_iso(),
        "batches": {
            "found": len(batches),
            "valid": len(valid),
            "invalid": len(invalid),
            "expected": len(expected),
            "missing_outputs": sorted(expected - seen),
            "unexpected_outputs": sorted(seen - expected) if expected else [],
        },
        "counts": {
            "workitems": len(items),
            "by_source": dict(sorted(Counter(w.source for w in items).items())),
            "by_type": dict(sorted(Counter(w.type for w in items).items())),
            "containers": sum(len(b.output.containers) for b in valid if b.output),
            "persons": sum(len(b.output.persons) for b in valid if b.output),
        },
        "canonical": written,
        "truth": truth["counts"] | {"identity_conflicts": len(truth["identity_conflicts"])},
        "warnings": {
            "dangling_targets": sum(dangling.values()),
            "distinct_dangling_keys": len(dangling),
            "top_dangling": [[k, n] for k, n in dangling.most_common(20)],
            "per_batch": {b.batch_id: b.warnings[:10] for b in batches if b.warnings},
            # links a merged record makes into a batch that was rejected in this same run
            "orphaned_by_rejection": sum(len(v) for v in orphaned.values()),
            "orphaned_per_batch": {k: v[:10] for k, v in orphaned.items()},
        },
        "notes": {b.batch_id: b.notes for b in batches if b.notes},
        "rejected": [
            {
                "batch": b.batch_id,
                "output": str(b.path),
                "errors": b.errors[:20],
                "error_count": len(b.errors),
            }
            for b in invalid
        ],
        "ratios": ratios_mod.summarize(ratios),
        "slice": {
            "eligible_real_items": len(real.eligible),
            "real_workitem_keys": len(real.workitem_keys),
            "real_person_ids": len(real.person_ids),
        },
        "spec_sha256": (manifest or {}).get("spec", {}).get("sha256"),
    }


def summarize(report: dict[str, Any]) -> str:
    b = report["batches"]
    c = report["counts"]
    r = report["ratios"]
    lines = [
        f"synth merge: {b['valid']}/{b['found']} batches valid → "
        f"{c['workitems']} work items, {c['persons']} identities, {c['containers']} containers",
        "truth: " + ", ".join(f"{n} {k}" for k, n in report["truth"].items()),
        f"warnings: {report['warnings']['dangling_targets']} dangling targets "
        f"({report['warnings']['distinct_dangling_keys']} distinct keys)"
        + (
            f", {report['warnings']['orphaned_by_rejection']} links into a rejected batch"
            if report["warnings"]["orphaned_by_rejection"]
            else ""
        ),
        f"ratios: {r['passed']}/{r['checked']} within tolerance"
        + (f" — FAILED: {', '.join(r['failed'])}" if r["failed"] else ""),
    ]
    if report["rejected"]:
        lines.append(
            "rejected: "
            + ", ".join(f"{x['batch']} ({x['error_count']} errors)" for x in report["rejected"])
        )
    if b["missing_outputs"]:
        lines.append(f"missing outputs: {', '.join(b['missing_outputs'])}")
    return "\n".join(lines)
