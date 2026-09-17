"""Layer 2 arithmetic, on synthetic `Result`s — no graph, no embedder, no clock.

Every number Plan 3 publishes about retrieval comes out of `brain/eval/metrics.py`, so the
matcher and the matrix are the two things that have to be right before a single question is
run. Both are pure functions over plain dicts here, which is what lets a four-item fake
prove a rule that the real corpus would only ever illustrate.
"""

from __future__ import annotations

import pytest

from brain.eval import metrics


def chunk_item(key: str, *, parent: str = "", chunks: tuple[str, ...] = (), score: float = 1.0):
    return {
        "kind": "Chunk",
        "key": key,
        "title": "",
        "snippet": "",
        "score": score,
        "props": {"parent_key": parent} if parent else {},
        "provenance": [{"chunk_id": c, "source": parent or None} for c in chunks],
    }


def node_item(kind: str, key: str, *, chunks: tuple[str, ...] = (), score: float = 1.0):
    return {
        "kind": kind,
        "key": key,
        "title": "",
        "snippet": "",
        "score": score,
        "props": {},
        "provenance": [{"chunk_id": c, "source": None} for c in chunks],
    }


SHA_A = "a" * 40
SHA_B = "b" * 40

TRUTH = {
    "renames": [{"test_key": "XT-10002", "jira_key": "KAFKA-14597", "test_phrase": "latency"}],
    "duplicate_tests": [{"a": "XT-10021", "b": "XT-10022"}],
    "text_only_links": [{"from_key": "ADO-10006", "to_key": "KAFKA-14651"}],
}


# --------------------------------------------------------------------------- gold references


def test_a_node_key_is_a_key_reference():
    (ref,) = metrics.gold_refs(["KAFKA-14649"], TRUTH)
    assert (ref.kind, ref.targets) == ("key", ("KAFKA-14649",))


def test_a_forty_hex_id_is_a_chunk_reference():
    (ref,) = metrics.gold_refs([SHA_A], TRUTH)
    assert ref.kind == "chunk"


def test_a_truth_reference_expands_to_the_keys_its_record_names():
    (ref,) = metrics.gold_refs(["truth:renames:0"], TRUTH)
    assert ref.kind == "truth"
    assert set(ref.targets) == {"XT-10002", "KAFKA-14597"}


def test_a_truth_reference_to_a_missing_record_is_unresolvable_not_silently_empty():
    (ref,) = metrics.gold_refs(["truth:renames:99"], TRUTH)
    assert ref.kind == "truth"
    assert ref.targets == ()
    assert ref.unresolved is True


def test_duplicate_gold_ids_collapse_so_recall_cannot_exceed_one():
    refs = metrics.gold_refs(["KAFKA-1", "KAFKA-1"], TRUTH)
    assert len(refs) == 1


# --------------------------------------------------------------------------- the matcher


def test_gold_key_matches_the_item_key():
    refs = metrics.gold_refs(["KAFKA-14649"], TRUTH)
    scored = metrics.score_items([node_item("WorkItem", "KAFKA-14649")], refs)
    assert scored["recall"] == 1.0
    assert scored["matches"][0]["kind"] == "key"


def test_gold_chunk_id_matches_a_returned_chunk_by_its_key():
    refs = metrics.gold_refs([SHA_A], TRUTH)
    scored = metrics.score_items([chunk_item(SHA_A)], refs)
    assert scored["recall"] == 1.0
    assert scored["matches"][0]["kind"] == "chunk"


def test_a_gold_chunk_prefix_matches_the_full_chunk_id():
    refs = metrics.gold_refs([SHA_A[:12]], TRUTH)
    scored = metrics.score_items([chunk_item(SHA_A)], refs)
    assert scored["matches"][0]["kind"] == "chunk_prefix"


def test_a_prefix_shorter_than_the_minimum_is_not_a_chunk_prefix_match():
    refs = metrics.gold_refs([SHA_A[:6]], TRUTH)
    scored = metrics.score_items([chunk_item(SHA_A)], refs)
    assert scored["recall"] == 0.0


def test_gold_chunk_id_matches_through_provenance():
    refs = metrics.gold_refs([SHA_B], TRUTH)
    scored = metrics.score_items([node_item("Entity", "Decision|x", chunks=(SHA_B,))], refs)
    assert scored["matches"][0]["kind"] == "provenance"


def test_a_truth_reference_is_hit_by_any_key_the_record_names():
    refs = metrics.gold_refs(["truth:duplicate_tests:0"], TRUTH)
    scored = metrics.score_items([node_item("WorkItem", "XT-10022")], refs)
    assert scored["recall"] == 1.0
    assert scored["matches"][0]["kind"] == "key"


def test_a_chunk_of_the_gold_node_is_a_parent_match_counted_apart_from_the_strict_recall():
    """S1 returns chunks, not nodes. A chunk *of* KAFKA-14649 found the evidence."""
    refs = metrics.gold_refs(["KAFKA-14649"], TRUTH)
    scored = metrics.score_items([chunk_item(SHA_A, parent="KAFKA-14649")], refs)
    assert scored["matches"][0]["kind"] == "parent"
    assert scored["recall"] == 1.0
    assert scored["recall_strict"] == 0.0


def test_recall_is_the_share_of_gold_ids_reached_not_the_number_of_matches():
    refs = metrics.gold_refs(["KAFKA-1", "KAFKA-2", SHA_A], TRUTH)
    scored = metrics.score_items(
        [node_item("WorkItem", "KAFKA-1"), node_item("WorkItem", "KAFKA-1")], refs
    )
    assert scored["gold"] == 3
    assert scored["matched"] == 1
    # Reported rounded to four places: a report is read, not differentiated.
    assert scored["recall"] == pytest.approx(1 / 3, abs=1e-4)


def test_context_precision_is_items_with_a_gold_match_over_items():
    refs = metrics.gold_refs(["KAFKA-1"], TRUTH)
    items = [node_item("WorkItem", "KAFKA-1"), node_item("WorkItem", "KAFKA-9")]
    assert metrics.score_items(items, refs)["precision"] == 0.5


def test_precision_of_an_empty_context_is_zero_and_says_so_rather_than_dividing_by_zero():
    scored = metrics.score_items([], metrics.gold_refs(["KAFKA-1"], TRUTH))
    assert scored["precision"] == 0.0
    assert scored["items"] == 0


def test_hit_at_k_respects_the_rank_the_item_came_back_at():
    refs = metrics.gold_refs(["KAFKA-9"], TRUTH)
    items = [node_item("WorkItem", f"KAFKA-{n}") for n in range(1, 10)]
    scored = metrics.score_items(items, refs)
    assert scored["hit_at"]["1"] is False
    assert scored["hit_at"]["3"] is False
    assert scored["hit_at"]["10"] is True
    assert scored["hit"] is True


def test_a_question_with_no_gold_is_scored_as_pending_not_as_a_zero():
    scored = metrics.score_items([node_item("WorkItem", "KAFKA-1")], [])
    assert scored["pending"] is True
    assert scored["recall"] is None
    assert scored["precision"] is None


# --------------------------------------------------------------------------- percentiles


def test_percentile_returns_a_measured_value_never_an_interpolation():
    assert metrics.percentile([10, 20, 30, 40], 50) == 20
    assert metrics.percentile([10, 20, 30, 40], 95) == 40
    assert metrics.percentile([7], 95) == 7
    assert metrics.percentile([], 50) is None


# --------------------------------------------------------------------------- the matrix


def record(
    qid: str,
    strategy: str,
    qtype: str,
    *,
    status: str = "ok",
    gold: list[str] | None = None,
    items: list[dict] | None = None,
    latency: int = 100,
    tokens: int = 1000,
    cypher: int = 1,
    lang: str = "en",
    reason: str | None = None,
    pair: str | None = None,
):
    row = {
        "qid": qid,
        "strategy": strategy,
        "type": qtype,
        "lang": lang,
        "status": status,
        "reason": reason,
        "gold_evidence": gold or [],
        "gold_source": "graph" if gold else "pending",
        "latency_ms": latency,
        "context_tokens": tokens,
        "cypher_count": cypher,
        "result": {"items": items or []},
    }
    if pair:
        row["pair"] = pair
    return row


def test_a_cell_counts_runs_not_applicable_records_and_pending_questions_separately():
    records = [
        record("q1", "s1", "traceability", gold=["KAFKA-1"], items=[node_item("W", "KAFKA-1")]),
        record("q2", "s1", "traceability"),  # pending gold
        record("q3", "s1", "traceability", status="n/a", reason="no temporal template"),
    ]
    cell = metrics.matrix(metrics.score_records(records, TRUTH))["s1"]["traceability"]
    assert (cell["n"], cell["ok"], cell["na"], cell["scored"], cell["pending"]) == (3, 2, 1, 1, 1)
    assert cell["recall"] == 1.0


def test_a_cell_with_only_pending_questions_reports_no_recall_rather_than_zero():
    records = [record("q1", "s2", "global"), record("q2", "s2", "global")]
    cell = metrics.matrix(metrics.score_records(records, TRUTH))["s2"]["global"]
    assert cell["recall"] is None
    assert cell["pending"] == 2
    assert cell["latency_p50_ms"] == 100


def test_latency_and_tokens_are_measured_over_the_runs_that_ran_not_over_the_na_records():
    records = [
        record("q1", "s6", "temporal", latency=10, tokens=100),
        record("q2", "s6", "temporal", latency=30, tokens=300),
        record("q3", "s6", "temporal", status="n/a", latency=0, tokens=0, reason="no template"),
    ]
    cell = metrics.matrix(metrics.score_records(records, TRUTH))["s6"]["temporal"]
    assert cell["latency_p50_ms"] == 10
    assert cell["latency_p95_ms"] == 30
    assert cell["context_tokens_p50"] == 100
    assert cell["na"] == 1


def test_by_strategy_totals_are_the_row_sums_of_the_matrix():
    records = [
        record("q1", "s1", "traceability", gold=["KAFKA-1"], items=[node_item("W", "KAFKA-1")]),
        record("q2", "s1", "impact", gold=["KAFKA-2"], items=[node_item("W", "KAFKA-9")]),
    ]
    scored = metrics.score_records(records, TRUTH)
    row = metrics.by_strategy(scored)["s1"]
    cells = metrics.matrix(scored)["s1"]
    assert row["n"] == sum(c["n"] for c in cells.values())
    assert row["recall"] == 0.5


def test_na_reasons_are_kept_so_the_report_can_say_why_a_cell_is_empty():
    records = [record("q1", "s4", "global", status="n/a", reason="no example above 0.4")]
    row = metrics.by_strategy(metrics.score_records(records, TRUTH))["s4"]
    assert row["na_reasons"] == {"no example above 0.4": 1}


def test_cross_lingual_pairs_put_the_hebrew_row_next_to_its_english_twin():
    records = [
        record("cq1", "s1", "impact", lang="en", pair="versions", items=[node_item("W", "K-1")]),
        record("cq2", "s1", "impact", lang="he", pair="versions", items=[node_item("W", "K-1")]),
    ]
    pairs = metrics.cross_lingual(metrics.score_records(records, TRUTH))
    assert pairs[0]["pair"] == "versions"
    assert pairs[0]["strategies"]["s1"]["en"]["qid"] == "cq1"
    assert pairs[0]["strategies"]["s1"]["he"]["qid"] == "cq2"
    assert pairs[0]["strategies"]["s1"]["key_jaccard"] == 1.0


def test_a_pair_with_no_twin_is_not_a_cross_lingual_comparison():
    records = [record("cq1", "s1", "impact", lang="en", pair="lonely")]
    assert metrics.cross_lingual(metrics.score_records(records, TRUTH)) == []
