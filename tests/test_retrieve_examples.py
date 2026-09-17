"""The example bank, and the rule that nothing enters it on trust.

The interesting assertions here are the *rejections*. An example bank is a set of patterns
every future query will copy, so the merge step has to be hostile: a write verb, a type
that is not one of the four, a query with no `cypher` at all, and — the one that matters —
a query that runs and returns nothing. All four are refused without a database, which is
also how these tests stay in `make check`.
"""

from __future__ import annotations

import json

import pytest

from brain.retrieve import examples as ex
from brain.retrieve.cypher_guard import GuardError


class _NoDatabase:
    """Everything the pure paths need; anything that reaches the graph is a test failure."""

    prefix = ""

    def label(self, name: str) -> str:
        return f"`{name}`"

    def read(self, *a: object, **kw: object):
        raise AssertionError("this path must not touch the database")


class _Components(_NoDatabase):
    """…except the one read `bind_params` legitimately makes."""

    def read(self, cypher: str, **params: object):
        assert "Component" in cypher
        return [{"name": "streams"}, {"name": "clients"}, {"name": "connect"}]


class _Graph(_NoDatabase):
    """The smallest thing `run_cypher` needs: a plan and some rows.

    The merge step is the one place in the bank pipeline that *runs* what the model wrote,
    so the accept path cannot be asserted against a context that refuses to be read. The
    plan is a read plan and the rows are whatever the test hands it — which is how "an
    example that returns nothing is refused" becomes a unit test instead of a live one.
    """

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = [] if rows is None else rows
        self.ran: list[str] = []

    @property
    def client(self):
        return self

    def explain(self, query: object, **params: object) -> dict:
        return {"operatorType": "ProduceResults@neo4j", "children": []}

    def read(self, query: object, **params: object):
        self.ran.append(str(getattr(query, "text", query)))
        return list(self.rows)


# ------------------------------------------------------------------ the bank


def test_every_question_type_has_a_seed_example() -> None:
    """A type with no example is a type where the model invents a schema."""
    assert {e["type"] for e in ex.SEED_EXAMPLES} == set(ex.QUESTION_TYPES)


def test_the_seeds_are_unique_among_themselves() -> None:
    """The bank's own reference answers cannot duplicate each other."""
    keys = [k for e in ex.SEED_EXAMPLES for k in ex.dedupe_keys(e)]
    assert len(keys) == len(set(keys))


def test_the_temporal_seed_asks_for_the_end_of_the_day() -> None:
    """The same cut-off `status_at` uses; midnight answers a different question (S6 vs bank)."""
    seed = next(e for e in ex.SEED_EXAMPLES if e["id"] == "seed-temporal-01")
    assert "duration('P1D')" in seed["cypher"]
    assert "<= datetime($date)" not in seed["cypher"]
    assert "end of that day" in seed["explanation"]


def test_the_impact_seed_requires_a_run_that_failed() -> None:
    """`run IS NULL` called every never-executed test a failure."""
    seed = next(e for e in ex.SEED_EXAMPLES if e["id"] == "seed-impact-01")
    assert "t IS NOT NULL AND run.status = 'FAIL'" in seed["cypher"]
    assert "run IS NULL OR" not in seed["cypher"]


def test_the_ado_seed_filters_the_source_it_was_asked_about() -> None:
    seed = next(e for e in ex.SEED_EXAMPLES if e["id"] == "seed-traceability-02")
    assert "w.source = $source" in seed["cypher"]
    assert seed["params"]["source"] == "ado"


def test_seed_examples_pass_the_guard() -> None:
    for example in ex.SEED_EXAMPLES:
        ex.guard.check(example["cypher"])


def test_cypher_examples_filters_by_type(tmp_path) -> None:
    bank = tmp_path / "bank.jsonl"
    ex.write_bank(
        [
            {"id": "a", "type": "impact", "question": "q1", "cypher": "MATCH (n) RETURN n.id AS i"},
            {"id": "b", "type": "temporal", "question": "q2", "cypher": "RETURN 1 AS x"},
        ],
        bank,
    )
    got = ex.cypher_examples("impact", path=bank)
    assert [e["id"] for e in got] == ["a"]


def test_cypher_examples_falls_back_to_the_seeds(tmp_path) -> None:
    """A bank that answers nothing for a type is worse than no bank at all."""
    bank = tmp_path / "bank.jsonl"
    ex.write_bank([{"id": "a", "type": "impact", "question": "q", "cypher": "RETURN 1 AS x"}], bank)
    got = ex.cypher_examples("rationale", path=bank)
    assert [e["id"] for e in got] == ["seed-rationale-01"]


def test_cypher_examples_rejects_an_unknown_type() -> None:
    with pytest.raises(ValueError, match="question_type"):
        ex.cypher_examples("global")


def test_bank_round_trips(tmp_path) -> None:
    bank = tmp_path / "bank.jsonl"
    rows = [{"id": "a", "type": "impact", "question": "q", "cypher": "RETURN 1 AS x"}]
    assert ex.read_bank(ex.write_bank(rows, bank)) == rows


def test_a_missing_bank_is_the_seeds(tmp_path) -> None:
    assert ex.cypher_examples(path=tmp_path / "nothing.jsonl")


# ------------------------------------------------------------------ parameters


def test_params_are_repointed_at_this_questions_anchors() -> None:
    bound = ex.bind_params(
        _Components(),
        "What was the status of KAFKA-14648 on 2024-03-01?",
        {"params": {"key": "KAFKA-15538", "date": "2024-01-16"}},
    )
    assert bound == {"key": "KAFKA-14648", "date": "2024-03-01"}


def test_a_component_is_matched_against_the_graphs_own_names() -> None:
    bound = ex.bind_params(
        _Components(), "Who owns component `clients`?", {"params": {"component": "streams"}}
    )
    assert bound == {"component": "clients"}


def test_an_unknown_component_keeps_the_example_value() -> None:
    """A parameter the question does not name must stay bound, or the query cannot run."""
    bound = ex.bind_params(
        _Components(), "Who owns the most work?", {"params": {"component": "streams"}}
    )
    assert bound == {"component": "streams"}


def test_versions_split_low_and_high() -> None:
    bound = ex.bind_params(
        _NoDatabase(),
        "What changed between 3.7 and 3.8?",
        {"params": {"version_low": "3.0", "version_high": "3.1"}},
    )
    assert bound == {"version_low": "3.7", "version_high": "3.8"}


def test_params_hint_reads_the_query() -> None:
    assert ex._params_hint("MATCH (n {key: $key}) WHERE n.at < $date RETURN n.id AS i") == {
        "date": "",
        "key": "",
    }


# ------------------------------------------------------------------ the batch


def test_batch_payload_carries_schema_types_questions_and_style() -> None:
    payload = ex.batch_payload(
        {"labels": [], "relationships": []},
        [{"question_id": "cq01", "type": "traceability", "question": "q"}],
    )
    assert payload["batch_id"] == "cypher-001"
    assert [t["name"] for t in payload["question_types"]] == list(ex.QUESTION_TYPES)
    assert len(payload["style_examples"]) == len(ex.SEED_EXAMPLES)
    assert payload["questions"][0]["question_id"] == "cq01"
    assert payload["output_contract"]["path"].endswith("001.out.json")


def test_a_global_question_is_marked_as_s5s_job() -> None:
    rows = [{"id": "cq12", "type": "global", "question": "themes?", "anchors": ["clients"]}]
    question = ex._questions_for_batch(rows)[0]
    assert "S5" in question["note"]


def test_a_typed_question_carries_no_note() -> None:
    rows = [{"id": "cq03", "type": "traceability", "question": "who owns?", "anchors": []}]
    assert "note" not in ex._questions_for_batch(rows)[0]


# ------------------------------------------------------------------ validation


def test_a_write_query_never_enters_the_bank() -> None:
    accepted, rejection = ex.validate_example(
        _NoDatabase(),
        {
            "question_id": "cq03",
            "type": "traceability",
            "question": "q",
            "cypher": "MATCH (n:WorkItem) SET n.owner = 'me' RETURN n.key AS key",
        },
    )
    assert accepted is None
    assert rejection["reason"] == "write-verb"


def test_an_unknown_type_never_enters_the_bank() -> None:
    accepted, rejection = ex.validate_example(
        _NoDatabase(),
        {"question_id": "cq12", "type": "global", "cypher": "MATCH (n) RETURN n.id AS id"},
    )
    assert accepted is None
    assert rejection["reason"] == "unknown-type"


def test_a_null_cypher_is_recorded_not_dropped() -> None:
    accepted, rejection = ex.validate_example(
        _NoDatabase(),
        {
            "question_id": "cq07",
            "type": "impact",
            "cypher": None,
            "explanation": "no path from Entity to Version in this schema",
        },
    )
    assert accepted is None
    assert rejection["reason"] == "no-cypher"
    assert "no path" in rejection["detail"]


def test_merge_keeps_the_seeds_when_every_answer_is_refused(tmp_path) -> None:
    batch_dir = tmp_path / "cypher"
    batch_dir.mkdir()
    (batch_dir / "001.in.json").write_text(
        json.dumps({"questions": [{"question_id": "cq03", "type": "traceability"}]}),
        encoding="utf-8",
    )
    (batch_dir / "001.out.json").write_text(
        json.dumps(
            {
                "answers": [
                    {
                        "question_id": "cq03",
                        "type": "traceability",
                        "cypher": "MATCH (n) DETACH DELETE n",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bank = tmp_path / "bank.jsonl"
    report = ex.merge_batch(
        _NoDatabase(), batch_dir=batch_dir, bank_path=bank, report_path=tmp_path / "merge.json"
    )
    assert report["accepted"] == 0
    assert report["rejected"][0]["reason"] == "write-verb"
    assert report["bank_size"] == len(ex.SEED_EXAMPLES)
    assert len(ex.read_bank(bank)) == len(ex.SEED_EXAMPLES)


def test_merge_reports_an_unreadable_output_file(tmp_path) -> None:
    batch_dir = tmp_path / "cypher"
    batch_dir.mkdir()
    (batch_dir / "001.out.json").write_text("{not json", encoding="utf-8")
    report = ex.merge_batch(
        _NoDatabase(),
        batch_dir=batch_dir,
        bank_path=tmp_path / "b.jsonl",
        report_path=tmp_path / "merge.json",
    )
    assert report["rejected"][0]["reason"] == "unreadable"


def test_guard_error_reasons_are_the_rejection_reasons() -> None:
    """The bank's vocabulary of refusals is the guard's, not a second one."""
    with pytest.raises(GuardError) as exc:
        ex.guard.check("CALL apoc.periodic.iterate('x', 'y', {}) YIELD batches RETURN batches")
    assert exc.value.reason == "procedure-not-allowed"


def test_an_answer_that_returns_a_row_enters_the_bank(tmp_path) -> None:
    """The accept path, end to end: guard, run, one row, and into the bank it goes."""
    batch_dir = tmp_path / "cypher"
    batch_dir.mkdir()
    (batch_dir / "001.in.json").write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "question_id": "cq03",
                        "type": "traceability",
                        "lang": "en",
                        "question": "Who owns component `streams`?",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (batch_dir / "001.out.json").write_text(
        json.dumps(
            {
                "batch_id": "cypher-001",
                "answers": [
                    {
                        "question_id": "cq03",
                        "type": "traceability",
                        "question": "Who owns component `streams`?",
                        "cypher": "MATCH (c:Component {name: $component})<-[:IN_COMPONENT]-"
                        "(w:WorkItem)-[:ASSIGNED_TO]->(p:Person) "
                        "RETURN p.display AS person, count(w) AS items "
                        "ORDER BY items DESC LIMIT 10",
                        "params": {"component": "streams"},
                        "explanation": "ownership is assignment count per person",
                        "uses_labels": ["Component", "WorkItem", "Person"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    ctx = _Graph([{"person": "lbrutschy", "items": 76}])
    bank = tmp_path / "bank.jsonl"

    report = ex.merge_batch(
        ctx, batch_dir=batch_dir, bank_path=bank, report_path=tmp_path / "merge.json"
    )

    assert report["accepted"] == 1
    assert report["rejected"] == []
    assert report["bank_size"] == len(ex.SEED_EXAMPLES) + 1
    assert report["per_type"]["traceability"] == 3
    assert ctx.ran, "the merge step RUNS the query rather than trusting it"
    accepted = ex.read_bank(bank)[-1]
    assert accepted["id"] == "traceability-cq03"
    assert accepted["source"] == "cypher-author"
    assert accepted["rows"] == 1
    assert accepted["lang"] == "en"
    assert accepted["params"] == {"component": "streams"}
    assert accepted["validated_at"]


def test_an_example_that_returns_nothing_is_refused(tmp_path) -> None:
    """A query that parses, passes the guard and answers nothing is the worst kind of example."""
    accepted, rejection = ex.validate_example(
        _Graph([]),
        {
            "question_id": "cq06",
            "type": "impact",
            "question": "which components have failing tests?",
            "cypher": "MATCH (c:Component) WHERE c.name = $component RETURN c.name AS name",
            "params": {"component": "nope"},
        },
    )
    assert accepted is None
    assert rejection["reason"] == "no-rows"


def test_a_sixth_example_of_a_full_type_is_refused(tmp_path) -> None:
    """3–5 per type (plan); the seed plus five candidates is one too many."""
    batch_dir = tmp_path / "cypher"
    batch_dir.mkdir()
    answers = [
        {
            "question_id": f"cq{i:02d}",
            "type": "temporal",
            "question": f"q{i}",
            # Distinct queries: five copies of one query are one example, not five.
            "cypher": f"MATCH (s:StatusChange) WHERE s.field = 'f{i}' RETURN s.id AS id LIMIT 5",
            "params": {},
        }
        for i in range(5)
    ]
    (batch_dir / "001.out.json").write_text(json.dumps({"answers": answers}), encoding="utf-8")
    report = ex.merge_batch(
        _Graph([{"id": "s1"}]),
        batch_dir=batch_dir,
        bank_path=tmp_path / "bank.jsonl",
        report_path=tmp_path / "merge.json",
    )
    assert report["per_type"]["temporal"] == ex.MAX_PER_TYPE
    assert [r["reason"] for r in report["rejected"]] == ["type-full"]
    assert report["validated"] == 5, "five ran and returned rows"
    assert report["accepted"] == 4, "…and four fitted, which is a different number"


def test_the_bank_is_rewritten_not_appended(tmp_path) -> None:
    """Merging twice must not grow the bank — the same rerun rule as every other step."""
    batch_dir = tmp_path / "cypher"
    batch_dir.mkdir()
    (batch_dir / "001.out.json").write_text(
        json.dumps(
            {
                "answers": [
                    {
                        "question_id": "cq14",
                        "type": "temporal",
                        "question": "status of KAFKA-1 on 2024-01-16?",
                        "cypher": "MATCH (s:StatusChange) RETURN s.id AS id LIMIT 1",
                        "params": {},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bank = tmp_path / "bank.jsonl"
    report_path = tmp_path / "merge.json"
    first = ex.merge_batch(
        _Graph([{"id": "s1"}]), batch_dir=batch_dir, bank_path=bank, report_path=report_path
    )
    second = ex.merge_batch(
        _Graph([{"id": "s1"}]), batch_dir=batch_dir, bank_path=bank, report_path=report_path
    )
    assert first["bank_size"] == second["bank_size"] == len(ex.SEED_EXAMPLES) + 1
    assert len(ex.read_bank(bank)) == second["bank_size"]


def test_the_merge_writes_a_step_report_with_every_outcome(tmp_path) -> None:
    """Why cq09 was refused is knowable at validation time and nowhere else afterwards."""
    batch_dir = tmp_path / "cypher"
    batch_dir.mkdir()
    (batch_dir / "001.out.json").write_text(
        json.dumps(
            {
                "answers": [
                    {
                        "question_id": "cq14",
                        "type": "temporal",
                        "question": "status?",
                        "cypher": "MATCH (s:StatusChange) RETURN s.id AS id LIMIT 1",
                        "params": {},
                    },
                    {
                        "question_id": "cq09",
                        "type": "rationale",
                        "question": "why?",
                        "cypher": "MATCH (d:Document) SET d.x = 1 RETURN d.key AS key",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "cypher_examples.json"
    report = ex.merge_batch(
        _Graph([{"id": "s1"}]),
        batch_dir=batch_dir,
        bank_path=tmp_path / "bank.jsonl",
        report_path=report_path,
    )
    assert report["report_path"] == str(report_path)
    written = json.loads(report_path.read_text(encoding="utf-8"))
    outcomes = {row["question_id"]: row for row in written["per_question"]}
    assert outcomes["cq14"]["outcome"] == "accepted"
    assert outcomes["cq14"]["rows"] == 1
    assert outcomes["cq09"]["outcome"] == "rejected"
    assert outcomes["cq09"]["reason"] == "write-verb"


# ------------------------------------------------------------------ duplicates


def _batch(tmp_path, answers: list[dict]):
    batch_dir = tmp_path / "cypher"
    batch_dir.mkdir()
    (batch_dir / "001.out.json").write_text(json.dumps({"answers": answers}), encoding="utf-8")
    return batch_dir


def test_an_answer_identical_to_a_seed_is_recorded_not_accepted(tmp_path) -> None:
    """Four of the seventeen bank rows were copies of seeds; a bank is patterns, not rows."""
    seed = next(e for e in ex.SEED_EXAMPLES if e["id"] == "seed-temporal-01")
    batch_dir = _batch(
        tmp_path,
        [
            {
                "question_id": "cq14",
                "type": "temporal",
                "question": "something else entirely",
                "cypher": seed["cypher"],
                "params": seed["params"],
            }
        ],
    )
    report = ex.merge_batch(
        _Graph([{"status": "Reopened"}]),
        batch_dir=batch_dir,
        bank_path=tmp_path / "bank.jsonl",
        report_path=tmp_path / "merge.json",
    )
    assert report["accepted"] == 0
    assert report["rejected"][0]["reason"] == "duplicate-of-seed"
    assert report["rejected"][0]["detail"] == "same cypher as seed-temporal-01"
    assert report["bank_size"] == len(ex.SEED_EXAMPLES)


def test_a_second_answer_to_a_seeds_question_is_a_duplicate_too(tmp_path) -> None:
    """Two answers to one question is how two tools of one server come to disagree."""
    seed = next(e for e in ex.SEED_EXAMPLES if e["id"] == "seed-temporal-01")
    batch_dir = _batch(
        tmp_path,
        [
            {
                "question_id": "cq14",
                "type": "temporal",
                "question": seed["question"],
                "cypher": "MATCH (s:StatusChange) WHERE s.at <= datetime($date) "
                "RETURN s.to AS status LIMIT 1",
                "params": {"date": "2024-01-16"},
            }
        ],
    )
    report = ex.merge_batch(
        _Graph([{"status": "Resolved"}]),
        batch_dir=batch_dir,
        bank_path=tmp_path / "bank.jsonl",
        report_path=tmp_path / "merge.json",
    )
    assert report["accepted"] == 0
    assert report["rejected"][0]["reason"] == "duplicate-of-seed"
    assert report["rejected"][0]["detail"] == "same question as seed-temporal-01"


def test_two_answers_with_the_same_query_keep_the_first(tmp_path) -> None:
    """The Hebrew twin is the same Cypher; it costs a slot and teaches nothing new."""
    cypher = "MATCH (d:Document {key: $key})-[:DECIDES]->(e:Entity) RETURN e.name AS name LIMIT 5"
    batch_dir = _batch(
        tmp_path,
        [
            {
                "question_id": "cq09",
                "type": "rationale",
                "question": "Why was the design in KIP-932 chosen?",
                "cypher": cypher,
                "params": {"key": "KIP-932"},
            },
            {
                "question_id": "cq18",
                "type": "rationale",
                "question": "\u05dc\u05de\u05d4 \u05e0\u05d1\u05d7\u05e8?",
                "cypher": cypher,
                "params": {"key": "KIP-932"},
            },
        ],
    )
    report = ex.merge_batch(
        _Graph([{"name": "share groups"}]),
        batch_dir=batch_dir,
        bank_path=tmp_path / "bank.jsonl",
        report_path=tmp_path / "merge.json",
    )
    assert report["accepted"] == 1
    assert [r["reason"] for r in report["rejected"]] == ["duplicate"]
    assert report["rejected"][0]["detail"] == "same cypher as rationale-cq09"
    assert report["duplicates"] == 1
    assert report["answers"] == 2, "two answers arrived; one of them was a copy"


def test_the_report_counts_unique_examples_per_type_and_says_which_are_thin(tmp_path) -> None:
    """The 3–5 rule is about distinct examples, and the exit code is about this number."""
    report = ex.merge_batch(
        _NoDatabase(),
        batch_dir=_batch(tmp_path, []),
        bank_path=tmp_path / "bank.jsonl",
        report_path=tmp_path / "merge.json",
    )
    seeds_per_type = {
        t: sum(1 for e in ex.SEED_EXAMPLES if e["type"] == t) for t in ex.QUESTION_TYPES
    }
    assert report["unique_per_type"] == seeds_per_type
    assert report["min_per_type"] == 3
    assert report["thin_types"] == sorted(t for t, n in seeds_per_type.items() if n < 3)
    assert report["ok"] is (not report["thin_types"])


def test_the_merge_report_carries_the_sha_it_was_measured_on(tmp_path) -> None:
    """Plan 1 lesson: a number without the commit it was measured on is STALE, not evidence."""
    report = ex.merge_batch(
        _NoDatabase(),
        batch_dir=_batch(tmp_path, []),
        bank_path=tmp_path / "bank.jsonl",
        report_path=tmp_path / "merge.json",
    )
    assert report["sha"] == ex.head_sha()
    assert report["generated_at"]
