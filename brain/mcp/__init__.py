"""The MCP surface of the retrieval library (spec §4.3, plan decision 1).

Nothing here retrieves anything. `brain/retrieve/` is the library; this package is the
adapter that lets an asking agent call it over stdio or HTTP, and the evaluation harness
call the very same functions from Python. If a rule about how a tool behaves lives only in
this package, it is in the wrong package.
"""

from brain.mcp.server import TOOL_NAMES, build_server, mcp

__all__ = ["TOOL_NAMES", "build_server", "mcp"]
