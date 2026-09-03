from brain.config import Settings


def test_defaults_are_local_dev_values(monkeypatch):
    for k in ["NEO4J_URI", "NEO4J_PASSWORD", "OLLAMA_URL", "EMBED_MODEL", "EMBED_DIM"]:
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.neo4j_uri == "bolt://localhost:7687"
    assert s.embed_model == "bge-m3"
    assert s.embed_dim == 1024
    assert s.ollama_url == "http://localhost:11434"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("EMBED_DIM", "768")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    s = Settings(_env_file=None)
    assert s.embed_dim == 768
    assert s.neo4j_password == "secret"
