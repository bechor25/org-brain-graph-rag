"""Canonical, source-agnostic model (spec §2.2).

Every connector maps into exactly these five record types. A new org system means a
new mapper into this model — never a new graph schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["jira", "ado", "xray", "confluence", "git", "github"]
RefKind = Literal["issue", "kip", "pr", "url", "user"]


class Ref(BaseModel):
    kind: RefKind
    key: str


class Comment(BaseModel):
    author: str | None = None
    at: datetime | None = None
    body: str


class ChangelogEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    field: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    at: datetime
    by: str | None = None


class Link(BaseModel):
    type: str  # e.g. "blocks", "relates", "duplicates", "tests", "parent", "clones"
    target: str  # key of the other item
    direction: Literal["out", "in"] = "out"


class WorkItem(BaseModel):
    id: str  # "<source>:<key>"
    key: str  # KAFKA-15123 / ADO-77 / XT-12
    source: Source
    type: str  # Bug / Improvement / Story / Epic / Task / Test / TestExecution / TestPlan ...
    title: str
    description: str = ""
    status: str
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


class Document(BaseModel):
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
    labels: list[str] = []
    refs: list[Ref] = []
    synthetic: bool = False
    raw_url: str | None = None


class Identity(BaseModel):
    source: Source
    key: str  # jira username / confluence userKey / git email / ado descriptor
    display: str | None = None
    email: str | None = None


class Person(BaseModel):
    id: str  # pre-resolution: "<source>:<key>"; post-resolution: canonical id
    identities: list[Identity] = Field(min_length=1)
    synthetic: bool = False


class Change(BaseModel):
    id: str  # commit sha or "pr:<number>"
    kind: Literal["commit", "pr"]
    message: str
    author_name: str | None = None
    author_email: str | None = None
    at: datetime
    files: list[str] = []
    refs: list[Ref] = []
    synthetic: bool = False
    raw_url: str | None = None


class Container(BaseModel):
    id: str  # "<source>:<kind>:<name>"
    source: Source
    kind: Literal["component", "version", "sprint", "area", "space", "testplan", "testset"]
    name: str
    parent: str | None = None
    synthetic: bool = False
