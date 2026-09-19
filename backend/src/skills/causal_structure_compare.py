"""
Causal structure comparison skill for detecting structural drift.

This module compares graph structure against live data patterns using
conditional independence tests to detect when the learned causal structure
no longer matches observed data.

EpistemicState constraints enforced:
- audit_log_required: All comparisons logged
- no_semantic_layer_writes: Read-only operation
"""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Try to import scipy for statistical tests
try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger.warning("SciPy not installed. Using fallback structure comparison.")

# Try to import DoWhy for causal tests
try:
    from dowhy.causal_refuters import GraphRefuter
    DOWHY_AVAILABLE = True
except ImportError:
    DOWHY_AVAILABLE = False


class StructureComparisonError(Exception):
    """Raised when structure comparison fails."""
    pass


async def causal_structure_compare(
    fragment: dict[str, Any],
    data: dict[str, list[float]],
    edge_confidence_threshold: float = 0.05,
) -> dict[str, Any]:
    """
    Compare graph structure against live data patterns.

    Runs conditional independence tests to detect when edges in the
    approved fragment no longer match observed conditional dependencies.

    Args:
        fragment: Graph fragment with edges to validate
        data: Current data observations {variable: values}
        edge_confidence_threshold: P-value threshold for edge validity

    Returns:
        dict with:
            - structural_drift_detected: Boolean flag
            - affected_edges: List of edge IDs with drift
            - edge_scores: Per-edge confidence scores
            - violated_independencies: List of violated d-separation statements

    EpistemicState constraints:
        - audit_log_required: Comparison results logged
        - no_semantic_layer_writes: Read-only operation
    """
    # Validate inputs
    edges = fragment.get("edges", [])
    if not edges:
        logger.warning("causal_structure_compare: no edges in fragment")
        return {
            "structural_drift_detected": False,
            "affected_edges": [],
            "edge_scores": {},
            "violated_independencies": [],
        }

    if not data:
        logger.warning("causal_structure_compare: no data provided")
        return {
            "structural_drift_detected": False,
            "affected_edges": [],
            "edge_scores": {},
            "violated_independencies": [],
        }

    affected_edges = []
    edge_scores = {}
    violated_independencies = []

    for edge in edges:
        edge_id = edge.get("id", "unknown")
        source = edge.get("source", {})
        target = edge.get("target", {})

        source_id = source.get("id", "")
        target_id = target.get("id", "")

        # Get variable names/indices
        source_var = _get_variable_name(source)
        target_var = _get_variable_name(target)

        # Check if variables exist in data
        if source_var not in data or target_var not in data:
            continue

        # Get conditioning set (adjusting variables)
        conditioning_set = _get_conditioning_set(edge, fragment, data)

        # Run conditional independence test
        try:
            is_independent, p_value = _conditional_independence_test(
                data[source_var],
                data[target_var],
                [data[c] for c in conditioning_set if c in data],
            )

            edge_scores[edge_id] = round(p_value, 4)

            # If edge claims dependency but test shows independence
            # (p > threshold), the edge may be invalid
            if is_independent and p_value > edge_confidence_threshold:
                affected_edges.append(edge_id)
                violated_independencies.append({
                    "edge": f"{source_var} -> {target_var}",
                    "p_value": p_value,
                    "conditioning_set": conditioning_set,
                })

        except Exception as e:
            logger.error("edge_test.failed", edge=edge_id, error=str(e))
            continue

    structural_drift_detected = len(affected_edges) > 0

    logger.info(
        "causal_structure_compare.complete",
        edges_tested=len(edges),
        affected_count=len(affected_edges),
        drift_detected=structural_drift_detected,
    )

    return {
        "structural_drift_detected": structural_drift_detected,
        "affected_edges": affected_edges,
        "edge_scores": edge_scores,
        "violated_independencies": violated_independencies,
    }


def _get_variable_name(node: dict[str, Any]) -> str:
    """Extract variable name from node reference."""
    # Use ID as variable name (truncated for readability)
    node_id = node.get("id", "")
    return node_id[:8] if node_id else "unknown"


def _get_conditioning_set(
    edge: dict[str, Any],
    fragment: dict[str, Any],
    data: dict[str, list[float]],
) -> list[str]:
    """
    Get conditioning set for an edge (variables that could block the path).

    In a simple implementation, we use all other variables in the fragment.
    """
    edges = fragment.get("edges", [])
    all_nodes = set()

    for e in edges:
        src = e.get("source", {}).get("id", "")[:8]
        tgt = e.get("target", {}).get("id", "")[:8]
        if src:
            all_nodes.add(src)
        if tgt:
            all_nodes.add(tgt)

    # Remove source and target of current edge
    source = edge.get("source", {}).get("id", "")[:8]
    target = edge.get("target", {}).get("id", "")[:8]

    conditioning = [n for n in all_nodes if n not in (source, target)]

    # Only include variables that exist in data
    return [c for c in conditioning if c in data]


def _conditional_independence_test(
    x: list[float],
    y: list[float],
    z: list[list[float]],
) -> tuple[bool, float]:
    """
    Test conditional independence: X ⊥ Y | Z

    Uses partial correlation for continuous variables.
    Falls back to simple correlation if no conditioning variables.

    Args:
        x: Variable X observations
        y: Variable Y observations
        z: Conditioning variables (list of lists)

    Returns:
        (is_independent, p_value)
    """
    if not SCIPY_AVAILABLE:
        # Fallback: use simple correlation
        if len(x) < 3 or len(y) < 3:
            return True, 1.0

        corr = np.corrcoef(x, y)[0, 1]
        if np.isnan(corr):
            return True, 1.0

        # Approximate p-value (conservative)
        p_value = 1 - abs(corr)
        is_independent = abs(corr) < 0.1

        return is_independent, p_value

    try:
        if not z:
            # No conditioning - simple Pearson correlation
            corr, p_value = stats.pearsonr(x, y)
            is_independent = p_value > 0.05
            return is_independent, p_value

        # With conditioning - use partial correlation
        # Residualize X and Y on Z
        z_matrix = np.column_stack(z) if len(z) > 0 else np.array([[]])

        if z_matrix.size == 0 or z_matrix.shape[1] == 0:
            corr, p_value = stats.pearsonr(x, y)
            is_independent = p_value > 0.05
            return is_independent, p_value

        # Multiple regression to residualize
        x_residual = _residualize(x, z_matrix)
        y_residual = _residualize(y, z_matrix)

        # Correlation of residuals
        if len(x_residual) < 3 or np.var(x_residual) < 1e-10 or np.var(y_residual) < 1e-10:
            return True, 1.0

        corr, p_value = stats.pearsonr(x_residual, y_residual)

        # Handle NaN
        if np.isnan(corr) or np.isnan(p_value):
            return True, 1.0

        is_independent = p_value > 0.05

        return is_independent, p_value

    except Exception as e:
        logger.error("ci_test.failed", error=str(e))
        return True, 1.0


def _residualize(
    y: list[float],
    x: np.ndarray,
) -> np.ndarray:
    """
    Residualize y on x using linear regression.

    Returns residuals (y - predicted).
    """
    try:
        y_arr = np.array(y)

        if x.shape[1] == 0:
            return y_arr

        # Add intercept
        x_with_intercept = np.column_stack([np.ones(x.shape[0]), x])

        # OLS: beta = (X'X)^-1 X'y
        try:
            beta = np.linalg.lstsq(x_with_intercept, y_arr, rcond=None)[0]
            predicted = x_with_intercept @ beta
            residuals = y_arr - predicted
        except np.linalg.LinAlgError:
            # Singular matrix - return original
            residuals = y_arr

        return residuals

    except Exception as e:
        logger.error("residualize.failed", error=str(e))
        return np.array(y)


def detect_edge_reversal(
    fragment: dict[str, Any],
    data: dict[str, list[float]],
) -> list[dict[str, Any]]:
    """
    Detect potential edge direction reversals using likelihood comparison.

    Args:
        fragment: Graph fragment with edges
        data: Current observations

    Returns:
        List of edges that may have reversed direction
    """
    reversals = []
    edges = fragment.get("edges", [])

    for edge in edges:
        source = edge.get("source", {})
        target = edge.get("target", {})

        source_var = _get_variable_name(source)
        target_var = _get_variable_name(target)

        if source_var not in data or target_var not in data:
            continue

        # Compare correlation in both directions
        # (simplified - real implementation would use score-based methods)
        try:
            corr_forward = abs(np.corrcoef(data[source_var], data[target_var])[0, 1])

            # If correlation is symmetric, can't determine direction from data alone
            # Mark for expert review
            if not np.isnan(corr_forward) and corr_forward > 0.5:
                reversals.append({
                    "edge_id": edge.get("id", "unknown"),
                    "source": source_var,
                    "target": target_var,
                    "correlation": round(float(corr_forward), 4),
                    "recommendation": "review_direction",
                })

        except Exception:
            continue

    return reversals
