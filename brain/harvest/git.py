"""git connector — the local apache/kafka clone, no GitHub API (spec §3.1, probe §7).

The GitHub REST API allows 60 unauthenticated requests/hour, so pulling ~6,000 PRs would
take ~100 hours. It is also unnecessary: 98.7% of commit subjects carry `(#PR)` and 66.6%
carry a `KAFKA-\\d+` key, so Commit→PR and Commit→Issue are pure text extraction.

Everything below runs against a shallow, blobless clone. `--filter=blob:none --no-checkout`
still ships the trees, so `git log --name-only` works fully offline.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from brain.harvest.base import (
    BaseConnector,
    Checkpoint,
    HarvestError,
    Page,
    ProbeResult,
    signature_of,
    utc_now_iso,
)

CLONE_URL = "https://github.com/apache/kafka"
CLONE_DIRNAME = "kafka"
WINDOW_START = "2023-01-01"
WINDOW_END = "2025-12-31"
COMMITS_FILE = "commits.jsonl"
BATCH = 1000

# ASCII record/unit separators: they cannot occur in a commit message, unlike any
# printable delimiter a project might paste into a body.
RS = "\x1e"
US = "\x1f"
LOG_FORMAT = f"{RS}%H{US}%an{US}%ae{US}%aI{US}%s{US}%b{US}"

ISSUE_KEY = re.compile(r"\bKAFKA-\d+\b")
PR_NUMBER = re.compile(r"\(#\d+\)")


def _run(args: list[str], cwd: Path | None = None, timeout: float = 900.0) -> str:
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise HarvestError(f"{' '.join(args)} failed ({proc.returncode}): {proc.stderr[:400]}")
    return proc.stdout


def parse_git_log(text: str) -> Iterator[dict[str, Any]]:
    """Parse `git log --name-only` output produced with LOG_FORMAT.

    Anything that does not have the seven expected fields is skipped rather than
    guessed at — a malformed record must not silently become a half-empty commit.
    """
    for raw in text.split(RS):
        if not raw.strip():
            continue
        parts = raw.split(US)
        if len(parts) < 7:
            continue
        sha, author_name, author_email, authored_at, subject, body, tail = parts[:7]
        files = [line.strip() for line in tail.splitlines() if line.strip()]
        yield {
            "sha": sha.strip(),
            "author_name": author_name,
            "author_email": author_email,
            "authored_at": authored_at.strip(),
            "subject": subject,
            "body": body.strip("\n"),
            "files": files,
        }


class GitConnector(BaseConnector):
    name = "git"
    page_stem = "commits"

    def __init__(
        self,
        raw_dir: Path,
        *,
        runner=_run,
        clone_url: str = CLONE_URL,
        batch: int = BATCH,
        window_end: str | None = WINDOW_END,
    ) -> None:
        super().__init__(raw_dir)
        self._run = runner
        self.clone_url = clone_url
        self.batch = batch
        self.window_end = window_end

    # -- layout ------------------------------------------------------------

    @property
    def clone_dir(self) -> Path:
        return self.source_dir() / CLONE_DIRNAME

    def commits_path(self, since: date | None) -> Path:
        return self.run_dir(since) / COMMITS_FILE

    def window(self, since: date | None) -> tuple[str, str | None]:
        return (since.isoformat() if since else WINDOW_START), self.window_end

    # -- identity ----------------------------------------------------------

    def query_text(self, since: date | None) -> str:
        start, end = self.window(since)
        return f"git log --since={start}" + (f" --until={end}" if end else "")

    def signature(self, since: date | None) -> str:
        return signature_of({"window": self.window(since), "format": LOG_FORMAT})

    # -- clone -------------------------------------------------------------

    def ensure_clone(self) -> bool:
        """Clone on first use; afterwards the local repo is the source of truth."""
        if (self.clone_dir / ".git").exists():
            return False
        self.clone_dir.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                f"--shallow-since={WINDOW_START}",
                self.clone_url,
                str(self.clone_dir),
            ],
            None,
        )
        return True

    def probe(self) -> ProbeResult:
        if not (self.clone_dir / ".git").exists():
            return ProbeResult(ok=False, detail=f"no clone at {self.clone_dir}")
        try:
            head = self._run(["git", "log", "-1", "--format=%H %aI"], self.clone_dir).strip()
        except HarvestError as exc:
            return ProbeResult(ok=False, detail=str(exc))
        return ProbeResult(ok=True, detail=f"clone at {self.clone_dir}, HEAD {head}")

    # -- fetch -------------------------------------------------------------

    def _log(self, since: date | None) -> str:
        start, end = self.window(since)
        args = ["git", "log", f"--since={start}"]
        if end:
            args.append(f"--until={end}")
        # --no-renames is not cosmetic. Rename detection is on by default and needs blob
        # *content* to score similarity, which in a --filter=blob:none clone means a
        # promisor fetch per commit: measured 7s for one month of history vs 0s with it
        # off. It is also the shape we want — both sides of a rename are files the commit
        # touched, and the graph should carry both.
        args += ["--name-only", "--no-renames", f"--format={LOG_FORMAT}"]
        return self._run(args, self.clone_dir)

    def fetch(self, since: date | None, checkpoint: Checkpoint) -> Iterator[Page]:
        if checkpoint.done:
            return
        if self.ensure_clone():
            self.errors.append(
                {
                    "when": utc_now_iso(),
                    "kind": "clone",
                    "detail": f"cloned {self.clone_url} into {self.clone_dir}",
                    "fatal": False,
                }
            )

        commits = list(parse_git_log(self._log(since)))
        out = self.commits_path(since)
        out.parent.mkdir(parents=True, exist_ok=True)
        written = _truncate_jsonl(out, checkpoint.records)
        index = checkpoint.pages

        if written >= len(commits):
            checkpoint.finish()
            return

        with out.open("a", encoding="utf-8") as f:
            for start in range(written, len(commits), self.batch):
                batch = commits[start : start + self.batch]
                for commit in batch:
                    f.write(json.dumps(commit, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
                page = Page(index=index, records=batch, path=out)
                checkpoint.advance(
                    page=page,
                    cursor={"offset": start + len(batch), "last_sha": batch[-1]["sha"]},
                    total=len(commits),
                )
                yield page
                index += 1
        checkpoint.finish()

    # -- stats -------------------------------------------------------------

    def stats(self, since: date | None) -> dict[str, Any]:
        return analyze(iter_raw_commits(self.commits_path(since)))


def _truncate_jsonl(path: Path, keep: int) -> int:
    """Drop anything written past the last checkpoint, so a resume cannot duplicate."""
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= keep:
        return len(lines)
    path.write_text("".join(line + "\n" for line in lines[:keep]), encoding="utf-8")
    return keep


# --------------------------------------------------------------------------- raw analysis


def iter_raw_commits(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def analyze(commits: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total = 0
    keyed = 0
    keyed_with_pr = 0
    with_pr = 0
    files = 0
    keys: set[str] = set()

    for commit in commits:
        total += 1
        text = f"{commit.get('subject', '')}\n{commit.get('body', '')}"
        found = ISSUE_KEY.findall(text)
        has_pr = bool(PR_NUMBER.search(text))
        files += len(commit.get("files") or [])
        with_pr += 1 if has_pr else 0
        if found:
            keyed += 1
            keys.update(found)
            keyed_with_pr += 1 if has_pr else 0

    n = total or 1
    return {
        "commits": total,
        "with_issue_key": keyed,
        "pct_with_issue_key": round(100 * keyed / n, 1),
        "with_pr_number": with_pr,
        "pct_with_pr_number": round(100 * with_pr / n, 1),
        "keyed_with_pr_number": keyed_with_pr,
        "pct_of_keyed_with_pr_number": round(100 * keyed_with_pr / (keyed or 1), 1),
        "distinct_issue_keys": len(keys),
        "avg_files_per_commit": round(files / n, 2),
    }
