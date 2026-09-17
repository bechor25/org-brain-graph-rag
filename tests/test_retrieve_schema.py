"""What the Text2Cypher author is allowed to see, and how much of it.

`apoc.meta.schema` on this graph is 64 top-level entries with every property of every
label and every relationship, sampled — far more than a prompt can carry and more than a
query author needs. `reduce_schema` is the editorial decision: labels with counts, their
property *types*, which properties are indexed, the relationship patterns that actually
occur, and the index list. These tests pin that decision to a fixture rather than to the
live graph, so a schema change is visible as a diff instead of as a bigger prompt.
"""

from __future__ import annotations

import pytest

from brain.retrieve import schema as schema_mod

META = {
    "Chunk": {
        "type": "node",
        "count": 13846,
        "labels": [],
        "properties": {
            "id": {"type": "STRING", "indexed": True, "unique": True, "existence": False},
            "text": {"type": "STRING", "indexed": False, "unique": False, "existence": False},
            "embedding": {"type": "LIST", "indexed": False, "unique": False, "array": True},
        },
        "relationships": {
            "MENTIONS": {
                "direction": "out",
                "count": 10,
                "labels": ["Entity"],
                "properties": {
                    "quote": {"type": "STRING"},
                    "batch_id": {"type": "STRING"},
                },
            },
            "HAS_CHUNK": {"direction": "in", "count": 13, "labels": ["Document"], "properties": {}},
        },
    },
    "Entity": {
        "type": "node",
        "count": 9038,
        "labels": [],
        "properties": {"id": {"type": "STRING", "indexed": True, "unique": True}},
        "relationships": {},
    },
    "_ResIndexMeta": {
        "type": "node",
        "count": 2,
        "labels": [],
        "properties": {},
        "relationships": {},
    },
    "MENTIONS": {"type": "relationship", "count": 10, "properties": {}},
}

STATS = {
    "nodeCount": 58624,
    "relCount": 161985,
    "labels": {"Chunk": 13846, "Entity": 9038, "_ResIndexMeta": 2},
    "relTypesCount": {"MENTIONS": 9375, "HAS_CHUNK": 13846},
}

INDEXES = [
    {
        "name": "chunk_embedding",
        "type": "VECTOR",
        "entityType": "NODE",
        "labelsOrTypes": ["Chunk"],
        "properties": ["embedding"],
        "state": "ONLINE",
    },
    {
        "name": "index_460996c0",
        "type": "LOOKUP",
        "entityType": "NODE",
        "labelsOrTypes": None,
        "properties": None,
        "state": "ONLINE",
    },
]

INDEX_META = [{"name": "chunk_embedding", "model": "bge-m3", "dim": 1024}]


@pytest.fixture
def reduced():
    return schema_mod.reduce_schema(META, STATS, INDEXES, INDEX_META)


def test_labels_carry_counts_and_property_types(reduced) -> None:
    chunk = next(label for label in reduced["labels"] if label["label"] == "Chunk")
    assert chunk["count"] == 13846
    assert chunk["properties"]["id"] == "STRING"
    assert chunk["indexed"] == ["id"]


def test_embeddings_never_reach_the_prompt(reduced) -> None:
    """1024 floats per node is not schema, and no query the author writes returns it."""
    chunk = next(label for label in reduced["labels"] if label["label"] == "Chunk")
    assert "embedding" not in chunk["properties"]


def test_internal_namespaces_are_hidden(reduced) -> None:
    """`_Retr…`, `_Res…` are test and pipeline scratch namespaces, not the org's schema."""
    assert all(not label["label"].startswith("_") for label in reduced["labels"])


def test_relationship_patterns_are_deduped_across_both_endpoints(reduced) -> None:
    """Every edge is described twice — once from each end — and must be listed once."""
    mentions = next(r for r in reduced["relationships"] if r["type"] == "MENTIONS")
    assert mentions["patterns"] == [{"from": "Chunk", "to": "Entity"}]
    assert mentions["count"] == 9375
    assert mentions["properties"]["quote"] == "STRING"
    has_chunk = next(r for r in reduced["relationships"] if r["type"] == "HAS_CHUNK")
    assert has_chunk["patterns"] == [{"from": "Document", "to": "Chunk"}]


def test_lookup_indexes_are_dropped_and_meta_is_kept(reduced) -> None:
    names = [i["name"] for i in reduced["indexes"]]
    assert names == ["chunk_embedding"]
    assert reduced["index_meta"] == INDEX_META


def test_totals_are_the_stats_not_the_sample(reduced) -> None:
    assert reduced["node_count"] == 58624
    assert reduced["relationship_count"] == 161985


def test_allowed_procedures_are_part_of_the_schema(reduced) -> None:
    """The author must know what it may call; the guard's allowlist is the only source."""
    from brain.retrieve.cypher_guard import ALLOWED_CALL_PREFIXES

    assert reduced["allowed_procedures"] == list(ALLOWED_CALL_PREFIXES)


def test_a_prefixed_namespace_sees_only_itself() -> None:
    meta = dict(META)
    meta["_RetrChunk"] = {
        "type": "node",
        "count": 13,
        "labels": [],
        "properties": {"id": {"type": "STRING"}},
        "relationships": {},
    }
    stats = {**STATS, "labels": {**STATS["labels"], "_RetrChunk": 13}}
    reduced = schema_mod.reduce_schema(meta, stats, [], [], prefix="_Retr")
    assert [label["label"] for label in reduced["labels"]] == ["_RetrChunk"]


# ------------------------------------------------------------------ sample values


class _ValueCtx:
    """A graph that answers the five bounded aggregations and nothing else."""

    prefix = ""

    def __init__(self, missing: str = "") -> None:
        self.missing = missing
        self.asked: list[str] = []

    def read(self, cypher: str, **params: object):
        self.asked.append(cypher)
        if self.missing and self.missing in cypher:
            raise RuntimeError("no such label in this database")
        assert "LIMIT $limit" in cypher, "the sample must be bounded on the server"
        return [{"value": "Resolved", "n": 900}, {"value": "Open", "n": 120}]


def test_sample_values_name_the_property_and_keep_order() -> None:
    """`STRING` does not tell an author that `open` is spelled nine ways in this corpus."""
    ctx = _ValueCtx()
    values = schema_mod.sample_values(ctx)
    assert values["WorkItem.status"] == ["Resolved", "Open"]
    assert set(values) == {f"{n}.{p}" for n, p, _ in schema_mod.SAMPLE_PROPERTIES}
    assert len(ctx.asked) == len(schema_mod.SAMPLE_PROPERTIES)


def test_a_relationship_property_is_sampled_over_the_relationship() -> None:
    ctx = _ValueCtx()
    schema_mod.sample_values(ctx, (("HAS_RUN", "status", "rel"),))
    assert "-[r:`HAS_RUN`]->" in ctx.asked[0]


def test_a_label_this_database_lacks_is_skipped_not_raised() -> None:
    """A namespace without work items still has a schema; it just has fewer samples."""
    values = schema_mod.sample_values(_ValueCtx(missing="WorkItem"))
    assert "WorkItem.status" not in values
    assert values["Document.kind"] == ["Resolved", "Open"]


def test_sample_values_reach_the_compact_rendering() -> None:
    reduced = schema_mod.reduce_schema(
        META, STATS, INDEXES, INDEX_META, samples={"WorkItem.status": ["Resolved", "Open"]}
    )
    assert reduced["sample_values"]["WorkItem.status"] == ["Resolved", "Open"]
    assert schema_mod.compact_schema(reduced)["sample_values"] == reduced["sample_values"]
    assert any("WorkItem.status IN" in line for line in schema_mod.schema_lines(reduced))


def test_a_schema_without_samples_still_has_the_key(reduced) -> None:
    assert reduced["sample_values"] == {}


# ------------------------------------------------------------------ caching


class _CountingCtx:
    """A context that records how often the schema was actually read."""

    prefix = ""

    def __init__(self) -> None:
        self.calls = 0

        class _S:
            neo4j_database = "neo4j"

        self.settings = _S()

    def read(self, cypher: str, **params: object):
        self.calls += 1
        if "apoc.meta.schema" in cypher:
            return [{"value": META}]
        if "apoc.meta.stats" in cypher:
            return [STATS]
        if "SHOW INDEXES" in cypher:
            return INDEXES
        return INDEX_META


def test_schema_is_cached_for_ten_minutes(monkeypatch) -> None:
    schema_mod.clear_cache()
    ctx = _CountingCtx()
    now = [1000.0]
    monkeypatch.setattr(schema_mod.time, "monotonic", lambda: now[0])

    first = schema_mod.get_schema(ctx)
    reads = ctx.calls
    assert reads > 0
    schema_mod.get_schema(ctx)
    assert ctx.calls == reads, "a second call inside the TTL must not touch the database"

    now[0] += schema_mod.SCHEMA_TTL_S + 1
    schema_mod.get_schema(ctx)
    assert ctx.calls > reads, "the cache expires after 10 minutes"
    assert first["labels"] == schema_mod.get_schema(ctx)["labels"]


def test_refresh_bypasses_the_cache(monkeypatch) -> None:
    schema_mod.clear_cache()
    ctx = _CountingCtx()
    monkeypatch.setattr(schema_mod.time, "monotonic", lambda: 5.0)
    schema_mod.get_schema(ctx)
    reads = ctx.calls
    schema_mod.get_schema(ctx, refresh=True)
    assert ctx.calls > reads
