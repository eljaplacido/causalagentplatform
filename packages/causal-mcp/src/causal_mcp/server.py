"""
causal-mcp: the causal-core contract as an MCP server (SDK v2).

    causal-mcp                      # stdio (default): point any MCP client at it
    causal-mcp --http --port 8765   # stateless streamable HTTP (needs the [http] extra)

Tools return typed structured output (``outputSchema`` is the contract), and
all are annotated read-only. The contract's JSON Schemas are also served as
resources under ``causal://schemas/<name>``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from typing import Any

import causal_core as cc
from causal_core.contract import CONTRACT_VERSION, json_schemas
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from causal_mcp import tools

INSTRUCTIONS = f"""\
Causal inference as tools (causal-core {cc.__version__}, contract {CONTRACT_VERSION}).

The LLM proposes; statistics decide. Recommended flow:
1. causal_check_graph -- draft the causal graph with the user; review its testable implications.
2. causal_identify -- confirm the effect is identifiable before asking for data.
3. causal_analyze -- estimate, or receive a typed refusal (a correct answer, not an error).
4. causal_verify_narrative -- check your prose before replying: numbers must be claims, and
   causal wording is allowed only for a `supported` result.
When the question is whether to *do* something, use causal_decide instead of steps 3-4: it
analyses and rules in one call, so the evidence cannot change between the two, and it returns
a verdict (act, act_with_approval, experiment, refuse) with an explanation you can relay.
Never state a causal effect that no tool returned, and never act on your own reading of an
estimate: the verdict is the tool's, not yours. If the outcome is a refusal, say so and
relay its `missing_information`.
"""

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

TOOLS: dict[str, tuple[Callable[..., Any], str]] = {
    "causal_analyze": (tools.causal_analyze, "Estimate a causal effect, or refuse with a reason"),
    "causal_identify": (tools.causal_identify, "Check identifiability from the graph alone"),
    "causal_check_graph": (tools.causal_check_graph, "Validate a graph and list its implications"),
    "causal_verify_narrative": (
        tools.causal_verify_narrative,
        "Verify prose against an outcome (claims and causal language)",
    ),
}

# The decision layer is optional (pip install causal-mcp[decision]). Without it the server
# still serves evidence; it just will not rule on whether an action is justified.
if tools.HAS_DECISION:
    TOOLS["causal_decide"] = (
        tools.causal_decide,
        "Decide whether an action is justified: act, approve, experiment or refuse",
    )


def build_server() -> MCPServer:
    server = MCPServer(
        "causal-core",
        title="Causal Platform kernel",
        instructions=INSTRUCTIONS,
        version=cc.__version__,
    )
    for name, (fn, title) in TOOLS.items():
        server.add_tool(fn, name=name, title=title, annotations=READ_ONLY, structured_output=True)

    schemas = json_schemas()

    def _schema_reader(name: str):  # type: ignore[no-untyped-def]
        def read() -> str:
            return json.dumps(schemas[name], indent=2, sort_keys=True)

        read.__name__ = f"schema_{name}"
        return read

    for name in schemas:
        server.resource(
            f"causal://schemas/{name}",
            name=f"schema-{name}",
            description=f"JSON Schema of the causal claim contract {CONTRACT_VERSION}: {name}",
            mime_type="application/schema+json",
        )(_schema_reader(name))
    return server


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="causal-mcp", description="causal-core as an MCP server")
    ap.add_argument(
        "--http", action="store_true", help="stateless streamable HTTP instead of stdio"
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    server = build_server()
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port, stateless_http=True)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
