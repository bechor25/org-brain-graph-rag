"""What every strategy needs to run: a read-only graph, an embedder, and index names.

One object, built once per process (`RetrieveContext.open()`), because the two expensive
things in a retrieval are a Neo4j driver and an HTTP client to Ollama and neither should
be created per question. It also carries the label `prefix`, which is how the mini-corpus
tests get a whole retrieval stack of their own (`_RetrChunk`, `_Retrchunk_embedding`)
without reading or touching the 13,846-chunk real graph.

Two invariants live here rather than in each strategy:

* **Reads only.** Every query goes through `GraphClient.read()`, i.e. `RoutingControl.READ`,
  which the *server* enforces. The strategies never see a write path.
* **One embedding model.** A query vector from a different model than the one that filled
  the index is not a worse search, it is a meaningless one — cosine over two unrelated
  spaces. `check_embedding_model()` compares the live `IndexMeta` with the configured model
  and raises `EmbedModelMismatch`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from brain.config import Settings, get_settings
from brain.embed.client import OllamaEmbedder
from brain.graph.client import GraphClient
from brain.graph.context import GraphContext
from brain.retrieve.types import EmbedModelMismatch, RetrieveError

CHUNK_INDEX = "chunk_embedding"
ENTITY_INDEX = "entity_embedding"
#: Fulltext over `Chunk.text`. `brain index` creates the same name; both use
#: `IF NOT EXISTS`, so whichever runs first wins and the other is a no-op.
CHUNK_FULLTEXT = "chunk_text"


@dataclass
class RetrieveContext:
    """A read-only view of one graph plus the embedder that matches its vector indexes."""

    client: GraphClient
    settings: Settings
    prefix: str = ""
    embedder: OllamaEmbedder | None = None
    #: Plan decision 6: synthetic records are part of the corpus unless a caller opts out.
    include_synthetic: bool = True
    _owns_client: bool = False
    _owns_embedder: bool = False
    #: index name -> its `IndexMeta` row, read once per process.
    _meta: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def open(
        cls,
        settings: Settings | None = None,
        *,
        prefix: str = "",
        include_synthetic: bool = True,
    ) -> RetrieveContext:
        s = settings or get_settings()
        client = GraphClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
        return cls(
            client=client,
            settings=s,
            prefix=prefix,
            include_synthetic=include_synthetic,
            _owns_client=True,
        )

    def close(self) -> None:
        if self._owns_embedder and self.embedder is not None:
            self.embedder.close()
            self.embedder = None
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> RetrieveContext:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ graph access

    @property
    def graph(self) -> GraphContext:
        return GraphContext(self.client, prefix=self.prefix)

    def label(self, name: str) -> str:
        """A backticked, namespaced label — `Chunk` in production, `_RetrChunk` in a test."""
        return f"`{self.prefix}{name}`"

    def index(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Every retrieval query in this library goes through here. READ, server-enforced."""
        return self.client.read(cypher, **params)

    # ------------------------------------------------------------------ embeddings

    def embed_query(self, text: str, index: str = CHUNK_INDEX) -> list[float]:
        """Embed a question with the model that filled `index`, or refuse to guess."""
        self.check_embedding_model(index)
        if self.embedder is None:
            self.embedder = OllamaEmbedder(
                self.settings.ollama_url, self.settings.embed_model, self.settings.embed_dim
            )
            self._owns_embedder = True
        return self.embedder.embed_one(text)

    def index_meta(self, index: str) -> dict[str, Any] | None:
        rows = self.read(
            f"MATCH (m:{self.label('IndexMeta')} {{`name`: $name}}) "
            "RETURN m.name AS name, m.model AS model, m.dim AS dim",
            name=self.index(index),
        )
        return rows[0] if rows else None

    def check_embedding_model(self, index: str = CHUNK_INDEX) -> dict[str, Any]:
        """Hard error on a model or dimension the index was not built with.

        `brain chunk` writes an `IndexMeta` for `chunk_embedding` and `brain resolve` one
        for `entity_embedding`. A missing meta node means the index was built by something
        that did not record what built it — reported by `brain doctor`, not silently
        accepted as permission to search it with a different model.

        Read once per process: the meta node does not change under a running retrieval, and
        one round trip per question to re-learn a constant is a latency budget spent on
        nothing.
        """
        if index in self._meta:
            return self._meta[index]
        meta = self.index_meta(index) or {}
        model, dim = meta.get("model"), meta.get("dim")
        if model and model != self.settings.embed_model:
            raise EmbedModelMismatch(
                f"index {self.index(index)} was built with {model!r} but queries would use "
                f"{self.settings.embed_model!r}; re-embed or change EMBED_MODEL"
            )
        if dim and int(dim) != int(self.settings.embed_dim):
            raise EmbedModelMismatch(
                f"index {self.index(index)} has dim {dim} but EMBED_DIM is "
                f"{self.settings.embed_dim}"
            )
        self._meta[index] = meta
        return meta

    # ------------------------------------------------------------------ synthetic layer

    def synthetic_clause(self, var: str, *, joiner: str = "AND") -> str:
        """`""` when synthetic records are in scope, a filter fragment when they are not.

        `coalesce(…, false)` because the flag arrived after some nodes were written: a null
        reads as "real", which is the safe direction — an excluded real node is a missing
        answer, an included synthetic one is labelled `props.synthetic` and visible.
        """
        if self.include_synthetic:
            return ""
        return f" {joiner} coalesce({var}.synthetic, false) = false"

    # ------------------------------------------------------------------ indexes

    def index_names(self) -> set[str]:
        return {r["name"] for r in self.read("SHOW INDEXES YIELD name RETURN name")}

    def ensure_fulltext_index(self) -> str:
        """`chunk_text` over `Chunk.text`, created on first use if `brain index` has not.

        `IF NOT EXISTS` and a *write* — the only write this library makes, and it goes
        through `GraphClient.write()` deliberately: an index is schema, not data, and
        creating it under `RoutingControl.READ` would (correctly) be refused by the server.
        `brain index` creates the same name concurrently; whichever runs first wins.

        Returns `""` when the index neither exists nor can be created. That is not a
        retrieval failure: S1 falls back to its vector half, which is a worse search and
        still an answer, and `fulltext_available` says which happened.
        """
        name = self.index(CHUNK_FULLTEXT)
        if name in self.index_names():
            return name
        try:
            self.client.write(
                f"CREATE FULLTEXT INDEX `{name}` IF NOT EXISTS "
                f"FOR (c:{self.label('Chunk')}) ON EACH [c.`text`]"
            )
        except Exception:  # noqa: BLE001 - a read-only role is a degradation, not a crash
            return ""
        return name if self.await_index(name) == "ONLINE" else ""

    def await_index(self, name: str, timeout_s: float = 120.0, missing_grace_s: float = 5.0) -> str:
        """Wait for *this* index, by name, rather than for every index in the database.

        `CALL db.awaitIndexes(300)` is the obvious call and the wrong one here: it waits for
        every index the server holds, and with several agents building scratch namespaces in
        one database a single stuck population blocks retrieval that does not depend on it.

        An index that never even appears is given `missing_grace_s` and then given up on —
        waiting two minutes for a row that is not coming is not patience, it is a hang.
        """
        started = time.monotonic()
        deadline = started + timeout_s
        while True:
            rows = self.read(
                "SHOW INDEXES YIELD name, state WHERE name = $name RETURN state", name=name
            )
            state = rows[0]["state"] if rows else "MISSING"
            now = time.monotonic()
            if state == "ONLINE" or now > deadline:
                return state
            if state == "MISSING" and now - started > missing_grace_s:
                return state
            time.sleep(0.25)

    def require_index(self, name: str) -> str:
        full = self.index(name)
        if full not in self.index_names():
            raise RetrieveError(
                f"index {full!r} does not exist — run `brain chunk` / `brain index` first"
            )
        return full
