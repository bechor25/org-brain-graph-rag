"""Shared fixtures, and the rule that `make check` never talks to anything real.

`test_unimplemented_step_exits_2_and_names_plan` used to invoke `brain chunk` to prove the
step was a stub. The day `chunk` stopped being a stub, that one line quietly ran the entire
pipeline — Neo4j, Ollama, 10,884 embeddings — inside `make check`, for 84 seconds, and
nothing failed to say so. `_no_real_connections` is the guard that makes the next one of
those an error instead of a slow test: outside `-m live` (and `-m network`, which respx
mocks), opening a Neo4j driver or letting an httpx request reach the wire raises.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from brain.config import get_settings
from tests.guards import ForbiddenConnection


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_connections(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Fail loudly on a real Neo4j or HTTP connection from an unmarked test.

    The HTTP guard sits on `httpcore`'s connection pool — the layer that opens the socket —
    because that is where respx's default mocker sits too. respx installs its patch when
    the test body starts, after this fixture, so a mocked request goes to respx and only a
    request nothing mocked reaches this and raises.
    """
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("network"):
        yield
        return

    import httpcore
    import neo4j

    def _forbidden_driver(uri: str, *args: Any, **kwargs: Any):
        raise ForbiddenConnection(
            f"this test opened a Neo4j driver on {uri}. Mark it `@pytest.mark.live` and put "
            "it in tests/live/, or use a fake client — `make check` must not need a database."
        )

    def _refuse(request: Any) -> None:
        raise ForbiddenConnection(
            f"this test opened a real connection to {getattr(request, 'url', '?')}. Mock it "
            "with respx, or mark it `@pytest.mark.live` / `@pytest.mark.network`."
        )

    # The names and signatures matter: respx refuses to wrap a target method whose
    # `__name__` is not `handle_request`/`handle_async_request` ("prevent mocking mock"),
    # and it reads the argspec to map positional args. Get either wrong and this guard
    # silently shadows respx instead of deferring to it.
    def handle_request(self: Any, request: Any) -> Any:
        _refuse(request)

    async def handle_async_request(self: Any, request: Any) -> Any:
        _refuse(request)

    monkeypatch.setattr(neo4j.GraphDatabase, "driver", staticmethod(_forbidden_driver))
    monkeypatch.setattr(httpcore.ConnectionPool, "handle_request", handle_request)
    monkeypatch.setattr(httpcore.HTTPProxy, "handle_request", handle_request)
    monkeypatch.setattr(httpcore.AsyncConnectionPool, "handle_async_request", handle_async_request)
    yield


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()
