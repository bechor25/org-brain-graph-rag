import pytest

from brain.config import Settings
from brain.embed.client import OllamaEmbedder

pytestmark = pytest.mark.live


def test_hebrew_and_english_embed_to_expected_dim():
    s = Settings()
    emb = OllamaEmbedder(s.ollama_url, s.embed_model, s.embed_dim)
    assert emb.has_model()
    vecs = emb.embed(["consumer rebalance protocol", "פרוטוקול איזון מחדש של הצרכן"])
    assert all(len(v) == s.embed_dim for v in vecs)
