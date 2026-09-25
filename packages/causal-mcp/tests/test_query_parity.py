"""The MCP tools take the kernel's query as the kernel defines it.

Walks CausalQuery.model_fields against each tool's input schema *as the server lists
it* (tools/list, through the SDK's own schema generation), and fails, naming the tool
and the field, when a field a tool should take is missing or differs from the contract
in type, default, constraints or description. The tools used to copy these fields by
hand; a missed copy made a capability unreachable over MCP (causal_decide could not
take treatment_levels, causal_impact could not re-certify an IV, front-door or proximal
answer) and a kept copy could describe a field differently from the kernel.

Which tools take which fields is not read from the code under test: the estimating
tools take every field (bar causal_mcp.query_params.NOT_OVER_MCP), and the identifying
tools take the fields identification actually reads, found by scanning the kernel's
source for ``query.<field>``.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import causal_core.identification
import pytest
from causal_core.contract import CausalQuery
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field, create_model

from causal_mcp import server, tools
from causal_mcp.query_params import IDENTIFICATION_FIELDS, NOT_OVER_MCP, takes_query

#: Tools that take no analysis query. A new tool must be put here or in expected_fields.
NO_QUERY = {"causal_check_graph", "causal_verify_narrative"}


def listed() -> dict[str, dict[str, Any]]:
    got = asyncio.run(server.build_server().list_tools())
    return {t.name: t.input_schema for t in got}


def _fields_read(sources: Iterable[Path], model: type[BaseModel]) -> set[str]:
    """Every ``query.<name>`` the given source reads, where <name> is a model field."""
    out: set[str] = set()
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "query"
                and node.attr in model.model_fields
            ):
                out.add(node.attr)
    return out


def identification_reads(model: type[BaseModel] = CausalQuery) -> set[str]:
    pkg = Path(causal_core.identification.__file__).parent
    return _fields_read(sorted(pkg.rglob("*.py")), model)


def expected_fields(model: type[BaseModel] = CausalQuery) -> dict[str, set[str]]:
    served = set(model.model_fields) - set(NOT_OVER_MCP)
    ident = identification_reads(model)
    out = {"causal_analyze": served, "causal_identify": ident}
    if tools.HAS_DECISION:
        import causal_decision.assumptions

        impact_src = Path(causal_decision.assumptions.__file__)
        out["causal_decide"] = served
        # impact() re-identifies under both graphs, and reads the query itself too.
        out["causal_impact"] = ident | _fields_read([impact_src], model)
    return out


def _inline(node: Any, defs: Mapping[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            rest = {k: v for k, v in node.items() if k != "$ref"}
            return {**_inline(defs[ref.removeprefix("#/$defs/")], defs), **_inline(rest, defs)}
        return {k: _inline(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def contract_schema(model: type[BaseModel]) -> dict[str, Any]:
    """What pydantic publishes for the contract's own fields, $refs resolved."""
    ref = create_model(  # type: ignore[call-overload]
        "ContractReference",
        **{name: (f.annotation, f) for name, f in model.model_fields.items()},
    )
    schema = ref.model_json_schema()
    return _inline(schema, schema.get("$defs", {}))  # type: ignore[no-any-return]


def parity_problems(
    schemas: Mapping[str, dict[str, Any]], model: type[BaseModel] = CausalQuery
) -> list[str]:
    reference = contract_schema(model)
    problems: list[str] = []
    expected = expected_fields(model)
    for tool in sorted(set(schemas) - set(expected) - NO_QUERY):
        problems.append(f"{tool}: unclassified -- add it to expected_fields or NO_QUERY")
    for tool, names in sorted(expected.items()):
        if tool not in schemas:
            problems.append(f"{tool}: not in the server's tool listing")
            continue
        schema = _inline(schemas[tool], schemas[tool].get("$defs", {}))
        props, required = schema["properties"], set(schema.get("required", ()))
        for name in sorted(names):
            if name not in props:
                problems.append(f"{tool}: CausalQuery.{name} is not in its input schema")
                continue
            want = reference["properties"][name]
            if props[name] != want:
                keys = sorted(
                    k for k in set(props[name]) | set(want) if props[name].get(k) != want.get(k)
                )
                problems.append(
                    f"{tool}: {name} differs from CausalQuery.{name} in {', '.join(keys)}: "
                    f"published {[props[name].get(k) for k in keys]!r}, "
                    f"contract {[want.get(k) for k in keys]!r}"
                )
            if (name in required) != (name in reference.get("required", ())):
                problems.append(f"{tool}: {name} required-ness differs from the contract")
        for name in sorted(set(NOT_OVER_MCP) & set(props)):
            problems.append(f"{tool}: {name} is published though NOT_OVER_MCP excludes it")
    return problems


# ── the check itself ─────────────────────────────────────────────────────────


def test_every_tool_takes_the_query_fields_it_should_as_the_contract_defines_them() -> None:
    schemas = listed()
    assert set(expected_fields()) <= set(schemas), "a query tool is missing from tools/list"
    assert parity_problems(schemas) == []


def test_the_identifying_tools_declare_what_identification_reads() -> None:
    """IDENTIFICATION_FIELDS is a declaration; this holds it to the kernel's source."""
    assert set(IDENTIFICATION_FIELDS) == identification_reads()
    assert {"treatment", "outcome", "adjust_for", "instruments"} <= identification_reads(), (
        "the source scan finds nothing: it would pass vacuously"
    )


def test_what_is_kept_off_mcp_is_a_real_field_with_a_reason() -> None:
    for name, reason in NOT_OVER_MCP.items():
        assert name in CausalQuery.model_fields, f"{name} left the contract; drop it here"
        assert len(reason) > 40, f"{name}: say why it is not served"


def test_the_published_parameters_carry_the_contracts_words() -> None:
    """Spot-check, independent of the comparison above: the words an agent reads."""
    props = listed()["causal_analyze"]["properties"]
    for name in ("washout", "cluster", "sequence", "segment_by"):
        assert props[name]["description"] == CausalQuery.model_fields[name].description
    number, _null = props["expected_treated_share"]["anyOf"]
    assert (number["exclusiveMinimum"], number["exclusiveMaximum"]) == (0, 1), "constraints too"


# ── negative controls: the check fails, and says where ───────────────────────


class Grown(CausalQuery):
    """The contract after it gains a field nobody copied into the tools."""

    carryover_lag: float | None = Field(
        default=None, ge=0, description="Time units a switch takes to wear off."
    )


class Reworded(CausalQuery):
    washout: float | None = Field(default=None, ge=0, description="A stale description.")


class Redefaulted(CausalQuery):
    estimand: Literal["ate", "att"] = Field(
        default="att", description=CausalQuery.model_fields["estimand"].description
    )


class Retyped(CausalQuery):
    segment_by: tuple[int, ...] = Field(  # type: ignore[assignment]
        default=(), description=CausalQuery.model_fields["segment_by"].description
    )


@pytest.mark.parametrize(
    ("model", "field", "says"),
    [
        (Grown, "carryover_lag", "CausalQuery.carryover_lag is not in its input schema"),
        (Reworded, "washout", "washout differs from CausalQuery.washout in description"),
        (Redefaulted, "estimand", "estimand differs from CausalQuery.estimand in default"),
        (Retyped, "segment_by", "segment_by differs from CausalQuery.segment_by in items"),
    ],
)
def test_a_contract_the_tools_do_not_match_fails_naming_field_and_tool(
    model: type[CausalQuery], field: str, says: str
) -> None:
    problems = parity_problems(listed(), model)
    estimating = ["causal_analyze"] + (["causal_decide"] if tools.HAS_DECISION else [])
    for tool in estimating:
        assert any(p.startswith(f"{tool}: ") and says in p for p in problems), (tool, problems)
    assert all(field in p for p in problems), problems


def test_a_tool_that_drops_a_field_fails() -> None:
    schemas = listed()
    del schemas["causal_impact" if tools.HAS_DECISION else "causal_identify"]["properties"][
        "instruments"
    ]
    problems = parity_problems(schemas)
    assert len(problems) == 1 and "CausalQuery.instruments is not in" in problems[0]


def test_a_new_contract_field_reaches_a_tool_without_an_edit() -> None:
    """The other half: derivation, not only detection. A tool declared against the grown
    contract publishes the new field, with its words, through the SDK's own listing."""

    @takes_query(model=Grown)
    def probe(graph: str, *, query: dict[str, Any]) -> dict[str, Any]:
        return {"graph": graph, **query}

    assert "carryover_lag" in inspect.signature(probe).parameters
    mcp = MCPServer("probe")
    mcp.add_tool(probe, name="probe")
    (tool,) = asyncio.run(mcp.list_tools())
    prop = tool.input_schema["properties"]["carryover_lag"]
    assert prop["description"] == "Time units a switch takes to wear off."
    assert prop["anyOf"][0]["minimum"] == 0
    assert probe("A -> B", treatment="A", outcome="B", carryover_lag=3.0)["carryover_lag"] == 3.0
