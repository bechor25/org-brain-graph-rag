"""The three guard layers that need a real server: `EXPLAIN`, the timeout, and READ mode.

The static half of the guard is asserted without a database in `tests/test_retrieve_guard.py`.
What cannot be faked is the planner's opinion and the server's clock, so this file is
deliberately small and fast: it plans the read queries against the live schema, proves the
plan check refuses a write even when the scanner is bypassed, and proves the 10s budget is
enforced by the server rather than by us waiting.

Nothing here writes. The one write it attempts is the attack it expects to be refused, and
it asserts afterwards that `_GuardTmp` does not exist.
"""

from __future__ import annotations

import time

import pytest
from neo4j import Query

from brain.config import Settings
from brain.graph.client import GraphClient
from brain.retrieve import cypher_guard as guard
from brain.retrieve.context import RetrieveContext
from brain.retrieve.cypher_guard import GuardError, run_cypher
from tests.test_retrieve_guard import ALLOWED, BLOCKED

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def ctx():
    settings = Settings()
    client = GraphClient(
        settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password, settings.neo4j_database
    )
    client.verify()
    try:
        yield RetrieveContext(client=client, settings=settings)
    finally:
        client.close()


def test_every_allowed_query_plans_against_the_live_schema(ctx) -> None:
    """A guard that lets nothing useful through is a broken tool, not a safe one."""
    planned = 0
    for name, cypher in ALLOWED:
        prepared = guard.prepare(cypher, limit=5)
        plan = guard.explain(ctx, prepared.cypher, {}, 10.0)
        assert guard.plan_write_operators(plan) == [], name
        planned += 1
    assert planned >= 10


def test_every_blocked_query_is_refused_before_the_driver(ctx) -> None:
    """The same table as the unit test, but through `run_cypher` with a live database behind it."""
    for name, cypher, reason in BLOCKED:
        with pytest.raises(GuardError) as exc:
            run_cypher(ctx, cypher, log=False)
        assert exc.value.reason == reason, name
    rows = ctx.read("MATCH (n:_GuardTmp) RETURN count(n) AS n")
    assert rows[0]["n"] == 0


def test_explain_sees_a_write_the_scanner_was_told_to_ignore(ctx, monkeypatch) -> None:
    """Layer 3 on its own: bypass the deny-list and the plan check still refuses.

    `EXPLAIN CREATE (n)` is *accepted* by the server under `RoutingControl.READ` — it
    plans but does not run — so this is the layer that catches anything the text scan
    misses, and it has to be asserted with the scan switched off.
    """
    monkeypatch.setattr(
        guard,
        "prepare",
        lambda cypher, limit=guard.DEFAULT_LIMIT: guard.PreparedCypher(
            cypher=cypher, original=cypher, limit=limit, limit_injected=False
        ),
    )
    with pytest.raises(GuardError) as exc:
        run_cypher(ctx, "CREATE (n:_GuardTmp {a: 1}) RETURN n.a AS a", log=False)
    assert exc.value.reason == "write-plan"
    assert "Create" in exc.value.detail
    assert ctx.read("MATCH (n:_GuardTmp) RETURN count(n) AS n")[0]["n"] == 0


def test_read_mode_still_refuses_when_everything_else_is_bypassed(ctx) -> None:
    """The bottom layer, unaided: the server rejects the write itself."""
    with pytest.raises(Exception) as exc:
        ctx.read("CREATE (n:_GuardTmp {a: 1}) RETURN n.a AS a")
    assert "read access mode" in str(exc.value).lower()
    assert ctx.read("MATCH (n:_GuardTmp) RETURN count(n) AS n")[0]["n"] == 0


def test_timeout_is_enforced_by_the_server(ctx) -> None:
    """A read-only query can still be an outage; the budget is the server's, not ours."""
    started = time.perf_counter()
    with pytest.raises(GuardError) as exc:
        run_cypher(
            ctx,
            "UNWIND range(1, 200000000) AS x RETURN count(x) AS n",
            timeout_s=0.5,
            log=False,
        )
    elapsed = time.perf_counter() - started
    assert exc.value.reason == "timeout"
    assert elapsed < 10.0, f"the client waited {elapsed:.1f}s for a 0.5s budget"


def test_a_read_query_returns_the_uniform_envelope(ctx) -> None:
    result = run_cypher(
        ctx,
        "MATCH (w:WorkItem) RETURN w.key AS key, w.title AS title ORDER BY w.key",
        limit=5,
        question="a few work items",
        log=False,
    )
    assert result.strategy == "s4"
    assert 1 <= len(result.items) <= 5
    assert {i.kind for i in result.items} == {"Row"}
    assert result.items[0].key.startswith(("KAFKA-", "ADO-"))
    assert result.cypher_used and result.cypher_used[0].rstrip().endswith("LIMIT 5")
    assert result.route["limit_injected"] is True
    assert result.latency_ms >= 0


def test_the_injected_limit_bounds_the_answer(ctx) -> None:
    result = run_cypher(ctx, "MATCH (c:Chunk) RETURN c.id AS id", limit=3, log=False)
    assert len(result.items) == 3


def test_a_query_object_carries_the_timeout_through_graphclient_read(ctx) -> None:
    """Documenting the mechanism: `read()` needed no change to gain a server-side timeout."""
    rows = ctx.read(Query("RETURN 1 AS one", timeout=5.0))
    assert rows == [{"one": 1}]
