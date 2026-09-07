"""`brain index` against the real Neo4j.

Two halves, on purpose.

The **mechanism** half runs in its own label namespace (`_Idx…`) on the mini fixture, so it
can create, re-create and drop indexes without touching the graph a real `brain index`
built. It is the test of the two promises this step makes: every managed index reaches
ONLINE, and a second run creates nothing.

The **corpus** half is read-only against the production namespace, and asserts nothing
about the mechanism. If `brain index` has never run it skips with a message saying so — a
skip is honest, an assertion about an empty graph would not be.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.config import Settings
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import drop_schema
from brain.index import census as cen
from brain.index import schema as index_schema
from brain.index.runner import run_index

pytestmark = pytest.mark.live

MINI = Path("data/fixtures/mini")
PREFIX = "_Idx"


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
def scratch(client, settings, tmp_path_factory):
    """The mini fixture in its own label space, with the indexes this step manages."""
    ctx = GraphContext(client, prefix=PREFIX)
    reports = tmp_path_factory.mktemp("reports")
    wipe(ctx)
    run_load(
        client=client,
        canonical_dir=MINI,
        reports_dir=reports,
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    try:
        yield ctx, reports
    finally:
        index_schema.drop_indexes(ctx)
        wipe(ctx)
        drop_schema(ctx)


# ------------------------------------------------------------------------------- mechanism


def test_every_managed_index_reaches_online(scratch, settings):
    ctx, _reports = scratch
    index_schema.apply_indexes(ctx, settings.embed_dim)
    status = index_schema.index_status(ctx)
    assert index_schema.not_online(status) == {}
    assert len([r for r in status if r["managed"]]) == len(index_schema.MANAGED)


def test_a_second_run_creates_no_index(scratch, settings):
    ctx, _reports = scratch
    index_schema.apply_indexes(ctx, settings.embed_dim)
    again = index_schema.apply_indexes(ctx, settings.embed_dim)
    assert again["created"] == 0
    assert again["existing"] == len(index_schema.MANAGED)


def test_the_fulltext_indexes_actually_answer_a_query(scratch, settings):
    """A fulltext index that exists but indexes the wrong property is still 'ONLINE'."""
    ctx, _reports = scratch
    index_schema.apply_indexes(ctx, settings.embed_dim)
    rows = ctx.read(
        "CALL db.index.fulltext.queryNodes($name, $q) YIELD node, score "
        "RETURN node.key AS key ORDER BY score DESC LIMIT 5",
        name=f"{PREFIX}workitem_text",
        q="rebalance",
    )
    assert rows, "workitem_text returned nothing for a term the mini corpus contains"


def test_the_index_step_writes_no_data(scratch, settings, tmp_path):
    ctx, reports = scratch
    before = ctx.read(f"MATCH (n) WHERE {cen.node_filter(ctx, 'n')} RETURN count(n) AS c")[0]["c"]
    report, _code = run_index(
        client=ctx.client,
        embed_dim=settings.embed_dim,
        ollama_url=settings.ollama_url,
        embed_model=settings.embed_model,
        reports_dir=reports,
        canonical_dir=MINI,
        docs_dir=tmp_path,
        prefix=PREFIX,
        write_report=False,
        write_census=False,
        echo=lambda _m: None,
    )
    after = ctx.read(f"MATCH (n) WHERE {cen.node_filter(ctx, 'n')} RETURN count(n) AS c")[0]["c"]
    assert after == before
    counters = report["last_run"]["counters"]
    assert counters["nodes_created"] == 0
    assert counters["relationships_created"] == 0
    assert counters["properties_set"] == 0


def test_the_census_of_a_namespace_ignores_the_production_graph(scratch, settings, tmp_path):
    ctx, reports = scratch
    report, _code = run_index(
        client=ctx.client,
        embed_dim=settings.embed_dim,
        ollama_url=settings.ollama_url,
        embed_model=settings.embed_model,
        reports_dir=reports,
        canonical_dir=MINI,
        docs_dir=tmp_path,
        prefix=PREFIX,
        write_report=False,
        write_census=False,
        echo=lambda _m: None,
    )
    # the mini fixture, not the 58k-node corpus. The expected count is read from the
    # fixture rather than typed, so growing the fixture does not fail this test.
    expected = len(
        [
            line
            for line in (MINI / "workitems.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
    )
    assert report["nodes"]["structural"]["WorkItem"]["count"] == expected
    assert report["nodes"]["total"] < 200
    assert report["communities"]["present"] is False
    assert report["chunks"]["present"] is False


def test_the_run_writes_both_artefacts(scratch, settings, tmp_path):
    ctx, reports = scratch
    run_index(
        client=ctx.client,
        embed_dim=settings.embed_dim,
        ollama_url=settings.ollama_url,
        embed_model=settings.embed_model,
        reports_dir=reports,
        canonical_dir=MINI,
        docs_dir=tmp_path,
        prefix=PREFIX,
        echo=lambda _m: None,
    )
    report = json.loads((reports / "index.json").read_text(encoding="utf-8"))
    census_md = (tmp_path / "report" / "plan1-graph-census.md").read_text(encoding="utf-8")
    assert report["step"] == "index"
    assert f"{report['nodes']['total']:,}" in census_md


# ---------------------------------------------------------------------------- the corpus


@pytest.fixture(scope="module")
def production(client):
    ctx = GraphContext(client)
    present = set(cen.present_labels(ctx))
    if "Entity" not in present:
        pytest.skip("the pipeline has not run against this database — nothing to census")
    return ctx, present


def test_no_llm_edge_in_the_real_graph_is_missing_provenance(production):
    """Conventions rule 3, asserted against the graph rather than against a merge report."""
    ctx, present = production
    prov = cen.provenance_census(ctx, present)
    assert prov["edges"]["missing"] == 0, prov["edges"]["by_type"]
    assert prov["nodes"]["missing"] == 0, prov["nodes"]["by_label"]


def test_every_index_the_real_graph_needs_is_online(production):
    ctx, _present = production
    status = index_schema.index_status(ctx)
    assert index_schema.not_online(status) == {}


def test_the_vector_indexes_all_carry_the_configured_dimension(production, settings):
    ctx, _present = production
    for row in index_schema.index_status(ctx):
        if row.get("type") == "VECTOR" and row.get("present"):
            assert row["dim"] == settings.embed_dim, row["name"]


def test_the_index_meta_live_count_is_read_from_the_graph_not_the_node(production):
    """Decision 5: the stored chunk count includes orphans, the census recomputes it."""
    ctx, present = production
    meta = cen.index_meta_census(ctx, present, index_schema.index_status(ctx))
    chunk_row = next(r for r in meta["rows"] if r["index"] == "chunk_embedding")
    live = ctx.read("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c) AS c")[0]["c"]
    assert chunk_row["live_vectors"] == live
