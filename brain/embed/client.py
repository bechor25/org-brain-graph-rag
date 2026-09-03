"""Local embeddings via Ollama (/api/embed). Model + dim are pinned by settings;
a dimension mismatch is a hard error (index/query vectors must come from the same model).
"""

from __future__ import annotations

import httpx


class EmbedDimMismatch(RuntimeError):
    pass


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, dim: int, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self._client = httpx.Client(timeout=timeout)

    def has_model(self) -> bool:
        r = self._client.get(f"{self.base_url}/api/tags")
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
        return any(n == self.model or n.startswith(self.model + ":") for n in names)

    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            r = self._client.post(
                f"{self.base_url}/api/embed", json={"model": self.model, "input": chunk}
            )
            r.raise_for_status()
            vectors = r.json()["embeddings"]
            for v in vectors:
                if len(v) != self.dim:
                    raise EmbedDimMismatch(
                        f"model {self.model} returned dim {len(v)}, expected {self.dim}"
                    )
            out.extend(vectors)
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
