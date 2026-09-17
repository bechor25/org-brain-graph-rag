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

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

#: The closed set of `Item.kind` values, enforced by pydantic rather than by convention.
#: `Row` is the escape hatch for a derived tuple that is not a node (a status at a date, a
#: version diff line, a `run_cypher` row) — it keeps the packer's "one item per kind" rule
#: meaningful instead of letting every tool invent a label. A `Literal` here is what stops
#: a tool from quietly shipping `kind="Test"`: the packer would then protect a kind nothing
#: else knows about, and a report grouped by kind would grow a column nobody declared.
ItemKind = Literal[
    "Chunk",
    "WorkItem",
    "Document",
    "Person",
    "Change",
    "Container",
    "Entity",
    "Community",
    "Row",
]
ITEM_KINDS: tuple[str, ...] = get_args(ItemKind)

#: How a `Provenance` entry backs its claim, because the three are not equal evidence:
#:
#: * `quote` — words the corpus really wrote about this node (a `MENTIONS.quote`, a chunk).
#: * `node-text` — the node's *own* description, handed back when the answer was derived by
#:   traversal or aggregation and nothing quotes it. Checkable, but it is the node talking
#:   about itself.
#: * `row` — a tuple a query produced (`run_cypher`, a test execution). Its audit trail is
#:   the Cypher in `Result.cypher_used`, not a chunk id.
#:
#: Plan 2 Task 4's `cite-check` counts them separately: a citation backed by a quote and a
#: citation backed by the node's own text are both traceable, and only one of them is
#: independent evidence.
SourceKind = Literal["quote", "node-text", "row"]
SOURCE_KINDS: tuple[str, ...] = get_args(SourceKind)

#: Strategy ids used by `route()`, `brain ask --strategy` and the log.
STRATEGIES: tuple[str, ...] = ("s1", "s2", "s3", "s4", "s5", "s6", "lookup")


class Provenance(BaseModel):
    """Where a claim came from. `chunk_id` is the one field a citation check can verify."""

    chunk_id: str | None = None
    quote: str | None = None
    source_kind: SourceKind = "quote"
    batch_id: str | None = None
    model: str | None = None
    source: str | None = None


class Item(BaseModel):
    kind: ItemKind
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
