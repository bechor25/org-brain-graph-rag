"""The 4k-token ceiling every strategy answers under (spec §4.4, plan decision 2).

Comparing six strategies is only fair if they all get the same amount of the agent's
context. Without a ceiling, S2 wins every evaluation by returning more text than S1, and
the number would measure verbosity rather than retrieval.

Three rules, in this order:

0. **No single value is bigger than a snippet.** Strategies clip what they put in `snippet`,
   but `props` holds whatever a projection returned, and `RETURN d.body_md` once produced a
   single item of 66,289 tokens that the packer reported as `truncated=false` — a ceiling
   that is not enforced on the widest field is not a ceiling. So every string anywhere in an
   item is clipped here too, after the strategy and before the cost is counted.
1. **At least one item of every kind survives.** A traceability answer that drops its only
   `Change` because five chunks outscored it is not a shorter answer, it is a wrong one.
2. **Everything else goes by score**, highest first, until the budget is spent.

`truncated` answers one question — *is this the whole answer?* — so it is `True` when items
were dropped, when a value was clipped, **and** when the protected set alone spends more
than the budget. The last case is the one that was wrong: nothing was dropped, so nothing
was reported, while the agent was handed four times its context ceiling.

`3.49` chars per token is not a guess: `brain chunk --measure` calibrated `len(text)/4`
against `bge-m3`'s own `prompt_eval_count` over the whole corpus and got 3.49. It is the
tokenizer of the embedding model, not of the asking agent, so it is an estimate — which is
why the budget has slack in it and `truncated` is reported rather than assumed.
"""

from __future__ import annotations

import json
from typing import Any

from brain.retrieve.types import Item

#: Measured over the real corpus in `brain chunk --measure` (progress.md, step 06).
CHARS_PER_TOKEN = 3.49
#: Spec §4.4: "~4k tokens per answer".
BUDGET_TOKENS = 4000
#: Longest snippet an item may carry before packing even starts. Roughly 170 tokens: long
#: enough to be a quotable piece of evidence, short enough that ten of them fit.
SNIPPET_CHARS = 600


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN + 0.999) if text else 0


def clip(text: str | None, limit: int = SNIPPET_CHARS) -> str:
    """One place decides what a snippet looks like, so every strategy's cost is comparable."""
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def item_tokens(item: Item) -> int:
    """What this item costs the asking agent: its JSON, which is what the tool returns."""
    return estimate_tokens(json.dumps(item.model_dump(), ensure_ascii=False, default=str))


def _clamp(value: Any, limit: int) -> tuple[Any, bool]:
    """Clip every string inside `value`, however deep. Returns (value, anything_clipped)."""
    if isinstance(value, str):
        return (clip(value, limit), True) if len(value) > limit else (value, False)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        cut = False
        for key, item in value.items():
            out[key], hit = _clamp(item, limit)
            cut = cut or hit
        return out, cut
    if isinstance(value, (list, tuple)):
        items, cut = [], False
        for entry in value:
            clamped, hit = _clamp(entry, limit)
            items.append(clamped)
            cut = cut or hit
        return items, cut
    return value, False


def clamp(item: Item, limit: int = SNIPPET_CHARS) -> tuple[Item, bool]:
    """A copy of this item with no string longer than `limit`, and whether that cost text.

    A copy, not an edit: the caller may still be holding the row it built the item from, and
    a packer that silently rewrites its input makes "what did the strategy return" an
    unanswerable question in the evaluation.
    """
    title, cut_title = _clamp(item.title, limit)
    snippet, cut_snippet = _clamp(item.snippet, limit)
    props, cut_props = _clamp(item.props, limit)
    provenance, cut_prov = [], False
    for entry in item.provenance:
        quote, hit = _clamp(entry.quote, limit)
        provenance.append(entry.model_copy(update={"quote": quote}) if hit else entry)
        cut_prov = cut_prov or hit
    cut = cut_title or cut_snippet or cut_props or cut_prov
    if not cut:
        return item, False
    return (
        item.model_copy(
            update={
                "title": title,
                "snippet": snippet,
                "props": props,
                "provenance": provenance,
            }
        ),
        True,
    )


def pack(items: list[Item], budget_tokens: int = BUDGET_TOKENS) -> tuple[list[Item], bool]:
    """Trim to the budget by score, keeping the best item of every kind. Returns (items, truncated).

    The protected set can itself exceed the budget — nine kinds of large items — and when it
    does they are kept anyway and `truncated` is `True`. Dropping a kind to respect a ceiling
    would trade a measurable answer for an unmeasurable one; reporting `truncated=false`
    while overspending it would trade a measurable answer for a false one.
    """
    if not items:
        return [], False
    clamped: list[Item] = []
    clipped = False
    for item in items:
        trimmed, cut = clamp(item)
        clamped.append(trimmed)
        clipped = clipped or cut
    items = clamped
    ranked = sorted(enumerate(items), key=lambda p: (-p[1].score, p[0]))
    protected: dict[str, int] = {}
    for index, item in ranked:
        protected.setdefault(item.kind, index)
    keep_first = set(protected.values())

    costs = {index: item_tokens(item) for index, item in ranked}
    spent = 0
    kept: set[int] = set()
    for index, _item in ranked:  # protected first, in score order
        if index in keep_first:
            kept.add(index)
            spent += costs[index]
    for index, _item in ranked:
        if index in kept:
            continue
        if spent + costs[index] > budget_tokens:
            continue
        kept.add(index)
        spent += costs[index]

    out = [item for index, item in ranked if index in kept]
    return out, len(out) < len(items) or spent > budget_tokens or clipped


def total_tokens(items: list[Item]) -> int:
    return sum(item_tokens(i) for i in items)
