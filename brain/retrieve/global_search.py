"""S5 — global search over the community reports (spec §4.1, §4.3; plan Task 3).

The question this strategy exists for is `cq12`: *"What are the main themes of open bugs in
clients?"* No chunk answers it and no traversal answers it, because the answer is not in the
corpus — it is a shape *of* the corpus. What answers it is the layer `brain communities`
built: 186 Leiden communities that an agent summarised into a title, a summary and ~8
findings, each finding carrying the chunk ids it was read from.

So S5 is deliberately the thinnest strategy in the library: embed the question, ask
`community_embedding` for the nearest reports, and hand the agent the reports. The *reduce*
step — turning five themes into one answer — belongs to the asking agent, which is what
"global search" means in the GraphRAG papers and what plan Task 3 says.

**Why the dedupe is not optional.** `Community.id` is `L<level>-<leidenId>`, and 22 member
sets exist at both levels: the coarse level merged nothing into them, so `L0-280` and
`L1-351` are the same 173 members under two ids. Their reports were written from the same
members, so their embeddings sit almost on top of each other and a top-5 spends two of its
five slots on one theme. `member_hash` (sha1 over the member keys) is what tells them apart
from two genuinely different communities that happen to be about the same subject, and the
*fine* one is kept because its membership is its own rather than an artefact of a level that
did no work. The suppressed id is reported in `props.duplicate_of`, never silently dropped.

**Why the survivor inherits the pair's best score.** The two reports differ in wording, so
their cosines differ by a hair. Keeping the fine report *and* its own slightly worse score
would push the theme down the ranking for a reason that has nothing to do with the question.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from brain.community.graph import decode_findings
from brain.retrieve.context import RetrieveContext
from brain.retrieve.envelope import Timer, finish
from brain.retrieve.nodes import to_item
from brain.retrieve.pack import BUDGET_TOKENS, clip, item_tokens
from brain.retrieve.types import Item, Provenance, Result

#: The vector index `brain communities` fills, and the `IndexMeta` the model check reads.
COMMUNITY_INDEX = "community_embedding"

#: Level name -> the `Community.level` the graph stores. Level 0 is the finer Leiden level
#: (762 communities, 106 summarised); level 1 is the coarser one (535 / 80).
LEVELS: dict[str, int] = {"fine": 0, "coarse": 1}

#: Ask the index for more than `k`: dedupe and the "summarised only" filter both drop rows
#: after the index has ranked them.
OVERFETCH = 4

#: How much of the 4k budget one report may spend. A global answer is a *reduce over five
#: themes*, so breadth beats depth here: an untrimmed report costs ~1,400 tokens and the
#: packer would hand the agent two of the five themes it asked for. These caps bring a report
#: to ~700 tokens — five themes inside the same ceiling. `props.finding_count` always reports
#: the true number, so nothing is hidden, only elided.
MAX_FINDINGS = 5
FINDING_CHARS = 200
MAX_EVIDENCE_PER_FINDING = 1
SUMMARY_CHARS = 360
RANK_REASON_CHARS = 140
#: Evidence chunk ids carried in `provenance` per community. The findings in `props` keep the
#: per-finding mapping; this is the flat, checkable list a citation check reads.
MAX_PROVENANCE = 5


def level_filter(level: str) -> list[int] | None:
    """`[0]`, `[1]` or `None` for "both". An unknown name is a caller error, not a wildcard."""
    name = (level or "any").strip().lower()
    if name in ("any", "all", "both"):
        return None
    if name in LEVELS:
        return [LEVELS[name]]
    raise ValueError(f"level must be one of 'fine', 'coarse', 'any' — got {level!r}")


def dedupe_by_member_hash(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """One row per member set, the finest level winning, in score order.

    Returns the surviving rows (each with the pair's best `score`) and
    `{surviving id: [suppressed id, …]}`. Rows without a `member_hash` are never merged:
    a null hash is a missing fact, not evidence that two communities are the same.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for index, row in enumerate(rows):
        key = row.get("member_hash") or f"__unhashed_{index}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    kept: list[dict[str, Any]] = []
    dropped: dict[str, list[str]] = {}
    for key in order:
        members = groups[key]
        # Finest level first; a tie goes to the better score, then to the id, so the choice
        # is the same on every run.
        winner = min(
            members,
            key=lambda r: (int(r.get("level") or 0), -float(r.get("score") or 0.0), str(r["id"])),
        )
        best = max(float(m.get("score") or 0.0) for m in members)
        survivor = {**winner, "score": best}
        others = [str(m["id"]) for m in members if m["id"] != winner["id"]]
        if others:
            dropped[str(winner["id"])] = others
        kept.append(survivor)
    kept.sort(key=lambda r: -float(r.get("score") or 0.0))
    return kept, dropped


def _query(ctx: RetrieveContext, levels: list[int] | None) -> str:
    """Top-k summarised communities by cosine. Unsummarised ones have nothing to return."""
    level_clause = " AND c.`level` IN $levels" if levels is not None else ""
    return (
        "CALL db.index.vector.queryNodes($index, $wide, $vector) YIELD node AS c, score\n"
        f"WHERE c:{ctx.label('Community')} AND c.`summary` IS NOT NULL{level_clause}"
        + ctx.synthetic_clause("c")
        + "\nRETURN c.`id` AS id, c.`level` AS level, c.`level_name` AS level_name,\n"
        "  c.`size` AS size, c.`member_hash` AS member_hash, c.`title` AS title,\n"
        "  c.`summary` AS summary, c.`rank` AS rank, c.`rank_reason` AS rank_reason,\n"
        "  c.`findings` AS findings, coalesce(c.`evidence_chunk_ids`, []) AS evidence_chunk_ids,\n"
        "  c.`members_by_label` AS members_by_label, coalesce(c.`misc`, false) AS misc,\n"
        "  c.`batch_id` AS batch_id, c.`model` AS model,\n"
        "  toString(c.`extracted_at`) AS extracted_at, score\n"
        "ORDER BY score DESC"
    )


def trim_findings(findings: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The findings an answer can afford: the first `MAX_FINDINGS`, each clipped.

    The agent writes the reduce; it needs the claim and one chunk id it can cite, not the
    whole report. Order is the order the summariser wrote them in, which is its own ranking.
    """
    return [
        {
            "statement": clip(f.get("statement") or "", FINDING_CHARS),
            "evidence_chunk_ids": list(f.get("evidence_chunk_ids") or [])[
                :MAX_EVIDENCE_PER_FINDING
            ],
        }
        for f in findings[:MAX_FINDINGS]
    ]


def _item(row: dict[str, Any], duplicates: list[str]) -> Item:
    """One community report as the uniform envelope, provenance = its findings' chunk ids."""
    findings = decode_findings(row.get("findings"))
    trimmed = trim_findings(findings)
    node = {
        "id": row["id"],
        "level": row.get("level"),
        "title": row.get("title") or "",
        "summary": clip(row.get("summary") or "", SUMMARY_CHARS),
        "rank": row.get("rank"),
    }
    item = to_item(
        "Community",
        node,
        float(row.get("score") or 0.0),
        level_name=row.get("level_name"),
        size=row.get("size"),
        rank_reason=clip(row.get("rank_reason") or "", RANK_REASON_CHARS),
        members_by_label=row.get("members_by_label"),
        misc=bool(row.get("misc")),
        findings=trimmed,
        finding_count=len(findings),
        batch_id=row.get("batch_id"),
        model=row.get("model"),
    )
    if duplicates:
        # Only when it explains something: `member_hash` is the reason *this* report survived
        # and the other did not, and it is 40 characters of the budget when nothing was
        # suppressed.
        item.props["duplicate_of"] = duplicates
        item.props["member_hash"] = row.get("member_hash")

    # A finding's own evidence first — that is the claim a citation can be checked against —
    # and the community's flat list only to fill the remainder.
    ordered: list[str] = []
    for finding in findings:
        ordered.extend(finding.get("evidence_chunk_ids") or [])
    ordered.extend(row.get("evidence_chunk_ids") or [])
    seen: dict[str, None] = {}
    for chunk_id in ordered:
        if chunk_id:
            seen.setdefault(chunk_id, None)
    # `batch_id` and `model` are the same for every chunk of one report, so they are stated
    # once in `props` instead of `MAX_PROVENANCE` times here (conventions rule 3 is about the
    # node carrying its provenance, not about repeating it per citation). What each entry has
    # to carry is the chunk id, because that is the field `brain eval cite-check` verifies.
    item.provenance = [
        Provenance(chunk_id=chunk_id, source=str(row["id"]))
        for chunk_id in list(seen)[:MAX_PROVENANCE]
    ]
    return item


def fit_to_share(item: Item, share_tokens: int) -> Item:
    """Shed detail until this report fits its share of the answer, keeping it an answer.

    The caps above are what a report is *allowed* to be; this is what it can *afford* to be
    when `k` of them have to share one 4k ceiling. Without it the packer does the trimming,
    and the packer's only move is to drop a whole community — so asking for five themes would
    return three, which is a worse answer to a thematic question than five terser ones.

    Order of shedding is order of value: extra findings first, then extra chunk ids, then the
    summary. A title, one finding and one citation always survive — below that the item stops
    being evidence of anything.
    """
    while item_tokens(item) > share_tokens:
        findings = item.props.get("findings") or []
        if len(findings) > 1:
            item.props["findings"] = findings[:-1]
            continue
        if len(item.provenance) > 1:
            item.provenance = item.provenance[:-1]
            continue
        if len(item.snippet) > 200:
            item.snippet = clip(item.snippet, 200)
            continue
        return item
    return item


def global_search(
    ctx: RetrieveContext,
    query: str,
    level: str = "any",
    k: int = 5,
    *,
    log_mode: str = "python",
    log_path: Path | None = None,
    log: bool = True,
    route: dict[str, Any] | None = None,
) -> Result:
    """Top-`k` community reports for `query`. The agent does the reduce; this does the map."""
    timer = Timer()
    levels = level_filter(level)
    index = ctx.require_index(COMMUNITY_INDEX)
    vector = ctx.embed_query(query, COMMUNITY_INDEX)

    cypher = _query(ctx, levels)
    rows = ctx.read(
        cypher,
        index=index,
        wide=max(int(k), 1) * OVERFETCH,
        vector=list(vector),
        levels=levels,
    )
    kept, dropped = dedupe_by_member_hash(rows)
    wanted = max(int(k), 1)
    share = BUDGET_TOKENS // wanted
    items = [
        fit_to_share(_item(row, dropped.get(str(row["id"]), [])), share) for row in kept[:wanted]
    ]

    return finish(
        "s5",
        items,
        question=query,
        timer=timer,
        cypher_used=[cypher],
        route=route,
        mode=log_mode,
        log_path=log_path,
        log=log,
    )
