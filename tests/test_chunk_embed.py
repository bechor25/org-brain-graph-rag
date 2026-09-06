"""The measurement harness, against a mocked Ollama — no model, no network."""

from __future__ import annotations

import httpx
import pytest
import respx

from brain.chunk.chunker import Chunk
from brain.chunk.embed import TIMEOUT_FLOOR_S, measure, merge_tables, sample_chunks
from brain.embed.client import EmbedCountMismatch, EmbedDimMismatch, OllamaEmbedder

URL = "http://ollama.test"
DIM = 4


def chunk(text: str, position: int = 0) -> Chunk:
    return Chunk(
        parent_key="KAFKA-1",
        parent_kind="WorkItem",
        kind="description",
        position=position,
        text=text,
    )


def embed_route(tokens_per_text: int = 7):
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        inputs = json.loads(request.content)["input"]
        return httpx.Response(
            200,
            json={
                "model": "bge-m3",
                "embeddings": [[0.1] * DIM for _ in inputs],
                "prompt_eval_count": tokens_per_text * len(inputs),
            },
        )

    return handler


@pytest.fixture
def embedder():
    with OllamaEmbedder(URL, "bge-m3", DIM, timeout=11.0) as e:
        yield e


@respx.mock
def test_the_embedder_keeps_the_real_token_count(embedder):
    respx.post(f"{URL}/api/embed").mock(side_effect=embed_route(tokens_per_text=7))
    vectors = embedder.embed(["a", "b", "c"], batch_size=2)
    assert len(vectors) == 3 and all(len(v) == DIM for v in vectors)
    assert embedder.requests == 2  # 2 + 1
    assert embedder.prompt_tokens == 7 * 3
    assert embedder.request_seconds > 0
    assert embedder.timeout_s == 11.0


@respx.mock
def test_the_dimension_guard_of_the_base_client_still_applies(embedder):
    respx.post(f"{URL}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1] * (DIM + 1)]})
    )
    with pytest.raises(EmbedDimMismatch):
        embedder.embed(["a"])


@respx.mock
def test_a_short_batch_of_vectors_is_an_error_not_a_silent_gap(embedder):
    respx.post(f"{URL}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1] * DIM]})
    )
    with pytest.raises(EmbedCountMismatch):
        embedder.embed(["a", "b"])


def test_the_sample_is_spread_through_the_corpus_not_taken_off_the_front():
    chunks = [chunk("x", i) for i in range(1000)]
    sample = sample_chunks(chunks, 10)
    assert len(sample) == 10
    assert [c.position for c in sample] == [0, 100, 200, 300, 400, 500, 600, 700, 800, 900]
    assert sample_chunks(chunks[:5], 10) == chunks[:5]


@respx.mock
def test_measure_reports_one_row_per_batch_size_and_chooses_the_fastest(embedder):
    respx.post(f"{URL}/api/embed").mock(side_effect=embed_route(tokens_per_text=10))
    chunks = [chunk("a" * 400, i) for i in range(64)]
    throughput = measure(embedder, chunks, batches=(8, 32), echo=lambda _m: None)

    assert [r["batch_size"] for r in throughput.rows] == [8, 32]
    assert [r["batches"] for r in throughput.rows] == [8, 2]
    assert all(r["chunks"] == 64 for r in throughput.rows)
    assert all(r["real_tokens"] == 640 for r in throughput.rows)
    # A mocked round trip is sub-millisecond, so s_per_batch rounds to 0.0 here.
    assert all(r["tokens_per_s"] > 0 and r["s_per_batch"] >= 0 for r in throughput.rows)
    assert throughput.batch_size in (8, 32)
    assert throughput.timeout_s >= TIMEOUT_FLOOR_S


@respx.mock
def test_measure_calibrates_the_chars_per_token_estimate(embedder):
    # 400 chars per chunk = 100 estimated tokens; the model reports 50 → 8 chars/token.
    respx.post(f"{URL}/api/embed").mock(side_effect=embed_route(tokens_per_text=50))
    chunks = [chunk("a" * 400, i) for i in range(8)]
    throughput = measure(embedder, chunks, batches=(8,), echo=lambda _m: None)
    assert throughput.sample_est_tokens == 800
    assert throughput.sample_real_tokens == 400
    assert throughput.chars_per_token == 8.0
    assert throughput.report()["estimate_calibration"]["assumed_chars_per_token"] == 4


@respx.mock
def test_measure_writes_nothing_and_returns_a_table_ready_for_the_report(embedder):
    respx.post(f"{URL}/api/embed").mock(side_effect=embed_route())
    report = measure(
        embedder, [chunk("a" * 100, i) for i in range(4)], batches=(2,), echo=lambda _m: None
    ).report()
    assert set(report) == {
        "sample_chunks",
        "table",
        "chosen_batch_size",
        "chosen_timeout_s",
        "estimate_calibration",
    }
    assert report["sample_chunks"] == 4


def test_merge_tables_keeps_a_row_per_batch_size_and_sample_size():
    """The 200-chunk comparison and a full-corpus pass answer different questions; both
    stay in the report and re-running either replaces only its own rows."""
    sample = [{"batch_size": 32, "chunks": 200, "seconds": 7.5}]
    full = [{"batch_size": 64, "chunks": 10884, "seconds": 416.0}]
    merged = merge_tables(sample, full)
    assert [(r["batch_size"], r["chunks"]) for r in merged] == [(32, 200), (64, 10884)]

    rerun = merge_tables(merged, [{"batch_size": 32, "chunks": 200, "seconds": 6.9}])
    assert len(rerun) == 2
    assert next(r for r in rerun if r["batch_size"] == 32)["seconds"] == 6.9
