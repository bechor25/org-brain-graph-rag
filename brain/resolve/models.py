"""The records this step passes around, and the batch contract it shares with the agent.

`Candidate` is the one shape both halves of resolution see: a `Person` and an `Entity`
differ in where their text comes from, not in what resolution does with them, so every
tier takes `Candidate`s and returns pairs of ids.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: What `--kinds` accepts, and the two candidate families behind it.
KINDS: tuple[str, ...] = ("person", "entity")


class Evidence(BaseModel):
    """One thing a candidate touched: what it is, and how the candidate touched it.

    `synthetic` marks an item the `synthetic-org-generator` agents invented. Real activity
    and invented activity are both evidence to a reader, but only real activity is evidence
    to the deterministic tier: the generator deliberately put two different people called
    "G. Harris" on ADO-20068, so "they touched the same item" is a fact about the noise.
    """

    model_config = ConfigDict(extra="forbid")

    role: str
    key: str
    title: str
    synthetic: bool = False


class Candidate(BaseModel):
    """A node resolution may merge. `block` is the only thing it may merge *within*."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str  # "person" | "entity"
    #: Same-kind-only (brief 08 decision 8): "person" for people, the Entity kind for
    #: entities. Two candidates with different blocks are never compared, at any tier.
    block: str
    name: str
    source: str = ""
    description: str | None = None
    identities: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    #: Ids an *earlier* tier already merged into this node. Carried so tier 3 adds to the
    #: list tier 1 wrote instead of replacing it — the node would otherwise forget which
    #: identities it is made of the moment a second tier touched it.
    merged_from: list[str] = Field(default_factory=list)
    #: The tier that last resolved this node, if any — so a survivor keeps the weakest
    #: evidence in its history rather than the tier that happened to touch it last.
    resolution_tier: int | None = None
    email: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def touched(self) -> frozenset[str]:
        """Every item key this candidate is attached to."""
        return frozenset(e.key for e in self.evidence if e.key)

    @property
    def touched_real(self) -> frozenset[str]:
        """The same, minus the synthetic layer — what tier 1 is allowed to reason from."""
        return frozenset(e.key for e in self.evidence if e.key and not e.synthetic)

    def embed_text(self, max_evidence: int = 5) -> str:
        """What tier 2 embeds. Brief 08 decision 3: the name is never enough on its own.

        For a person: the display name plus the titles of up to five items they touched —
        two people called "S. An" work on different things, and the titles are the only
        thing in the graph that says so. For an entity: `name — description`, spec 3.6.
        """
        head = self.name if not self.description else f"{self.name} — {self.description}"
        lines = [head]
        lines += [f"{e.role.lower()}: {e.title}" for e in self.evidence[:max_evidence] if e.title]
        return "\n".join(lines)


class PairSide(BaseModel):
    """One half of an adjudication pair, as the agent reads it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    source: str = ""
    description: str | None = None
    identities: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class BatchPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    kind: str
    block: str
    similarity: float
    a: PairSide
    b: PairSide


class BatchInput(BaseModel):
    """`data/batches/resolve/<shard>/NNN.in.json`."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    shard: str
    index: int
    task: str = "resolve"
    kind: str
    generated_at: str
    schema_path: str
    schema_sha256: str
    band: list[float]
    pair_count: int
    pairs: list[BatchPair]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pair_id: str
    verdict: str
    reason: str


class BatchOutput(BaseModel):
    """`<NNN>.out.json` — what `entity-adjudicator` writes."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    decisions: list[Decision]
    notes: list[str] = Field(default_factory=list)


class Pair(BaseModel):
    """A merge proposal. `a` and `b` are node ids, ordered so the pair is its own key."""

    model_config = ConfigDict(extra="forbid")

    a: str
    b: str
    kind: str
    block: str
    tier: int
    rule: str
    score: float
    reason: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.a, self.b)


def make_pair(
    a: str, b: str, *, kind: str, block: str, tier: int, rule: str, score: float, reason: str
) -> Pair:
    """Ordered so `(a, b)` and `(b, a)` are the same pair and the same `pair_id`."""
    lo, hi = sorted((a, b))
    return Pair(
        a=lo, b=hi, kind=kind, block=block, tier=tier, rule=rule, score=score, reason=reason
    )
