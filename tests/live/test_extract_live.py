"""`brain extract` build → merge → sample against the real Neo4j, in its own label space.

Everything here runs under the `_Extract` prefix on the mini fixture, so it neither reads
nor damages the graph a real run built. No embeddings are involved: extraction reads
`Chunk.text`, so the chunks are written straight in and Ollama is never asked anything.

The canned `.out.json` files in `tests/fixtures/extract/` stand in for the agents. That is
what makes `make smoke` a real end-to-end run of this step without a `kg-extractor`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from brain.chunk import graph as chunk_graph
from brain.config import Settings
from brain.extract import graph as extract_graph
from brain.extract.build import MAX_BATCH_BYTES, BuildError, run_build
from brain.extract.merge import LEDGER_NAME, run_merge
from brain.extract.sample import run_sample
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import apply_schema, drop_schema
from tests.extract_helpers import MINI, MINI_MIN_CHARS, mini_chunks, write_chunks

pytestmark = pytest.mark.live

PREFIX = "_Extract"
FIXTURES = Path("tests/fixtures/extract")
QUIET = lambda _m: None  # noqa: E731


@pytest.fixture(scope="module")
def client():
    s = Settings()
    c = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
    c.verify()
    try:
        yield c
    finally:
        c.close()


def _clean(ctx: GraphContext) -> None:
    ctx.client.write(f"MATCH (c:{ctx.label('Chunk')}) DETACH DELETE c")
    ctx.client.write(f"MATCH (e:{ctx.label('Entity')}) DETACH DELETE e")
    wipe(ctx)
    chunk_graph.drop_chunk_schema(ctx)
    extract_graph.drop_extract_schema(ctx)
    drop_schema(ctx)


@pytest.fixture(scope="module")
def graph(client):
    """The mini corpus loaded and chunked, in a namespace of its own."""
    ctx = GraphContext(client, prefix=PREFIX)
    _clean(ctx)
    try:
        apply_schema(ctx)
        report, code = run_load(
            client=client,
            canonical_dir=MINI,
            reports_dir=Path("/tmp"),
            prefix=PREFIX,
            write_report=False,
            echo=QUIET,
        )
        assert code == 0, [c for c in report["checks"] if not c["ok"]]
        write_chunks(ctx, mini_chunks())
        yield ctx
    finally:
        _clean(ctx)


# ------------------------------------------------------------------------------- build


@pytest.fixture(scope="module")
def built(graph, tmp_path_factory):
    root = tmp_path_factory.mktemp("extract-build")
    manifest, code = run_build(
        ctx=graph,
        canonical_dir=MINI,
        batches_dir=root,
        reports_dir=root,
        shards=1,
        batch_size=2,
        min_chars=MINI_MIN_CHARS,
        write_report=False,
        echo=QUIET,
    )
    assert code == 0
    return manifest, root / "extract"


def test_build_selects_the_kip_sections_and_the_long_real_descriptions(built):
    manifest, _root = built
    selection = manifest["selection"]
    assert selection["kip_sections"] == 1  # KIP-5, the only document in scope
    # KAFKA-100 (Improvement) and KAFKA-101 (Bug); KAFKA-102 is short, XT/XE/XP are synthetic
    assert selection["issue_descriptions"] == 2
    assert selection["issue_descriptions_by_type"] == {"Bug": 1, "Improvement": 1}
    assert selection["chunks"] == 3
    assert selection["scope"]["documents_in_scope"] == 1


def test_build_excludes_comments_commits_and_the_synthetic_layer(built, graph):
    """Phase A is KIP sections and issue descriptions. The graph holds more than that."""
    manifest, root = built
    picked = {
        c["chunk_id"]
        for path in sorted(root.glob("shard-*/*.in.json"))
        for c in json.loads(path.read_text(encoding="utf-8"))["chunks"]
    }
    rows = graph.read(
        f"MATCH (c:{graph.label('Chunk')}) WHERE c.id IN $ids "
        "RETURN DISTINCT c.kind AS kind, c.parent_kind AS parent",
        ids=sorted(picked),
    )
    assert {r["kind"] for r in rows} <= {"section", "description"}
    assert "Commit" not in {r["parent"] for r in rows}
    synthetic = graph.read(
        f"MATCH (w:{graph.label('WorkItem')})-[:HAS_CHUNK]->(c:{graph.label('Chunk')}) "
        "WHERE w.synthetic AND c.id IN $ids RETURN count(c) AS c",
        ids=sorted(picked),
    )
    assert synthetic[0]["c"] == 0
    assert manifest["totals"]["chunks"] == len(picked)


def test_every_batch_file_is_indented_and_within_the_size_budget(built):
    manifest, root = built
    for path in sorted(root.glob("shard-*/*.in.json")):
        text = path.read_text(encoding="utf-8")
        assert path.stat().st_size <= MAX_BATCH_BYTES, path
        assert text.startswith("{\n  ")
        assert max(len(line.encode()) for line in text.splitlines()) < 45_000
    assert manifest["sizes"]["max_bytes"] <= MAX_BATCH_BYTES
    assert manifest["sizes"]["over_budget"] == []


def test_each_shard_gets_a_status_file_and_the_run_gets_a_manifest(built):
    _manifest, root = built
    assert json.loads((root / "shard-01" / "status.json").read_text()) == {
        "shard": "shard-01",
        "done": [],
        "failed": [],
    }
    assert (root / "MANIFEST.json").is_file()


def test_a_rebuild_over_a_working_shard_is_refused_not_raced(built, graph):
    _manifest, root = built
    status = root / "shard-01" / "status.json"
    original = status.read_text(encoding="utf-8")
    status.write_text(json.dumps({"shard": "shard-01", "done": ["001"]}), encoding="utf-8")
    try:
        with pytest.raises(BuildError, match="refusing to rebuild"):
            run_build(
                ctx=graph,
                canonical_dir=MINI,
                batches_dir=root.parent,
                reports_dir=root.parent,
                shards=1,
                batch_size=2,
                min_chars=MINI_MIN_CHARS,
                write_report=False,
                echo=QUIET,
            )
    finally:
        status.write_text(original, encoding="utf-8")


def test_the_fixture_chunk_ids_are_still_the_ones_the_mini_corpus_produces(graph):
    """The canned outputs cite chunk ids. If the chunker's splitting changes they stop
    matching, and this says so instead of the merge quietly rejecting every record."""
    cited = {
        c["chunk_id"]
        for path in sorted(FIXTURES.glob("shard-*/*.in.json"))
        for c in json.loads(path.read_text(encoding="utf-8"))["chunks"]
    }
    present = extract_graph.existing_chunk_ids(graph, sorted(cited))
    assert present == cited, (
        f"{sorted(cited - present)} are not in the mini graph any more — rebuild "
        "tests/fixtures/extract from `brain extract build`, do not edit the ids"
    )


# ------------------------------------------------------------------------------- merge


@pytest.fixture(scope="module")
def merged(graph, tmp_path_factory):
    root = tmp_path_factory.mktemp("extract-merge")
    shutil.copytree(FIXTURES, root / "extract", ignore=shutil.ignore_patterns("README.md"))
    report, code = run_merge(
        ctx=graph, batches_dir=root, reports_dir=root, write_report=False, echo=QUIET
    )
    assert code == 0, report["rejected_batches"]
    return report, root


def test_both_canned_batches_merge_with_nothing_rejected(merged):
    report, _root = merged
    assert report["batches"] == {
        **report["batches"],
        "found": 2,
        "valid": 2,
        "invalid": 0,
        "first_pass_rate": 1.0,
    }
    assert report["rejected_records"]["total"] == 0
    assert report["rejected_batches"] == []


def test_entities_land_with_their_kinds_and_their_normalised_ids(merged, graph):
    report, _root = merged
    rows = graph.read(
        f"MATCH (e:{graph.label('Entity')}) RETURN e.id AS id, e.kind AS kind, "
        "e.name AS name, e.norm_name AS norm ORDER BY id"
    )
    assert rows, report["planned"]
    assert all(r["id"] == f"{r['kind']}|{r['norm']}" for r in rows)
    assert {r["kind"] for r in rows} <= {
        "Feature",
        "Decision",
        "Problem",
        "Alternative",
        "Risk",
        "Technology",
    }
    assert report["census"]["entities"] == len(rows)


def test_a_name_that_is_an_existing_key_links_that_node_and_mints_no_entity(merged, graph):
    """`KIP-5` in a description is the document the graph already holds."""
    report, _root = merged
    assert report["census"]["mentions_by_target_label"]["Document"] >= 1
    hit = graph.read(
        f"MATCH (:{graph.label('Chunk')})-[r:MENTIONS]->(d:{graph.label('Document')}) "
        "RETURN d.key AS key, r.quote AS quote"
    )
    assert [h["key"] for h in hit] == ["KIP-5"]
    assert hit[0]["quote"]
    assert not graph.read(
        f"MATCH (e:{graph.label('Entity')}) WHERE e.name = 'KIP-5' RETURN e.id AS id"
    )


def test_a_name_that_is_an_existing_component_links_the_component(merged, graph):
    hit = graph.read(
        f"MATCH (:{graph.label('Chunk')})-[r:MENTIONS]->(c:{graph.label('Component')}) "
        "RETURN c.name AS name"
    )
    assert [h["name"] for h in hit] == ["streams"]


def test_relations_hang_off_the_structured_nodes_they_name(merged, graph):
    rows = graph.read(
        f"MATCH (a)-[r:IMPLEMENTS|DEPENDS_ON|INTRODUCES_RISK|DECIDES]->(b) "
        f"WHERE a:{graph.label('WorkItem')} OR a:{graph.label('Document')} "
        "RETURN type(r) AS type, coalesce(a.key, a.name) AS src, b.id AS dst ORDER BY type, src"
    )
    assert {r["src"] for r in rows} == {"KAFKA-100", "KAFKA-101", "KIP-5"}
    assert {r["type"] for r in rows} == {"IMPLEMENTS", "DEPENDS_ON", "INTRODUCES_RISK", "DECIDES"}


def test_no_llm_derived_edge_exists_without_provenance(merged, graph):
    """The live assertion of conventions rule 3, read back from the graph."""
    report, _root = merged
    assert report["provenance"]["edges_without_provenance"] == 0
    assert extract_graph.provenance_gaps(graph)["total"] == 0
    rows = graph.read(
        f"MATCH (a)-[r]->(b) WHERE type(r) IN $types AND a:{graph.label('Chunk')} "
        "RETURN r.model AS model, r.batch_id AS batch, size(r.evidence_chunk_ids) AS n, "
        "r.extracted_at AS at",
        types=list(extract_graph.LLM_RELATION_TYPES),
    )
    assert rows
    assert all(
        r["model"] == extract_graph.MODEL and r["batch"] and r["n"] and r["at"] for r in rows
    )


def test_a_decision_with_no_motivation_is_marked_weak_and_a_motivated_one_is_not(merged, graph):
    report, _root = merged
    rows = {
        r["name"]: r["weak"]
        for r in graph.read(
            f"MATCH (e:{graph.label('Entity')} ) WHERE e.kind = 'Decision' "
            "RETURN e.name AS name, e.weak AS weak"
        )
    }
    assert rows["move assignment to the group coordinator"] is False
    assert rows["fence members with stale epochs"] is True
    assert report["weak_decisions"] == {"decisions": 2, "weak": 1, "supported": 1}


def test_the_ledger_records_every_merged_batch(merged):
    _report, root = merged
    ledger = json.loads((root / "extract" / LEDGER_NAME).read_text(encoding="utf-8"))
    assert sorted(ledger["batches"]) == ["shard-01/001", "shard-01/002"]
    assert ledger["failed"] == {}
    assert ledger["model"] == extract_graph.MODEL


def test_a_second_merge_creates_nothing(merged, graph, tmp_path):
    """Acceptance criterion: a repeated merge is 0 new entities and 0 new edges."""
    _first, root = merged
    graph.counters.update(dict.fromkeys(graph.counters, 0))
    report, code = run_merge(
        ctx=graph, batches_dir=root, reports_dir=tmp_path, write_report=False, echo=QUIET
    )
    assert code == 0
    assert report["counters"]["nodes_created"] == 0
    assert report["counters"]["relationships_created"] == 0
    assert report["census"] == _first["census"]


def test_a_batch_that_breaks_the_contract_is_quarantined_after_two_retries(graph, tmp_path):
    root = tmp_path / "batches"
    shutil.copytree(FIXTURES, root / "extract", ignore=shutil.ignore_patterns("README.md"))
    bad = root / "extract" / "shard-01" / "001.out.json"
    payload = json.loads(bad.read_text(encoding="utf-8"))
    payload["entities"][0]["kind"] = "Component"  # not in the closed set
    states = []
    for attempt in range(3):
        payload["notes"] = [f"attempt {attempt}"]  # the agent regenerated it, still wrong
        bad.write_text(json.dumps(payload), encoding="utf-8")
        report, code = run_merge(
            ctx=graph, batches_dir=root, reports_dir=tmp_path, write_report=False, echo=QUIET
        )
        assert code == 1
        ledger = json.loads((root / "extract" / "ledger.json").read_text(encoding="utf-8"))
        states.append(ledger["failed"]["shard-01/001"]["state"])
    assert states == ["retry", "retry", "quarantine"]
    assert (root / "extract" / "shard-01" / "quarantine" / "001.in.json").is_file()
    assert report["batches"]["valid"] == 1  # the other batch still merged


# ------------------------------------------------------------------------------ sample


def test_sample_prints_each_quote_inside_the_text_it_came_from(merged, graph, tmp_path):
    lines: list[str] = []
    report, code = run_sample(
        ctx=graph, reports_dir=tmp_path, n=5, seed=3, write_report=False, echo=lines.append
    )
    assert code == 0
    assert report["sample"]
    for row in report["sample"]:
        assert f"[[{row['quote']}]]" in row["context"] or row["quote"] in row["context"]
        assert row["supported"] is None  # the human fills this in
    assert any("quote:" in line for line in lines)
