"""Every retrieval strategy, against a real Neo4j and a real `bge-m3`, on the mini corpus.

The whole stack is built in the `_Retr` label namespace: `brain load` and `brain chunk`
from `data/fixtures/mini`, then the five entities and rationale edges `brain extract`
would have produced (`tests/retrieve_helpers.py`). Nothing here reads the production
graph, so the assertions can be exact — "S3 anchored on KIP-5 returns the rejected
alternative" is a fact about a corpus this test owns, not a hope about 13,846 chunks.

What is asserted, per strategy: it returns items, every item is inside the envelope, the
provenance points at chunk ids that exist, and the answer the corpus obviously contains is
in it. The router is asserted separately, in `tests/test_retrieve_route.py`, because it
needs no database at all.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from brain.chunk import graph as chunk_graph
from brain.chunk.runner import run_chunk
from brain.config import Settings
from brain.embed.client import OllamaEmbedder
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import drop_schema
from brain.retrieve.context import RetrieveContext
from brain.retrieve.explain import explain_edge
from brain.retrieve.graph_vector import search_with_context
from brain.retrieve.hybrid import search_chunks
from brain.retrieve.impact import impact
from brain.retrieve.local import local_search
from brain.retrieve.lookup import lookup
from brain.retrieve.runner import ask
from brain.retrieve.temporal import assignees_over_time, changes_between, status_at, timeline
from brain.retrieve.types import Result, RetrieveError
from tests.retrieve_helpers import PREFIX, build_extract_layer, drop_extract_layer

pytestmark = pytest.mark.live

MINI = Path("data/fixtures/mini")


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
def embedder(settings):
    with OllamaEmbedder(
        settings.ollama_url, settings.embed_model, settings.embed_dim, timeout=120
    ) as e:
        assert e.has_model(), f"{settings.embed_model} is not pulled in Ollama"
        yield e


def _clean(gctx: GraphContext) -> None:
    drop_extract_layer(gctx)
    gctx.client.write(f"MATCH (c:{gctx.label('Chunk')}) DETACH DELETE c")
    gctx.client.write(f"MATCH (m:{gctx.label('IndexMeta')}) DETACH DELETE m")
    wipe(gctx)
    chunk_graph.drop_chunk_schema(gctx)
    drop_schema(gctx)
    gctx.client.write(f"DROP INDEX `{gctx.prefix}chunk_text` IF EXISTS")


def _retry_await_indexes(step, attempts: int = 4):
    """Retry a step that failed inside `CALL db.awaitIndexes`.

    Four agents share one Neo4j in this tree and `db.awaitIndexes` waits for *every* index
    in the database, including another agent's scratch namespace mid-population. That is a
    neighbour's index, not a failure of this step, so it is retried rather than reported.
    """
    from neo4j.exceptions import ClientError

    for attempt in range(attempts):
        try:
            return step()
        except ClientError as exc:
            if "awaitIndexes" not in str(exc) or attempt == attempts - 1:
                raise
            time.sleep(5)
    raise AssertionError("unreachable")


@pytest.fixture(scope="module")
def graph(client, embedder, settings, tmp_path_factory):
    """Load, chunk, embed and extract the mini corpus into `_Retr…`. Torn down after."""
    gctx = GraphContext(client, prefix=PREFIX)
    _clean(gctx)
    reports = tmp_path_factory.mktemp("retrieve-reports")
    load_report, code = _retry_await_indexes(
        lambda: run_load(
            client=client,
            canonical_dir=MINI,
            reports_dir=reports,
            prefix=PREFIX,
            write_report=False,
            echo=lambda _m: None,
        )
    )
    assert code == 0, [c for c in load_report["checks"] if not c["ok"]]
    chunk_report, code = _retry_await_indexes(
        lambda: run_chunk(
            client=client,
            embedder=embedder,
            canonical_dir=MINI,
            reports_dir=reports,
            prefix=PREFIX,
            write_report=False,
            echo=lambda _m: None,
        )
    )
    assert code == 0, [c for c in chunk_report["checks"] if not c["ok"]]
    _retry_await_indexes(lambda: build_extract_layer(gctx, embedder, settings.embed_dim))
    try:
        yield gctx
    finally:
        _clean(gctx)


@pytest.fixture(scope="module")
def ctx(graph, client, settings):
    with RetrieveContext(client=client, settings=settings, prefix=PREFIX) as c:
        c.ensure_fulltext_index()
        yield c


# --------------------------------------------------------------------- shared invariants


def check_envelope(result: Result, strategy: str, ctx: RetrieveContext) -> None:
    """Every strategy answers in the same shape, and its citations are real."""
    assert result.strategy == strategy
    assert result.latency_ms >= 0
    assert isinstance(result.truncated, bool)
    assert result.items, "a strategy that returns nothing measures nothing"
    for item in result.items:
        assert item.kind in {
            "Chunk",
            "WorkItem",
            "Document",
            "Person",
            "Change",
            "Container",
            "Entity",
            "Community",
            "Row",
        }
        assert item.key
    ids = [p.chunk_id for i in result.items for p in i.provenance if p.chunk_id]
    if ids:
        rows = ctx.read(
            f"MATCH (c:{ctx.label('Chunk')}) WHERE c.id IN $ids RETURN c.id AS id", ids=ids
        )
        assert {r["id"] for r in rows} == set(ids), "a citation names a chunk that does not exist"


# ------------------------------------------------------------------------------- S1


@pytest.mark.parametrize("mode", ["hybrid", "vector", "fulltext"])
def test_s1_finds_the_kip_text_in_every_mode(ctx, mode):
    result = search_chunks(ctx, "why were long rebalances a problem", k=5, mode=mode, log=False)
    check_envelope(result, "s1", ctx)
    assert all(i.kind == "Chunk" for i in result.items)
    assert any("KIP-5" in (p.source or "") for i in result.items for p in i.provenance)


def test_s1_is_cross_lingual_on_the_same_corpus(ctx):
    """bge-m3 is multilingual; this is the corpus-level check that it actually is here."""
    english = search_chunks(ctx, "move assignment to the group coordinator", k=5, log=False)
    hebrew = search_chunks(ctx, "העברת חישוב השיוך לרכז הקבוצה", k=5, log=False)
    en = {p.source for i in english.items for p in i.provenance}
    he = {p.source for i in hebrew.items for p in i.provenance}
    assert en & he, f"no shared parent between {en} and {he}"


def test_s1_fulltext_survives_a_question_full_of_lucene_metacharacters(ctx):
    result = search_chunks(ctx, "KAFKA-100: what about KIP-5 (fencing)? [urgent]", k=5, log=False)
    check_envelope(result, "s1", ctx)


def test_s1_rejects_an_unknown_mode(ctx):
    with pytest.raises(RetrieveError):
        search_chunks(ctx, "anything", mode="telepathy", log=False)


# ------------------------------------------------------------------------------- S2


def test_s2_returns_parents_with_a_structured_neighbourhood(ctx):
    result = search_with_context(
        ctx, "rebalance storm when the coordinator restarts", k=5, log=False
    )
    check_envelope(result, "s2", ctx)
    anchors = [i for i in result.items if i.kind != "Chunk"]
    assert anchors, "S2 must return the parents, not only the chunks"
    assert any(i.props.get("neighbors") for i in anchors)
    assert {i.key for i in anchors} & {"KAFKA-100", "KAFKA-101", "KIP-5", "a1b2c3d4", "c9d0e1f2"}


def test_s2_two_hops_reach_further_than_one(ctx):
    one = search_with_context(ctx, "consumer group protocol", k=5, hops=1, log=False)
    two = search_with_context(ctx, "consumer group protocol", k=5, hops=2, log=False)
    assert _neighbours(two) >= _neighbours(one)


def _neighbours(result: Result) -> int:
    return sum(i.props.get("neighbor_count", 0) for i in result.items)


# ------------------------------------------------------------------------------- S3


def test_s3_anchored_on_a_key_returns_the_rationale_with_quotes(ctx):
    result = local_search(ctx, "Why was the design in KIP-5 chosen?", k=10, log=False)
    check_envelope(result, "s3", ctx)
    keys = {i.key for i in result.items}
    assert "Decision|move assignment to the group coordinator" in keys
    assert "Alternative|keep client side assignment" in keys, "decision 8: rejects must travel"
    assert "Problem|long rebalances" in keys, "two hops: decision -> problem"
    quotes = [p.quote for i in result.items for p in i.provenance if p.quote]
    assert any("coordinator" in q for q in quotes)


def test_s3_gives_the_same_answer_in_hebrew_because_the_key_is_the_anchor(ctx):
    english = local_search(ctx, "Why was the design in KIP-5 chosen?", k=10, log=False)
    hebrew = local_search(ctx, "למה נבחר התכן ב-KIP-5?", k=10, log=False)
    assert [i.key for i in english.items] == [i.key for i in hebrew.items]


def test_s3_without_a_key_anchors_on_the_nearest_entities(ctx):
    result = local_search(ctx, "clients spend a long time rebalancing", k=8, log=False)
    check_envelope(result, "s3", ctx)
    assert any(i.props.get("anchored_by") == "entity-vector" for i in result.items)


def test_s3_can_be_restricted_to_entity_kinds(ctx):
    result = local_search(ctx, "long rebalances", kinds=["Problem"], k=8, log=False)
    entity_kinds = {i.props.get("kind") for i in result.items if i.kind == "Entity"}
    assert entity_kinds <= {"Problem"}


def test_s3_falls_back_to_a_nodes_own_text_when_nothing_quotes_it(ctx):
    """An `XT-` test has no prose about it; the answer still has to be checkable."""
    result = local_search(ctx, "Which tests cover KAFKA-100?", k=8, log=False)
    check_envelope(result, "s3", ctx)
    assert all(any(p.chunk_id for p in i.provenance) for i in result.items)


def test_s3_returns_the_document_the_question_named_first(ctx):
    """The anchor is pinned: `KIP-5` was outside its own top-10 before (planner decision)."""
    result = local_search(ctx, "Why was the design in KIP-5 chosen?", k=10, log=False)
    assert result.items[0].key == "KIP-5"
    assert result.items[0].props.get("pinned") is True
    assert result.items[0].score >= result.items[1].score


def test_s3_ranks_a_document_by_the_question_when_the_fulltext_indexes_exist(ctx):
    """`Document`/`WorkItem` carry no vector, so their factor comes from `document_text`.

    The indexes are `brain index`'s, not this namespace's, so they are created and dropped
    here by name — never `db.awaitIndexes`, which would wait for every other agent's.
    """
    names = {"Document": "document_text", "WorkItem": "workitem_text"}
    props = {"Document": ("title", "body_md"), "WorkItem": ("title", "description")}
    try:
        for label, index in names.items():
            fields = ", ".join(f"n.`{p}`" for p in props[label])
            ctx.client.write(
                f"CREATE FULLTEXT INDEX `{ctx.index(index)}` IF NOT EXISTS "
                f"FOR (n:{ctx.label(label)}) ON EACH [{fields}]"
            )
            assert ctx.await_index(ctx.index(index)) == "ONLINE"
        result = local_search(
            ctx, "Which client work implements the KIP-5 protocol?", k=10, log=False
        )
        check_envelope(result, "s3", ctx)
        assert any("db.index.fulltext.queryNodes" in c for c in result.cypher_used)
        factors = [
            i.props["text_factor"] for i in result.items if i.props.get("text_factor") is not None
        ]
        assert factors, "no Document or WorkItem came back to carry the lexical factor"
        assert max(factors) > 1.0, "the index answered but nothing was ranked by it"
        assert all(i.props.get("text_factor") is None for i in result.items if i.kind == "Entity")
    finally:
        for index in names.values():
            ctx.client.write(f"DROP INDEX `{ctx.index(index)}` IF EXISTS")


# ------------------------------------------------------------------------------- S6


def test_status_at_reads_the_changelog_not_the_current_status(ctx):
    before = status_at(ctx, "KAFKA-100", "2024-01-20", log=False)
    after = status_at(ctx, "KAFKA-100", "2024-03-10", log=False)
    check_envelope(before, "s6", ctx)
    assert before.items[0].props["status"] == "In Progress"
    assert after.items[0].props["status"] == "Resolved"


def test_status_at_before_the_item_existed_says_so(ctx):
    result = status_at(ctx, "KAFKA-100", "2023-01-01", log=False)
    assert result.items[0].props["status"] is None
    assert "did not exist" in result.items[0].props["basis"]


def test_status_at_before_the_first_transition_returns_the_initial_status(ctx):
    result = status_at(ctx, "KAFKA-100", "2024-01-11", log=False)
    assert result.items[0].props["status"] == "Open"
    assert "initial status" in result.items[0].props["basis"]


def test_status_at_on_an_unknown_key_raises(ctx):
    with pytest.raises(RetrieveError):
        status_at(ctx, "KAFKA-999999", "2024-01-01", log=False)


def test_timeline_is_ordered_and_covers_every_field(ctx):
    result = timeline(ctx, "KAFKA-100", log=False)
    check_envelope(result, "s6", ctx)
    stamps = [i.props["at"] for i in result.items if i.kind == "Row"]
    assert stamps == sorted(stamps)
    assert {i.props["field"] for i in result.items if i.kind == "Row"} >= {"status", "assignee"}


def test_changes_between_uses_the_version_window_not_string_order(ctx):
    result = changes_between(ctx, "clients", "3.6", "3.7", log=False)
    check_envelope(result, "s6", ctx)
    assert "KAFKA-100" in {i.key for i in result.items}
    with pytest.raises(RetrieveError):
        changes_between(ctx, "clients", "3.7", "3.6", log=False)


def test_assignees_over_time_marks_the_open_interval_as_current(ctx):
    result = assignees_over_time(ctx, "KAFKA-100", log=False)
    check_envelope(result, "s6", ctx)
    people = [i for i in result.items if i.kind == "Person"]
    assert people and any(i.props["current"] for i in people)


# --------------------------------------------------------------------- lookup / explain


def test_lookup_returns_the_node_and_a_capped_neighbourhood(ctx):
    result = lookup(ctx, "KIP-5", log=False)
    check_envelope(result, "lookup", ctx)
    head = result.items[0]
    assert head.kind == "Document" and head.key == "KIP-5"
    assert head.props["neighbor_count"] <= 50
    assert "DECIDES:out" in head.props["neighbors"]


def test_lookup_finds_a_commit_a_person_and_an_entity(ctx):
    assert lookup(ctx, "a1b2c3d4", log=False).items[0].kind == "Change"
    assert lookup(ctx, "clients", log=False).items[0].kind == "Container"
    entity = lookup(ctx, "Problem|long rebalances", log=False)
    assert entity.items[0].kind == "Entity"


def test_lookup_on_an_invented_key_raises_rather_than_returning_empty(ctx):
    with pytest.raises(RetrieveError):
        lookup(ctx, "KAFKA-999999", log=False)


def test_explain_edge_returns_the_provenance_of_a_rejection(ctx):
    result = explain_edge(ctx, "KIP-5", "Alternative|keep client side assignment", log=False)
    check_envelope(result, "lookup", ctx)
    edge = result.items[0]
    assert edge.props["type"] == "REJECTS"
    assert edge.props["llm_derived"] is True
    assert edge.props["missing_provenance"] == []


def test_explain_edge_with_no_direct_edge_reports_the_path_instead(ctx):
    result = explain_edge(ctx, "KAFKA-102", "Problem|long rebalances", log=False)
    row = result.items[0]
    assert row.title == "no direct edge"
    assert "path_types" in row.props


# ------------------------------------------------------------------------------ impact


def test_impact_reports_open_issues_tests_docs_and_commits(ctx):
    result = impact(ctx, "KAFKA-100", depth=2, log=False)
    check_envelope(result, "s3", ctx)
    head = result.items[0]
    assert head.props["anchor"] == "KAFKA-100"
    categories = {i.props.get("category") for i in result.items}
    assert "open_issue" in categories, "KAFKA-101 is open and blocked by KAFKA-100"
    assert "test" in categories, "XT-1 tests KAFKA-100"
    assert "document" in categories, "KIP-5"
    runs = [
        i.props.get("last_run_status") for i in result.items if i.props.get("category") == "test"
    ]
    assert any(runs), "a test with an execution must report its last run status"


def test_impact_on_a_component_walks_in_component(ctx):
    result = impact(ctx, "clients", depth=1, log=False)
    check_envelope(result, "s3", ctx)
    assert result.items[0].props["anchor_label"] == "Component"


def test_impact_answers_the_same_question_the_same_way_twice(ctx):
    """The file slice used to come off an unordered `collect`: same question, other files."""
    first = impact(ctx, "clients", depth=2, log=False)
    second = impact(ctx, "clients", depth=2, log=False)
    assert [(i.kind, i.key) for i in first.items] == [(i.kind, i.key) for i in second.items]
    assert first.items[0].props["files"] == second.items[0].props["files"]
    assert first.cypher_used == second.cypher_used


def test_a_covering_test_is_a_row_that_cites_its_execution(ctx):
    """Not a `WorkItem`: `lookup` cannot resolve an XT- key, and a run is not a quote."""
    result = impact(ctx, "KAFKA-100", depth=2, log=False)
    tests = [i for i in result.items if i.props.get("category") == "test"]
    assert tests, "XT-1 tests KAFKA-100"
    assert {i.kind for i in tests} == {"Row"}
    entries = [p for i in tests for p in i.provenance]
    assert entries and all(p.source_kind == "row" for p in entries)
    assert all(p.quote for p in entries), "a row cites what it recorded, or says it never ran"


def test_a_derived_answer_never_calls_the_nodes_own_text_a_quote(ctx):
    """S6 reads events, not prose. Its citations must say which kind of evidence they are."""
    for result in (
        status_at(ctx, "KAFKA-100", "2024-01-20", log=False),
        timeline(ctx, "KAFKA-100", log=False),
        assignees_over_time(ctx, "KAFKA-100", log=False),
    ):
        kinds = {p.source_kind for i in result.items for p in i.provenance}
        assert kinds, "a derived answer with no provenance at all is not checkable"
        assert kinds <= {"row", "node-text"}, kinds


# -------------------------------------------------------------------------------- ask


ASK_CASES = [
    ("Which tests cover KAFKA-100 and what was their last execution status?", "s3"),
    ("Why was the design in KIP-5 chosen?", "s3"),
    ("What alternatives were rejected in KIP-5 and why?", "s3"),
    ("What was the status of KAFKA-100 on 2024-01-20?", "s6"),
    ("Who was assigned to KAFKA-100 over time?", "s6"),
    ("What changed in `clients` between 3.6 and 3.7?", "s6"),
    ("What depends on the group coordinator?", "s2"),
    ("If we change `clients`, which open issues and tests are affected?", "s2"),
    ("KAFKA-101", "lookup"),
    ("אילו טסטים מכסים את KAFKA-100 ומה סטטוס ההרצה האחרון שלהם?", "s3"),
    ("למה נבחר התכן ב-KIP-5?", "s3"),
    ("מה השתנה ב-`clients` בין 3.6 ל-3.7?", "s6"),
    ("מה היה הסטטוס של KAFKA-100 בתאריך 2024-01-20?", "s6"),
]


@pytest.mark.parametrize(("question", "strategy"), ASK_CASES)
def test_ask_routes_executes_and_answers(ctx, question, strategy):
    result = ask(ctx, question, k=8)
    assert result.strategy == strategy, result.route
    check_envelope(result, strategy, ctx)


def test_ask_logs_every_call(ctx, tmp_path):
    from brain.retrieve.log import read_log

    path = tmp_path / "retrieval.jsonl"
    ask(ctx, "Why was the design in KIP-5 chosen?", log_path=path)
    ask(ctx, "KAFKA-101", log_path=path)
    records = read_log(path)
    assert len(records) == 2
    assert all(r["hit_ids"] and r["latency_ms"] >= 0 and r["cypher"] for r in records)
    assert records[0]["route"]["strategy"] == "s3"


def test_no_synthetic_excludes_the_xray_layer(graph, client, settings):
    """Plan decision 6: synthetic is in by default and can be switched off."""
    with RetrieveContext(
        client=client, settings=settings, prefix=PREFIX, include_synthetic=False
    ) as ctx:
        clause = ctx.synthetic_clause("c")
    assert "synthetic" in clause
