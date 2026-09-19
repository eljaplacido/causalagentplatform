"""Intervention triage score for agent recommendations -- a heuristic, not an estimate.

This skill ranks candidate interventions when no observational data are
available. Its number is a confidence-weighted score over one proposed edge. It
is *not* a causal effect: there are no data behind it and no identification.
Every output says so (``heuristic: True``, ``caveat``), and no field is named
like an estimate. For an effect estimate, call ``effect_estimate`` with data, or
the causal-core kernel directly.
"""

from __future__ import annotations

from typing import Any

CAVEAT = (
    "Heuristic triage score from the proposed edge's confidence; not a causal effect "
    "estimate (no data, no identification). Estimate the effect with data before acting."
)


async def intervention_estimate(
    *,
    source_id: str,
    target_id: str,
    edge_type: str,
    confidence: float,
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score a proposed intervention on one edge, for ranking only."""
    c = max(0.0, min(1.0, float(confidence)))
    names = {str(a.get("assumption_type", "")) for a in assumptions or []}
    # The score is the edge confidence, discounted when nothing is assumed about
    # confounding: a ranking aid, calibrated against nothing.
    score = round(c * (1.0 if "IGNORABILITY" in names else 0.8), 4)
    if c >= 0.8:
        risk_tier = "LOW"
    elif c >= 0.55:
        risk_tier = "MEDIUM"
    else:
        risk_tier = "HIGH"
    return {
        "heuristic": True,
        "heuristic_score": score,
        "caveat": CAVEAT,
        "source_id": source_id,
        "target_id": target_id,
        "edge_type": edge_type,
        "risk_tier": risk_tier,
        "action": (
            f"Investigate and mitigate the suspected {edge_type.lower()} path "
            f"from {source_id} to {target_id}. Requires human approval before execution."
        ),
    }
