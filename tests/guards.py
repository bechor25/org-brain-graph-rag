"""The exception the no-real-connections guard raises.

It lives here rather than in `conftest.py` because pytest imports the root conftest as the
top-level module `conftest`, while a test importing it writes `tests.conftest` — two module
objects, two classes, and a `pytest.raises` that never matches. One module, one class.
"""

from __future__ import annotations


class ForbiddenConnection(RuntimeError):
    """A test that is not marked `live` or `network` tried to reach a real service."""
