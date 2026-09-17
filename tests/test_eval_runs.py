"""Fixed-mode runs: applicability, the record, idempotency and the report — no graph.

`run_one` takes its `ask` and `select_example` as arguments, which is what lets the whole
orchestration — route suggestion, n/a reasons, packing cost, the run file, the rerun
comparison and the report — be proved against a fake strategy in `make check`. The live
half is `tests/live/test_eval_runs_live.py`.
"""

from __future__ import annotations

import json

import pytest

from brain.eval import runs
from brain.retrieve.types import Item, Provenance, Result


def question(qid="q001", qtype="traceability", text="Which tests cover KAFKA-14649?", **extra):
    row = {
        "id": qid,
        "type": qtype,
        "lang": "en",
        "question": text,
        "gold_answer": "…",
        "gold_evidence": ["KAFKA-14649"],
        "difficulty": 2,
        "expected_strategy": "s3",
        "source_path_id": None,
        "gold_source": "graph",
        "origin": "forged",
    }
    row.update(extra)
    return row


def result(strategy="s1", *, keys=("KAFKA-14649",), latency=42, cypher=("MATCH (n) RETURN n",)):
    return Result(
        strategy=strategy,
        items=[
            Item(
                kind="WorkItem",
                key=k,
                title="t",
                snippet="s",
                score=1.0,
                provenance=[Provenance(chunk_id="a" * 40)],
            )
            for k in keys
        ],
        cypher_used=list(cypher),
        latency_ms=latency,
        route={"strategy": "s3", "executed": strategy},
    )


def fake_ask(returns=None, *, strategy_override=None):
    calls = []

    def _ask(ctx, text, strategy="auto", **kwargs):
        calls.append({"question": text, "strategy": strategy, **kwargs})
        if returns is not None:
            return returns
        return result(strategy_override or strategy)

    _ask.calls = calls
    return _ask


def fake_example(similarity=0.9, example_id="ex-1"):
    def _select(ctx, text, question_type=None, **kwargs):
        return ({"id": example_id, "type": "impact", "question": text}, similarity)

    return _select


# --------------------------------------------------------------------------- strategies


def test_the_strategy_list_has_the_baseline_and_its_unreranked_twin():
    assert runs.STRATEGIES == ("s1", "s1r", "s2", "s3", "s4", "s5", "s6")
    assert runs.BASELINE == "s1r"
    assert runs.base_strategy("s1r") == "s1"
    assert runs.base_strategy("s6") == "s6"


def test_parse_strategies_rejects_a_name_no_strategy_answers_to():
    assert runs.parse_strategies("s1,s3") == ("s1", "s3")
    assert runs.parse_strategies(None) == runs.STRATEGIES
    with pytest.raises(ValueError, match="s9"):
        runs.parse_strategies("s1,s9")


def test_a_run_file_is_named_for_the_question_and_the_strategy():
    path = runs.run_path("/tmp/runs", "cq01", "s1r")
    assert path.name == "cq01.s1r.json"
    assert path.parent.name == "fixed"


# --------------------------------------------------------------------------- applicability


def test_s5_does_not_apply_to_a_question_with_no_thematic_signal():
    plan = runs.applicability(question(), "s5", {"rule": "keys", "strategy": "s3"})
    assert plan.ok is False
    assert "thematic" in plan.reason


def test_s5_applies_to_a_global_question_even_when_the_router_saw_a_key():
    plan = runs.applicability(question(qtype="global"), "s5", {"rule": "keys"})
    assert plan.ok is True


def test_s5_applies_everywhere_when_the_caller_widens_its_scope():
    plan = runs.applicability(question(), "s5", {"rule": "keys"}, s5_scope="all")
    assert plan.ok is True


def test_every_other_strategy_applies_before_it_is_attempted():
    for strategy in ("s1", "s1r", "s2", "s3", "s4", "s6"):
        assert runs.applicability(question(), strategy, {"rule": "keys"}).ok is True


# --------------------------------------------------------------------------- one run


def test_a_run_record_carries_the_route_suggestion_the_cost_and_the_packed_result():
    record = runs.run_one(None, question(), "s1", ask=fake_ask(), select_example=fake_example())
    assert record["status"] == "ok"
    assert record["strategy"] == "s1"
    assert record["executed"] == "s1"
    assert record["route"]["strategy"] == "s3"  # the question names a key
    assert record["items"] == 1
    assert record["cypher_count"] == 1
    assert record["context_tokens"] > 0
    assert record["latency_ms"] == 42
    assert record["result"]["items"][0]["key"] == "KAFKA-14649"
    assert record["budget_tokens"] == 4000
    assert record["include_synthetic"] is True


def test_the_baseline_asks_for_the_reranker_and_the_plain_s1_does_not():
    ask = fake_ask()
    runs.run_one(None, question(), "s1r", ask=ask, select_example=fake_example())
    runs.run_one(None, question(), "s1", ask=ask, select_example=fake_example())
    assert ask.calls[0]["strategy"] == "s1" and ask.calls[0]["rerank"] is True
    assert ask.calls[1]["strategy"] == "s1" and ask.calls[1]["rerank"] is False


def test_s4_is_not_applicable_when_no_bank_example_is_close_enough():
    record = runs.run_one(
        None, question(), "s4", ask=fake_ask(), select_example=fake_example(similarity=0.11)
    )
    assert record["status"] == "n/a"
    assert "0.11" in record["reason"] and "example" in record["reason"]
    assert record["result"] is None
    assert record["latency_ms"] is None


def test_s4_records_which_example_it_borrowed_when_one_is_close_enough():
    record = runs.run_one(
        None,
        question(),
        "s4",
        ask=fake_ask(strategy_override="s4"),
        select_example=fake_example(0.77, "ex-9"),
    )
    assert record["status"] == "ok"
    assert record["example"] == {"id": "ex-9", "similarity": 0.77}


def test_a_strategy_that_fell_back_is_an_na_record_with_the_reason_never_a_silent_pass():
    """S6 without a date, a version pair or a key has no template to bind — `_temporal` says so."""
    fallen = result("s2")
    fallen.route = {
        "strategy": "s6",
        "executed": "s2",
        "fallback_from": "s6",
        "fallback_reason": "temporal wording but no key/date/version pair to bind",
    }
    record = runs.run_one(
        None, question(qtype="temporal"), "s6", ask=fake_ask(fallen), select_example=fake_example()
    )
    assert record["status"] == "n/a"
    assert record["reason"] == "temporal wording but no key/date/version pair to bind"
    assert record["executed"] == "s2"


def test_a_strategy_that_raises_is_an_na_record_not_a_crashed_sweep():
    from brain.retrieve.types import RetrieveError

    def _boom(ctx, text, strategy="auto", **kwargs):
        raise RetrieveError("index 'community_embedding' does not exist")

    record = runs.run_one(
        None, question(qtype="global"), "s5", ask=_boom, select_example=fake_example()
    )
    assert record["status"] == "n/a"
    assert "community_embedding" in record["reason"]


def test_an_na_record_still_carries_the_question_its_type_and_its_gold():
    record = runs.run_one(
        None, question(), "s4", ask=fake_ask(), select_example=fake_example(similarity=0.0)
    )
    assert record["type"] == "traceability"
    assert record["gold_evidence"] == ["KAFKA-14649"]
    assert record["gold_source"] == "graph"


# --------------------------------------------------------------------------- idempotency


def test_a_rerun_that_differs_only_in_latency_is_reported_unchanged_and_not_written(tmp_path):
    row, ask = question(), fake_ask()
    first = runs.run_one(None, row, "s1", ask=ask, select_example=fake_example())
    path = runs.run_path(tmp_path, row["id"], "s1")
    assert runs.save(path, first, force=False) == "new"
    before = path.read_text(encoding="utf-8")

    slower = runs.run_one(
        None, row, "s1", ask=fake_ask(result("s1", latency=999)), select_example=fake_example()
    )
    assert runs.save(path, slower, force=False) == "unchanged"
    assert path.read_text(encoding="utf-8") == before


def test_a_rerun_whose_items_changed_is_reported_and_kept_until_force(tmp_path):
    row = question()
    path = runs.run_path(tmp_path, row["id"], "s1")
    runs.save(
        path,
        runs.run_one(None, row, "s1", ask=fake_ask(), select_example=fake_example()),
        force=False,
    )
    other = runs.run_one(
        None,
        row,
        "s1",
        ask=fake_ask(result("s1", keys=("KAFKA-999",))),
        select_example=fake_example(),
    )
    assert runs.save(path, other, force=False) == "changed"
    assert json.loads(path.read_text())["result"]["items"][0]["key"] == "KAFKA-14649"
    assert runs.save(path, other, force=True) == "rewritten"
    assert json.loads(path.read_text())["result"]["items"][0]["key"] == "KAFKA-999"


def test_the_comparison_ignores_the_clock_and_the_commit_but_nothing_else():
    a = {"latency_ms": 1, "generated_at": "x", "sha": "aa", "items": 2, "result": {"latency_ms": 1}}
    b = {"latency_ms": 9, "generated_at": "y", "sha": "bb", "items": 2, "result": {"latency_ms": 9}}
    assert runs.comparable(a) == runs.comparable(b)
    assert runs.comparable(a) != runs.comparable({**b, "items": 3})


def test_a_stopwatch_nested_inside_the_envelope_is_not_a_difference():
    """`run_cypher` records `query_ms` in `route`; S4 called 17 files changed over it."""
    a = {"items": 1, "result": {"route": {"rows": 3, "query_ms": 1}, "items": [{"key": "K"}]}}
    b = {"items": 1, "result": {"route": {"rows": 3, "query_ms": 8}, "items": [{"key": "K"}]}}
    assert runs.comparable(a) == runs.comparable(b)
    assert runs.comparable(a) != runs.comparable(
        {"items": 1, "result": {"route": {"rows": 4, "query_ms": 8}, "items": [{"key": "K"}]}}
    )


# --------------------------------------------------------------------------- the sweep


def test_every_question_times_every_strategy_gets_a_record(tmp_path):
    rows = [question("q001"), question("q002", qtype="global", text="What are the main themes?")]
    summary = runs.run_all(
        None,
        rows,
        strategies=("s1", "s5"),
        out_dir=tmp_path,
        ask=fake_ask(),
        select_example=fake_example(),
    )
    assert summary["expected"] == 4
    assert len(summary["records"]) == 4
    assert sorted(p.name for p in (tmp_path / "fixed").glob("*.json")) == [
        "q001.s1.json",
        "q001.s5.json",
        "q002.s1.json",
        "q002.s5.json",
    ]
    statuses = {(r["qid"], r["strategy"]): r["status"] for r in summary["records"]}
    assert statuses[("q001", "s5")] == "n/a"  # no thematic signal
    assert statuses[("q002", "s5")] == "ok"


def test_coverage_names_the_pairs_that_have_no_file_at_all(tmp_path):
    rows = [question("q001")]
    runs.run_all(
        None,
        rows,
        strategies=("s1",),
        out_dir=tmp_path,
        ask=fake_ask(),
        select_example=fake_example(),
    )
    coverage = runs.coverage(rows, ("s1", "s2"), tmp_path)
    assert coverage["missing"] == ["q001.s2"]
    assert coverage["present"] == 1


# --------------------------------------------------------------------------- the report


def test_the_report_carries_the_matrix_the_cost_table_and_the_question_set_state(tmp_path):
    rows = [question("q001"), question("q002", qtype="impact")]
    summary = runs.run_all(
        None,
        rows,
        strategies=("s1", "s3"),
        out_dir=tmp_path,
        ask=fake_ask(),
        select_example=fake_example(),
    )
    report = runs.build_report(
        summary["records"],
        rows,
        strategies=("s1", "s3"),
        questions_path=tmp_path / "questions.jsonl",
        questions_complete=False,
        questions_total=2,
        truth={},
        summary=summary,
        out_dir=tmp_path,
    )
    assert report["step"] == "eval.run.fixed"
    assert report["baseline"] == "s1r"
    assert report["questions_complete"] is False
    assert set(report["matrix"]) == {"s1", "s3"}
    assert report["matrix"]["s1"]["traceability"]["recall"] == 1.0
    assert report["cost"]["s1"]["runs"] == 2
    assert [q["qid"] for q in report["questions"]] == ["q001", "q002"]
    assert report["coverage"]["missing"] == []


def test_pending_questions_are_run_and_counted_but_never_scored(tmp_path):
    rows = [question("cq01", gold_source="pending", gold_evidence=[], gold_answer=None)]
    summary = runs.run_all(
        None,
        rows,
        strategies=("s1",),
        out_dir=tmp_path,
        ask=fake_ask(),
        select_example=fake_example(),
    )
    report = runs.build_report(
        summary["records"],
        rows,
        strategies=("s1",),
        questions_path=tmp_path / "q.jsonl",
        questions_complete=False,
        questions_total=1,
        truth={},
        summary=summary,
        out_dir=tmp_path,
    )
    cell = report["matrix"]["s1"]["traceability"]
    assert cell["pending"] == 1 and cell["scored"] == 0 and cell["recall"] is None
    assert report["gold_pending"] == 1
    assert cell["latency_p50_ms"] == 42


def test_the_printed_matrix_marks_pending_cells_and_the_ones_that_did_not_apply():
    matrix = {
        "s1": {"traceability": {"ok": 2, "na": 0, "recall": 0.5, "scored": 2, "pending": 0}},
        "s5": {"traceability": {"ok": 0, "na": 2, "recall": None, "scored": 0, "pending": 0}},
        "s6": {"traceability": {"ok": 1, "na": 1, "recall": None, "scored": 0, "pending": 1}},
    }
    text = "\n".join(runs.matrix_lines(matrix, ("traceability",)))
    assert "0.50" in text
    assert "n/a" in text
    assert "pend" in text


def test_gold_that_resolves_to_nothing_is_charged_to_the_question_set_not_to_retrieval():
    rows = [
        question("q001", gold_evidence=["truth:renames:0"]),
        question("q002", gold_evidence=["truth:renames:404", "truth:nowhere:0"]),
    ]
    truth = {"renames": [{"test_key": "XT-1", "jira_key": "KAFKA-1"}]}
    assert runs.unresolved_gold(rows, truth) == [
        {"qid": "q002", "refs": ["truth:renames:404", "truth:nowhere:0"]}
    ]
    note = "\n".join(runs._notes(0, True, runs.unresolved_gold(rows, truth)))
    assert "q002" in note and "not about retrieval" in note


# ------------------------------------------------ the columns come from the files on disk


OLD_SHA = "0" * 40
NEW_SHA = "9" * 40
OLD_CLOCK = "2026-09-01T00:00:00+00:00"


def sweep(tmp_path, rows, strategies=runs.STRATEGIES, ask=None, force=False):
    return runs.run_all(
        None,
        rows,
        strategies=strategies,
        out_dir=tmp_path,
        ask=ask or fake_ask(),
        select_example=fake_example(),
        force=force,
        sha=NEW_SHA,
    )


def backdate(tmp_path, strategies, *, sha=OLD_SHA, clock=OLD_CLOCK):
    """Make these columns' run files look like they were measured at an older commit."""
    for path in sorted((tmp_path / "fixed").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["strategy"] in strategies:
            record["sha"], record["generated_at"] = sha, clock
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", "utf-8")


def report_for(tmp_path, rows, summary, ran, *, head=NEW_SHA):
    return runs.build_report(
        summary["records"],
        rows,
        strategies=ran,
        questions_path=tmp_path / "questions.jsonl",
        questions_complete=True,
        questions_total=len(rows),
        truth={},
        summary=summary,
        out_dir=tmp_path,
        sha=NEW_SHA,
        head=head,
    )


def test_a_single_strategy_rerun_reports_seven_columns_and_rewrites_only_its_own(tmp_path):
    """The bug this is here for: `--strategies s6` published a report with one column.

    The other six strategies' run files were still on disk, unread and unreported — so the
    report claimed a matrix that the evidence directory contradicted.
    """
    rows = [question("q001"), question("q002", qtype="impact")]
    sweep(tmp_path, rows)
    backdate(tmp_path, set(runs.STRATEGIES) - {"s6"})
    before = {p.name: p.read_text(encoding="utf-8") for p in (tmp_path / "fixed").glob("*.json")}

    summary = sweep(tmp_path, rows, strategies=("s6",), ask=fake_ask(result("s6", keys=("K-9",))))

    assert set(summary["outcomes"]) == {"changed"}  # kept without --force
    report = report_for(tmp_path, rows, summary, ("s6",))
    assert report["strategies"] == list(runs.STRATEGIES)
    assert set(report["matrix"]) == set(runs.STRATEGIES)
    assert set(report["cost"]) == set(runs.STRATEGIES)
    assert report["coverage"]["complete"] is True
    untouched = {p.name: p.read_text(encoding="utf-8") for p in (tmp_path / "fixed").glob("*.json")}
    assert {k: v for k, v in untouched.items() if ".s6." not in k} == {
        k: v for k, v in before.items() if ".s6." not in k
    }


def test_every_column_carries_the_commit_its_own_runs_were_measured_at(tmp_path):
    rows = [question("q001")]
    sweep(tmp_path, rows)
    backdate(tmp_path, set(runs.STRATEGIES) - {"s6"})
    summary = sweep(tmp_path, rows, strategies=("s6",), force=True)

    columns = report_for(tmp_path, rows, summary, ("s6",))["columns"]
    assert columns["s6"] == {
        "status": "fresh",
        "stale": False,
        "runs": 1,
        "stale_runs": 0,
        "sha": NEW_SHA,
        "generated_at": columns["s6"]["generated_at"],
        "ran": True,
    }
    assert columns["s1"]["sha"] == OLD_SHA
    assert columns["s1"]["stale"] is True
    assert columns["s1"]["status"] == "stale"
    assert columns["s1"]["generated_at"] == OLD_CLOCK
    assert columns["s1"]["ran"] is False


def test_a_strategy_with_no_run_file_at_all_is_a_missing_column_not_a_silent_absence(tmp_path):
    rows = [question("q001")]
    summary = sweep(tmp_path, rows, strategies=("s1", "s3"))
    report = report_for(tmp_path, rows, summary, ("s1", "s3"))

    assert report["strategies"] == ["s1", "s3"]
    assert report["columns"]["s5"] == {
        "status": "missing",
        "stale": True,
        "runs": 0,
        "stale_runs": 0,
        "sha": None,
        "generated_at": None,
        "ran": False,
    }
    # A column nobody ever ran is not charged to this run's coverage.
    assert report["coverage"]["complete"] is True
    assert "s5" in "\n".join(runs.summary_lines(report))


def test_the_report_reads_the_kept_file_not_the_result_a_rerun_refused_to_write(tmp_path):
    rows = [question("q001")]
    sweep(tmp_path, rows, strategies=("s1",))
    summary = sweep(tmp_path, rows, strategies=("s1",), ask=fake_ask(result("s1", keys=("K-9",))))

    report = report_for(tmp_path, rows, summary, ("s1",))
    assert summary["changed"] == ["q001.s1"]
    # The kept file found the gold; the result the rerun produced and did not write did not.
    assert report["questions"][0]["strategies"]["s1"]["recall"] == 1.0


def test_the_run_history_records_the_strategies_this_invocation_ran(tmp_path):
    rows = [question("q001")]
    sweep(tmp_path, rows)
    summary = sweep(tmp_path, rows, strategies=("s6",), force=True)
    report = report_for(tmp_path, rows, summary, ("s6",))

    path = runs.write_report(report, tmp_path / "eval_retrieval.json")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["strategies"] == list(runs.STRATEGIES)
    assert written["runs"][-1]["strategies"] == ["s6"]
    assert written["columns"]["s6"]["ran"] is True


def test_the_printed_summary_names_the_columns_that_are_not_at_head(tmp_path):
    rows = [question("q001")]
    sweep(tmp_path, rows)
    backdate(tmp_path, {"s2"})
    summary = sweep(tmp_path, rows, strategies=("s6",), force=True)
    text = "\n".join(runs.summary_lines(report_for(tmp_path, rows, summary, ("s6",))))
    assert "columns:" in text
    assert "s2" in text.split("columns:")[1].splitlines()[0]
