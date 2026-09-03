import json

import httpx
import pytest
import respx

from brain.embed.client import EmbedCountMismatch, EmbedDimMismatch, OllamaEmbedder

BASE = "http://ollama.test"


@respx.mock
def test_embed_batches_and_returns_vectors():
    calls = []

    def handler(request: httpx.Request):
        payload = json.loads(request.read())
        calls.append(payload)
        assert payload["model"] == "bge-m3"
        return httpx.Response(200, json={"embeddings": [[0.1] * 4 for _ in payload["input"]]})

    respx.post(f"{BASE}/api/embed").mock(side_effect=handler)
    emb = OllamaEmbedder(BASE, model="bge-m3", dim=4)
    vecs = emb.embed(["a", "b", "c"], batch_size=2)
    assert len(vecs) == 3 and len(vecs[0]) == 4
    assert len(calls) == 2  # 2 + 1
    assert [c["input"] for c in calls] == [["a", "b"], ["c"]]


@respx.mock
def test_dim_mismatch_is_hard_error():
    respx.post(f"{BASE}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1] * 3]})
    )
    emb = OllamaEmbedder(BASE, model="bge-m3", dim=4)
    with pytest.raises(EmbedDimMismatch):
        emb.embed(["a"])


@respx.mock
def test_count_mismatch_is_hard_error():
    respx.post(f"{BASE}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1] * 4]})
    )
    emb = OllamaEmbedder(BASE, model="bge-m3", dim=4)
    with pytest.raises(EmbedCountMismatch):
        emb.embed(["a", "b"])


@respx.mock
def test_has_model():
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}]})
    )
    emb = OllamaEmbedder(BASE, model="bge-m3", dim=4)
    assert emb.has_model() is True


@respx.mock
def test_has_model_rejects_prefix_lookalike():
    respx.get(f"{BASE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "bge-m3-large:latest"}]})
    )
    emb = OllamaEmbedder(BASE, model="bge-m3", dim=4)
    assert emb.has_model() is False
