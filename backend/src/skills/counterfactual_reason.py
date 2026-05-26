"""Bounded counterfactual reasoning helper for agent flows."""

from __future__ import annotations

from typing import Any


async def counterfactual_reason(
    *,
    proposed_edges: list[dict[str, Any]],
    query: str,
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce a bounded counterfactual summary from proposed edge structure."""
    if not proposed_edges:
        return {
            "estimated_distribution": {},
            "identification_status": "NOT_IDENTIFIED",
            "sensitivity_labels": ["insufficient_structure"],
            "assumptions": [],
            "narrative": "Insufficient causal structure for counterfactual analysis.",
        }

    confidences = [
        max(0.0, min(1.0, float(edge.get("confidence", 0.0))))
        for edge in proposed_edges
    ]
    strongest = max(confidences, default=0.0)
    mean_strength = sum(confidences) / len(confidences)
    reduction_low = round(max(0.0, strongest * 0.45), 4)
    reduction_high = round(min(1.0, strongest * 0.85 + 0.05), 4)

    assumption_names = [str(item.get("assumption_type", "")).strip() for item in assumptions or []]
    assumption_names = [item for item in assumption_names if item]
    identification_status = "IDENTIFIED" if strongest >= 0.75 else "PARTIALLY_IDENTIFIED"
    sensitivity_labels = ["graph_heuristic"]
    if strongest < 0.6:
        sensitivity_labels.append("high_structural_uncertainty")
    if not assumption_names:
        sensitivity_labels.append("limited_assumptions")

    narrative = (
        f"Counterfactual query: '{query}'. Removing or mitigating the leading proposed cause "
        f"would be expected to reduce incident impact by roughly {reduction_low:.0%} "
        f"to {reduction_high:.0%} under the current graph assumptions. "
        "This is a bounded graph-heuristic estimate over "
        f"{len(proposed_edges)} candidate path(s), not an observationally identified "
        "production estimate."
    )

    return {
        "estimated_distribution": {
            "impact_reduction_low": reduction_low,
            "impact_reduction_high": reduction_high,
            "mean_path_strength": round(mean_strength, 4),
        },
        "identification_status": identification_status,
        "sensitivity_labels": sensitivity_labels,
        "assumptions": assumption_names,
        "narrative": narrative,
    }
