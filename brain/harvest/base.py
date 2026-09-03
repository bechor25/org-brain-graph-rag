"""Connector plumbing shared by every source (spec §3.1).

Three ideas carry the whole step:

1. **Raw to disk, once.** A connector writes the source's answer verbatim under
   ``data/raw/<source>/``. Nothing is normalized here — mapping is `brain canon`,
   and keeping raw means a mapper bug costs a re-run of `canon`, not of `harvest`.
2. **A checkpoint after every page.** A crash at page 40 of 56 resumes at 40.
   The checkpoint carries the *signature* of the query it belongs to, so changing
   the query (or `--since`) can never silently resume into a different result set.
3. **Polite and stubborn.** ≥1s between remote calls, exponential backoff with
   jitter on 429/5xx/timeouts, and every failure recorded in the report instead
   of a silent "success".

Reading ``data/raw/`` back (this is the contract for `brain canon`)
------------------------------------------------------------------
**Never glob a run directory.** The file list lives in ``<run_dir>/checkpoint.json``
under ``files``; use :func:`checkpoint_pages`. A page index restarts at 0 whenever the
query signature changes, so a glob can pick up higher-numbered pages left by an older,
different query and hand the same record to canon twice.

A source has one **authoritative** directory — ``data/raw/<source>/``, the full slice —
plus zero or more **incremental** directories ``data/raw/<source>/since-<date>/`` from
``--since`` runs. They overlap by construction: an incremental pull re-fetches records the
full pull already has. Canon must read the authoritative directory first, then each
incremental directory in date order, and **deduplicate**:

===========  ============  ====================================
source       dedupe key    recency field (latest wins)
===========  ============  ====================================
jira         ``key``       ``fields.updated``
confluence   ``id``        ``version.number``
git          ``sha``       n/a — a sha is content-addressed
===========  ============  ====================================

``data/reports/harvest.json`` restates this under ``raw_layout`` with the directories
that actually exist.

Durability limit: pages and checkpoints are written temp+rename with an fsync on the
file, but the *directory* entry is not fsynced. A power cut (not a process crash) can
therefore lose the last rename. Harvest is re-runnable, so the cost is one refetched page.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

RawRecord = dict[str, Any]

USER_AGENT = "org-brain-poc/0.1 (Graph RAG study project; contact: bechor21@gmail.com)"


class HarvestError(RuntimeError):
    """A source could not be harvested. Recorded in the report; other sources continue."""


# --------------------------------------------------------------------------- utils


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def signature_of(payload: Any) -> str:
    """Stable short hash of whatever defines "the same pull" for a connector."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


CHECKPOINT_NAME = "checkpoint.json"


def read_checkpoint_file(run_dir: Path) -> dict[str, Any]:
    """The raw checkpoint dict for a run directory, or {} if there is none/unreadable."""
    path = run_dir / CHECKPOINT_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def checkpoint_pages(run_dir: Path) -> list[Path]:
    """The page files this run's checkpoint recorded, in write order.

    This is the *only* correct way to enumerate raw pages. Globbing `pages-*.json`
    would also return pages left behind by an earlier run with a different query
    signature — the page index restarts at 0 on a signature change — which shows up
    downstream as duplicate records rather than as an error.
    """
    files = read_checkpoint_file(run_dir).get("files") or []
    return [p for p in (run_dir / str(name) for name in files) if p.is_file()]


def write_json_atomic(path: Path, obj: Any) -> None:
    """Write via temp+rename so a crash never leaves a half-written raw page behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


# --------------------------------------------------------------------------- models


@dataclass
class ProbeResult:
    """Cheap reachability check before a full pull."""

    ok: bool
    detail: str
    total: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "detail": self.detail, "total": self.total}


@dataclass
class Page:
    """One persisted unit of raw data. Pages are the checkpoint granularity."""

    index: int
    records: list[RawRecord]
    path: Path | None = None

    def __len__(self) -> int:
        return len(self.records)


@dataclass
class Checkpoint:
    """Resume state for one (source, query) pair, persisted after every page."""

    source: str
    path: Path
    signature: str = ""
    cursor: dict[str, Any] = field(default_factory=dict)
    pages: int = 0
    records: int = 0
    total: int | None = None
    done: bool = False
    files: list[str] = field(default_factory=list)
    updated_at: str | None = None
    query: str | None = None
    #: pages written by a *previous*, differently-signed query — must be deleted before
    #: this run writes, or they linger as duplicates nobody counted on.
    stale_files: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, source: str, signature: str, query: str | None = None) -> Checkpoint:
        """Restore the checkpoint if it belongs to this exact query, else start fresh.

        A signature mismatch means the query changed under us (new JQL, new `--since`);
        resuming would splice two different result sets together, so we start over — and
        the pages of the old query are handed back in `stale_files` for deletion, because
        the page index restarts at 0 and would otherwise leave orphans behind it.
        """
        cp = cls(source=source, path=path, signature=signature, query=query)
        if not path.exists():
            return cp
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cp
        if data.get("signature") != signature:
            cp.stale_files = [str(name) for name in data.get("files") or []]
            return cp
        cp.cursor = data.get("cursor") or {}
        cp.pages = int(data.get("pages", 0))
        cp.records = int(data.get("records", 0))
        cp.total = data.get("total")
        cp.done = bool(data.get("done", False))
        cp.files = list(data.get("files") or [])
        cp.updated_at = data.get("updated_at")
        return cp

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "signature": self.signature,
            "query": self.query,
            "cursor": self.cursor,
            "pages": self.pages,
            "records": self.records,
            "total": self.total,
            "done": self.done,
            "files": self.files,
            "updated_at": self.updated_at,
        }

    def save(self) -> None:
        self.updated_at = utc_now_iso()
        write_json_atomic(self.path, self.as_dict())

    def advance(self, *, page: Page, cursor: dict[str, Any], total: int | None = None) -> None:
        self.pages += 1
        self.records += len(page.records)
        self.cursor = cursor
        if total is not None:
            self.total = total
        if page.path is not None:
            name = page.path.name
            if name not in self.files:
                self.files.append(name)
        self.save()

    def finish(self) -> None:
        self.done = True
        self.save()


@dataclass
class HarvestResult:
    """What one source contributed to this run — goes straight into the report."""

    source: str
    records: int = 0  # records fetched *in this run*
    pages: int = 0  # pages fetched *in this run*
    duration_s: float = 0.0
    errors: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "pages": self.pages,
            "duration_s": round(self.duration_s, 2),
            "errors": self.errors,
            "checkpoint": self.checkpoint,
            "stats": self.stats,
        }


@runtime_checkable
class Connector(Protocol):
    """The contract every source implements."""

    name: str

    def probe(self) -> ProbeResult: ...

    def fetch(self, since: date | None, checkpoint: Checkpoint) -> Iterator[Page]: ...


# --------------------------------------------------------------------------- http


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 2.0
    max_delay: float = 60.0
    jitter: float = 0.25


class HttpFetcher:
    """A polite, stubborn HTTP GET: pacing between calls + backoff on transient failures.

    `sleep` and `monotonic` are injectable so tests can assert on the backoff schedule
    without actually waiting.
    """

    RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
    RETRY_EXC = (httpx.TimeoutException, httpx.TransportError)

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        min_interval: float = 1.0,
        policy: RetryPolicy | None = None,
        timeout: float = 300.0,
        sleep=time.sleep,
        monotonic=time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.policy = policy or RetryPolicy()
        self.min_interval = min_interval
        # A 500-issue Jira page with fields=*all is tens of MB: the read budget has to be
        # generous, while connect stays short so a dead host fails fast into the backoff.
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=15.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self._owns_client = client is None
        self._sleep = sleep
        self._monotonic = monotonic
        self._rng = rng or random.Random(0)
        self._last_call: float | None = None
        self.calls = 0
        self.retries = 0
        self.errors: list[dict[str, Any]] = []

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- pacing ------------------------------------------------------------

    def _pace(self) -> None:
        if self._last_call is None:
            return
        wait = self.min_interval - (self._monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.policy.max_delay)
            except ValueError:
                pass
        delay = min(self.policy.base_delay * (2**attempt), self.policy.max_delay)
        return delay * (1 + self._rng.uniform(0, self.policy.jitter))

    # -- request -----------------------------------------------------------

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        last_detail = "unknown"
        for attempt in range(self.policy.max_attempts):
            self._pace()
            self.calls += 1
            try:
                response = self._client.get(url, params=params)
                self._last_call = self._monotonic()
                if response.status_code in self.RETRY_STATUS:
                    last_detail = f"HTTP {response.status_code}"
                    retry_after = response.headers.get("Retry-After")
                else:
                    response.raise_for_status()
                    return response.json()
            except self.RETRY_EXC as exc:
                self._last_call = self._monotonic()
                last_detail = f"{type(exc).__name__}: {exc}"
                retry_after = None
            except httpx.HTTPStatusError as exc:  # 4xx that is not worth retrying
                self._last_call = self._monotonic()
                detail = f"HTTP {exc.response.status_code} for {url}: {exc.response.text[:400]}"
                self.errors.append(
                    {"when": utc_now_iso(), "kind": "http", "detail": detail, "fatal": True}
                )
                raise HarvestError(detail) from exc

            if attempt + 1 >= self.policy.max_attempts:
                break
            delay = self._backoff(attempt, retry_after)
            self.retries += 1
            note = f"{last_detail} for {url}; attempt {attempt + 1}, sleeping {delay:.1f}s"
            self.errors.append(
                {"when": utc_now_iso(), "kind": "retry", "detail": note, "fatal": False}
            )
            self._sleep(delay)

        detail = f"gave up after {self.policy.max_attempts} attempts on {url}: {last_detail}"
        self.errors.append({"when": utc_now_iso(), "kind": "http", "detail": detail, "fatal": True})
        raise HarvestError(detail)


# --------------------------------------------------------------------------- base connector


class BaseConnector:
    """Shared run loop: resolve the run directory, resume, page, checkpoint, report."""

    name: str = "base"
    #: file stem for raw pages, e.g. "issues" -> issues-0000.json
    page_stem: str = "page"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = Path(raw_dir)
        self.errors: list[dict[str, Any]] = []

    # -- layout ------------------------------------------------------------

    def source_dir(self) -> Path:
        return self.raw_dir / self.name

    def run_dir(self, since: date | None) -> Path:
        """Full pulls live in `data/raw/<source>/`; `--since` pulls get their own subdir.

        Keeping incremental pulls apart means a narrow re-pull can never overwrite (or be
        confused with) the full slice, and each run keeps its own resumable checkpoint.
        """
        base = self.source_dir()
        return base if since is None else base / f"since-{since.isoformat()}"

    def checkpoint_path(self, since: date | None) -> Path:
        return self.run_dir(since) / "checkpoint.json"

    def page_path(self, since: date | None, index: int) -> Path:
        return self.run_dir(since) / f"{self.page_stem}-{index:04d}.json"

    # -- hooks -------------------------------------------------------------

    def signature(self, since: date | None) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def query_text(self, since: date | None) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def probe(self) -> ProbeResult:  # pragma: no cover - overridden
        raise NotImplementedError

    def fetch(self, since: date | None, checkpoint: Checkpoint) -> Iterator[Page]:
        raise NotImplementedError

    def stats(self, since: date | None) -> dict[str, Any]:
        return {}

    # -- run ---------------------------------------------------------------

    def checkpoint(self, since: date | None) -> Checkpoint:
        checkpoint = Checkpoint.load(
            self.checkpoint_path(since),
            source=self.name,
            signature=self.signature(since),
            query=self.query_text(since),
        )
        self.discard_stale(checkpoint)
        return checkpoint

    def discard_stale(self, checkpoint: Checkpoint) -> list[str]:
        """Delete pages left by a previous query before this one starts writing.

        The page index restarts at 0 on a signature change, so a shorter new pull would
        leave the old run's higher-numbered pages in place. They are not referenced by the
        new checkpoint, but anything that globs the directory would still find them.
        """
        if not checkpoint.stale_files:
            return []
        run_dir = checkpoint.path.parent
        removed: list[str] = []
        for name in checkpoint.stale_files:
            path = run_dir / name
            if path.is_file():
                path.unlink()
                removed.append(name)
        checkpoint.stale_files = []
        if removed:
            self.errors.append(
                {
                    "when": utc_now_iso(),
                    "kind": "reset",
                    "detail": f"query changed; removed {len(removed)} stale page(s) "
                    f"from {run_dir}: {', '.join(removed[:5])}" + ("…" if len(removed) > 5 else ""),
                    "fatal": False,
                }
            )
        return removed

    def write_page(self, since: date | None, index: int, payload: Any) -> Path:
        path = self.page_path(since, index)
        write_json_atomic(path, payload)
        return path

    def collected_errors(self) -> list[dict[str, Any]]:
        """Connector-level notes plus whatever the HTTP layer recorded (retries included)."""
        http = getattr(self, "http", None)
        return list(self.errors) + (list(http.errors) if http is not None else [])

    def run(self, since: date | None = None) -> HarvestResult:
        started = time.perf_counter()
        result = HarvestResult(source=self.name)
        checkpoint = self.checkpoint(since)
        failure: str | None = None
        try:
            for page in self.fetch(since, checkpoint):
                result.pages += 1
                result.records += len(page.records)
        except Exception as exc:  # noqa: BLE001 - one bad source must not sink the others
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            result.errors.extend(self.collected_errors())
            if failure and not any(e.get("fatal") for e in result.errors):
                result.errors.append(
                    {"when": utc_now_iso(), "kind": "fatal", "detail": failure, "fatal": True}
                )
            result.duration_s = time.perf_counter() - started
            result.checkpoint = checkpoint.as_dict()
            try:
                result.stats = self.stats(since)
            except Exception as exc:  # noqa: BLE001 - stats must never sink a run
                result.errors.append(
                    {"when": utc_now_iso(), "kind": "stats", "detail": str(exc), "fatal": False}
                )
        return result
