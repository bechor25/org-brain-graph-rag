"""`brain reset` against the real Neo4j — in the `_ResetTest` label namespace only.

The offline tests prove the logic; this proves it against a database that really enforces
constraints, really deletes edges with `DETACH DELETE`, and really keeps an index after
the nodes behind it are gone. It runs in its own label space, so the graph a previous
`brain load` built is neither read nor deleted.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from brain.chunk.synthetic import stamp_synthetic
from brain.config import Settings
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import UNIQUE_KEYS, drop_schema
from brain.reset import census, wipe_graph, wipe_synthetic

pytestmark = pytest.mark.live

MINI = Path("data/fixtures/mini")
PREFIX = "_ResetTest"


@pytest.fixture
def ctx():
    s = Settings()
    client = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    client.verify()
    c = GraphContext(client, prefix=PREFIX)
    wipe(c)
    drop_schema(c)
    try:
        yield c
    finally:
        wipe(c)
        drop_schema(c)
        client.close()


@pytest.fixture
def canonical(tmp_path) -> Path:
    """The mini corpus, copied so a reset can eat it without touching `data/fixtures`."""
    target = tmp_path / "canonical"
    shutil.copytree(MINI, target)
    return target


@pytest.fixture
def loaded(ctx, canonical, tmp_path_factory):
    report, code = run_load(
        client=ctx.client,
        canonical_dir=canonical,
        reports_dir=tmp_path_factory.mktemp("reports"),
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0, [c for c in report["checks"] if not c["ok"]]
    return report


def index_names(ctx: GraphContext) -> set[str]:
    rows = ctx.read("SHOW INDEXES YIELD name RETURN name")
    return {r["name"] for r in rows if r["name"].startswith(PREFIX)}


def constraint_names(ctx: GraphContext) -> set[str]:
    rows = ctx.read("SHOW CONSTRAINTS YIELD name RETURN name")
    return {r["name"] for r in rows if r["name"].startswith(PREFIX)}


def test_graph_reset_leaves_zero_nodes_and_every_constraint(ctx, loaded):
    before = census(ctx)
    assert sum(before.values()) > 0, "nothing was loaded, so nothing is being proven"
    constraints_before = constraint_names(ctx)
    indexes_before = index_names(ctx)
    assert len(constraints_before) == len(UNIQUE_KEYS)

    report = wipe_graph(ctx)

    assert report["nodes"] == sum(before.values())
    assert all(count == 0 for count in census(ctx).values())
    assert ctx.read("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"] >= 0
    # The schema is what a reload needs and cannot rebuild concurrently: it stays.
    assert constraint_names(ctx) == constraints_before
    assert index_names(ctx) == indexes_before


def test_a_reload_after_a_reset_reproduces_the_same_census(
    ctx, canonical, loaded, tmp_path_factory
):
    before = census(ctx)
    wipe_graph(ctx)
    report, code = run_load(
        client=ctx.client,
        canonical_dir=canonical,
        reports_dir=tmp_path_factory.mktemp("reports"),
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0
    assert census(ctx) == before


def test_synthetic_reset_removes_exactly_the_synthetic_nodes(ctx, canonical, loaded):
    synthetic_before = ctx.read(
        f"MATCH (n:{ctx.label('WorkItem')}) WHERE n.synthetic = true RETURN count(n) AS c"
    )[0]["c"]
    real_before = ctx.read(
        f"MATCH (n:{ctx.label('WorkItem')}) WHERE n.synthetic = false RETURN count(n) AS c"
    )[0]["c"]
    assert synthetic_before == 4 and real_before == 3, "the mini fixture changed"

    with (canonical / "workitems.jsonl").open(encoding="utf-8") as handle:
        records = sum(1 for line in handle if line.strip() and json.loads(line).get("synthetic"))
    assert records == synthetic_before

    report = wipe_synthetic(canonical, ctx=ctx, batches_dir=None, apply=True)

    assert report["records"]["workitems"] == 4
    assert report["nodes_by_label"].get("WorkItem") == 4
    after = ctx.read(
        f"MATCH (n:{ctx.label('WorkItem')}) RETURN count(n) AS c, "
        "count(CASE WHEN n.synthetic = true THEN 1 END) AS synthetic"
    )[0]
    assert after == {"c": real_before, "synthetic": 0}
    # and the real corpus is still the real corpus
    assert constraint_names(ctx)


def test_a_shared_container_survives_the_synthetic_reset(ctx, canonical, loaded):
    """`Component {name:"clients"}` is one node Jira and the ADO layer both merge on."""
    ctx.write(f"MATCH (n:{ctx.label('Component')} {{name: 'clients'}}) SET n.synthetic = true")
    wipe_synthetic(canonical, ctx=ctx, batches_dir=None, apply=True)
    rows = ctx.read(
        f"MATCH (n:{ctx.label('Component')} {{name: 'clients'}}) RETURN n.synthetic AS synthetic"
    )
    assert rows == [{"synthetic": False}], "a component a real record claims must survive"


# ------------------------------------------------------- the synthetic backfill (11b)


@pytest.fixture
def chunked(ctx, loaded):
    """Chunks with a null `synthetic`, hung off the mini corpus's real and synthetic items.

    This is the state the live graph was in: `brain chunk` wrote these nodes before the
    property existed, so no property-based sweep could find them and `brain reset
    --synthetic` fell back to "the parent is gone".

    Cleaned up here rather than by the `ctx` fixture, which calls `brain.graph.runner.wipe`
    — that only knows `PRIMARY_LABELS`, so a `Chunk` left behind would arrive at the next
    test already stamped and quietly prove nothing.
    """
    for label in ("Chunk", "Entity"):
        ctx.write(f"MATCH (n:{ctx.label(label)}) DETACH DELETE n")
    rows = ctx.read(
        f"MATCH (w:{ctx.label('WorkItem')}) RETURN w.key AS key, w.synthetic AS synthetic "
        "ORDER BY key"
    )
    for i, row in enumerate(rows):
        ctx.write(
            f"MATCH (w:{ctx.label('WorkItem')} {{key: $key}})\n"
            f"MERGE (c:{ctx.label('Chunk')} {{id: $id}})\n"
            "MERGE (w)-[:HAS_CHUNK]->(c)",
            key=row["key"],
            id=f"chunk-{i}",
        )
    # One entity per side, with the flag an extract merge against null chunks would write.
    ctx.write(
        f"MERGE (e:{ctx.label('Entity')} {{id: 'Feature|real'}}) "
        "SET e.synthetic = false, e.evidence_chunk_ids = $ids",
        ids=[f"chunk-{i}" for i, r in enumerate(rows) if not r["synthetic"]][:1],
    )
    ctx.write(
        f"MERGE (e:{ctx.label('Entity')} {{id: 'Feature|syn'}}) "
        "SET e.synthetic = false, e.evidence_chunk_ids = $ids",
        ids=[f"chunk-{i}" for i, r in enumerate(rows) if r["synthetic"]][:1],
    )
    try:
        yield {"chunks": len(rows), "synthetic": sum(1 for r in rows if r["synthetic"])}
    finally:
        for label in ("Chunk", "Entity"):
            ctx.write(f"MATCH (n:{ctx.label(label)}) DETACH DELETE n")


def nulls(ctx: GraphContext, label: str) -> int:
    return ctx.read(f"MATCH (n:{ctx.label(label)}) WHERE n.synthetic IS NULL RETURN count(n) AS c")[
        0
    ]["c"]


def test_the_backfill_leaves_no_chunk_with_a_null_flag_and_is_idempotent(ctx, chunked):
    assert nulls(ctx, "Chunk") == chunked["chunks"], "the fixture is not reproducing the bug"

    first = stamp_synthetic(ctx, echo=lambda _m: None)

    assert nulls(ctx, "Chunk") == 0
    assert first["stamped"]["chunks"] == chunked["chunks"]
    assert first["after"]["chunks"]["synthetic_after"] == chunked["synthetic"]
    # An entity whose only evidence is a synthetic chunk changes sides; the real one does not.
    assert first["after"]["entities"]["synthetic_after"] == 1
    assert (
        ctx.read(f"MATCH (e:{ctx.label('Entity')} {{id: 'Feature|syn'}}) RETURN e.synthetic AS s")[
            0
        ]["s"]
        is True
    )

    again = stamp_synthetic(ctx, echo=lambda _m: None)
    assert again["stamped"] == {"chunks": 0, "entities": 0}


def test_after_the_backfill_the_synthetic_reset_predicts_zero_orphans(ctx, canonical, chunked):
    """The blocker, end to end: the chunks go by their own flag, not as unattributed orphans."""
    before = wipe_synthetic(canonical, ctx=ctx, batches_dir=None, apply=False)
    assert before["chunks_orphaned_by_parent"] == chunked["synthetic"]
    assert "Chunk" not in before["nodes_by_label"]

    stamp_synthetic(ctx, echo=lambda _m: None)

    after = wipe_synthetic(canonical, ctx=ctx, batches_dir=None, apply=False)
    assert after["chunks_orphaned_by_parent"] == 0
    assert after["nodes_by_label"]["Chunk"] == chunked["synthetic"]


def test_the_stamped_reset_deletes_exactly_the_synthetic_chunks(ctx, canonical, chunked):
    stamp_synthetic(ctx, echo=lambda _m: None)
    report = wipe_synthetic(canonical, ctx=ctx, batches_dir=None, apply=True)

    assert report["nodes_by_label"]["Chunk"] == chunked["synthetic"]
    assert report["chunks_orphaned_by_parent"] == 0
    remaining = ctx.read(f"MATCH (c:{ctx.label('Chunk')}) RETURN count(c) AS c")[0]["c"]
    assert remaining == chunked["chunks"] - chunked["synthetic"]
    assert nulls(ctx, "Chunk") == 0


def test_the_report_s_backfill_check_measures_a_real_transition_and_cleans_up(ctx):
    """`data/reports/modularity.json` carries a stamp run, not a claim that one happened.

    The live graph is settled, so the numbers there are all zero and prove nothing about
    the transition. This puts a graph into the pre-backfill state, runs the real
    `stamp_synthetic`, and reports what moved — inside `_ModCheck`, never a real label.
    """
    from brain.modularity import MECHANISM_PREFIX, stamp_mechanism_check

    section = stamp_mechanism_check(ctx)

    assert section["prefix"] == MECHANISM_PREFIX
    assert section["before"]["chunks"] == {"null": 3, "true": 0}
    assert section["after"]["chunks"] == {"null": 0, "true": 2}
    assert section["stamped"] == {"chunks": 3, "entities": 1}
    assert section["rerun_stamped"] == {"chunks": 0, "entities": 0}
    assert section["after"]["entities"]["true"] == 1

    scratch = GraphContext(ctx.client, prefix=MECHANISM_PREFIX)
    for label in ("WorkItem", "Chunk", "Entity"):
        left = scratch.read(f"MATCH (n:{scratch.label(label)}) RETURN count(n) AS c")[0]["c"]
        assert left == 0, f"{label} nodes left behind in {MECHANISM_PREFIX}"
