"""`brain communities` against the real Neo4j (GDS) and the real Ollama, on the mini corpus.

Everything runs in the `_Comm…` label namespace and a `_Commbrain_communities` GDS graph,
so `make smoke` neither reads nor drops what a production run built.

The fixture is `data/fixtures/mini/` loaded by `brain load`, its Phase A chunks written by
the chunker (no embeddings — communities read structure, not vectors), and a handful of
entities and `MENTIONS` edges written straight in. That last part is deliberate: the point
of the smoke test is the projection, Leiden, the batch contract and the merge, and running
four `kg-extractor` agents to get there would test the extraction instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.community import graph as community_graph
from brain.community import projection as proj
from brain.community.batches import run_batches
from brain.community.build import run_build
from brain.community.merge import LEDGER_NAME, run_merge
from brain.community.models import TASK
from brain.config import Settings
from brain.embed.client import OllamaEmbedder
from brain.extract.build import MAX_BATCH_BYTES
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load
from brain.graph.schema import drop_schema
from tests.extract_helpers import MINI, MINI_MIN_CHARS, mini_chunks, write_chunks

pytestmark = pytest.mark.live

PREFIX = "_Comm"
MIN_SIZE = 2

#: Four entities and the KIP/issue chunks that mention them. Two of them are `Technology`
#: with no semantic edge at all — the 83% case from `data/reports/extract.json`, which only
#: reaches a community through the collapsed MENTIONS edge.
ENTITIES = [
    ("Decision|move assignment to the coordinator", "Decision", "Move assignment"),
    ("Problem|long rebalances", "Problem", "Long rebalances"),
    ("Technology|group coordinator", "Technology", "Group coordinator"),
    ("Technology|consumer client", "Technology", "Consumer client"),
]


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def ctx(settings):
    client = GraphClient(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database
    )
    client.verify()
    c = GraphContext(client, prefix=PREFIX)

    def clean() -> None:
        from brain.chunk.graph import drop_chunk_schema
        from brain.graph.runner import wipe

        proj.drop(c)
        wipe(c)
        # `wipe` only knows the labels `brain load` writes; these three belong to the
        # steps after it and would otherwise survive into the next run of this module.
        for label in ("Chunk", "Entity", "Community"):
            c.client.write(f"MATCH (n:{c.label(label)}) DETACH DELETE n")
        drop_schema(c)
        drop_chunk_schema(c)
        community_graph.drop_community_schema(c)

    clean()
    try:
        yield c
    finally:
        clean()
        client.close()


@pytest.fixture(scope="module")
def workdir(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("communities-work")
    dirs = {name: root / name for name in ("reports", "batches")}
    for path in dirs.values():
        path.mkdir()
    return dirs


@pytest.fixture(scope="module")
def embedder(settings):
    with OllamaEmbedder(settings.ollama_url, settings.embed_model, settings.embed_dim) as e:
        yield e


@pytest.fixture(scope="module")
def loaded(ctx, workdir):
    """mini corpus -> nodes, then its chunks, then a small extraction written by hand."""
    report, code = run_load(
        client=ctx.client,
        canonical_dir=MINI,
        reports_dir=workdir["reports"],
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0, [c for c in report["checks"] if not c["ok"]]

    from brain.chunk.graph import apply_chunk_schema

    apply_chunk_schema(ctx, 1024)
    chunks = [c for c in mini_chunks() if c.char_len >= MINI_MIN_CHARS or c.kind == "section"]
    write_chunks(ctx, chunks)

    ctx.write_rows(
        f"UNWIND $rows AS row MERGE (e:{ctx.label('Entity')} {{id: row.id}}) SET e += row",
        [
            {"id": eid, "kind": kind, "name": name, "description": f"{name} in the mini corpus."}
            for eid, kind, name in ENTITIES
        ],
    )
    # every entity is mentioned by the first chunk of the KIP; the decision and the problem
    # are also mentioned by an issue description, which is what joins the two parents.
    kip = [c for c in chunks if c.parent_kind == "Document"]
    issues = [c for c in chunks if c.parent_kind == "WorkItem"]
    assert kip and issues, "the mini corpus has no KIP sections or no issue descriptions"
    mentions = [{"src": kip[0].id, "dst": eid} for eid, _, _ in ENTITIES]
    mentions += [{"src": issues[0].id, "dst": ENTITIES[i][0]} for i in (0, 1)]
    ctx.write_rows(
        f"UNWIND $rows AS row\nMATCH (c:{ctx.label('Chunk')} {{id: row.src}})\n"
        f"MATCH (e:{ctx.label('Entity')} {{id: row.dst}})\n"
        "MERGE (c)-[m:MENTIONS]->(e) SET m.quote = 'q'",
        mentions,
    )
    ctx.write_rows(
        f"UNWIND $rows AS row\nMATCH (a:{ctx.label('Entity')} {{id: row.src}})\n"
        f"MATCH (b:{ctx.label('Entity')} {{id: row.dst}})\n"
        "MERGE (a)-[r:MOTIVATED_BY]->(b)",
        [{"src": ENTITIES[0][0], "dst": ENTITIES[1][0]}],
    )
    return {"chunks": chunks, "kip": kip, "issues": issues}


def build(ctx, workdir, **kw):
    report, code = run_build(
        ctx=ctx,
        reports_dir=workdir["reports"],
        embed_dim=1024,
        min_size=MIN_SIZE,
        write_report=False,
        echo=lambda _m: None,
        **kw,
    )
    assert code == 0
    return report


def communities(ctx) -> dict[str, dict]:
    rows = ctx.read(
        f"MATCH (c:{ctx.label('Community')}) "
        "RETURN c.id AS id, c.level AS level, c.size AS size, c.member_hash AS member_hash, "
        "c.misc AS misc, c.title AS title, c.summary AS summary, c.rank AS rank, "
        "c.findings AS findings, c.evidence_chunk_ids AS evidence_chunk_ids, "
        "c.batch_id AS batch_id, c.model AS model, c.extracted_at AS extracted_at, "
        "c.embedding IS NOT NULL AS embedded"
    )
    return {r["id"]: r for r in rows}


# ---------------------------------------------------------------------------- the dry run


def test_a_dry_run_projects_and_measures_and_writes_nothing(loaded, ctx, workdir):
    report = build(ctx, workdir, dry_run=True)

    assert report["dry_run"] is True and report["written"]["applied"] is False
    assert report["projection"]["nodes"] >= 10
    assert report["projection"]["edges"] >= 1
    assert report["totals"]["communities"] >= 1
    assert communities(ctx) == {}
    # the in-memory graph never outlives the run (brief 09, note 2)
    assert proj.exists(ctx) is False


def test_the_projection_holds_only_the_four_labels_and_the_collapsed_edges(loaded, ctx, workdir):
    report = build(ctx, workdir, dry_run=True)
    branches = report["projection"]["rows_per_branch"]

    assert branches["MENTIONS_PARENT"] >= len(ENTITIES), "the chunk collapse produced no edges"
    assert set(report["projection"]["labels"]) == {
        "Entity",
        "WorkItem",
        "Document",
        "Component",
    }
    # a Chunk is never a member, however many MENTIONS it carries: the projection holds
    # exactly the non-synthetic nodes of the four labels and nothing else
    assert ctx.read(f"MATCH (c:{ctx.label('Chunk')}) RETURN count(c) AS n")[0]["n"] > 0
    expected = ctx.read(
        f"MATCH (n) WHERE (n:{ctx.label('Entity')} OR n:{ctx.label('WorkItem')} "
        f"OR n:{ctx.label('Document')} OR n:{ctx.label('Component')}) "
        "AND coalesce(n.synthetic, false) = false RETURN count(n) AS n"
    )[0]["n"]
    assert report["projection"]["nodes"] == expected


def test_a_pair_of_nodes_carries_one_edge_per_type_however_many_relationships_join_them(
    loaded, ctx, workdir
):
    """GDS keeps every row it is given, so a duplicate row is twice the weight."""
    ctx.write_rows(
        f"UNWIND $rows AS row\nMATCH (a:{ctx.label('Entity')} {{id: row.src}})\n"
        f"MATCH (b:{ctx.label('Entity')} {{id: row.dst}})\n"
        "CREATE (a)-[:MOTIVATED_BY]->(b)",
        # the same edge again, and once more the other way round: the two shapes the
        # projection has to aggregate (extract merge leaves both behind)
        [
            {"src": ENTITIES[0][0], "dst": ENTITIES[1][0]},
            {"src": ENTITIES[1][0], "dst": ENTITIES[0][0]},
        ],
    )
    try:
        report = build(ctx, workdir, dry_run=True)
        p = report["projection"]
        assert p["rows_per_branch"]["MOTIVATED_BY"] == 1
        assert p["relationships_per_branch"]["MOTIVATED_BY"] == 3
        assert p["parallel_relationships_collapsed"]["MOTIVATED_BY"] == 2
        # every branch's rows, and not one relationship more, reached the in-memory graph
        assert p["edges"] == sum(n for name, n in p["rows_per_branch"].items() if name != "nodes")
    finally:
        # back to the one edge the fixture wrote, so the rest of the module sees it
        ctx.write(f"MATCH (:{ctx.label('Entity')})-[r:MOTIVATED_BY]->() DELETE r")
        ctx.write_rows(
            f"UNWIND $rows AS row\nMATCH (a:{ctx.label('Entity')} {{id: row.src}})\n"
            f"MATCH (b:{ctx.label('Entity')} {{id: row.dst}})\n"
            "MERGE (a)-[:MOTIVATED_BY]->(b)",
            [{"src": ENTITIES[0][0], "dst": ENTITIES[1][0]}],
        )


# ------------------------------------------------------------------------------ the build


@pytest.fixture(scope="module")
def built(loaded, ctx, workdir):
    return build(ctx, workdir)


def test_the_build_writes_a_community_for_every_group_leiden_found(built, ctx):
    found = communities(ctx)
    assert len(found) == built["totals"]["communities"] >= 1
    assert all(row["member_hash"] for row in found.values())
    assert all(row["summary"] is None for row in found.values())


def test_every_projected_member_belongs_to_a_fine_community(built, ctx):
    """The acceptance criterion: `IN_COMMUNITY` at the fine level for every member."""
    assert built["unplaced_members_fine_level"]["total"] == 0
    edges = built["census"]["in_community_edges"]
    assert edges["0"] == built["projection"]["nodes"]


def test_the_technology_entities_reached_a_community_through_their_parent(built, ctx):
    """They have no semantic edge at all; only the collapsed MENTIONS puts them anywhere."""
    rows = ctx.read(
        f"MATCH (e:{ctx.label('Entity')})-[:IN_COMMUNITY {{level: 0}}]->"
        f"(c:{ctx.label('Community')})\n"
        "WHERE e.kind = 'Technology' RETURN e.id AS id, c.id AS community, c.size AS size"
    )
    assert len(rows) == 2
    assert all(r["size"] > 1 for r in rows), "a Technology entity was left in a singleton"


def test_a_second_build_with_the_same_seed_gives_the_same_member_hashes(built, ctx, workdir):
    before = {c["id"]: c["member_hash"] for c in communities(ctx).values()}
    build(ctx, workdir)
    after = {c["id"]: c["member_hash"] for c in communities(ctx).values()}
    assert after == before


def test_a_different_seed_is_allowed_to_disagree_but_still_places_everyone(ctx, workdir, built):
    report = build(ctx, workdir, seed=1234)
    assert report["unplaced_members_fine_level"]["total"] == 0
    build(ctx, workdir)  # back to the canonical partition for the rest of the module


# ---------------------------------------------------------------------------- the batches


@pytest.fixture(scope="module")
def packed(built, ctx, workdir):
    manifest, code = run_batches(
        ctx=ctx,
        batches_dir=workdir["batches"],
        reports_dir=workdir["reports"],
        shards=2,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0
    return manifest


def inputs(workdir) -> list[Path]:
    root = workdir["batches"] / TASK
    return sorted(root.glob("shard-*/[0-9][0-9][0-9].in.json"))


def test_every_batch_is_indented_within_budget_and_carries_its_contract(packed, workdir):
    files = inputs(workdir)
    assert files, packed
    for path in files:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        assert text.startswith("{\n  ")
        assert len(text.encode("utf-8")) <= MAX_BATCH_BYTES
        assert payload["schema_path"] == "brain/community/schema.json"
        assert payload["schema_sha256"]
        assert payload["community_count"] == len(payload["communities"])


def test_a_community_ships_its_top_members_and_capped_evidence(packed, workdir):
    for path in inputs(workdir):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for community in payload["communities"]:
            assert 1 <= len(community["members"]) <= 25
            degrees = [m["degree"] for m in community["members"]]
            assert degrees == sorted(degrees, reverse=True), "members are not by degree"
            assert len(community["evidence"]) <= 8
            for item in community["evidence"]:
                assert len(item["text"]) <= 600
                assert len(item["chunk_id"]) == 40


def test_the_evidence_of_a_community_comes_from_its_own_members(packed, workdir, ctx):
    for path in inputs(workdir):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for community in payload["communities"]:
            if not community["evidence"]:
                continue
            member_keys = {m["key"] for m in community["members"]}
            rows = ctx.read(
                f"MATCH (ch:{ctx.label('Chunk')}) WHERE ch.id IN $ids\n"
                f"OPTIONAL MATCH (ch)-[:MENTIONS]->(e:{ctx.label('Entity')})\n"
                "RETURN ch.id AS id, collect(e.id) AS entities, ch.parent_key AS parent",
                ids=[e["chunk_id"] for e in community["evidence"]],
            )
            assert len(rows) == len(community["evidence"])
            for row in rows:
                touches = set(row["entities"]) | {row["parent"]}
                assert touches & member_keys, f"{row['id']} evidences nothing in the community"


def test_misc_communities_are_never_sent_to_an_agent(packed, workdir, ctx):
    """They are placed and counted; they are not worth an agent's reading."""
    sent = {
        c["community_id"]
        for path in inputs(workdir)
        for c in json.loads(path.read_text(encoding="utf-8"))["communities"]
    }
    misc = {
        r["id"]
        for r in ctx.read(f"MATCH (c:{ctx.label('Community')}) WHERE c.misc RETURN c.id AS id")
    }
    assert sent and not (sent & misc)
    assert packed["selection"]["misc_skipped"] == len(misc)


# ------------------------------------------------------------------------------ the merge


def answer(workdir, *, cite_foreign: bool = False) -> int:
    """Play `community-summarizer`: write a report for every community of every batch."""
    written = 0
    for path in inputs(workdir):
        payload = json.loads(path.read_text(encoding="utf-8"))
        reports = []
        for community in payload["communities"]:
            evidence = [e["chunk_id"] for e in community["evidence"]]
            assert evidence, "a community with no evidence should never have been packed"
            cited = ["f" * 40] if cite_foreign else evidence[:2]
            names = ", ".join(m["name"] for m in community["members"][:5])
            reports.append(
                {
                    "community_id": community["community_id"],
                    "title": f"Rebalance work around {community['members'][0]['name']}"[:120],
                    "summary": (
                        "This community collects the work items, the KIP and the extracted "
                        f"entities around {names}. The classic rebalance protocol asks the "
                        "clients to compute the assignment, which is what makes a membership "
                        "change stop the world for every member of the group. The material "
                        "here records the decision to move that computation to the group "
                        "coordinator and the problems that motivated it, and it leaves the "
                        "upgrade path from one protocol to the other open."
                    ),
                    "findings": [
                        {
                            "statement": "Client-side assignment is what makes rebalances long.",
                            "evidence_chunk_ids": cited,
                        }
                    ],
                    "rank": 7.5,
                    "rank_reason": "It reaches every consumer and the migration is still open.",
                }
            )
            written += 1
        path.with_name(path.name.replace(".in.", ".out.")).write_text(
            json.dumps({"batch_id": payload["batch_id"], "reports": reports}, indent=2),
            encoding="utf-8",
        )
    return written


@pytest.fixture(scope="module")
def merged(packed, ctx, workdir, embedder):
    answer(workdir)
    report, code = run_merge(
        ctx=ctx,
        batches_dir=workdir["batches"],
        reports_dir=workdir["reports"],
        embedder=embedder,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0, report["rejected_batches"]
    return report


def test_every_report_lands_with_provenance_and_no_finding_without_evidence(merged, ctx):
    assert merged["reports"]["written"] >= 1
    assert merged["findings"]["without_evidence"] == 0
    assert merged["provenance"]["communities_without_provenance"] == 0
    summarised = [c for c in communities(ctx).values() if c["summary"]]
    assert summarised
    for row in summarised:
        assert row["model"] == "opus:community-summarizer"
        assert row["batch_id"].startswith("shard-")
        assert row["extracted_at"] and row["evidence_chunk_ids"]
        assert json.loads(row["findings"][0])["statement"]


def test_the_cited_chunks_exist_and_belong_to_the_community(merged, ctx):
    rows = ctx.read(
        f"MATCH (c:{ctx.label('Community')}) WHERE c.summary IS NOT NULL\n"
        "UNWIND c.evidence_chunk_ids AS cid\n"
        f"MATCH (ch:{ctx.label('Chunk')} {{id: cid}})\n"
        "RETURN count(DISTINCT cid) AS found"
    )
    cited = ctx.read(
        f"MATCH (c:{ctx.label('Community')}) WHERE c.summary IS NOT NULL "
        "UNWIND c.evidence_chunk_ids AS cid RETURN count(DISTINCT cid) AS n"
    )[0]["n"]
    assert rows[0]["found"] == cited > 0


def test_the_vector_index_is_online_and_every_report_is_embedded(merged, ctx):
    index = merged["embedding"]["index"]
    assert index["state"] == "ONLINE" and index["type"] == "VECTOR"
    assert index["name"] == "_Commcommunity_embedding"
    assert index["dim"] == 1024 and index["similarity"].lower() == "cosine"
    assert merged["embedding"]["written"] == merged["embedding"]["needed"] >= 1
    assert all(c["embedded"] for c in communities(ctx).values() if c["summary"])


def test_the_index_meta_says_which_model_made_the_vectors(merged, ctx, settings):
    """A vector index without one is unreadable: a query embedded by another model
    returns nonsense rather than an error."""
    meta = community_graph.read_index_meta(ctx)
    assert meta is not None, "community_embedding has no IndexMeta node"
    assert meta["name"] == "_Commcommunity_embedding"
    assert meta["model"] == settings.embed_model and meta["dim"] == 1024
    assert meta["label"] == "Community" and meta["state"] == "ONLINE"
    live = len([c for c in communities(ctx).values() if c["embedded"]])
    assert meta["live"] == live == merged["embedding"]["index_meta"]["live"]


def test_the_ledger_records_the_member_hash_a_report_was_written_for(merged, workdir, ctx):
    ledger = json.loads((workdir["batches"] / TASK / LEDGER_NAME).read_text(encoding="utf-8"))
    hashes = {c["member_hash"] for c in communities(ctx).values() if c["summary"]}
    assert set(ledger["reported"]) == hashes
    assert all(v["model"] == "opus:community-summarizer" for v in ledger["reported"].values())


def test_a_second_merge_writes_the_same_reports_and_embeds_nothing(merged, ctx, workdir, embedder):
    before = communities(ctx)
    report, code = run_merge(
        ctx=ctx,
        batches_dir=workdir["batches"],
        reports_dir=workdir["reports"],
        embedder=embedder,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0
    assert report["embedding"]["needed"] == 0 and report["embedding"]["written"] == 0
    # nothing embedded and nothing written: a re-merge of the same outputs is a read
    assert report["reports"]["written"] == 0
    assert report["reports"]["unchanged"] == report["reports"]["on_graph"] >= 1
    after = communities(ctx)
    assert {k: v["summary"] for k, v in after.items()} == {
        k: v["summary"] for k, v in before.items()
    }


def test_a_member_set_summarised_at_two_levels_is_named_in_the_report(merged, ctx):
    """Leiden's coarse level need not merge anything; when it does not, say so."""
    dup = merged["cross_level_duplicates"]
    expected = ctx.read(
        f"MATCH (c:{ctx.label('Community')}) WHERE coalesce(c.misc, false) = false\n"
        "WITH c.member_hash AS h, collect(c.id) AS ids WHERE size(ids) > 1 RETURN count(*) AS n"
    )[0]["n"]
    assert dup["total"] == expected
    assert dup["communities"] == sum(len(p["ids"]) for p in dup["pairs"])
    assert all(len(set(p["ids"])) == len(p["ids"]) for p in dup["pairs"])


# ------------------------------------------------------ the incremental half of the step


def test_a_rebuild_keeps_the_reports_of_communities_that_did_not_change(merged, ctx, workdir):
    before = {c["id"]: c["summary"] for c in communities(ctx).values() if c["summary"]}
    report = build(ctx, workdir)

    assert report["written"]["reports_carried_over"] == len(before)
    after = {c["id"]: c["summary"] for c in communities(ctx).values() if c["summary"]}
    assert after == before
    assert all(c["embedded"] for c in communities(ctx).values() if c["summary"])


def test_a_rebuild_gives_every_community_back_its_own_report_not_a_neighbours(merged, ctx, workdir):
    """The provenance half of the carry, and the one a summary-only check cannot see.

    Two communities that hold the same members were summarised separately; a carry keyed on
    the member set alone hands both of them one of the two texts, stamped with a batch id
    that answered about the other. Every field the report states is compared here, not just
    the summary.
    """

    def snapshot() -> dict[str, dict]:
        return {
            r.pop("id"): r
            for r in ctx.read(
                f"MATCH (c:{ctx.label('Community')}) WHERE c.summary IS NOT NULL\n"
                "RETURN c.id AS id, c.title AS title, c.summary AS summary, c.rank AS rank, "
                "c.findings AS findings, c.batch_id AS batch_id, c.model AS model, "
                "c.extracted_at AS extracted_at, c.copied_from AS copied_from"
            )
        }

    before = snapshot()
    # `cross_level_duplicates` covers every *summarisable* community, and a community the
    # corpus gives no evidence for is summarisable and unsummarised; only the pairs where
    # both halves were actually written can say anything about the carry.
    pairs = [
        p["ids"]
        for p in merged["cross_level_duplicates"]["pairs"]
        if all(i in before for i in p["ids"])
    ]
    if not pairs:
        pytest.skip("no member set in the mini corpus carries a report at both levels")
    duplicated = {i for ids in pairs for i in ids}

    build(ctx, workdir)

    after = snapshot()
    assert after == before
    # every one of them still names the batch that answered about it
    assert all(after[i]["batch_id"] == before[i]["batch_id"] for i in duplicated)


def test_after_a_rebuild_there_is_nothing_left_to_summarise(merged, ctx, workdir):
    build(ctx, workdir)
    manifest, code = run_batches(
        ctx=ctx,
        batches_dir=workdir["batches"],
        reports_dir=workdir["reports"],
        shards=2,
        force=True,  # outputs on disk would otherwise refuse the repack
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0
    assert manifest["selection"]["already_summarized_skipped"] >= 1
    assert manifest["selection"]["selected"] == 0


def test_a_repeated_member_set_is_copied_rather_than_sent_to_an_agent_again(merged, ctx, workdir):
    """The other half of "re-summarise only what changed", across levels instead of runs.

    The report is stripped off one of a duplicate pair, exactly as it would be after a
    build that produced that community for the first time. Packing it would buy a second
    agent-written text about one set of things.
    """
    pairs = merged["cross_level_duplicates"]["pairs"]
    if not pairs:
        pytest.skip("Leiden merged something at every level of the mini corpus")
    source, victim = pairs[0]["ids"][0], pairs[0]["ids"][-1]
    ctx.write(
        f"MATCH (c:{ctx.label('Community')} {{id: $id}})\n"
        "REMOVE c.title, c.summary, c.findings, c.finding_statements, c.rank, "
        "c.rank_reason, c.evidence_chunk_ids, c.batch_id, c.model, c.extracted_at, "
        "c.reported_at, c.embedding, c.embed_hash\n"
        "SET c.summarized = false",
        id=victim,
    )
    manifest, code = run_batches(
        ctx=ctx,
        batches_dir=workdir["batches"],
        reports_dir=workdir["reports"],
        shards=2,
        force=True,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0
    assert manifest["selection"]["copied_from_another_level"] == 1
    assert manifest["selection"]["copied_pairs"] == [{"community": victim, "from": source}]
    assert manifest["selection"]["selected"] == 0, "the copy still went to an agent"

    rows = communities(ctx)
    assert rows[victim]["summary"] == rows[source]["summary"]
    assert rows[victim]["embedded"], "a copied report reuses the vector it already has"
    copied = ctx.read(
        f"MATCH (c:{ctx.label('Community')} {{id: $id}}) "
        "RETURN c.copied_from AS copied_from, c.batch_id AS batch_id, c.model AS model",
        id=victim,
    )[0]
    # provenance of the run that wrote the text, plus where this copy came from
    assert copied["copied_from"] == source
    assert copied["batch_id"] == rows[source]["batch_id"]
    assert copied["model"] == "opus:community-summarizer"


def test_a_batch_that_breaks_the_contract_is_quarantined_after_two_retries(ctx, workdir, merged):
    from brain.community.merge import MAX_RETRIES, QUARANTINE_DIR

    target = inputs(workdir)[0]
    out = target.with_name(target.name.replace(".in.", ".out."))
    batch_id = json.loads(target.read_text(encoding="utf-8"))["batch_id"]
    for attempt in range(MAX_RETRIES + 1):
        # a *new* attempt needs a changed output, not a re-read of the same bad one
        out.write_text(
            json.dumps({"batch_id": batch_id, "reports": [], "attempt": attempt}),
            encoding="utf-8",
        )
        report, code = run_merge(
            ctx=ctx,
            batches_dir=workdir["batches"],
            reports_dir=workdir["reports"],
            embed_dim=1024,
            write_report=False,
            echo=lambda _m: None,
        )
        assert code == 1 and report["batches"]["invalid"] == 1
        # the *input* is what lands in retry/ and quarantine/: it is the work to redo
        landed = out.parent / ("retry" if attempt < MAX_RETRIES else QUARANTINE_DIR)
        assert (landed / target.name).is_file(), f"attempt {attempt} went nowhere"
    assert (out.parent / QUARANTINE_DIR / target.name).is_file()
    assert not (out.parent / "retry" / target.name).exists()
