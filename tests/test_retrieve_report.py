"""`data/reports/retrieve.json`: who wrote which section, when, and at which commit.

Four steps write into one report (`brain competency`, `brain serve --check`,
`brain cypher-examples check`, and whatever Plan 3 adds). Two failures follow from that and
both were found in the Plan 2 review:

* a writer that rewrites the whole file deletes the other steps' sections;
* a section measured three commits ago sits next to one measured now, and nothing in the
  file says which is which — a measurement dragged forward is a lie with a timestamp on it
  (conventions, "לקחים מסקירת סגירת Plan 1").

So every section carries the `sha` it was measured at, and any section whose `sha` is not
HEAD is marked `stale`. The acceptance checks are pure functions over the report dict,
which is why they can be asserted here without a graph.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.retrieve import report as rep
from brain.retrieve.types import Item, Provenance, Result

# ------------------------------------------------------------------------- freshness


def test_every_section_carries_the_sha_and_time_it_was_measured_at(tmp_path: Path) -> None:
    target = tmp_path / "retrieve.json"
    rep.merge_sections({"mcp": {"transport": "stdio"}, "questions": [1, 2]}, target)
    written = json.loads(target.read_text(encoding="utf-8"))

    head = rep.head_sha()
    for name in ("mcp", "questions"):
        entry = written["sections"][name]
        assert entry["sha"] == head
        assert entry["generated_at"]
        assert entry["stale"] is False
    # And the sections themselves are untouched: `latency` is a map of measurements, not a
    # record with room for metadata, so the stamp lives in the index and only there.
    assert written["mcp"] == {"transport": "stdio"}


def test_a_section_measured_at_another_commit_is_marked_stale(tmp_path: Path) -> None:
    target = tmp_path / "retrieve.json"
    rep.merge_sections({"guard": {"attacks": 42}}, target, sha="0" * 40)
    rep.merge_sections({"mcp": {"transport": "stdio"}}, target)

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["sections"]["guard"]["sha"] == "0" * 40
    assert written["sections"]["guard"]["stale"] is True
    assert written["sections"]["mcp"]["stale"] is False
    assert written["guard"]["attacks"] == 42, "stale is a label, not a delete"


def test_a_section_written_before_stamping_cannot_prove_it_is_fresh(tmp_path: Path) -> None:
    target = tmp_path / "retrieve.json"
    target.write_text(json.dumps({"rerank": {"top1_changed": 19}}), encoding="utf-8")
    rep.merge_sections({"mcp": {}}, target)

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["sections"]["rerank"]["sha"] is None
    assert written["sections"]["rerank"]["stale"] is True


def test_a_section_that_stamped_itself_is_believed(tmp_path: Path) -> None:
    """The guard's check writes its own `sha`. Calling that section stale would be noise."""
    target = tmp_path / "retrieve.json"
    target.write_text(
        json.dumps({"guard": {"attacks": 42, "sha": rep.head_sha(), "generated_at": "now"}}),
        encoding="utf-8",
    )
    rep.merge_sections({"mcp": {}}, target)
    index = json.loads(target.read_text(encoding="utf-8"))["sections"]
    assert index["guard"] == {"sha": rep.head_sha(), "generated_at": "now", "stale": False}


def test_writing_task_1_keeps_the_sections_the_other_steps_wrote(tmp_path: Path) -> None:
    """`brain competency`, `brain serve --check` and the guard's check, in any order."""
    target = tmp_path / "retrieve.json"
    rep.merge_sections({"mcp": {"transport": "stdio"}, "guard": {"attacks": 42}}, target)

    rep.write_report({"step": "plan2-task1-retrieval", "questions": [1, 2, 3]}, target)

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["questions"] == [1, 2, 3]
    assert written["mcp"]["transport"] == "stdio"
    assert written["guard"]["attacks"] == 42


def test_a_map_of_measurements_is_never_polluted_with_metadata(tmp_path: Path) -> None:
    """`for name, stats in report["latency"].items()` must not meet a string called `sha`."""
    target = tmp_path / "retrieve.json"
    rep.merge_sections({"latency": {"s1": {"p50_ms": 139}}}, target)
    latency = json.loads(target.read_text(encoding="utf-8"))["latency"]
    assert set(latency) == {"s1"}


def test_a_corrupt_report_is_replaced_rather_than_left_unwritten(tmp_path: Path) -> None:
    target = tmp_path / "retrieve.json"
    target.write_text("{not json", encoding="utf-8")
    rep.merge_sections({"mcp": {"ok": True}}, target)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["mcp"]["ok"] is True
    assert [p.name for p in tmp_path.iterdir()] == ["retrieve.json"]


# --------------------------------------------------------------- what an answer can prove


def _result(*items: Item, cypher: list[str] | None = None) -> Result:
    used = ["MATCH (n) RETURN n"] if cypher is None else cypher
    return Result(strategy="s3", items=list(items), cypher_used=used)


def test_an_items_evidence_is_counted_by_the_kind_of_evidence_it_is() -> None:
    result = _result(
        Item(kind="Chunk", key="c", provenance=[Provenance(chunk_id="a1", source_kind="quote")]),
        Item(kind="Row", key="r", provenance=[Provenance(chunk_id="b2", source_kind="node-text")]),
        Item(kind="Row", key="t", provenance=[Provenance(source="XE-1", source_kind="row")]),
    )
    counts = rep.by_source_kind(result)
    assert counts == {"quote": 1, "node-text": 1, "row": 1}


def test_a_row_backed_item_is_auditable_only_with_the_cypher_that_produced_it() -> None:
    """A tuple with no chunk id is checkable by re-running the query — and by nothing else."""
    row = Item(kind="Row", key="r", provenance=[Provenance(source="XE-1", source_kind="row")])
    assert rep.auditable_items(_result(row), valid={}) == 1
    assert rep.auditable_items(_result(row, cypher=[]), valid={}) == 0


def test_an_item_whose_chunk_does_not_exist_is_not_auditable() -> None:
    item = Item(kind="Chunk", key="c", provenance=[Provenance(chunk_id="ghost")])
    assert rep.auditable_items(_result(item), valid=set()) == 0
    assert rep.auditable_items(_result(item), valid={"ghost"}) == 1


# --------------------------------------------------------------------- acceptance checks


def _report(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": {
            "questions": 19,
            "evidence_questions": 18,
            "with_chunk_provenance": 16,
            "without_chunk_provenance": ["cq03", "cq06"],
            "auditable": 18,
            "fully_auditable": 17,
            "invalid_chunk_ids": 0,
            "empty_answers": [],
        },
        "cross_lingual_summary": {
            "pairs": 4,
            "s3_anchor_identical": 4,
            "s3_mean_jaccard": 0.812,
            "s1_mean_jaccard": 0.479,
            "s1_vector_mean_jaccard": 0.396,
        },
        "latency": {"s1(fixed)": {"p50_ms": 139, "p90_ms": 200, "max_ms": 300, "n": 3}},
        "vector_syntax": {"chosen": "db.index.vector.queryNodes"},
    }
    base.update(over)
    return base


def _check(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in rep.acceptance_checks(report) if c["name"] == name)


def test_chunk_provenance_is_reported_apart_from_auditability_and_is_not_the_gate() -> None:
    checks = rep.acceptance_checks(_report())
    chunk = _check(_report(), "chunk_provenance")
    audit = _check(_report(), "auditable")
    assert chunk["met"] == "partial" and chunk["gate"] is False
    assert "cq03" in chunk["detail"] and "cq06" in chunk["detail"]
    assert audit["ok"] is True and audit["gate"] is True and audit["met"] == "yes"
    assert all("met" in c and "gate" in c for c in checks)


def test_the_cross_lingual_criterion_is_partial_and_says_which_half_holds() -> None:
    check = _check(_report(), "cross_lingual_anchors_s1_s3")
    assert check["met"] == "partial"
    assert check["ok"] is True, "the S3 half — the anchor itself — is 4/4"
    assert "4/4" in check["detail"]
    assert "0.479" in check["detail"] and "0.396" in check["detail"]


def test_an_s3_pair_that_lost_an_anchor_is_not_partial_but_failed() -> None:
    summary = {
        "pairs": 4,
        "s3_anchor_identical": 3,
        "s3_mean_jaccard": 0.6,
        "s1_mean_jaccard": 0.4,
        "s1_vector_mean_jaccard": 0.3,
    }
    check = _check(_report(cross_lingual_summary=summary), "cross_lingual_anchors_s1_s3")
    assert check["ok"] is False and check["met"] == "no"
