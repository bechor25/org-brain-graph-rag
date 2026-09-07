"""Tier 1 (deterministic) and the tier-2 band. No I/O: pairs in, pairs out.

Every tier-1 rule is a *stated* reason, not a similarity — the pair is proposed because
two systems wrote the same fact about the same identity, and the report names which rule
did it. That is what makes tier 1 auditable in a way tier 2 can never be, and it is why
`Pair.rule` is a required field rather than a comment.

The one rule the brief spells out and this corpus cannot honour is "identical display +
activity overlap": the synthetic ADO/Xray identities share no neighbour with the Jira
identity they belong to (measured: 0 of 420), so the rule is implemented, reported, and
finds only the real-corpus duplicates it was written for.
"""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from brain.resolve.models import Candidate, Pair, make_pair
from brain.resolve.names import (
    display_tokens,
    key_of,
    norm_display,
    rejected_stem,
    source_of,
    username_stem,
)

#: Cosine at or above which tier 2 merges without asking (spec 3.6, brief decision 3).
AUTO_THRESHOLD = 0.92
#: Cosine below which tier 2 does not even ask.
ADJUDICATE_FLOOR = 0.80
#: Substantive name words (initials do not count) a display must carry before any tier is
#: allowed to merge it *without asking*. Measured: "S. An", "J. Wang", "N. Kumar" are one
#: surname and one letter, bge-m3 scores them 0.93-1.00 against a different person of the
#: same surname, and every graded false positive tier 2 made was a pair like that. Below
#: this the pair is not dropped — it is demoted to the adjudicator, which can read context.
MIN_SUBSTANTIVE_TOKENS = 2
#: Shortest identity-key stem that counts as a name in the grey-band blocking. Three
#: characters match by accident; four rarely do.
MIN_STEM = 4
#: `KIP-848: The Next Generation…` -> `The Next Generation…`
_KIP_PREFIX = re.compile(r"^\s*KIP-\d+\s*[:.\-]?\s*", re.IGNORECASE)

#: Tier-1 rule names, in the order the report lists them.
PERSON_RULES: tuple[str, ...] = (
    "email",
    "jira_git_name",
    "display_and_activity",
    "alias_candidates",
    "confluence_userkey",
)
ENTITY_RULES: tuple[str, ...] = ("norm_name", "kip_title_alias")
#: Kinds a KIP title may name. A `Decision` or a `Problem` called after a KIP is a
#: coincidence of phrasing; a `Feature` or a `Technology` is the thing the KIP proposes.
KIP_ALIAS_KINDS: frozenset[str] = frozenset({"Feature", "Technology"})


def _index(candidates: Iterable[Candidate]) -> dict[str, Candidate]:
    return {c.id: c for c in candidates}


def substantive(name: str | None) -> int:
    """How many words of the display are more than an initial."""
    return len(display_tokens(name))


def auto_guard(a: Candidate, b: Candidate) -> bool:
    """May this pair be merged without a reader looking at it?

    Only when both sides name a person in more than one word. An initials-plus-surname
    display carries no evidence a machine can weigh — see `MIN_SUBSTANTIVE_TOKENS`.
    """
    return (
        substantive(a.name) >= MIN_SUBSTANTIVE_TOKENS
        and substantive(b.name) >= MIN_SUBSTANTIVE_TOKENS
    )


def stems(candidate: Candidate) -> frozenset[str]:
    """Name-shaped words from every identity key this candidate holds.

    `ado:an.sanghyeok.10115` gives {`sanghyeok`}; `jira:chickenchickenlove` gives
    {`chickenchickenlove`}. Digits and short fragments are dropped: they match by accident.
    """
    words: set[str] = set()
    for identity in candidate.identities or [candidate.id]:
        for word in norm_display(key_of(identity)).split(" "):
            if len(word) >= MIN_STEM and not word.isdigit():
                words.add(word)
    return frozenset(words)


def initials_key(name: str | None) -> tuple[str, str] | None:
    """`"S. An"` and `"Sanghyeok An"` both give `("s", "an")`. `None` for a one-word name.

    This is what makes the *hard* pairs reachable: the whole point of the grey band is that
    the adjudicator sees "S. An" beside "Shichao An" and beside "Sanghyeok An" and decides
    which is which. Blocking them out to save batches would remove the question.
    """
    words = [w for w in norm_display(name).split(" ") if w]
    if len(words) < 2:
        return None
    return (words[0][0], words[-1])


def name_blocked(a: Candidate, b: Candidate) -> bool:
    """Do these two names share anything a person would recognise as the same name?

    A cosine of 0.83 between two short unrelated names is noise the embedding cannot help
    with; a shared surname, a shared username stem or the same initial-plus-surname is a
    question worth an agent's time. Applied to the grey band only — never to a merge.
    """
    if display_tokens(a.name) & display_tokens(b.name):
        return True
    if stems(a) & stems(b):
        return True
    key = initials_key(a.name)
    return key is not None and key == initials_key(b.name)


def _pairs_from_buckets(
    buckets: dict[str, list[str]],
    by_id: dict[str, Candidate],
    *,
    kind: str,
    tier: int,
    rule: str,
    reason: str,
) -> list[Pair]:
    out: list[Pair] = []
    for value, ids in sorted(buckets.items()):
        if len(ids) < 2:
            continue
        for a, b in itertools.combinations(sorted(set(ids)), 2):
            if by_id[a].block != by_id[b].block:
                continue  # brief decision 8: never across kinds
            out.append(
                make_pair(
                    a,
                    b,
                    kind=kind,
                    block=by_id[a].block,
                    tier=tier,
                    rule=rule,
                    score=1.0,
                    reason=f"{reason}: {value}",
                )
            )
    return out


def person_tier1(
    candidates: Sequence[Candidate],
    *,
    alias_candidates: Sequence[dict] = (),
) -> list[Pair]:
    """Brief 08 decision 1 (a)-(d), each rule tagged with the name the report uses."""
    by_id = _index(candidates)
    pairs: list[Pair] = []

    # (a) the same email address, lowercased. Only git carries one in this corpus.
    by_email: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        if c.email:
            by_email[c.email.strip().lower()].append(c.id)
    pairs += _pairs_from_buckets(
        by_email, by_id, kind="person", tier=1, rule="email", reason="same email"
    )

    # (b1) `jira.name == git.name` normalised: the Jira *username* is the git author name.
    jira_by_name: dict[str, list[str]] = defaultdict(list)
    git_by_name: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        source = source_of(c.id)
        if source == "jira":
            # The Jira *username* (`name` in the API), not `displayName`: two different
            # people share a display name, and nobody shares a username. Display equality
            # is rule (b2) below, and it has to bring activity with it.
            if username := norm_display(c.id.split(":", 1)[1]):
                jira_by_name[username].append(c.id)
        elif source == "git":
            if name := norm_display(c.name):
                git_by_name[name].append(c.id)
    for name, jira_ids in sorted(jira_by_name.items()):
        for a, b in itertools.product(
            sorted(set(jira_ids)), sorted(set(git_by_name.get(name, [])))
        ):
            pairs.append(
                make_pair(
                    a,
                    b,
                    kind="person",
                    block="person",
                    tier=1,
                    rule="jira_git_name",
                    score=1.0,
                    reason=f"jira name == git author name: {name}",
                )
            )

    # (b2) identical display *and* at least one REAL item both of them touched, and a
    # display that says more than one word. The synthetic layer is excluded because the
    # generator deliberately puts two different people of one name on one ADO item, so
    # "both touched it" there is a statement about the injected noise, not about identity.
    by_display: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        if name := norm_display(c.name):
            by_display[name].append(c.id)
    for name, ids in sorted(by_display.items()):
        for a, b in itertools.combinations(sorted(set(ids)), 2):
            shared = by_id[a].touched_real & by_id[b].touched_real
            if not shared or not auto_guard(by_id[a], by_id[b]):
                continue
            pairs.append(
                make_pair(
                    a,
                    b,
                    kind="person",
                    block="person",
                    tier=1,
                    rule="display_and_activity",
                    score=1.0,
                    reason=f"same display {name!r} and both touched {sorted(shared)[0]}",
                )
            )

    # (c) `alias_candidates` from `brain load`: Jira itself wrote both spellings of one
    # identity onto one issue — the changelog id and the field username.
    for row in alias_candidates:
        a, b = row.get("changelog_identity"), row.get("field_identity")
        if not a or not b or a not in by_id or b not in by_id:
            continue
        examples = row.get("examples") or []
        where = examples[0] if examples else f"{row.get('work_items', '?')} work items"
        pairs.append(
            make_pair(
                a,
                b,
                kind="person",
                block="person",
                tier=1,
                rule="alias_candidates",
                score=1.0,
                reason=f"jira wrote both spellings on {where}",
            )
        )

    # (d) a Confluence userKey that IS a Jira username.
    jira_keys = {
        norm_display(c.id.split(":", 1)[1]): c.id for c in candidates if source_of(c.id) == "jira"
    }
    for c in candidates:
        if source_of(c.id) != "confluence":
            continue
        target = jira_keys.get(norm_display(c.id.split(":", 1)[1]))
        if target:
            pairs.append(
                make_pair(
                    c.id,
                    target,
                    kind="person",
                    block="person",
                    tier=1,
                    rule="confluence_userkey",
                    score=1.0,
                    reason=f"confluence userKey is the jira username {c.id.split(':', 1)[1]!r}",
                )
            )

    # (e) the same username, byte for byte, typed into two different systems.
    # `danica.fine.10077` (ADO), `danica.fine@gmail.com` (git) and `danicafine` (Jira) are
    # one string once the synthetic namespace, the numeric id, the mail domain and the
    # separators come off — and nobody arrives at another person's username by accident.
    # Two identities of the *same* source are excluded: within one system a username is
    # already unique, so a collision there means the stemming was too aggressive.
    stems_by_id: dict[str, dict[str, set[str]]] = {}
    for c in candidates:
        for identity in c.identities or [c.id]:
            if stem := username_stem(identity):
                stems_by_id.setdefault(c.id, {}).setdefault(stem, set()).add(source_of(identity))
    by_stem: dict[str, list[str]] = defaultdict(list)
    for cid, found in stems_by_id.items():
        for stem in found:
            by_stem[stem].append(cid)
    for stem, ids in sorted(by_stem.items()):
        for a, b in itertools.combinations(sorted(set(ids)), 2):
            sources = stems_by_id[a][stem] | stems_by_id[b][stem]
            if len(sources) < 2:
                continue
            pairs.append(
                make_pair(
                    a,
                    b,
                    kind="person",
                    block="person",
                    tier=1,
                    rule="username_stem",
                    score=1.0,
                    reason=f"username {stem!r} typed identically in {'/'.join(sorted(sources))}",
                )
            )

    return dedupe(pairs)


def generic_stems_seen(candidates: Sequence[Candidate]) -> dict[str, int]:
    """Stems `username_stem` refused as generic words, and how often.

    In the report because a stoplist that quietly swallowed a real surname would otherwise
    be invisible: the rule it disables is the highest-precision one this tier has.
    """
    counts: dict[str, int] = {}
    for c in candidates:
        for identity in c.identities or [c.id]:
            if stem := rejected_stem(identity):
                counts[stem] = counts.get(stem, 0) + 1
    return dict(sorted(counts.items()))


def entity_tier1(candidates: Sequence[Candidate]) -> list[Pair]:
    """Brief 08 decision 2: `norm_name` inside one kind.

    `Entity.id` already *is* `kind|norm_name` (spec 2.4), so this rule finds duplicates
    only where two ids differ by something the key does not normalise. Reporting a zero
    here is the point: it says the load-time key did its job, not that nothing was checked.
    """
    by_id = _index(candidates)
    buckets: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        buckets[f"{c.block}|{norm_display(c.name)}"].append(c.id)
    return dedupe(
        _pairs_from_buckets(
            buckets, by_id, kind="entity", tier=1, rule="norm_name", reason="same normalised name"
        )
    )


#: Words whose presence or absence does not change which thing is named. Deliberately
#: grammatical: `all`, `max`, `avg`, `latest` and `range` are NOT here, because they are
#: exactly what tells `rebalance latency avg` from `rebalance latency max`.
FILLER_TOKENS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "by",
        "for",
        "in",
        "its",
        "new",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
    }
)


def kip_title_links(
    candidates: Sequence[Candidate], titles: dict[str, str]
) -> list[dict[str, Any]]:
    """Brief 08 decision 2: a Feature/Technology named exactly after a KIP links to the
    Document — `SAME_AS`, not a merge. Returns rows `write_document_links` can write.

    Matched on the title's *substantive words*, with the `KIP-N:` prefix and the articles
    dropped: the page is called "KIP-848: The Next Generation of the Consumer Rebalance
    Protocol" and the extractor names "next generation consumer rebalance protocol". Those
    are the same name; requiring the articles to line up would find almost nothing.

    Word *set*, not sequence, and every word has to be there — this is a naming identity,
    not a similarity, and it is the only entity rule allowed to fire without a reader.
    """
    index: dict[frozenset[str], str] = {}
    for title, key in titles.items():
        words = display_tokens(_KIP_PREFIX.sub("", title)) - FILLER_TOKENS
        if words:
            index.setdefault(words, key)
    rows: list[dict[str, Any]] = []
    for c in candidates:
        if c.block not in KIP_ALIAS_KINDS:
            continue
        for name in {c.name, *c.aliases}:
            key = index.get(display_tokens(name) - FILLER_TOKENS)
            if key:
                rows.append(
                    {
                        "src": c.id,
                        "dst": key,
                        "props": {
                            "tier": 1,
                            "rule": "kip_title_alias",
                            "score": 1.0,
                            "reason": f"{c.block} is named exactly after {key}",
                        },
                    }
                )
                break
    return sorted(rows, key=lambda r: (r["src"], r["dst"]))


def entity_auto_ok(a: Candidate, b: Candidate) -> bool:
    """May two entities be merged on similarity alone?

    Coordinator's decision (b) asks for a shared word or a shared parent document. Measured
    on this corpus that is not enough, and the failure is not subtle: `rebalance latency
    avg`, `rebalance latency max` and `rebalance latency total` share three words and one
    KIP, score above 0.92 against each other, and are three different metrics. A dry run
    chained eight of them into one node, and fourteen query APIs into another.

    So the shared context is necessary and the *names* decide: an automatic merge needs the
    two names to be the same words — same order or not, singular or plural, punctuation or
    not — give or take a grammatical filler. Every other pair, however high the cosine, is
    a question for a reader, which is what the grey band is for.
    """
    if not (display_tokens(a.name) & display_tokens(b.name) or a.parents & b.parents):
        return False
    ta, tb = display_tokens(a.name), display_tokens(b.name)
    if not ta or not tb:
        return False
    return not (ta ^ tb) - FILLER_TOKENS


def band(score: float) -> str:
    """`"auto"` (>= 0.92), `"grey"` (0.80-0.92), `"reject"` (< 0.80)."""
    if score >= AUTO_THRESHOLD:
        return "auto"
    if score >= ADJUDICATE_FLOOR:
        return "grey"
    return "reject"


def tier2_pairs(
    scored: Iterable[tuple[str, str, float]],
    by_id: dict[str, Candidate],
    *,
    text_note: str = "the embedded text",
    guard: bool = True,
    blocking: bool = True,
    floor: float = ADJUDICATE_FLOOR,
    auto_extra: Callable[[Candidate, Candidate], bool] | None = None,
) -> tuple[list[Pair], list[Pair], dict[str, int]]:
    """Split scored candidate pairs into (auto-merge, adjudicate) plus what was filtered.

    `text_note` says what was embedded, so a merge reason in the ledger still means
    something a year later — `--vector-evidence` changes what a cosine of 0.94 is about.

    `guard` demotes an auto pair whose names are initials to the grey band (it is not
    dropped: a reader may still merge it). `blocking` drops grey pairs whose names share
    nothing at all, which is the only filter here that removes a question rather than
    moving it.
    """
    auto: dict[tuple[str, str], Pair] = {}
    grey: dict[tuple[str, str], Pair] = {}
    filtered = {"demoted_by_name_guard": 0, "dropped_by_blocking": 0, "demoted_by_auto_extra": 0}
    for a, b, score in scored:
        if a == b or a not in by_id or b not in by_id:
            continue
        if by_id[a].block != by_id[b].block:
            continue
        if score < floor:
            continue
        where = "auto" if score >= AUTO_THRESHOLD else "grey"
        demoted = where == "auto" and guard and not auto_guard(by_id[a], by_id[b])
        unrelated = (
            where == "auto"
            and not demoted
            and auto_extra is not None
            and not auto_extra(by_id[a], by_id[b])
        )
        if demoted or unrelated:
            where = "grey"
            filtered["demoted_by_auto_extra" if unrelated else "demoted_by_name_guard"] += 1
        if where == "grey" and blocking and not name_blocked(by_id[a], by_id[b]):
            filtered["dropped_by_blocking"] += 1
            continue
        pair = make_pair(
            a,
            b,
            kind=by_id[a].kind,
            block=by_id[a].block,
            tier=2,
            rule="embedding_auto" if where == "auto" else "embedding_grey",
            score=round(score, 4),
            reason=(
                f"cosine {score:.4f} on {text_note}"
                + (" — names are initials, so not merged unasked" if demoted else "")
                + (" — no shared word and no shared parent" if unrelated else "")
            ),
        )
        target = auto if where == "auto" else grey
        prior = target.get(pair.key)
        if prior is None or pair.score > prior.score:
            target[pair.key] = pair
    for key in auto:
        grey.pop(key, None)
    return (
        sorted(auto.values(), key=lambda p: (-p.score, p.a, p.b)),
        sorted(grey.values(), key=lambda p: (-p.score, p.a, p.b)),
        filtered,
    )


def band_table(
    scored: Iterable[tuple[str, str, float]],
    by_id: dict[str, Candidate],
    *,
    floors: Sequence[float] = (0.80, 0.83, 0.85),
) -> list[dict[str, Any]]:
    """Grey-band size at each floor, with and without blocking — the adjudicator's bill.

    Reported, never applied: the floor stays what the spec says until a brief moves it.
    """
    rows: list[dict[str, Any]] = []
    scored = list(scored)
    for floor in floors:
        for blocking in (True, False):
            _auto, grey, filtered = tier2_pairs(scored, by_id, blocking=blocking, floor=floor)
            rows.append(
                {
                    "floor": floor,
                    "blocking": blocking,
                    "grey_pairs": len(grey),
                    "dropped_by_blocking": filtered["dropped_by_blocking"],
                }
            )
    return rows


def cap_band(
    grey: Sequence[Pair], by_id: dict[str, Candidate], *, limit: int
) -> tuple[list[Pair], float | None]:
    """Keep the `limit` most answerable pairs, ranked by score x a shared-token bonus.

    A band bigger than the agents can read is not a band, it is a backlog. Ranking rather
    than raising the floor keeps the *hard* pairs — a 0.84 pair whose names share two words
    outranks a 0.91 pair that shares none — and the cut-off is reported so the planner can
    see what was left out and at what rank, rather than discovering a silent truncation.
    """
    if limit <= 0 or len(grey) <= limit:
        return list(grey), None

    def rank(p: Pair) -> float:
        a, b = by_id[p.a], by_id[p.b]
        shared = len(display_tokens(a.name) & display_tokens(b.name))
        bonus = 1.0 + 0.10 * min(shared, 3) + (0.10 if a.parents & b.parents else 0.0)
        return p.score * bonus

    ordered = sorted(grey, key=lambda p: (-rank(p), p.a, p.b))
    return ordered[:limit], round(rank(ordered[limit - 1]), 4)


def dedupe(pairs: Iterable[Pair]) -> list[Pair]:
    """One pair per (a, b) — the earliest tier wins, then the strongest rule name."""
    best: dict[tuple[str, str], Pair] = {}
    for pair in pairs:
        prior = best.get(pair.key)
        if prior is None or (pair.tier, -pair.score) < (prior.tier, -prior.score):
            best[pair.key] = pair
    return sorted(best.values(), key=lambda p: (p.tier, p.rule, p.a, p.b))


def groups(pairs: Iterable[Pair]) -> list[list[str]]:
    """Connected components of the merge graph: union-find, sorted, size >= 2."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in pairs:
        ra, rb = find(pair.a), find(pair.b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    members: dict[str, list[str]] = defaultdict(list)
    for node in parent:
        members[find(node)].append(node)
    return sorted((sorted(v) for v in members.values() if len(v) > 1), key=lambda g: g[0])
