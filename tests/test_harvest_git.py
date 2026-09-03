"""git connector: log parsing from a fixed string, batching, resume, idempotency."""

from __future__ import annotations

import json
from datetime import date

import pytest

from brain.harvest.base import HarvestError
from brain.harvest.git import RS, US, GitConnector, analyze, parse_git_log

# A verbatim shape of `git log --name-only --format=LOG_FORMAT`: subject with a PR number,
# a multi-line body with a trailer, then the touched files. The third record is a merge
# commit — no files at all.
FIXED_LOG = (
    f"{RS}32efc1cd09817b4eb1de19b789949abc0b7b9f89{US}Lan Ding{US}isDing_L@163.com{US}"
    f"2025-12-31T00:52:10+08:00{US}"
    f"KAFKA-19755 Move KRaftClusterTest from core module to server module [3/4] (#21175){US}"
    "Move KRaftClusterTest from core module to server module.\n"
    "\n"
    f"Reviewers: Chia-Ping Tsai <chia7712@gmail.com>{US}"
    "\n"
    "\n"
    "core/src/test/scala/integration/kafka/server/KRaftClusterTest.scala\n"
    "server/src/test/java/org/apache/kafka/server/KRaftClusterTest.java\n"
    f"{RS}59e4af3f2cb084cf1e8fb2946eca15de1fee1e10{US}Nandini Singhal{US}"
    f"nandini.singhal@datadoghq.com{US}2025-12-30T00:54:01+01:00{US}"
    f"MINOR: docs typo{US}{US}"
    "\n"
    "docs/README.md\n"
    f"{RS}aaaabbbbccccddddeeeeffff0000111122223333{US}Ismael Juma{US}ismael@juma.me.uk{US}"
    f"2025-12-29T10:00:00Z{US}Merge branch 'trunk'{US}{US}"
    "\n"
)


def test_parses_a_fixed_git_log_string():
    commits = list(parse_git_log(FIXED_LOG))
    assert len(commits) == 3

    first = commits[0]
    assert first["sha"] == "32efc1cd09817b4eb1de19b789949abc0b7b9f89"
    assert first["author_name"] == "Lan Ding"
    assert first["author_email"] == "isDing_L@163.com"
    assert first["authored_at"] == "2025-12-31T00:52:10+08:00"
    assert first["subject"].startswith("KAFKA-19755 Move KRaftClusterTest")
    assert first["subject"].endswith("(#21175)")
    assert "Reviewers: Chia-Ping Tsai" in first["body"]
    assert first["files"] == [
        "core/src/test/scala/integration/kafka/server/KRaftClusterTest.scala",
        "server/src/test/java/org/apache/kafka/server/KRaftClusterTest.java",
    ]

    assert commits[1]["body"] == ""
    assert commits[1]["files"] == ["docs/README.md"]
    assert commits[2]["files"] == []  # merge commit


def test_parser_skips_malformed_records_instead_of_guessing():
    broken = FIXED_LOG + f"{RS}deadbeef{US}only two fields\n"
    assert len(list(parse_git_log(broken))) == 3


def test_empty_log_yields_nothing():
    assert list(parse_git_log("")) == []


# --------------------------------------------------------------------------- connector


def fake_runner(log_text: str, calls: list[list[str]] | None = None):
    def runner(args: list[str], cwd=None) -> str:
        if calls is not None:
            calls.append(args)
        if args[:2] == ["git", "log"] and "--name-only" in args:
            return log_text
        return "abc123 2025-12-31T00:00:00Z\n"

    return runner


def connector(tmp_path, log_text: str = FIXED_LOG, calls=None, batch: int = 1000) -> GitConnector:
    clone = tmp_path / "git" / "kafka" / ".git"
    clone.mkdir(parents=True, exist_ok=True)
    return GitConnector(tmp_path, runner=fake_runner(log_text, calls), batch=batch)


def test_writes_commits_jsonl_with_the_agreed_fields(tmp_path):
    result = connector(tmp_path).run()

    out = tmp_path / "git" / "commits.jsonl"
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert result.records == 3
    assert len(rows) == 3
    assert set(rows[0]) == {
        "sha",
        "author_name",
        "author_email",
        "authored_at",
        "subject",
        "body",
        "files",
    }
    assert result.checkpoint["done"] is True


def test_log_window_matches_the_corpus_slice(tmp_path):
    calls: list[list[str]] = []
    connector(tmp_path, calls=calls).run()
    log_args = [a for a in calls if a[:2] == ["git", "log"] and "--name-only" in a][0]
    assert "--since=2023-01-01" in log_args
    assert "--until=2025-12-31" in log_args


def test_rename_detection_is_off(tmp_path):
    """On a blobless clone rename scoring triggers a promisor fetch per commit."""
    calls: list[list[str]] = []
    connector(tmp_path, calls=calls).run()
    log_args = [a for a in calls if a[:2] == ["git", "log"] and "--name-only" in a][0]
    assert "--no-renames" in log_args


def test_since_overrides_the_window_start_and_isolates_the_output(tmp_path):
    calls: list[list[str]] = []
    connector(tmp_path, calls=calls).run(since=date(2025, 6, 1))
    log_args = [a for a in calls if a[:2] == ["git", "log"] and "--name-only" in a][0]
    assert "--since=2025-06-01" in log_args
    assert (tmp_path / "git" / "since-2025-06-01" / "commits.jsonl").exists()


def test_second_run_writes_nothing_new(tmp_path):
    first = connector(tmp_path).run()
    second = connector(tmp_path).run()
    assert first.records == 3
    assert second.records == 0
    assert second.pages == 0
    rows = (tmp_path / "git" / "commits.jsonl").read_text().splitlines()
    assert len(rows) == 3


def test_resume_after_a_crash_does_not_duplicate_commits(tmp_path):
    c = connector(tmp_path, batch=1)
    checkpoint = c.checkpoint(None)
    pages = c.fetch(None, checkpoint)
    next(pages)
    pages.close()
    assert checkpoint.records == 1

    # a crash between flush and checkpoint can leave an extra line on disk
    out = tmp_path / "git" / "commits.jsonl"
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"sha": "torn", "files": []}) + "\n")

    resumed = connector(tmp_path, batch=1).run()

    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert resumed.records == 2
    assert len(rows) == 3
    assert [r["sha"] for r in rows] == [c["sha"] for c in parse_git_log(FIXED_LOG)]


def test_clone_is_created_when_missing(tmp_path):
    calls: list[list[str]] = []
    c = GitConnector(tmp_path, runner=fake_runner(FIXED_LOG, calls))
    c.run()
    clone_calls = [a for a in calls if a[:2] == ["git", "clone"]]
    assert len(clone_calls) == 1
    assert "--filter=blob:none" in clone_calls[0]
    assert "--shallow-since=2023-01-01" in clone_calls[0]


def test_a_failing_git_is_recorded_not_swallowed(tmp_path):
    def boom(args, cwd=None):
        raise HarvestError("git log failed (128): not a repository")

    clone = tmp_path / "git" / "kafka" / ".git"
    clone.mkdir(parents=True)
    result = GitConnector(tmp_path, runner=boom).run()
    assert result.records == 0
    assert any(e["fatal"] for e in result.errors)


def test_probe_needs_a_clone(tmp_path):
    assert GitConnector(tmp_path, runner=fake_runner("")).probe().ok is False
    assert connector(tmp_path).probe().ok is True


# --------------------------------------------------------------------------- analysis


@pytest.mark.parametrize(
    "subject, keyed, pr",
    [
        ("KAFKA-19755 Move things (#21175)", 1, 1),
        ("MINOR: docs typo (#2)", 0, 1),
        ("KAFKA-1 no pr here", 1, 0),
    ],
)
def test_analyze_counts_keys_and_pr_numbers(subject, keyed, pr):
    stats = analyze([{"subject": subject, "body": "", "files": []}])
    assert stats["with_issue_key"] == keyed
    assert stats["with_pr_number"] == pr


def test_analyze_reports_pct_of_keyed_commits_carrying_a_pr():
    commits = [
        {"subject": "KAFKA-1 a (#1)", "body": "", "files": ["a"]},
        {"subject": "KAFKA-2 b (#2)", "body": "", "files": []},
        {"subject": "KAFKA-3 c", "body": "", "files": []},
        {"subject": "MINOR d (#4)", "body": "", "files": []},
    ]
    stats = analyze(commits)
    assert stats["commits"] == 4
    assert stats["with_issue_key"] == 3
    assert stats["pct_of_keyed_with_pr_number"] == 66.7
    assert stats["distinct_issue_keys"] == 3
    assert stats["avg_files_per_commit"] == 0.25


def test_analyze_finds_keys_in_the_body_too():
    stats = analyze([{"subject": "MINOR: cleanup (#7)", "body": "follow-up to KAFKA-42"}])
    assert stats["with_issue_key"] == 1
