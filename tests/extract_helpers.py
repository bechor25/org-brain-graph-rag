"""Small builders for the extract tests, and the mini graph the live tests run against.

The graph half is deliberately thin: `brain load` on the mini fixture, then the chunker's
own output written straight in with `brain.chunk.graph.write_nodes/write_edges` and **no
embeddings**. Extraction reads `Chunk.text`, never `Chunk.embedding`, so pulling Ollama
into these tests would buy nothing and cost a minute per run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from brain.canon.io import read_jsonl
from brain.canon.models import Document, WorkItem
from brain.chunk import graph as chunk_graph
from brain.chunk.chunker import Chunk, ChunkStats, chunk_document, chunk_workitem_description
from brain.extract.models import BatchInput, BatchOutput
from brain.extract.validate import Batch, GraphFacts, Ref
from brain.graph.context import GraphContext

MINI = Path("data/fixtures/mini")
#: The mini corpus has no description over 300 characters, so the live tests lower the
#: Phase A floor rather than inventing a fixture issue nobody else uses.
MINI_MIN_CHARS = 90


def mini_chunks(canonical_dir: Path = MINI) -> list[Chunk]:
    """Every chunk Phase A could see in the mini corpus: KIP sections + descriptions."""
    stats = ChunkStats()
    chunks: list[Chunk] = []
    for doc in read_jsonl(canonical_dir / "documents.jsonl", Document):
        chunks.extend(chunk_document(doc, stats))
    for item in read_jsonl(canonical_dir / "workitems.jsonl", WorkItem):
        if (item.description or "").strip():
            chunks.extend(chunk_workitem_description(item, stats))
    return chunks


def write_chunks(ctx: GraphContext, chunks: list[Chunk]) -> int:
    chunk_graph.write_nodes(ctx, chunks)
    chunk_graph.write_edges(ctx, chunks)
    return len(chunks)


# ------------------------------------------------------------------------- unit helpers


def chunk_context(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "chunk_id": "a" * 40,
        "parent_key": "KIP-5",
        "parent_kind": "Document",
        "parent_title": "KIP-5: Next generation consumer group protocol",
        "position": 0,
        "kip_keys_referenced": [],
        "text": "The classic protocol makes clients do assignment, which causes long rebalances.",
    }
    return {**base, **kw}


def batch_input(chunks: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    items = chunks if chunks is not None else [chunk_context()]
    base: dict[str, Any] = {
        "batch_id": "shard-01/001",
        "shard": "shard-01",
        "index": 1,
        "task": "extract",
        "phase": "A",
        "generated_at": "2026-09-06T00:00:00+00:00",
        "schema_path": "brain/extract/schema.json",
        "schema_sha256": "0" * 64,
        "chunk_count": len(items),
        "chunks": items,
    }
    return {**base, **kw}


def entity(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "Problem",
        "name": "long rebalances",
        "description": "Assignment done by clients makes rebalances long.",
        "quote": "causes long rebalances",
        "chunk_id": "a" * 40,
    }
    return {**base, **kw}


def relation(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "MOTIVATED_BY",
        "source": "move assignment to the coordinator",
        "target": "long rebalances",
        "evidence_chunk_id": "a" * 40,
    }
    return {**base, **kw}


def batch_output(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"batch_id": "shard-01/001", "entities": [entity()], "relations": []}
    return {**base, **kw}


def written_batch(tmp_path: Path, *, inp: dict[str, Any], out: dict[str, Any]) -> Path:
    """Write an `.in.json`/`.out.json` pair where `discover` will find them."""
    import json

    shard, index = inp["batch_id"].split("/")
    shard_dir = tmp_path / "extract" / shard
    shard_dir.mkdir(parents=True, exist_ok=True)
    (shard_dir / f"{index}.in.json").write_text(
        json.dumps(inp, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    path = shard_dir / f"{index}.out.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def screened(path: Path, schema: dict[str, Any], facts: GraphFacts | None = None) -> Batch:
    """Parse + screen one written batch, the way `run_merge` does."""
    from brain.extract import validate as validate_mod

    shard = path.parent.name
    index = int(path.name.split(".", 1)[0])
    batch = Batch(
        batch_id=f"{shard}/{index:03d}",
        shard=shard,
        index=index,
        path=path,
        sha256="0" * 64,
    )
    validate_mod.parse_batch(batch, schema)
    validate_mod.screen(batch, facts or GraphFacts(), schema)
    return batch


__all__ = [
    "MINI",
    "MINI_MIN_CHARS",
    "Batch",
    "BatchInput",
    "BatchOutput",
    "GraphFacts",
    "Ref",
    "batch_input",
    "batch_output",
    "chunk_context",
    "entity",
    "mini_chunks",
    "relation",
    "screened",
    "write_chunks",
    "written_batch",
]
