"""One retrieval library, two surfaces (plan decision 1).

Everything here is callable from Python — which is what the Plan 3 evaluation needs to run
each strategy against each question under identical conditions — and every one of these
functions becomes an MCP tool of the same name in Task 3. There is no retrieval logic in
the server; the server is an adapter.
"""

from brain.retrieve.context import RetrieveContext
from brain.retrieve.explain import explain_edge
from brain.retrieve.graph_vector import search_with_context
from brain.retrieve.hybrid import search_chunks
from brain.retrieve.impact import impact
from brain.retrieve.local import local_search
from brain.retrieve.lookup import lookup
from brain.retrieve.route import route
from brain.retrieve.runner import ask, render
from brain.retrieve.temporal import assignees_over_time, changes_between, status_at, timeline
from brain.retrieve.types import Item, Provenance, Result, RetrieveError

__all__ = [
    "Item",
    "Provenance",
    "Result",
    "RetrieveContext",
    "RetrieveError",
    "ask",
    "assignees_over_time",
    "changes_between",
    "explain_edge",
    "impact",
    "local_search",
    "lookup",
    "render",
    "route",
    "search_chunks",
    "search_with_context",
    "status_at",
    "timeline",
]
