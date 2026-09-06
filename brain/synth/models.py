"""The two shapes that cross the agent boundary: what a batch asks for, what it answers.

`BatchInput` is written by `brain synth build` and read by `synthetic-org-generator`.
`BatchOutput` is the reverse. Between them sits `brain/synth/schema.json`, which is the
same contract expressed for an agent that cannot import Python — keep the two in step;
`tests/test_synth_schema.py` checks that they agree on the required fields.

Everything here is deliberately small. The batch input is read by an LLM one file at a
time, so every field has to earn its bytes: the caps in :mod:`brain.synth.build` exist
because a 480 KB batch is a batch that gets skimmed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from brain.canon.models import Container, Link, Person, WorkItem

#: The key prefixes this layer may mint, and the source each belongs to.
PREFIX_SOURCE: dict[str, str] = {
    "XT": "xray",
    "XE": "xray",
    "XP": "xray",
    "XS": "xray",
    "ADO": "ado",
}
PREFIXES: tuple[str, ...] = tuple(PREFIX_SOURCE)


# ------------------------------------------------------------------ batch input (build)


class BatchComment(BaseModel):
    """A trimmed real comment. Present for grounding, not for reproduction."""

    author: str | None = None
    at: str | None = None
    body: str


class BatchItem(BaseModel):
    """A real Jira work item, trimmed to what a test/story writer needs."""

    key: str
    type: str
    status: str
    resolution: str | None = None
    priority: str | None = None
    title: str
    description: str
    description_chars: int = 0  #: full length before truncation, so "…" is not a mystery
    created: str
    updated: str | None = None
    resolved_at: str | None = None
    reporter: str | None = None  #: real Jira identity key — resolve via `persons[]`
    assignee: str | None = None
    components: list[str] = []
    labels: list[str] = []
    fix_versions: list[str] = []
    affects_versions: list[str] = []
    parent: str | None = None
    links: list[Link] = []
    kip_refs: list[str] = []
    issue_refs: list[str] = []
    comments: list[BatchComment] = []


class BatchDocument(BaseModel):
    """A referenced KIP. `excerpt` is the head of `body_md` — enough to paraphrase."""

    key: str
    title: str
    excerpt: str
    body_chars: int = 0


class BatchIdentity(BaseModel):
    """A real person as this batch knows them. The agent invents a *variant* of this."""

    person_id: str  #: canonical Person id, e.g. "jira:jrao" — the truth map's value
    source: str
    key: str
    display: str | None = None
    used_as: list[str] = []  #: reporter | assignee | commenter


class BatchContainer(BaseModel):
    id: str
    kind: str
    name: str


class Numbering(BaseModel):
    """The key range this shard owns for one prefix.

    `next` is a *seed*, present on a shard's first batch only. Every later batch reads the
    running value from `status.json`: three agents write in parallel, so the only thing
    that can be globally true is the range, not a counter build could have guessed.
    """

    range_start: int
    range_end: int
    next: int | None = None


class PreassignedEpic(BaseModel):
    """An ADO Epic whose key build minted, because a KIP crosses shard boundaries.

    `owned` says whether *this* batch writes the record. Every other batch that cites the
    same KIP gets the same key with `owned: false` and links to it without emitting it —
    which is the only way "one Epic per referenced KIP" survives three parallel writers.
    """

    key: str
    kip: str
    title: str
    owned: bool


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    shard: str
    index: int
    generated_at: str
    spec: str
    spec_sha256: str
    schema_ref: str
    output: str
    instructions: list[str]
    numbering: dict[str, Numbering]
    epics: list[PreassignedEpic] = []
    workitems: list[BatchItem]
    documents: list[BatchDocument] = []
    persons: list[BatchIdentity] = []
    containers: list[BatchContainer] = []


# ----------------------------------------------------------------- batch output (agent)


class TextOnlyLink(BaseModel):
    """A reference that lives only in prose. One shape for both directions.

    `synthetic_spec.md` names the fields per case (`ado_key`/`jira_key` for a Story,
    `test_key`/`jira_key` for a Test). They are the same fact with two names, and the
    merge normalises the spec's spellings into this one — see `merge._normalise_truth`.
    """

    model_config = ConfigDict(extra="forbid")
    from_key: str
    to_key: str


class StaleState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ado_key: str
    jira_key: str
    ado_status: str
    jira_status: str


class Rename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    test_key: str
    jira_key: str
    test_phrase: str
    jira_phrase: str


class DuplicateTests(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str
    b: str


class Truth(BaseModel):
    """Ground truth by construction. Evaluation only — never an input to the pipeline."""

    model_config = ConfigDict(extra="forbid")
    identity_map: dict[str, str] = Field(default_factory=dict)
    text_only_links: list[TextOnlyLink] = []
    stale_states: list[StaleState] = []
    renames: list[Rename] = []
    duplicate_tests: list[DuplicateTests] = []


class BatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    workitems: list[WorkItem] = []
    containers: list[Container] = []
    persons: list[Person] = []
    truth: Truth = Field(default_factory=Truth)
    next_ids: dict[str, int] | None = None
    notes: list[str] = []


# --------------------------------------------------------------------------- status.json


class ShardStatus(BaseModel):
    """`status.json` — agent-owned. build seeds it empty and never rewrites a non-empty one.

    `next_ids` extends the conventions' `{done, failed}` shape: with disjoint per-shard key
    ranges there is no global counter to read, so the running value has to live where the
    agent resumes from.
    """

    model_config = ConfigDict(extra="allow")
    shard: str
    done: list[str] = []
    failed: list[dict[str, Any]] = []
    next_ids: dict[str, int] = Field(default_factory=dict)


Direction = Literal["out", "in"]
