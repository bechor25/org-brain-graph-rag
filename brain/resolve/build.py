"""`brain resolve build-batches` — the grey band, packed for `entity-adjudicator`.

The band is not stored between runs; it is re-derived from the vectors on the nodes every
time. That is deliberate: the adjudicator has to judge pairs as the graph is *now*, and
after a tier-2 auto-merge "now" is a graph with fewer nodes and richer evidence on the
survivors. A stored queue would hand the agent pairs about nodes that no longer exist.

Sizes follow step 07's hard lesson: indented JSON, measured against the real serialised
payload, 40 KB per file — a batch an agent's file reader truncates is a batch judged on
text nobody read.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brain.embed.client import OllamaEmbedder
from brain.graph.context import GraphContext
from brain.harvest.base import utc_now_iso
from brain.resolve.models import BatchInput, BatchPair, Candidate, Pair, PairSide
from brain.resolve.tiers import ADJUDICATE_FLOOR, AUTO_THRESHOLD

TASK = "resolve"
SCHEMA_PATH = Path(__file__).with_name("schema.json")
SCHEMA_REF = "brain/resolve/schema.json"
STATUS_NAME = "status.json"
IN_GLOB = "[0-9][0-9][0-9].in.json"

#: Brief 08 decision 4.
DEFAULT_SHARDS = 2
MAX_PAIRS = 25
#: Bytes, decimal KB — the brief says "40 KB" and a reviewer dividing by 1000 must agree.
MAX_BATCH_BYTES = 40_000
MAX_LINE_BYTES = 45_000
#: Evidence per side, as (quotes/roles, parents). Brief 08 gives people three touched
#: items; the coordinator gives entities two quotes plus the documents they were found in,
#: because for an entity "where was this said" is most of the question.
EVIDENCE_PER_SIDE: dict[str, tuple[int, int]] = {"person": (3, 0), "entity": (2, 2)}
MAX_EVIDENCE_PER_SIDE = 3
PARENT_ROLE = "PARENT"


class BuildError(RuntimeError):
    """Batches cannot be produced an agent could safely work from."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def shard_name(index: int) -> str:
    return f"shard-{index + 1:02d}"


def pair_id(kind: str, a: str, b: str) -> str:
    """Stable across rebuilds: the same two nodes always get the same id."""
    lo, hi = sorted((a, b))
    return hashlib.sha1(f"{kind}|{lo}|{hi}".encode()).hexdigest()[:16]


def side(candidate: Candidate) -> PairSide:
    quotes, parents = EVIDENCE_PER_SIDE.get(candidate.kind, (MAX_EVIDENCE_PER_SIDE, 0))
    said = [e for e in candidate.evidence if e.role != PARENT_ROLE][:quotes]
    where = [e for e in candidate.evidence if e.role == PARENT_ROLE][:parents]
    return PairSide(
        id=candidate.id,
        name=candidate.name,
        source=candidate.source,
        description=candidate.description,
        identities=candidate.identities,
        aliases=candidate.aliases,
        evidence=[*said, *where],
    )


def to_batch_pair(pair: Pair, by_id: dict[str, Candidate]) -> BatchPair:
    return BatchPair(
        pair_id=pair_id(pair.kind, pair.a, pair.b),
        kind=pair.kind,
        block=pair.block,
        similarity=round(pair.score, 4),
        a=side(by_id[pair.a]),
        b=side(by_id[pair.b]),
    )


@dataclass
class PlannedBatch:
    shard: int
    index: int
    pairs: list[BatchPair]

    @property
    def batch_id(self) -> str:
        return f"{shard_name(self.shard)}/{self.index:03d}"


def assign_shards(pairs: Sequence[BatchPair], shards: int) -> list[list[BatchPair]]:
    """Round-robin by descending similarity: every shard gets the same mix of hard pairs."""
    if shards < 1:
        raise BuildError(f"shards must be at least 1, got {shards}")
    buckets: list[list[BatchPair]] = [[] for _ in range(shards)]
    ordered = sorted(pairs, key=lambda p: (-p.similarity, p.pair_id))
    for i, pair in enumerate(ordered):
        buckets[i % shards].append(pair)
    return [sorted(b, key=lambda p: (-p.similarity, p.pair_id)) for b in buckets]


def envelope(
    *,
    batch_id: str,
    shard: str,
    index: int,
    kind: str,
    pairs: Sequence[BatchPair],
    generated_at: str,
    schema_sha: str,
) -> dict[str, Any]:
    """`band` describes what is *in this batch*, not the tier's nominal window.

    The two are not the same and saying so cost an adjudication round: a pair scoring 1.00
    can be in the band, because the name guard demotes an initials-only pair however high
    its cosine. Reading `[0.80, 0.92)` beside a `similarity: 1.0` makes the reader distrust
    one of the two numbers, and the number they should distrust is the label.
    """
    scores = [p.similarity for p in pairs]
    return BatchInput(
        batch_id=batch_id,
        shard=shard,
        index=index,
        task=TASK,
        kind=kind,
        generated_at=generated_at,
        schema_path=SCHEMA_REF,
        schema_sha256=schema_sha,
        band=[min(scores), max(scores)] if scores else [ADJUDICATE_FLOOR, AUTO_THRESHOLD],
        band_note=(
            f"Actual similarity range of this batch. The adjudication window is "
            f"[{ADJUDICATE_FLOOR}, {AUTO_THRESHOLD}), but a pair above it is here because "
            "an automatic merge was refused — for people, a display that is only initials "
            "plus a surname; for entities, no shared word and no shared parent document. "
            "A high similarity is therefore not a reason to answer `same`."
        ),
        pair_count=len(pairs),
        pairs=list(pairs),
    ).model_dump(mode="json")


def serialise(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def pack(
    pairs: Sequence[BatchPair],
    *,
    shard: int,
    kind: str,
    max_pairs: int = MAX_PAIRS,
    max_bytes: int = MAX_BATCH_BYTES,
    generated_at: str = "",
    schema_sha: str = "",
) -> list[PlannedBatch]:
    """Greedy packing against the *serialised* size, so "40 KB" is measured, not guessed."""
    batches: list[PlannedBatch] = []
    current: list[BatchPair] = []

    def close() -> None:
        if current:
            batches.append(PlannedBatch(shard=shard, index=len(batches) + 1, pairs=list(current)))
            current.clear()

    def size(candidate: Sequence[BatchPair]) -> int:
        payload = envelope(
            batch_id=f"{shard_name(shard)}/{len(batches) + 1:03d}",
            shard=shard_name(shard),
            index=len(batches) + 1,
            kind=kind,
            pairs=candidate,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )
        return len(serialise(payload).encode("utf-8"))

    for pair in pairs:
        current.append(pair)
        if len(current) > max_pairs:
            current.pop()
            close()
            current.append(pair)
            continue
        if size(current) > max_bytes and len(current) > 1:
            current.pop()
            close()
            current.append(pair)
    close()
    return batches


def write_batch_json(path: Path, payload: dict[str, Any]) -> None:
    text = serialise(payload)
    longest = max((len(line.encode("utf-8")) for line in text.splitlines()), default=0)
    if longest > MAX_LINE_BYTES:
        raise BuildError(
            f"{path}: longest line is {longest} bytes, over the {MAX_LINE_BYTES} an agent's "
            "reader handles."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def write_input_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    """Write unless the only difference from what is on disk is `generated_at`."""
    existing: Any = None
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


def shards_in_flight(root: Path) -> dict[str, int]:
    busy: dict[str, int] = {}
    if not root.is_dir():
        return busy
    for path in sorted(root.glob(f"shard-[0-9][0-9]/{STATUS_NAME}")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        done = data.get("done") if isinstance(data, dict) else None
        if isinstance(done, list) and done:
            busy[path.parent.name] = len(done)
    return busy


def remove_stale_inputs(root: Path, planned: Sequence[PlannedBatch]) -> list[str]:
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
    ctx: GraphContext,
    batches_dir: Path,
    kinds: Sequence[str],
    embedder: OllamaEmbedder,
    k: int,
    vector_evidence: int | None = None,
    shards: int = DEFAULT_SHARDS,
    force: bool = False,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    """Re-derive the grey band and write `<shard>/NNN.in.json` for it."""
    from brain.resolve import graph as resolve_graph
    from brain.resolve.embed import EVIDENCE_IN_VECTOR
    from brain.resolve.runner import score_tier2

    started = time.perf_counter()
    if len(kinds) != 1:
        raise BuildError(
            f"build one kind at a time, got {list(kinds)}: people and entities get their own "
            "shard tree so an agent working one is never handed the other"
        )
    # `data/batches/resolve/<kind>/shard-NN/`. People and entities are separate work with
    # separate status files; sharing a tree would make a rebuild of one refuse because the
    # other is in flight, and would delete its inputs as stale.
    root = batches_dir / TASK / kinds[0]
    busy = shards_in_flight(root)
    if busy and not force:
        raise BuildError(
            f"{root}: {busy} — a shard already reports finished batches against the current "
            "inputs. Rebuilding would repoint the pairs those decisions answer. Merge them "
            "first (`brain resolve merge-decisions`) or pass --force."
        )

    generated_at = utc_now_iso()
    schema_sha = sha256_of(SCHEMA_PATH)
    planned: list[PlannedBatch] = []
    per_kind: dict[str, Any] = {}
    for kind in kinds:
        candidates = resolve_graph.read_candidates(ctx, kind)
        if not candidates:
            per_kind[kind] = {"candidates": 0, "grey": 0, "batches": 0}
            continue
        by_id = {c.id: c for c in candidates}
        from brain.resolve.runner import DEFAULT_K, ENTITY_K

        _auto, grey, stats = score_tier2(
            ctx,
            kind=kind,
            candidates=candidates,
            embedder=embedder,
            k=ENTITY_K if (kind == "entity" and k == DEFAULT_K) else k,
            vector_evidence=EVIDENCE_IN_VECTOR if vector_evidence is None else vector_evidence,
            echo=echo,
        )
        batch_pairs = [to_batch_pair(p, by_id) for p in grey]
        offset = len(planned)
        for i, bucket in enumerate(assign_shards(batch_pairs, shards)):
            planned.extend(
                pack(
                    bucket,
                    shard=offset + i,
                    kind=kind,
                    generated_at=generated_at,
                    schema_sha=schema_sha,
                )
            )
        per_kind[kind] = {
            "candidates": len(candidates),
            "auto_band": stats["auto"],
            "grey": len(grey),
            "band_limit": stats.get("band_limit"),
            "band_cut_off_rank": stats.get("band_cut_off_rank"),
            "shards": shards,
            "score_range": [
                round(min((p.score for p in grey), default=0.0), 4),
                round(max((p.score for p in grey), default=0.0), 4),
            ],
        }

    removed = remove_stale_inputs(root, planned)
    written = 0
    for batch in planned:
        path = root / shard_name(batch.shard) / f"{batch.index:03d}.in.json"
        payload = envelope(
            batch_id=batch.batch_id,
            shard=shard_name(batch.shard),
            index=batch.index,
            kind=batch.pairs[0].kind if batch.pairs else "person",
            pairs=batch.pairs,
            generated_at=generated_at,
            schema_sha=schema_sha,
        )
        if write_input_if_changed(path, payload):
            written += 1
    for shard in {shard_name(b.shard) for b in planned}:
        status = root / shard / STATUS_NAME
        if not status.exists():
            status.parent.mkdir(parents=True, exist_ok=True)
            status.write_text(json.dumps({"done": [], "failed": []}, indent=2) + "\n", "utf-8")

    sizes = [
        (root / shard_name(b.shard) / f"{b.index:03d}.in.json").stat().st_size for b in planned
    ]
    manifest = {
        "step": "resolve",
        "section": "build_batches",
        "generated_at": generated_at,
        "root": str(root),
        "schema_path": SCHEMA_REF,
        "schema_sha256": schema_sha,
        "band": [ADJUDICATE_FLOOR, AUTO_THRESHOLD],
        "kinds": dict(per_kind),
        "batches": len(planned),
        "pairs": sum(len(b.pairs) for b in planned),
        "shards": sorted({shard_name(b.shard) for b in planned}),
        "files_written": written,
        "stale_removed": removed,
        "max_batch_bytes": max(sizes, default=0),
        "duration_s": round(time.perf_counter() - started, 2),
    }
    for kind, stats in per_kind.items():
        echo(f"{kind}: grey band {stats.get('grey', 0)} pairs")
    echo(
        f"build-batches: {manifest['batches']} batches, {manifest['pairs']} pairs, "
        f"largest {manifest['max_batch_bytes']} bytes -> {root}"
    )
    return manifest, 0
