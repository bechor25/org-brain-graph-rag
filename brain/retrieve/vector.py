"""The two index reads S1–S3 are built on, and the fusion that combines them.

**Vector syntax (plan decision 4).** Neo4j 2026.06.0 Community was asked directly, on both
`CYPHER 5` and `CYPHER 25`, for the declarative form:

    SEARCH VECTOR INDEX chunk_embedding FOR $v YIELD node, score

and answered `Neo.ClientError.Statement.SyntaxError: Invalid input 'SEARCH'` on both. So
this library uses `db.index.vector.queryNodes`, which is deprecated-but-supported and is
what `brain chunk` and `brain resolve` already call. The check is recorded in
`data/reports/retrieve.json` so the next reader does not have to repeat it.

**Fulltext.** A Lucene query is not a sentence. `KAFKA-15123` contains a `-`, which Lucene
reads as NOT; a question mark is a wildcard; an unbalanced quote is a parse error. Every
term is escaped and the query is OR-ed, which is what makes `search_chunks` survive being
handed a raw question in either language.

**RRF.** Reciprocal Rank Fusion, `1/(60 + rank)`, is the fusion `neo4j-graphrag`'s
`HybridRetriever` uses and the one the spec names. It combines *ranks*, not scores, which
is the whole point: a cosine similarity of 0.71 and a Lucene score of 4.3 are not
comparable numbers, but "third in each list" is a comparable fact.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from brain.retrieve.context import CHUNK_INDEX, ENTITY_INDEX, RetrieveContext
from brain.retrieve.keys import find_keys
from brain.retrieve.nodes import projection

#: The constant in `1/(k + rank)`. 60 is the value from the original RRF paper and the
#: default in `neo4j-graphrag`; it flattens the head of each list so a single list's
#: first place cannot outvote agreement between two lists.
RRF_K = 60

#: Ask the index for more than we need: orphaned chunks and synthetic filtering both drop
#: rows *after* the index has ranked them.
OVERFETCH = 4

_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/&|])')

#: Bare uppercase boolean operators are read by the *parser*, before the analyzer ever
#: lowercases them, so a question containing "AND" would rewrite the whole query. They are
#: stopwords in every analyzer this index uses, so dropping them costs nothing.
_LUCENE_OPERATORS = frozenset({"AND", "OR", "NOT", "TO"})

#: How much more a term that is a *key* is worth than an ordinary word. A question is
#: mostly stopwords ("why was the … chosen in"); `KIP-848` is the whole question. Without
#: the boost the nine common terms outvote the one that identifies the document.
KEY_BOOST = 8

#: Punctuation that surrounds a term without belonging to it.
_TRIM = ".,;:!?()[]{}<>\u201c\u201d\u2018\u2019\"'`\u2014\u2013\u00bb\u00ab"


def lucene_escape(text: str, boost_keys: bool = True) -> str:
    """Turn a natural-language question into a safe OR-query over the fulltext index.

    Every term is escaped (a `-` is Lucene's NOT, a `?` is a wildcard, a lone `"` is a
    parse error) and the terms are OR-ed. Anything `find_keys` recognises — `KAFKA-15123`,
    `KIP-848`, a sha — is boosted, which is the difference between "chunks about consumer
    groups" and "the chunks that name this KIP".
    """
    keys = list(find_keys(text or "").all_keys()) if boost_keys else []
    lowered = {k.casefold() for k in keys}
    terms: list[str] = []
    for raw in (text or "").split():
        # Trailing punctuation is not part of the term: `KIP-848?` escaped whole becomes a
        # literal question mark and matches nothing.
        token = raw.strip(_TRIM)
        if not token or token.casefold() in lowered or token in _LUCENE_OPERATORS:
            continue
        terms.append(_LUCENE_SPECIAL.sub(r"\\\1", token))
    # The keys are added as terms of their own rather than boosted in place: Hebrew glues
    # a prefix on ("ב-KIP-848"), so the key is often not a token at all.
    for key in keys:
        escaped_key = _LUCENE_SPECIAL.sub(r"\\\1", key)
        terms.append(f"({escaped_key})^{KEY_BOOST}")
    return " OR ".join(t for t in terms if t)


def search_chunk_vectors(
    ctx: RetrieveContext, vector: Sequence[float], k: int = 10
) -> list[dict[str, Any]]:
    """Top-`k` live chunks by cosine. Orphans are excluded: the text is no longer on any page.

    They stay *in* the index on purpose — `brain extract` cites them as evidence — so the
    filter is here, and the index is over-asked so the answer is still `k` long.
    """
    name = ctx.require_index(CHUNK_INDEX)
    cypher = (
        "CALL db.index.vector.queryNodes($index, $wide, $vector) YIELD node AS c, score\n"
        f"WHERE c:{ctx.label('Chunk')} AND coalesce(c.orphaned, false) = false"
        + ctx.synthetic_clause("c")
        + f"\nRETURN {projection('c', 'Chunk')} AS node, score\n"
        "ORDER BY score DESC LIMIT $k"
    )
    rows = ctx.read(cypher, index=name, k=k, wide=k * OVERFETCH, vector=list(vector))
    return [{"cypher": cypher, **r} for r in rows]


def search_entity_vectors(
    ctx: RetrieveContext, vector: Sequence[float], k: int = 10, kinds: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Top-`k` entities by cosine, optionally restricted to `Entity.kind`s."""
    name = ctx.require_index(ENTITY_INDEX)
    kind_filter = " AND e.kind IN $kinds" if kinds else ""
    cypher = (
        "CALL db.index.vector.queryNodes($index, $wide, $vector) YIELD node AS e, score\n"
        f"WHERE e:{ctx.label('Entity')}{kind_filter}"
        + ctx.synthetic_clause("e")
        + f"\nRETURN {projection('e', 'Entity')} AS node, score\n"
        "ORDER BY score DESC LIMIT $k"
    )
    rows = ctx.read(
        cypher,
        index=name,
        k=k,
        wide=k * OVERFETCH,
        vector=list(vector),
        kinds=list(kinds) if kinds else None,
    )
    return [{"cypher": cypher, **r} for r in rows]


def search_chunk_fulltext(ctx: RetrieveContext, query: str, k: int = 10) -> list[dict[str, Any]]:
    """Lucene over `Chunk.text`.

    Two ways to get nothing rather than an exception: a query that is all punctuation, and
    a database where the index does not exist and could not be created. Both leave S1 with
    its vector half, which is the degradation the caller can live with.
    """
    escaped = lucene_escape(query)
    if not escaped:
        return []
    name = ctx.ensure_fulltext_index()
    if not name:
        return []
    cypher = (
        "CALL db.index.fulltext.queryNodes($index, $q, {limit: $wide}) YIELD node AS c, score\n"
        f"WHERE c:{ctx.label('Chunk')} AND coalesce(c.orphaned, false) = false"
        + ctx.synthetic_clause("c")
        + f"\nRETURN {projection('c', 'Chunk')} AS node, score\n"
        "ORDER BY score DESC LIMIT $k"
    )
    rows = ctx.read(cypher, index=name, q=escaped, k=k, wide=k * OVERFETCH)
    return [{"cypher": cypher, **r} for r in rows]


def rrf(
    ranked_lists: Sequence[Sequence[str]], k: int = RRF_K, weights: Sequence[float] | None = None
) -> dict[str, float]:
    """`{id: fused score}` from several ranked id lists. Rank is 1-based; ties keep list order."""
    weights = list(weights or [1.0] * len(ranked_lists))
    scores: dict[str, float] = {}
    for ranked, weight in zip(ranked_lists, weights, strict=False):
        for rank, key in enumerate(ranked, start=1):
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
    return scores
