"""Registry entries for the connector tests.

The connectors read `sources.yaml` now, so the tests read it too: a test that hardcoded
`https://issues.apache.org/jira` would pass while the registry said something else, which
is exactly the failure the registry exists to prevent.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from brain.harvest.registry import SourceConfig, get_registry


def source(name: str, *, options: dict[str, Any] | None = None, **over: Any) -> SourceConfig:
    """The registry's entry for `name`, optionally with a few fields overridden.

    `options` is merged into the entry's own options (so a test that wants a page size of
    10 does not have to restate `fields`, `expand` and the rest).
    """
    config = get_registry().source(name)
    if options:
        config = replace(config, options={**config.options, **options})
    return replace(config, **over) if over else config
