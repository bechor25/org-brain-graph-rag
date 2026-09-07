"""Canonical, source-agnostic model (spec §2.2).

Every connector maps into exactly these five record types. A new org system means a
new mapper into this model — never a new graph schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

Source = Literal["jira", "ado", "xray", "confluence", "git", "github"]
RefKind = Literal["issue", "kip", "pr", "url", "user"]


class Origin(BaseModel):
    """`source_id`: which *registry entry* produced this record.

    `source` is a **type** (`jira`), and two Jira instances in `sources.yaml` produce
    records it cannot tell apart — so a partial `brain canon --source jira-eu` would carry
    over the other instance's work items as "not mine" or delete them as "mine", depending
    on a coin flip. `source_id` is the registry `id`, and it is what `owner_of` asks.

    It is written **only when the id differs from the type**, and dropped from the JSON
    when it is None. Two reasons, and the second is the important one:

    * The single-instance registry — one `jira`, one `confluence`, one `git` — is the whole
      POC and every `id` there already equals its `type`. Recording the same string twice
      on 19,266 records buys nothing.
    * The canonical corpus is compared byte-for-byte against the digests taken before the
      registry refactor (`data/reports/modularity.json`). A field that always serialized
      would change every line of every file and retire that check permanently — the check
      that exists to prove config-driven sources changed nothing.

    The one reader that has to resolve both cases is
    :func:`brain.canon.runner.owner_of`, and it is the only one that can: the fallback for
    an unset `source_id` needs the registry (and, for a `Change`, which has no `source`
    either, a convention). Nothing else branches on the field being absent.
    """

    source_id: str | None = None

    @model_serializer(mode="wrap")
    def _drop_default_source_id(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict) and data.get("source_id") is None:
            data.pop("source_id", None)
        return data


class Ref(BaseModel):
    """A cross-reference found in a record.

    `via` says *how* it was found: `"link"` for a formal link the source itself records
    (a Jira issuelink), `"text"` for one only the regexes saw. `brain load` turns it into
    `REFERENCES{via}` — the number that answers "how much traceability is prose?".
    A text mention that duplicates a formal link counts as `"link"`: the formal link is
    the stronger evidence, and counting it twice would inflate the text share.
    """

    kind: RefKind
    key: str
    via: Literal["text", "link"] = "text"


class Comment(BaseModel):
    author: str | None = None
    at: datetime | None = None
    body: str


class ChangelogEntry(BaseModel):
    """One field change. Display strings *and* the raw ids behind them.

    `from_`/`to` are what Jira shows a human ("Jun Rao"); `from_id`/`to_id` are the
    identity keys it stores ("jrao"). Only the ids can build `ASSIGNED_TO` — 9 display
    names in this corpus are shared by 18 different people.
    """

    model_config = ConfigDict(populate_by_name=True)
    field: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    from_id: str | None = None
    to_id: str | None = None
    at: datetime
    by: str | None = None


class Link(BaseModel):
    type: str  # e.g. "blocks", "relates", "duplicates", "tests", "parent", "clones"
    target: str  # key of the other item
    direction: Literal["out", "in"] = "out"


class WorkItem(Origin):
    id: str  # "<source>:<key>"
    key: str  # KAFKA-15123 / ADO-77 / XT-12
    source: Source
    type: str  # Bug / Improvement / Story / Epic / Task / Test / TestExecution / TestPlan ...
    title: str
    description: str = ""
    status: str
    #: Why the item stopped, and when — `status` alone cannot tell "Resolved/Fixed" from
    #: "Resolved/Won't Fix", and the two mean opposite things to a reader of the graph.
    resolution: str | None = None
    resolved_at: datetime | None = None
    priority: str | None = None
    created: datetime
    updated: datetime | None = None
    reporter: str | None = None  # identity key in the source (e.g. jira username)
    assignee: str | None = None
    components: list[str] = []
    labels: list[str] = []
    fix_versions: list[str] = []
    affects_versions: list[str] = []
    parent: str | None = None
    links: list[Link] = []
    comments: list[Comment] = []
    changelog: list[ChangelogEntry] = []
    refs: list[Ref] = []  # deterministic mentions found in title/description/comments
    synthetic: bool = False
    raw_url: str | None = None


class Document(Origin):
    id: str  # "confluence:<pageId>"
    key: str  # "KIP-848" or page id
    source: Source
    kind: Literal["KIP", "Page", "Readme"]
    space: str | None = None
    title: str
    body_md: str
    version: int = 1
    created: datetime | None = None
    updated: datetime | None = None
    author: str | None = None
    ancestors: list[str] = []
    #: For a page that carries a `KIP-N` another page owns: that page's key. `ancestors`
    #: stays what Confluence means by it — the page tree — and nothing else.
    kip_of: str | None = None
    labels: list[str] = []
    refs: list[Ref] = []
    synthetic: bool = False
    raw_url: str | None = None


class Identity(BaseModel):
    source: Source
    key: str  # jira username / confluence userKey / git email / ado descriptor
    display: str | None = None
    email: str | None = None


class Person(Origin):
    id: str  # pre-resolution: "<source>:<key>"; post-resolution: canonical id
    identities: list[Identity] = Field(min_length=1)
    #: False until `brain resolve` merges identities into one person (spec §3.6).
    #: canon emits one Person per identity, so it is always False here.
    resolved: bool = False
    synthetic: bool = False


class Change(Origin):
    id: str  # commit sha or "pr:<number>"
    kind: Literal["commit", "pr"]
    #: For a commit: the id of the pull request it *is* (`pr:21175`), from the trailing
    #: `(#N)` a squashed merge leaves in the subject. Other `#N` in the text are mentions
    #: and stay in `refs`.
    pr: str | None = None
    message: str
    author_name: str | None = None
    author_email: str | None = None
    at: datetime
    files: list[str] = []
    refs: list[Ref] = []
    synthetic: bool = False
    raw_url: str | None = None


class Container(Origin):
    id: str  # "<source>:<kind>:<name>"
    source: Source
    kind: Literal["component", "version", "sprint", "area", "space", "testplan", "testset"]
    name: str
    parent: str | None = None
    synthetic: bool = False
