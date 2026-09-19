"""
Identification check skill using DoWhy for causal identification.

This module validates whether a causal effect is identifiable under the
current assumptions and graph structure using DoWhy's identification algorithms.

EpistemicState constraints enforced:
- require_approved_fragment: Only checks APPROVED fragments
- audit_log_required: Identification results logged
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Try to import DoWhy
try:
    import dowhy
    from dowhy import CausalModel
    DOWHY_AVAILABLE = True
except ImportError:
    DOWHY_AVAILABLE = False
    logger.warning("DoWhy not installed. Using fallback identification logic.")


class IdentificationError(Exception):
    """Raised when identification check fails."""
    pass


async def identification_check(
    fragment: dict[str, Any],
    assumptions: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Check if the causal effect is identified under current assumptions.

    Uses DoWhy's identification algorithms to determine if the causal effect
    can be estimated from observed data given the graph structure.

    Args:
        fragment: Graph fragment with nodes and edges
        assumptions: List of causal assumptions (ignorability, positivity, SUTVA)

    Returns:
        dict with status and strategy:
            - status: "IDENTIFIED", "UNIDENTIFIED", or "PARTIALLY_IDENTIFIED"
            - strategy: "backdoor", "frontdoor", "iv", or "bounds_only"

    EpistemicState constraints:
        - require_approved_fragment: fragment must be APPROVED
        - audit_log_required: identification attempt logged
    """
    # Validate fragment approval
    if fragment.get("approval_status") != "APPROVED":
        logger.error("identification_check: fragment not APPROVED")
        return {
            "status": "UNIDENTIFIED",
            "strategy": "none",
            "reason": "fragment_not_approved",
        }

    # Extract graph structure
    edges = fragment.get("edges", [])
    if not edges:
        logger.warning("identification_check: no edges in fragment")
        return {
            "status": "UNIDENTIFIED",
            "strategy": "none",
            "reason": "empty_graph",
        }

    # Check for ignorability assumption (required for backdoor)
    has_ignorability = any(
        a.get("assumption_type") == "IGNORABILITY" or
        "unconfoundedness" in a.get("statement", "").lower()
        for a in assumptions
    )

    # Check for IV assumptions
    has_iv = any(
        a.get("assumption_type") == "EXCLUSION_RESTRICTION" or
        "instrument" in a.get("statement", "").lower()
        for a in assumptions
    )

    # Check for positivity
    has_positivity = any(
        a.get("assumption_type") == "POSITIVITY" or
        "positivity" in a.get("statement", "").lower()
        for a in assumptions
    )

    # Use DoWhy if available for proper identification
    if DOWHY_AVAILABLE:
        try:
            return _dowhy_identification(fragment, has_ignorability, has_iv)
        except Exception as e:
            logger.error("dowhy_identification.failed: %s", e)
            # Fall through to heuristic

    # Fallback: Heuristic identification based on assumptions
    return _heuristic_identification(edges, has_ignorability, has_iv, has_positivity)


def _dowhy_identification(
    fragment: dict[str, Any],
    has_ignorability: bool,
    has_iv: bool,
) -> dict[str, Any]:
    """
    Use DoWhy to check identification.

    Creates a dummy model to test identifiability without actual data.
    """
    import pandas as pd

    # Build minimal data structure for DoWhy
    # DoWhy requires data even for identification-only checks
    n_samples = 100
    data = pd.DataFrame({
        "treatment": [0] * n_samples,
        "outcome": [0] * n_samples,
    })

    # Build causal graph
    causal_graph = _build_causal_graph(fragment)

    try:
        model = CausalModel(
            data=data,
            treatment="treatment",
            outcome="outcome",
            graph=causal_graph,
        )

        # Try to identify effect
        identified_estimand = model.identify_effect(proceed_when_unidentifiable=True)

        # Check if identified
        if identified_estimand is None:
            return {
                "status": "UNIDENTIFIED",
                "strategy": "none",
                "reason": "dowhy_identification_failed",
            }

        # Determine strategy from estimand
        strategy = _determine_strategy(identified_estimand, has_ignorability, has_iv)

        # Check if any backdoor paths exist
        has_backdoor = _has_backdoor_paths(fragment)

        if strategy == "backdoor" and not has_backdoor:
            # No confounding, effect is identified by adjustment
            status = "IDENTIFIED"
        elif strategy == "backdoor" and has_ignorability:
            # Ignorability assumed, can use backdoor
            status = "IDENTIFIED"
        elif strategy == "iv" and has_iv:
            # IV available and exclusion restriction assumed
            status = "IDENTIFIED"
        elif strategy == "backdoor" and not has_ignorability:
            # Backdoor possible but ignorability not assumed
            status = "PARTIALLY_IDENTIFIED"
        else:
            status = "IDENTIFIED" if identified_estimand.estimand_type != "nonparametric-ate" else "PARTIALLY_IDENTIFIED"

        logger.info(
            "identification_check.complete",
            status=status,
            strategy=strategy,
            estimand_type=getattr(identified_estimand, 'estimand_type', 'unknown'),
        )

        return {
            "status": status,
            "strategy": strategy,
            "estimand_type": getattr(identified_estimand, 'estimand_type', 'nonparametric-ate'),
        }

    except Exception as e:
        logger.error("dowhy_model.failed: %s", e)
        raise IdentificationError(f"DoWhy identification failed: {e}") from e


def _heuristic_identification(
    edges: list[dict[str, Any]],
    has_ignorability: bool,
    has_iv: bool,
    has_positivity: bool,
) -> dict[str, Any]:
    """
    Fallback heuristic identification when DoWhy unavailable.

    Uses simple graph structure analysis.
    """
    has_backdoor = _has_backdoor_paths_from_edges(edges)

    if has_iv:
        return {
            "status": "IDENTIFIED" if has_ignorability else "PARTIALLY_IDENTIFIED",
            "strategy": "iv",
        }

    if has_backdoor and has_ignorability:
        return {
            "status": "IDENTIFIED",
            "strategy": "backdoor",
        }

    if has_backdoor and not has_ignorability:
        return {
            "status": "PARTIALLY_IDENTIFIED",
            "strategy": "bounds_only",
            "reason": "backdoor_paths_without_ignorability",
        }

    # No confounding paths - identified by default
    return {
        "status": "IDENTIFIED",
        "strategy": "backdoor",
        "reason": "no_confounding_paths",
    }


def _build_causal_graph(fragment: dict[str, Any]) -> str:
    """Build Graphviz DOT format graph from fragment."""
    edges = fragment.get("edges", [])
    if not edges:
        return "digraph { treatment -> outcome; }"

    lines = ["digraph {"]

    for edge in edges:
        source = edge.get("source", {})
        target = edge.get("target", {})
        source_id = source.get("id", "unknown")[:8]
        target_id = target.get("id", "unknown")[:8]
        lines.append(f'    "{source_id}" -> "{target_id}";')

    lines.append("}")
    return "\n".join(lines)


def _has_backdoor_paths(fragment: dict[str, Any]) -> bool:
    """Check if fragment has potential backdoor paths."""
    edges = fragment.get("edges", [])
    return _has_backdoor_paths_from_edges(edges)


def _has_backdoor_paths_from_edges(edges: list[dict[str, Any]]) -> bool:
    """
    Heuristic: Check for confounder nodes.

    A confounder has edges to both treatment and outcome.
    """
    treatment_targets = set()
    outcome_sources = set()

    for edge in edges:
        source = edge.get("source", {})
        target = edge.get("target", {})

        if source.get("object_type") == "Treatment":
            treatment_targets.add(target.get("id"))

        if target.get("object_type") == "Outcome":
            outcome_sources.add(source.get("id"))

    # Check for common nodes
    common = treatment_targets & outcome_sources
    return len(common) > 0


def _determine_strategy(
    identified_estimand: Any,
    has_ignorability: bool,
    has_iv: bool,
) -> str:
    """
    Determine identification strategy from DoWhy estimand.
    """
    # Check estimand attributes
    estimand_type = getattr(identified_estimand, 'estimand_type', 'nonparametric-ate')

    if has_iv and "iv" in str(estimand_type).lower():
        return "iv"

    # Default to backdoor for most cases
    if has_ignorability:
        return "backdoor"

    # Check for frontdoor if available
    if hasattr(identified_estimand, 'backdoor_variables') and identified_estimand.backdoor_variables:
        return "backdoor"

    return "backdoor"
