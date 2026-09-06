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
from collections import defaultdict
from collections.abc import Iterable, Sequence

from brain.resolve.models import Candidate, Pair, make_pair
from brain.resolve.names import norm_display, source_of

#: Cosine at or above which tier 2 merges without asking (spec 3.6, brief decision 3).
AUTO_THRESHOLD = 0.92
#: Cosine below which tier 2 does not even ask.
ADJUDICATE_FLOOR = 0.80

#: Tier-1 rule names, in the order the report lists them.
PERSON_RULES: tuple[str, ...] = (
    "email",
    "jira_git_name",
    "display_and_activity",
    "alias_candidates",
    "confluence_userkey",
)
ENTITY_RULES: tuple[str, ...] = ("norm_name",)


def _index(candidates: Iterable[Candidate]) -> dict[str, Candidate]:
    return {c.id: c for c in candidates}


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

    # (b2) identical display *and* at least one item both of them touched.
    by_display: dict[str, list[str]] = defaultdict(list)
    for c in candidates:
        if name := norm_display(c.name):
            by_display[name].append(c.id)
    for name, ids in sorted(by_display.items()):
        for a, b in itertools.combinations(sorted(set(ids)), 2):
            shared = by_id[a].touched & by_id[b].touched
            if not shared:
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

    return dedupe(pairs)


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
) -> tuple[list[Pair], list[Pair]]:
    """Split scored candidate pairs into (auto-merge, adjudicate). Same block only.

    `text_note` says what was embedded, so a merge reason in the ledger still means
    something a year later — `--vector-evidence` changes what a cosine of 0.94 is about.
    """
    auto: dict[tuple[str, str], Pair] = {}
    grey: dict[tuple[str, str], Pair] = {}
    for a, b, score in scored:
        if a == b or a not in by_id or b not in by_id:
            continue
        if by_id[a].block != by_id[b].block:
            continue
        where = band(score)
        if where == "reject":
            continue
        pair = make_pair(
            a,
            b,
            kind=by_id[a].kind,
            block=by_id[a].block,
            tier=2,
            rule=f"embedding_{where}",
            score=round(score, 4),
            reason=f"cosine {score:.4f} on {text_note}",
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
    )


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
