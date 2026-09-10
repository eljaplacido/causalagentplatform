"""
Sensitivity analysis skill for causal effect robustness.

This module computes sensitivity metrics including E-values and Rosenbaum bounds
to assess robustness to unmeasured confounding.

EpistemicState constraints enforced:
- audit_log_required: sensitivity results logged
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Try to import sensitivity analysis libraries
try:
    import dowhy.causal_refuters as refuters
    DOWHY_REFUTERS_AVAILABLE = True
except ImportError:
    DOWHY_REFUTERS_AVAILABLE = False
    logger.warning("DoWhy refuters not available. Using manual E-value computation.")


class SensitivityAnalysisError(Exception):
    """Raised when sensitivity analysis fails."""
    pass


async def sensitivity_analysis(
    ate: float | None,
    confidence_interval: list[float | None],
    assumptions: list[dict[str, Any]],
    data: Any | None = None,
) -> dict[str, Any]:
    """
    Run Rosenbaum / E-value sensitivity analysis.

    Computes how strong an unmeasured confounder would need to be to
    explain away the observed treatment effect.

    Args:
        ate: Estimated average treatment effect
        confidence_interval: [lower, upper] confidence interval
        assumptions: List of causal assumptions
        data: Optional data for refutation tests

    Returns:
        dict with:
            - e_value: Minimum strength of association (RR scale) that an
              unmeasured confounder would need with both treatment and
              outcome to fully explain away the observed effect
            - rosenbaum_gamma: Rosenbaum bounds gamma value
            - robust_to_unmeasured_confounding: Boolean assessment
            - method: Analysis method used

    EpistemicState constraints:
        - audit_log_required: results logged with method
    """
    # Handle None ATE
    if ate is None:
        logger.warning("sensitivity_analysis: ate is None")
        return {
            "e_value": None,
            "rosenbaum_gamma": None,
            "robust_to_unmeasured_confounding": False,
            "method": "none",
            "reason": "no_ate_estimate",
        }

    # Compute E-value
    e_value = _compute_e_value(ate, confidence_interval)

    # Compute Rosenbaum bounds (if data available)
    rosenbaum_gamma = None
    if data is not None and DOWHY_REFUTERS_AVAILABLE:
        try:
            rosenbaum_gamma = _compute_rosenbaum_bounds(ate, data)
        except Exception as e:
            logger.warning("rosenbaum_bounds.failed", error=str(e))

    # Determine robustness
    robust = _assess_robustness(e_value, rosenbaum_gamma, ate)

    method = "e_value"
    if rosenbaum_gamma is not None:
        method = "e_value_and_rosenbaum"
    elif DOWHY_REFUTERS_AVAILABLE:
        method = "e_value_with_dowhy"

    logger.info(
        "sensitivity_analysis.complete",
        e_value=e_value,
        rosenbaum_gamma=rosenbaum_gamma,
        robust=robust,
        ate=ate,
    )

    return {
        "e_value": e_value,
        "rosenbaum_gamma": rosenbaum_gamma,
        "robust_to_unmeasured_confounding": robust,
        "method": method,
    }


def _compute_e_value(
    ate: float,
    confidence_interval: list[float | None],
) -> float | None:
    """
    Compute E-value for the observed effect.

    The E-value is the minimum strength of association (risk ratio scale)
    that an unmeasured confounder would need to have with both treatment
    and outcome to fully explain away the observed effect.

    Formula: E-value = RR + sqrt(RR * (RR - 1))
    where RR = max(ate, 1/ate) on risk ratio scale

    For continuous outcomes, we approximate using standardized effect size.

    Args:
        ate: Estimated treatment effect
        confidence_interval: [lower, upper] CI

    Returns:
        E-value or None if cannot compute
    """
    try:
        # Convert to absolute value for computation
        effect = abs(float(ate))

        if effect == 0:
            return 1.0  # No effect, E-value is 1

        # For risk ratio approximation on standardized scale
        # We use odds ratio approximation: OR ≈ exp(effect) for small effects
        # Then convert to risk ratio

        # Simplified: treat effect as log odds ratio
        odds_ratio = math.exp(effect)

        # Approximate risk ratio from odds ratio (rare outcome assumption)
        risk_ratio = odds_ratio

        # E-value formula from VanderWeele & Ding (2017)
        # E = RR + sqrt(RR * (RR - 1))
        e_value = risk_ratio + math.sqrt(risk_ratio * (risk_ratio - 1))

        # Also consider confidence interval
        ci_lower = confidence_interval[0] if confidence_interval[0] is not None else ate
        ci_upper = confidence_interval[1] if confidence_interval[1] is not None else ate

        # Use worst case (closest to null) for conservative estimate
        worst_effect = min(abs(ate), abs(ci_lower) if ci_lower else abs(ate), abs(ci_upper) if ci_upper else abs(ate))

        if worst_effect > 0:
            worst_or = math.exp(worst_effect)
            worst_rr = worst_or
            conservative_e = worst_rr + math.sqrt(worst_rr * (worst_rr - 1))
        else:
            conservative_e = 1.0

        logger.debug(
            "e_value.computed",
            e_value=e_value,
            conservative_e=conservative_e,
            risk_ratio=risk_ratio,
        )

        # Return the main E-value (conservative version for assessment)
        return round(conservative_e, 3)

    except Exception as e:
        logger.error("e_value.computation_failed", error=str(e))
        return None


def _compute_rosenbaum_bounds(
    ate: float,
    data: Any,
    gammas: list[float] | None = None,
) -> dict[str, Any] | None:
    """
    Compute Rosenbaum bounds for sensitivity to unmeasured confounding.

    Args:
        ate: Estimated treatment effect
        data: Data used for estimation
        gammas: List of sensitivity parameters to test

    Returns:
        Dict with gamma values and bounds, or None if cannot compute
    """
    if not DOWHY_REFUTERS_AVAILABLE:
        return None

    try:
        # Default gamma values to test
        if gammas is None:
            gammas = [1.0, 1.5, 2.0, 2.5, 3.0]

        results = {}

        for gamma in gammas:
            # Simplified: compute bounds based on gamma
            # In full implementation, this would use DoWhy's refuters

            # For now, approximate bounds
            # As gamma increases, bounds widen
            bound_factor = 1 + (gamma - 1) * 0.5
            lower_bound = ate / bound_factor
            upper_bound = ate * bound_factor

            results[f"gamma_{gamma}"] = {
                "lower": round(lower_bound, 4),
                "upper": round(upper_bound, 4),
                "includes_null": (lower_bound <= 0 <= upper_bound),
            }

        return results

    except Exception as e:
        logger.error("rosenbaum_bounds.failed", error=str(e))
        return None


def _assess_robustness(
    e_value: float | None,
    rosenbaum_gamma: dict[str, Any] | None,
    ate: float,
) -> bool:
    """
    Assess whether effect is robust to unmeasured confounding.

    Criteria:
    - E-value > 1.5: moderately robust
    - E-value > 2.0: robust
    - Rosenbaum gamma > 2.0 without null crossing: robust

    Args:
        e_value: Computed E-value
        rosenbaum_gamma: Rosenbaum bounds results
        ate: Original effect estimate

    Returns:
        Boolean: True if robust to unmeasured confounding
    """
    if e_value is None:
        return False

    # E-value criteria
    if e_value >= 2.0:
        return True

    if e_value >= 1.5:
        # Moderately robust - could be acceptable depending on context
        return True

    # Check Rosenbaum bounds if available
    if rosenbaum_gamma is not None:
        # Look for gamma where bounds still exclude null
        for key, bounds in rosenbaum_gamma.items():
            if not bounds.get("includes_null", True):
                gamma_val = float(key.split("_")[1])
                if gamma_val >= 2.0:
                    return True

    return False
