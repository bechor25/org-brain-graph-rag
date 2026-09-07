"""Leiden's output -> communities: the levels, the misc rule, and the hash.

These are the two claims the whole step rests on — *the same partition gives the same
`member_hash`* and *a community under `--min-size` is placed but never summarised* — and
neither of them needs a database to be true or false.
"""

from __future__ import annotations

import pytest

from brain.community.partition import (
    COARSE,
    FINE,
    Community,
    Member,
    PartitionError,
    kind_placement,
    label_placement,
    level_summary,
    member_hash,
    partition,
    pick_levels,
    size_distribution,
)
from tests.community_helpers import line, rows


def community(size: int = 6, *, level: int = FINE, min_size: int = 5, label="Entity") -> Community:
    return Community(
        level=level,
        community_id=1,
        members=[Member(label=label, key=f"k{i}") for i in range(size)],
        min_size=min_size,
    )


# ------------------------------------------------------------------------------- the hash


def test_member_hash_ignores_the_order_members_arrived_in():
    a = [Member("Entity", "x"), Member("WorkItem", "KAFKA-1")]
    assert member_hash(a) == member_hash(list(reversed(a)))


def test_member_hash_separates_two_labels_that_share_a_key():
    """`Component` "streams" and `Entity` "streams" are two members, not one."""
    assert member_hash([Member("Component", "streams")]) != member_hash(
        [Member("Entity", "streams")]
    )


def test_two_communities_with_the_same_members_have_the_same_hash_and_different_ids():
    left = Community(level=FINE, community_id=7, members=[Member("Entity", "a")])
    right = Community(level=FINE, community_id=9999, members=[Member("Entity", "a")])
    assert left.member_hash == right.member_hash
    assert left.id == "L0-7" and right.id == "L0-9999"


def test_a_repeated_partition_of_the_same_rows_produces_identical_hashes():
    spec = {**line(6, levels=(0, 0)), **line(6, levels=(1, 0), label="WorkItem")}
    first, _ = partition(rows(spec))
    second, _ = partition(rows(dict(reversed(list(spec.items())))))
    assert [c.member_hash for c in first] == [c.member_hash for c in second]


# ------------------------------------------------------------------------------- levels


def test_the_finer_level_is_level_zero_whatever_order_gds_numbered_them():
    """Two GDS levels, the second coarser. Fine is the one with more communities."""
    spec = {
        "Entity:a": [0, 0],
        "Entity:b": [1, 0],
        "Entity:c": [2, 0],
    }
    communities, gds_levels = partition(rows(spec), min_size=1)
    assert gds_levels == [0, 1]
    fine = [c for c in communities if c.level == FINE]
    coarse = [c for c in communities if c.level == COARSE]
    assert len(fine) == 3 and len(coarse) == 1


def test_a_reversed_hierarchy_is_still_read_fine_first():
    """If GDS ever returned coarse-first, the level *names* must not flip with it."""
    spec = {"Entity:a": [0, 0], "Entity:b": [0, 1], "Entity:c": [0, 2]}
    communities, gds_levels = partition(rows(spec), min_size=1)
    assert gds_levels == [1, 0]
    assert len({c.community_id for c in communities if c.level == FINE}) == 3
    assert len({c.community_id for c in communities if c.level == COARSE}) == 1


def test_only_the_coarsest_levels_are_kept():
    spec = {"Entity:a": [0, 1, 2, 3], "Entity:b": [9, 8, 7, 3]}
    assert pick_levels(rows(spec), 2) == [2, 3]


def test_explicit_level_indices_win_over_the_default_choice():
    spec = {"Entity:a": [0, 1, 2, 3], "Entity:b": [9, 8, 7, 3]}
    _, used = partition(rows(spec), indices=[0, 3], min_size=1)
    assert used == [0, 3]


def test_a_level_index_outside_what_leiden_ran_is_an_error_not_a_silent_clamp():
    with pytest.raises(PartitionError, match="outside the 2 levels"):
        partition(rows({"Entity:a": [0, 0]}), indices=[0, 5])


def test_a_coarse_community_names_the_fine_communities_it_swallowed():
    spec = {"Entity:a": [0, 0], "Entity:b": [1, 0], "Entity:c": [2, 0]}
    communities, _ = partition(rows(spec), min_size=1)
    coarse = next(c for c in communities if c.level == COARSE)
    assert coarse.children == [0, 1, 2]


def test_nodes_assigned_at_different_depths_are_refused():
    bad = rows({"Entity:a": [0, 1], "Entity:b": [0]})
    with pytest.raises(PartitionError, match="every node must be assigned at every level"):
        partition(bad)


def test_an_empty_stream_is_an_error_not_an_empty_partition():
    with pytest.raises(PartitionError, match="nothing to partition"):
        partition([])


# --------------------------------------------------------------------------------- misc


def test_a_community_under_min_size_is_misc_and_not_summarized():
    small = community(size=4, min_size=5)
    assert small.misc is True and small.summarized is False


def test_a_community_at_min_size_is_summarized():
    assert community(size=5, min_size=5).summarized is True


def test_a_misc_community_still_carries_every_member():
    """The acceptance criterion: every member has a fine-level community, misc or not."""
    spec = {**line(6, levels=(0, 0)), "Entity:lonely": [1, 0]}
    communities, _ = partition(rows(spec), min_size=5)
    fine = [c for c in communities if c.level == FINE]
    assert sum(c.size for c in fine) == 7
    assert [c.misc for c in sorted(fine, key=lambda c: c.community_id)] == [False, True]


def test_the_row_written_to_neo4j_never_claims_a_report_it_does_not_have():
    row = community(size=9).row()
    assert row["summarized"] is False
    assert row["id"] == "L0-1" and row["size"] == 9
    assert row["member_hash"] and row["misc"] is False
    assert row["members_by_label"] == ["Entity=9"]


# ------------------------------------------------------------------------- distributions


def test_size_distribution_buckets_and_percentiles():
    d = size_distribution([1, 1, 2, 6, 30, 400])
    assert d["communities"] == 6 and d["members"] == 440
    assert d["min"] == 1 and d["max"] == 400
    assert d["buckets"] == {"1-1": 2, "2-4": 1, "5-9": 1, "25-49": 1, "250+": 1}


def test_an_empty_distribution_is_zeroes_not_a_crash():
    assert size_distribution([])["communities"] == 0


def test_level_summary_counts_summarized_and_misc_apart():
    spec = {**line(6, levels=(0, 0)), "Entity:lonely": [1, 0]}
    communities, _ = partition(rows(spec), min_size=5)
    summary = level_summary(communities)
    assert summary["0"]["communities"] == 2
    assert summary["0"]["summarized"] == 1 and summary["0"]["misc"] == 1
    assert summary["1"]["communities"] == 1


# ------------------------------------------------------------------------------ placement


def test_label_placement_counts_singletons_and_misc_at_the_fine_level():
    spec = {
        **line(6, levels=(0, 0)),
        "Entity:alone": [1, 0],
        "WorkItem:KAFKA-1": [2, 0],
    }
    communities, _ = partition(rows(spec), min_size=5)
    entity = label_placement(communities, "Entity")
    assert entity["members"] == 7
    assert entity["in_singleton_community"] == 1 and entity["in_misc_community"] == 1
    assert label_placement(communities, "WorkItem")["in_misc_community"] == 1


def test_kind_placement_splits_the_technology_question_out_of_the_entity_total():
    """83% of Technology entities have no semantic edge; this is where that shows up."""
    spec = {**line(6, levels=(0, 0)), "Entity:tech-alone": [1, 0]}
    communities, _ = partition(rows(spec), min_size=5)
    kinds = {f"k{i}": "Decision" for i in range(6)} | {"tech-alone": "Technology"}
    by_kind = kind_placement(communities, kinds)
    assert by_kind["Technology"] == {
        "members": 1,
        "in_singleton_community": 1,
        "in_misc_community": 1,
        "pct_in_misc": 100.0,
    }
    assert by_kind["Decision"]["in_misc_community"] == 0
