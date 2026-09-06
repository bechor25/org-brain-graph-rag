"""The guard that keeps `make check` off the network and out of the database.

It exists because a single line in `test_cli.py` once ran the whole pipeline — Neo4j,
Ollama, thousands of embeddings — inside `make check` and nothing said so. These tests
assert the guard fires, and that it still defers to respx so the mocked harvest suite
keeps working.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from brain.embed.client import OllamaEmbedder
from brain.graph.client import GraphClient
from tests.guards import ForbiddenConnection


def test_opening_a_neo4j_driver_is_refused():
    with pytest.raises(ForbiddenConnection, match="Neo4j driver"):
        GraphClient("bolt://localhost:7687", "neo4j", "x")


def test_a_real_http_request_is_refused():
    with pytest.raises(ForbiddenConnection, match="real connection"):
        httpx.Client(timeout=1).get("http://localhost:11434/api/tags")


def test_the_embedder_cannot_reach_a_real_ollama():
    with (
        OllamaEmbedder("http://localhost:11434", "bge-m3", 1024, timeout=1) as e,
        pytest.raises(ForbiddenConnection),
    ):
        e.embed_one("this must never leave the process")


@respx.mock
def test_respx_still_wins_so_the_mocked_suites_keep_working():
    respx.post("http://ollama.test/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.5] * 4], "prompt_eval_count": 9})
    )
    with OllamaEmbedder("http://ollama.test", "bge-m3", 4) as e:
        assert e.embed_one("mocked") == [0.5] * 4
        assert e.prompt_tokens == 9


@respx.mock
def test_a_request_respx_does_not_know_about_still_cannot_reach_the_wire():
    respx.post("http://ollama.test/api/embed").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(Exception) as exc:  # respx refuses first; either way, no socket
        httpx.Client(timeout=1).get("https://issues.apache.org/jira/rest/api/2/search")
    assert "issues.apache.org" in str(exc.value)
