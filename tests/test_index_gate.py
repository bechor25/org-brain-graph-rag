"""The gate's arithmetic and its refusal to round anything up.

The one test this file exists for is `test_person_recall_is_a_fail_even_though_the_planner
_accepted_it`: the accepted-but-failing criterion is the exact place a gate degrades into
a formality, so it is pinned.
"""

from __future__ import annotations

from pathlib import Path

from brain.index import gate as g


def corpus(**kw):
    base = {
        "real_issues": 1416,
        "synthetic_workitems": 1849,
        "kip_documents": 1334,
        "kips_embedded": 267,
        "kips_referenced": 267,
        "kips_referenced_embedded": 267,
        "commits": 6107,
        "commits_keyed": 4150,
    }
    return {**base, **kw}


def resolution(person_recall=0.7439, entity_recall=0.98, available=True):
    return {
        "available": available,
        "source": "data/reports/resolve.json",
        "target": 0.85,
        "kinds": {
            "person": {"precision": 0.9792, "recall": person_recall, "gold_pairs": 633},
            "entity": {"precision": 1.0, "recall": entity_recall, "gold_pairs": 100},
        },
    }


def provenance(missing=0):
    return {"edges": {"total": 14875, "missing": missing, "by_type": {}}}


def indexes(offline=None):
    return {"managed": 11, "online": 11 - len(offline or {}), "not_online": offline or {}}


def by_name(criteria):
    return {c["name"]: c for c in criteria}


def evaluate(tmp_path: Path, **kw):
    args = {
        "corpus": corpus(),
        "resolution": resolution(),
        "provenance": provenance(),
        "indexes": indexes(),
        "docs_dir": tmp_path,
        "smoke": {"ok": True, "status": "PASS", "at": "now", "duration_s": 60},
    }
    args.update(kw)
    return g.evaluate(**args)


def full_docs(tmp_path: Path) -> Path:
    (tmp_path / "lessons").mkdir()
    for n in g.REQUIRED_LESSONS:
        (tmp_path / "lessons" / f"{n:02d}-step.md").write_text("x", encoding="utf-8")
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "progress.md").write_text(
        "| Plan | step | status | commit |\n|---|---|---|---|\n| 1 | 01 probe | ✅ | abc |\n",
        encoding="utf-8",
    )
    return tmp_path


# ------------------------------------------------------------------------- the honest FAIL


def test_person_recall_is_a_fail_even_though_the_planner_accepted_it(tmp_path):
    gate = evaluate(full_docs(tmp_path))
    recall = by_name(gate["criteria"])["person_resolution_recall"]
    assert recall["value"] == 0.7439
    assert recall["status"] == "FAIL"
    assert "accepted by the planner" in recall["note"]
    assert gate["ok"] is False
    assert gate["failed_names"] == ["person_resolution_recall"]


def test_the_only_way_to_pass_that_criterion_is_to_move_the_number(tmp_path):
    gate = evaluate(full_docs(tmp_path), resolution=resolution(person_recall=0.851))
    assert by_name(gate["criteria"])["person_resolution_recall"]["status"] == "PASS"
    assert gate["ok"] is True


# ------------------------------------------------------------------------------ thresholds


def test_corpus_thresholds_come_from_task_9_not_the_roadmap_table(tmp_path):
    issues = by_name(evaluate(full_docs(tmp_path))["criteria"])["issues"]
    assert issues["value"] == 1416
    assert issues["status"] == "PASS"
    # the summary table's pre-probe draft is recorded, not applied
    assert g.ROADMAP_TABLE_VARIANT["issues"] == 1500
    assert "FAIL" in issues["note"]


def test_a_missing_resolve_report_is_a_failure_not_a_blank(tmp_path):
    gate = evaluate(full_docs(tmp_path), resolution={"available": False, "source": "x.json"})
    for name in ("person_resolution_precision", "entity_resolution_recall"):
        row = by_name(gate["criteria"])[name]
        assert row["value"] == "UNKNOWN"
        assert row["status"] == "FAIL"


def test_one_llm_edge_without_provenance_fails_the_gate(tmp_path):
    gate = evaluate(full_docs(tmp_path), provenance=provenance(missing=1))
    assert by_name(gate["criteria"])["llm_edges_without_provenance"]["status"] == "FAIL"


def test_an_index_that_is_not_online_fails_the_gate(tmp_path):
    gate = evaluate(full_docs(tmp_path), indexes=indexes({"chunk_text": "POPULATING"}))
    row = by_name(gate["criteria"])["all_indexes_online"]
    assert row["status"] == "FAIL"
    assert "POPULATING" in row["note"]


def test_referenced_kips_that_are_not_embedded_fail_the_all_referenced_half(tmp_path):
    gate = evaluate(
        full_docs(tmp_path),
        corpus=corpus(kips_referenced=300, kips_referenced_embedded=267),
    )
    row = by_name(gate["criteria"])["kips_referenced_all_embedded"]
    assert row["value"] == "267/300"
    assert row["status"] == "FAIL"


# ------------------------------------------------------------------------- process criteria


def test_an_unrecorded_smoke_run_is_a_failure(tmp_path):
    gate = evaluate(full_docs(tmp_path), smoke=None)
    row = by_name(gate["criteria"])["make_smoke_green"]
    assert row["value"] == "NOT RECORDED"
    assert row["status"] == "FAIL"
    assert "--smoke" in row["note"]


def test_a_failed_smoke_run_is_a_failure(tmp_path):
    gate = evaluate(full_docs(tmp_path), smoke={"ok": False, "status": "FAIL", "exit_code": 1})
    assert by_name(gate["criteria"])["make_smoke_green"]["status"] == "FAIL"


def test_missing_lessons_are_named(tmp_path):
    docs = full_docs(tmp_path)
    (docs / "lessons" / "09-step.md").unlink()
    row = by_name(evaluate(docs)["criteria"])["lessons_01_09"]
    assert row["status"] == "FAIL"
    assert "[9]" in row["note"]


def test_lessons_are_found_by_their_number_prefix(tmp_path):
    docs = full_docs(tmp_path)
    status = g.lesson_status(docs / "lessons")
    assert status["missing"] == []
    assert status["present"] == list(g.REQUIRED_LESSONS)


def test_a_missing_lessons_directory_is_absence_not_a_crash(tmp_path):
    status = g.lesson_status(tmp_path / "nope")
    assert status["files"] == []
    assert status["missing"] == list(g.REQUIRED_LESSONS)


# -------------------------------------------------------------------------- progress table


PROGRESS = """# progress
| Plan | step | status | commit | notes |
|---|---|---|---|---|
| 0 | 1 scaffold | ✅ | a | |
| 1 | 01 probe | ✅ | b | |
| 1 | 09 communities | ⬜ | | |
| 1 | 08 resolve | 🟡 fix-required | c | |
"""


def test_progress_counts_only_plan_1_rows_and_names_the_open_ones(tmp_path):
    path = tmp_path / "progress.md"
    path.write_text(PROGRESS, encoding="utf-8")
    status = g.progress_status(path)
    assert status["rows"] == 3
    assert status["done"] == 1
    assert status["open"] == ["09 communities [⬜]", "08 resolve [🟡 fix-required]"]


def test_an_open_progress_row_fails_the_gate(tmp_path):
    docs = full_docs(tmp_path)
    (docs / "planning" / "progress.md").write_text(PROGRESS, encoding="utf-8")
    row = by_name(evaluate(docs)["criteria"])["progress_complete"]
    assert row["status"] == "FAIL"
    assert "09 communities" in row["note"]


def test_a_missing_progress_file_is_a_failure(tmp_path):
    status = g.progress_status(tmp_path / "nope.md")
    assert status["available"] is False
    assert status["open"]


# -------------------------------------------------------------------------------- printing


def test_the_table_prints_a_line_per_criterion_with_its_value(tmp_path):
    gate = evaluate(full_docs(tmp_path))
    table = g.render_table(gate)
    for c in gate["criteria"]:
        assert c["name"] in table
    assert "0.7439" in table
    assert "FAIL" in table
    assert "person_resolution_recall" in table.split("PASS —")[-1]
