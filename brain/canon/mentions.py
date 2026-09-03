"""Deterministic cross-reference extraction (spec §2.2). Runs before any LLM.

Order of results is first-appearance order; duplicates removed.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from brain.canon.models import Ref

_ISSUE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d+)\b")
_KIP = re.compile(r"\bKIP-(\d+)\b", re.IGNORECASE)
_PR = re.compile(r"(?<![\w/#])#(\d{1,7})\b")
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_USER = re.compile(r"(?<![\w.])@([A-Za-z0-9_.-]+)")

_URL_ISSUE = re.compile(r"/browse/([A-Z][A-Z0-9]{1,9}-\d+)")
_URL_KIP = re.compile(r"KIP-(\d+)", re.IGNORECASE)
_URL_PR = re.compile(r"/pull/(\d+)")


def _classify_url(url: str) -> Ref:
    u = unquote(url)
    if m := _URL_ISSUE.search(u):
        return Ref(kind="issue", key=m.group(1))
    if "confluence" in u and (m := _URL_KIP.search(u)):
        return Ref(kind="kip", key=f"KIP-{m.group(1)}")
    if "github.com" in u and (m := _URL_PR.search(u)):
        return Ref(kind="pr", key=m.group(1))
    return Ref(kind="url", key=url.rstrip(".,;"))


def extract_refs(text: str) -> list[Ref]:
    if not text:
        return []
    found: list[tuple[int, Ref]] = []

    urls = list(_URL.finditer(text))
    for m in urls:
        found.append((m.start(), _classify_url(m.group(0))))
    # mask URLs so their inner tokens are not re-matched as keys
    masked = _URL.sub(lambda m: " " * len(m.group(0)), text)

    for m in _ISSUE.finditer(masked):
        if m.group(1).upper().startswith("KIP-"):
            continue
        found.append((m.start(), Ref(kind="issue", key=m.group(1))))
    for m in _KIP.finditer(masked):
        found.append((m.start(), Ref(kind="kip", key=f"KIP-{m.group(1)}")))
    for m in _PR.finditer(masked):
        found.append((m.start(), Ref(kind="pr", key=m.group(1))))
    for m in _USER.finditer(masked):
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
