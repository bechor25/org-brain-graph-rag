"""`brain reset` — the three scopes, the refusal, and the node a real record still claims.

Everything here runs on a tmp data directory and an in-memory graph. `data/` and the real
Neo4j are never touched; the live counterpart is `tests/live/test_reset_live.py`, which
runs the same wipe in the `_ResetTest` label namespace.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from brain.cli import app
from brain.reset import (
    DATA_DIRS,
    PROTECTED_DIRS,
    Manifest,
    ResetError,
    census,
    format_manifest,
    prune_resolution_ledger,
    real_container_names,
    run_reset,
    strip_synthetic_records,
    wipe_data,
    wipe_graph,
    wipe_synthetic,
)
from tests.reset_helpers import FakeGraph, ctx

MINI = Path("data/fixtures/mini")


# --------------------------------------------------------------------------- fixtures


@pytest.fixture
def data_dir(tmp_path) -> Path:
    """A `data/` layout: the mini corpus plus a synthetic person, container and ledger."""
    root = tmp_path / "data"
    canonical = root / "canonical"
    shutil.copytree(MINI, canonical)
    for name in DATA_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "fixtures" / "mini").mkdir(parents=True)
    (root / "fixtures" / "mini" / "workitems.jsonl").write_text("{}\n", encoding="utf-8")

    with (canonical / "persons.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "id": "ado:rao.jun",
                    "identities": [{"source": "ado", "key": "rao.jun"}],
                    "synthetic": True,
                }
            )
            + "\n"
        )
    with (canonical / "containers.jsonl").open("a", encoding="utf-8") as f:
        # A sprint only the synthetic layer knows, and a component it *shares* with Jira.
        f.write(
            json.dumps(
                {
                    "id": "ado:sprint:Sprint 2023-01",
                    "source": "ado",
                    "kind": "sprint",
                    "name": "Sprint 2023-01",
                    "synthetic": True,
                }
            )
            + "\n"
        )
        f.write(
            json.dumps(
                {
                    "id": "ado:component:clients",
                    "source": "ado",
                    "kind": "component",
                    "name": "clients",
                    "synthetic": True,
                }
            )
            + "\n"
        )

    (canonical / "synthetic_merged.json").write_text(
        json.dumps({"provenance": {"xray:XT-1": {"batch_id": "000", "merged_at": "x"}}}),
        encoding="utf-8",
    )
    (canonical / "synthetic_truth.json").write_text("{}", encoding="utf-8")
    (root / "batches" / "synthetic" / "0").mkdir(parents=True)
    (root / "batches" / "synthetic" / "0" / "000.in.json").write_text("{}", encoding="utf-8")
    (root / "batches" / "extract" / "0").mkdir(parents=True)
    (root / "batches" / "extract" / "0" / "000.in.json").write_text("{}", encoding="utf-8")
    (root / "raw" / "jira").mkdir(parents=True)
    (root / "raw" / "jira" / "issues-0000.json").write_text("{}", encoding="utf-8")
    (root / "reports" / "load.json").write_text("{}", encoding="utf-8")
    (canonical / "resolution_ledger.json").write_text(
        json.dumps(
            {
                "persons": {
                    "ado:rao.jun": {"canonical": "jira:jrao", "tier": 2},
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
    """Two real work items, two synthetic ones, and the shared `clients` component."""
    return FakeGraph(
        nodes=[
            ("WorkItem", "KAFKA-1", {"synthetic": False}),
            ("WorkItem", "KAFKA-2", {"synthetic": False}),
            ("WorkItem", "ADO-1", {"synthetic": True}),
            ("WorkItem", "XT-1", {"synthetic": True}),
            ("Person", "jira:jrao", {"synthetic": False}),
            ("Person", "ado:rao.jun", {"synthetic": True}),
            ("Sprint", "Sprint 2023-01", {"synthetic": True}),
            # merged on `name` by both Jira and the synthetic layer; last writer stamped it
            ("Component", "clients", {"synthetic": True}),
            ("Chunk", "c1", {"synthetic": False}),
        ],
        edges=[(0, 2), (2, 7), (0, 8)],
    )


# --------------------------------------------------------------------------- refusal


def test_without_yes_nothing_is_deleted_and_the_exit_code_says_so(data_dir, graph):
    before = sorted(p.name for p in (data_dir / "canonical").iterdir())
    report, code = run_reset(
        data_dir=data_dir,
        canonical_dir=data_dir / "canonical",
        batches_dir=data_dir / "batches",
        reports_dir=data_dir / "reports",
        ctx=ctx(graph),
        graph=True,
        data=True,
        confirmed=False,
        echo=lambda _m: None,
    )
    assert code == 1
    assert report["applied"] is False
    assert sorted(p.name for p in (data_dir / "canonical").iterdir()) == before
    assert graph.labels()["WorkItem"] == 4
    assert not (data_dir / "reports" / "reset.json").exists()


def test_the_dry_run_manifest_counts_what_it_would_delete(data_dir, graph):
    report, _ = run_reset(
        data_dir=data_dir,
        canonical_dir=data_dir / "canonical",
        batches_dir=data_dir / "batches",
        reports_dir=data_dir / "reports",
        ctx=ctx(graph),
        graph=True,
        data=True,
        confirmed=False,
        echo=lambda _m: None,
    )
    assert report["graph"]["nodes"] == 9
    assert report["graph"]["nodes_by_label"]["WorkItem"] == 4
    assert report["data"]["dirs"]["canonical"]["entries"] > 0


def test_no_scope_is_an_error_not_a_no_op(data_dir):
    with pytest.raises(ResetError, match="nothing to reset"):
        run_reset(
            data_dir=data_dir,
            canonical_dir=data_dir / "canonical",
            batches_dir=data_dir / "batches",
            reports_dir=data_dir / "reports",
            confirmed=True,
            echo=lambda _m: None,
        )


def test_graph_scopes_need_a_connection(data_dir):
    with pytest.raises(ResetError, match="need a graph connection"):
        run_reset(
            data_dir=data_dir,
            canonical_dir=data_dir / "canonical",
            batches_dir=data_dir / "batches",
            reports_dir=data_dir / "reports",
            graph=True,
            confirmed=True,
            echo=lambda _m: None,
        )


# --------------------------------------------------------------------------- --graph


def test_graph_wipe_removes_every_node_and_keeps_the_schema(graph):
    context = ctx(graph)
    report = wipe_graph(context)
    assert report["nodes"] == 9
    assert graph.labels() == {}
    assert census(context) == dict.fromkeys(census(context), 0)
    # not one DROP CONSTRAINT / DROP INDEX was issued
    assert not any("DROP" in q for q in graph.queries)


def test_the_wipe_batches_its_deletes(graph):
    """9 nodes at BATCH_ROWS=1000 is one slice per label plus the terminating empty one."""
    wipe_graph(ctx(graph))
    deletes = [q for q in graph.queries if "DETACH DELETE" in q]
    assert all("WITH n LIMIT 1000" in q for q in deletes)


# --------------------------------------------------------------------------- --synthetic


def test_synthetic_removes_only_the_synthetic_records(data_dir):
    removed = strip_synthetic_records(data_dir / "canonical", apply=True)
    assert removed == {
        "workitems": 4,
        "documents": 0,
        "persons": 1,
        "changes": 0,
        "containers": 2,
    }
    kept = [
        json.loads(line)
        for line in (data_dir / "canonical" / "workitems.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(kept) == 3
    assert all(not r.get("synthetic") for r in kept)


def test_a_dry_run_counts_but_does_not_rewrite(data_dir):
    before = (data_dir / "canonical" / "workitems.jsonl").read_bytes()
    assert strip_synthetic_records(data_dir / "canonical", apply=False)["workitems"] == 4
    assert (data_dir / "canonical" / "workitems.jsonl").read_bytes() == before


def test_an_unparseable_canonical_file_stops_the_rewrite(data_dir):
    (data_dir / "canonical" / "workitems.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ResetError, match="not JSON"):
        strip_synthetic_records(data_dir / "canonical", apply=True)


def test_a_container_a_real_record_still_claims_survives(data_dir, graph):
    """`clients` is one node Jira and the ADO layer both merged on. Deleting it would take
    a real component and every edge into it."""
    protected = real_container_names(data_dir / "canonical")
    assert "clients" in protected["Component"]  # the mini fixture's real components
    assert "Sprint" not in protected  # only the synthetic layer ever names a sprint

    report = wipe_synthetic(
        data_dir / "canonical",
        ctx=ctx(graph),
        batches_dir=data_dir / "batches",
        apply=True,
    )
    assert graph.prop("Component", "clients", "synthetic") is False
    assert report["shared_nodes_kept"] == 1
    assert report["nodes_by_label"] == {"WorkItem": 2, "Person": 1, "Sprint": 1}
    assert sorted(graph.labels()) == ["Chunk", "Component", "Person", "WorkItem"]
    assert graph.labels()["WorkItem"] == 2


def test_synthetic_deletes_the_ledger_the_truth_and_the_synthetic_batches(data_dir, graph):
    report = wipe_synthetic(
        data_dir / "canonical", ctx=ctx(graph), batches_dir=data_dir / "batches", apply=True
    )
    assert not (data_dir / "canonical" / "synthetic_merged.json").exists()
    assert not (data_dir / "canonical" / "synthetic_truth.json").exists()
    assert not (data_dir / "batches" / "synthetic").exists()
    # the extractor's batches are not the synthetic layer's
    assert (data_dir / "batches" / "extract" / "0" / "000.in.json").exists()
    assert len(report["files"]) == 3


def test_the_resolution_ledger_loses_only_the_rows_naming_a_deleted_identity(data_dir):
    dropped = prune_resolution_ledger(data_dir / "canonical", {"ado:rao.jun"}, apply=True)
    assert dropped == 1
    rows = json.loads((data_dir / "canonical" / "resolution_ledger.json").read_text())
    assert list(rows["persons"]) == ["confluence:mjsax"]


def test_the_real_corpus_is_still_loadable_after_a_synthetic_reset(data_dir, graph):
    from brain.graph.corpus import load_corpus

    wipe_synthetic(
        data_dir / "canonical", ctx=ctx(graph), batches_dir=data_dir / "batches", apply=True
    )
    corpus = load_corpus(data_dir / "canonical")
    assert len(corpus.workitems) == 3
    assert all(not w.synthetic for w in corpus.workitems)


# --------------------------------------------------------------------------- --data


def test_data_wipe_empties_the_five_dirs_and_never_the_fixtures(data_dir):
    report = wipe_data(data_dir, apply=True)
    assert set(report["dirs"]) <= set(DATA_DIRS)
    for name in DATA_DIRS:
        assert list((data_dir / name).iterdir()) == [], name
        assert (data_dir / name).is_dir()
    assert report["kept"] == sorted(PROTECTED_DIRS)
    assert (data_dir / "fixtures" / "mini" / "workitems.jsonl").exists()


def test_fixtures_is_not_in_the_wipe_list():
    assert "fixtures" not in DATA_DIRS
    assert PROTECTED_DIRS == {"fixtures"}


# --------------------------------------------------------------------------- --all


def test_all_leaves_zero_nodes_and_empty_dirs_and_writes_its_manifest(data_dir, graph):
    report, code = run_reset(
        data_dir=data_dir,
        canonical_dir=data_dir / "canonical",
        batches_dir=data_dir / "batches",
        reports_dir=data_dir / "reports",
        ctx=ctx(graph),
        graph=True,
        data=True,
        confirmed=True,
        echo=lambda _m: None,
    )
    assert code == 0
    assert graph.labels() == {}
    assert all(v == 0 for v in report["census_after"].values())
    for name in DATA_DIRS:
        assert list((data_dir / name).iterdir()) == [str(p) for p in []] or name == "reports"
    assert (data_dir / "fixtures" / "mini" / "workitems.jsonl").exists()
    # the manifest is written back into the directory it just emptied
    written = json.loads((data_dir / "reports" / "reset.json").read_text(encoding="utf-8"))
    assert written["scopes"] == ["graph", "data"] and written["applied"] is True


def test_the_manifest_reads_as_a_manifest():
    text = format_manifest(
        Manifest(
            scopes=["graph"],
            applied=True,
            graph={"nodes": 3, "edges": 2, "nodes_by_label": {"WorkItem": 3}},
            census_after={"WorkItem": 0},
        )
    )
    assert "deleted       3 :WorkItem" in text
    assert "constraints and indexes kept" in text
    assert "every label is 0" in text


def test_a_dry_run_manifest_says_it_deleted_nothing():
    text = format_manifest(Manifest(scopes=["data"], applied=False, data={"dirs": {}}))
    assert "DRY RUN" in text
    assert "pass --yes" in text


# --------------------------------------------------------------------------- cli


def test_the_cli_needs_a_scope(runner):
    out = runner.invoke(app, ["reset"])
    assert out.exit_code != 0
    assert "pick a scope" in out.output


def test_the_cli_refuses_without_yes_and_never_opens_a_connection(runner, tmp_path, monkeypatch):
    """`--data` alone must not need Neo4j: the conftest guard fails the test if it opens one."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "x.json").write_text("{}", encoding="utf-8")
    out = runner.invoke(app, ["reset", "--data"])
    assert out.exit_code == 1
    assert "DRY RUN" in out.output
    assert (tmp_path / "raw" / "x.json").exists()


def test_the_cli_applies_data_with_yes(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "x.json").write_text("{}", encoding="utf-8")
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "keep.jsonl").write_text("{}", encoding="utf-8")
    out = runner.invoke(app, ["reset", "--data", "--yes"])
    assert out.exit_code == 0, out.output
    assert not (tmp_path / "raw" / "x.json").exists()
    assert (tmp_path / "fixtures" / "keep.jsonl").exists()
