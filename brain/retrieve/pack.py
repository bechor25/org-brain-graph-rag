"""The 4k-token ceiling every strategy answers under (spec §4.4, plan decision 2).

Comparing six strategies is only fair if they all get the same amount of the agent's
context. Without a ceiling, S2 wins every evaluation by returning more text than S1, and
the number would measure verbosity rather than retrieval.

Two rules, in this order:

1. **At least one item of every kind survives.** A traceability answer that drops its only
   `Change` because five chunks outscored it is not a shorter answer, it is a wrong one.
2. **Everything else goes by score**, highest first, until the budget is spent.

`3.49` chars per token is not a guess: `brain chunk --measure` calibrated `len(text)/4`
against `bge-m3`'s own `prompt_eval_count` over the whole corpus and got 3.49. It is the
tokenizer of the embedding model, not of the asking agent, so it is an estimate — which is
why the budget has slack in it and `truncated` is reported rather than assumed.
"""

from __future__ import annotations

import json

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


def pack(items: list[Item], budget_tokens: int = BUDGET_TOKENS) -> tuple[list[Item], bool]:
    """Trim to the budget by score, keeping the best item of every kind. Returns (items, truncated).

    The protected set can itself exceed the budget — twelve kinds of large items — and when
    it does they are kept anyway and `truncated` is `True`. Dropping a kind to respect a
    ceiling would trade a measurable answer for an unmeasurable one.
    """
    if not items:
        return [], False
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
    return out, len(out) < len(items)


def total_tokens(items: list[Item]) -> int:
    return sum(item_tokens(i) for i in items)
