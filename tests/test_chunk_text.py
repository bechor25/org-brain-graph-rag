"""Block splitting, the token estimate, the language heuristic and chunk identity."""

from __future__ import annotations

from brain.chunk.text import (
    chunk_id,
    detect_lang,
    sha1,
    split_blocks,
    tail_overlap,
    token_est,
)


def kinds(text: str) -> list[str]:
    return [b.kind for b in split_blocks(text)]


def test_headings_paragraphs_and_blank_lines_become_separate_blocks():
    blocks = split_blocks("# Title\n\nfirst para\nstill first\n\nsecond para\n")
    assert [(b.kind, b.level) for b in blocks] == [("heading", 1), ("text", 0), ("text", 0)]
    assert blocks[1].text == "first para\nstill first"


def test_a_fenced_code_block_is_one_block_including_its_fences():
    text = "before\n\n```python\nx = 1\n\ny = 2\n```\n\nafter"
    blocks = split_blocks(text)
    assert kinds(text) == ["text", "code", "text"]
    code = blocks[1]
    assert code.text.startswith("```python")
    assert code.text.endswith("```")
    # The blank line inside the fence did not split it.
    assert "y = 2" in code.text


def test_a_heading_inside_a_fence_is_not_a_heading():
    text = "```\n# not a heading\n```\n"
    assert kinds(text) == ["code"]


def test_a_tilde_fence_is_not_closed_by_a_backtick_fence():
    text = "~~~\n```\nstill inside\n~~~\nafter\n"
    blocks = split_blocks(text)
    assert blocks[0].kind == "code"
    assert "still inside" in blocks[0].text
    assert blocks[1].text == "after"


def test_an_unterminated_fence_swallows_the_rest_of_the_document():
    blocks = split_blocks("intro\n\n```\ncode\nmore code\n")
    assert [b.kind for b in blocks] == ["text", "code"]
    assert blocks[1].text.endswith("more code")


def test_consecutive_pipe_lines_are_one_table_block():
    text = "para\n\n| a | b |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |\n\ntail"
    blocks = split_blocks(text)
    assert [b.kind for b in blocks] == ["text", "table", "text"]
    assert blocks[1].text.count("\n") == 3


def test_token_est_is_chars_over_four_rounded_up():
    assert token_est("") == 0
    assert token_est("abc") == 1
    assert token_est("a" * 800) == 200


def test_language_heuristic_reads_the_script_census_not_the_first_letter():
    assert detect_lang("consumer group rebalance protocol") == "en"
    assert detect_lang("פרוטוקול איזון מחדש של הצרכן") == "he"
    # A Hebrew sentence that names an English product is still Hebrew.
    assert detect_lang("הצוות סגר את KIP-848 בגרסה 4.0 אחרי דיון ארוך על הפרוטוקול") == "he"
    assert detect_lang("1234 5678 ---") == "other"


def test_chunk_id_is_deterministic_and_separates_kinds_and_positions():
    a = chunk_id("KAFKA-1", "comment", 0, "same text")
    assert a == chunk_id("KAFKA-1", "comment", 0, "same text")
    assert a != chunk_id("KAFKA-1", "description", 0, "same text")
    assert a != chunk_id("KAFKA-1", "comment", 1, "same text")
    assert a != chunk_id("KAFKA-2", "comment", 0, "same text")
    assert a != chunk_id("KAFKA-1", "comment", 0, "other text")
    assert a == sha1("KAFKA-1|comment|0|same text")


def test_tail_overlap_snaps_to_a_word_boundary():
    text = "alpha beta gamma delta epsilon zeta eta theta"
    tail = tail_overlap(text, 2)  # 8 characters
    assert tail and text.endswith(tail)
    assert " " not in tail[:1]
    assert tail.split()[0] in text.split()


def test_tail_overlap_refuses_to_carry_half_a_fence_or_a_table():
    assert tail_overlap("text\n\n```\ncode here\n", 50) == ""
    assert tail_overlap("| a | b |\n| 1 | 2 |", 50) == ""
    assert tail_overlap("", 50) == ""
    assert tail_overlap("some plain tail", 0) == ""
