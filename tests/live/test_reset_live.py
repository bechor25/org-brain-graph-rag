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
from brain.reset import census, run_reset, wipe_graph, wipe_slice, wipe_synthetic

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


# ------------------------------------------------- the incremental slice (Plan 3 Task 4)


INCREMENTAL_KEYS = ("KAFKA-102",)


@pytest.fixture
def sliced(ctx, canonical, loaded):
    """One work item of the mini corpus re-labelled as an increment, on disk and in the graph.

    `brain load` would write this from a canonical file whose record carries
    `slice: "incremental"`; doing it by hand here keeps the fixture the committed mini
    corpus (which is a *base* corpus and must stay one) while still exercising the real
    queries against a real database.
    """
    path = canonical / "workitems.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    for row in rows:
        if row["key"] in INCREMENTAL_KEYS:
            row["slice"] = "incremental"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    ctx.write(
        f"MATCH (w:{ctx.label('WorkItem')}) SET w.slice = "
        "CASE WHEN w.key IN $keys THEN 'incremental' ELSE 'base' END",
        keys=list(INCREMENTAL_KEYS),
    )
    for label in ("Chunk", "Entity"):
        ctx.write(f"MATCH (n:{ctx.label(label)}) DETACH DELETE n")
    # One chunk per work item, carrying its parent's slice — what `brain chunk` writes.
    for row in ctx.read(
        f"MATCH (w:{ctx.label('WorkItem')}) RETURN w.key AS key, w.slice AS slice ORDER BY key"
    ):
        ctx.write(
            f"MATCH (w:{ctx.label('WorkItem')} {{key: $key}})\n"
            f"MERGE (c:{ctx.label('Chunk')} {{id: $id}}) SET c.slice = $slice\n"
            "MERGE (w)-[:HAS_CHUNK]->(c)",
            key=row["key"],
            id=f"chunk-{row['key']}",
            slice=row["slice"],
        )
    # Two entities: one the increment invented, one it only touched.
    ctx.write(
        f"MERGE (e:{ctx.label('Entity')} {{id: 'Feature|new'}}) SET e.evidence_chunk_ids = $ids",
        ids=[f"chunk-{k}" for k in INCREMENTAL_KEYS],
    )
    ctx.write(
        f"MERGE (e:{ctx.label('Entity')} {{id: 'Feature|old'}}) SET e.evidence_chunk_ids = $ids",
        ids=["chunk-KAFKA-100", *(f"chunk-{k}" for k in INCREMENTAL_KEYS)],
    )
    try:
        yield {"keys": INCREMENTAL_KEYS, "canonical": canonical}
    finally:
        for label in ("Chunk", "Entity"):
            ctx.write(f"MATCH (n:{ctx.label(label)}) DETACH DELETE n")


def by_slice(ctx: GraphContext, label: str) -> dict[str, int]:
    rows = ctx.read(
        f"MATCH (n:{ctx.label(label)}) RETURN coalesce(n.slice, 'base') AS slice, count(n) AS c"
    )
    return {r["slice"]: r["c"] for r in rows}


def test_the_slice_dry_run_predicts_exactly_what_the_apply_deletes(ctx, sliced):
    predicted = wipe_slice(sliced["canonical"], "incremental", ctx=ctx, apply=False)
    before = by_slice(ctx, "WorkItem")

    applied = wipe_slice(sliced["canonical"], "incremental", ctx=ctx, apply=True)

    assert predicted["nodes_by_label"] == applied["nodes_by_label"]
    assert predicted["entities_only_in_slice"] == applied["entities_only_in_slice"] == 1
    assert by_slice(ctx, "WorkItem") == {"base": before["base"]}
    assert "incremental" not in by_slice(ctx, "Chunk")


def test_the_slice_reset_keeps_the_entity_the_increment_only_touched(ctx, sliced):
    wipe_slice(sliced["canonical"], "incremental", ctx=ctx, apply=True)
    rows = ctx.read(f"MATCH (e:{ctx.label('Entity')}) RETURN e.id AS id ORDER BY id")
    assert [r["id"] for r in rows] == ["Feature|old"]


def test_the_slice_reset_leaves_the_base_corpus_loadable(ctx, sliced, tmp_path_factory):
    """The point of the whole scope: run the increment, measure it, take it out, reload."""
    wipe_slice(sliced["canonical"], "incremental", ctx=ctx, apply=True)
    after = census(ctx)
    report, code = run_load(
        client=ctx.client,
        canonical_dir=sliced["canonical"],
        reports_dir=tmp_path_factory.mktemp("reports"),
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0, [c for c in report["checks"] if not c["ok"]]
    # A reload of the trimmed canonical files creates nothing new: the increment is gone
    # from the files too, so the graph it rebuilds is the one the reset left behind.
    assert census(ctx)["WorkItem"] == after["WorkItem"]
    assert by_slice(ctx, "WorkItem").get("incremental", 0) == 0


# ----------------------------------- the rollback criterion (Plan 3 Task 4, decision 7)

#: The increment's one new issue. It links to a base work item, names base people and a
#: base component, and carries a changelog — so it arrives with edges into the base corpus
#: and `StatusChange` nodes of its own, which is what makes "restores the census" a claim
#: about more than a single node.
ROLLBACK_KEY = "KAFKA-103"
QUIET = lambda _m: None  # noqa: E731


def edges_by_type(ctx: GraphContext) -> dict[str, int]:
    """Every edge with an endpoint in this namespace, counted by type.

    `census()` counts nodes only, and a slice reset that left an edge standing would pass
    a node-only comparison. Relationship types are not namespaced, so the label prefix is
    the only thing that keeps this fixture's edges apart from the real graph's.
    """
    rows = ctx.read(
        "MATCH (a)-[r]->(b) WHERE any(l IN labels(a) WHERE l STARTS WITH $p) "
        "OR any(l IN labels(b) WHERE l STARTS WITH $p) "
        "RETURN type(r) AS type, count(r) AS c ORDER BY type",
        p=PREFIX,
    )
    return {r["type"]: r["c"] for r in rows}


def full_census(ctx: GraphContext) -> dict[str, dict[str, int]]:
    """Nodes by label and edges by type — the two halves the acceptance line is about."""
    return {"nodes": census(ctx), "edges": edges_by_type(ctx)}


def chunk_every_workitem(ctx: GraphContext) -> None:
    """One chunk per work item, carrying its parent's slice — what `brain chunk` writes.

    Idempotent, so running it again after the increment loads adds the increment's chunk
    and leaves the base ones exactly as they were.
    """
    for row in ctx.read(
        f"MATCH (w:{ctx.label('WorkItem')}) RETURN w.key AS key, w.slice AS slice ORDER BY key"
    ):
        ctx.write(
            f"MATCH (w:{ctx.label('WorkItem')} {{key: $key}})\n"
            f"MERGE (c:{ctx.label('Chunk')} {{id: $id}}) SET c.slice = $slice\n"
            "MERGE (w)-[:HAS_CHUNK]->(c)",
            key=row["key"],
            id=f"chunk-{row['key']}",
            slice=row["slice"],
        )


#: The batch ids the two halves write. A sliced build puts the slice in front of every id
#: it plans, which is what makes "did the increment evidence this" a prefix test.
BASE_BATCH = "shard-01/001"
SLICE_BATCH = "incremental/shard-01/001"


def provenance(batches: list[str], chunk_ids: list[str]) -> dict[str, object]:
    """Exactly what `union_provenance` leaves on a node or an edge: sorted, first-seen."""
    return {
        "batch_ids": sorted(batches),
        "batch_id": sorted(batches)[0],
        "shard": sorted(batches)[0].split("/")[0],
        "evidence_chunk_ids": sorted(chunk_ids),
        "model": "opus:kg-extractor",
        "extracted_at": "2026-09-17T00:00:00+00:00",
    }


PROPS = ", ".join(
    f"x.`{p}` AS `{p}`"
    for p in ("batch_ids", "batch_id", "shard", "evidence_chunk_ids", "model", "extracted_at")
)


def put_entity(ctx: GraphContext, entity_id: str, batches: list[str], chunks: list[str]) -> None:
    ctx.write(
        f"MERGE (e:{ctx.label('Entity')} {{id: $id}}) SET e += $props",
        id=entity_id,
        props=provenance(batches, chunks),
    )


def put_edge(
    ctx: GraphContext, src: str, rel: str, dst: str, batches: list[str], chunks: list[str]
) -> None:
    """An LLM-derived edge between two `Entity` nodes — the shape the node sweep cannot see."""
    ctx.write(
        f"MATCH (a:{ctx.label('Entity')} {{id: $src}}), (b:{ctx.label('Entity')} {{id: $dst}})\n"
        f"MERGE (a)-[r:`{rel}`]->(b) SET r += $props",
        src=src,
        dst=dst,
        props=provenance(batches, chunks),
    )


def entity_props(ctx: GraphContext, entity_id: str) -> dict:
    rows = ctx.read(f"MATCH (x:{ctx.label('Entity')} {{id: $id}}) RETURN {PROPS}", id=entity_id)
    return dict(rows[0])


def edge_props(ctx: GraphContext, src: str, rel: str, dst: str) -> list[dict]:
    rows = ctx.read(
        f"MATCH (:{ctx.label('Entity')} {{id: $src}})-[x:`{rel}`]->"
        f"(:{ctx.label('Entity')} {{id: $dst}}) RETURN {PROPS}",
        src=src,
        dst=dst,
    )
    return [dict(r) for r in rows]


def append_incremental_record(canonical: Path) -> None:
    """Write the `--since` pull's new issue into the canonical file `brain load` reads."""
    path = canonical / "workitems.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    base = next(r for r in rows if r["key"] == "KAFKA-100")
    rows.append(
        {
            **base,
            "id": f"jira:{ROLLBACK_KEY}",
            "key": ROLLBACK_KEY,
            "title": "Follow-up found by the since-pull",
            "description": "Follow-up work the incremental pull brought in.",
            "slice": "incremental",
            "links": [{"type": "blocker", "target": "KAFKA-100", "direction": "out"}],
        }
    )
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def rollback(ctx, canonical, loaded, tmp_path_factory):
    """A base graph, measured, then an increment loaded on top of it.

    The base half is chunked and extracted first and measured *before* anything
    incremental exists, so the number the reset is compared against is a real earlier
    state of this graph rather than a recomputation after the fact.
    """
    slice_chunk = f"chunk-{ROLLBACK_KEY}"
    chunk_every_workitem(ctx)
    put_entity(ctx, "Feature|base", [BASE_BATCH], ["chunk-KAFKA-100"])
    put_entity(ctx, "Feature|touched", [BASE_BATCH], ["chunk-KAFKA-101"])
    # An edge the base corpus evidences, which the increment will go on to agree with.
    put_edge(
        ctx, "Feature|touched", "MOTIVATED_BY", "Feature|base", [BASE_BATCH], ["chunk-KAFKA-101"]
    )
    pre_slice = {
        "base": entity_props(ctx, "Feature|base"),
        "touched": entity_props(ctx, "Feature|touched"),
        "motivated_by": edge_props(ctx, "Feature|touched", "MOTIVATED_BY", "Feature|base"),
    }
    before = full_census(ctx)

    append_incremental_record(canonical)
    report, code = run_load(
        client=ctx.client,
        canonical_dir=canonical,
        reports_dir=tmp_path_factory.mktemp("reports"),
        prefix=PREFIX,
        write_report=False,
        echo=QUIET,
    )
    assert code == 0, [c for c in report["checks"] if not c["ok"]]
    chunk_every_workitem(ctx)
    put_entity(ctx, "Feature|new", [SLICE_BATCH], [slice_chunk])
    # the increment also cites an entity the base corpus already had: it must survive, and
    # its arrays are what `union_provenance` leaves behind
    put_entity(ctx, "Feature|touched", [BASE_BATCH, SLICE_BATCH], ["chunk-KAFKA-101", slice_chunk])
    put_edge(
        ctx,
        "Feature|touched",
        "MOTIVATED_BY",
        "Feature|base",
        [BASE_BATCH, SLICE_BATCH],
        ["chunk-KAFKA-101", slice_chunk],
    )
    # The gap the review found: an edge between two *base* entities that only the increment
    # evidences. No endpoint is the slice's, so no `DETACH DELETE` reaches it.
    put_edge(ctx, "Feature|base", "DEPENDS_ON", "Feature|touched", [SLICE_BATCH], [slice_chunk])
    try:
        yield {"canonical": canonical, "before": before, "pre_slice": pre_slice}
    finally:
        for label in ("Chunk", "Entity"):
            ctx.write(f"MATCH (n:{ctx.label(label)}) DETACH DELETE n")


def reset_slice(ctx, canonical: Path, tmp_path: Path) -> tuple[dict, int]:
    """`brain reset --slice incremental --yes`, one layer under the CLI.

    The CLI builds a `GraphContext` with no prefix — the real graph — so the command
    itself cannot be pointed at a scratch namespace. Everything below `typer` is what
    this calls, with `confirmed=True` being exactly what `--yes` sets.
    """
    return run_reset(
        data_dir=tmp_path / "data",
        canonical_dir=canonical,
        batches_dir=tmp_path / "batches",
        reports_dir=tmp_path / "reports",
        ctx=ctx,
        slice_="incremental",
        confirmed=True,
        write_report=False,
        echo=QUIET,
    )


def test_the_increment_moves_both_halves_of_the_census(ctx, rollback):
    """The guard that keeps the next test honest: a reset that restored nothing would also
    pass if the increment had never changed the graph."""
    now = full_census(ctx)
    before = rollback["before"]

    assert now["nodes"]["WorkItem"] == before["nodes"]["WorkItem"] + 1
    assert now["nodes"]["StatusChange"] > before["nodes"]["StatusChange"]
    assert now["nodes"]["Chunk"] == before["nodes"]["Chunk"] + 1
    assert now["nodes"]["Entity"] == before["nodes"]["Entity"] + 1
    assert any(now["edges"][t] > before["edges"].get(t, 0) for t in now["edges"])
    # the edge between two base entities that only the increment evidences
    assert now["edges"]["DEPENDS_ON"] == 1 and "DEPENDS_ON" not in before["edges"]


def test_a_slice_reset_restores_the_exact_base_census(ctx, rollback, tmp_path):
    """The acceptance line of step 16, proven here instead of on the real graph.

    Nodes by label *and* edges by type: the increment arrives with edges into the base
    corpus (`IN_COMPONENT` to a base component, a link to a base work item, `REPORTED_BY`
    to a base person), and those are the ones a sweep that only matched on `n.slice` would
    leave dangling.
    """
    before = rollback["before"]

    report, code = reset_slice(ctx, rollback["canonical"], tmp_path)

    assert code == 0 and report["applied"] is True
    assert full_census(ctx) == before


def test_the_slice_reset_names_what_it_deleted_and_keeps_the_touched_entity(
    ctx, rollback, tmp_path
):
    """Restoring the census is not enough on its own — deleting the base corpus and
    reloading it would do that too. This is the other half: it deleted the increment."""
    report, _code = reset_slice(ctx, rollback["canonical"], tmp_path)
    manifest = report["slice"]

    assert manifest["nodes_by_label"]["WorkItem"] == 1
    assert manifest["nodes_by_label"]["Chunk"] == 1
    assert manifest["entities_only_in_slice"] == 1
    assert manifest["entity_ids"] == ["Feature|new"]
    assert manifest["records"]["workitems"] == 1
    ids = [
        r["id"] for r in ctx.read(f"MATCH (e:{ctx.label('Entity')}) RETURN e.id AS id ORDER BY id")
    ]
    assert ids == ["Feature|base", "Feature|touched"]
    assert not ctx.read(
        f"MATCH (w:{ctx.label('WorkItem')} {{key: $key}}) RETURN w", key=ROLLBACK_KEY
    )


def test_an_edge_only_the_increment_evidenced_goes_even_between_two_base_entities(
    ctx, rollback, tmp_path
):
    """The gap the review found. `DEPENDS_ON` runs between two base entities, so the label
    sweep never touches either end — but its only `batch_ids` entry is the slice's, and
    after the reset the batch and the chunk it cites are both gone. An edge whose whole
    evidence has been deleted is not a fact the graph may keep."""
    reset_slice(ctx, rollback["canonical"], tmp_path)

    assert edge_props(ctx, "Feature|base", "DEPENDS_ON", "Feature|touched") == []


def test_an_edge_the_base_corpus_also_evidenced_keeps_only_its_base_provenance(
    ctx, rollback, tmp_path
):
    """The other half of the rule: `MOTIVATED_BY` was there before the increment and the
    increment agreed with it, so it stays — with exactly the arrays it had before, which is
    the inverse of the union `brain extract merge --slice` applied on the way in."""
    pre = rollback["pre_slice"]
    mixed = edge_props(ctx, "Feature|touched", "MOTIVATED_BY", "Feature|base")
    assert mixed[0]["batch_ids"] == [SLICE_BATCH, BASE_BATCH]  # the increment is on it now

    reset_slice(ctx, rollback["canonical"], tmp_path)

    assert edge_props(ctx, "Feature|touched", "MOTIVATED_BY", "Feature|base") == pre["motivated_by"]


def test_the_entities_the_increment_touched_get_their_pre_slice_arrays_back(
    ctx, rollback, tmp_path
):
    """`Feature|touched` survives either way — it always had base evidence. What it must not
    keep is a `batch_ids` naming a batch the reset removed and an `evidence_chunk_ids`
    naming a chunk that no longer exists."""
    pre = rollback["pre_slice"]

    report, _code = reset_slice(ctx, rollback["canonical"], tmp_path)

    assert entity_props(ctx, "Feature|touched") == pre["touched"]
    assert entity_props(ctx, "Feature|base") == pre["base"]  # never touched by the increment
    assert report["slice"]["provenance"] == {
        **report["slice"]["provenance"],
        "edges_deleted": 1,
        "edges_stripped": 1,
        "entities_stripped": 1,
    }


def test_the_slice_dry_run_predicts_the_provenance_sweep_it_would_apply(ctx, rollback, tmp_path):
    """The survivor clause is shared by both modes so these two numbers cannot drift:
    predicting, the doomed nodes are still there and it excludes them; applying, they are
    already gone and it excludes nothing."""
    predicted = wipe_slice(rollback["canonical"], "incremental", ctx=ctx, apply=False)

    applied = wipe_slice(rollback["canonical"], "incremental", ctx=ctx, apply=True)

    assert predicted["provenance"] == applied["provenance"]
    assert predicted["nodes_by_label"] == applied["nodes_by_label"]
