"""The batch envelope, both directions.

`BatchInput` is what `brain communities batches` writes and the `community-summarizer`
agent reads; `BatchOutput` is the pydantic half of the contract `brain/community/schema.json`
states in JSON Schema. The schema catches shape; pydantic catches what a schema cannot say
cheaply — a blank title after stripping, the same community answered twice in one file.

Merge does not validate the *whole file* against `BatchOutput` and stop there: it screens
report by report, so one bad report costs that report and the rest of the batch still
lands. The two rules that need the input to check them — a `community_id` this batch did
not ask about, an `evidence_chunk_id` offered to a different community — live in
`brain/community/merge.py` for exactly that reason.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TASK = "communities"
#: `L<level>-<leiden id>`. The one shape rule pydantic repeats after the schema, because a
#: malformed id is an identity error — it matches no node — rather than a style problem.
COMMUNITY_ID = re.compile(r"^L[0-9]+-[0-9]+\Z")
TITLE_MAX = 120
SUMMARY_MAX = 3000
STATEMENT_MAX = 600
RANK_MIN, RANK_MAX = 0.0, 10.0


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


# ------------------------------------------------------------------------------- input


class MemberContext(Base):
    """One member as the agent sees it. `degree` is degree in the projected graph."""

    key: str
    label: str
    kind: str | None = None
    name: str
    description: str = ""
    degree: float = 0.0


class EvidenceContext(Base):
    """One chunk offered as evidence, already truncated by the builder."""

    chunk_id: str
    parent_key: str | None = None
    text: str
    members_mentioned: int = 0


class CommunityContext(Base):
    community_id: str
    level: int
    level_name: str
    size: int
    member_hash: str
    members_shown: int
    members: list[MemberContext]
    evidence: list[EvidenceContext] = Field(default_factory=list)

    @model_validator(mode="after")
    def _counts_match(self) -> CommunityContext:
        if self.members_shown != len(self.members):
            raise ValueError(f"members_shown {self.members_shown} != {len(self.members)} members")
        ids = [e.chunk_id for e in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("the same chunk is offered twice as evidence")
        return self


class BatchInput(Base):
    batch_id: str
    shard: str
    index: int
    task: str = TASK
    generated_at: str
    schema_path: str
    schema_sha256: str
    community_count: int
    communities: list[CommunityContext]

    @model_validator(mode="after")
    def _count_matches(self) -> BatchInput:
        if self.community_count != len(self.communities):
            raise ValueError(
                f"community_count {self.community_count} != {len(self.communities)} communities"
            )
        ids = [c.community_id for c in self.communities]
        if len(set(ids)) != len(ids):
            raise ValueError("the same community appears twice in one batch")
        return self

    def evidence_ids(self, community_id: str) -> set[str]:
        for c in self.communities:
            if c.community_id == community_id:
                return {e.chunk_id for e in c.evidence}
        return set()


class ShardStatus(Base):
    """`data/batches/communities/<shard>/status.json` — the agent's own progress file."""

    shard: str
    done: list[str] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)


# ------------------------------------------------------------------------------ output


class Finding(Base):
    statement: str
    evidence_chunk_ids: list[str]

    @field_validator("statement")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("evidence_chunk_ids")
    @classmethod
    def _has_evidence(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("a finding must cite at least one evidence chunk")
        return v


class Report(Base):
    community_id: str
    title: str
    summary: str
    findings: list[Finding]
    rank: float
    rank_reason: str

    @field_validator("community_id")
    @classmethod
    def _well_formed_id(cls, v: str) -> str:
        if not COMMUNITY_ID.match(v):
            raise ValueError(f"{v!r} is not a community id of the form L<level>-<id>")
        return v

    @field_validator("title", "summary", "rank_reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("rank")
    @classmethod
    def _in_range(cls, v: float) -> float:
        if not RANK_MIN <= v <= RANK_MAX:
            raise ValueError(f"rank must be between {RANK_MIN} and {RANK_MAX}, got {v}")
        return v

    @field_validator("findings")
    @classmethod
    def _at_least_one(cls, v: list[Finding]) -> list[Finding]:
        if not v:
            raise ValueError("a report must carry at least one finding")
        return v

    @property
    def evidence_chunk_ids(self) -> list[str]:
        return sorted({c for f in self.findings for c in f.evidence_chunk_ids})


class BatchOutput(Base):
    batch_id: str
    reports: list[Report] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
