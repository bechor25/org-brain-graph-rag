"""The fixed-strategy sweep against a real Neo4j and a real `bge-m3`, on the mini corpus.

The unit tests prove the arithmetic against fake `Result`s; this proves the sweep is wired
to the real `ask()` — that every strategy produces either a run file or an `n/a` record with
a reason, that the recall matcher reaches gold the mini corpus genuinely holds, and that a
second sweep over the same corpus changes nothing but the clock.

Everything lives in the `_EvalRun` label namespace, built from `data/fixtures/mini`, so this
neither reads nor damages the real graph while four agents share one database. The mini
corpus has no community reports and no ADO assignment history, so S5 and S6 are *expected*
to come back `n/a` here — which is the behaviour worth asserting: a strategy with nothing to
work on says so instead of returning an empty answer that looks like a miss.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from brain.chunk import graph as chunk_graph
from brain.chunk.runner import run_chunk
from brain.config import Settings
from brain.embed.client import OllamaEmbedder
from brain.eval import runs as runs_mod
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.graph.runner import run_load, wipe
from brain.graph.schema import drop_schema
from brain.retrieve.context import RetrieveContext
from brain.retrieve.runner import ask
from brain.retrieve.text2cypher import select_example
from tests.retrieve_helpers import build_extract_layer, drop_extract_layer

pytestmark = pytest.mark.live

MINI = Path("data/fixtures/mini")
PREFIX = "_EvalRun"

#: Two questions the mini corpus really answers, one per type, with gold that exists in it.
#: `pair` ties them so the cross-lingual block has something to line up.
QUESTIONS: list[dict] = [
    {
        "id": "lv01",
        "type": "rationale",
        "lang": "en",
        "question": "Why was client-side assignment rejected in KIP-5?",
        "gold_answer": "It caused long rebalances.",
        "gold_evidence": ["KIP-5", "Alternative|keep client side assignment"],
        "difficulty": 2,
        "expected_strategy": "s3",
        "gold_source": "graph",
        "origin": "forged",
        "pair": "kip5",
    },
    {
        "id": "lv02",
        "type": "global",
        "lang": "en",
        "question": "What are the main themes across the corpus?",
        "gold_answer": "—",
        "gold_evidence": ["KIP-5"],
        "difficulty": 3,
        "expected_strategy": "s5",
        "gold_source": "graph",
        "origin": "forged",
    },
]


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
    """`db.awaitIndexes` waits for every index in the database, a neighbour's included."""
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
def ctx(client, embedder, settings, tmp_path_factory):
    gctx = GraphContext(client, prefix=PREFIX)
    _clean(gctx)
    reports = tmp_path_factory.mktemp("eval-run-reports")
    _, code = _retry_await_indexes(
        lambda: run_load(
            client=client,
            canonical_dir=MINI,
            reports_dir=reports,
            prefix=PREFIX,
            write_report=False,
            echo=lambda _m: None,
        )
    )
    assert code == 0
    _, code = _retry_await_indexes(
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
    assert code == 0
    _retry_await_indexes(lambda: build_extract_layer(gctx, embedder, settings.embed_dim))
    with RetrieveContext(client=client, settings=settings, prefix=PREFIX) as c:
        c.ensure_fulltext_index()
        try:
            yield c
        finally:
            _clean(gctx)


def _sweep(ctx, out_dir: Path, *, force: bool = False) -> dict:
    return runs_mod.run_all(
        ctx,
        QUESTIONS,
        strategies=runs_mod.STRATEGIES,
        out_dir=out_dir,
        ask=ask,
        select_example=select_example,
        force=force,
        log=False,
    )


def test_every_pair_lands_as_a_run_file_or_an_na_record_with_a_reason(ctx, tmp_path):
    summary = _sweep(ctx, tmp_path)

    assert summary["expected"] == len(QUESTIONS) * len(runs_mod.STRATEGIES)
    assert len(summary["records"]) == summary["expected"]
    coverage = runs_mod.coverage(QUESTIONS, runs_mod.STRATEGIES, tmp_path)
    assert coverage["missing"] == []

    for record in summary["records"]:
        stored = json.loads(
            runs_mod.run_path(tmp_path, record["qid"], record["strategy"]).read_text()
        )
        assert stored["status"] in ("ok", "n/a")
        if stored["status"] == "n/a":
            assert stored["reason"], f"{stored['qid']}.{stored['strategy']} is n/a with no reason"
            assert stored["result"] is None
        else:
            assert stored["executed"] == runs_mod.base_strategy(stored["strategy"])
            assert stored["latency_ms"] >= 0
            assert stored["context_tokens"] <= stored["budget_tokens"] * 1.5
            assert stored["route"]["strategy"] in ("s1", "s2", "s3", "s4", "s5", "s6", "lookup")


def test_the_entity_anchored_strategy_reaches_gold_the_mini_corpus_really_holds(ctx, tmp_path):
    _sweep(ctx, tmp_path)
    records = runs_mod.load_records(QUESTIONS, runs_mod.STRATEGIES, tmp_path)
    scored = metrics_for(records)
    s3 = next(r for r in scored if r["qid"] == "lv01" and r["strategy"] == "s3")
    assert s3["status"] == "ok"
    assert s3["score"]["recall"] > 0, "S3 anchored on KIP-5 should reach the rejected alternative"


def metrics_for(records):
    from brain.eval import metrics

    return metrics.score_records(records, {})


def test_a_second_sweep_over_the_same_corpus_changes_nothing_but_the_clock(ctx, tmp_path):
    first = _sweep(ctx, tmp_path)
    assert set(first["outcomes"]) == {"new"}
    second = _sweep(ctx, tmp_path)
    assert second["changed"] == []
    assert set(second["outcomes"]) == {"unchanged"}, second["outcomes"]


def test_the_report_is_computed_from_the_files_and_prints_a_matrix(ctx, tmp_path):
    summary = _sweep(ctx, tmp_path)
    report = runs_mod.build_report(
        summary["records"],
        QUESTIONS,
        strategies=runs_mod.STRATEGIES,
        questions_path=tmp_path / "questions.jsonl",
        questions_complete=False,
        questions_total=len(QUESTIONS),
        truth={},
        summary=summary,
        out_dir=tmp_path,
    )
    assert report["coverage"]["complete"] is True
    assert set(report["matrix"]) == set(runs_mod.STRATEGIES)
    assert report["cost"]["s1"]["runs"] == len(QUESTIONS)
    text = "\n".join(runs_mod.summary_lines(report))
    assert "strategy" in text and "coverage:" in text

    path = runs_mod.write_report(report, tmp_path / "eval_retrieval.json")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["step"] == "eval.run.fixed"
    assert "sections" in written and "matrix" in written["sections"]
    assert len(written["runs"]) == 1
