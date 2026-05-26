"""
Causal Platform — MCP integration layer.

Exposes the platform's causal-inference skills as Model Context Protocol tools so
that *any* MCP-capable agent (Claude, an internal LangGraph node, a third-party
assistant) can call causal reasoning as a capability — without importing the
platform's internals or running its full session graph.

This is the modular "integrate causal inference into agentic systems" surface.
"""

from mcp_server.tools import CAUSAL_TOOLS

__all__ = ["CAUSAL_TOOLS"]
