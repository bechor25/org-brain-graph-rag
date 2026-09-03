"""Typed JSONL helpers for canonical files.

Writes go through temp+rename (carryover from Plan 0): a canonical file is either the
previous complete run or the new complete run, never a half-written mixture that the
next step would happily read and validate.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def read_jsonl(path: Path, model: type[T]) -> Iterator[T]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield model.model_validate_json(line)
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"{path}:{line_no}: {e}") from e


def write_jsonl(path: Path, records: Iterable[BaseModel]) -> int:
    """Write records as JSONL atomically. Returns the number of lines written.

    The rename is atomic within the directory, so a crash mid-write leaves the previous
    file intact. The directory entry itself is not fsynced — a power cut (not a process
    crash) can still lose the rename, and the fix is to re-run the step.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.model_dump(mode="json", by_alias=True), ensure_ascii=False) + "\n")
            n += 1
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    return n
