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
from brain.retrieve.guard_cases import ALLOWED, BLOCKED


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
    assert len(BLOCKED) >= 25
    assert len(ALLOWED) >= 10


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


def test_guard_error_is_an_error_hint_pair() -> None:
    with pytest.raises(GuardError) as exc:
        guard.check("CREATE (n:Foo)")
    payload = exc.value.as_dict()
    assert set(payload) == {"error", "hint", "reason"}
    assert payload["reason"] == "write-verb"
