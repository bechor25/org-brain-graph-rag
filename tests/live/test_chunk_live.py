"""`brain chunk` against the real Neo4j and the real `bge-m3`.

Two halves, on purpose.

The round trip runs in its own label namespace (`_Chunk…`) on the mini fixture, so it can
load, chunk, rerun and reword without touching the graph a real `brain chunk` built. It is
the test of the mechanism: vectors land, the index comes online, a second run creates and
embeds nothing, and a reworded page orphans the chunk it replaced instead of deleting it.

The cross-lingual sanity check is the test of the *corpus*, so it reads the real
`chunk_embedding` index and asserts nothing about the mechanism. It is read-only. If
`brain chunk` has never run it skips with a message saying so — a skip is honest, a query
against an empty index would not be.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.chunk import graph as chunk_graph
from brain.chunk.runner import run_chunk
from brain.config import Settings
from brain.embed.client import OllamaEmbedder
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import drop_schema

pytestmark = pytest.mark.live

MINI = Path("data/fixtures/mini")
PREFIX = "_Chunk"

HEBREW_QUERY = "פרוטוקול איזון מחדש של הצרכן"
ENGLISH_QUERY = "consumer group rebalance protocol"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def client(settings):
    c = GraphClient(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database
    )
    c.verify()
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="module")
def embedder(settings):
    with OllamaEmbedder(
        settings.ollama_url, settings.embed_model, settings.embed_dim, timeout=120
    ) as e:
        assert e.has_model(), f"{settings.embed_model} is not pulled in Ollama"
        yield e


# --------------------------------------------------------------------------- round trip


@pytest.fixture(scope="module")
def scratch(client):
    ctx = GraphContext(client, prefix=PREFIX)
    _clean(ctx)
    try:
        yield ctx
    finally:
        _clean(ctx)


def _clean(ctx: GraphContext) -> None:
    ctx.client.write(f"MATCH (c:{ctx.label('Chunk')}) DETACH DELETE c")
    ctx.client.write(f"MATCH (m:{ctx.label('IndexMeta')}) DETACH DELETE m")
    wipe(ctx)
    chunk_graph.drop_chunk_schema(ctx)
    drop_schema(ctx)


def _chunk(ctx, embedder, canonical_dir, reports_dir, **kw):
    return run_chunk(
        client=ctx.client,
        embedder=embedder,
        canonical_dir=canonical_dir,
        reports_dir=reports_dir,
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
        **kw,
    )


@pytest.fixture(scope="module")
def chunked(scratch, embedder, tmp_path_factory):
    reports = tmp_path_factory.mktemp("chunk-reports")
    load_report, code = run_load(
        client=scratch.client,
        canonical_dir=MINI,
        reports_dir=reports,
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0, [c for c in load_report["checks"] if not c["ok"]]
    report, code = _chunk(scratch, embedder, MINI, reports)
    assert code == 0, [c for c in report["checks"] if not c["ok"]]
    return report


def test_the_mini_corpus_produces_chunks_of_every_kind(chunked):
    """Counts come from the scope, not from a hardcoded total: the mini fixture grows as
    other steps add records to it, and this test is about Phase A's rules."""
    census = chunked["census"]
    scope = chunked["scope"]
    assert set(census["by_kind"]) == {"section", "description", "comment", "message"}
    # KIP-5 is the only page and both KAFKA-100 and commit a1b2c3d4 name it.
    assert scope["documents_reachable"] == 1
    assert scope["documents_skipped_unreferenced"] == 0
    # One of the three commits ("e5f6a7b8") names no issue and no KIP, so it is out.
    assert scope["commits_keyed"] == scope["commits_total"] - 1
    assert census["by_parent_kind"]["Commit"] == scope["commits_keyed"]
    assert census["by_parent_kind"]["Document"] == 1
    # Every work item in the fixture has a usable description, plus one chunk per comment.
    assert census["by_parent_kind"]["WorkItem"] == scope["workitems"] + scope["comments"]
    assert census["chunks"] == chunked["chunking"]["chunks"]


def test_every_chunk_is_embedded_and_hangs_off_its_parent(chunked, scratch):
    census = chunked["census"]
    assert census["missing_embedding"] == 0
    assert census["without_has_chunk"] == 0
    assert census["has_chunk_total"] == census["chunks"]
    rows = scratch.read(
        f"MATCH (d:{scratch.label('Document')} {{key: 'KIP-5'}})"
        f"-[:HAS_CHUNK]->(c:{scratch.label('Chunk')}) "
        "RETURN c.kind AS kind, c.position AS position, size(c.embedding) AS dim "
        "ORDER BY position"
    )
    assert rows and all(r["kind"] == "section" and r["dim"] == 1024 for r in rows)


def test_the_vector_index_is_online_with_the_settings_dimension(chunked, settings):
    index = chunked["index"]
    assert index["name"] == f"{PREFIX}chunk_embedding"
    assert index["state"] == "ONLINE"
    assert index["type"] == "VECTOR"
    assert index["dim"] == settings.embed_dim
    assert index["similarity"].lower() == "cosine"  # Neo4j answers COSINE


def test_index_meta_records_the_model_the_vectors_came_from(chunked, settings):
    meta = chunked["index_meta"]
    assert meta["model"] == settings.embed_model
    assert meta["dim"] == settings.embed_dim
    assert meta["similarity"] == "cosine"
    assert meta["chunk_count"] == chunked["census"]["chunks"]
    assert meta["created_at"] and meta["updated_at"]


def test_a_second_run_creates_nothing_and_embeds_nothing(chunked, scratch, embedder, tmp_path):
    report, code = _chunk(scratch, embedder, MINI, tmp_path)
    assert code == 0
    assert report["last_run"]["created"] == {"nodes": 0, "relationships": 0}
    assert report["embedding"]["requested"] == 0
    assert report["embedding"]["skipped_unchanged"] == report["chunking"]["chunks"]
    assert report["census"] == chunked["census"]
    assert [c["name"] for c in report["checks"] if not c["ok"]] == []
    # `created_at` survives the rerun; `updated_at` moves.
    assert report["index_meta"]["created_at"] == chunked["index_meta"]["created_at"]


def test_a_reworded_page_orphans_the_chunk_it_replaced_instead_of_deleting_it(
    chunked, scratch, embedder, tmp_path
):
    before = {
        r["id"]
        for r in scratch.read(
            f"MATCH (c:{scratch.label('Chunk')} {{parent_key: 'KIP-5'}}) RETURN c.id AS id"
        )
    }
    edited = tmp_path / "canonical"
    edited.mkdir()
    for path in MINI.glob("*.jsonl"):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        if path.name == "documents.jsonl":
            for row in rows:
                row["body_md"] += "\n\nAn extra paragraph nobody had written before today."
        edited.joinpath(path.name).write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )

    report, code = _chunk(scratch, embedder, edited, tmp_path)
    assert code == 0
    assert report["embedding"]["requested"] > 0  # the reworded page is re-embedded
    assert report["orphans"]["checked"] is True
    assert report["orphans"]["marked"] == len(before)
    assert report["census"]["orphaned"] == len(before)

    still_there = scratch.read(
        f"MATCH (c:{scratch.label('Chunk')}) WHERE c.id IN $ids "
        "RETURN count(c) AS c, sum(CASE WHEN c.orphaned THEN 1 ELSE 0 END) AS orphaned",
        ids=sorted(before),
    )
    assert still_there[0] == {"c": len(before), "orphaned": len(before)}


def test_a_partial_run_never_marks_orphans(scratch, embedder, tmp_path):
    report, _ = _chunk(scratch, embedder, MINI, tmp_path, kinds={"doc"})
    assert report["orphans"] == {
        "checked": False,
        "reason": "partial run (--limit or --kinds)",
        "marked": 0,
    }


# --------------------------------------------------------------------- cross-lingual


@pytest.fixture(scope="module")
def production(client):
    """The real, unprefixed graph — read only."""
    ctx = GraphContext(client, prefix="")
    rows = ctx.read("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c) AS c")
    if not rows or rows[0]["c"] == 0:
        pytest.skip("no embedded :Chunk nodes — run `uv run brain chunk` first")
    return ctx


def search(ctx, embedder, query: str, k: int = 5):
    return chunk_graph.query_similar(ctx, embedder.embed_one(query), k=k)


def test_a_hebrew_query_finds_english_rebalance_text(production, embedder):
    """bge-m3 is multilingual: the Hebrew for "consumer rebalance protocol" has to reach
    the English KIPs, or the whole bilingual premise of this POC is wrong."""
    hits = search(production, embedder, HEBREW_QUERY, k=5)
    assert len(hits) == 5
    matched = [h for h in hits if "rebalance" in h["text"].lower()]
    assert matched, [
        (h["parent_key"], h["heading"], round(h["score"], 3), h["text"][:120]) for h in hits
    ]


def kip_848_hops(ctx, parent_key: str) -> int | None:
    """How far the hit's record is from KIP-848 along *structural* edges, or None.

    "848-related" is decided by edges `brain load` wrote, never by a substring. Only
    `REFERENCES` / `IMPLEMENTS_KIP` (this record names the KIP) and one hop of
    `PARENT_OF` / `LINKS_TO` (its story or its linked issue does) count. Person-mediated
    paths are deliberately excluded: two records sharing a reporter says nothing about
    what they are about, and this assertion exists to be falsifiable.
    """
    if parent_key == "KIP-848":
        return 0
    rows = ctx.read(
        "MATCH (n) WHERE n.key = $key OR n.sha = $key "
        "MATCH p = (n)-[:PARENT_OF|LINKS_TO*0..1]-()-[:REFERENCES|IMPLEMENTS_KIP]->"
        "(:Document {key: 'KIP-848'}) "
        "RETURN min(length(p)) AS hops",
        key=parent_key,
    )
    return rows[0]["hops"] if rows and rows[0]["hops"] is not None else None


def test_the_english_query_lands_on_kip_848_or_something_that_names_it(production, embedder):
    """The Graph RAG claim in one assertion: the vector finds the right neighbourhood and
    the graph proves the connection.

    The top hit here is a *synthetic* ADO task ("Document the consumer group member state
    machine") that outscores KIP-848's own prose — the noise layer is doing its job. It is
    not a miss: its parent story references KIP-848, which the graph can show and a
    substring test could not.
    """
    hits = search(production, embedder, ENGLISH_QUERY, k=5)
    top = hits[0]
    assert "rebalance" in top["text"].lower(), top["text"][:200]
    hops = kip_848_hops(production, top["parent_key"])
    assert hops is not None and hops <= 2, (
        top["parent_key"],
        top["kind"],
        top["heading"],
        top["text"][:200],
    )
    # And KIP-848's own text is in the neighbourhood, not merely something linked to it.
    assert any(h["parent_key"] == "KIP-848" for h in hits), [h["parent_key"] for h in hits]


def test_the_production_index_is_online_and_matches_its_meta(production):
    index = chunk_graph.index_status(production)
    assert index["name"] == "chunk_embedding"
    assert index["state"] == "ONLINE"
    meta = chunk_graph.read_index_meta(production)
    assert meta is not None
    assert meta["dim"] == index["dim"]
    assert meta["model"]
