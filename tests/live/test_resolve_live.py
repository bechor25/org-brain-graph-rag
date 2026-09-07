"""`brain resolve` against the real Neo4j and the real Ollama, on the mini corpus.

Everything runs in the `_Res…` label namespace, so `make smoke` never reads or deletes
the graph a production `brain load` built.

The fixture corpus is `data/fixtures/mini/` with one change made in `tmp_path`: each
identity becomes its own `Person` record. That is what `brain canon` actually produces on
the real corpus (2,187 persons, one identity each); the committed fixture pre-merges Jun
Rao's three identities, which is the *output* of this step, not its input. Splitting it
here is what lets the smoke test prove the brief's acceptance criterion — jrao in three
identities becomes one node — instead of asserting that a hand-written file already said so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.config import Settings
from brain.embed.client import OllamaEmbedder
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import drop_schema
from brain.resolve import graph as resolve_graph
from brain.resolve.build import TASK, run_build
from brain.resolve.ledger import LEDGER_NAME, ResolutionLedger
from brain.resolve.runner import run_resolve

pytestmark = pytest.mark.live

MINI = Path("data/fixtures/mini")
PREFIX = "_Res"

JRAO = ["jira:jrao", "git:junrao@example.org", "confluence:rao.jun"]


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="module")
def split_corpus(tmp_path_factory) -> Path:
    """`data/fixtures/mini`, one Person record per identity — what canon really emits."""
    out = tmp_path_factory.mktemp("mini-split")
    for path in MINI.glob("*.jsonl"):
        if path.name != "persons.jsonl":
            (out / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    records = []
    for line in (MINI / "persons.jsonl").read_text(encoding="utf-8").splitlines():
        person = json.loads(line)
        for identity in person["identities"]:
            records.append(
                {
                    "id": f"{identity['source']}:{identity['key']}",
                    "identities": [identity],
                    "resolved": False,
                    "synthetic": person.get("synthetic", False),
                }
            )
    (out / "persons.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    return out


@pytest.fixture(scope="module")
def ctx(settings):
    client = GraphClient(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database
    )
    client.verify()
    c = GraphContext(client, prefix=PREFIX)
    wipe(c)
    drop_schema(c)
    resolve_graph.drop_resolve_schema(c)
    try:
        yield c
    finally:
        wipe(c)
        drop_schema(c)
        resolve_graph.drop_resolve_schema(c)
        client.close()


@pytest.fixture(scope="module")
def workdir(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("resolve-work")
    dirs = {name: root / name for name in ("canonical", "reports", "batches", "eval")}
    for path in dirs.values():
        path.mkdir()
    return dirs


def load(ctx, split_corpus, workdir):
    report, code = run_load(
        client=ctx.client,
        canonical_dir=split_corpus,
        reports_dir=workdir["reports"],
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0, [c for c in report["checks"] if not c["ok"]]
    return report


def resolve(ctx, split_corpus, workdir, *, tiers, kinds=("person",), dry_run=False, embedder=None):
    """`canonical_dir` is the corpus directory, exactly as in production: the ledger has to
    land beside the canonical files, because that is where the next `brain load` reads it."""
    report, code = run_resolve(
        client=ctx.client,
        canonical_dir=split_corpus,
        reports_dir=workdir["reports"],
        batches_dir=workdir["batches"],
        kinds=list(kinds),
        tiers=tiers,
        embedder=embedder,
        prefix=PREFIX,
        dry_run=dry_run,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0
    return report


@pytest.fixture(scope="module")
def embedder(settings):
    with OllamaEmbedder(settings.ollama_url, settings.embed_model, settings.embed_dim) as e:
        yield e


@pytest.fixture(scope="module")
def loaded(ctx, split_corpus, workdir):
    return load(ctx, split_corpus, workdir)


def persons(ctx) -> dict[str, dict]:
    rows = ctx.read(
        f"MATCH (p:{ctx.label('Person')}) RETURN p.id AS id, p.identity_keys AS identity_keys, "
        "p.aliases AS aliases, p.merged_from AS merged_from, p.resolved AS resolved, "
        "p.resolution_tier AS tier"
    )
    return {r["id"]: r for r in rows}


# ------------------------------------------------------------------ tier 1, deterministic


def test_the_split_fixture_starts_as_one_person_per_identity(loaded, ctx):
    assert loaded["census"]["nodes_by_label"]["Person"] == 6
    assert set(JRAO) <= set(persons(ctx))


def test_a_dry_run_proposes_the_merge_and_writes_nothing(loaded, ctx, split_corpus, workdir):
    report = resolve(ctx, split_corpus, workdir, tiers=[1], dry_run=True)
    tier1 = report["person"]["tier1"]

    assert tier1["pairs"] >= 1 and tier1["applied"] is False
    assert len(persons(ctx)) == 6
    assert not (split_corpus / LEDGER_NAME).exists()
    # Relationship types are not namespaced, so this has to reach SAME_AS through a
    # prefixed label or it counts the production graph's links too.
    assert ctx.read(f"MATCH (:{ctx.label('Person')})-[r:SAME_AS]->() RETURN count(r) AS n") == [
        {"n": 0}
    ]


def test_tier_one_merges_dlee_on_display_plus_the_issue_behind_her_commit(
    loaded, ctx, split_corpus, workdir
):
    """The only deterministic pair in the fixture, and it needs the derived WORKED_ON edge:
    Dana Lee's git identity touches commit shas, her Jira identity touches issue keys, and
    the two key spaces meet only through the issue her commit resolves."""
    report = resolve(ctx, split_corpus, workdir, tiers=[1])
    tier1 = report["person"]["tier1"]

    assert tier1["by_rule"] == {"display_and_activity": 1}
    people = persons(ctx)
    assert "git:dlee@example.org" not in people
    assert people["jira:dlee"]["merged_from"] == ["git:dlee@example.org"]
    assert people["jira:dlee"]["resolved"] is True
    assert people["jira:dlee"]["tier"] == 1
    assert set(people["jira:dlee"]["identity_keys"]) == {"jira:dlee", "git:dlee@example.org"}
    # Jun Rao's commit resolves no issue, so tier 1 cannot reach his git identity.
    assert "git:junrao@example.org" in people


def test_the_ledger_records_every_swallowed_identity(ctx, split_corpus, workdir):
    ledger = ResolutionLedger.load(split_corpus)
    assert ledger.available
    assert ledger.canonical("person", "git:dlee@example.org") == "jira:dlee"
    assert ledger.persons["git:dlee@example.org"]["tier"] == 1
    assert ledger.persons["git:dlee@example.org"]["rule"] == "display_and_activity"


def test_a_second_tier_one_run_merges_nothing(ctx, split_corpus, workdir):
    before = len(persons(ctx))
    report = resolve(ctx, split_corpus, workdir, tiers=[1])
    assert report["person"]["tier1"]["identities_merged"] == 0
    assert len(persons(ctx)) == before


# --------------------------------------------------------------------- tier 2, embedding


@pytest.fixture(scope="module")
def after_tier2(ctx, split_corpus, workdir, embedder):
    return resolve(ctx, split_corpus, workdir, tiers=[2], embedder=embedder)


def test_tier_two_auto_merges_the_identical_display_and_bands_the_reordered_one(after_tier2, ctx):
    """ "Jun Rao" == "Jun Rao" is 1.0 and merges; "Rao, Jun" is 0.89 — inside the band the
    adjudicator exists for, which is where a name permutation belongs."""
    tier2 = after_tier2["person"]["tier2"]
    auto = {(p["a"], p["b"]) for p in tier2["auto_pairs"]}
    grey = {(p["a"], p["b"]) for p in tier2["grey_pairs_sample"]}

    assert tier2["embedding"]["evidence_in_vector"] == 0
    assert ("git:junrao@example.org", "jira:jrao") in auto
    assert ("confluence:rao.jun", "jira:jrao") in grey
    people = persons(ctx)
    assert "git:junrao@example.org" not in people
    assert people["jira:jrao"]["tier"] == 2
    assert "confluence:rao.jun" in people


def test_a_second_tier_two_run_merges_nothing_and_re_embeds_only_the_survivor(
    ctx, split_corpus, workdir, embedder
):
    before = len(persons(ctx))
    report = resolve(ctx, split_corpus, workdir, tiers=[2], embedder=embedder)

    # Exactly the node the previous merge changed, not the whole label.
    assert report["person"]["tier2"]["embedding"]["stale"] == 1
    assert report["person"]["tier2"]["identities_merged"] == 0
    assert len(persons(ctx)) == before

    third = resolve(ctx, split_corpus, workdir, tiers=[2], embedder=embedder)
    assert third["person"]["tier2"]["embedding"]["stale"] == 0
    assert third["person"]["tier2"]["identities_merged"] == 0


# -------------------------------------------------------------- tier 3, the adjudication


@pytest.fixture(scope="module")
def built(ctx, workdir, embedder, after_tier2):
    manifest, code = run_build(
        ctx=ctx,
        batches_dir=workdir["batches"],
        kinds=["person"],
        embedder=embedder,
        k=25,
        shards=2,
        echo=lambda _m: None,
    )
    assert code == 0
    return manifest


def test_every_batch_is_indented_within_budget_and_at_most_25_pairs(built, workdir):
    root = workdir["batches"] / TASK / "person"
    files = sorted(root.glob("shard-*/[0-9][0-9][0-9].in.json"))
    assert files, built
    for path in files:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text)
        assert text.startswith("{\n  ")
        assert len(text.encode("utf-8")) <= 40_000
        assert 1 <= payload["pair_count"] <= 25
        assert payload["schema_sha256"]
        for pair in payload["pairs"]:
            for sideward in ("a", "b"):
                assert len(payload["pairs"][0][sideward]["evidence"]) <= 3
            assert 0.80 <= pair["similarity"] < 0.92


def test_each_shard_gets_a_status_file(built, workdir):
    for shard in built["shards"]:
        assert (workdir["batches"] / TASK / "person" / shard / "status.json").is_file()


def test_a_rebuild_over_a_working_shard_is_refused_not_raced(built, workdir, ctx, embedder):
    from brain.resolve.build import BuildError

    shard = built["shards"][0]
    status = workdir["batches"] / TASK / "person" / shard / "status.json"
    status.write_text(json.dumps({"done": [f"{shard}/001"], "failed": []}), encoding="utf-8")
    with pytest.raises(BuildError, match="already reports finished batches"):
        run_build(
            ctx=ctx,
            batches_dir=workdir["batches"],
            kinds=["person"],
            embedder=embedder,
            k=25,
            echo=lambda _m: None,
        )
    status.write_text(json.dumps({"done": [], "failed": []}), encoding="utf-8")


def answer(workdir, verdicts: dict[tuple[str, str], str]) -> int:
    """Play `entity-adjudicator`: answer every pair of every batch on disk."""
    written = 0
    root = workdir["batches"] / TASK / "person"
    for path in sorted(root.glob("shard-*/[0-9][0-9][0-9].in.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        decisions = []
        for pair in payload["pairs"]:
            verdict = verdicts.get((pair["a"]["id"], pair["b"]["id"]), "unsure")
            decisions.append(
                {
                    "pair_id": pair["pair_id"],
                    "verdict": verdict,
                    "reason": "the two identities spell one Kafka committer's name",
                }
            )
        path.with_name(path.name.replace(".in.", ".out.")).write_text(
            json.dumps({"batch_id": payload["batch_id"], "decisions": decisions}, indent=2),
            encoding="utf-8",
        )
        written += len(decisions)
    return written


@pytest.fixture(scope="module")
def adjudicated(ctx, split_corpus, workdir, built):
    answer(workdir, {("confluence:rao.jun", "jira:jrao"): "same"})
    return resolve(ctx, split_corpus, workdir, tiers=[3])


def test_only_the_same_verdicts_merge(adjudicated, ctx):
    tier3 = adjudicated["person"]["tier3"]
    assert tier3["verdicts"]["same"] == 1
    assert tier3["verdicts"]["different"] == 0
    assert tier3["identities_merged"] == 1
    assert tier3["batches_failed"] == []


def test_jraos_three_identities_are_now_one_node(adjudicated, ctx):
    """The brief's smoke criterion, end to end: three systems, three tiers, one person."""
    people = persons(ctx)
    assert "confluence:rao.jun" not in people and "git:junrao@example.org" not in people
    node = people["jira:jrao"]
    assert set(node["identity_keys"]) == set(JRAO)
    assert node["merged_from"] == ["confluence:rao.jun", "git:junrao@example.org"]
    assert node["aliases"] == ["Rao, Jun"]
    assert node["resolved"] is True and node["tier"] == 3


def test_the_merged_person_kept_every_edge_of_the_identities_it_swallowed(adjudicated, ctx):
    rows = ctx.read(
        f"MATCH (p:{ctx.label('Person')} {{id: 'jira:jrao'}})--(x) "
        "RETURN DISTINCT labels(x)[0] AS label, count(*) AS n"
    )
    labels = {r["label"] for r in rows}
    # the Jira identity reported/commented on issues, the git one authored a commit,
    # the Confluence one authored the KIP page
    assert {"_ResWorkItem", "_ResCommit", "_ResDocument"} <= labels


def test_no_same_as_self_loop_survives_the_merge(adjudicated, ctx):
    assert ctx.read(f"MATCH (n:{ctx.label('Person')})-[r:SAME_AS]->(n) RETURN count(r) AS n") == [
        {"n": 0}
    ]


def test_a_second_merge_decisions_run_merges_nothing(ctx, split_corpus, workdir, adjudicated):
    before = len(persons(ctx))
    report = resolve(ctx, split_corpus, workdir, tiers=[3])
    assert report["person"]["tier3"]["identities_merged"] == 0
    assert len(persons(ctx)) == before


def test_a_batch_that_breaks_the_contract_is_quarantined_after_two_retries(
    ctx, split_corpus, workdir
):
    from brain.resolve.decisions import MAX_RETRIES, QUARANTINE_DIR

    root = workdir["batches"] / TASK / "person"
    target = sorted(root.glob("shard-*/[0-9][0-9][0-9].in.json"))[0]
    out = target.with_name(target.name.replace(".in.", ".out."))
    broken = {
        "batch_id": json.loads(target.read_text(encoding="utf-8"))["batch_id"],
        "decisions": [{"pair_id": "f" * 16, "verdict": "same", "reason": "a pair id nobody asked"}],
    }
    for _ in range(MAX_RETRIES + 1):
        out.write_text(json.dumps(broken), encoding="utf-8")
        report = resolve(ctx, split_corpus, workdir, tiers=[3])
        assert report["person"]["tier3"]["batches_failed"]
        broken = json.loads((out.parent / "retry" / out.name).read_text(encoding="utf-8"))
    assert (out.parent / QUARANTINE_DIR / out.name).is_file()


# ------------------------------------------------------- the criterion the ledger exists for


def test_reloading_the_canonical_files_does_not_resurrect_the_merged_identities(
    ctx, workdir, split_corpus, adjudicated
):
    """load -> resolve -> load: the Person count does not move (brief 08 decision 9)."""
    before = persons(ctx)
    # six identities in, three people out: jrao (3), dlee (2), mrivera (1)
    assert len(before) == 3

    report, code = run_load(
        client=ctx.client,
        canonical_dir=split_corpus,
        reports_dir=workdir["reports"],
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    assert code == 0, [c for c in report["checks"] if not c["ok"]]

    after = persons(ctx)
    assert len(after) == len(before)
    assert set(after) == set(before)
    assert report["census"]["nodes_by_label"]["Person"] == 3
    assert report["resolution"]["identities_folded"] == 3
    assert report["last_run"]["counters"]["nodes_created"] == 0
    assert set(after["jira:jrao"]["identity_keys"]) == set(JRAO)
    assert after["jira:jrao"]["resolved"] is True


def test_the_reload_points_every_edge_at_the_survivor(ctx):
    """The commit `git:junrao@example.org` authored still hangs off the Jira person."""
    rows = ctx.read(
        f"MATCH (p:{ctx.label('Person')})-[:AUTHORED]->(c:{ctx.label('Commit')}) "
        "RETURN p.id AS id, c.sha AS sha ORDER BY sha"
    )
    assert "jira:jrao" in {r["id"] for r in rows}
    orphaned = ctx.read(
        f"MATCH (p:{ctx.label('Person')}) WHERE p.id IN $gone RETURN p.id AS id",
        gone=["git:junrao@example.org", "confluence:rao.jun"],
    )
    assert orphaned == []


def test_reset_drops_the_person_layer_and_load_rebuilds_it(ctx, split_corpus, workdir):
    """There is no unmerge, so a threshold change means reset -> load -> resolve."""
    from brain.resolve.ledger import ResolutionLedger
    from brain.resolve.reset import run_reset

    plan, code = run_reset(
        ctx, canonical_dir=split_corpus, kind="person", confirmed=False, echo=lambda _m: None
    )
    assert code == 0 and plan["applied"] is False
    assert plan["nodes_to_delete"] == 3 and plan["ledger_rows_to_drop"] == 3
    assert len(persons(ctx)) == 3  # a dry preview changes nothing

    done, code = run_reset(
        ctx, canonical_dir=split_corpus, kind="person", confirmed=True, echo=lambda _m: None
    )
    assert code == 0 and done["applied"] is True
    assert persons(ctx) == {}
    assert ResolutionLedger.load(split_corpus).counts()["persons"] == 0
    # …and nothing else went with them
    assert ctx.read(f"MATCH (w:{ctx.label('WorkItem')}) RETURN count(w) AS n") == [{"n": 7}]

    report, code = run_load(
        client=ctx.client,
        canonical_dir=split_corpus,
        reports_dir=workdir["reports"],
        prefix=PREFIX,
        write_report=False,
        echo=lambda _m: None,
    )
    # A rebuild legitimately creates everything, so only that one check fails — and it
    # is the one `run_load` deliberately keeps out of the exit code.
    assert code == 0
    assert [c["name"] for c in report["checks"] if not c["ok"]] == ["second_run_creates_nothing"]
    assert report["census"]["nodes_by_label"]["Person"] == 6
    assert report["resolution"]["identities_folded"] == 0


# ------------------------------------------------------------------------------ entities


@pytest.fixture(scope="module")
def entities(ctx):
    """Three `Feature`s in one KIP: two phrasings of one thing, and a different thing."""
    rows = [
        {
            "id": "Feature|versioned state store",
            "name": "Versioned State Stores",
            "description": "A store that keeps history.",
            "kind": "Feature",
        },
        {
            "id": "Feature|the versioned state store",
            "name": "The versioned state stores",
            "description": "A store that keeps history for a retention period.",
            "kind": "Feature",
        },
        {
            "id": "Feature|versioned state store upgrade path",
            "name": "Versioned state store upgrade path",
            "description": "How an existing store becomes versioned.",
            "kind": "Feature",
        },
        {
            "id": "Problem|versioned state store",
            "name": "Versioned state stores",
            "description": "A store that keeps history.",
            "kind": "Problem",
        },
    ]
    ctx.write_rows(
        f"UNWIND $rows AS row MERGE (e:{ctx.label('Entity')} {{id: row.id}}) SET e += row", rows
    )
    yield rows
    ctx.client.write(f"MATCH (e:{ctx.label('Entity')}) DETACH DELETE e")


def test_a_feature_named_after_a_kip_gets_a_link_and_stays_a_separate_node(
    ctx, entities, split_corpus, workdir
):
    ctx.write_rows(
        f"UNWIND $rows AS row MATCH (d:{ctx.label('Document')} {{key: row.key}})\n"
        "SET d.title = row.title",
        [{"key": "KIP-5", "title": "KIP-5: Versioned State Stores"}],
    )
    report = resolve(ctx, split_corpus, workdir, tiers=[1], kinds=["entity"])
    links = report["entity"]["tier1"]["kip_title_links"]

    assert links["count"] >= 1
    assert ("Feature|versioned state store", "KIP-5") in {
        (r["entity"], r["document"]) for r in links["sample"]
    }
    # a link, never a merge: both nodes are still there
    assert ctx.read(
        f"MATCH (e:{ctx.label('Entity')})-[:SAME_AS]->(d:{ctx.label('Document')}) "
        "RETURN e.id AS entity, d.key AS document ORDER BY entity"
    )
    assert ctx.read(f"MATCH (e:{ctx.label('Entity')}) RETURN count(e) AS n") == [{"n": 4}]
    # …and a Problem of the same name is not a Feature, so it is not linked
    assert "Problem|versioned state store" not in {r["entity"] for r in links["sample"]}


def test_entity_tier_two_stays_inside_the_kind_and_merges_only_the_same_words(
    ctx, entities, split_corpus, workdir, embedder
):
    report = resolve(ctx, split_corpus, workdir, tiers=[2], kinds=["entity"], embedder=embedder)
    tier2 = report["entity"]["tier2"]
    merged = {(p["a"], p["b"]) for p in tier2["auto_pairs"]}

    # "versioned state stores" and "the versioned state stores" differ by an article
    assert ("Feature|the versioned state store", "Feature|versioned state store") in merged
    # a different Feature is not merged, however close it reads
    assert "Feature|versioned state store upgrade path" not in {i for p in merged for i in p}
    # and the same name under another kind is never even a candidate
    assert all(a.split("|")[0] == b.split("|")[0] for a, b in merged), (
        "a merge crossed an Entity kind"
    )
    survivors = ctx.read(f"MATCH (e:{ctx.label('Entity')}) RETURN e.id AS id ORDER BY id")
    assert "Problem|versioned state store" in {r["id"] for r in survivors}


def test_the_entity_survivor_is_the_one_with_the_most_evidence(ctx, entities):
    from brain.resolve.models import Evidence
    from brain.resolve.names import survivor
    from brain.resolve.runner import evidence_weight
    from tests.resolve_helpers import entity as make_entity

    thin = make_entity("Feature|a", "a")
    fat = make_entity("Feature|the a", "the a")
    fat.evidence = [Evidence(role="MENTIONS", key=f"{i:040d}", title="q") for i in range(3)]
    by_id = {c.id: c for c in (thin, fat)}
    # without evidence the shorter name wins; with it, the better-evidenced node does
    assert survivor("entity", by_id) == "Feature|a"
    assert survivor("entity", by_id, evidence_weight(by_id)) == "Feature|the a"
