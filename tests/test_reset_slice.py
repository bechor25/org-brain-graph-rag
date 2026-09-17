"""`brain reset --slice incremental` — undo one `--since` pull, leave the corpus standing.

Offline: a tmp `data/` and the in-memory graph of `tests/reset_helpers.py`. The live
counterpart runs in the `_ResetTest` label namespace (`tests/live/test_reset_live.py`);
nothing here touches the real database.
"""

from __future__ import annotations

import json

import pytest

from brain.cli import app
from brain.reset import (
    ResetError,
    entities_only_in_slice,
    format_manifest,
    in_slice,
    run_reset,
    strip_slice_records,
    wipe_slice,
)
from tests.reset_helpers import FakeGraph, ctx

BASE_ITEM = {"id": "jira:KAFKA-1", "key": "KAFKA-1", "source": "jira"}
NEW_ITEM = {"id": "jira:KAFKA-9001", "key": "KAFKA-9001", "source": "jira", "slice": "incremental"}


@pytest.fixture
def data_dir(tmp_path):
    """Two base work items, one incremental; one shared person, one new one."""
    root = tmp_path / "data"
    canonical = root / "canonical"
    canonical.mkdir(parents=True)
    for name in ("raw", "batches", "reports", "eval"):
        (root / name).mkdir(parents=True)

    def write(name: str, rows: list[dict]) -> None:
        (canonical / f"{name}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
        )

    write(
        "workitems",
        [BASE_ITEM, {"id": "jira:KAFKA-2", "key": "KAFKA-2", "source": "jira"}, NEW_ITEM],
    )
    write(
        "persons",
        [
            # named by a base issue and by the increment: canon already wrote it `base`
            {"id": "jira:mjsax", "identities": [{"source": "jira", "key": "mjsax"}]},
            {
                "id": "jira:newbie",
                "identities": [{"source": "jira", "key": "newbie"}],
                "slice": "incremental",
            },
        ],
    )
    write(
        "containers",
        [
            {
                "id": "jira:component:clients",
                "source": "jira",
                "kind": "component",
                "name": "clients",
            },
            {
                "id": "jira:component:kraft",
                "source": "jira",
                "kind": "component",
                "name": "kraft",
                "slice": "incremental",
            },
        ],
    )
    write("documents", [])
    write("changes", [])
    (canonical / "resolution_ledger.json").write_text(
        json.dumps(
            {
                "persons": {
                    "jira:newbie": {"canonical": "jira:mjsax", "tier": 1},
                    "confluence:mjsax": {"canonical": "jira:mjsax", "tier": 1},
                },
                "entities": {},
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def graph() -> FakeGraph:
    """Base nodes, incremental nodes, and the two entity cases the sweep has to tell apart."""
    return FakeGraph(
        nodes=[
            ("WorkItem", "KAFKA-1", {"slice": "base"}),
            ("WorkItem", "KAFKA-2", {"slice": "base"}),
            ("WorkItem", "KAFKA-9001", {"slice": "incremental"}),
            ("Person", "jira:mjsax", {"slice": "base"}),
            ("Person", "jira:newbie", {"slice": "incremental"}),
            ("Component", "clients", {"slice": "base"}),
            ("Component", "kraft", {"slice": "incremental"}),
            ("Chunk", "c1", {"slice": "base"}),
            ("Chunk", "c9", {"slice": "incremental"}),
            # A chunk of the incremental item written before `Chunk.slice` existed: only
            # the vanished parent can find it, which is what the orphan fallback is for.
            ("Chunk", "c10", {}),
            # invented by the increment — every evidence chunk is in the slice
            (
                "Entity",
                "Feature|kraft quorum",
                {"id": "Feature|kraft quorum", "evidence_chunk_ids": ["c9"]},
            ),
            # touched by the increment but older than it: one base chunk keeps it alive
            (
                "Entity",
                "Feature|rebalance",
                {"id": "Feature|rebalance", "evidence_chunk_ids": ["c1", "c9"]},
            ),
        ],
        edges=[
            (0, 7, "HAS_CHUNK"),
            (2, 8, "HAS_CHUNK"),
            (2, 9, "HAS_CHUNK"),
        ],
    )


def apply(data_dir, graph, *, confirmed: bool):
    return run_reset(
        data_dir=data_dir,
        canonical_dir=data_dir / "canonical",
        batches_dir=data_dir / "batches",
        reports_dir=data_dir / "reports",
        ctx=ctx(graph),
        slice_="incremental",
        confirmed=confirmed,
        echo=lambda _m: None,
    )


# --------------------------------------------------------------------------- selection


def test_a_line_with_no_slice_field_is_a_base_record():
    """Every line written before the field existed. Getting this backwards eats the corpus."""
    assert in_slice({"id": "x"}, "incremental") is False
    assert in_slice({"id": "x", "slice": "base"}, "incremental") is False
    assert in_slice({"id": "x", "slice": "incremental"}, "incremental") is True


def test_strip_removes_only_the_incremental_lines(data_dir):
    removed = strip_slice_records(data_dir / "canonical", "incremental", apply=True)
    assert removed == {
        "workitems": 1,
        "documents": 0,
        "persons": 1,
        "changes": 0,
        "containers": 1,
    }
    rows = [
        json.loads(line)
        for line in (data_dir / "canonical" / "workitems.jsonl").read_text().splitlines()
    ]
    assert [r["key"] for r in rows] == ["KAFKA-1", "KAFKA-2"]


def test_a_dry_run_counts_but_rewrites_nothing(data_dir):
    before = (data_dir / "canonical" / "workitems.jsonl").read_bytes()
    assert strip_slice_records(data_dir / "canonical", "incremental", apply=False)["workitems"] == 1
    assert (data_dir / "canonical" / "workitems.jsonl").read_bytes() == before


def test_an_entity_is_only_in_the_slice_when_every_surviving_chunk_is(graph):
    assert entities_only_in_slice(ctx(graph), "incremental") == ["Feature|kraft quorum"]


# --------------------------------------------------------------------------- the sweep


def test_the_dry_run_manifest_counts_what_it_would_delete(data_dir, graph):
    report, code = apply(data_dir, graph, confirmed=False)

    assert code == 1
    section = report["slice"]
    assert section["slice"] == "incremental"
    assert section["records"]["workitems"] == 1
    assert section["nodes_by_label"] == {
        "WorkItem": 1,
        "Person": 1,
        "Component": 1,
        "Chunk": 1,
    }
    assert section["entities_only_in_slice"] == 1
    # c10: the unflagged chunk whose only parent is about to go
    assert section["chunks_orphaned_by_parent"] == 1
    assert graph.labels()["WorkItem"] == 3, "a dry run deleted a node"


def test_applying_removes_the_increment_and_keeps_the_base_corpus(data_dir, graph):
    report, code = apply(data_dir, graph, confirmed=True)

    assert code == 0
    labels = graph.labels()
    assert labels["WorkItem"] == 2
    assert labels["Person"] == 1  # jira:mjsax stayed
    assert labels["Component"] == 1  # clients stayed
    assert "Chunk" not in labels or labels["Chunk"] == 1  # only c1 survives
    assert report["slice"]["counters"]["nodes_deleted"] >= 5


def test_the_entity_the_increment_invented_goes_and_the_one_it_touched_stays(data_dir, graph):
    apply(data_dir, graph, confirmed=True)
    entities = {n["key"] for n in graph._live() if n["label"] == "Entity"}
    assert entities == {"Feature|rebalance"}


def test_a_person_a_base_record_also_claims_is_never_deleted(data_dir, graph):
    """No protected-names pass here: canon already wrote the shared identity as `base`."""
    apply(data_dir, graph, confirmed=True)
    assert {n["key"] for n in graph._live() if n["label"] == "Person"} == {"jira:mjsax"}
    rows = [
        json.loads(line)
        for line in (data_dir / "canonical" / "persons.jsonl").read_text().splitlines()
    ]
    assert [r["id"] for r in rows] == ["jira:mjsax"]


def test_the_ledger_loses_only_the_rows_naming_a_deleted_identity(data_dir, graph):
    report, _ = apply(data_dir, graph, confirmed=True)
    ledger = json.loads((data_dir / "canonical" / "resolution_ledger.json").read_text())
    assert report["slice"]["ledger_rows_dropped"] == 1
    assert list(ledger["persons"]) == ["confluence:mjsax"]


def test_a_second_reset_finds_nothing_left(data_dir, graph):
    apply(data_dir, graph, confirmed=True)
    report, _ = apply(data_dir, graph, confirmed=True)
    assert report["slice"]["nodes"] == 0
    assert sum(report["slice"]["records"].values()) == 0


def test_the_graph_is_untouched_when_no_graph_is_given(data_dir):
    out = wipe_slice(data_dir / "canonical", "incremental", ctx=None, apply=True)
    assert "nodes_by_label" not in out
    assert out["records"]["workitems"] == 1


# --------------------------------------------------------------------------- the manifest


def test_the_manifest_says_what_it_is_keeping(data_dir, graph):
    report, _ = apply(data_dir, graph, confirmed=False)
    from brain.reset import Manifest

    text = format_manifest(Manifest(scopes=["slice:incremental"], slice=report["slice"]))
    assert "since-*" in text
    assert "Community nodes are kept" in text
    assert "would delete" in text


# --------------------------------------------------------------------------- refusals


def test_an_unknown_slice_is_refused_before_anything_is_read(data_dir, graph):
    with pytest.raises(ResetError, match="unknown slice"):
        run_reset(
            data_dir=data_dir,
            canonical_dir=data_dir / "canonical",
            batches_dir=data_dir / "batches",
            reports_dir=data_dir / "reports",
            ctx=ctx(graph),
            slice_="base",
            confirmed=True,
            echo=lambda _m: None,
        )


def test_the_slice_scope_needs_a_graph_connection(data_dir):
    with pytest.raises(ResetError, match="need a graph connection"):
        run_reset(
            data_dir=data_dir,
            canonical_dir=data_dir / "canonical",
            batches_dir=data_dir / "batches",
            reports_dir=data_dir / "reports",
            ctx=None,
            slice_="incremental",
            confirmed=True,
            echo=lambda _m: None,
        )


def test_the_cli_refuses_an_unknown_slice_without_opening_a_connection(runner, tmp_path):
    result = runner.invoke(app, ["reset", "--slice", "base", "--yes"])
    assert result.exit_code == 2
    assert "unknown slice" in result.output


def test_the_cli_takes_slice_as_a_scope_of_its_own(runner):
    """`--slice` alone is a scope: it must not demand --graph/--data as well."""
    result = runner.invoke(app, ["reset"])
    assert result.exit_code == 2
    assert "--slice" in result.output


# ------------------------------------------- the provenance the slice leaves behind (16)

BASE_BATCH = "shard-01/001"
SLICE_BATCH = "incremental/shard-01/001"


def prov(batches: list[str], chunks: list[str], **kw) -> dict:
    """What `union_provenance` leaves: sorted lists, first-seen `batch_id`/`shard`."""
    first = kw.pop("batch_id", sorted(batches)[0])
    return {
        "batch_ids": sorted(batches),
        "batch_id": first,
        "shard": first.split("/")[0],
        "evidence_chunk_ids": sorted(chunks),
        **kw,
    }


@pytest.fixture
def provenance_graph() -> FakeGraph:
    """Two base entities and the three provenance cases the sweep has to tell apart.

    The point of the fixture is that **no node here is in the slice**. Everything the node
    sweep looks at survives; the only thing naming the increment is what the extraction
    wrote onto the edges and the entities.
    """
    return FakeGraph(
        nodes=[
            ("Chunk", "c1", {"slice": "base"}),
            ("Chunk", "c9", {"slice": "incremental"}),
            (
                "Entity",
                "Feature|classic",
                {"id": "Feature|classic", **prov([BASE_BATCH], ["c1"])},
            ),
            # the increment agreed with an entity the base corpus already had, and its
            # `batch_id` happens to name the slice — first-seen has to be re-derived
            (
                "Entity",
                "Feature|rebalance",
                {
                    "id": "Feature|rebalance",
                    **prov([BASE_BATCH, SLICE_BATCH], ["c1", "c9"], batch_id=SLICE_BATCH),
                },
            ),
        ],
        edges=[
            # only the increment evidences it, and neither end is the slice's
            (2, 3, "DEPENDS_ON", prov([SLICE_BATCH], ["c9"])),
            # the base corpus evidences it too, so it stays and loses the slice's half
            (3, 2, "MOTIVATED_BY", prov([BASE_BATCH, SLICE_BATCH], ["c1", "c9"])),
            # not this step's edge type: provenance or not, the sweep does not own it
            (2, 3, "SAME_AS", prov([SLICE_BATCH], ["c9"])),
        ],
    )


def test_the_dry_run_counts_the_provenance_it_would_take_back(data_dir, provenance_graph):
    report = wipe_slice(
        data_dir / "canonical", "incremental", ctx=ctx(provenance_graph), apply=False
    )

    assert report["provenance"] == {
        **report["provenance"],
        "edges_deleted": 1,
        "edges_stripped": 1,
        "entities_stripped": 1,
        "slice_chunks": 1,
    }
    assert [e["type"] for e in provenance_graph.edges] == [
        "DEPENDS_ON",
        "MOTIVATED_BY",
        "SAME_AS",
    ]


def test_an_edge_only_the_slice_evidenced_is_deleted_between_two_surviving_nodes(
    data_dir, provenance_graph
):
    """The gap: no `DETACH DELETE` reaches this edge, because neither end is the slice's."""
    wipe_slice(data_dir / "canonical", "incremental", ctx=ctx(provenance_graph), apply=True)

    assert [e["type"] for e in provenance_graph.edges] == ["MOTIVATED_BY", "SAME_AS"]


def test_a_mixed_edge_keeps_its_base_batches_and_re_derives_first_seen(data_dir, provenance_graph):
    wipe_slice(data_dir / "canonical", "incremental", ctx=ctx(provenance_graph), apply=True)
    kept = next(e for e in provenance_graph.edges if e["type"] == "MOTIVATED_BY")

    assert kept["props"] == prov([BASE_BATCH], ["c1"])


def test_a_mixed_entity_loses_the_slice_from_both_arrays(data_dir, provenance_graph):
    """And `batch_id` stops naming a batch the reset just removed: it was the slice's."""
    wipe_slice(data_dir / "canonical", "incremental", ctx=ctx(provenance_graph), apply=True)

    assert provenance_graph.nodes[3]["props"] == {
        "id": "Feature|rebalance",
        **prov([BASE_BATCH], ["c1"]),
    }
    assert provenance_graph.nodes[2]["props"] == {
        "id": "Feature|classic",
        **prov([BASE_BATCH], ["c1"]),
    }


def test_an_edge_this_step_did_not_write_is_not_the_slice_sweep_s_business(
    data_dir, provenance_graph
):
    """`SAME_AS` carries provenance too and is `brain resolve`'s, not extraction's. The
    sweep is scoped to the closed type set for the same reason every other audit here is:
    a rule that reaches outside what the step wrote is a rule nobody can review."""
    wipe_slice(data_dir / "canonical", "incremental", ctx=ctx(provenance_graph), apply=True)
    same_as = next(e for e in provenance_graph.edges if e["type"] == "SAME_AS")

    assert same_as["props"] == prov([SLICE_BATCH], ["c9"])


def test_the_manifest_says_the_provenance_it_took_back(data_dir, provenance_graph):
    report, _code = run_reset(
        data_dir=data_dir,
        canonical_dir=data_dir / "canonical",
        batches_dir=data_dir / "batches",
        reports_dir=data_dir / "reports",
        ctx=ctx(provenance_graph),
        slice_="incremental",
        confirmed=False,
        write_report=False,
        echo=lambda _m: None,
    )
    from brain.reset import Manifest

    text = format_manifest(Manifest(scopes=["slice:incremental"], slice=report["slice"]))

    assert "would delete       1 LLM edges" in text
    assert "would strip" in text and "1 entities" in text
