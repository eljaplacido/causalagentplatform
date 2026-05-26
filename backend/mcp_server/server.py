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
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from mcp_server.tools import (
    tool_causal_structure_compare,
    tool_counterfactual_reason,
    tool_drift_detect,
    tool_effect_estimate,
    tool_identification_check,
    tool_intervention_estimate,
    tool_sensitivity_analysis,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("causal-platform-mcp")

mcp = FastMCP(
    "causal-platform",
    instructions=(
        "Causal inference as a tool. Use these to reason about cause and effect "
        "instead of correlation. Recommended flow: (1) causal_identification_check "
        "to confirm the effect is identifiable, (2) causal_effect_estimate for the "
        "ATE, (3) causal_sensitivity_analysis to test robustness to unmeasured "
        "confounding before acting. If identification returns UNIDENTIFIED, do not "
        "act on any point estimate."
    ),
)

# Register each wrapper. FastMCP derives the JSON schema from type hints and the
# tool description from the docstring, so the wrappers in tools.py are the
# single source of truth.
mcp.tool(name="causal_effect_estimate")(tool_effect_estimate)
mcp.tool(name="causal_counterfactual_reason")(tool_counterfactual_reason)
mcp.tool(name="causal_identification_check")(tool_identification_check)
mcp.tool(name="causal_sensitivity_analysis")(tool_sensitivity_analysis)
mcp.tool(name="causal_drift_detect")(tool_drift_detect)
mcp.tool(name="causal_intervention_estimate")(tool_intervention_estimate)
mcp.tool(name="causal_structure_compare")(tool_causal_structure_compare)


def main() -> None:
    """Entry point — runs the MCP server over stdio."""
    log.info("causal-platform MCP server starting (stdio transport, 7 tools)")
    mcp.run()


if __name__ == "__main__":
    main()
