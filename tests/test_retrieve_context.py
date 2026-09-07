"""The invariants `RetrieveContext` enforces, with a fake client instead of a database.

The important one is the embedding-model check. A query vector from a different model than
the one that filled the index does not return worse results — it returns cosine distances
between two unrelated spaces, which look like results and are noise. That has to be a hard
error, and it has to be a hard error *before* the query runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from brain.config import Settings
from brain.retrieve.context import CHUNK_INDEX, RetrieveContext
from brain.retrieve.types import EmbedModelMismatch, RetrieveError


class FakeClient:
    """Answers the two shapes `RetrieveContext` asks for, and records what it was asked."""

    def __init__(self, meta: dict[str, Any] | None = None, indexes: tuple[str, ...] = ()) -> None:
        self.meta = meta
        self.indexes = list(indexes)
        self.queries: list[str] = []

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        self.queries.append(cypher)
        if "SHOW INDEXES" in cypher:
            return [{"name": n, "state": "ONLINE"} for n in self.indexes]
        if "IndexMeta" in cypher:
            return [self.meta] if self.meta else []
        return []

    def write(self, cypher: str, **params: Any) -> dict[str, int]:
        self.queries.append(cypher)
        if "CREATE FULLTEXT INDEX" in cypher:
            # Like the server: the index exists (ONLINE) right after the create returns.
            self.indexes.append(cypher.split("`")[1])
        return {}

    def close(self) -> None:
        pass


def context(client: FakeClient, **kw: Any) -> RetrieveContext:
    return RetrieveContext(client=client, settings=Settings(), **kw)


def test_a_model_the_index_was_not_built_with_is_a_hard_error() -> None:
    ctx = context(FakeClient(meta={"name": "chunk_embedding", "model": "e5-large", "dim": 1024}))
    with pytest.raises(EmbedModelMismatch, match="e5-large"):
        ctx.check_embedding_model()


def test_a_dimension_mismatch_is_a_hard_error() -> None:
    settings = Settings()
    ctx = context(
        FakeClient(meta={"name": "chunk_embedding", "model": settings.embed_model, "dim": 384})
    )
    with pytest.raises(EmbedModelMismatch, match="384"):
        ctx.check_embedding_model()


def test_a_matching_index_passes_and_is_read_only_once() -> None:
    settings = Settings()
    client = FakeClient(
        meta={"name": "chunk_embedding", "model": settings.embed_model, "dim": settings.embed_dim}
    )
    ctx = context(client)
    assert ctx.check_embedding_model()["model"] == settings.embed_model
    before = len(client.queries)
    ctx.check_embedding_model()
    assert len(client.queries) == before, "the meta node is a constant; re-reading it is latency"


def test_a_missing_index_meta_is_not_an_error() -> None:
    """An index nothing recorded is a `brain doctor` finding, not a reason to refuse to search."""
    assert context(FakeClient()).check_embedding_model() == {}


def test_require_index_names_the_step_that_creates_it() -> None:
    with pytest.raises(RetrieveError, match="brain chunk"):
        context(FakeClient()).require_index(CHUNK_INDEX)
    assert context(FakeClient(indexes=("chunk_embedding",))).require_index(CHUNK_INDEX) == (
        "chunk_embedding"
    )


def test_a_prefix_namespaces_labels_and_index_names() -> None:
    ctx = context(FakeClient(), prefix="_Retr")
    assert ctx.label("Chunk") == "`_RetrChunk`"
    assert ctx.index(CHUNK_INDEX) == "_Retrchunk_embedding"
    assert ctx.graph.prefix == "_Retr"


def test_the_synthetic_clause_is_empty_by_default_and_a_filter_when_switched_off() -> None:
    assert context(FakeClient()).synthetic_clause("c") == ""
    off = context(FakeClient(), include_synthetic=False)
    assert off.synthetic_clause("c") == " AND coalesce(c.synthetic, false) = false"
    assert off.synthetic_clause("c", joiner="WHERE").startswith(" WHERE")


def test_an_index_that_never_appears_is_given_up_on_rather_than_waited_out() -> None:
    """`await_index` on a name nothing creates must not block for the whole timeout."""
    import time

    started = time.monotonic()
    assert context(FakeClient()).await_index("nope", missing_grace_s=0.3) == "MISSING"
    assert time.monotonic() - started < 5


def test_the_fulltext_index_is_created_only_when_it_is_missing() -> None:
    present = FakeClient(indexes=("chunk_text",))
    assert context(present).ensure_fulltext_index() == "chunk_text"
    assert not any("CREATE FULLTEXT" in q for q in present.queries)

    missing = FakeClient()
    context(missing).ensure_fulltext_index()
    created = [q for q in missing.queries if "CREATE FULLTEXT" in q]
    assert created and "IF NOT EXISTS" in created[0]
    assert "ON EACH [c.`text`]" in created[0]
    assert not any("db.awaitIndexes" in q for q in missing.queries), (
        "awaiting every index in a shared database blocks on other agents' namespaces"
    )
