"""What `brain eval questions merge` accepts, what it rejects, and why.

The two checks worth the most here are the ones nothing else in the pipeline does.

**Evidence existence.** `gold_evidence` is what layer 2 measures recall against. A key that
is not in the graph makes recall unmeasurable *and* makes the question look hard when it is
merely wrong, so a question whose gold rests on a key nobody can retrieve is rejected.

**Leakage.** A question derived from a graph path must still be answerable from the raw
text (plan decision 2), and it is not a measurement at all if the answer is sitting in the
question. The path declares which of its nodes are the subject (`anchors`) and which are
the answer (`answers`); naming an answer key in the question is a rejection, naming an
anchor is the whole point.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from brain.eval.questions_report import (
    DIFFICULTY_DEFAULT,
    KEY_LOOKUPS,
    Verdict,
    competency_row,
    existing_keys,
    longest_shared_run,
    offered_keys,
    read_outputs,
    review_question,
    select_balanced,
    truth_evidence_ok,
    words,
)

SCHEMA = json.loads(Path("brain/eval/question_schema.json").read_text(encoding="utf-8"))

PATH = {
    "path_id": "test_fix:KAFKA-16448",
    "type": "traceability",
    "shape": "test_fix",
    "gold_source": "graph",
    "anchors": ["KAFKA-16448"],
    "answers": ["XT-10109", "8d11d9579503426edfaeae791ec4bb212da37ad2"],
    "nodes": [
        {"role": "work_item", "label": "WorkItem", "key": "KAFKA-16448", "title": "t", "props": {}},
        {"role": "test", "label": "Test", "key": "XT-10109", "title": "t", "props": {}},
    ],
    "snippets": [{"chunk_id": "a" * 40, "parent_key": "KAFKA-16448", "text": "x"}],
    "truth": [],
    "asks": [{"lang": "en", "questions": 1}],
}


def question(**kw):
    base = {
        "id": "q101",
        "type": "traceability",
        "lang": "en",
        "question": "Which Xray tests cover KAFKA-16448 and what did their last run say?",
        "gold_answer": "XT-10109 covers it; its last recorded run passed.",
        "gold_evidence": ["XT-10109", "KAFKA-16448"],
        "difficulty": 2,
        "expected_strategy": "s3",
        "source_path_id": "test_fix:KAFKA-16448",
        "gold_source": "graph",
    }
    return {**base, **kw}


def review(q, *, known=None, paths=None, truth=None):
    return review_question(
        q,
        batch_id="shard-01/001",
        paths={p["path_id"]: p for p in (paths or [PATH])},
        known_keys=known if known is not None else {"XT-10109", "KAFKA-16448", "a" * 40},
        truth=truth or {},
        schema=SCHEMA,
    )


# ------------------------------------------------------------------------- happy path


def test_a_well_formed_question_is_accepted_and_carries_its_provenance():
    v = review(question())
    assert v.ok, v.reasons
    assert v.row["origin"] == "forged"
    assert v.row["shape"] == "test_fix"
    assert v.row["batch_id"] == "shard-01/001"
    assert v.row["difficulty_source"] == "forged"
    assert v.row["anchors"] == ["KAFKA-16448"]


def test_the_row_it_produces_validates_against_the_merged_row_schema():
    from brain.common.jsonschema_mini import validate

    v = review(question())
    row_schema = {**SCHEMA["$defs"]["row"], "$defs": SCHEMA["$defs"]}
    assert validate(v.row, row_schema) == []


# ----------------------------------------------------------------------------- schema


def test_a_question_that_fails_the_schema_is_rejected_with_the_field_named():
    v = review(question(difficulty=9))
    assert not v.ok
    assert any(r.startswith("schema") for r in v.reasons)


def test_an_id_outside_the_q_range_is_a_schema_rejection_not_a_silent_rename():
    v = review(question(id="cq20"))
    assert not v.ok
    assert any(r.startswith("schema") for r in v.reasons)


# ------------------------------------------------------------------------------ paths


def test_a_question_about_a_path_this_batch_did_not_offer_is_rejected():
    v = review(question(source_path_id="test_fix:KAFKA-99999"))
    assert not v.ok
    assert "unknown_path" in v.reasons


def test_a_question_typed_differently_from_its_path_unbalances_the_set_and_is_rejected():
    v = review(question(type="global"))
    assert not v.ok
    assert "type_mismatch" in v.reasons


def test_a_gold_source_that_contradicts_the_path_is_rejected():
    v = review(question(gold_source="truth"))
    assert not v.ok
    assert "gold_source_mismatch" in v.reasons


# --------------------------------------------------------------------------- evidence


def test_evidence_the_batch_never_offered_is_rejected_even_if_it_exists_in_the_graph():
    v = review(question(gold_evidence=["KAFKA-14649"]), known={"KAFKA-14649"})
    assert not v.ok
    assert "evidence_not_offered" in v.reasons


def test_evidence_that_does_not_exist_in_the_graph_is_rejected():
    v = review(question(), known={"KAFKA-16448"})
    assert not v.ok
    assert "evidence_missing_in_graph" in v.reasons


def test_a_chunk_id_offered_as_a_snippet_counts_as_evidence():
    v = review(question(gold_evidence=["a" * 40]))
    assert v.ok, v.reasons


def test_an_id_the_path_names_only_on_an_edge_is_offered():
    """A `test_fix` path names its TestExecutions on HAS_RUN edges, never as nodes. A
    question that cites the execution its answer rests on was being rejected for it."""
    path = {
        **PATH,
        "edges": [
            {"type": "TESTS", "from": "XT-10109", "to": "KAFKA-16448", "props": {}},
            {"type": "HAS_RUN", "from": "XE-10004", "to": "XT-10109", "props": {"status": "PASS"}},
        ],
    }
    assert "XE-10004" in offered_keys(path)
    v = review(
        question(gold_evidence=["KAFKA-16448", "XE-10004"]),
        paths=[path],
        known={"KAFKA-16448", "XE-10004"},
    )
    assert v.ok, v.reasons


def test_offered_keys_still_refuses_an_id_the_path_never_mentions():
    assert "XE-99999" not in offered_keys(PATH)


def test_the_existence_check_looks_up_an_execution_key_under_its_own_label():
    """`TestExecution` carries no `WorkItem` label, so without its own lookup every
    `XE-…` id resolved through nothing and was rejected as missing from the graph."""
    labels = dict(KEY_LOOKUPS)
    assert labels["TestExecution"] == "key"
    assert labels["Test"] == "key"


def test_the_existence_check_queries_every_label_it_lists():
    asked: list[str] = []

    class Recording:
        prefix = ""

        def label(self, name):
            return f"`{name}`"

        def read(self, cypher, **params):
            asked.append(cypher)
            return []

    existing_keys(Recording(), ["XE-1"])
    for label, _ in KEY_LOOKUPS:
        assert any(f"`{label}`" in cypher for cypher in asked), label


def test_truth_evidence_is_checked_against_the_truth_file_not_the_graph():
    truth = {"stale_states": [{"ado_key": "ADO-1", "jira_key": "KAFKA-1"}]}
    assert truth_evidence_ok("truth:stale_states:0", truth)
    assert not truth_evidence_ok("truth:stale_states:1", truth)
    assert not truth_evidence_ok("truth:renames:0", truth)
    assert not truth_evidence_ok("truth:stale_states:x", truth)


def test_a_truth_question_may_cite_a_truth_excerpt_the_graph_has_never_heard_of():
    path = {
        **PATH,
        "path_id": "stale_state:ADO-1-KAFKA-1",
        "shape": "stale_state",
        "type": "temporal",
        "gold_source": "truth",
        "anchors": ["ADO-1"],
        "answers": ["KAFKA-1"],
        "nodes": [
            {"role": "mirror_item", "label": "WorkItem", "key": "ADO-1", "title": "m", "props": {}},
            {
                "role": "source_item",
                "label": "WorkItem",
                "key": "KAFKA-1",
                "title": "s",
                "props": {},
            },
        ],
        "truth": [{"id": "truth:stale_states:0", "section": "stale_states", "index": 0}],
        "snippets": [],
    }
    q = question(
        type="temporal",
        gold_source="truth",
        expected_strategy="s6",
        source_path_id="stale_state:ADO-1-KAFKA-1",
        question="What is the real current state of the work mirrored by ADO-1?",
        gold_answer="It is already Resolved in Jira; the mirror still says Active.",
        gold_evidence=["truth:stale_states:0", "ADO-1"],
    )
    v = review(
        q,
        paths=[path],
        known={"ADO-1"},
        truth={"stale_states": [{"ado_key": "ADO-1", "jira_key": "KAFKA-1"}]},
    )
    assert v.ok, v.reasons


# ---------------------------------------------------------------------------- leakage


def test_a_question_that_names_the_answer_is_rejected():
    v = review(question(question="Did XT-10109 cover KAFKA-16448 and pass its last run?"))
    assert not v.ok
    assert "leak_answer_key" in v.reasons


def test_naming_the_anchor_is_not_leakage_it_is_the_question():
    v = review(question())
    assert "leak_answer_key" not in v.reasons
    assert "leak_evidence_key" not in v.reasons


def test_an_evidence_key_that_is_not_an_anchor_may_not_appear_in_the_question():
    path = {**PATH, "answers": []}
    v = review(
        question(question="Does XT-10109 still cover KAFKA-16448?"),
        paths=[path],
    )
    assert not v.ok
    assert "leak_evidence_key" in v.reasons


def test_a_question_that_restates_the_gold_answer_is_rejected():
    leaky = question(
        question="Which test covers it, given XT covers it and its last recorded run passed fine?",
        gold_answer="XT covers it and its last recorded run passed fine.",
        gold_evidence=["KAFKA-16448"],
    )
    v = review(leaky)
    assert not v.ok
    assert "leak_answer_text" in v.reasons


def test_the_shared_run_is_measured_in_words_and_ignores_punctuation_and_case():
    assert longest_shared_run("Who owns the clients component?", "The clients component.") == 3
    assert longest_shared_run("nothing alike", "completely different") == 0
    assert longest_shared_run("מי אחראי על רכיב clients", "רכיב clients") == 2


# ------------------------------------------------------------------------ bookkeeping


def test_two_questions_with_the_same_id_cannot_both_land():
    first = review(question())
    second = review_question(
        question(),
        batch_id="shard-02/001",
        paths={PATH["path_id"]: PATH},
        known_keys={"XT-10109", "KAFKA-16448", "a" * 40},
        truth={},
        schema=SCHEMA,
        seen_ids={first.row["id"]},
    )
    assert not second.ok
    assert "duplicate_id" in second.reasons


def test_the_same_question_text_twice_is_a_duplicate_however_it_is_numbered():
    v = review_question(
        question(id="q102"),
        batch_id="shard-02/001",
        paths={PATH["path_id"]: PATH},
        known_keys={"XT-10109", "KAFKA-16448", "a" * 40},
        truth={},
        schema=SCHEMA,
        seen_questions={" ".join(words(question()["question"]))},
    )
    assert not v.ok
    assert "duplicate_question" in v.reasons


# ---------------------------------------------------------------------------- balance


def _accepted(qid, qtype, lang):
    return Verdict(
        ok=True,
        reasons=[],
        row={"id": qid, "type": qtype, "lang": lang, "origin": "forged"},
        batch_id="shard-01/001",
    )


def test_balance_keeps_what_each_cell_needs_and_parks_the_slack_as_surplus():
    verdicts = [
        _accepted("q101", "global", "he"),
        _accepted("q102", "global", "he"),
        _accepted("q103", "global", "he"),
        _accepted("q104", "global", "en"),
    ]
    kept, surplus = select_balanced(verdicts, {("global", "he"): 2, ("global", "en"): 3})
    assert [v.row["id"] for v in kept] == ["q101", "q102", "q104"]
    assert [v.row["id"] for v in surplus] == ["q103"]


def test_balance_is_stable_so_two_merges_of_the_same_batches_keep_the_same_questions():
    verdicts = [_accepted(f"q1{i:02d}", "impact", "en") for i in range(5)]
    need = {("impact", "en"): 2}
    first, _ = select_balanced(verdicts, need)
    second, _ = select_balanced(list(reversed(verdicts)), need)
    assert [v.row["id"] for v in first] == [v.row["id"] for v in second]


# ------------------------------------------------------------------------ competency


def test_a_competency_question_is_carried_over_with_its_gold_left_pending():
    row = competency_row(
        {
            "id": "cq12",
            "type": "global",
            "lang": "en",
            "question": "What are the main themes of open bugs in clients?",
            "expected_strategy": "s5",
            "pair": "",
            "anchors": ["clients"],
        }
    )
    assert row["gold_source"] == "pending"
    assert row["gold_answer"] is None
    assert row["gold_evidence"] == []
    assert row["origin"] == "competency"
    assert row["source_path_id"] is None
    assert row["difficulty"] == DIFFICULTY_DEFAULT["global"]
    assert row["difficulty_source"] == "type_default"


def test_every_question_type_has_a_default_difficulty_so_no_row_lands_without_one():
    from brain.eval.paths import QUESTION_TYPES

    assert set(DIFFICULTY_DEFAULT) == set(QUESTION_TYPES)
    assert all(1 <= v <= 3 for v in DIFFICULTY_DEFAULT.values())


# ---------------------------------------------------------------------------- reading


def test_an_unreadable_output_file_is_a_failed_batch_not_a_crash(tmp_path: Path):
    shard = tmp_path / "shard-01"
    shard.mkdir(parents=True)
    (shard / "001.in.json").write_text(json.dumps({"batch_id": "shard-01/001", "paths": []}))
    (shard / "001.out.json").write_text("{ not json")
    outputs, failures = read_outputs(tmp_path)
    assert outputs == []
    assert failures and failures[0]["batch"] == "shard-01/001"
    assert "json" in failures[0]["reason"].lower()


def test_an_output_whose_batch_id_disagrees_with_its_filename_is_refused(tmp_path: Path):
    shard = tmp_path / "shard-01"
    shard.mkdir(parents=True)
    (shard / "001.in.json").write_text(json.dumps({"batch_id": "shard-01/001", "paths": []}))
    (shard / "001.out.json").write_text(json.dumps({"batch_id": "shard-02/007", "questions": []}))
    outputs, failures = read_outputs(tmp_path)
    assert outputs == []
    assert "batch_id" in failures[0]["reason"]


def test_an_output_with_no_input_beside_it_is_refused(tmp_path: Path):
    shard = tmp_path / "shard-01"
    shard.mkdir(parents=True)
    (shard / "001.out.json").write_text(json.dumps({"batch_id": "shard-01/001", "questions": []}))
    outputs, failures = read_outputs(tmp_path)
    assert outputs == []
    assert "input" in failures[0]["reason"].lower()


@pytest.mark.parametrize("qtype", ["traceability", "impact", "rationale", "global", "temporal"])
def test_the_schema_and_the_shape_registry_agree_on_the_question_types(qtype):
    assert qtype in SCHEMA["$defs"]["type"]["enum"]
