"""
The risk score Guardian rule PR-002 reads, computed by the platform.

PR-002 used to read ``hallucination_risk_score`` from the request body, so a caller
could declare 0.0 for any output and obtain a signed APPROVED verdict. Nothing in the
platform has ever computed an "H-Neuron" score, and none is claimed here. What the
platform CAN check, deterministically, is whether generated prose says more than the
evidence it rests on:

* every number in it must be a faithful rounding of a claim in the kernel outcome it
  reports (``causal_core.claims.verify``), and
* causal wording ("caused", "will reduce", "root cause", ...) is allowed only when that
  outcome is a ``supported`` causal result.

The score is therefore a grounding score, not a probability:

    0.0  there is no narrative to score, or every number and causal phrase in it is
         backed by the outcome supplied with it
    1.0  something in the narrative is not backed: an unbound number, an unknown claim
         token, unlicensed causal wording, or an outcome that does not parse

A narrative with no outcome is scored against nothing, so any number or causal wording
in it is ungrounded. That is fail-closed on purpose: prose that quotes figures without
the result they came from is exactly what PR-002 exists to stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from causal_core.claims import CAUSAL_LANGUAGE, NUMBER, TOKEN, verify
from causal_core.contract import OUTCOME_ADAPTER
from pydantic import ValidationError

GROUNDED = 0.0
UNGROUNDED = 1.0


@dataclass(frozen=True)
class GroundingRisk:
    """The PR-002 score and, when it is not zero, why."""

    score: float
    reasons: list[str] = field(default_factory=list)


def grounding_risk(narrative: str | None, outcome: dict[str, Any] | None) -> GroundingRisk:
    """Score ``narrative`` against ``outcome`` (a kernel outcome, as ``analyze`` returns it)."""
    if not narrative or not narrative.strip():
        return GroundingRisk(GROUNDED)

    if outcome is None:
        body = TOKEN.sub(" ", narrative)
        reasons = []
        if TOKEN.search(narrative):
            reasons.append("claim tokens with no outcome to resolve them")
        numbers = [m.group(0) for m in NUMBER.finditer(body)]
        if numbers:
            reasons.append(f"numbers with no outcome to back them: {numbers}")
        causal = sorted({m.group(0).lower() for m in CAUSAL_LANGUAGE.finditer(body)})
        if causal:
            reasons.append(f"causal language with no outcome to support it: {causal}")
        return GroundingRisk(UNGROUNDED if reasons else GROUNDED, reasons)

    try:
        parsed = OUTCOME_ADAPTER.validate_python(outcome)
    except ValidationError as exc:
        return GroundingRisk(
            UNGROUNDED, [f"the outcome is not a kernel outcome: {exc.error_count()} error(s)"]
        )
    v = verify(narrative, parsed)
    return GroundingRisk(GROUNDED if v.ok else UNGROUNDED, v.reasons())
