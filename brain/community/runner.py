"""Settings → a Neo4j connection (and an embedder) → one of the three `brain communities`
commands.

The commands themselves take a `GraphContext`, never settings: that is what lets the live
tests run all three inside a `_Comm…` label namespace against the same database the real
graph lives in, without a flag that could be left on by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from brain.community.batches import (
    DEFAULT_EVIDENCE,
    DEFAULT_EVIDENCE_CHARS,
    DEFAULT_SHARDS,
    DEFAULT_TOP_MEMBERS,
    run_batches,
)
from brain.community.build import run_build
from brain.community.merge import run_merge
from brain.community.partition import DEFAULT_LEVELS, DEFAULT_MIN_SIZE
from brain.community.projection import DEFAULT_GAMMA, DEFAULT_MAX_LEVELS, DEFAULT_SEED
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext


def _context(prefix: str = "") -> tuple[GraphClient, GraphContext]:
    from brain.config import get_settings

    s = get_settings()
    client = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    return client, GraphContext(client, prefix=prefix)


def build_from_settings(
    reports_dir: Path,
    *,
    levels: int = DEFAULT_LEVELS,
    level_indices: list[int] | None = None,
    min_size: int = DEFAULT_MIN_SIZE,
    seed: int = DEFAULT_SEED,
    max_levels: int = DEFAULT_MAX_LEVELS,
    gamma: float = DEFAULT_GAMMA,
    include_synthetic: bool = False,
    dry_run: bool = False,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    from brain.config import get_settings

    settings = get_settings()
    client, ctx = _context()
    with client:
        return run_build(
            ctx=ctx,
            reports_dir=reports_dir,
            embed_dim=settings.embed_dim,
            levels=levels,
            level_indices=level_indices,
            min_size=min_size,
            seed=seed,
            max_levels=max_levels,
            gamma=gamma,
            include_synthetic=include_synthetic,
            dry_run=dry_run,
            echo=echo,
        )


def batches_from_settings(
    batches_dir: Path,
    reports_dir: Path,
    *,
    shards: int = DEFAULT_SHARDS,
    top_members: int = DEFAULT_TOP_MEMBERS,
    evidence: int = DEFAULT_EVIDENCE,
    evidence_chars: int = DEFAULT_EVIDENCE_CHARS,
    force: bool = False,
    resummarize: bool = False,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    client, ctx = _context()
    with client:
        return run_batches(
            ctx=ctx,
            batches_dir=batches_dir,
            reports_dir=reports_dir,
            shards=shards,
            top_members=top_members,
            evidence=evidence,
            evidence_chars=evidence_chars,
            force=force,
            resummarize=resummarize,
            echo=echo,
        )


def merge_from_settings(
    batches_dir: Path,
    reports_dir: Path,
    *,
    echo: Callable[[str], None] = print,
) -> tuple[dict[str, Any], int]:
    from brain.config import get_settings
    from brain.embed.client import OllamaEmbedder

    settings = get_settings()
    client, ctx = _context()
    with (
        client,
        OllamaEmbedder(settings.ollama_url, settings.embed_model, settings.embed_dim) as embedder,
    ):
        return run_merge(
            ctx=ctx,
            batches_dir=batches_dir,
            reports_dir=reports_dir,
            embedder=embedder,
            echo=echo,
        )
