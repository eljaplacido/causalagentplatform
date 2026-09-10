"""
End-to-end demo: an external agent talks to the causal-platform MCP server.

Run from ``backend/``:

    .venv/bin/python -m mcp_server.examples.client_demo

What it does:
  1. Spawns ``python -m mcp_server.server`` as a stdio subprocess.
  2. Lists the tools the server advertises.
  3. Calls ``causal_identification_check`` against an APPROVED toy fragment.
  4. Calls ``causal_effect_estimate`` against the same fragment with data.
  5. Prints each response as JSON.

This is the contract an external Claude/agent will see — keep it small and
boring on purpose. If you want to call the wrappers without stdio (e.g. in
unit tests), import them directly from ``mcp_server.tools``.
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Toy approved fragment. IDs are kept ≤8 chars because the effect_estimate
# skill truncates names to 8 characters when matching DataFrame columns —
# longer IDs would silently mismatch the column lookup.
FRAGMENT = {
    "approval_status": "APPROVED",
    "edges": [
        {
            "source": {"id": "tx", "object_type": "Treatment"},
            "target": {"id": "oy", "object_type": "Outcome"},
            "confidence": 0.8,
            "edge_type": "CAUSES",
        }
    ],
}

DATA = {
    "tx": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0],
    "oy": [0.2, 0.9, 0.1, 1.1, 0.3, 1.0, 0.0, 1.2],
}


def _dump(label: str, payload: object) -> None:
    print(f"\n── {label} " + "─" * (70 - len(label)))
    print(json.dumps(payload, indent=2, default=str))


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
    )

    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        _dump("tools advertised", [t.name for t in tools.tools])

        ident = await session.call_tool(
            "causal_identification_check",
            arguments={
                "fragment": FRAGMENT,
                "assumptions": [
                    {
                        "assumption_type": "IGNORABILITY",
                        "statement": "no unobserved confounders",
                    }
                ],
            },
        )
        _dump("identification_check", [c.model_dump() for c in ident.content])

        estimate = await session.call_tool(
            "causal_effect_estimate",
            arguments={
                "fragment": FRAGMENT,
                "identification_strategy": "backdoor",
                "data": DATA,
            },
        )
        _dump("effect_estimate", [c.model_dump() for c in estimate.content])


if __name__ == "__main__":
    asyncio.run(main())
