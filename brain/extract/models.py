"""The batch envelope, both directions.

The input model is what `brain extract build` writes and the agent reads; the output model
is the pydantic half of the contract `brain/extract/schema.json` states in JSON Schema.
The schema catches shape, pydantic catches what a schema cannot say cheaply (a
stripped-empty name, a duplicate chunk in one batch).

`BatchOutput` is the *whole-file* view an agent validates against before writing. Merge
does not use it: it validates the envelope and then each record on its own, so one bad
entity costs that entity rather than the batch (`brain/extract/validate.py`). Two rules
therefore live only in the screening code and not here — a relation from a thing to itself,
and a chunk id that is not in the batch — because both are record-level rejections with
reasons of their own.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Closed sets, spec 2.4. `schema.json` states the same lists and a test asserts they agree
#: — a disagreement between the two would let a batch pass one gate and fail the other.
KINDS: tuple[str, ...] = ("Feature", "Decision", "Problem", "Alternative", "Risk", "Technology")
RELATION_TYPES: tuple[str, ...] = (
    "DECIDES",
    "MOTIVATED_BY",
    "REJECTS",
    "IMPLEMENTS",
    "DEPENDS_ON",
    "INTRODUCES_RISK",
)
#: Minted by merge from `entities[].quote`, never stated by an agent: a MENTIONS edge needs
#: a Chunk on its source side and the output format has no field for one.
DERIVED_RELATION_TYPE = "MENTIONS"

QUOTE_MAX = 300
NAME_MAX = 120
DESCRIPTION_MAX = 300


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


# ------------------------------------------------------------------------------- input


class ChunkContext(Base):
    """One chunk as the agent sees it. Exactly the fields of brief 07 decision 3."""

    chunk_id: str
    parent_key: str
    parent_kind: str
    parent_title: str
    position: int
    kip_keys_referenced: list[str] = Field(default_factory=list)
    text: str


class BatchInput(Base):
    batch_id: str
    shard: str
    index: int
    task: str = "extract"
    phase: str = "A"
    generated_at: str
    schema_path: str
    schema_sha256: str
    chunk_count: int
    chunks: list[ChunkContext]

    @model_validator(mode="after")
    def _count_matches(self) -> BatchInput:
        if self.chunk_count != len(self.chunks):
            raise ValueError(f"chunk_count {self.chunk_count} != {len(self.chunks)} chunks")
        ids = [c.chunk_id for c in self.chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("the same chunk appears twice in one batch")
        return self


# ------------------------------------------------------------------------------ output


class Entity(Base):
    kind: str
    name: str
    description: str
    quote: str
    chunk_id: str

    @field_validator("name", "description", "quote")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in KINDS:
            raise ValueError(f"{v!r} is not one of {list(KINDS)}")
        return v


class Relation(Base):
    type: str
    source: str
    target: str
    evidence_chunk_id: str
    note: str | None = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v == DERIVED_RELATION_TYPE:
            raise ValueError(
                "MENTIONS is minted by merge from entities[].quote, never stated by an agent"
            )
        if v not in RELATION_TYPES:
            raise ValueError(f"{v!r} is not one of {list(RELATION_TYPES)}")
        return v

    @field_validator("source", "target")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v


class BatchOutput(Base):
    batch_id: str
    entities: list[Entity] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ShardStatus(Base):
    """`data/batches/extract/<shard>/status.json` — the agent's own progress file."""

    shard: str
    done: list[str] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)
