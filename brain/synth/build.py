"""`brain synth build` — turn the real corpus into batches an agent can write against.

Four decisions carry this module, and each one exists because three agents write the layer
in parallel:

1. **Which real items are worth covering.** Bug / Improvement / New Feature / Task / Wish
   with a non-empty description. `Sub-task` is out: its description is a fragment of its
   parent's, and generating a Test per sub-task would count the same story twice against
   the coverage ratio. Jira's own `Test` type is out too — those are test *tracking*
   issues, and covering a test with a test is noise the spec never asked for.

2. **Shards are cut by fix version, not round-robin.** Three of the spec's rules group
   across items — one TestPlan per fix version, one TestExecution per (plan, month), one
   ADO Feature per component+version group. If the items of `4.0.0` are spread over three
   shards, three agents each mint their own plan for it. Keeping a version whole inside one
   shard makes those rules satisfiable by an agent that can only see its own batches. The
   45% of items with no fix version have no grouping to protect and are dealt round-robin
   to balance the shards.

3. **Key ranges are disjoint per shard.** The spec asks for "global and monotonic"
   numbering read from `status.json`; that is only possible for one writer. Global
   *uniqueness* is what the acceptance criterion actually needs, so each shard gets its own
   block (`shard-01` → 10001–20000, `shard-02` → 20001–30000, …) and stays monotonic
   inside it. `brain synth merge` rejects a key outside its shard's block, so drift is
   caught rather than merged.

4. **Epics are pre-assigned.** "One ADO Epic per KIP referenced by the slice" is the one
   grouping that cuts across fix versions, so sharding cannot protect it. build mints
   `ADO-1…ADO-N` for the referenced KIPs out of a reserved block below every shard's range
   and marks exactly one batch as the `owned` writer; the others get the same key to link
   to. Without this, a KIP cited from two shards gets two Epics.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brain.canon.io import read_jsonl
from brain.canon.models import Container, Document, Person, WorkItem
from brain.canon.runner import natural_key
from brain.harvest.base import utc_now_iso, write_json_atomic
from brain.synth.models import (
    PREFIXES,
    BatchComment,
    BatchContainer,
    BatchDocument,
    BatchIdentity,
    BatchInput,
    BatchItem,
    Numbering,
    PreassignedEpic,
    ShardStatus,
)

#: Real Jira types worth a synthetic test / delivery item. See the module docstring.
ELIGIBLE_TYPES: frozenset[str] = frozenset({"Bug", "Improvement", "New Feature", "Task", "Wish"})

#: Types deliberately left out, with the reason the report prints.
EXCLUDED_TYPES: dict[str, str] = {
    "Sub-task": "description is a fragment of the parent's; covering both double-counts",
    "Test": "Jira's own test-tracking issues — the Xray layer is what replaces them",
}

#: Ids `ADO-1 … ADO-<RESERVED_MAX>` belong to build (pre-assigned Epics), never to a shard.
RESERVED_MAX = 10_000
#: Each shard owns `RESERVED_MAX` ids per prefix, starting one block above the reserved one.
SHARD_BLOCK = 10_000

#: Byte caps. A batch an agent reads in one gulp is a batch it reads properly; the target
#: is <150 KB per `.in.json`, and these keep the worst case around 100 KB at 40 items.
DESCRIPTION_CHARS = 1_500
COMMENT_CHARS = 300
MAX_COMMENTS = 2
KIP_EXCERPT_CHARS = 600

#: The longest single line a batch may contain. The agents' file reader truncates a long
#: line at ~48 KB; a compact `json.dump` put the whole 100 KB batch on one line and every
#: agent silently read only its first ~24 items. See `write_batch_json`.
MAX_LINE_BYTES = 8 * 1024

#: Package files, resolved from the module rather than the cwd. `data/` is relative by
#: convention (`Settings.data_dir`) because it is the user's; the contract and the schema
#: ship with the code, and `brain synth build` run from another directory must still find them.
_PACKAGE = Path(__file__).resolve().parent.parent
SPEC_PATH = _PACKAGE / "canon" / "synthetic_spec.md"
SCHEMA_PATH = _PACKAGE / "synth" / "schema.json"
#: How the two are *named* to the agent and in the manifest: repo-relative, so a batch
#: written on one machine reads the same on another.
SPEC_REF = "brain/canon/synthetic_spec.md"
SCHEMA_REF = "brain/synth/schema.json"
SPEC_COPY_NAME = "synthetic_spec.md"
MANIFEST_NAME = "MANIFEST.json"
STATUS_NAME = "status.json"
IN_GLOB = "[0-9][0-9][0-9].in.json"

INSTRUCTIONS: list[str] = [
    "Read `synthetic_spec.md` in this directory first. It is the contract; "
    "these lines only summarise it.",
    "Write `<NNN>.out.json` beside this file, validated against `brain/synth/schema.json`. "
    "Never a partial file.",
    "Every record: `synthetic: true`. Keys only XT/XE/XP/XS (source `xray`) or ADO (source `ado`).",
    "Use ONLY numbers inside this shard's `numbering` range, monotonically. `next` is the "
    "seed for batch 001; every later batch continues from `next_ids` in `status.json`.",
    "`epics[]` with `owned: true` are yours to emit with exactly that key; `owned: false` "
    "means another batch writes it — link to that key, do not emit the record.",
    "`persons[]` here are REAL people. Emit a new identity (a different display form) for "
    "each one you use and map it in `truth.identity_map` as `<new id>` -> `<person_id>`. "
    "Never invent a person, never invent an email.",
    "`reporter`/`assignee` on your records use the new synthetic identity keys, not the "
    "real Jira usernames — entity resolution has to have work to do.",
    "Record every planted noise in `truth`: text-only links, stale states, renames, "
    "duplicate tests. Unrecorded noise is a bug, not noise.",
    "Update `status.json` after every batch: append to `done`, and set `next_ids` to the "
    "next free number per prefix.",
    "If you cannot produce a valid output, append to `failed` with a reason. Never skip silently.",
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n… [truncated, {len(text)} chars in full]"


def shard_name(index: int) -> str:
    return f"shard-{index + 1:02d}"


def shard_range(index: int) -> tuple[int, int]:
    """`shard-01` → (10001, 20000). Disjoint per shard, all above the reserved block."""
    start = RESERVED_MAX * (index + 1) + 1
    return start, start + SHARD_BLOCK - 1


# --------------------------------------------------------------------------- selection


def is_eligible(item: WorkItem) -> bool:
    return (
        item.source == "jira"
        and not item.synthetic
        and item.type in ELIGIBLE_TYPES
        and bool((item.description or "").strip())
    )


def select_items(workitems: Iterable[WorkItem]) -> list[WorkItem]:
    """Eligible real items in a stable order (`KAFKA-9` before `KAFKA-10`)."""
    return sorted((w for w in workitems if is_eligible(w)), key=lambda w: natural_key(w.key))


def exclusion_reason(item: WorkItem) -> str | None:
    """Why an item is not in the slice, or None if it is. One reason each, first match."""
    if item.source != "jira":
        return f"source_{item.source}"
    if item.synthetic:
        return "already_synthetic"
    if item.type in EXCLUDED_TYPES:
        return f"type_{item.type}"
    if item.type not in ELIGIBLE_TYPES:
        return f"type_other_{item.type}"
    if not (item.description or "").strip():
        return "empty_description"
    return None


def excluded_counts(workitems: Iterable[WorkItem]) -> dict[str, int]:
    """What the selection threw away and why — the other half of `selected`."""
    counts = Counter(r for r in (exclusion_reason(w) for w in workitems) if r)
    return dict(sorted(counts.items()))


def primary_version(item: WorkItem) -> str | None:
    """The version a grouping rule should treat this item as belonging to.

    Sorted rather than "the first Jira listed", so the grouping does not depend on the
    order a REST response happened to use.
    """
    return sorted(item.fix_versions, key=natural_key)[0] if item.fix_versions else None


def assign_shards(items: Sequence[WorkItem], shards: int) -> list[list[WorkItem]]:
    """Split the items across shards, keeping every fix version whole inside one shard.

    Greedy largest-first bin packing over the versioned groups (the biggest group is 116 of
    1,021 items, so the bins stay close), then the unversioned items dealt round-robin.
    """
    if shards < 1:
        raise ValueError("shards must be >= 1")
    groups: dict[str, list[WorkItem]] = defaultdict(list)
    loose: list[WorkItem] = []
    for item in items:
        version = primary_version(item)
        if version is None:
            loose.append(item)
        else:
            groups[version].append(item)

    buckets: list[list[WorkItem]] = [[] for _ in range(shards)]
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), natural_key(kv[0])))
    for _, members in ordered:
        target = min(range(shards), key=lambda i: (len(buckets[i]), i))
        buckets[target].extend(members)
    for item in loose:
        target = min(range(shards), key=lambda i: (len(buckets[i]), i))
        buckets[target].append(item)

    return [sorted(b, key=lambda w: natural_key(w.key)) for b in buckets]


def chunked(items: Sequence[WorkItem], size: int) -> list[list[WorkItem]]:
    if size < 1:
        raise ValueError("batch size must be >= 1")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


# --------------------------------------------------------------------------- attachment


@dataclass
class Corpus:
    """The real canonical records build reads, indexed the way build needs them."""

    workitems: list[WorkItem]
    documents: dict[str, Document] = field(default_factory=dict)
    identities: dict[tuple[str, str], BatchIdentity] = field(default_factory=dict)
    components: dict[str, Container] = field(default_factory=dict)
    versions: dict[str, Container] = field(default_factory=dict)


def load_corpus(canonical_dir: Path) -> Corpus:
    workitems = list(read_jsonl(canonical_dir / "workitems.jsonl", WorkItem))
    corpus = Corpus(workitems=workitems)
    doc_path = canonical_dir / "documents.jsonl"
    if doc_path.is_file():
        for doc in read_jsonl(doc_path, Document):
            if doc.kind == "KIP" and doc.key not in corpus.documents:
                corpus.documents[doc.key] = doc
    person_path = canonical_dir / "persons.jsonl"
    if person_path.is_file():
        for person in read_jsonl(person_path, Person):
            if person.synthetic:
                continue
            for identity in person.identities:
                corpus.identities.setdefault(
                    (identity.source, identity.key),
                    BatchIdentity(
                        person_id=person.id,
                        source=identity.source,
                        key=identity.key,
                        display=identity.display,
                    ),
                )
    container_path = canonical_dir / "containers.jsonl"
    if container_path.is_file():
        for container in read_jsonl(container_path, Container):
            if container.synthetic:
                continue
            if container.kind == "component":
                corpus.components.setdefault(container.name, container)
            elif container.kind == "version":
                corpus.versions.setdefault(container.name, container)
    return corpus


def to_batch_item(item: WorkItem) -> BatchItem:
    description = (item.description or "").strip()
    comments = [
        BatchComment(
            author=c.author,
            at=c.at.isoformat() if c.at else None,
            body=_truncate(c.body, COMMENT_CHARS),
        )
        for c in item.comments[:MAX_COMMENTS]
        if (c.body or "").strip()
    ]
    return BatchItem(
        key=item.key,
        type=item.type,
        status=item.status,
        resolution=item.resolution,
        priority=item.priority,
        title=item.title,
        description=_truncate(description, DESCRIPTION_CHARS),
        description_chars=len(description),
        created=item.created.isoformat(),
        updated=item.updated.isoformat() if item.updated else None,
        resolved_at=item.resolved_at.isoformat() if item.resolved_at else None,
        reporter=item.reporter,
        assignee=item.assignee,
        components=list(item.components),
        labels=list(item.labels),
        fix_versions=list(item.fix_versions),
        affects_versions=list(item.affects_versions),
        parent=item.parent,
        links=list(item.links),
        kip_refs=sorted({r.key for r in item.refs if r.kind == "kip"}, key=natural_key),
        issue_refs=sorted({r.key for r in item.refs if r.kind == "issue"}, key=natural_key),
        comments=comments,
    )


def kips_of(items: Iterable[WorkItem]) -> list[str]:
    return sorted({r.key for i in items for r in i.refs if r.kind == "kip"}, key=natural_key)


def identity_keys_of(item: WorkItem) -> dict[str, set[str]]:
    """Real Jira identity keys this item uses, and the role each was used in."""
    used: dict[str, set[str]] = defaultdict(set)
    if item.reporter:
        used[item.reporter].add("reporter")
    if item.assignee:
        used[item.assignee].add("assignee")
    for comment in item.comments:
        if comment.author:
            used[comment.author].add("commenter")
    return used


# --------------------------------------------------------------------------- the build


@dataclass
class PlannedBatch:
    shard: int
    index: int
    items: list[WorkItem]

    @property
    def batch_id(self) -> str:
        return f"{shard_name(self.shard)}/{self.index:03d}"


def plan_batches(items: Sequence[WorkItem], *, shards: int, batch_size: int) -> list[PlannedBatch]:
    planned: list[PlannedBatch] = []
    for shard, bucket in enumerate(assign_shards(items, shards)):
        for i, chunk in enumerate(chunked(bucket, batch_size), start=1):
            planned.append(PlannedBatch(shard=shard, index=i, items=chunk))
    return planned


def plan_epics(
    planned: Sequence[PlannedBatch], documents: dict[str, Document]
) -> tuple[dict[str, PreassignedEpic], dict[str, str]]:
    """Mint one `ADO-<n>` per referenced KIP and pick the batch that must write it.

    Returns (kip -> epic, kip -> owning batch id). Keys are handed out in KIP order so the
    mapping is reproducible; the owner is the first batch (in shard/index order) that cites
    the KIP, which spreads the Epics over the shards that actually care about them.
    """
    owner: dict[str, str] = {}
    for batch in planned:
        for kip in kips_of(batch.items):
            owner.setdefault(kip, batch.batch_id)
    epics: dict[str, PreassignedEpic] = {}
    for n, kip in enumerate(sorted(owner, key=natural_key), start=1):
        if n > RESERVED_MAX:
            raise ValueError(f"more referenced KIPs than the reserved block holds ({RESERVED_MAX})")
        doc = documents.get(kip)
        epics[kip] = PreassignedEpic(
            key=f"ADO-{n}",
            kip=kip,
            title=doc.title if doc else kip,
            owned=False,
        )
    return epics, owner


def build_input(
    batch: PlannedBatch,
    *,
    corpus: Corpus,
    epics: dict[str, PreassignedEpic],
    epic_owner: dict[str, str],
    spec_sha: str,
    generated_at: str,
) -> BatchInput:
    kips = kips_of(batch.items)
    documents = [
        BatchDocument(
            key=kip,
            title=corpus.documents[kip].title,
            excerpt=_truncate(corpus.documents[kip].body_md, KIP_EXCERPT_CHARS),
            body_chars=len(corpus.documents[kip].body_md),
        )
        for kip in kips
        if kip in corpus.documents
    ]

    used: dict[str, set[str]] = defaultdict(set)
    for item in batch.items:
        for key, roles in identity_keys_of(item).items():
            used[key] |= roles
    persons = [
        BatchIdentity(
            person_id=corpus.identities[("jira", key)].person_id,
            source="jira",
            key=key,
            display=corpus.identities[("jira", key)].display,
            used_as=sorted(roles),
        )
        for key, roles in sorted(used.items())
        if ("jira", key) in corpus.identities
    ]

    component_names = sorted({c for item in batch.items for c in item.components})
    version_names = sorted(
        {v for item in batch.items for v in (*item.fix_versions, *item.affects_versions)},
        key=natural_key,
    )
    containers = [
        BatchContainer(id=corpus.components[name].id, kind="component", name=name)
        for name in component_names
        if name in corpus.components
    ] + [
        BatchContainer(id=corpus.versions[name].id, kind="version", name=name)
        for name in version_names
        if name in corpus.versions
    ]

    start, end = shard_range(batch.shard)
    numbering = {
        prefix: Numbering(
            range_start=start, range_end=end, next=start if batch.index == 1 else None
        )
        for prefix in PREFIXES
    }

    return BatchInput(
        batch_id=batch.batch_id,
        shard=shard_name(batch.shard),
        index=batch.index,
        generated_at=generated_at,
        spec=SPEC_COPY_NAME,
        spec_sha256=spec_sha,
        schema_ref=SCHEMA_REF,
        output=f"{batch.index:03d}.out.json",
        instructions=INSTRUCTIONS,
        numbering=numbering,
        epics=[
            epics[kip].model_copy(update={"owned": epic_owner[kip] == batch.batch_id})
            for kip in kips
            if kip in epics
        ],
        workitems=[to_batch_item(item) for item in batch.items],
        documents=documents,
        persons=persons,
        containers=containers,
    )


def shards_in_flight(root: Path) -> dict[str, int]:
    """Shards whose `status.json` already records finished batches, and how many.

    Rebuilding under a working agent is the one destructive thing this command can do: the
    agent is holding `.in.json` open, its `next_ids` describe keys minted against *these*
    inputs, and a reshard would silently repoint them. So a rebuild refuses rather than
    races, and the operator decides (delete the shard's status, or wait).
    """
    busy: dict[str, int] = {}
    if not root.is_dir():
        return busy
    for path in sorted(root.glob("shard-[0-9][0-9]/" + STATUS_NAME)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        done = data.get("done") if isinstance(data, dict) else None
        if isinstance(done, list) and done:
            busy[path.parent.name] = len(done)
    return busy


def write_batch_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a batch input *indented*, temp+rename, and refuse to leave a huge line behind.

    This is the one difference from `write_json_atomic`, and it cost 42% of the corpus.
    A compact dump puts the whole batch on a single 100 KB line; the LLM-role agents' file
    reader truncates a long line at ~48 KB, so every agent saw the first ~24 of its 40 items
    and silently generated against the head of each batch. Nothing failed — the outputs were
    valid, the ratios passed, and items 28-39 of every batch were never read by anybody.

    Indenting makes the longest line a single long description (~1.7 KB) instead of the whole
    file, so a line-oriented reader can page through it. `MAX_LINE_BYTES` is the guard that
    keeps the failure from coming back quietly: a batch that would ship an unreadable line is
    an error, not a warning, because the symptom of the warning being ignored is invisible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    longest = max((len(line.encode("utf-8")) for line in text.splitlines()), default=0)
    if longest > MAX_LINE_BYTES:
        raise ValueError(
            f"{path}: longest line is {longest} bytes, over the {MAX_LINE_BYTES} an agent's "
            "reader handles. Lower DESCRIPTION_CHARS / COMMENT_CHARS / KIP_EXCERPT_CHARS — a "
            "line an agent cannot read is content nobody generates against."
        )
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def longest_line_bytes(path: Path) -> int:
    with path.open("rb") as f:
        return max((len(line.rstrip(b"\n")) for line in f), default=0)


def write_input_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    """Write the batch unless the only difference from what is on disk is `generated_at`.

    Returns True if it wrote. A rebuild that changes nothing should leave the mtime and the
    sha alone: an agent watching its inputs, and a manifest recording their hashes, both
    read a rewrite as "this batch changed" — and `generated_at` alone changes on every run.
    """
    existing = None
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
    if isinstance(existing, dict):
        stamp = existing.get("generated_at")
        if existing == {**payload, "generated_at": stamp}:
            return False
    write_batch_json(path, payload)
    return True


def remove_stale_inputs(root: Path, planned: Sequence[PlannedBatch]) -> list[str]:
    """Delete `.in.json` files a previous, differently-shaped run left behind.

    A rebuild with a larger `--batch-size` plans fewer batches; without this the old
    `009.in.json` stays on disk and an agent dutifully writes an output for a batch that no
    longer exists. Only inputs are removed — an `.out.json` is the agent's work and stays,
    where merge reports it as `unexpected_outputs` rather than swallowing it.
    """
    current = {
        str((root / shard_name(b.shard) / f"{b.index:03d}.in.json").resolve()) for b in planned
    }
    removed: list[str] = []
    if not root.is_dir():
        return removed
    for path in sorted(root.glob(f"shard-*/{IN_GLOB}")):
        if str(path.resolve()) in current:
            continue
        path.unlink()
        removed.append(str(path.relative_to(root)))
    return removed


def run_build(
    *,
    canonical_dir: Path,
    batches_dir: Path,
    shards: int = 3,
    batch_size: int = 40,
    spec_path: Path = SPEC_PATH,
    schema_path: Path = SCHEMA_PATH,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Write `data/batches/synthetic/<shard>/NNN.in.json` + status + manifest. Idempotent."""
    started = time.perf_counter()
    if not spec_path.is_file():
        raise FileNotFoundError(f"the noise contract is missing: {spec_path}")

    busy = shards_in_flight(batches_dir / "synthetic")
    if busy:
        listed = ", ".join(f"{shard} ({n} done)" for shard, n in busy.items())
        raise ValueError(
            f"refusing to rebuild: {listed} already has finished batches. The agents' "
            "`next_ids` were minted against the current inputs, so resharding would "
            "silently repoint them. Merge what is done, or clear the shard's status.json."
        )

    corpus = load_corpus(canonical_dir)
    items = select_items(corpus.workitems)
    if not items:
        raise ValueError(f"no eligible work items in {canonical_dir}/workitems.jsonl")

    planned = plan_batches(items, shards=shards, batch_size=batch_size)
    epics, epic_owner = plan_epics(planned, corpus.documents)
    spec_sha = sha256_of(spec_path)
    generated_at = utc_now_iso()

    root = batches_dir / "synthetic"
    spec_text = spec_path.read_text(encoding="utf-8")
    written: list[dict[str, Any]] = []

    for batch in planned:
        shard_dir = root / shard_name(batch.shard)
        shard_dir.mkdir(parents=True, exist_ok=True)
        (shard_dir / SPEC_COPY_NAME).write_text(spec_text, encoding="utf-8")

        payload = build_input(
            batch,
            corpus=corpus,
            epics=epics,
            epic_owner=epic_owner,
            spec_sha=spec_sha,
            generated_at=generated_at,
        )
        path = shard_dir / f"{batch.index:03d}.in.json"
        rewritten = write_input_if_changed(path, payload.model_dump(mode="json"))

        status_path = shard_dir / STATUS_NAME
        if not status_path.is_file():
            start, _ = shard_range(batch.shard)
            write_json_atomic(
                status_path,
                ShardStatus(
                    shard=shard_name(batch.shard),
                    next_ids={p: start for p in PREFIXES},
                ).model_dump(mode="json"),
            )

        written.append(
            {
                "id": batch.batch_id,
                "path": str(path.relative_to(root)),
                "items": len(payload.workitems),
                "documents": len(payload.documents),
                "persons": len(payload.persons),
                "containers": len(payload.containers),
                "epics_owned": sum(1 for e in payload.epics if e.owned),
                "bytes": path.stat().st_size,
                "max_line_bytes": longest_line_bytes(path),
                "sha256": sha256_of(path),
                "rewritten": rewritten,
            }
        )
        echo(
            f"[{batch.batch_id}] {len(payload.workitems)} items, "
            f"{len(payload.documents)} KIPs, {len(payload.persons)} identities, "
            f"{path.stat().st_size / 1024:.0f} KB"
        )

    stale = remove_stale_inputs(root, planned)
    for name in stale:
        echo(f"removed stale input {name} (a previous run planned more batches than this one)")

    manifest = _manifest(
        written,
        corpus=corpus,
        items=items,
        planned=planned,
        epics=epics,
        epic_owner=epic_owner,
        shards=shards,
        batch_size=batch_size,
        canonical_dir=canonical_dir,
        spec_sha=spec_sha,
        schema_path=schema_path,
        generated_at=generated_at,
        stale=stale,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    write_json_atomic(root / MANIFEST_NAME, manifest)
    echo(
        f"synth build: {len(planned)} batches over {shards} shards, "
        f"{len(items)} real items, {len(epics)} pre-assigned Epics, "
        f"largest batch {manifest['sizes']['max_bytes'] / 1024:.0f} KB"
    )
    echo(f"manifest: {root / MANIFEST_NAME}")
    return manifest, 0


def _manifest(
    written: list[dict[str, Any]],
    *,
    corpus: Corpus,
    items: Sequence[WorkItem],
    planned: Sequence[PlannedBatch],
    epics: dict[str, PreassignedEpic],
    epic_owner: dict[str, str],
    shards: int,
    batch_size: int,
    canonical_dir: Path,
    spec_sha: str,
    schema_path: Path,
    generated_at: str,
    stale: Sequence[str],
    duration_ms: int,
) -> dict[str, Any]:
    sizes = [b["bytes"] for b in written]
    per_shard: Counter = Counter(b["id"].split("/")[0] for b in written)
    return {
        "step": "synth.build",
        "generated_at": generated_at,
        "duration_ms": duration_ms,
        "spec": {"path": SPEC_REF, "sha256": spec_sha, "copied_as": SPEC_COPY_NAME},
        "schema": {
            "path": SCHEMA_REF,
            "sha256": sha256_of(schema_path) if schema_path.is_file() else None,
        },
        "canonical_sha256": {
            p.name: sha256_of(p) for p in sorted(canonical_dir.glob("*.jsonl")) if p.is_file()
        },
        "selection": {
            "considered": len(corpus.workitems),
            "selected": len(items),
            "eligible_types": sorted(ELIGIBLE_TYPES),
            "excluded_types": EXCLUDED_TYPES,
            "rule": "source=jira, not synthetic, eligible type, non-empty description",
            "by_type": dict(sorted(Counter(i.type for i in items).items())),
            "with_fix_version": sum(1 for i in items if i.fix_versions),
            "excluded_counts": excluded_counts(corpus.workitems),
        },
        "sharding": {
            "shards": shards,
            "batch_size": batch_size,
            "rule": "fix versions kept whole (greedy largest-first), unversioned dealt round-robin",
            "batches_per_shard": dict(sorted(per_shard.items())),
            "items_per_shard": {
                shard_name(i): sum(len(b.items) for b in planned if b.shard == i)
                for i in range(shards)
            },
            "removed_stale_inputs": list(stale),
        },
        "numbering": {
            "prefixes": list(PREFIXES),
            "reserved_for_build": [1, RESERVED_MAX],
            "shard_ranges": {shard_name(i): list(shard_range(i)) for i in range(shards)},
            "note": (
                "Global monotonic numbering is impossible for parallel shards; global "
                "uniqueness is what the criterion needs. Each shard is monotonic inside "
                "its own block and merge rejects a key outside it."
            ),
        },
        "epics": [
            {"key": e.key, "kip": e.kip, "owner_batch": epic_owner[e.kip], "title": e.title}
            for e in sorted(epics.values(), key=lambda e: natural_key(e.key))
        ],
        "caps": {
            "description_chars": DESCRIPTION_CHARS,
            "comment_chars": COMMENT_CHARS,
            "max_comments": MAX_COMMENTS,
            "kip_excerpt_chars": KIP_EXCERPT_CHARS,
        },
        "sizes": {
            "max_line_bytes": max((b["max_line_bytes"] for b in written), default=0),
            "line_budget_bytes": MAX_LINE_BYTES,
            "max_bytes": max(sizes) if sizes else 0,
            "min_bytes": min(sizes) if sizes else 0,
            "total_bytes": sum(sizes),
            "mean_bytes": round(sum(sizes) / len(sizes)) if sizes else 0,
        },
        "totals": {
            "batches": len(written),
            "items": sum(b["items"] for b in written),
            "kip_attachments": sum(b["documents"] for b in written),
            "identity_attachments": sum(b["persons"] for b in written),
            "distinct_kips": len(epics),
        },
        "batches": written,
    }


def load_manifest(batches_dir: Path) -> dict[str, Any] | None:
    path = batches_dir / "synthetic" / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
