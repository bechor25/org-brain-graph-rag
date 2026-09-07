"""Leiden's output, turned into the communities the rest of the step writes and summarises.

Nothing here talks to Neo4j or to GDS. That is deliberate: the two facts this step has to
be able to prove — *the same graph gives the same communities* and *a community that did
not change is not re-summarised* — are both facts about this file, and a test that needs a
database to state them is a test nobody runs.

Three decisions live here.

**Which two levels.** `gds.leiden.stream` with `includeIntermediateCommunities` returns one
list per node, one entry per level it ran. Level ordering is a property of the algorithm,
not of the brief, so the levels are picked by what they *are* rather than by their index:
of the levels kept, the one with more communities is `level 0` (fine) and the one with
fewer is `level 1` (coarse). `gds_levels` in the report says which indices those were.

**`member_hash`.** sha1 over the sorted `Label:key` of every member. It is the identity of
a community *as a set of things*, which is the identity that matters for re-summarisation:
Leiden's own community id is derived from internal node ids and moves when the projection
changes, and a report thrown away because a node id moved is a report paid for twice.

**`misc`.** A community under `min_size` gets its node and its `IN_COMMUNITY` edges — every
member is placed at both levels, always — but is marked `misc` and never summarised. Nine
hundred two-node communities would eat the batch budget and say nothing; their content
reaches global search through the coarse community that contains them.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: `level 0` is fine, `level 1` is coarse. Two levels, brief 09 decision 2.
FINE, COARSE = 0, 1
LEVEL_NAMES: dict[int, str] = {FINE: "fine", COARSE: "coarse"}
DEFAULT_LEVELS = 2
DEFAULT_MIN_SIZE = 5
#: Buckets the size distribution is reported in. Open-ended at the top.
SIZE_BUCKETS: tuple[int, ...] = (1, 2, 5, 10, 25, 50, 100, 250)


class PartitionError(RuntimeError):
    """Leiden's output cannot be read as a hierarchy of communities."""


@dataclass(frozen=True, order=True)
class Member:
    """One projected node: the label it was projected under and its key in that label."""

    label: str
    key: str

    @property
    def member_key(self) -> str:
        """`Entity:Decision|x`, `WorkItem:KAFKA-1` — unique across labels, unlike `key`."""
        return f"{self.label}:{self.key}"


@dataclass
class Community:
    """One community at one level, and the members Leiden put in it."""

    level: int
    community_id: int
    members: list[Member]
    #: Fine communities this coarse one contains — `[]` at the fine level.
    children: list[int] = field(default_factory=list)
    min_size: int = DEFAULT_MIN_SIZE

    @property
    def id(self) -> str:
        return f"L{self.level}-{self.community_id}"

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def misc(self) -> bool:
        """Too small to be worth a report of its own (brief 09 decision 2)."""
        return self.size < self.min_size

    @property
    def summarized(self) -> bool:
        return not self.misc

    @property
    def member_keys(self) -> list[str]:
        return sorted(m.member_key for m in self.members)

    @property
    def member_hash(self) -> str:
        return member_hash(self.members)

    def counts_by_label(self) -> dict[str, int]:
        return dict(sorted(Counter(m.label for m in self.members).items()))

    def row(self) -> dict[str, Any]:
        """The node properties `brain communities build` writes. No report fields."""
        return {
            "id": self.id,
            "level": self.level,
            "level_name": LEVEL_NAMES.get(self.level, str(self.level)),
            "community_id": self.community_id,
            "size": self.size,
            "member_hash": self.member_hash,
            "misc": self.misc,
            "summarized": False,
            "members_by_label": [f"{k}={v}" for k, v in self.counts_by_label().items()],
            "children": sorted(self.children),
        }


def member_hash(members: Iterable[Member]) -> str:
    """sha1 of the sorted `Label:key` list. Order of arrival never reaches it."""
    joined = "\n".join(sorted({m.member_key for m in members}))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------- levels


@dataclass(frozen=True)
class NodeAssignment:
    """One row of `gds.leiden.stream`: a node and its community at every level."""

    member: Member
    community_ids: tuple[int, ...]


def pick_levels(rows: Sequence[NodeAssignment], levels: int = DEFAULT_LEVELS) -> list[int]:
    """The GDS level indices to keep, ordered fine (most communities) first.

    The coarsest `levels` indices are the candidates — the finest Leiden level on this
    corpus is thousands of two-node groups, which is not a level anybody summarises — and
    they are then *ordered by what they contain*, so a change in how GDS numbers its
    levels cannot silently swap fine and coarse.
    """
    if not rows:
        raise PartitionError("Leiden returned no nodes; there is nothing to partition")
    depths = {len(r.community_ids) for r in rows}
    if len(depths) != 1:
        raise PartitionError(
            f"Leiden returned {sorted(depths)} intermediate levels for different nodes; "
            "every node must be assigned at every level"
        )
    depth = depths.pop()
    if depth < 1:
        raise PartitionError("Leiden returned no intermediate community ids")
    if levels < 1:
        raise PartitionError(f"levels must be at least 1, got {levels}")
    chosen = list(range(max(0, depth - levels), depth))
    counted = [(len({r.community_ids[i] for r in rows}), i) for i in chosen]
    # More communities = finer. Ties keep the GDS order, which keeps a one-level run sane.
    return [i for _, i in sorted(counted, key=lambda ci: (-ci[0], ci[1]))]


def partition(
    rows: Sequence[NodeAssignment],
    *,
    levels: int = DEFAULT_LEVELS,
    min_size: int = DEFAULT_MIN_SIZE,
    indices: Sequence[int] | None = None,
) -> tuple[list[Community], list[int]]:
    """Leiden rows -> the communities of every kept level, plus the GDS indices used.

    `indices` is the planner's override (`--level-indices 1,4`), given fine-to-coarse and
    taken as stated; without it the levels are chosen by `pick_levels`.

    Communities come back ordered by `(level, community_id)`, and a coarse community
    carries the fine community ids it contains, so the report can say which fine
    communities a coarse one swallowed without a second pass over the graph.
    """
    if indices is None:
        gds_levels = pick_levels(rows, levels)
    else:
        depth = len(rows[0].community_ids) if rows else 0
        bad = [i for i in indices if not 0 <= i < depth]
        if bad or not indices:
            raise PartitionError(
                f"level indices {list(indices)} are outside the {depth} levels Leiden ran"
            )
        gds_levels = list(indices)
    out: list[Community] = []
    fine_index = gds_levels[0]
    for level, gds_index in enumerate(gds_levels):
        grouped: dict[int, list[Member]] = {}
        children: dict[int, set[int]] = {}
        for row in rows:
            cid = row.community_ids[gds_index]
            grouped.setdefault(cid, []).append(row.member)
            if level != FINE:
                children.setdefault(cid, set()).add(row.community_ids[fine_index])
        for cid in sorted(grouped):
            out.append(
                Community(
                    level=level,
                    community_id=cid,
                    members=sorted(grouped[cid]),
                    children=sorted(children.get(cid, ())),
                    min_size=min_size,
                )
            )
    return out, gds_levels


# -------------------------------------------------------------------------- distribution


def _bucket_low(label: str) -> int:
    """`"5-9"` -> 5, `"250+"` -> 250 — the bucket's lower bound, for ordering."""
    return int(label.split("-")[0].rstrip("+"))


def size_distribution(sizes: Sequence[int]) -> dict[str, Any]:
    """Count, extremes, percentiles and a bucketed histogram of community sizes."""
    if not sizes:
        return {
            "communities": 0,
            "members": 0,
            "min": 0,
            "p50": 0,
            "p90": 0,
            "max": 0,
            "mean": 0.0,
            "buckets": {},
        }
    ordered = sorted(sizes)

    def pct(q: float) -> int:
        return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]

    buckets: Counter = Counter()
    for size in ordered:
        low = max((b for b in SIZE_BUCKETS if b <= size), default=SIZE_BUCKETS[0])
        higher = [b for b in SIZE_BUCKETS if b > low]
        buckets[f"{low}-{higher[0] - 1}" if higher else f"{low}+"] += 1
    order = {b: i for i, b in enumerate(SIZE_BUCKETS)}
    return {
        "communities": len(ordered),
        "members": sum(ordered),
        "min": ordered[0],
        "p50": pct(0.5),
        "p90": pct(0.9),
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 2),
        "buckets": dict(
            sorted(
                buckets.items(), key=lambda kv: order.get(int(kv[0].split("-")[0].rstrip("+")), 0)
            )
        ),
    }


def level_summary(communities: Sequence[Community]) -> dict[str, Any]:
    """Per level: how many communities, how many are summarised, how big they are."""
    out: dict[str, Any] = {}
    for level in sorted({c.level for c in communities}):
        at = [c for c in communities if c.level == level]
        summarised = [c for c in at if c.summarized]
        by_label: Counter = Counter()
        for c in at:
            by_label.update(c.counts_by_label())
        out[str(level)] = {
            "name": LEVEL_NAMES.get(level, str(level)),
            "communities": len(at),
            "summarized": len(summarised),
            "misc": len(at) - len(summarised),
            "members_by_label": dict(sorted(by_label.items())),
            "sizes": size_distribution([c.size for c in at]),
            "sizes_summarized": size_distribution([c.size for c in summarised]),
        }
    return out


def label_placement(communities: Sequence[Community], label: str) -> dict[str, Any]:
    """Where the nodes of one label landed at the fine level — the Technology question.

    83% of `Technology` entities carry no semantic edge (`data/reports/extract.json`), so
    they reach a community only through the collapsed `MENTIONS` edge to their parent.
    Whether that worked is exactly "how many of them are alone or in a misc community".
    """
    fine = [c for c in communities if c.level == FINE]
    total = singleton = in_misc = 0
    for c in fine:
        here = sum(1 for m in c.members if m.label == label)
        if not here:
            continue
        total += here
        if c.size == 1:
            singleton += here
        if c.misc:
            in_misc += here
    return {
        "label": label,
        "members": total,
        "in_singleton_community": singleton,
        "in_misc_community": in_misc,
        "pct_in_misc": round(100 * in_misc / total, 1) if total else 0.0,
    }


def kind_placement(
    communities: Sequence[Community], kinds: Mapping[str, str], label: str = "Entity"
) -> dict[str, dict[str, Any]]:
    """`label_placement`, split by the `kind` of each member (Decision, Technology, …)."""
    fine = [c for c in communities if c.level == FINE]
    totals: Counter = Counter()
    singles: Counter = Counter()
    miscs: Counter = Counter()
    for c in fine:
        for m in c.members:
            if m.label != label:
                continue
            kind = kinds.get(m.key, "unknown")
            totals[kind] += 1
            if c.size == 1:
                singles[kind] += 1
            if c.misc:
                miscs[kind] += 1
    return {
        kind: {
            "members": totals[kind],
            "in_singleton_community": singles[kind],
            "in_misc_community": miscs[kind],
            "pct_in_misc": round(100 * miscs[kind] / totals[kind], 1) if totals[kind] else 0.0,
        }
        for kind in sorted(totals)
    }
