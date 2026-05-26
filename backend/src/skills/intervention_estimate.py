"""Shared intervention estimation helper for agent recommendations.

This skill reuses the platform's ``effect_estimate`` implementation to avoid
empty or confidence-only placeholders in operational agent flows.
"""

from __future__ import annotations

from typing import Any

from src.skills.effect_estimate import effect_estimate


async def intervention_estimate(
    *,
    source_id: str,
    target_id: str,
    edge_type: str,
    confidence: float,
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Estimate intervention leverage from graph structure when data is absent."""
    fragment = {
        "approval_status": "APPROVED",
        "edges": [
            {
                "source": {"id": source_id, "object_type": "Treatment"},
                "target": {"id": target_id, "object_type": "Outcome"},
                "edge_type": edge_type,
                "confidence": confidence,
            }
        ],
    }

    estimate = await effect_estimate(
        fragment=fragment,
        identification_strategy="backdoor",
        assumptions=assumptions or [],
        data=None,
    )

    signed_effect = float(estimate.get("ate") or 0.0)
    estimated_effect = round(abs(signed_effect), 4)
    ci = estimate.get("confidence_interval", [0.0, 0.0])
    interval_width = abs(float(ci[1]) - float(ci[0])) if None not in ci else 1.0

    if confidence >= 0.8 and interval_width <= 0.7:
        risk_tier = "LOW"
    elif confidence >= 0.55 and interval_width <= 1.0:
        risk_tier = "MEDIUM"
    else:
        risk_tier = "HIGH"

    return {
        **estimate,
        "estimated_effect": estimated_effect,
        "signed_effect": signed_effect,
        "risk_tier": risk_tier,
        "action": (
            f"Investigate and mitigate the suspected {edge_type.lower()} path "
            f"from {source_id} to {target_id}. Requires human approval before execution."
        ),
    }
