"""
Causal Platform MCP server.

Runs a Model Context Protocol server over stdio that exposes the platform's
causal-inference skills as tools. Point any MCP client at it:

    {
      "mcpServers": {
        "causal-platform": {
          "command": "python",
          "args": ["-m", "mcp_server.server"]
        }
      }
    }

Run standalone (stdio transport):

    cd backend && .venv/bin/python -m mcp_server.server

The tool *logic* lives in ``tools.py`` and is unit-tested without this transport.
This module is the thin MCP binding.

stdout belongs to the protocol. Over stdio every byte on stdout is parsed as a
JSON-RPC frame, so one stray ``print`` or a logger with a stdout handler (e.g.
structlog's default ``PrintLogger``) corrupts the session. ``main`` therefore
takes a private duplicate of fd 1 for protocol frames and points fd 1 itself at
stderr before any tool module is imported: whatever else writes to "stdout" --
Python code, C extensions, child processes -- lands on stderr instead.
``tests/integration/test_mcp_stdio.py`` holds this in place.
"""

from __future__ import annotations

import logging
import os
import sys
from io import TextIOWrapper

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server

INSTRUCTIONS = (
    "Causal inference as a tool. Use these to reason about cause and effect "
    "instead of correlation. Recommended flow: (1) causal_identification_check "
    "to confirm the effect is identifiable, (2) causal_effect_estimate for the "
    "ATE, (3) causal_sensitivity_analysis to test robustness to unmeasured "
    "confounding before acting. If identification returns UNIDENTIFIED, do not "
    "act on any point estimate. Tools marked heuristic (counterfactual_reason, "
    "intervention_estimate) return triage scores, not effects. This is the legacy "
    "server; packages/causal-mcp (console script causal-mcp) serves the causal-core "
    "kernel with typed outputs and a narrative verifier, and is the recommended one."
)

log = logging.getLogger("causal-platform-mcp")


def build_server() -> FastMCP:
    """Create the FastMCP instance and register every tool in ``CAUSAL_TOOLS``."""
    from mcp_server.tools import CAUSAL_TOOLS

    server = FastMCP("causal-platform", instructions=INSTRUCTIONS)
    # FastMCP derives the JSON schema from type hints and the description from
    # the docstring, so the wrappers in tools.py are the single source of truth.
    for name, fn in CAUSAL_TOOLS.items():
        server.tool(name=name)(fn)
    return server


def _claim_stdout_for_protocol() -> TextIOWrapper:
    """Return a private text stream on the real stdout; send fd 1 to stderr."""
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    return TextIOWrapper(os.fdopen(protocol_fd, "wb", buffering=0), encoding="utf-8")


def _configure_logging() -> None:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, stream=sys.stderr, force=True)
    try:
        import structlog

        structlog.configure(
            logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
            wrapper_class=structlog.make_filtering_bound_logger(level),
        )
    except ImportError:  # structlog is optional for the MCP binding
        pass


async def _serve_stdio(server: FastMCP, protocol_out: TextIOWrapper) -> None:
    # Same as FastMCP.run_stdio_async, except the protocol stream is passed in
    # explicitly instead of being re-read from sys.stdout.
    async with stdio_server(stdout=anyio.wrap_file(protocol_out)) as (read, write):
        low_level = server._mcp_server
        await low_level.run(read, write, low_level.create_initialization_options())


def main() -> None:
    """Entry point — runs the MCP server over stdio."""
    protocol_out = _claim_stdout_for_protocol()
    _configure_logging()
    server = build_server()
    log.warning(
        "causal-platform MCP server is DEPRECATED: use packages/causal-mcp (console script "
        "`causal-mcp`), which serves the causal-core kernel with typed outputs, the decision "
        "layer and a narrative verifier. See conformance/declared_not_implemented.json "
        "entry legacy-mcp-server."
    )
    log.info("causal-platform MCP server starting (stdio transport)")
    anyio.run(_serve_stdio, server, protocol_out)


if __name__ == "__main__":
    main()
