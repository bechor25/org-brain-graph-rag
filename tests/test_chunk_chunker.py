"""The chunker against a golden Markdown page and against each record type.

`tests/fixtures/chunk/sample_kip.md` is shaped like a real KIP: `#`/`##`/`###` headings,
a 1,400-token Java fence and a 25-row table. `expected_sample_kip.jsonl` is the chunking
it must keep producing — every property, including the text, so a change to the packer
shows up as a diff and not as a number that moved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from brain.canon.models import Change, Comment, Document, WorkItem
from brain.chunk.chunker import (
    BODY_MAX,
    HARD_MAX,
    MIN_CHARS,
    OVERLAP,
    OVERSIZE_ATOMIC,
    OVERSIZE_SINGLE_UNIT,
    TARGET_MAX,
    Chunk,
    ChunkStats,
    chunk_comments,
    chunk_commit,
    chunk_document,
    chunk_workitem_description,
)
from brain.chunk.text import fence_parity, split_blocks, tail_overlap

FIXTURES = Path("tests/fixtures/chunk")


@pytest.fixture(scope="module")
def sample_md() -> str:
    return (FIXTURES / "sample_kip.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def expected() -> list[dict]:
    lines = (FIXTURES / "expected_sample_kip.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def kip(body: str, key: str = "KIP-848") -> Document:
    return Document(
        id=f"confluence:{key}", key=key, source="confluence", kind="KIP", title=key, body_md=body
    )


def issue(**kw) -> WorkItem:
    base = {
        "id": "jira:KAFKA-1",
        "key": "KAFKA-1",
        "source": "jira",
        "type": "Bug",
        "title": "t",
        "status": "Open",
        "created": datetime(2024, 3, 1, tzinfo=UTC),
    }
    return WorkItem(**{**base, **kw})


def test_golden_document_chunking(sample_md, expected):
    stats = ChunkStats()
    got = [c.props() for c in chunk_document(kip(sample_md), stats)]
    for row in got:
        row.pop("author")
        row.pop("at")
    assert got == expected


def test_no_chunk_cuts_a_code_fence_or_a_table(sample_md):
    chunks = list(chunk_document(kip(sample_md), ChunkStats()))
    for c in chunks:
        assert c.text.count("```") % 2 == 0, f"chunk {c.position} carries half a fence"
    # The whole 25-row table lands in exactly one chunk, header and separator included.
    with_table = [c for c in chunks if "| field0 |" in c.text]
    assert len(with_table) == 1
    assert "| field24 |" in with_table[0].text


def test_targets_are_met_except_where_an_atomic_block_forbids_it(sample_md):
    stats = ChunkStats()
    chunks = list(chunk_document(kip(sample_md), stats))
    oversize = [c for c in chunks if c.token_est > TARGET_MAX]
    assert len(oversize) == stats.oversize_chunks == 1
    assert "```java" in oversize[0].text  # the only reason it is oversize


def test_consecutive_chunks_overlap_by_about_fifty_tokens(sample_md):
    chunks = list(chunk_document(kip(sample_md), ChunkStats()))
    assert len(chunks) > 2
    carried = 0
    for previous, current in zip(chunks, chunks[1:], strict=False):
        tail = tail_overlap(previous.text, OVERLAP)
        if not tail:  # the previous chunk ended inside a fence or a table
            continue
        assert current.text.startswith(tail), f"chunk {current.position} lost its overlap"
        assert OVERLAP * 2 <= len(tail) <= OVERLAP * 4
        carried += 1
    assert carried >= len(chunks) - 3


def test_ids_are_stable_across_runs_and_unique_within_a_document(sample_md):
    first = [c.id for c in chunk_document(kip(sample_md), ChunkStats())]
    second = [c.id for c in chunk_document(kip(sample_md), ChunkStats())]
    assert first == second
    assert len(set(first)) == len(first)


def test_a_reworded_page_produces_new_ids_only_for_the_chunks_that_changed(sample_md):
    before = list(chunk_document(kip(sample_md), ChunkStats()))
    after = list(
        chunk_document(kip(sample_md + "\n\n# Rejected Alternatives\n\nNone."), ChunkStats())
    )
    assert [c.id for c in before[:-1]] == [c.id for c in after[:-1]]
    assert before[-1].id != after[-1].id


def test_a_short_description_is_one_chunk():
    chunks = list(
        chunk_workitem_description(
            issue(description="The consumer hangs on rebalance."), ChunkStats()
        )
    )
    assert len(chunks) == 1
    assert chunks[0].kind == "description"
    assert chunks[0].position == 0
    assert chunks[0].parent_kind == "WorkItem"
    assert chunks[0].heading is None


def test_a_long_description_splits_by_paragraph_and_stays_under_the_target():
    paragraph = ("rebalance protocol coordinator " * 30).strip()
    text = "\n\n".join(paragraph for _ in range(12))
    stats = ChunkStats()
    chunks = list(chunk_workitem_description(issue(description=text), stats))
    assert len(chunks) > 1
    assert all(c.token_est <= TARGET_MAX for c in chunks)
    assert [c.position for c in chunks] == list(range(len(chunks)))


def test_a_heading_inside_a_description_is_not_a_chunk_boundary():
    text = "# Steps to reproduce\n\nStart a consumer.\n\n# Expected\n\nIt joins the group."
    chunks = list(chunk_workitem_description(issue(description=text), ChunkStats()))
    assert len(chunks) == 1


def test_a_description_shorter_than_the_floor_is_rejected_and_counted():
    stats = ChunkStats()
    assert list(chunk_workitem_description(issue(description="lgtm"), stats)) == []
    assert stats.rejected_short == 1
    assert len("lgtm") < MIN_CHARS


def test_every_comment_is_exactly_one_chunk_keeping_author_and_time():
    at = datetime(2024, 5, 2, 9, 30, tzinfo=UTC)
    long_body = "rebalance " * 500  # ~1,250 estimated tokens, still one chunk
    item = issue(
        comments=[
            Comment(author="dlee", at=at, body="This reproduces on 3.7 with static membership."),
            Comment(author="mrivera", at=None, body=long_body),
            Comment(author="x", at=at, body="+1"),
        ]
    )
    stats = ChunkStats()
    chunks = list(chunk_comments(item, stats))
    assert [c.kind for c in chunks] == ["comment", "comment"]
    assert chunks[0].author == "dlee" and chunks[0].at == at
    assert chunks[0].position == 0 and chunks[1].position == 1
    assert chunks[1].token_est > TARGET_MAX  # never split
    assert stats.rejected_short == 1  # "+1"


def test_comment_position_is_the_index_on_the_item_not_the_chunk_number():
    short, real = "x", "a real comment, long enough to survive the floor"
    item = issue(comments=[Comment(author="a", body=short), Comment(author="b", body=real)])
    chunks = list(chunk_comments(item, ChunkStats()))
    assert [c.position for c in chunks] == [1]


def test_a_commit_message_is_one_chunk_with_its_author_and_time():
    at = datetime(2024, 6, 1, tzinfo=UTC)
    change = Change(
        id="a" * 40,
        kind="commit",
        message="KAFKA-14048: implement the consumer group heartbeat (#14001)\n\nDetails follow.",
        author_email="dlee@example.org",
        at=at,
    )
    stats = ChunkStats()
    chunks = list(chunk_commit(change, stats))
    assert len(chunks) == 1
    assert chunks[0].kind == "message"
    assert chunks[0].parent_kind == "Commit"
    assert chunks[0].parent_key == "a" * 40
    assert chunks[0].author == "dlee@example.org" and chunks[0].at == at


def test_an_empty_body_produces_nothing_and_is_counted():
    stats = ChunkStats()
    assert list(chunk_document(kip("   \n\n  "), stats)) == []
    assert list(chunk_workitem_description(issue(description="  "), stats)) == []
    assert stats.by_kind == {}


def test_props_carry_exactly_the_briefs_property_set():
    chunk = next(iter(chunk_workitem_description(issue(description="a" * 200), ChunkStats())))
    assert set(chunk.props()) == {
        "id",
        "parent_key",
        "parent_kind",
        "kind",
        "position",
        "text",
        "char_len",
        "token_est",
        "lang",
        "heading",
        "author",
        "at",
        "hash",
        "orphaned",
    }
    assert chunk.props()["char_len"] == 200
    assert chunk.props()["token_est"] == 50
    assert chunk.props()["orphaned"] is False


# ------------------------------------------------ the Confluence list-item dialect


@pytest.fixture(scope="module")
def indented_md() -> str:
    return (FIXTURES / "indented_fence.md").read_text(encoding="utf-8")


def test_a_fence_indented_inside_a_list_item_is_still_one_block(indented_md):
    """`markdownify` indents a list item's fence by 4-8 spaces; CommonMark would call that
    an indented code block and let the packer cut through it. Measured on the real corpus,
    reading it the CommonMark way split twelve KIP code blocks in half."""
    blocks = split_blocks(indented_md)
    code = [b for b in blocks if b.kind == "code"]
    assert len(code) == 2
    assert "group.consumer.heartbeat.interval.ms=5000" in code[0].text  # blank line inside
    assert code[0].text.rstrip().endswith("```")
    assert "GROUP_REMOTE_ASSIGNOR_CONFIG" in code[1].text
    tables = [b for b in blocks if b.kind == "table"]
    assert len(tables) == 1 and "TargetEpoch" in tables[0].text


def test_every_chunk_of_the_indented_page_closes_its_fences(indented_md):
    stats = ChunkStats()
    chunks = list(chunk_document(kip(indented_md, key="KIP-999"), stats))
    assert chunks
    assert stats.fence_parity_violations == 0
    for c in chunks:
        assert fence_parity(c.text), c.text


def test_the_fence_counter_notices_a_chunk_that_lost_half_a_fence():
    """The number the report must show as 0. It is counted on every chunk, so a packer
    change that starts cutting fences shows up as a failed check, not as bad retrieval."""
    stats = ChunkStats()
    half = Chunk(
        parent_key="KIP-1",
        parent_kind="Document",
        kind="section",
        position=0,
        text="```java\npublic void reconcile() {\n    // the closing fence is in the next chunk",
    )
    stats.count(half)
    assert stats.fence_parity_violations == 1

    stats = ChunkStats()
    stats.count(
        Chunk(
            parent_key="KIP-1",
            parent_kind="Document",
            kind="section",
            position=0,
            text="```java\npublic void reconcile() {}\n```",
        )
    )
    assert stats.fence_parity_violations == 0


# --------------------------------------------------------------- the overlap budget


def test_the_overlap_is_charged_to_the_budget_not_added_on_top(sample_md):
    """The overlap used to be prefixed after the <=800 decision, so section p95 sat at 850."""
    chunks = list(chunk_document(kip(sample_md), ChunkStats()))
    for c in chunks:
        if "```" in c.text or "|" in c.text:
            continue  # an atomic block is allowed over the target
        assert c.token_est <= TARGET_MAX, (c.position, c.token_est)


def test_a_prose_block_is_cut_to_leave_room_for_the_overlap():
    paragraph = ("rebalance protocol coordinator " * 30).strip()
    text = "\n\n".join(paragraph for _ in range(12))
    chunks = list(chunk_workitem_description(issue(description=text), ChunkStats()))
    assert len(chunks) > 1
    assert all(c.token_est <= TARGET_MAX for c in chunks)
    assert BODY_MAX == TARGET_MAX - OVERLAP


def test_oversize_chunks_are_counted_by_the_reason_they_are_oversize(sample_md):
    stats = ChunkStats()
    list(chunk_document(kip(sample_md), stats))
    assert stats.oversize_by_cause == {OVERSIZE_ATOMIC: stats.oversize_chunks}
    assert "other" not in stats.oversize_by_cause


def test_a_long_comment_is_oversize_as_a_single_unit_not_as_a_packer_failure():
    stats = ChunkStats()
    item = issue(comments=[Comment(author="a", body="rebalance " * 500)])
    list(chunk_comments(item, stats))
    assert stats.oversize_by_cause == {OVERSIZE_SINGLE_UNIT: 1}


def test_a_paragraph_with_no_line_break_is_wrapped_at_a_word_boundary():
    """One Jira description in this corpus is 23,000 characters on a single line. Left
    whole it is a 5,800-token chunk in an index whose others are 650."""
    one_line = " ".join(f"word{i}" for i in range(2000))
    stats = ChunkStats()
    chunks = list(chunk_workitem_description(issue(description=one_line), stats))
    assert len(chunks) > 1
    assert stats.wrapped_long_lines >= 1
    assert all(c.token_est <= TARGET_MAX for c in chunks)
    # Nothing was cut through the middle of a word.
    for c in chunks:
        for word in c.text.split():
            assert word.startswith("word") and word[4:].isdigit(), word


def test_a_fence_too_big_for_the_model_is_cut_and_not_blamed_on_the_packer():
    """`HARD_MAX` is the one cut allowed to break a fence: bge-m3 stops reading at 8,192
    tokens, so a 29,000-token log dump inside a fence has to be split somewhere."""
    dump = "\n".join(f"    at kafka.server.Thread.run(Thread.scala:{i})" for i in range(9000))
    stats = ChunkStats()
    chunks = list(chunk_document(kip(f"# Logs\n\n```\n{dump}\n```\n", key="KIP-2"), stats))
    assert len(chunks) > 1
    assert stats.hard_split_blocks == 1
    assert all(c.token_est <= HARD_MAX + TARGET_MAX for c in chunks)
    # The fence is broken — by design — and the counter does not blame the packer for it.
    assert not all(fence_parity(c.text) for c in chunks)
    assert stats.fence_parity_violations == 0
