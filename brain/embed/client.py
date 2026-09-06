"""Local embeddings via Ollama (/api/embed). Model + dim are pinned by settings;
a dimension mismatch is a hard error (index/query vectors must come from the same model).

Every call also tallies what it cost: `prompt_tokens` accumulates the `prompt_eval_count`
Ollama returns, which is the model's *own* tokenizer counting the text it just embedded.
Ollama 0.32.6 answers `/api/tokenize` with 404, so this is the only real token count this
stack can get, and `brain chunk --measure` calibrates its `len(text)/4` estimate against
it. Keeping the tally here rather than in a subclass means one HTTP loop, not two.
"""

from __future__ import annotations

import time

import httpx


class EmbedDimMismatch(RuntimeError):
    pass


class EmbedCountMismatch(RuntimeError):
    pass


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, dim: int, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self.timeout_s = timeout
        self._client = httpx.Client(timeout=timeout)
        #: Real tokens, requests and seconds spent since this embedder was created.
        self.prompt_tokens = 0
        self.requests = 0
        self.request_seconds = 0.0

    def usage(self) -> dict[str, float | int]:
        """What this embedder has cost so far — the numbers the step report quotes."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "requests": self.requests,
            "request_seconds": round(self.request_seconds, 2),
            "timeout_s": self.timeout_s,
        }

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaEmbedder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def has_model(self) -> bool:
        r = self._client.get(f"{self.base_url}/api/tags")
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
        return any(n == self.model or n.startswith(self.model + ":") for n in names)

    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            started = time.perf_counter()
            r = self._client.post(
                f"{self.base_url}/api/embed", json={"model": self.model, "input": chunk}
            )
            r.raise_for_status()
            payload = r.json()
            self.request_seconds += time.perf_counter() - started
            self.requests += 1
            vectors = payload["embeddings"]
            if len(vectors) != len(chunk):
                raise EmbedCountMismatch(
                    f"model {self.model} returned {len(vectors)} vectors for {len(chunk)} inputs"
                )
            for v in vectors:
                if len(v) != self.dim:
                    raise EmbedDimMismatch(
                        f"model {self.model} returned dim {len(v)}, expected {self.dim}"
                    )
            self.prompt_tokens += int(payload.get("prompt_eval_count") or 0)
            out.extend(vectors)
        return out

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
