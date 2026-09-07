"""Small builders for the community tests, and the fake graph the unit tests read from.

Nothing here talks to Neo4j. `FakeContext` answers the handful of queries the batch
builder and the merge screening make, which is what lets "a report citing another
community's chunk is rejected" be a test that runs in `make check`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from brain.community.partition import Member, NodeAssignment

SCHEMA = json.loads(Path("brain/community/schema.json").read_text(encoding="utf-8"))
A, B, C = "a" * 40, "b" * 40, "c" * 40


def member(label: str = "Entity", key: str = "Decision|x") -> Member:
    return Member(label=label, key=key)


def rows(spec: dict[str, list[int]]) -> list[NodeAssignment]:
    """`{"Entity:Decision|x": [0, 3]}` -> assignments. One entry per Leiden level."""
    out: list[NodeAssignment] = []
    for member_key, community_ids in spec.items():
        label, key = member_key.split(":", 1)
        out.append(
            NodeAssignment(member=Member(label=label, key=key), community_ids=tuple(community_ids))
        )
    return out


def line(n: int, *, levels: tuple[int, ...], label: str = "Entity") -> dict[str, list[int]]:
    """`n` members, all in the same community at every level."""
    return {f"{label}:k{i}": list(levels) for i in range(n)}


# ------------------------------------------------------------------------ batch payloads


def evidence(chunk_id: str = A, text: str = "the classic protocol makes clients assign") -> dict:
    return {"chunk_id": chunk_id, "parent_key": "KIP-848", "text": text, "members_mentioned": 2}


def community_context(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "community_id": "L0-1",
        "level": 0,
        "level_name": "fine",
        "size": 6,
        "member_hash": "0" * 40,
        "members_shown": 1,
        "members": [
            {
                "key": "Decision|move assignment to the coordinator",
                "label": "Entity",
                "kind": "Decision",
                "name": "Move assignment to the coordinator",
                "description": "The coordinator computes the assignment.",
                "degree": 7.0,
            }
        ],
        "evidence": [evidence()],
    }
    merged = {**base, **kw}
    merged["members_shown"] = len(merged["members"])
    return merged


def batch_input(communities: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    items = communities if communities is not None else [community_context()]
    base: dict[str, Any] = {
        "batch_id": "shard-01/001",
        "shard": "shard-01",
        "index": 1,
        "task": "communities",
        "generated_at": "2026-09-07T00:00:00+00:00",
        "schema_path": "brain/community/schema.json",
        "schema_sha256": "0" * 64,
        "community_count": len(items),
        "communities": items,
    }
    return {**base, **kw}


SUMMARY = (
    "This community is about moving partition assignment from the consumer clients to the "
    "group coordinator. The classic rebalance protocol asks every member to compute the "
    "assignment itself, which forces a stop-the-world barrier on every membership change "
    "and makes rebalances long enough to look like an outage. The decision recorded here "
    "moves that computation to the broker-side coordinator and replaces the barrier with "
    "incremental reconciliation, so a member joining or leaving no longer revokes the "
    "partitions of every other member. The open questions in this cluster are the upgrade "
    "path from the classic protocol and how the two protocols coexist inside one group "
    "while a cluster is being migrated."
)


def report(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "community_id": "L0-1",
        "title": "Consumer rebalance moves to the coordinator",
        "summary": SUMMARY,
        "findings": [
            {
                "statement": "Assignment computed by clients is what makes rebalances long.",
                "evidence_chunk_ids": [A],
            }
        ],
        "rank": 8.5,
        "rank_reason": "It touches every consumer and the migration is still open.",
    }
    return {**base, **kw}


def batch_output(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"batch_id": "shard-01/001", "reports": [report()]}
    return {**base, **kw}


def written_batch(tmp_path: Path, *, inp: dict[str, Any], out: dict[str, Any] | None) -> Path:
    """Write an `.in.json`/`.out.json` pair where `discover` will find them."""
    shard, index = inp["batch_id"].split("/")
    shard_dir = tmp_path / "communities" / shard
    shard_dir.mkdir(parents=True, exist_ok=True)
    (shard_dir / f"{index}.in.json").write_text(
        json.dumps(inp, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    path = shard_dir / f"{index}.out.json"
    if out is not None:
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def screened(path: Path, known_chunks: set[str] | None = None):
    """Parse + screen one written batch, the way `run_merge` does."""
    from brain.community import merge as merge_mod

    shard = path.parent.name
    index = int(path.name.split(".", 1)[0])
    batch = merge_mod.Batch(
        batch_id=f"{shard}/{index:03d}",
        shard=shard,
        index=index,
        path=path,
        sha256="0" * 64,
        extracted_at="2026-09-07T01:00:00+00:00",
    )
    merge_mod.parse_batch(batch, SCHEMA)
    merge_mod.screen(batch, known_chunks)
    return batch


__all__ = [
    "A",
    "B",
    "C",
    "SCHEMA",
    "SUMMARY",
    "batch_input",
    "batch_output",
    "community_context",
    "evidence",
    "line",
    "member",
    "report",
    "rows",
    "screened",
    "written_batch",
]
