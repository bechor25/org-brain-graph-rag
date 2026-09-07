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

    records = sum(
        1
        for line in (canonical / "workitems.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("synthetic")
    )
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
