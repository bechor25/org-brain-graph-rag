"""git mapper: commits, and the pull requests their subjects imply."""

from __future__ import annotations

from brain.canon.mappers.git import map_commits
from tests.canon_helpers import commit, load_fixture


def test_golden_commit_maps_to_the_expected_change():
    """A real apache/kafka commit, verbatim from `data/raw/`, against a frozen record."""
    raw = load_fixture("git", "raw_commit.json")
    bundle = map_commits([raw])
    by_kind = {c.kind: c for c in bundle.changes}

    assert by_kind["commit"].model_dump(mode="json") == load_fixture("git", "expected_commit.json")
    assert by_kind["pr"].model_dump(mode="json") == load_fixture("git", "expected_pr.json")


def test_the_message_is_the_subject_plus_the_body():
    change = map_commits([commit("a" * 40, "KAFKA-1234 fix (#12)", body="why\nnot")]).changes[0]

    assert change.message == "KAFKA-1234 fix (#12)\n\nwhy\nnot"
    assert change.pr == "pr:12"
    assert [(r.kind, r.key) for r in change.refs] == [("issue", "KAFKA-1234")]


def test_one_pr_record_per_number_even_when_a_backport_repeats_it():
    commits = [
        commit("b" * 40, "KAFKA-1234 fix (#12)", authored_at="2024-05-01T00:00:00+00:00"),
        commit("a" * 40, "KAFKA-1234 fix (#12)", authored_at="2024-03-01T00:00:00+00:00"),
    ]
    bundle = map_commits(commits)
    prs = [c for c in bundle.changes if c.kind == "pr"]

    assert [c.id for c in prs] == ["pr:12"]
    # The earliest commit owns the PR, so git log order cannot change the output.
    assert prs[0].at.isoformat() == "2024-03-01T00:00:00+00:00"
    assert bundle.stats["prs_claimed_by_more_than_one_commit"] == 1


def test_the_pull_request_a_commit_is_goes_on_pr_not_into_refs():
    """`(#12)` at the end of the subject is the merge, not a mention of another PR."""
    bundle = map_commits(
        [commit("a" * 40, "KAFKA-1234 fix, follow-up to (#7) (#12)", body="see #99")]
    )
    by_kind = {c.kind: c for c in bundle.changes}

    assert by_kind["commit"].pr == "pr:12"
    assert [(r.kind, r.key) for r in by_kind["commit"].refs] == [
        ("issue", "KAFKA-1234"),
        ("pr", "7"),
        ("pr", "99"),
    ]
    assert bundle.stats["subjects_naming_more_than_one_pr"] == 1
    assert bundle.stats["commits_with_pr"] == 1
    # both numbers still get a PR record; only the trailing one is this commit's identity
    assert sorted(c.id for c in bundle.changes if c.kind == "pr") == ["pr:12", "pr:7"]


def test_a_commit_without_a_trailing_marker_has_no_pr():
    bundle = map_commits([commit("a" * 40, "KAFKA-1234 fix", body="follow-up to #99")])

    assert bundle.changes[0].pr is None
    assert bundle.stats["commits_with_pr"] == 0


def test_a_bare_hash_in_the_body_is_a_mention_not_a_pull_request_record():
    bundle = map_commits([commit("a" * 40, "KAFKA-1234 fix", body="follow-up to #99")])

    assert [c.kind for c in bundle.changes] == ["commit"]
    assert ("pr", "99") in [(r.kind, r.key) for r in bundle.changes[0].refs]


def test_the_author_identity_is_the_lowercased_email():
    bundle = map_commits([commit("a" * 40, "x", author_email="Jun.Rao@Example.COM")])
    person = bundle.persons["git:jun.rao@example.com"]

    assert person.identities[0].email == "jun.rao@example.com"
    assert person.identities[0].display == "Jun Rao"
    assert bundle.changes[0].author_email == "jun.rao@example.com"


def test_pseudo_keys_are_filtered_out_of_commit_messages():
    bundle = map_commits([commit("a" * 40, "Bump to fix CVE-2023-1234 and KAKFA-17173")])

    assert [r.key for r in bundle.changes[0].refs] == []
    assert bundle.refs.removed["CVE-2023"] == 1
    assert bundle.refs.removed["KAKFA-17173"] == 1


def test_git_has_nothing_unmapped_because_the_harvester_already_projected():
    bundle = map_commits([commit("a" * 40, "x")])

    assert bundle.stats["unmapped_fields"]["dropped_with_data"] == {}
    assert bundle.stats["limitations"]


def test_files_belong_to_the_commit_not_to_the_pull_request():
    bundle = map_commits([commit("a" * 40, "KAFKA-1234 (#12)", files=["a.java", "b.java"])])
    by_kind = {c.kind: c for c in bundle.changes}

    assert by_kind["commit"].files == ["a.java", "b.java"]
    assert by_kind["pr"].files == []
