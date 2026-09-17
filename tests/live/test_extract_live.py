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
from brain.chunk.chunker import Chunk
from brain.config import Settings
from brain.extract import graph as extract_graph
from brain.extract.build import MAX_BATCH_BYTES, BuildError, run_build
from brain.extract.merge import LEDGER_NAME, run_merge
from brain.extract.sample import run_sample
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import apply_schema, drop_schema
from brain.reset import entities_only_in_slice
from tests.extract_helpers import (
    MINI,
    MINI_MIN_CHARS,
    batch_input,
    batch_output,
    chunk_context,
    entity,
    mini_chunks,
    relation,
    write_chunks,
)

pytestmark = pytest.mark.live

PREFIX = "_Extract"
#: A one-off namespace an earlier fixture-building script used. It is cleaned here so the
#: repository has one place that knows every scratch label this step has ever created —
#: leftovers in a prefixed namespace are invisible to every production query and therefore
#: never noticed until someone counts nodes.
LEGACY_PREFIXES = ("_ExtractFix",)
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
    for prefix in LEGACY_PREFIXES:
        _clean_legacy(GraphContext(ctx.client, prefix=prefix))


def _clean_legacy(ctx: GraphContext) -> None:
    ctx.client.write(f"MATCH (c:{ctx.label('Chunk')}) DETACH DELETE c")
    ctx.client.write(f"MATCH (e:{ctx.label('Entity')}) DETACH DELETE e")
    ctx.client.write(f"MATCH (m:{ctx.label('IndexMeta')}) DETACH DELETE m")
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
        "envelope_valid_rate": 1.0,
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
        "RETURN d.key AS key, r.quote AS quote, r.kind AS kind, r.description AS description"
    )
    assert [h["key"] for h in hit] == ["KIP-5"]
    assert hit[0]["quote"]
    # the Document node knows nothing about the extraction, so the edge carries it
    assert hit[0]["kind"] == "Feature"
    assert hit[0]["description"]
    assert not graph.read(
        f"MATCH (e:{graph.label('Entity')}) WHERE e.name = 'KIP-5' RETURN e.id AS id"
    )


def test_a_name_that_is_an_existing_component_links_the_component(merged, graph):
    hit = graph.read(
        f"MATCH (:{graph.label('Chunk')})-[r:MENTIONS]->(c:{graph.label('Component')}) "
        "RETURN c.name AS name, r.kind AS kind, r.name AS extracted, r.description AS description"
    )
    assert hit[0]["kind"] == "Technology"
    assert hit[0]["extracted"] == "streams"
    assert hit[0]["description"]
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


def test_the_report_measures_how_much_the_extraction_consolidated(merged):
    """The number `brain resolve` is sized by: entities cited by exactly one chunk are
    observations, not things."""
    report, _root = merged
    c = report["consolidation"]
    assert c["entities"] == report["census"]["entities"]
    assert c["mentions_per_entity"] >= 1
    assert c["entities_with_one_evidence_chunk"] <= c["entities"]
    assert 0 <= c["pct_entities_with_one_evidence_chunk"] <= 100
    assert set(c["isolated_by_kind"]) <= {
        "Feature",
        "Decision",
        "Problem",
        "Alternative",
        "Risk",
        "Technology",
    }
    assert c["chunks_with_an_entity"] >= 1
    assert "MENTIONS" not in c["semantic_types"]


def test_a_fact_the_batches_no_longer_declare_is_reported_not_deleted(merged, graph, tmp_path):
    """A node written by an earlier merge and dropped from the current batches is the one
    thing a MERGE-only step cannot notice by itself."""
    report, root = merged
    assert report["stale"] == {**report["stale"], "entities": 0, "edges": 0}

    label = graph.label("Entity")
    stamp = graph.read(f"MATCH (e:{label}) RETURN e.merged_at AS at LIMIT 1")[0]["at"]
    assert stamp
    graph.write(
        f"MATCH (e:{label}) WITH e ORDER BY e.id LIMIT 1 "
        "SET e.merged_at = '1999-01-01T00:00:00+00:00'"
    )
    graph.write(
        f"MATCH (e:{label}) WHERE e.merged_at = $stamp WITH e ORDER BY e.id LIMIT 1 "
        "REMOVE e.merged_at",
        stamp=stamp,
    )
    stale = extract_graph.stale(graph, stamp)
    # both the old stamp and the missing one: `null <> $now` is null, not true
    assert stale["entities"] == 2, stale
    assert stale["action"].startswith("reported only")

    again, code = run_merge(
        ctx=graph, batches_dir=root, reports_dir=tmp_path, write_report=False, echo=QUIET
    )
    assert code == 0
    assert again["stale"]["entities"] == 0  # the batches still declare them, so they restamp


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


def test_a_batch_whose_envelope_is_wrong_is_quarantined_after_two_retries(graph, tmp_path):
    """Batch-level rejection is for the envelope only: this file says it answers a batch
    that does not exist, so nothing in it can be trusted."""
    root = tmp_path / "batches"
    shutil.copytree(FIXTURES, root / "extract", ignore=shutil.ignore_patterns("README.md"))
    bad = root / "extract" / "shard-01" / "001.out.json"
    payload = json.loads(bad.read_text(encoding="utf-8"))
    payload["batch_id"] = "shard-09/099"
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


def test_a_kind_outside_the_closed_set_costs_that_entity_and_what_cited_it(graph, tmp_path):
    """The batch is not quarantined: the other four entities and the whole second batch
    still merge. The two relations that named the rejected entity fall with it — an edge to
    a node nothing accepted has no endpoint, and inventing one is the bug this prevents."""
    root = tmp_path / "batches"
    shutil.copytree(FIXTURES, root / "extract", ignore=shutil.ignore_patterns("README.md"))
    bad = root / "extract" / "shard-01" / "001.out.json"
    payload = json.loads(bad.read_text(encoding="utf-8"))
    payload["entities"][0]["kind"] = "Component"
    bad.write_text(json.dumps(payload), encoding="utf-8")

    report, code = run_merge(
        ctx=graph, batches_dir=root, reports_dir=tmp_path, write_report=False, echo=QUIET
    )
    assert code == 0
    assert report["batches"]["valid"] == 2
    assert report["rejected_records"]["by_reason"] == {
        "entity:unknown_kind": 1,
        "relation:unresolved_endpoint": 2,
    }
    assert report["provenance"]["edges_without_provenance"] == 0


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


# ------------------------------------------ a partial merge onto an existing entity (16)

#: A namespace of its own, not `_Extract`: these tests merge twice over one graph and
#: assert on what the *first* merge left behind, which the shared module fixture cannot
#: promise once another test has written to it.
SLICE_PREFIX = "_ExtrTest"
SLICE = "incremental"
SLICE_BATCH_ID = f"{SLICE}/shard-01/001"
DECISION_NAME = "move assignment to the group coordinator"
#: What `brain chunk --slice incremental` would write for a follow-up record: a new chunk,
#: carrying the slice, whose text names something the corpus already extracted.
SLICE_TEXT = (
    "Follow-up for the Connect workers: move assignment to the group coordinator "
    "applies to them too, and the Connect worker config gains a rebalance timeout."
)
NEW_IN_SLICE = "Connect worker rebalance timeout"


@pytest.fixture(scope="module")
def slice_graph(client, tmp_path_factory):
    """The mini corpus, its chunks, and the two canned batches merged as the base corpus."""
    ctx = GraphContext(client, prefix=SLICE_PREFIX)
    _clean(ctx)
    reports = tmp_path_factory.mktemp("extr-reports")
    try:
        apply_schema(ctx)
        report, code = run_load(
            client=client,
            canonical_dir=MINI,
            reports_dir=reports,
            prefix=SLICE_PREFIX,
            write_report=False,
            echo=QUIET,
        )
        assert code == 0, [c for c in report["checks"] if not c["ok"]]
        write_chunks(ctx, mini_chunks())

        root = tmp_path_factory.mktemp("extr-base")
        shutil.copytree(FIXTURES, root / "extract", ignore=shutil.ignore_patterns("README.md"))
        base, code = run_merge(
            ctx=ctx, batches_dir=root, reports_dir=reports, write_report=False, echo=QUIET
        )
        assert code == 0, base["rejected_batches"]
        yield ctx
    finally:
        _clean(ctx)


def slice_chunk_id(ctx: GraphContext) -> str:
    """Write the increment's one chunk, hung off a work item the base corpus already has."""
    chunk = Chunk(
        parent_key="KAFKA-102",
        parent_kind="WorkItem",
        kind="description",
        position=1,
        text=SLICE_TEXT,
        slice=SLICE,
    )
    write_chunks(ctx, [chunk])
    return chunk.id


def write_slice_batch(root: Path, chunk_id: str) -> Path:
    """The `.in.json`/`.out.json` pair a sliced `brain extract build` + `kg-extractor` make.

    It re-states one entity and one relation the corpus already holds, from new evidence —
    which is the whole shape of an incremental extraction, and the shape that overwrote
    three live entities' Plan 1 provenance before the union went in.
    """
    shard_dir = root / "extract" / SLICE / "shard-01"
    shard_dir.mkdir(parents=True, exist_ok=True)
    inp = batch_input(
        [
            chunk_context(
                chunk_id=chunk_id,
                parent_key="KAFKA-102",
                parent_kind="WorkItem",
                parent_title="KAFKA-102: Connect worker config changes",
                position=1,
                text=SLICE_TEXT,
            )
        ],
        batch_id=SLICE_BATCH_ID,
    )
    out = batch_output(
        batch_id=SLICE_BATCH_ID,
        entities=[
            entity(
                kind="Decision",
                name=DECISION_NAME,
                description="Assignment is computed by the coordinator, for Connect too.",
                quote=DECISION_NAME,
                chunk_id=chunk_id,
            ),
            entity(
                kind="Feature",
                name=NEW_IN_SLICE,
                description="A rebalance timeout added to the Connect worker config.",
                quote="the Connect worker config gains a rebalance timeout",
                chunk_id=chunk_id,
            ),
        ],
        relations=[
            relation(
                type="DECIDES",
                source="KIP-5",
                target=DECISION_NAME,
                evidence_chunk_id=chunk_id,
            )
        ],
    )
    (shard_dir / "001.in.json").write_text(json.dumps(inp, indent=2), encoding="utf-8")
    (shard_dir / "001.out.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return root


#: The RETURN clause both reads share, so the entity and the edge are asked exactly the
#: same question — the union has to hold on both and a difference must not be a typo.
PROVENANCE = (
    "x.`evidence_chunk_ids` AS evidence, x.`batch_id` AS batch_id, "
    "x.`batch_ids` AS batch_ids, x.`shard` AS shard, x.`model` AS model, "
    "x.`extracted_at` AS extracted_at"
)


def decision_id(ctx: GraphContext) -> str:
    rows = ctx.read(
        f"MATCH (e:{ctx.label('Entity')}) WHERE e.kind = 'Decision' AND e.name = $name "
        "RETURN e.id AS id",
        name=DECISION_NAME,
    )
    assert len(rows) == 1, rows
    return str(rows[0]["id"])


def entity_provenance(ctx: GraphContext, entity_id: str) -> dict:
    rows = ctx.read(
        f"MATCH (x:{ctx.label('Entity')} {{id: $id}}) RETURN {PROVENANCE}", id=entity_id
    )
    return dict(rows[0])


def decides_provenance(ctx: GraphContext, entity_id: str) -> dict:
    rows = ctx.read(
        f"MATCH (:{ctx.label('Document')} {{key: 'KIP-5'}})-[x:DECIDES]->"
        f"(:{ctx.label('Entity')} {{id: $id}}) RETURN {PROVENANCE}",
        id=entity_id,
    )
    assert len(rows) == 1, f"{len(rows)} DECIDES edges — a partial merge must not add one"
    return dict(rows[0])


@pytest.fixture(scope="module")
def after_slice(slice_graph, tmp_path_factory):
    """Base provenance, then one sliced merge over it. Returns both sides and the root."""
    ctx = slice_graph
    entity_id = decision_id(ctx)
    before = {
        "entity": entity_provenance(ctx, entity_id),
        "decides": decides_provenance(ctx, entity_id),
    }
    chunk_id = slice_chunk_id(ctx)
    root = write_slice_batch(tmp_path_factory.mktemp("extr-slice"), chunk_id)
    report, code = run_merge(
        ctx=ctx,
        batches_dir=root,
        reports_dir=tmp_path_factory.mktemp("extr-slice-reports"),
        slice_=SLICE,
        write_report=False,
        echo=QUIET,
    )
    assert code == 0, report["rejected_batches"]
    return {
        "id": entity_id,
        "chunk_id": chunk_id,
        "before": before,
        "root": root,
        "report": report,
    }


def test_the_base_merge_left_the_decision_citing_one_chunk_and_one_batch(after_slice):
    """The starting state the union has to preserve. Without it the next test can pass by
    the entity never having had any provenance to lose."""
    before = after_slice["before"]
    assert before["entity"]["batch_ids"] == ["shard-01/002"]
    assert len(before["entity"]["evidence"]) == 1
    assert before["decides"]["batch_ids"] == ["shard-01/002"]


def test_a_sliced_merge_keeps_the_corpus_evidence_on_the_entity_and_adds_its_own(
    after_slice, slice_graph
):
    """The regression that damaged three live entities on 2026-09-17, through the real
    `write_entities` — `union_provenance` being right is not the same as it being wired in.

    `SET n += row.props` replaces a list property wholesale, so before the union the
    entity came out of the slice merge citing only the slice's chunk and only the slice's
    batch, and its Plan 1 evidence was gone with no record that it had ever been there.
    """
    before, after = (
        after_slice["before"]["entity"],
        entity_provenance(slice_graph, after_slice["id"]),
    )

    assert set(after["evidence"]) == set(before["evidence"]) | {after_slice["chunk_id"]}
    assert after["batch_ids"] == sorted([SLICE_BATCH_ID, "shard-01/002"])
    # `batch_id` and `shard` are first-seen, so they still name the batch that said it first
    assert after["batch_id"] == before["batch_id"] == "shard-01/002"
    assert after["shard"] == before["shard"]
    assert after["model"] == extract_graph.MODEL


def test_a_sliced_merge_keeps_the_corpus_evidence_on_the_edge_it_re_states(
    after_slice, slice_graph
):
    """`write_relations` is the same wiring one layer over, and an edge that loses its
    evidence loses the only answer `explain_edge` has."""
    before = after_slice["before"]["decides"]
    after = decides_provenance(slice_graph, after_slice["id"])

    assert set(after["evidence"]) == set(before["evidence"]) | {after_slice["chunk_id"]}
    assert after["batch_ids"] == sorted([SLICE_BATCH_ID, "shard-01/002"])
    assert after["batch_id"] == before["batch_id"]


def test_the_mention_the_corpus_wrote_is_not_touched_by_the_slice(after_slice, slice_graph):
    """Two chunks, two `MENTIONS`: a partial merge adds an edge, it does not move one."""
    rows = slice_graph.read(
        f"MATCH (c:{slice_graph.label('Chunk')})-[m:MENTIONS]->"
        f"(:{slice_graph.label('Entity')} {{id: $id}}) "
        "RETURN c.id AS chunk, m.batch_id AS batch_id, m.batch_ids AS batch_ids ORDER BY chunk",
        id=after_slice["id"],
    )
    by_chunk = {r["chunk"]: r for r in rows}
    assert set(by_chunk) == set(after_slice["before"]["entity"]["evidence"]) | {
        after_slice["chunk_id"]
    }
    assert by_chunk[after_slice["chunk_id"]]["batch_ids"] == [SLICE_BATCH_ID]
    base_chunk = after_slice["before"]["entity"]["evidence"][0]
    assert by_chunk[base_chunk]["batch_ids"] == ["shard-01/002"]


def test_a_second_partial_merge_changes_nothing(after_slice, slice_graph, tmp_path_factory):
    """Idempotency of the *partial* path. A union that appended rather than set-unioned
    would grow the arrays on every re-merge and nothing else would notice."""
    ctx = slice_graph
    before = {
        "entity": entity_provenance(ctx, after_slice["id"]),
        "decides": decides_provenance(ctx, after_slice["id"]),
        "census": extract_graph.census(ctx),
    }
    ctx.counters.update(dict.fromkeys(ctx.counters, 0))

    report, code = run_merge(
        ctx=ctx,
        batches_dir=after_slice["root"],
        reports_dir=tmp_path_factory.mktemp("extr-slice-again"),
        slice_=SLICE,
        write_report=False,
        echo=QUIET,
    )

    assert code == 0
    assert report["counters"]["nodes_created"] == 0
    assert report["counters"]["relationships_created"] == 0
    assert entity_provenance(ctx, after_slice["id"]) == before["entity"]
    assert decides_provenance(ctx, after_slice["id"]) == before["decides"]
    assert extract_graph.census(ctx) == before["census"]
    assert extract_graph.provenance_gaps(ctx)["total"] == 0


def test_after_the_partial_merge_the_slice_reset_does_not_claim_the_corpus_entity(
    after_slice, slice_graph
):
    """Why the union is not a cosmetic fix. `brain reset --slice` deletes the entities whose
    *every* surviving evidence chunk is in the slice; an entity the merge had stripped down
    to the slice's one chunk answered that description, and 26 of them were counted for
    deletion on the live graph. Read-only here — nothing is applied."""
    doomed = entities_only_in_slice(slice_graph, SLICE)

    assert after_slice["id"] not in doomed
    assert [d for d in doomed if d.endswith(NEW_IN_SLICE.casefold())] == doomed, doomed
    assert len(doomed) == 1, "only the entity the increment invented belongs to the increment"
