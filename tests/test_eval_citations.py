"""What counts as a citation, and what a sentence without one looks like.

The gate stands on this regex. If it misses `[KIP-848]` the answer looks uncited; if it
matches `[:RESOLVES]` inside a Cypher snippet the answer looks like it cited a relationship
type. Both failures are silent in the report and only visible here, so every form the
analyst is told to write — and several it might write by accident — has a case.
"""

from __future__ import annotations

from brain.eval.citations import find_citations, sentence_stats


def kinds(text: str) -> list[tuple[str, str]]:
    return [(c.kind, c.value) for c in find_citations(text)]


# ------------------------------------------------------------------------ the six forms


def test_every_form_the_analyst_is_told_to_write_is_found() -> None:
    text = (
        "The client work is KAFKA-100 [KAFKA-100]. The design is [KIP-5]. "
        "Evidence [chunk:ab12cd34ef56] and the fix [a1b2c3d4]. "
        "Owner [person:jira:dlee], theme [community:L1-7]."
    )
    assert kinds(text) == [
        ("workitem", "KAFKA-100"),
        ("document", "KIP-5"),
        ("chunk", "ab12cd34ef56"),
        ("commit", "a1b2c3d4"),
        ("person", "jira:dlee"),
        ("community", "L1-7"),
    ]


def test_a_bare_key_outside_brackets_is_not_a_citation() -> None:
    """KAFKA-100 in prose is the question's subject; the bracket is the claim's receipt."""
    assert kinds("KAFKA-100 is about clients and KIP-5.") == []


def test_one_bracket_may_carry_several_citations() -> None:
    assert kinds("Two commits landed it [a1b2c3d4, c9d0e1f2; KAFKA-100].") == [
        ("commit", "a1b2c3d4"),
        ("commit", "c9d0e1f2"),
        ("workitem", "KAFKA-100"),
    ]


def test_a_truncated_chunk_id_keeps_the_prefix_and_drops_the_ellipsis() -> None:
    """`[chunk:ab12…]` is what the agent prompt shows, in both unicode and ascii spelling."""
    assert kinds("a [chunk:ab12cd34…] b [chunk:ef56ab78...]") == [
        ("chunk", "ab12cd34"),
        ("chunk", "ef56ab78"),
    ]


def test_a_chunk_prefix_under_eight_hex_characters_is_a_citation_that_cannot_be_checked() -> None:
    """It is still a citation — refusing to see it would hide it from the invalid column."""
    (citation,) = find_citations("see [chunk:ab12]")
    assert citation.kind == "chunk"
    assert citation.problem and "8" in citation.problem


def test_a_chunk_id_is_lowercased_because_sha1_hex_in_the_graph_is() -> None:
    assert kinds("[chunk:AB12CD34]") == [("chunk", "ab12cd34")]


# ---------------------------------------------------------------------------- near misses


def test_a_markdown_link_is_not_a_citation() -> None:
    assert kinds("see [KAFKA-100](https://issues.apache.org/jira/browse/KAFKA-100)") == []


def test_a_fenced_code_block_is_not_scanned() -> None:
    text = "Ran this:\n```cypher\nMATCH (c)-[:RESOLVES]->(w {key: 'KAFKA-100'})\n```\nSo [KIP-5]."
    assert kinds(text) == [("document", "KIP-5")]


def test_a_relationship_type_or_a_footnote_marker_is_not_a_citation() -> None:
    assert kinds("the path (c)-[:TESTS]->(w) and note [1] and [see above] and [..3]") == []


def test_the_excluded_pseudo_keys_stay_excluded() -> None:
    """`brain canon` learned that UTF-8 matches a project-key pattern. It is not a key."""
    assert kinds("encoded as [UTF-8] with [SHA-256]") == []


def test_a_sha_needs_seven_hex_characters_and_at_most_forty() -> None:
    assert kinds("[a1b2c3]") == []
    assert kinds("[a1b2c3d]") == [("commit", "a1b2c3d")]
    assert kinds("[" + "a" * 41 + "]") == []


def test_kip_is_a_document_even_though_it_matches_the_work_item_pattern() -> None:
    assert kinds("[KIP-848]") == [("document", "KIP-848")]


def test_hebrew_text_around_a_citation_does_not_swallow_it() -> None:
    """`re` treats Hebrew letters as `\\w`, which is how a greedy key pattern goes wrong."""
    assert kinds("הטסטים מכוסים על ידי [KAFKA-100] ולפי [chunk:ab12cd34ef56].") == [
        ("workitem", "KAFKA-100"),
        ("chunk", "ab12cd34ef56"),
    ]


def test_the_same_citation_twice_is_two_occurrences_of_one_citation() -> None:
    found = find_citations("first [KAFKA-100] and again [KAFKA-100]")
    assert len(found) == 2
    assert len({c.id for c in found}) == 1


# ------------------------------------------------------------------------------ sentences


def test_a_sentence_with_a_citation_and_one_without_are_counted_apart() -> None:
    body = (
        "Two tests cover the issue [KAFKA-100].\n"
        "They were last run green.\n"
        "The design is in [KIP-5]."
    )
    stats = sentence_stats(body)
    assert (stats["total"], stats["with_citation"], stats["without_citation"]) == (3, 2, 1)


def test_a_version_number_does_not_end_a_sentence() -> None:
    stats = sentence_stats("The change landed in release 3.7.0 for the clients module [KIP-5].")
    assert stats["total"] == 1 and stats["with_citation"] == 1


def test_headings_table_rows_and_bare_citations_are_not_sentences() -> None:
    body = "## Answer\n\n| key | title |\n|---|---|\n| KAFKA-100 | x |\n\n[KIP-5]\n"
    assert sentence_stats(body)["total"] == 0


def test_a_bullet_is_a_sentence_because_that_is_where_the_claims_are() -> None:
    body = "- Two tests cover the issue [KAFKA-100]\n- Neither has ever been executed\n"
    stats = sentence_stats(body)
    assert (stats["total"], stats["with_citation"]) == (2, 1)


def test_hebrew_sentences_are_counted_the_same_way() -> None:
    body = "שני טסטים מכסים את הנושא [KAFKA-100]. הם מעולם לא הורצו.\n"
    stats = sentence_stats(body)
    assert (stats["total"], stats["with_citation"], stats["without_citation"]) == (2, 1, 1)


def test_an_empty_body_has_no_sentences_and_no_division_by_zero() -> None:
    assert sentence_stats("")["pct_with_citation"] == 0.0
