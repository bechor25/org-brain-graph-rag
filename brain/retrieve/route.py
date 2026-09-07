"""The deterministic pre-router (spec §4.2, layer 1).

It is a suggestion, never an order. The asking agent picks tools by their MCP
descriptions; `route()` exists so that (a) the fixed-strategy evaluation in Plan 3 has a
defensible "which strategy should this question have used", and (b) `brain ask` without
`--strategy` does something sensible. Being deterministic is the whole point: a router
that is itself a model cannot be a baseline for a model.

Rule order is not the order the spec lists them in, and the reason is a question:

    "What was the status of KAFKA-15123 on 2024-03-01?"

It names a key *and* a date. Keys-first would send it to S3, which cannot answer "on that
date"; the date is the more specific signal, so temporal is tested first. Every rule below
is ordered by specificity for the same reason, and each returns the phrase that fired so
the report can say *why* — a router whose reasons are unreadable is a router nobody trusts.
"""

from __future__ import annotations

import re
from typing import Any

from brain.retrieve.keys import find_keys

#: Explicit point-in-time or interval language. Deliberately narrow: "last execution
#: status" is about the latest run, not about a date, and must not become a temporal query.
TEMPORAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("iso-date", r"\b\d{4}-\d{2}-\d{2}\b"),
    ("over-time", r"\bover time\b|\bthrough time\b|\bhistory of\b|\btimeline\b"),
    ("as-of", r"\bas of\b|\bat the time\b|\bwas the status\b|\bstatus (?:of|on)\b.*\bon\b"),
    ("changed-in", r"\bwhat changed\b|\bwhat has changed\b"),
    ("between-versions", r"\bbetween\b[^?]*\b\d+\.\d+(?:\.\d+)?\b[^?]*\band\b"),
    ("he-over-time", r"לאורך זמן|לאורך הזמן|היסטוריה|ציר זמן"),
    ("he-date", r"בתאריך|נכון ל־?\d|מה היה הסטטוס"),
    ("he-changed", r"מה השתנה|אילו שינויים"),
    ("he-between", r"בין [^?]*\d+\.\d+[^?]*ל"),
)

#: Counting and superlatives — the questions a traversal answers badly and an aggregation
#: answers exactly. "who owns" is here because the spec's own example spells out what it
#: means: *most* assignments and commits.
AGGREGATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("how-many", r"\bhow many\b|\bhow much\b|\bnumber of\b"),
    ("count", r"\bcount\b|\btotal\b"),
    ("superlative", r"\bmost\b|\bfewest\b|\bhighest\b|\blargest\b|\btop \d+\b|\bbiggest\b"),
    ("owns", r"\bwho owns\b|\bwho maintains\b"),
    ("which-have", r"\bwhich \w+s? (?:have|has|contain|with)\b"),
    ("he-count", r"כמה |מספר ה"),
    ("he-superlative", r"הכי |ביותר"),
    ("he-owns", r"מי אחראי|מי הבעלים|בבעלות מי"),
)

#: Thematic/global language: the answer is a shape of the corpus, not a path in it.
THEME_PATTERNS: tuple[tuple[str, str], ...] = (
    ("themes", r"\bthemes?\b|\btopics?\b|\bpatterns?\b|\btrends?\b"),
    ("overview", r"\boverview\b|\bsummar(?:y|ise|ize)\b|\bin general\b|\blandscape\b"),
    ("he-themes", r"נושאים|מגמות|דפוסים"),
    ("he-overview", r"סקירה|תמונה כללית|באופן כללי"),
)

STRATEGY_FOR: dict[str, str] = {
    "temporal": "s6",
    "aggregation": "s4",
    "theme": "s5",
    "keys": "s3",
    "bare-key": "lookup",
    "default": "s2",
}


def _first_match(question: str, patterns: tuple[tuple[str, str], ...]) -> str | None:
    for name, pattern in patterns:
        if re.search(pattern, question, flags=re.IGNORECASE):
            return name
    return None


def _is_bare_key(question: str, keys) -> bool:
    """`"KAFKA-15123"` or `"lookup KIP-848"` — a key with nothing asked about it."""
    stripped = question
    for key in keys.all_keys():
        stripped = stripped.replace(key, " ")
    words = [w for w in re.split(r"[^\w]+", stripped, flags=re.UNICODE) if w]
    return len(words) <= 1


def route(question: str) -> dict[str, Any]:
    """`{strategy, reason, confidence}` — advisory, logged, never enforced.

    `confidence` is a flat per-rule constant, not a probability: it says how specific the
    firing signal was (an ISO date is unambiguous; "default" is a shrug), and Plan 3 gets
    to find out empirically whether the ordering was right.
    """
    q = (question or "").strip()
    keys = find_keys(q)

    hit = _first_match(q, TEMPORAL_PATTERNS)
    if hit:
        return {
            "strategy": "s6",
            "reason": f"temporal signal ({hit})",
            "confidence": 0.9,
            "rule": hit,
            "keys": list(keys.all_keys()),
        }
    hit = _first_match(q, AGGREGATION_PATTERNS)
    if hit:
        return {
            "strategy": "s4",
            "reason": f"aggregation signal ({hit})",
            "confidence": 0.8,
            "rule": hit,
            "keys": list(keys.all_keys()),
        }
    hit = _first_match(q, THEME_PATTERNS)
    if hit:
        return {
            "strategy": "s5",
            "reason": f"thematic signal ({hit})",
            "confidence": 0.8,
            "rule": hit,
            "keys": list(keys.all_keys()),
        }
    if keys.any_node_key:
        bare = _is_bare_key(q, keys)
        return {
            "strategy": "lookup" if bare else "s3",
            "reason": ("bare key" if bare else "question names a key")
            + f": {', '.join(keys.all_keys()[:3])}",
            "confidence": 0.95 if bare else 0.7,
            "rule": "bare-key" if bare else "keys",
            "keys": list(keys.all_keys()),
        }
    return {
        "strategy": "s2",
        "reason": "no deterministic signal — graph-enhanced vector is the default",
        "confidence": 0.4,
        "rule": "default",
        "keys": [],
    }
