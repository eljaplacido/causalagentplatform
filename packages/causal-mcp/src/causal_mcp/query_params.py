"""
The analysis parameters the MCP tools take, derived from the kernel's contract.

Every CausalQuery field a tool takes is published in that tool's input schema with
the contract's own type, default, constraints and description: `causal_core.contract`
is the single source. The tools used to copy each field into their signatures by hand,
and a field the kernel gained (as the switchback fields did in contract 0.9.0) had to
be added to every copy; a copy that was missed made a capability unreachable over MCP
or described it wrongly. Now a new CausalQuery field reaches `causal_analyze` and
`causal_decide` without an edit here, and `tests/test_query_parity.py` checks the
published schemas against the contract, as the server lists them.

The one place a field is kept off MCP is `NOT_OVER_MCP`, with the reason.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, TypeVar

from causal_core.contract import CausalQuery
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

R = TypeVar("R")

#: CausalQuery fields no MCP tool takes, and why. Everything else is served.
NOT_OVER_MCP: Mapping[str, str] = {
    "rung": "The kernel answers interventional questions only: analyze refuses any other rung "
    "with RUNG_MISMATCH, and every tool here asks an interventional question. Exposing it "
    "would offer an agent a parameter whose only effect is a refusal.",
}

#: The CausalQuery fields identification reads (causal_core.identification). The tools that
#: only identify -- causal_identify, and causal_impact, which re-identifies under both graphs
#: -- take these and no others: an estimation-only field there would be accepted and ignored.
IDENTIFICATION_FIELDS: tuple[str, ...] = (
    "treatment",
    "outcome",
    "design",
    "adjust_for",
    "instruments",
    "treatment_proxies",
    "outcome_proxies",
)


def query_fields(
    only: Sequence[str] | None = None, model: type[BaseModel] = CausalQuery
) -> dict[str, FieldInfo]:
    """The contract fields a tool takes, in the contract's order.

    `only` restricts them (for a tool that uses part of the query); by default a tool
    takes every field not in NOT_OVER_MCP, including any the contract gains later.
    """
    fields = model.model_fields
    if only is not None:
        unknown = sorted(set(only) - set(fields))
        if unknown:
            raise ValueError(f"not {model.__name__} fields: {', '.join(unknown)}")
    return {
        name: field
        for name, field in fields.items()
        if name not in NOT_OVER_MCP and (only is None or name in only)
    }


def _parameter(name: str, field: FieldInfo) -> inspect.Parameter:
    if field.default_factory is not None:  # pragma: no cover - the contract uses none
        raise TypeError(f"CausalQuery.{name} has a default_factory; give it a plain default")
    # Constraints (gt, ge, ...) live in field.metadata; the description rides in a Field
    # without a default, which Annotated does not allow -- the default is the parameter's.
    described = Field(description=field.description)
    annotation: Any = Annotated[(field.annotation, *field.metadata, described)]
    default = inspect.Parameter.empty if field.is_required() else field.default
    return inspect.Parameter(
        name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation, default=default
    )


def takes_query(
    only: Sequence[str] | None = None, model: type[BaseModel] = CausalQuery
) -> Callable[[Callable[..., R]], Callable[..., R]]:
    """Publish a tool's CausalQuery parameters from the contract.

    The decorated function declares its own parameters plus a keyword-only
    ``query: dict[str, Any]``, which receives the query fields by name, ready for
    ``CausalQuery(**query)``. Over MCP the SDK validates the published types and bounds
    before the tool runs; a direct caller's bad value reaches the tool, which builds the
    query inside its own error handling and refuses. The published signature -- what the MCP SDK
    builds the input schema from, and what a direct caller sees -- is the tool's
    required parameters, the contract's required fields, the tool's optional
    parameters, then the contract's optional fields.
    """
    fields = query_fields(only, model)

    def decorate(fn: Callable[..., R]) -> Callable[..., R]:
        declared = inspect.signature(fn, eval_str=True)
        own = [p for p in declared.parameters.values() if p.name != "query"]
        clash = sorted({p.name for p in own} & set(fields))
        if clash:
            raise TypeError(f"{fn.__name__} declares query fields itself: {', '.join(clash)}")
        query_params = [_parameter(n, f) for n, f in fields.items()]

        def required(p: inspect.Parameter) -> bool:
            return p.default is inspect.Parameter.empty

        published = inspect.Signature(
            [p for p in own if required(p)]
            + [p for p in query_params if required(p)]
            + [p for p in own if not required(p)]
            + [p for p in query_params if not required(p)],
            return_annotation=declared.return_annotation,
        )

        @functools.wraps(fn)
        def tool(*args: Any, **kwargs: Any) -> R:
            bound = published.bind(*args, **kwargs)
            bound.apply_defaults()
            given = bound.arguments
            query = {n: given[n] for n in fields}
            return fn(**{k: v for k, v in given.items() if k not in fields}, query=query)

        tool.__signature__ = published  # type: ignore[attr-defined]
        tool.__annotations__ = {p.name: p.annotation for p in published.parameters.values()}
        tool.__annotations__["return"] = published.return_annotation
        return tool

    return decorate
