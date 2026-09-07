"""Settings → a Neo4j connection → one of the three `brain extract` commands.

The commands themselves take a `GraphContext`, never settings: that is what lets the live
tests run all three inside a `_Extract…` label namespace against the same database the real
graph lives in, without a flag that could be left on by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.extract.build import DEFAULT_BATCH_SIZE, DEFAULT_SHARDS, run_build
from brain.extract.merge import run_merge
from brain.extract.sample import DEFAULT_N, DEFAULT_SEED, DEFAULT_TARGETS, run_sample
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext


def _context(prefix: str = "") -> tuple[GraphClient, GraphContext]:
    from brain.config import get_settings

    s = get_settings()
    client = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    return client, GraphContext(client, prefix=prefix)


def build_from_settings(
    canonical_dir: Path,
    batches_dir: Path,
    reports_dir: Path,
    *,
    shards: int = DEFAULT_SHARDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    min_chars: int | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    client, ctx = _context()
    with client:
        return run_build(
            ctx=ctx,
            canonical_dir=canonical_dir,
            batches_dir=batches_dir,
            reports_dir=reports_dir,
            shards=shards,
            batch_size=batch_size,
            min_chars=min_chars,
            echo=echo,
        )


def merge_from_settings(
    batches_dir: Path,
    reports_dir: Path,
    *,
    canonical_dir: Path | None = None,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    client, ctx = _context()
    with client:
        return run_merge(
            ctx=ctx,
            batches_dir=batches_dir,
            reports_dir=reports_dir,
            canonical_dir=canonical_dir,
            echo=echo,
        )


def sample_from_settings(
    reports_dir: Path,
    *,
    n: int = DEFAULT_N,
    seed: int = DEFAULT_SEED,
    targets: str = DEFAULT_TARGETS,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    client, ctx = _context()
    with client:
        return run_sample(
            ctx=ctx, reports_dir=reports_dir, n=n, seed=seed, targets=targets, echo=echo
        )
