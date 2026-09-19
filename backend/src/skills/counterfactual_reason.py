"""Counterfactual triage summary for agent flows -- a heuristic, not a counterfactual.

A counterfactual ("what would have happened without X?") needs a structural
causal model fitted to data. This skill has neither: it summarizes the
confidences of proposed edges for triage. It therefore reports
``identification_status: NOT_ASSESSED``, labels itself heuristic, and its
narrative makes no quantitative or causal claim. Earlier versions reported
"IDENTIFIED" whenever an edge's confidence was >= 0.75 and wrote invented
impact-reduction percentages into the narrative; both are gone.
"""

from __future__ import annotations

from typing import Any

CAVEAT = (
    "Heuristic summary of proposed-edge confidences. No structural model and no data: "
    "not a counterfactual estimate. Use unit-level counterfactuals only once they are "
    "computed from a fitted structural causal model."
)


async def counterfactual_reason(
    *,
    proposed_edges: list[dict[str, Any]],
    query: str,
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Summarize proposed edges for a counterfactual question, without answering it."""
    names = [str(a.get("assumption_type", "")).strip() for a in assumptions or []]
    names = [n for n in names if n]
    if not proposed_edges:
        return {
            "heuristic": True,
            "caveat": CAVEAT,
            "heuristic_summary": {},
            "identification_status": "NOT_ASSESSED",
            "sensitivity_labels": ["insufficient_structure"],
            "assumptions": names,
            "narrative": "Insufficient causal structure to discuss this counterfactual.",
        }
    confidences = [max(0.0, min(1.0, float(e.get("confidence", 0.0)))) for e in proposed_edges]
    strongest = max(confidences)
    labels = ["graph_heuristic", "no_structural_model"]
    if strongest < 0.6:
        labels.append("high_structural_uncertainty")
    if not names:
        labels.append("limited_assumptions")
    narrative = (
        f"Counterfactual query: '{query}'. Not answered: this needs a structural causal model "
        f"fitted to data, and none is available. For triage, the strongest of "
        f"{len(proposed_edges)} proposed edge(s) has confidence {strongest:.2f}."
    )
    return {
        "heuristic": True,
        "caveat": CAVEAT,
        "heuristic_summary": {
            "max_edge_confidence": round(strongest, 4),
            "mean_edge_confidence": round(sum(confidences) / len(confidences), 4),
            "edge_count": len(proposed_edges),
        },
        "identification_status": "NOT_ASSESSED",
        "sensitivity_labels": labels,
        "assumptions": names,
        "narrative": narrative,
    }
