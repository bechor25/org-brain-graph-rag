"""`brain chunk` — canonical text into `:Chunk` nodes, vectors and a vector index.

Order matters twice. Nodes are written before any vector, so an interrupted embedding run
resumes instead of restarting: the nodes are already there, `state()` reports which of
them still lack a vector, and the next run embeds only those. And the throughput
measurement runs before the full pass, so the batch size and the HTTP timeout are read
off a table rather than guessed — `--measure` writes nothing but that table.

The report is `data/reports/chunk.json`, merged rather than overwritten, so a `--measure`
run and the full run that follows it leave one file describing both.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from brain.chunk import graph as chunk_graph
from brain.chunk.chunker import TARGET_MAX, Chunk, ChunkStats
from brain.chunk.embed import (
    MEASURE_BATCHES,
    MEASURE_SAMPLE,
    CountingEmbedder,
    Throughput,
    measure,
    merge_tables,
)
from brain.chunk.scope import iter_chunks, select
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.corpus import load_corpus
from brain.harvest.base import utc_now_iso, write_json_atomic

REPORT_NAME = "chunk.json"
ALL_KINDS = ("doc", "issue", "comment", "commit")
#: Vectors are written in transactions of this many rows, independent of the embed batch:
#: a bigger embed batch should not mean a bigger transaction to lose to an interrupt.
WRITE_FLUSH = 250
DEFAULT_BATCH_SIZE = 32
DEFAULT_TIMEOUT_S = 120

NOTES = [
    "Token counts are estimates (`len(text)/4`): Ollama 0.32.6 answers /api/tokenize with "
    "404 and no tokenizer is installed. `throughput.estimate_calibration` compares the "
    "estimate with bge-m3's own `prompt_eval_count` over the 200-chunk measurement sample.",
    "A comment and a commit message are one chunk each whatever their length (brief §2), "
    "so the token distribution is bimodal: sections and descriptions sit in the 500-800 "
    "band, comments and messages are mostly under 150. Read `tokens_by_kind`, not the "
    "corpus-wide p50.",
    "A code fence or a Markdown table is never cut, so a chunk containing one can exceed "
    "the 800-token target; `oversize_chunks` counts them.",
    "An orphaned chunk keeps its node. `brain extract` cites chunk ids as evidence, and "
    "deleting a chunk would turn that provenance into a dangling reference.",
    "`Chunk.heading` is one property beyond the brief's list: the `#`/`##` section a "
    "document chunk starts under. Retrieval shows it and `brain extract` reads it as "
    "context; recovering it later would mean re-parsing the page.",
    "`--measure --measure-sample N --batch-size B` times an embedding pass over N chunks "
    "and writes nothing, which is how the full-corpus row in the throughput table was "
    "produced without touching the vectors already in the graph.",
]


@dataclass
class Timings:
    stages: dict[str, float]

    def record(self, name: str, started: float) -> None:
        self.stages[name] = round(time.perf_counter() - started, 2)


def resolve_kinds(value: str) -> set[str]:
    if value.strip() in ("", "all"):
        return set(ALL_KINDS)
    kinds = {k.strip() for k in value.split(",") if k.strip()}
    unknown = kinds - set(ALL_KINDS)
    if unknown:
        raise ValueError(f"unknown chunk kinds {sorted(unknown)}; known: {', '.join(ALL_KINDS)}")
    return kinds


def _percentiles(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    n = len(ordered)

    def pct(p: float) -> int:
        return ordered[min(n - 1, int(round(p * (n - 1))))]

    return {
        "count": n,
        "min": ordered[0],
        "p50": pct(0.50),
        "p95": pct(0.95),
        "max": ordered[-1],
        "sum": sum(ordered),
        "mean": round(sum(ordered) / n, 1),
    }


def chunking_report(chunks: Sequence[Chunk], stats: ChunkStats, seconds: float) -> dict[str, Any]:
    by_kind_tokens = {}
    for kind in sorted(stats.by_kind):
        by_kind_tokens[kind] = _percentiles([c.token_est for c in chunks if c.kind == kind])
    langs: dict[str, int] = {}
    for c in chunks:
        langs[c.lang] = langs.get(c.lang, 0) + 1
    return {
        "chunks": len(chunks),
        "seconds": round(seconds, 2),
        "by_kind": dict(sorted(stats.by_kind.items())),
        "by_parent_kind": dict(sorted(stats.by_parent_kind.items())),
        "by_lang": dict(sorted(langs.items())),
        "rejected_short": stats.rejected_short,
        "rejected_empty": stats.rejected_empty,
        "oversize_chunks": stats.oversize_chunks,
        "oversize_threshold_tokens": TARGET_MAX,
        "hard_split_blocks": stats.hard_split_blocks,
        "truncated": stats.truncated,
        "tokens_est": _percentiles([c.token_est for c in chunks]),
        "chars": _percentiles([c.char_len for c in chunks]),
        "tokens_by_kind": by_kind_tokens,
        "duplicate_ids": len(chunks) - len({c.id for c in chunks}),
    }


def _merge_report(path: Path, section: dict[str, Any]) -> dict[str, Any]:
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(section)
    return existing


def _previous_throughput(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("throughput")
    except json.JSONDecodeError:
        return None


def embed_chunks(
    ctx: GraphContext,
    embedder: CountingEmbedder,
    pending: Sequence[Chunk],
    *,
    batch_size: int,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Embed and persist in batches, so an interrupt costs one batch and not a run."""
    started = time.perf_counter()
    written = 0
    buffer: list[dict[str, Any]] = []
    last_echo = started
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        vectors = embedder.embed([c.text for c in batch], batch_size=batch_size)
        buffer.extend({"id": c.id, "vector": v} for c, v in zip(batch, vectors, strict=True))
        if len(buffer) >= WRITE_FLUSH:
            written += chunk_graph.write_embeddings(ctx, buffer)
            buffer.clear()
        now = time.perf_counter()
        if now - last_echo >= 20:
            done = i + len(batch)
            rate = done / (now - started)
            eta = (len(pending) - done) / rate if rate else 0
            echo(
                f"  embed: {done}/{len(pending)} chunks, {rate:.1f} chunks/s, "
                f"eta {eta / 60:.1f} min"
            )
            last_echo = now
    written += chunk_graph.write_embeddings(ctx, buffer)
    seconds = time.perf_counter() - started
    return {
        "requested": len(pending),
        "written": written,
        "seconds": round(seconds, 2),
        "chunks_per_s": round(len(pending) / seconds, 1) if seconds else 0.0,
        "real_tokens": embedder.prompt_tokens,
        "tokens_per_s": round(embedder.prompt_tokens / seconds) if seconds else 0,
        "est_tokens": sum(c.token_est for c in pending),
        "requests": embedder.requests,
        "request_seconds": round(embedder.request_seconds, 2),
    }


def _checks(
    census: dict[str, Any],
    index: dict[str, Any],
    meta: dict[str, Any] | None,
    embedding: dict[str, Any],
    chunking: dict[str, Any],
    created: dict[str, int],
    dim: int,
    model: str,
) -> list[dict[str, Any]]:
    return [
        {
            "name": "chunk_ids_unique",
            "expected": 0,
            "actual": chunking["duplicate_ids"],
            "ok": chunking["duplicate_ids"] == 0,
        },
        {
            "name": "every_chunk_has_an_embedding",
            "expected": 0,
            "actual": census["missing_embedding"],
            "ok": census["missing_embedding"] == 0,
        },
        {
            "name": "every_chunk_has_a_parent",
            "expected": 0,
            "actual": census["without_has_chunk"],
            "ok": census["without_has_chunk"] == 0,
        },
        {
            "name": "vector_index_online",
            "expected": "ONLINE",
            "actual": index.get("state"),
            "ok": index.get("state") == "ONLINE",
        },
        {
            "name": "vector_index_dim_matches_settings",
            "expected": dim,
            "actual": index.get("dim"),
            "ok": index.get("dim") == dim,
        },
        {
            "name": "index_meta_records_the_model",
            "expected": model,
            "actual": (meta or {}).get("model"),
            "ok": (meta or {}).get("model") == model,
        },
        {
            "name": "second_run_embeds_nothing",
            "expected": 0,
            "actual": embedding["requested"],
            "ok": embedding["requested"] == 0,
            "note": "only meaningful on a rerun over an already-chunked graph",
        },
        {
            "name": "second_run_creates_no_nodes",
            "expected": 0,
            "actual": created["nodes"],
            "ok": created["nodes"] == 0,
            "note": "only meaningful on a rerun over an already-chunked graph",
        },
    ]


def run_chunk(
    *,
    client: GraphClient,
    embedder: CountingEmbedder,
    canonical_dir: Path,
    reports_dir: Path,
    kinds: set[str] | None = None,
    limit: int | None = None,
    all_docs: bool = False,
    measure_only: bool = False,
    measure_sample: int | None = None,
    batch_size: int | None = None,
    prefix: str = "",
    write_report: bool = True,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    kinds = kinds or set(ALL_KINDS)
    started = time.perf_counter()
    timings = Timings({})
    ctx = GraphContext(client, prefix=prefix)
    report_path = reports_dir / REPORT_NAME

    t0 = time.perf_counter()
    corpus = load_corpus(canonical_dir)
    scope = select(corpus, all_docs=all_docs, limit=limit)
    if scope.parents() == 0:
        # `load_corpus` treats a missing file as an empty list, so an empty scope is
        # indistinguishable from "wrong directory" — and it is the input to orphan
        # marking, which would flag every chunk in the graph. Refuse instead.
        raise ValueError(
            f"{canonical_dir} holds no documents, work items or commits to chunk — "
            "run `uv run brain canon` first, or point --canonical-dir somewhere else"
        )
    timings.record("read_canonical", t0)
    echo(
        f"scope: {len(scope.documents)} documents, {len(scope.workitems)} work items, "
        f"{len(scope.commits)} keyed commits "
        f"({scope.stats['documents_skipped_unreferenced']} documents and "
        f"{scope.stats['commits_skipped_unkeyed']} commits are out of Phase A)"
    )

    t0 = time.perf_counter()
    stats = ChunkStats()
    chunks = list(iter_chunks(scope, kinds, stats))
    timings.record("chunking", t0)
    chunking = chunking_report(chunks, stats, timings.stages["chunking"])
    echo(
        f"chunks: {len(chunks)} in {timings.stages['chunking']}s — "
        f"{chunking['by_kind']}; token_est p50 {chunking['tokens_est'].get('p50')}, "
        f"p95 {chunking['tokens_est'].get('p95')}, max {chunking['tokens_est'].get('max')}; "
        f"{stats.rejected_short} rejected as too short"
    )

    if measure_only:
        sample_size = measure_sample or MEASURE_SAMPLE
        batches = (batch_size,) if batch_size else MEASURE_BATCHES
        echo(
            f"measuring embed throughput on {min(len(chunks), sample_size)} real chunks "
            f"at batch {', '.join(str(b) for b in batches)} (nothing is written)…"
        )
        t0 = time.perf_counter()
        throughput = measure(embedder, chunks, batches=batches, sample_size=sample_size, echo=echo)
        timings.record("measure", t0)
        previous = _previous_throughput(report_path) or {}
        table = merge_tables(previous.get("table", []), throughput.rows)
        throughput.rows = table
        est_total = _projection(chunking, throughput)
        echo(
            f"chosen: batch {throughput.batch_size}, timeout {throughput.timeout_s}s; "
            f"projected full run {est_total['projected_minutes']} min for "
            f"{est_total['chunks']} chunks"
        )
        report = _merge_report(
            report_path,
            {
                "step": "chunk",
                "generated_at": utc_now_iso(),
                "last_run": {
                    "mode": "measure",
                    "kinds": sorted(kinds),
                    "limit": limit,
                    "all_docs": all_docs,
                    "label_prefix": prefix,
                    "duration_s": round(time.perf_counter() - started, 2),
                    "per_stage_s": timings.stages,
                },
                "scope": scope.stats,
                "chunking": chunking,
                "throughput": {**throughput.report(), "projection": est_total},
                "notes": NOTES,
            },
        )
        if write_report:
            write_json_atomic(report_path, report)
        return report, 0

    previous = _previous_throughput(report_path)
    chosen_batch = batch_size or (previous or {}).get("chosen_batch_size") or DEFAULT_BATCH_SIZE
    echo(f"embedding with batch {chosen_batch}, timeout {embedder.timeout_s}s")

    t0 = time.perf_counter()
    schema = chunk_graph.apply_chunk_schema(ctx, embedder.dim)
    timings.record("schema", t0)

    t0 = time.perf_counter()
    before = chunk_graph.state(ctx)
    chunk_graph.write_nodes(ctx, chunks)
    # Snapshot here, not at the end: `IndexMeta` is created once and would otherwise make
    # the idempotency check read 1 on the first run and 0 on every run after it.
    created = {
        "nodes": ctx.counters["nodes_created"],
        "relationships": ctx.counters["relationships_created"],
    }
    edges = chunk_graph.write_edges(ctx, chunks)
    created["relationships"] = ctx.counters["relationships_created"] - created["relationships"]
    timings.record("write_nodes", t0)
    echo(
        f"nodes: {len(chunks)} merged ({created['nodes']} new), "
        f"HAS_CHUNK {sum(edges.values())} "
        f"({created['relationships']} new) in {timings.stages['write_nodes']}s"
    )

    pending = [c for c in chunks if before.needs_embedding(c)]
    echo(f"embedding: {len(pending)} of {len(chunks)} chunks need a vector")
    t0 = time.perf_counter()
    embedding = embed_chunks(ctx, embedder, pending, batch_size=chosen_batch, echo=echo)
    timings.record("embedding", t0)
    embedding.update(
        {
            "model": embedder.model,
            "dim": embedder.dim,
            "batch_size": chosen_batch,
            "timeout_s": embedder.timeout_s,
            "skipped_unchanged": len(chunks) - len(pending),
        }
    )

    t0 = time.perf_counter()
    orphans = _mark_orphans(ctx, chunks, kinds=kinds, limit=limit, all_docs=all_docs)
    timings.record("orphans", t0)

    t0 = time.perf_counter()
    index = chunk_graph.index_status(ctx)
    census = chunk_graph.census(ctx)
    now = utc_now_iso()
    chunk_graph.write_index_meta(
        ctx,
        {
            "name": chunk_graph.index_name(ctx),
            "model": embedder.model,
            "dim": embedder.dim,
            "similarity": "cosine",
            "chunk_count": census["chunks"],
            "updated_at": now,
        },
        now,
    )
    meta = chunk_graph.read_index_meta(ctx)
    timings.record("census", t0)

    checks = _checks(
        census,
        index,
        meta,
        embedding,
        chunking,
        created,
        embedder.dim,
        embedder.model,
    )
    total = round(time.perf_counter() - started, 2)
    report = _merge_report(
        report_path,
        {
            "step": "chunk",
            "generated_at": utc_now_iso(),
            "last_run": {
                "mode": "full" if not limit and kinds == set(ALL_KINDS) else "partial",
                "kinds": sorted(kinds),
                "limit": limit,
                "all_docs": all_docs,
                "canonical_dir": str(canonical_dir),
                "label_prefix": prefix,
                "duration_s": total,
                "per_stage_s": timings.stages,
                "counters": ctx.counters,
                "created": created,
            },
            "scope": scope.stats,
            "chunking": chunking,
            "embedding": embedding,
            "schema": schema,
            "index": index,
            "index_meta": meta,
            "orphans": orphans,
            "census": census,
            "checks": checks,
            "notes": NOTES,
        },
    )
    if write_report:
        write_json_atomic(report_path, report)

    rerun_only = {"second_run_embeds_nothing", "second_run_creates_no_nodes"}
    failed = [c["name"] for c in checks if not c["ok"] and c["name"] not in rerun_only]
    for c in checks:
        echo(
            f"[{'OK  ' if c['ok'] else 'FAIL'}] {c['name']}: "
            f"expected {c['expected']}, actual {c['actual']}"
        )
    echo(
        f"chunk: {census['chunks']} chunks, {census['embedded']} embedded, "
        f"{embedding['requested']} embedded this run in {embedding['seconds']}s "
        f"({embedding['tokens_per_s']} tokens/s), total {total}s"
    )
    return report, (1 if failed else 0)


def _projection(chunking: dict[str, Any], throughput: Throughput) -> dict[str, Any]:
    """What the measurement says the full pass will cost, before anyone waits for it.

    The rate comes from the *largest* sample measured at the chosen batch size: a
    200-chunk sample is optimistic about a run 50 times longer, and once a full pass has
    been timed there is no reason to keep quoting the sample.
    """
    rows = [r for r in throughput.rows if r["batch_size"] == throughput.batch_size]
    best = max(rows, key=lambda r: r["chunks"])
    rate = best["chunks_per_s"] or 1
    seconds = chunking["chunks"] / rate
    return {
        "chunks": chunking["chunks"],
        "est_tokens": chunking["tokens_est"].get("sum", 0),
        "at_chunks_per_s": rate,
        "measured_on_chunks": best["chunks"],
        "projected_seconds": round(seconds),
        "projected_minutes": round(seconds / 60, 1),
    }


def _mark_orphans(
    ctx: GraphContext,
    chunks: Sequence[Chunk],
    *,
    kinds: set[str],
    limit: int | None,
    all_docs: bool,
) -> dict[str, Any]:
    """Only a full pass may declare a chunk orphaned.

    A `--limit 500` run did not look at the other 10,000 chunks; marking everything it did
    not produce would turn a sampling run into a corpus-wide vandalism of the index.
    """
    if limit is not None or kinds != set(ALL_KINDS):
        return {"checked": False, "reason": "partial run (--limit or --kinds)", "marked": 0}
    if not chunks:
        return {"checked": False, "reason": "this run produced no chunks", "marked": 0}
    live = {c.id for c in chunks}
    existing = chunk_graph.existing_ids(ctx)
    orphan_ids = sorted(existing - live)
    marked = chunk_graph.mark_orphans(ctx, orphan_ids)
    return {
        "checked": True,
        "all_docs": all_docs,
        "existing_before": len(existing),
        "live": len(live),
        "marked": marked,
    }


def chunk_from_settings(
    canonical_dir: Path,
    reports_dir: Path,
    *,
    kinds: set[str] | None = None,
    limit: int | None = None,
    all_docs: bool = False,
    measure_only: bool = False,
    measure_sample: int | None = None,
    batch_size: int | None = None,
    timeout: float | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    from brain.config import get_settings

    s = get_settings()
    chosen_timeout = timeout or (_previous_throughput(reports_dir / REPORT_NAME) or {}).get(
        "chosen_timeout_s"
    )
    with GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database) as client:
        with CountingEmbedder(
            s.ollama_url, s.embed_model, s.embed_dim, timeout=chosen_timeout or DEFAULT_TIMEOUT_S
        ) as embedder:
            if not embedder.has_model():
                raise RuntimeError(
                    f"embedding model {s.embed_model!r} is not present in Ollama at {s.ollama_url}"
                )
            return run_chunk(
                client=client,
                embedder=embedder,
                canonical_dir=canonical_dir,
                reports_dir=reports_dir,
                kinds=kinds,
                limit=limit,
                all_docs=all_docs,
                measure_only=measure_only,
                measure_sample=measure_sample,
                batch_size=batch_size,
                echo=echo,
            )
