"""Typed JSONL helpers for canonical files."""

from __future__ import annotations

import json
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
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.model_dump(mode="json", by_alias=True), ensure_ascii=False) + "\n")
            n += 1
    return n
