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
import inspect
import json
import logging
import sys
from collections.abc import Callable
from typing import Any

import causal_core as cc
from causal_core.contract import CONTRACT_VERSION, json_schemas
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from causal_mcp import __version__, tools

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
When the graph behind an earlier answer changes, call causal_impact with the old and new graph
and the adjustment set the earlier analysis used: it says whether that answer still stands.
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
    TOOLS["causal_impact"] = (
        tools.causal_impact,
        "Does a revised graph break an earlier estimate? holds / invalidated / unverified",
    )


#: JSON Schema annotation keywords: they document a value and constrain nothing.
_ANNOTATIONS = frozenset({"description", "title", "default", "examples"})


def _strip_prose(node: Any) -> Any:
    """The schema without its annotation keywords. A property that happens to be *named*
    like one (a model field called ``title``) is structure, and stays."""
    if isinstance(node, dict):
        return {
            k: (
                {name: _strip_prose(sub) for name, sub in v.items()}
                if k in ("properties", "$defs") and isinstance(v, dict)
                else _strip_prose(v)
            )
            for k, v in node.items()
            if k not in _ANNOTATIONS
        }
    if isinstance(node, list):
        return [_strip_prose(v) for v in node]
    return node


def _merge_type_unions(node: Any) -> Any:
    """``{"anyOf": [{"type": "number"}, {"type": "null"}]}`` as ``{"type": ["number", "null"]}``.

    The two are the same schema (JSON Schema 2020-12, section 6.1.1): a value is valid under
    one exactly when it is valid under the other. Pydantic writes every optional scalar the
    long way, and the output schemas carry a few hundred of them.
    """
    if isinstance(node, dict):
        out = {
            k: (
                {name: _merge_type_unions(sub) for name, sub in v.items()}
                if k in ("properties", "$defs") and isinstance(v, dict)
                else _merge_type_unions(v)
            )
            for k, v in node.items()
        }
        branches = out.get("anyOf")
        if (
            isinstance(branches, list)
            and len(branches) > 1
            and "type" not in out
            and all(
                isinstance(b, dict) and set(b) == {"type"} and isinstance(b["type"], str)
                for b in branches
            )
        ):
            out.pop("anyOf")
            out["type"] = [b["type"] for b in branches]
        return out
    if isinstance(node, list):
        return [_merge_type_unions(v) for v in node]
    return node


def _drop_implied_types(node: Any) -> Any:
    """``{"enum": ["a", "b"], "type": "string"}`` as ``{"enum": ["a", "b"]}``; the same for
    ``const``.

    An ``enum`` (or ``const``) admits exactly the values it lists (JSON Schema 2020-12, section
    6.1.2), so when every listed value is a string, ``"type": "string"`` excludes nothing more:
    the two schemas accept the same values. Every enumeration and discriminator in the
    contract is written with both (contract 0.12.0, when the Rosenbaum fields pushed the listing
    past its budget).
    """
    if isinstance(node, dict):
        out = {
            k: (
                {name: _drop_implied_types(sub) for name, sub in v.items()}
                if k in ("properties", "$defs") and isinstance(v, dict)
                else _drop_implied_types(v)
            )
            for k, v in node.items()
        }
        values = out.get("enum", [out["const"]] if "const" in out else None)
        if (
            out.get("type") == "string"
            and isinstance(values, list)
            and values
            and all(isinstance(v, str) for v in values)
        ):
            out.pop("type")
        return out
    if isinstance(node, list):
        return [_drop_implied_types(v) for v in node]
    return node


def _slim_output_schema(server: MCPServer, name: str) -> None:
    """Drop annotations (descriptions, titles, defaults) from a tool's OUTPUT schema; keep
    them on its input.

    The Python SDK's stdio client reads each message as one line with a 64 KiB limit, and
    the tool listing is one message. The output schemas embed the whole outcome contract --
    causal_decide's alone was 34 KB -- so the listing reached 62.9 KB with five tools and
    the sixth pushed it over, failing every call from such a client. The structure stays
    (clients validate against it); the annotations are what the causal://schemas resources
    and conformance/causal-claims already publish in full. Defaults went too when the input
    schemas began carrying the contract's parameter descriptions (septa-4): a default in an
    output schema validates nothing. Input schemas keep every word, because that is what an
    agent reads to call a tool correctly. Unions of bare types are written as one type list
    (contract 0.10.0, when two panel fields pushed the listing past its budget again): the
    same schema in fewer bytes; so is a string enumeration without its implied
    ``"type": "string"`` (contract 0.12.0).
    """
    tool = server._tool_manager.get_tool(name)  # private: mcp 2.2 exposes no setter
    if tool is not None and tool.output_schema is not None:
        tool.__dict__["output_schema"] = _drop_implied_types(
            _merge_type_unions(_strip_prose(tool.output_schema))
        )


def build_server() -> MCPServer:
    server = MCPServer(
        "causal-core",
        title="Causal Platform kernel",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    for name, (fn, title) in TOOLS.items():
        server.add_tool(
            fn,
            name=name,
            title=title,
            # Without its source indentation: prose to an agent, and bytes in a size-capped listing.
            description=inspect.cleandoc(fn.__doc__ or ""),
            annotations=READ_ONLY,
            structured_output=True,
        )
        _slim_output_schema(server, name)

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
