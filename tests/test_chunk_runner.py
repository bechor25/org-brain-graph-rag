"""The runner's pure parts: kind parsing, the distribution report and the checks."""

from __future__ import annotations

import json

import pytest

from brain.chunk.chunker import Chunk, ChunkStats
from brain.chunk.runner import (
    ALL_KINDS,
    _checks,
    _coverage_check,
    _embedding_history,
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
    assert report["fence_parity_violations"] == 0


def test_chunking_report_notices_colliding_ids():
    twice = [chunk("same text here, twice"), chunk("same text here, twice")]
    stats = ChunkStats()
    for c in twice:
        stats.count(c)
    assert chunking_report(twice, stats, 0.1)["duplicate_ids"] == 1


def chunking_stub(**kw):
    base = {
        "duplicate_ids": 0,
        "fence_parity_violations": 0,
        "oversize_by_cause": {"atomic_block": 3, "single_unit": 41},
        "tokens_est_within_budget": {"max": 800},
    }
    return {**base, **kw}


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
        "chunking": chunking_stub(),
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


def test_a_chunk_that_split_a_fence_fails_its_check():
    got = checks_by_name(chunking=chunking_stub(fence_parity_violations=3))
    assert got["no_chunk_splits_a_code_fence"]["ok"] is False
    assert got["no_chunk_splits_a_code_fence"]["actual"] == 3


def test_an_oversize_chunk_with_no_reason_fails_its_check():
    got = checks_by_name(chunking=chunking_stub(oversize_by_cause={"atomic_block": 2, "other": 5}))
    assert got["no_oversize_chunk_without_a_reason"]["ok"] is False
    assert got["no_oversize_chunk_without_a_reason"]["actual"] == 5


def test_the_coverage_check_compares_input_fingerprints_with_load_json(tmp_path):
    inputs = {"workitems.jsonl": {"sha256": "aaa"}, "documents.jsonl": {"sha256": "bbb"}}
    (tmp_path / "load.json").write_text(json.dumps({"inputs": inputs}))
    assert _coverage_check(inputs, tmp_path)["ok"] is True

    stale = {**inputs, "workitems.jsonl": {"sha256": "ccc"}}
    check = _coverage_check(stale, tmp_path)
    assert check["ok"] is False
    assert "workitems.jsonl" in check["note"]


def test_the_coverage_check_says_so_when_there_is_nothing_to_compare(tmp_path):
    check = _coverage_check({"workitems.jsonl": {"sha256": "aaa"}}, tmp_path)
    assert check["ok"] is True
    assert "no input fingerprint" in check["actual"]


def embed_run(at: str, requested: int, seconds: float = 1.0):
    return {
        "at": at,
        "requested": requested,
        "written": requested,
        "seconds": seconds,
        "chunks_per_s": 1.0,
        "real_tokens": requested * 10,
        "tokens_per_s": 10,
        "batch_size": 64,
        "chunks_in_scope": 100,
        "skipped_unchanged": 100 - requested,
    }


def test_a_rerun_that_embedded_nothing_never_overwrites_a_real_run(tmp_path):
    path = tmp_path / "chunk.json"
    real = embed_run("2026-09-06T10:00:00+00:00", 870, seconds=33.65)
    kept, history = _embedding_history(path, real)
    assert kept is real and len(history) == 1
    path.write_text(json.dumps({"embedding": kept, "embedding_history": history}))

    rerun = embed_run("2026-09-06T11:00:00+00:00", 0, seconds=0.0)
    kept, history = _embedding_history(path, rerun)
    assert kept["requested"] == 870 and kept["seconds"] == 33.65
    assert kept["superseded_by_a_rerun_that_embedded_nothing"] == rerun["at"]
    assert [h["requested"] for h in history] == [870, 0]


def test_a_run_that_did_work_replaces_the_kept_one(tmp_path):
    path = tmp_path / "chunk.json"
    path.write_text(json.dumps({"embedding": embed_run("t0", 870)}))
    kept, history = _embedding_history(path, embed_run("t1", 1939))
    assert kept["requested"] == 1939
    assert "superseded_by_a_rerun_that_embedded_nothing" not in kept
    assert [h["at"] for h in history] == ["t1"]


def test_the_history_is_capped_so_the_report_does_not_become_a_log(tmp_path):
    path = tmp_path / "chunk.json"
    history = [embed_run(f"t{i}", 0) for i in range(40)]
    path.write_text(json.dumps({"embedding_history": history}))
    _kept, merged = _embedding_history(path, embed_run("new", 5))
    assert len(merged) == 20
    assert merged[-1]["at"] == "new"


def test_a_budgeted_chunk_over_the_target_fails_its_check():
    """The uncuttable ones are excused by name; a chunk the packer sized is not."""
    got = checks_by_name(chunking=chunking_stub(tokens_est_within_budget={"max": 850}))
    assert got["budgeted_chunks_stay_inside_the_target"]["ok"] is False
    assert got["budgeted_chunks_stay_inside_the_target"]["actual"] == 850
    assert checks_by_name()["budgeted_chunks_stay_inside_the_target"]["ok"] is True
