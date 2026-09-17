"""The Task 2 report: what it claims, and what it must not erase.

Three commands write `data/reports/retrieve.json` — `brain competency`, `brain serve
--check` and `brain cypher-examples check` — and they run in any order from different
agents. The merge is therefore the part worth a test: a section written last must add to
the file rather than replace it.

The rest is arithmetic over numbers measured elsewhere, so what is asserted here is the
arithmetic (`checks` turns measurements into pass/fail) and one invariant that is easy to
break silently: every refusal the guard can produce has a layer the report can name it by.
"""

from __future__ import annotations

import json

from brain.retrieve import cypher_guard as guard
from brain.retrieve import s4_report
from brain.retrieve.types import Item, Provenance, Result


def _sections(**overrides) -> dict:
    """A measured-looking report, with every number at its passing value."""
    sections = {
        "guard": {
            "blocked": {
                "cases": 56,
                "refused": 56,
                "refused_for_the_expected_reason": 56,
                "leaked": [],
            },
            "allowed": {"cases": 21, "passed": 21, "returned_rows": 19},
            "plan_only": {"cases": 3, "refused": 3},
            "timeout": {"refused": True, "elapsed_ms": 1349, "budget_s": 1.0},
            "logging": {
                "rejections_logged": 43,
                "rejections_expected": 43,
                "every_rejection_carries_a_reason": True,
            },
            "side_effects": {"labels": {"Foo": 0}, "nodes_created": 0, "evil_indexes": []},
        },
        "rerank": {
            "available": True,
            "questions": [{"id": "cq01"}],
            "summary": {"questions": 19, "top1_changed": 19},
        },
        "cypher_examples": {
            "bank_size": 14,
            "returning_at_least_one_row": 14,
            "unique_per_type": {"traceability": 4, "impact": 4, "rationale": 3, "temporal": 3},
            "thin_types": [],
            "duplicates": [],
            "s4": [{"id": "cq03", "mode_a": {"rows": 10, "p50_ms": 14}}],
        },
    }
    for key, value in overrides.items():
        section, _, field = key.partition(".")
        target = sections[section]
        while "." in field:
            head, _, field = field.partition(".")
            target = target[head]
        target[field] = value
    return sections


# ------------------------------------------------------------------ merging


def test_merge_adds_sections_without_erasing_the_others(tmp_path) -> None:
    """Task 1 and Task 3 wrote this file first; writing last must not mean writing alone."""
    path = tmp_path / "retrieve.json"
    path.write_text(json.dumps({"questions": [1, 2, 3], "mcp": {"tools": 15}}), encoding="utf-8")
    s4_report.merge({"guard": {"blocked": {"refused": 42}}}, path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["questions"] == [1, 2, 3]
    assert written["mcp"] == {"tools": 15}
    assert written["guard"]["blocked"]["refused"] == 42


def test_merge_replaces_only_its_own_section(tmp_path) -> None:
    path = tmp_path / "retrieve.json"
    s4_report.merge({"guard": {"run": 1}, "keep": True}, path)
    s4_report.merge({"guard": {"run": 2}}, path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["guard"] == {"run": 2}
    assert written["keep"] is True


def test_merge_stamps_what_it_wrote_and_leaves_the_index_to_one_writer(tmp_path) -> None:
    """One `sections` index, written by `report.merge_sections` — not a second stamper here."""
    from brain.retrieve.report import head_sha

    path = tmp_path / "retrieve.json"
    s4_report.merge({"guard": {"blocked": {"refused": 56}}}, path)
    index = json.loads(path.read_text(encoding="utf-8"))["sections"]
    assert index["guard"]["sha"] == head_sha()
    assert index["guard"]["stale"] is False
    assert index["guard"]["generated_at"]


def test_merge_survives_a_corrupt_file(tmp_path) -> None:
    """A half-written report is a reason to start a new one, not to lose this run."""
    path = tmp_path / "retrieve.json"
    path.write_text("{not json", encoding="utf-8")
    s4_report.merge({"guard": {"ok": True}}, path)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["guard"] == {"ok": True}
    # `sha`/`generated_at` are this write's own stamp, not recovered content.
    assert set(written) == {"guard", "sections", "sha", "generated_at"}


# ------------------------------------------------------------------ the claims


def test_every_refusal_the_guard_can_raise_has_a_layer() -> None:
    """Defence in depth is only defence in depth if you can say which layer did the work."""
    assert set(guard.HINTS) <= set(s4_report.LAYERS)


def test_checks_pass_when_every_number_is_at_its_passing_value() -> None:
    checks = s4_report.checks(_sections())
    assert [c["name"] for c in checks if not c["ok"]] == []


def test_one_leaked_attack_fails_the_check() -> None:
    sections = _sections(**{"guard.blocked.refused": 41, "guard.blocked.leaked": ["create"]})
    failed = {c["name"] for c in s4_report.checks(sections) if not c["ok"]}
    assert "every_write_or_injection_case_is_refused" in failed


def test_a_node_left_behind_fails_the_check() -> None:
    sections = _sections(**{"guard.side_effects.nodes_created": 1})
    failed = {c["name"] for c in s4_report.checks(sections) if not c["ok"]}
    assert "no_attack_left_a_node_behind" in failed


def test_an_example_that_returns_nothing_fails_the_check() -> None:
    sections = _sections(**{"cypher_examples.returning_at_least_one_row": 13})
    failed = {c["name"] for c in s4_report.checks(sections) if not c["ok"]}
    assert "every_bank_example_returns_at_least_one_row" in failed


def test_a_type_with_two_unique_examples_fails_the_check() -> None:
    """Five rows that are two patterns is two examples, and the check counts patterns."""
    sections = _sections(
        **{
            "cypher_examples.unique_per_type": {
                "traceability": 4,
                "impact": 4,
                "rationale": 2,
                "temporal": 3,
            },
            "cypher_examples.thin_types": ["rationale"],
        }
    )
    check = next(
        c
        for c in s4_report.checks(sections)
        if c["name"] == "three_to_five_unique_examples_per_type"
    )
    assert check["ok"] is False
    assert "rationale" in check["detail"]


def test_a_duplicate_left_in_the_bank_fails_the_check() -> None:
    sections = _sections(
        **{"cypher_examples.duplicates": [{"id": "temporal-cq14", "same": "cypher", "as": "seed"}]}
    )
    failed = {c["name"] for c in s4_report.checks(sections) if not c["ok"]}
    assert "three_to_five_unique_examples_per_type" in failed


def test_the_plan_layer_must_refuse_on_its_own() -> None:
    """If `EXPLAIN` stops catching writes, the deny-list is the only thing left."""
    sections = _sections(**{"guard.plan_only": {"cases": 3, "refused": 2}})
    failed = {c["name"] for c in s4_report.checks(sections) if not c["ok"]}
    assert "the_plan_layer_refuses_a_write_on_its_own" in failed


def test_a_missing_reranker_is_reported_not_hidden() -> None:
    sections = _sections(
        **{"rerank.available": False, "rerank.questions": [], "rerank.error": "no torch"}
    )
    check = next(c for c in s4_report.checks(sections) if c["name"] == "rerank_effect_is_measured")
    assert check["ok"] is False
    assert "no torch" in check["detail"]


# ------------------------------------------------------------------ small helpers


def test_a_chunk_is_reported_by_the_document_it_belongs_to() -> None:
    result = Result(
        strategy="s1",
        items=[
            Item(
                kind="Chunk", key="abc", provenance=[Provenance(chunk_id="abc", source="KIP-932")]
            ),
            Item(kind="Chunk", key="def", props={"parent_key": "KAFKA-1"}),
            Item(kind="Chunk", key="ghi"),
        ],
    )
    assert s4_report._sources(result) == ["KIP-932", "KAFKA-1", "ghi"]


def test_percentiles_are_taken_over_the_sorted_values() -> None:
    assert s4_report._stats([50, 10, 30])["p50_ms"] == 30
    assert s4_report._stats([])["p50_ms"] == 0
    assert s4_report._stats([5])["max_ms"] == 5


def test_a_batch_without_an_output_is_reported_as_waiting(tmp_path) -> None:
    (tmp_path / "001.in.json").write_text(
        json.dumps({"questions": [{"question_id": "cq01"}], "schema": {"labels": [1, 2]}}),
        encoding="utf-8",
    )
    state = s4_report._batch_state(tmp_path)
    assert state["state"].startswith("awaiting")
    assert state["inputs"][0]["questions"] == 1
    assert state["inputs"][0]["within_cap"] is True


def test_a_merged_batch_says_so(tmp_path) -> None:
    (tmp_path / "001.in.json").write_text(json.dumps({"questions": []}), encoding="utf-8")
    (tmp_path / "001.out.json").write_text(json.dumps({"answers": []}), encoding="utf-8")
    assert s4_report._batch_state(tmp_path)["state"] == "merged"
