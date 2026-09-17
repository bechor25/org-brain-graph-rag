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

**A Document anchor gets a derived history.** Ten temporal questions in the Plan 3 sweep
name a KIP, and a KIP is a `Document`: it has no changelog, no assignee and no fix version
of its own, so every tool here used to answer `no WorkItem with key 'KIP-848'` — a true
sentence about the graph and a useless one about the question. A KIP does have a history;
it is just written down somewhere else:

* the commits that `IMPLEMENTS_KIP` it — when the design was actually built, and by whom,
* the `StatusChange` events of the work items that `REFERENCES` it — when the work moved,
* the `FIX_VERSION` of those same work items — which release carried it.

The union of those, in time order, is the KIP's timeline; the `ASSIGNED_TO` intervals of
the same work items, merged per person, are its assignees. Nothing here infers anything:
each row is one edge the graph holds, and `props.via` names that edge (`IMPLEMENTS_KIP` or
`REFERENCES`) so a reader can tell a derived row from a recorded one. A work item's own
answer is untouched — same query, same rows, same `cypher_used`.

A fix version has no timestamp of its own (`FIX_VERSION` is a plain edge), so it is dated
by the `Fix Version` changelog row that first set it, or by the item's resolution when the
changelog has none, and `props.dated_by` says which. An undated event sorts last rather
than pretending to a place in the order.
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
#: Status changes read per referencing work item. `status_at` needs the whole history to
#: find the last row before a date, so this is a guard against a pathological item, not a
#: display cap: the largest history in this corpus is two orders of magnitude below it.
MAX_CHANGES_PER_ITEM = 500
#: Referencing work items read when the answer is per-item rather than per-event.
MAX_REFERRERS = 60
#: `ASSIGNED_TO` intervals read across every referencing work item before merging.
MAX_INTERVALS = 400


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


# --------------------------------------------------------------------- the arithmetic
#
# Three pure functions, kept apart from the queries so they can be tested without a
# database and so the work item's answer and the document's derived answer are decided by
# the same code rather than by two implementations that agree today.


def _status_on(
    changes: list[dict[str, Any]],
    created_epoch: int | None,
    current_status: str | None,
    cutoff: int,
) -> dict[str, Any]:
    """The status an item held at `cutoff`, and the row that says so.

    `changes` is ordered oldest first and carries `epoch`/`at`/`from`/`to`. The subtlety is
    the initial status: a changelog records *transitions*, so the status before the first
    one is that transition's `from` — not `null` and not today's status.
    """
    before = [c for c in changes if (c.get("epoch") or 0) <= cutoff]
    after = [c for c in changes if (c.get("epoch") or 0) > cutoff]
    existed = not (created_epoch is not None and created_epoch > cutoff)
    if not existed:
        status, basis = None, "did not exist yet"
    elif before:
        status, basis = before[-1]["to"], f"status change at {before[-1]['at']}"
    elif after:
        status, basis = after[0]["from"], f"initial status, before the change at {after[0]['at']}"
    else:
        status, basis = current_status, "no status change recorded; current status"
    return {
        "status": status,
        "basis": basis,
        "before": len(before),
        "after": len(after),
        "existed": existed,
    }


def _by_time(events: list[dict[str, Any]], limit: int) -> tuple[list[dict[str, Any]], bool]:
    """Oldest first, undated last, ties broken by key; and whether anything was cut.

    A derived timeline mixes three sources, and a fix version can have no timestamp at all.
    Sorting `None` as if it were zero would put it at the dawn of the corpus, which is a
    claim the graph never made, so undated events sort after every dated one.
    """
    ordered = sorted(events, key=lambda e: (e["epoch"] is None, e["epoch"] or 0, e["key"]))
    return ordered[:limit], len(ordered) > limit


def _merge_intervals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`ASSIGNED_TO` rows from several work items, merged into one entry per person.

    Twelve items referencing one KIP produce the same three names twelve times over; the
    answer to "who was assigned over time" is the three names, each with the span they
    actually held. An open interval anywhere means the person still holds something, so it
    beats every closed one — `valid_to` is `None` and `current` is true.
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        person = row.get("person")
        if not person:
            continue
        entry = merged.get(person)
        if entry is None:
            merged[person] = {
                "person": person,
                "display": row.get("display"),
                "valid_from": row.get("valid_from"),
                "from_epoch": row.get("from_epoch"),
                "valid_to": row.get("valid_to"),
                "to_epoch": row.get("to_epoch"),
                "current": row.get("valid_to") is None,
                "intervals": 1,
                "work_items": [row["work_item"]],
            }
            continue
        entry["intervals"] += 1
        if row["work_item"] not in entry["work_items"]:
            entry["work_items"].append(row["work_item"])
        if entry["current"]:
            continue
        if row.get("valid_to") is None:
            entry["current"], entry["valid_to"], entry["to_epoch"] = True, None, None
        elif (row.get("to_epoch") or 0, row.get("valid_to") or "") > (
            entry.get("to_epoch") or 0,
            entry.get("valid_to") or "",
        ):
            entry["valid_to"], entry["to_epoch"] = row["valid_to"], row.get("to_epoch")
    return list(merged.values())


# ------------------------------------------------------- the document anchor's three reads


def _document(ctx: RetrieveContext, key: str) -> tuple[dict[str, Any] | None, str]:
    """The Document this key names, or `None`. The anchor test for every derived answer."""
    cypher = (
        f"MATCH (d:{ctx.label('Document')} {{`key`: $key}})\n"
        "RETURN d.key AS key, d.title AS title, d.kind AS kind, d.space AS space,\n"
        "  d.source AS source, d.status AS status, toString(d.updated) AS updated,\n"
        "  coalesce(d.synthetic, false) AS synthetic"
    )
    rows = ctx.read(cypher, key=key)
    return (rows[0] if rows and rows[0].get("key") else None), cypher


def _implementing_commits(
    ctx: RetrieveContext, key: str, limit: int
) -> tuple[list[dict[str, Any]], str]:
    """The commits that say they implement this document, oldest first."""
    cypher = (
        f"MATCH (c:{ctx.label('Commit')})-[:IMPLEMENTS_KIP]->"
        f"(d:{ctx.label('Document')} {{`key`: $key}})\n"
        "RETURN c.sha AS sha, toString(c.at) AS at, c.at.epochSeconds AS epoch,\n"
        "  c.author_name AS author, c.message AS message,\n"
        "  coalesce(c.synthetic, false) AS synthetic\n"
        "ORDER BY c.at, c.sha LIMIT $limit"
    )
    return ctx.read(cypher, key=key, limit=limit), cypher


def _referencing_items(
    ctx: RetrieveContext, key: str, *, items: int, changes: int
) -> tuple[list[dict[str, Any]], str]:
    """Work items that reference this document, each with its status history and releases.

    One query rather than three: the two scoped subqueries aggregate, so a work item with
    no status change and no fix version still comes back (with empty lists) instead of
    being dropped by an inner match that found nothing.
    """
    cypher = (
        f"MATCH (w:{ctx.label('WorkItem')})-[:REFERENCES]->"
        f"(d:{ctx.label('Document')} {{`key`: $key}})\n"
        "CALL (w) {\n"
        f"  MATCH (w)-[:HAS_CHANGE]->(s:{ctx.label('StatusChange')} {{`field`: 'status'}})\n"
        # `epoch` rather than the string, for the same reason `status_at` does it: two rows
        # written in different offsets compare wrongly as ISO text and correctly as instants.
        "  WITH s ORDER BY s.at\n"
        "  RETURN collect({id: s.id, at: toString(s.at), epoch: s.at.epochSeconds,\n"
        "    from: s.`from`, to: s.`to`, by: s.by})[..$changes] AS changes\n"
        "}\n"
        "CALL (w) {\n"
        f"  MATCH (w)-[:FIX_VERSION]->(v:{ctx.label('Version')})\n"
        f"  OPTIONAL MATCH (w)-[:HAS_CHANGE]->(s:{ctx.label('StatusChange')}"
        " {`field`: 'Fix Version'})\n"
        "    WHERE s.`to` = v.name\n"
        # The first time this version was set, not the last: a fix version re-applied in a
        # later edit is the same decision, and the timeline wants the moment it was made.
        "  WITH v, s ORDER BY s.at\n"
        "  WITH v, head(collect({at: toString(s.at), epoch: s.at.epochSeconds})) AS first_set\n"
        "  ORDER BY v.name\n"
        "  RETURN collect({version: v.name, at: first_set.at,\n"
        "    epoch: first_set.epoch}) AS versions\n"
        "}\n"
        "RETURN w.key AS key, w.title AS title, w.type AS type, w.status AS status,\n"
        "  toString(w.created) AS created, w.created.epochSeconds AS created_epoch,\n"
        "  toString(w.resolved_at) AS resolved, w.resolved_at.epochSeconds AS resolved_epoch,\n"
        "  coalesce(w.synthetic, false) AS synthetic, changes, versions\n"
        "ORDER BY w.created, w.key LIMIT $items"
    )
    return ctx.read(cypher, key=key, items=items, changes=changes), cypher


def _referencing_assignments(
    ctx: RetrieveContext, key: str, limit: int
) -> tuple[list[dict[str, Any]], str]:
    """Every assignment interval on every work item that references this document."""
    cypher = (
        f"MATCH (w:{ctx.label('WorkItem')})-[:REFERENCES]->"
        f"(d:{ctx.label('Document')} {{`key`: $key}})\n"
        f"MATCH (w)-[r:ASSIGNED_TO]->(p:{ctx.label('Person')})\n"
        "RETURN p.id AS person, p.display AS display, w.key AS work_item,\n"
        "  toString(r.valid_from) AS valid_from, r.valid_from.epochSeconds AS from_epoch,\n"
        "  toString(r.valid_to) AS valid_to, r.valid_to.epochSeconds AS to_epoch,\n"
        "  r.source AS source\n"
        "ORDER BY r.valid_from, w.key LIMIT $limit"
    )
    return ctx.read(cypher, key=key, limit=limit), cypher


def _stamp(at: str | None) -> str:
    return at or "undated"


def _document_events(key: str, commits: list[dict], refs: list[dict]) -> list[dict[str, Any]]:
    """The three event sources as one list of rows, each naming the edge it came from."""
    events: list[dict[str, Any]] = []
    for row in commits:
        message = clip(row.get("message"), 160)
        events.append(
            {
                "epoch": row.get("epoch"),
                "key": row["sha"],
                "title": f"{_stamp(row.get('at'))} · commit implements {key}",
                "snippet": message + (f" (by {row['author']})" if row.get("author") else ""),
                "props": {
                    "document": key,
                    "via": "IMPLEMENTS_KIP",
                    "event": "commit",
                    "sha": row["sha"],
                    "at": row.get("at"),
                    "author": row.get("author"),
                    "synthetic": row.get("synthetic"),
                },
                "source": row["sha"],
                "quote": message,
            }
        )
    for row in refs:
        item = row["key"]
        for change in row.get("changes") or []:
            if not change.get("id"):
                continue
            by = f" (by {change['by']})" if change.get("by") else ""
            events.append(
                {
                    "epoch": change.get("epoch"),
                    "key": change["id"],
                    "title": f"{_stamp(change.get('at'))} · {item} status",
                    "snippet": f"{change.get('from')} → {change.get('to')}{by}",
                    "props": {
                        "document": key,
                        "via": "REFERENCES",
                        "event": "status",
                        "work_item": item,
                        "field": "status",
                        "from": change.get("from"),
                        "to": change.get("to"),
                        "at": change.get("at"),
                        "by": change.get("by"),
                        "synthetic": row.get("synthetic"),
                    },
                    "source": item,
                    "quote": f"{change.get('from')} → {change.get('to')} "
                    f"at {_stamp(change.get('at'))}",
                }
            )
        for version in row.get("versions") or []:
            name = version.get("version")
            if not name:
                continue
            epoch, at, dated_by = version.get("epoch"), version.get("at"), "changelog"
            if epoch is None:
                epoch, at = row.get("resolved_epoch"), row.get("resolved")
                dated_by = "resolution" if epoch is not None else "undated"
            note = {
                "changelog": "",
                "resolution": " (dated by the item's resolution)",
                "undated": " (no date recorded)",
            }[dated_by]
            events.append(
                {
                    "epoch": epoch,
                    "key": f"{item}@{name}",
                    "title": f"{_stamp(at)} · {item} fix version",
                    "snippet": f"fix version {name}{note}",
                    "props": {
                        "document": key,
                        "via": "REFERENCES",
                        "event": "fix_version",
                        "work_item": item,
                        "fix_version": name,
                        "at": at,
                        "dated_by": dated_by,
                        "synthetic": row.get("synthetic"),
                    },
                    "source": item,
                    "quote": f"fix version {name}",
                }
            )
    return events


def _event_item(event: dict[str, Any], score: float) -> Item:
    return Item(
        kind="Row",
        key=event["key"],
        title=event["title"],
        snippet=event["snippet"],
        score=score,
        props=event["props"],
        provenance=[Provenance(source=event["source"], source_kind="row", quote=event["quote"])],
    )


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
    """What `key` held on `date` (`YYYY-MM-DD`), from the changelog. Deterministic.

    A work item answers from its own `StatusChange` rows; a document answers for each
    work item that references it (see `_document_status_at`).
    """
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
        return _document_status_at(
            ctx,
            key,
            date,
            timer=timer,
            log_mode=log_mode,
            log_path=log_path,
            log=log,
            route=route,
        )
    row = rows[0]
    changes = [c for c in row["changes"] if c.get("at")]
    cutoff = _end_of_day(date)
    on = _status_on(changes, row["created_epoch"], row["current_status"], cutoff)
    status, basis = on["status"], on["basis"]

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
                "changes_before": on["before"],
                "changes_after": on["after"],
                "synthetic": row["synthetic"],
            },
            provenance=[Provenance(source=f"StatusChange of {key}", source_kind="row")],
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


def _document_status_at(
    ctx: RetrieveContext,
    key: str,
    date: str,
    *,
    timer: Timer,
    log_mode: str,
    log_path: Path | None,
    log: bool,
    route: dict[str, Any] | None,
) -> Result:
    """A document's status on a date: one row per work item that references it.

    A KIP has no status of its own unless the page carries one, so the honest answer is
    the state of the work it names, item by item, computed by the same `_status_on` the
    work item's own answer uses. Items that did not exist yet are still returned and say
    so, and they score below the ones that did: "no row" and "not yet" are different
    answers and only one of them is evidence.
    """
    doc, doc_cypher = _document(ctx, key)
    if doc is None:
        raise RetrieveError(f"no WorkItem or Document with key {key!r}")
    if route is not None:
        route["anchor"] = "Document"
    refs, ref_cypher = _referencing_items(
        ctx, key, items=MAX_REFERRERS + 1, changes=MAX_CHANGES_PER_ITEM
    )
    cut = len(refs) > MAX_REFERRERS
    refs = refs[:MAX_REFERRERS]
    cutoff = _end_of_day(date)

    answers = [
        (
            row,
            _status_on(
                [c for c in row["changes"] if c.get("at")],
                row["created_epoch"],
                row["status"],
                cutoff,
            ),
        )
        for row in refs
    ]
    existed = [a for a in answers if a[1]["existed"]]
    statuses: dict[str, int] = {}
    for _, on in existed:
        label = on["status"] or "unknown"
        statuses[label] = statuses.get(label, 0) + 1
    summary = ", ".join(f"{n} {name}" for name, n in sorted(statuses.items())) or "nothing"
    own_status = doc.get("status")

    evidence, evidence_cypher = own_chunks(ctx, [key])
    document_evidence = as_provenance(evidence.get(key, []), key)
    head = Item(
        kind="Row",
        key=f"{key}@{date}",
        title=f"{key} on {date}",
        snippet=(f"{own_status} — the document's own status. " if own_status else "")
        + f"{len(existed)} of {len(refs)} work items referencing {key} existed on {date}"
        + f": {summary}",
        score=1.0,
        props={
            "document": key,
            "date": date,
            "status": own_status,
            "via": "REFERENCES",
            "referencing": len(refs),
            "existed": len(existed),
            "statuses": statuses,
        },
        provenance=[
            Provenance(source=f"REFERENCES of {key}", source_kind="row"),
            *document_evidence,
        ],
    )
    items: list[Item] = [head]
    for index, (row, on) in enumerate(answers):
        item_key = row["key"]
        items.append(
            Item(
                kind="Row",
                key=f"{item_key}@{date}",
                title=f"{item_key} on {date}",
                snippet=f"{on['status'] or 'did not exist yet'} — {on['basis']}",
                score=round((0.9 if on["existed"] else 0.2) - index / 1000, 4),
                props={
                    "document": key,
                    "via": "REFERENCES",
                    "work_item": item_key,
                    "date": date,
                    "status": on["status"],
                    "basis": on["basis"],
                    "current_status": row["status"],
                    "created": row["created"],
                    "changes_before": on["before"],
                    "changes_after": on["after"],
                    "synthetic": row["synthetic"],
                },
                provenance=[
                    Provenance(
                        source=item_key,
                        source_kind="row",
                        quote=f"{on['status'] or 'did not exist yet'} — {on['basis']}",
                    )
                ],
            )
        )
    document = to_item("Document", doc, 0.5)
    document.provenance.extend(document_evidence)
    items.append(document)
    return finish(
        "s6",
        items,
        question=f"status_at({key}, {date})",
        timer=timer,
        cypher_used=[doc_cypher, ref_cypher, evidence_cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
        already_truncated=cut,
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
    """Every recorded change on `key`, oldest first: status, assignee, fix version, component.

    A document has no changelog of its own and gets the derived timeline instead — its
    commits and the movement of what references it (see `_document_timeline`).
    """
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
        return _document_timeline(
            ctx,
            key,
            limit=limit,
            timer=timer,
            log_mode=log_mode,
            log_path=log_path,
            log=log,
            route=route,
        )
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
                provenance=[Provenance(source=f"StatusChange of {key}", source_kind="row")],
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


def _document_timeline(
    ctx: RetrieveContext,
    key: str,
    *,
    limit: int,
    timer: Timer,
    log_mode: str,
    log_path: Path | None,
    log: bool,
    route: dict[str, Any] | None,
) -> Result:
    """A document's history, derived: its commits, and the movement of what references it.

    Oldest first, like the work item's own timeline, and scored the same way — so when the
    4k budget cuts, what survives is the beginning of the story rather than an arbitrary
    slice of it.
    """
    doc, doc_cypher = _document(ctx, key)
    if doc is None:
        raise RetrieveError(f"no WorkItem or Document with key {key!r}")
    if route is not None:
        route["anchor"] = "Document"
    # One row past the cap on each read, so "there was more" is measured rather than
    # inferred from a full page.
    commits, commit_cypher = _implementing_commits(ctx, key, limit + 1)
    refs, ref_cypher = _referencing_items(ctx, key, items=limit + 1, changes=MAX_CHANGES_PER_ITEM)
    cut = len(commits) > limit or len(refs) > limit
    events, more = _by_time(_document_events(key, commits[:limit], refs[:limit]), limit)

    evidence, evidence_cypher = own_chunks(ctx, [key])
    head = to_item("Document", doc, 1.0)
    head.provenance.extend(as_provenance(evidence.get(key, []), key))
    head.props.update(
        {
            "derived_from": ["IMPLEMENTS_KIP", "REFERENCES"],
            "commits": len(commits[:limit]),
            "referencing_work_items": len(refs[:limit]),
            "events": len(events),
        }
    )
    items: list[Item] = [head]
    for index, event in enumerate(events):
        items.append(_event_item(event, round(1.0 - index / max(len(events), 1), 4)))
    return finish(
        "s6",
        items,
        question=f"timeline({key})",
        timer=timer,
        cypher_used=[doc_cypher, commit_cypher, ref_cypher, evidence_cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
        already_truncated=cut or more,
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
                    source_kind="node-text",
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
    """Every `ASSIGNED_TO` interval on `key`, oldest first. An open interval is the current one.

    A document has no assignee; it gets the people who held the work that references it,
    merged one entry per person (see `_document_assignees`).
    """
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
                    Provenance(source=f"ASSIGNED_TO interval on {key}", source_kind="row"),
                    *provenance,
                ],
            )
        )
    if not items:
        document = _document_assignees(
            ctx,
            key,
            timer=timer,
            log_mode=log_mode,
            log_path=log_path,
            log=log,
            route=route,
            evidence=provenance,
            evidence_cypher=evidence_cypher,
        )
        if document is not None:
            return document
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


def _document_assignees(
    ctx: RetrieveContext,
    key: str,
    *,
    timer: Timer,
    log_mode: str,
    log_path: Path | None,
    log: bool,
    route: dict[str, Any] | None,
    evidence: list[Provenance],
    evidence_cypher: str,
) -> Result | None:
    """Who held the work that references this document, merged per person.

    `None` when the key is not a document at all — then the caller's "no assignment
    interval" row is still the right answer, and it is left to say so.
    """
    doc, doc_cypher = _document(ctx, key)
    if doc is None:
        return None
    if route is not None:
        route["anchor"] = "Document"
    rows, assign_cypher = _referencing_assignments(ctx, key, MAX_INTERVALS)
    merged = _merge_intervals(rows)
    items: list[Item] = []
    for index, entry in enumerate(merged):
        held = entry["work_items"]
        items.append(
            Item(
                kind="Person",
                key=entry["person"],
                title=entry["display"] or entry["person"],
                snippet=f"{entry['valid_from']} → {entry['valid_to'] or 'now'}"
                + (" (current)" if entry["current"] else "")
                + f" · {entry['intervals']} interval(s) on {len(held)} item(s)"
                + f" referencing {key}: {', '.join(held[:5])}",
                score=1.0 if entry["current"] else round(0.9 - index / 100, 4),
                props={
                    "document": key,
                    "via": "REFERENCES",
                    "valid_from": entry["valid_from"],
                    "valid_to": entry["valid_to"],
                    "current": entry["current"],
                    "intervals": entry["intervals"],
                    "work_items": held[:10],
                },
                provenance=[
                    Provenance(
                        source=item,
                        source_kind="row",
                        quote=f"ASSIGNED_TO {entry['person']} on {item}",
                    )
                    for item in held[:3]
                ],
            )
        )
    if not items:
        items.append(
            Item(
                kind="Row",
                key=key,
                title=f"{key}: no assignment interval",
                snippet=f"no work item referencing {key} records an assignee",
                score=1.0,
                props={"document": key, "via": "REFERENCES", "intervals": 0},
                provenance=evidence,
            )
        )
    return finish(
        "s6",
        items,
        question=f"assignees_over_time({key})",
        timer=timer,
        cypher_used=[doc_cypher, assign_cypher, evidence_cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
        already_truncated=len(rows) >= MAX_INTERVALS,
    )
