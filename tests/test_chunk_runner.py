"""The runner's pure parts: kind parsing, the distribution report and the checks."""

from __future__ import annotations

import json

import pytest

from brain.chunk.chunker import Chunk, ChunkStats
from brain.chunk.runner import (
    ALL_KINDS,
    _checks,
    _merge_report,
    _percentiles,
    _previous_throughput,
    chunking_report,
    resolve_kinds,
)


def chunk(text: str, kind: str = "section", parent_kind: str = "Document", position: int = 0):
    c = Chunk(
        parent_key="KIP-848",
        parent_kind=parent_kind,
        kind=kind,
        position=position,
        text=text,
    )
    return c


def test_resolve_kinds_accepts_all_a_subset_and_rejects_a_typo():
    assert resolve_kinds("all") == set(ALL_KINDS)
    assert resolve_kinds("") == set(ALL_KINDS)
    assert resolve_kinds("doc, comment") == {"doc", "comment"}
    with pytest.raises(ValueError, match="issues"):
        resolve_kinds("doc,issues")


def test_percentiles_of_an_empty_series_is_a_count_not_a_crash():
    assert _percentiles([]) == {"count": 0}


def test_percentiles_are_taken_from_the_sorted_series():
    stats = _percentiles([5, 1, 3, 2, 4])
    assert stats["min"] == 1 and stats["max"] == 5
    assert stats["p50"] == 3
    assert stats["sum"] == 15 and stats["mean"] == 3.0


def test_chunking_report_splits_the_distribution_by_kind():
    chunks = [
        chunk("a" * 2800, "section"),
        chunk("b" * 400, "comment", "WorkItem", 1),
        chunk("c" * 200, "message", "Commit"),
    ]
    stats = ChunkStats()
    for c in chunks:
        stats.count(c)
    stats.rejected_short = 4
    report = chunking_report(chunks, stats, 1.5)

    assert report["chunks"] == 3
    assert report["by_kind"] == {"comment": 1, "message": 1, "section": 1}
    assert report["by_parent_kind"] == {"Commit": 1, "Document": 1, "WorkItem": 1}
    assert report["by_lang"] == {"en": 3}
    assert report["rejected_short"] == 4
    assert report["tokens_by_kind"]["section"]["p50"] == 700
    assert report["tokens_by_kind"]["comment"]["p50"] == 100
    assert report["duplicate_ids"] == 0
    assert report["seconds"] == 1.5


def test_chunking_report_notices_colliding_ids():
    twice = [chunk("same text here, twice"), chunk("same text here, twice")]
    stats = ChunkStats()
    for c in twice:
        stats.count(c)
    assert chunking_report(twice, stats, 0.1)["duplicate_ids"] == 1


def census(**kw):
    base = {"chunks": 10, "embedded": 10, "missing_embedding": 0, "without_has_chunk": 0}
    return {**base, **kw}


def index(**kw):
    return {"state": "ONLINE", "dim": 1024, **kw}


def checks_by_name(**kw):
    args = {
        "census": census(),
        "index": index(),
        "meta": {"model": "bge-m3"},
        "embedding": {"requested": 0},
        "chunking": {"duplicate_ids": 0},
        "created": {"nodes": 0, "relationships": 0},
        "dim": 1024,
        "model": "bge-m3",
    }
    args.update(kw)
    return {c["name"]: c for c in _checks(**args)}


def test_all_checks_pass_on_a_clean_rerun():
    assert all(c["ok"] for c in checks_by_name().values())


def test_a_chunk_without_a_vector_fails_the_check():
    got = checks_by_name(census=census(embedded=9, missing_embedding=1))
    assert got["every_chunk_has_an_embedding"]["ok"] is False
    assert got["every_chunk_has_an_embedding"]["actual"] == 1


def test_an_index_that_is_still_populating_fails_the_check():
    got = checks_by_name(index=index(state="POPULATING"))
    assert got["vector_index_online"]["ok"] is False


def test_a_dimension_mismatch_between_index_and_settings_fails():
    got = checks_by_name(index=index(dim=768))
    assert got["vector_index_dim_matches_settings"]["ok"] is False


def test_a_missing_index_meta_node_fails_the_model_check():
    got = checks_by_name(meta=None)
    assert got["index_meta_records_the_model"]["ok"] is False
    assert got["index_meta_records_the_model"]["actual"] is None


def test_the_rerun_checks_are_marked_as_only_meaningful_on_a_rerun():
    got = checks_by_name(embedding={"requested": 500}, created={"nodes": 500, "relationships": 9})
    assert got["second_run_embeds_nothing"]["ok"] is False
    assert "rerun" in got["second_run_embeds_nothing"]["note"]
    assert got["second_run_creates_no_nodes"]["ok"] is False


def test_the_report_file_keeps_what_an_earlier_run_wrote(tmp_path):
    path = tmp_path / "chunk.json"
    path.write_text(json.dumps({"step": "chunk", "throughput": {"chosen_batch_size": 16}}))
    merged = _merge_report(path, {"census": {"chunks": 3}})
    assert merged["throughput"] == {"chosen_batch_size": 16}
    assert merged["census"] == {"chunks": 3}


def test_the_full_run_reads_the_batch_size_the_measurement_chose(tmp_path):
    path = tmp_path / "chunk.json"
    assert _previous_throughput(path) is None
    path.write_text(json.dumps({"throughput": {"chosen_batch_size": 64, "chosen_timeout_s": 90}}))
    assert _previous_throughput(path) == {"chosen_batch_size": 64, "chosen_timeout_s": 90}
    path.write_text("not json")
    assert _previous_throughput(path) is None
