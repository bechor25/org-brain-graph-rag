"""Tier 2's vectors: embed what changed, leave the rest alone.

The hash is of the *text*, not of the node, so the expensive question ("has this
candidate's display or activity changed since we last embedded it?") is answered without
calling Ollama. A merged survivor's text changes — it inherited the other node's
evidence — so the merge clears its hash and the next tier-2 pass re-embeds exactly the
nodes a merge touched.

**Why `EVIDENCE_IN_VECTOR` is 0.** Brief 08 decision 3 asks for `display` plus up to five
touched-item titles, "not the name alone". Measured on this corpus, over the 570 gold
identity pairs and the 63 hard negatives (`data/reports/resolve.json`, tier2.text_choice):

    text                     true pairs p10/p50/p90   hard negatives p50
    display + 5 titles       0.45 / 0.63 / 0.75       0.53
    display alone            0.60 / 0.76 / 0.89       0.81

With the titles in the vector the *median true pair scores 0.63* — under the 0.80 floor at
which the tier even starts asking — because the two identities of one person live in
different systems and share no item titles at all (0 shared neighbours across all 420
synthetic identities, measured). Meanwhile the titles pull *different* people who work the
same backlog together: on the mini corpus "Jun Rao" and "Dana Lee" reach 0.94 and would
auto-merge. The context term measures shared work, and shared work is not shared identity.

So the vector is the display name, and the touched titles go where they do discriminate:
into `evidence[]` on the adjudication batch, in front of a reader that can tell "worked on
the same issue" from "is the same person". Set `--vector-evidence 5` to reproduce the
briefed behaviour; the report records which was used.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Any

from brain.embed.client import OllamaEmbedder
from brain.graph.context import GraphContext
from brain.resolve import graph as resolve_graph
from brain.resolve.models import Candidate

#: Touched-item titles to put *in the vector*. 0 = the display name alone; see above.
EVIDENCE_IN_VECTOR = 0
#: What brief 08 decision 3 asks for, kept so `--vector-evidence 5` is one flag away.
BRIEF_EVIDENCE_IN_VECTOR = 5
BATCH_SIZE = 64


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def texts_of(candidates: Sequence[Candidate], evidence: int = EVIDENCE_IN_VECTOR) -> dict[str, str]:
    return {c.id: c.embed_text(evidence) for c in candidates}


def stale(texts: dict[str, str], hashes: dict[str, str]) -> list[str]:
    """Ids whose stored vector is missing or was made from different text."""
    return sorted(i for i, t in texts.items() if hashes.get(i) != text_hash(t))


def ensure_embeddings(
    ctx: GraphContext,
    label: str,
    candidates: Sequence[Candidate],
    embedder: OllamaEmbedder,
    *,
    batch_size: int = BATCH_SIZE,
    evidence: int = EVIDENCE_IN_VECTOR,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Bring every candidate's vector up to date. Returns what it cost."""
    texts = texts_of(candidates, evidence)
    hashes = resolve_graph.embed_hashes(ctx, label)
    todo = stale(texts, hashes)
    written = 0
    for start in range(0, len(todo), batch_size):
        block = todo[start : start + batch_size]
        vectors = embedder.embed([texts[i] for i in block], batch_size=batch_size)
        written += resolve_graph.write_embeddings(
            ctx,
            label,
            [
                {"id": i, "embedding": v, "embed_hash": text_hash(texts[i])}
                for i, v in zip(block, vectors, strict=True)
            ],
        )
        echo(f"embed {label}: {written}/{len(todo)}")
    return {
        "candidates": len(texts),
        "stale": len(todo),
        "embedded": written,
        "already_current": len(texts) - len(todo),
        "evidence_in_vector": evidence,
        "text_choice": (
            "display alone"
            if evidence == 0
            else f"display + up to {evidence} touched titles (brief 08 decision 3)"
        ),
        "usage": embedder.usage(),
    }
