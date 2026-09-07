"""The one envelope every strategy returns (spec §4.4, plan decision 2).

Six strategies, fifteen MCP tools and an evaluation harness that compares them head to
head only work if they all speak the same shape. `Item` is that shape: a `kind` the
packer can protect, a `key` a citation can name, a `snippet` an agent can quote and a
`provenance` list that says which chunk the claim came from. Anything a single kind needs
and the others do not goes in `props`, deliberately small — the 4k token budget in
`pack.py` is spent on evidence, not on echoing the whole node back.

`Result.cypher_used` is not decoration. It is what makes a retrieval reproducible: paste
it into the browser and you get the same rows, which is the difference between a demo and
a measurement.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

#: The closed set of `Item.kind` values. `Row` is the escape hatch for a derived tuple
#: that is not a node (a status at a date, a version diff line) — it keeps the packer's
#: "one item per kind" rule meaningful instead of letting every tool invent a label.
ITEM_KINDS: tuple[str, ...] = (
    "Chunk",
    "WorkItem",
    "Document",
    "Person",
    "Change",
    "Container",
    "Entity",
    "Community",
    "Row",
)

#: Strategy ids used by `route()`, `brain ask --strategy` and the log.
STRATEGIES: tuple[str, ...] = ("s1", "s2", "s3", "s4", "s5", "s6", "lookup")


class Provenance(BaseModel):
    """Where a claim came from. `chunk_id` is the one field a citation check can verify."""

    chunk_id: str | None = None
    quote: str | None = None
    batch_id: str | None = None
    model: str | None = None
    source: str | None = None


class Item(BaseModel):
    kind: str
    key: str
    title: str = ""
    snippet: str = ""
    score: float = 0.0
    props: dict[str, Any] = Field(default_factory=dict)
    provenance: list[Provenance] = Field(default_factory=list)


class Result(BaseModel):
    strategy: str
    items: list[Item] = Field(default_factory=list)
    cypher_used: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    truncated: bool = False
    #: What `route()` suggested and what actually ran — `None` when the caller named the
    #: strategy itself. Advisory, never binding (spec §4.2).
    route: dict[str, Any] | None = None


class RetrieveError(RuntimeError):
    """A retrieval could not run: a missing index, a model mismatch, an unknown key."""


class EmbedModelMismatch(RetrieveError):
    """The query embedder is not the model that filled the index it is about to search."""
