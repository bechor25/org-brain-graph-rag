"""The attack surface of S4, as a table (spec §4.5, module 10).

`run_cypher` is the only place in this project where text an LLM wrote reaches the
database. Everything else is code we wrote; this one is code the model wrote, so it gets
the treatment: a table of write and injection attempts that must all be refused, and a
table of ordinary read queries that must all survive — because a guard that blocks
everything is not a guard, it is a broken tool.

These tests are pure: they exercise the static half of the guard (comment/literal masking,
the deny-list, the procedure allowlist, `LIMIT` injection) and never open a driver. The
half that needs a server — `EXPLAIN` plan inspection, the 10s timeout, the server's own
READ enforcement — is in `tests/live/test_retrieve_guard_live.py`.
"""

from __future__ import annotations

import pytest

from brain.retrieve import cypher_guard as guard
from brain.retrieve.cypher_guard import GuardError
from brain.retrieve.guard_cases import ALLOWED, BLOCKED, PLAN_BLOCKED


@pytest.mark.parametrize(("name", "cypher", "reason"), BLOCKED, ids=[c[0] for c in BLOCKED])
def test_blocked(name: str, cypher: str, reason: str) -> None:
    with pytest.raises(GuardError) as exc:
        guard.check(cypher)
    assert exc.value.reason == reason, f"{name}: blocked for {exc.value.reason}, want {reason}"
    assert exc.value.hint, "every rejection tells the caller what to do instead"


@pytest.mark.parametrize(("name", "cypher"), ALLOWED, ids=[c[0] for c in ALLOWED])
def test_allowed(name: str, cypher: str) -> None:
    guard.check(cypher)


def test_at_least_25_write_or_injection_cases() -> None:
    """The acceptance criterion is a number; keep it visible in the test file."""
    assert len(BLOCKED) >= 45
    assert len(ALLOWED) >= 10


@pytest.mark.parametrize(
    ("name", "cypher", "reason"), PLAN_BLOCKED, ids=[c[0] for c in PLAN_BLOCKED]
)
def test_plan_blocked_cases_are_also_refused_statically(
    name: str, cypher: str, reason: str
) -> None:
    """Layer 3's table is not a hole in layer 2.

    `PLAN_BLOCKED` exists to prove the `EXPLAIN` layer refuses these on its own (the live
    test runs them with the deny-list switched off). That is only interesting while the
    deny-list *also* refuses them — otherwise the table would be documenting a bypass.
    """
    assert reason == "write-plan"
    with pytest.raises(GuardError) as exc:
        guard.check(cypher)
    assert exc.value.reason == "write-verb"


def test_insert_is_a_write_verb() -> None:
    """GQL's CREATE. The deny-list learned the word after the plan layer caught it."""
    assert "INSERT" in guard.WRITE_TOKENS
    with pytest.raises(GuardError) as exc:
        guard.check("INSERT (n:Foo {a: 1}) RETURN n.a AS a")
    assert exc.value.reason == "write-verb"


def test_a_dotted_call_is_refused_whatever_its_root() -> None:
    """The allowlist is the rule; an unfamiliar namespace is not an exemption from it."""
    with pytest.raises(GuardError) as exc:
        guard.check("CALL n10s.rdf.import.fetch('http://x/y.ttl', 'Turtle') YIELD x RETURN x")
    assert exc.value.reason == "procedure-not-allowed"
    assert "n10s" in exc.value.detail


def test_backticks_do_not_hide_a_procedure_name() -> None:
    """`sanitize` masks backticked identifiers; the call scan reads them, as the parser does."""
    assert "apoc" not in guard.sanitize("CALL `apoc`.`cypher`.`run`('x', {}) YIELD value")
    assert "apoc.cypher.run" in guard._call_text("CALL `apoc`.`cypher`.`run`('x', {}) YIELD value")
    with pytest.raises(GuardError) as exc:
        guard.check("CALL `apoc`.`cypher`.`run`('CREATE (n)', {}) YIELD value RETURN value")
    assert exc.value.reason == "procedure-not-allowed"


def test_cyphers_own_value_functions_still_work() -> None:
    """A guard that refuses `duration.between` refuses temporal questions."""
    guard.check("MATCH (w:WorkItem) RETURN duration.between(w.created, w.updated).days AS d")
    guard.check("RETURN vector.similarity.cosine($a, $b) AS score")


def test_show_is_an_allowlist_not_a_deny_list() -> None:
    guard.check("SHOW INDEXES YIELD name WHERE name STARTS WITH 'chunk' RETURN name")
    guard.check("SHOW CONSTRAINTS YIELD name RETURN name")
    for cypher in (
        "SHOW SETTINGS YIELD name RETURN name",
        "SHOW TRANSACTIONS YIELD transactionId RETURN transactionId",
        "SHOW USERS YIELD user RETURN user",
        "SHOW PRIVILEGES YIELD access RETURN access",
        "SHOW PROCEDURES YIELD name RETURN name",
        "SHOW FUNCTIONS YIELD name RETURN name",
    ):
        with pytest.raises(GuardError) as exc:
            guard.check(cypher)
        assert exc.value.reason == "show-not-allowed", cypher
        assert exc.value.hint


# ------------------------------------------------------------------ masking / limits


def test_sanitize_keeps_positions_and_hides_literals() -> None:
    cypher = "MATCH (n) WHERE n.t = 'DELETE me' /* SET */ RETURN n.id AS id // DROP"
    masked = guard.sanitize(cypher)
    assert len(masked) == len(cypher)
    assert "DELETE" not in masked and "SET" not in masked and "DROP" not in masked
    assert masked.index("RETURN") == cypher.index("RETURN")


def test_sanitize_hides_backtick_identifiers() -> None:
    masked = guard.sanitize("MATCH (n:`_RetrChunk`) RETURN n.id AS id")
    assert "_RetrChunk" not in masked


def test_limit_is_injected_when_missing() -> None:
    out, injected = guard.inject_limit("MATCH (n:Chunk) RETURN n.id AS id", 100)
    assert injected is True
    assert out.rstrip().endswith("LIMIT 100")


def test_limit_is_left_alone_when_present() -> None:
    out, injected = guard.inject_limit("MATCH (n:Chunk) RETURN n.id AS id LIMIT 7", 100)
    assert injected is False
    assert out.endswith("LIMIT 7")


def test_limit_after_return_only() -> None:
    """`WITH n LIMIT 1` bounds an intermediate row set, not the answer."""
    cypher = "MATCH (n:Chunk) WITH n LIMIT 1 MATCH (n)-[:MENTIONS]->(e) RETURN e.id AS id"
    out, injected = guard.inject_limit(cypher, 50)
    assert injected is True
    assert out.rstrip().endswith("LIMIT 50")


def test_limit_ignores_the_word_inside_a_literal() -> None:
    cypher = "MATCH (c:Chunk) WHERE c.text CONTAINS 'LIMIT 5' RETURN c.id AS id"
    out, injected = guard.inject_limit(cypher, 25)
    assert injected is True
    assert out.rstrip().endswith("LIMIT 25")


def test_limit_is_not_injected_into_a_union() -> None:
    """`… UNION … LIMIT n` is a syntax error, not a bound."""
    cypher = "MATCH (d:Document) RETURN d.key AS key UNION MATCH (w:WorkItem) RETURN w.key AS key"
    out, injected = guard.inject_limit(cypher, 10)
    assert injected is False
    assert out == cypher


def test_limit_is_not_injected_after_finish() -> None:
    """`FINISH` returns no rows at all; there is nothing to limit."""
    out, injected = guard.inject_limit("MATCH (c:Chunk) WHERE c.id IS NOT NULL FINISH", 10)
    assert injected is False
    assert out.endswith("FINISH")


def test_trailing_semicolon_is_not_a_second_statement() -> None:
    prepared = guard.prepare("MATCH (n:Chunk) RETURN n.id AS id;", limit=10)
    assert ";" not in prepared.cypher
    assert prepared.cypher.rstrip().endswith("LIMIT 10")


def test_prepare_reports_what_it_changed() -> None:
    prepared = guard.prepare("MATCH (n:Chunk) RETURN n.id AS id", limit=42)
    assert prepared.limit_injected is True
    assert prepared.limit == 42
    assert prepared.original.startswith("MATCH")


def test_too_long_is_refused() -> None:
    with pytest.raises(GuardError) as exc:
        guard.check("MATCH (n:Chunk) RETURN n.id AS id // " + "x" * guard.MAX_CYPHER_CHARS)
    assert exc.value.reason == "too-long"


# ------------------------------------------------------------------ the row ceiling


def test_a_wide_column_is_clipped_to_one_snippet() -> None:
    from brain.retrieve.pack import SNIPPET_CHARS

    item = guard._row_item({"key": "KIP-848", "body": "x" * 200_000}, 0)
    assert len(item.props["body"]) <= SNIPPET_CHARS
    assert item.props["_clipped"] == ["body"]
    assert item.key == "KIP-848"


def test_a_row_stays_inside_the_answer_budget() -> None:
    """The 4k ceiling is not the packer's alone: one item can blow it by itself."""
    from brain.retrieve.pack import BUDGET_TOKENS, item_tokens

    item = guard._row_item({"body": "x" * 220_855}, 0)
    assert item_tokens(item) <= BUDGET_TOKENS


def test_many_wide_columns_drop_the_widest_and_say_so() -> None:
    """Forty columns of 590 characters each are inside the snippet limit and over the row's."""
    row = {"key": "KAFKA-1", "a": "a" * 590, "b": "b" * 580, "c": "c" * 570, "d": "d" * 560}
    item = guard._row_item(row, 0)
    assert item.props["key"] == "KAFKA-1", "the identifier survives the cut"
    assert item.props["_clipped"] == ["a"], "the widest column went first, and it said so"
    assert sum(len(str(v)) for k, v in item.props.items() if k != "_clipped") <= (
        guard.MAX_ROW_PROP_CHARS
    )


def test_strings_inside_a_collected_list_are_clipped_too() -> None:
    item = guard._row_item({"key": "k", "quotes": ["q" * 5_000, {"text": "t" * 5_000}]}, 0)
    assert len(item.props["quotes"][0]) <= 600
    assert len(item.props["quotes"][1]["text"]) <= 600
    assert item.props["_clipped"] == ["quotes"]


def test_a_row_that_fits_is_untouched() -> None:
    item = guard._row_item({"key": "KAFKA-1", "status": "Resolved"}, 0)
    assert item.props == {"key": "KAFKA-1", "status": "Resolved"}
    assert "_clipped" not in item.props


def test_an_unnamed_key_column_cannot_be_a_document_body() -> None:
    item = guard._row_item({"whatever": "x" * 10_000}, 3)
    assert len(item.key) <= guard.MAX_ROW_KEY_CHARS


# ------------------------------------------------------------------ plan inspection


def test_write_operators_are_found_in_a_plan() -> None:
    plan = {
        "operatorType": "ProduceResults@neo4j",
        "children": [
            {
                "operatorType": "Projection@neo4j",
                "children": [{"operatorType": "SetProperty@neo4j", "children": []}],
            }
        ],
    }
    assert guard.plan_write_operators(plan) == ["SetProperty"]


def test_a_read_plan_has_no_write_operators() -> None:
    plan = {
        "operatorType": "ProduceResults@neo4j",
        "children": [{"operatorType": "NodeCountFromCountStore@neo4j", "children": []}],
    }
    assert guard.plan_write_operators(plan) == []


def test_subquery_foreach_counts_as_a_write() -> None:
    plan = {"operatorType": "SubqueryForeach@neo4j", "children": []}
    assert guard.plan_write_operators(plan) == ["SubqueryForeach"]


# ------------------------------------------------------------------ rejection is logged


class _NeverRead:
    """A context whose database would raise if the guard ever let a write reach it."""

    prefix = ""

    def read(self, *a: object, **kw: object) -> list[dict]:
        raise AssertionError("the guard let a rejected query reach the database")

    @property
    def client(self):  # pragma: no cover - the same assertion, one layer down
        raise AssertionError("the guard let a rejected query reach the database")


def test_rejection_is_logged_and_never_touches_the_database(tmp_path) -> None:
    from brain.retrieve.cypher_guard import run_cypher
    from brain.retrieve.log import read_log

    log_path = tmp_path / "retrieval.jsonl"
    with pytest.raises(GuardError) as exc:
        run_cypher(_NeverRead(), "MATCH (n) DETACH DELETE n", log_path=log_path)
    assert exc.value.reason == "write-verb"

    records = read_log(log_path)
    assert len(records) == 1
    assert records[0]["strategy"] == "s4"
    assert records[0]["rejected"] == "write-verb"
    assert records[0]["cypher"] == ["MATCH (n) DETACH DELETE n"]
    assert records[0]["hit_ids"] == []


class _PlannerFails:
    """A context whose `EXPLAIN` fails with a `DatabaseError` — not a `ClientError`."""

    prefix = ""

    @property
    def client(self):
        return self

    def explain(self, query: object, **params: object) -> dict:
        from neo4j.exceptions import DatabaseError

        raise DatabaseError("Neo.DatabaseError.Statement.ExecutionFailed: planner exploded")

    def read(self, *a: object, **kw: object) -> list[dict]:
        raise AssertionError("the guard ran a query it could not plan")


def test_a_planner_failure_is_a_refusal_not_a_stack_trace() -> None:
    """`Neo4jError`, not only `ClientError`: a plan can fail as a DatabaseError."""
    from brain.retrieve.cypher_guard import run_cypher

    with pytest.raises(GuardError) as exc:
        run_cypher(_PlannerFails(), "MATCH (n:Chunk) RETURN n.id AS id", log=False)
    assert exc.value.reason == "invalid-cypher"
    assert exc.value.hint


def test_guard_error_is_an_error_hint_pair() -> None:
    with pytest.raises(GuardError) as exc:
        guard.check("CREATE (n:Foo)")
    payload = exc.value.as_dict()
    assert set(payload) == {"error", "hint", "reason"}
    assert payload["reason"] == "write-verb"
