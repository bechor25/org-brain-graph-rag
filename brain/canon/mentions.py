"""Deterministic cross-reference extraction (spec §2.2). Runs before any LLM.

Order of results is first-appearance order; duplicates removed.

Two rules earn their complexity:

* **Issue keys are matched in any case, but only normalized when the project is
  allowlisted.** `kafka-15123` is the same issue as `KAFKA-15123` and becomes it; `utf-8`
  and `pre-2023` stay as written, so the allowlist still throws them out. Matching
  case-sensitively would have been simpler and would have lost real refs.
* **Both mention syntaxes are users.** Jira writes `[~jrao]`, everything else writes
  `@jrao`; a graph that only knows one of them has a hole exactly where the discussion is.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from urllib.parse import unquote

from brain.canon.models import Ref

_ISSUE = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,9})-(\d+)\b")
_KIP = re.compile(r"\bKIP-(\d+)\b", re.IGNORECASE)
_PR = re.compile(r"(?<![\w/#])#(\d{1,7})\b")
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_USER = re.compile(r"(?<![\w.])@([A-Za-z0-9_.-]+)")
#: Jira's own mention syntax, as it appears in descriptions and comments: `[~jrao]`.
_JIRA_USER = re.compile(r"\[~([A-Za-z0-9_.-]+)\]")

#: Project keys an `ABC-123` text match may become an issue ref for. The regex cannot
#: tell `KAFKA-15123` from `UTF-8` or `SHA-256`; only a key the harvest actually saw can.
#: The synthetic layer (Plan 1 Task 3) adds `XT/XE/XP/XS/ADO`.
ISSUE_PROJECT_ALLOWLIST: frozenset[str] = frozenset({"KAFKA"})

#: `KAFKA-1` is the placeholder key in the KIP page template — it is on ~2% of KIP pages
#: and means "put your Jira key here", never the real issue KAFKA-1.
ISSUE_KEY_BLACKLIST: frozenset[str] = frozenset({"KAFKA-1"})

NOT_ALLOWED = "not_in_allowlist"
BLACKLISTED = "blacklisted"


_URL_ISSUE = re.compile(r"/browse/([A-Za-z][A-Za-z0-9]{1,9})-(\d+)")
_URL_KIP = re.compile(r"KIP-(\d+)", re.IGNORECASE)
_URL_PR = re.compile(r"/pull/(\d+)")


def _issue_key(project: str, number: str, allowlist: Collection[str]) -> str:
    """`kafka-15123` → `KAFKA-15123`, but only for a project the allowlist knows.

    Normalizing everything would hide `utf-8` and `pre-2023` behind a plausible-looking
    `UTF-8` / `PRE-2023`; leaving them as written keeps the filter report readable and
    lets `filter_refs` throw them out on the project name.
    """
    return f"{project.upper()}-{number}" if project.upper() in allowlist else f"{project}-{number}"


def _classify_url(url: str, allowlist: Collection[str]) -> Ref:
    u = unquote(url)
    if m := _URL_ISSUE.search(u):
        return Ref(kind="issue", key=_issue_key(m.group(1), m.group(2), allowlist))
    if "confluence" in u and (m := _URL_KIP.search(u)):
        return Ref(kind="kip", key=f"KIP-{m.group(1)}")
    if "github.com" in u and (m := _URL_PR.search(u)):
        return Ref(kind="pr", key=m.group(1))
    return Ref(kind="url", key=url.rstrip(".,;"))


def extract_refs(text: str, *, allowlist: Collection[str] = ISSUE_PROJECT_ALLOWLIST) -> list[Ref]:
    """Every cross-reference in `text`, in first-appearance order, deduplicated.

    `allowlist` only decides *case normalization* here; dropping the rest is
    :func:`filter_refs`, so a caller can still see what the regexes thought was a key.
    """
    if not text:
        return []
    allowed = {p.upper() for p in allowlist}
    found: list[tuple[int, Ref]] = []

    urls = list(_URL.finditer(text))
    for m in urls:
        found.append((m.start(), _classify_url(m.group(0), allowed)))
    # mask URLs so their inner tokens are not re-matched as keys
    masked = _URL.sub(lambda m: " " * len(m.group(0)), text)

    for m in _ISSUE.finditer(masked):
        if m.group(1).upper() == "KIP":
            continue
        found.append((m.start(), Ref(kind="issue", key=_issue_key(*m.groups(), allowed))))
    for m in _KIP.finditer(masked):
        found.append((m.start(), Ref(kind="kip", key=f"KIP-{m.group(1)}")))
    for m in _PR.finditer(masked):
        found.append((m.start(), Ref(kind="pr", key=m.group(1))))
    for m in _USER.finditer(masked):
        found.append((m.start(), Ref(kind="user", key=m.group(1).rstrip("."))))
    for m in _JIRA_USER.finditer(masked):
        found.append((m.start(), Ref(kind="user", key=m.group(1).rstrip("."))))

    found.sort(key=lambda t: t[0])
    seen: set[tuple[str, str]] = set()
    out: list[Ref] = []
    for _, ref in found:
        k = (ref.kind, ref.key)
        if k in seen:
            continue
        seen.add(k)
        out.append(ref)
    return out


# --------------------------------------------------------------------------- filtering


def project_of(key: str) -> str:
    """`KAFKA-15123` → `KAFKA`. Uppercased, so the allowlist check is case-insensitive."""
    return key.split("-", 1)[0].upper()


def filter_refs(
    refs: Iterable[Ref],
    *,
    allowlist: Collection[str] = ISSUE_PROJECT_ALLOWLIST,
    blacklist: Collection[str] = ISSUE_KEY_BLACKLIST,
) -> tuple[list[Ref], list[tuple[str, str]]]:
    """Keep the issue refs that can be real; return the rest with the reason they went.

    Only `kind="issue"` refs are filtered — a URL, a `KIP-N`, a `#N` or an `@user` carries
    its own evidence. The removed list is what the report turns into "what the regex
    thought was an issue key": `UTF-8`, `SHA-256`, `HTTP-2`, `AES-256`…
    """
    allowed = {p.upper() for p in allowlist}
    denied = {k.upper() for k in blacklist}
    kept: list[Ref] = []
    removed: list[tuple[str, str]] = []
    for ref in refs:
        if ref.kind != "issue":
            kept.append(ref)
            continue
        if ref.key.upper() in denied:
            removed.append((ref.key, BLACKLISTED))
        elif project_of(ref.key) not in allowed:
            removed.append((ref.key, NOT_ALLOWED))
        else:
            kept.append(ref)
    return kept, removed
