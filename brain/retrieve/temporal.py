"""S6 — the four temporal tools. No embeddings, no LLM, no ranking. Facts or nothing.

Spec §2.4 stores time in two layers and each answers a different question:

* `StatusChange` event nodes (`field`, `from`, `to`, `at`) — "what was the status on D".
* `ASSIGNED_TO {valid_from, valid_to}` intervals — "who was responsible, and when".

Both are read here with `<=` and `>` and nothing else. A temporal question answered by a
vector search is a guess with a citation attached; these four return the row the changelog
actually holds, or say plainly that there is no row. `status_at` on a date before the item
existed returns "did not exist yet" rather than the first status it ever had, because the
second is a wrong answer that looks like a right one.

The one subtlety is the initial status. A Jira changelog records *transitions*, not the
starting state, so the status before the first transition is that transition's `from` —
not `null`, and not today's status.

Versions sort as tuples, not as strings: `"3.10.0" < "3.9.0"` lexicographically and that is
the wrong window for `changes_between`. A two-part bound (`3.6`) means the whole family, so
`3.6` → `3.7` excludes `3.6.2` and includes `3.7.0`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.retrieve.context import RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.evidence import as_provenance, own_chunks
from brain.retrieve.nodes import to_item
from brain.retrieve.pack import clip
from brain.retrieve.types import Item, Provenance, Result, RetrieveError

#: A version bound with fewer than three parts covers its whole family.
_FAMILY_CEILING = 9999


def _end_of_day(date: str) -> int:
    """`YYYY-MM-DD` -> the last second of that day, UTC, as seconds since the epoch."""
    try:
        day = datetime.fromisoformat(date).date()
    except ValueError as exc:
        raise RetrieveError(f"{date!r} is not a date; expected YYYY-MM-DD") from exc
    return int(datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC).timestamp())


def version_key(name: str) -> tuple[int, ...]:
    parts = [int(p) for p in re.findall(r"\d+", name or "")]
    return tuple(parts) if parts else (0,)


def _bound(name: str, ceiling: bool) -> tuple[int, ...]:
    parts = list(version_key(name))
    while len(parts) < 3:
        parts.append(_FAMILY_CEILING if ceiling else 0)
    return tuple(parts)


def _pad(parts: tuple[int, ...], length: int = 4) -> tuple[int, ...]:
    return tuple(list(parts) + [0] * (length - len(parts)))[:length]


def status_at(
    ctx: RetrieveContext,
    key: str,
    date: str,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """What `key`'s status was on `date` (`YYYY-MM-DD`), from the changelog. Deterministic."""
    timer = Timer()
    cypher = (
        f"MATCH (w:{ctx.label('WorkItem')} {{`key`: $key}})\n"
        "OPTIONAL MATCH (w)-[:HAS_CHANGE]->(s:"
        + ctx.label("StatusChange")
        + " {`field`: 'status'})\n"
        "WITH w, s ORDER BY s.at\n"
        # `epoch` rather than the string: comparing ISO strings is only correct while every
        # timestamp carries the same offset, and one `+02:00` row would silently reorder the
        # history. Seconds since the epoch are the same instant in every zone.
        "WITH w, collect({at: toString(s.at), epoch: s.at.epochSeconds, from: s.`from`,\n"
        "  to: s.`to`, by: s.by}) AS changes\n"
        "RETURN w.key AS key, w.title AS title, w.status AS current_status,\n"
        "  w.type AS type, toString(w.created) AS created,\n"
        "  w.created.epochSeconds AS created_epoch, w.resolution AS resolution,\n"
        "  coalesce(w.synthetic, false) AS synthetic, changes"
    )
    rows = ctx.read(cypher, key=key)
    items: list[Item] = []
    if not rows or rows[0]["key"] is None:
        raise RetrieveError(f"no WorkItem with key {key!r}")
    row = rows[0]
    changes = [c for c in row["changes"] if c.get("at")]
    cutoff = _end_of_day(date)
    before = [c for c in changes if (c.get("epoch") or 0) <= cutoff]
    after = [c for c in changes if (c.get("epoch") or 0) > cutoff]

    if row["created_epoch"] is not None and row["created_epoch"] > cutoff:
        status, basis = None, "did not exist yet"
    elif before:
        status, basis = before[-1]["to"], f"status change at {before[-1]['at']}"
    elif after:
        status, basis = after[0]["from"], f"initial status, before the change at {after[0]['at']}"
    else:
        status, basis = row["current_status"], "no status change recorded; current status"

    items.append(
        Item(
            kind="Row",
            key=f"{key}@{date}",
            title=f"{key} on {date}",
            snippet=f"{status or 'did not exist yet'} — {basis}",
            score=1.0,
            props={
                "work_item": key,
                "date": date,
                "status": status,
                "basis": basis,
                "current_status": row["current_status"],
                "created": row["created"],
                "changes_before": len(before),
                "changes_after": len(after),
                "synthetic": row["synthetic"],
            },
            provenance=[Provenance(source=f"StatusChange of {key}")],
        )
    )
    evidence, evidence_cypher = own_chunks(ctx, [key])
    items[-1].provenance.extend(as_provenance(evidence.get(key, []), key))
    items.append(
        to_item(
            "WorkItem",
            {
                "key": key,
                "title": row["title"],
                "type": row["type"],
                "status": row["current_status"],
                "resolution": row["resolution"],
                "created": row["created"],
                "synthetic": row["synthetic"],
            },
            0.5,
        )
    )
    items[-1].provenance.extend(as_provenance(evidence.get(key, []), key))
    return finish(
        "s6",
        items,
        question=f"status_at({key}, {date})",
        timer=timer,
        cypher_used=[cypher, evidence_cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )


def timeline(
    ctx: RetrieveContext,
    key: str,
    *,
    limit: int = 60,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Every recorded change on `key`, oldest first: status, assignee, fix version, component."""
    timer = Timer()
    cypher = (
        f"MATCH (w:{ctx.label('WorkItem')} {{`key`: $key}})\n"
        f"OPTIONAL MATCH (w)-[:HAS_CHANGE]->(s:{ctx.label('StatusChange')})\n"
        "RETURN w.key AS key, w.title AS title, w.status AS status, w.type AS type,\n"
        "  toString(w.created) AS created, coalesce(w.synthetic, false) AS synthetic,\n"
        "  s.id AS change_id, s.field AS field, s.`from` AS from_value, s.`to` AS to_value,\n"
        "  toString(s.at) AS at, s.by AS by\n"
        "ORDER BY s.at LIMIT $limit"
    )
    rows = ctx.read(cypher, key=key, limit=limit)
    if not rows:
        raise RetrieveError(f"no WorkItem with key {key!r}")
    head = rows[0]
    items: list[Item] = [
        to_item(
            "WorkItem",
            {
                "key": key,
                "title": head["title"],
                "status": head["status"],
                "type": head["type"],
                "created": head["created"],
                "synthetic": head["synthetic"],
            },
            1.0,
        )
    ]
    evidence, evidence_cypher = own_chunks(ctx, [key])
    items[0].provenance.extend(as_provenance(evidence.get(key, []), key))
    for index, row in enumerate(rows):
        if not row["change_id"]:
            continue
        items.append(
            Item(
                kind="Row",
                key=row["change_id"],
                title=f"{row['at']} · {row['field']}",
                snippet=f"{row['from_value']} → {row['to_value']}"
                + (f" (by {row['by']})" if row["by"] else ""),
                score=round(1.0 - index / max(len(rows), 1), 4),
                props={
                    "work_item": key,
                    "field": row["field"],
                    "from": row["from_value"],
                    "to": row["to_value"],
                    "at": row["at"],
                    "by": row["by"],
                },
                provenance=[Provenance(source=f"StatusChange of {key}")],
            )
        )
    return finish(
        "s6",
        items,
        question=f"timeline({key})",
        timer=timer,
        cypher_used=[cypher, evidence_cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )


def changes_between(
    ctx: RetrieveContext,
    component: str,
    v1: str,
    v2: str,
    *,
    limit: int = 60,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Work items in `component` fixed after `v1` and up to `v2`, with the commits that did it."""
    timer = Timer()
    cypher = (
        f"MATCH (w:{ctx.label('WorkItem')})-[:IN_COMPONENT]->"
        f"(c:{ctx.label('Component')} {{`name`: $component}})\n"
        f"MATCH (w)-[:FIX_VERSION]->(v:{ctx.label('Version')})\n"
        f"OPTIONAL MATCH (commit:{ctx.label('Commit')})-[:RESOLVES]->(w)\n"
        "RETURN w.key AS key, w.title AS title, w.type AS type, w.status AS status,\n"
        "  w.resolution AS resolution, coalesce(w.synthetic, false) AS synthetic,\n"
        "  v.name AS version, collect(DISTINCT commit.sha)[..5] AS commits,\n"
        "  collect(DISTINCT commit.message)[..1] AS commit_messages"
    )
    rows = ctx.read(cypher, component=component)
    low, high = _pad(_bound(v1, ceiling=True)), _pad(_bound(v2, ceiling=True))
    if low >= high:
        raise RetrieveError(f"version window is empty: {v1!r} is not before {v2!r}")

    items: list[Item] = []
    seen: set[str] = set()
    for row in rows:
        key = _pad(version_key(row["version"]))
        if not (low < key <= high) or row["key"] in seen:
            continue
        seen.add(row["key"])
        item = to_item(
            "WorkItem",
            {
                "key": row["key"],
                "title": row["title"],
                "type": row["type"],
                "status": row["status"],
                "resolution": row["resolution"],
                "synthetic": row["synthetic"],
            },
            round(1.0 / (1 + len(seen)), 4),
        )
        item.props.update({"fix_version": row["version"], "component": component})
        if row["commits"]:
            item.props["commits"] = row["commits"]
            item.provenance = [
                Provenance(
                    source=sha,
                    quote=clip(row["commit_messages"][0] if row["commit_messages"] else "", 160),
                )
                for sha in row["commits"][:2]
            ]
        items.append(item)
        if len(items) >= limit:
            break

    evidence, evidence_cypher = own_chunks(ctx, [i.key for i in items])
    for item in items:
        item.provenance.extend(as_provenance(evidence.get(item.key, []), item.key))

    items.insert(
        0,
        Item(
            kind="Row",
            key=f"{component}:{v1}..{v2}",
            title=f"{component} between {v1} and {v2}",
            snippet=f"{len(seen)} work items with a fix version in ({v1}, {v2}]",
            score=1.0,
            props={"component": component, "from": v1, "to": v2, "work_items": len(seen)},
        ),
    )
    return finish(
        "s6",
        items,
        question=f"changes_between({component}, {v1}, {v2})",
        timer=timer,
        cypher_used=[cypher, evidence_cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )


def assignees_over_time(
    ctx: RetrieveContext,
    key: str,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Every `ASSIGNED_TO` interval on `key`, oldest first. An open interval is the current one."""
    timer = Timer()
    cypher = (
        f"MATCH (w)-[r:ASSIGNED_TO]->(p:{ctx.label('Person')})\n"
        "WHERE coalesce(w.key, w.id) = $key\n"
        "RETURN coalesce(w.key, w.id) AS key, w.title AS title, w.status AS status,\n"
        "  p.id AS person, p.display AS display, toString(r.valid_from) AS valid_from,\n"
        "  toString(r.valid_to) AS valid_to, r.source AS source\n"
        "ORDER BY r.valid_from"
    )
    rows = ctx.read(cypher, key=key)
    evidence, evidence_cypher = own_chunks(ctx, [key])
    provenance = as_provenance(evidence.get(key, []), key)
    items: list[Item] = []
    for index, row in enumerate(rows):
        current = row["valid_to"] is None
        items.append(
            Item(
                kind="Person",
                key=row["person"],
                title=row["display"] or row["person"],
                snippet=f"{row['valid_from']} → {row['valid_to'] or 'now'}"
                + (" (current)" if current else ""),
                score=1.0 if current else round(0.9 - index / 100, 4),
                props={
                    "work_item": key,
                    "valid_from": row["valid_from"],
                    "valid_to": row["valid_to"],
                    "current": current,
                    "source": row["source"],
                },
                provenance=[
                    Provenance(source=f"ASSIGNED_TO interval on {key}"),
                    *provenance,
                ],
            )
        )
    if not items:
        items.append(
            Item(
                kind="Row",
                key=key,
                title=f"{key}: no assignment interval",
                snippet="the changelog records no assignee for this item",
                score=1.0,
                props={"work_item": key, "intervals": 0},
                provenance=provenance,
            )
        )
    return finish(
        "s6",
        items,
        question=f"assignees_over_time({key})",
        timer=timer,
        cypher_used=[cypher, evidence_cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )
