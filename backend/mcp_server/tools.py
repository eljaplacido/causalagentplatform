"""
MCP tool wrappers for the causal-inference skills.

Each wrapper is a plain async function — independently unit-testable, with no MCP
transport dependency. `server.py` registers them with a FastMCP instance.

Design contract:
  * Inputs are JSON-native only (dict / list / str / float / bool). No DataFrames,
    no numpy, no Pydantic models cross the boundary — agents speak JSON.
  * `data` columns arrive as ``{"col_name": [v1, v2, ...]}`` and are converted to a
    pandas DataFrame internally before reaching a skill.
  * Every wrapper catches skill-level exceptions and returns a structured
    ``{"error": ..., "error_type": ..., "tool": ...}`` dict instead of raising —
    a calling agent should always get a parseable result.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from src.skills.causal_structure_compare import causal_structure_compare
from src.skills.counterfactual_reason import counterfactual_reason
from src.skills.drift_detect import drift_detect
from src.skills.effect_estimate import effect_estimate
from src.skills.identification_check import identification_check
from src.skills.intervention_estimate import intervention_estimate
from src.skills.sensitivity_analysis import sensitivity_analysis

logger = logging.getLogger(__name__)


def _to_dataframe(data: dict[str, list[float]] | None) -> pd.DataFrame | None:
    """Convert a column-oriented JSON dict to a DataFrame, or None."""
    if not data:
        return None
    return pd.DataFrame(data)


def _error(tool: str, exc: Exception) -> dict[str, Any]:
    """Uniform structured error envelope so agents never receive a raised exception."""
    logger.warning("mcp_tool.error tool=%s error=%s", tool, exc)
    return {
        "error": str(exc),
        "error_type": type(exc).__name__,
        "tool": tool,
        "ok": False,
    }


# ── Tool wrappers ─────────────────────────────────────────────────────────────


async def tool_effect_estimate(
    fragment: dict[str, Any],
    identification_strategy: str = "backdoor",
    assumptions: list[dict[str, Any]] | None = None,
    data: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """Estimate the effect of an APPROVED fragment's Treatment on its Outcome, via causal-core.

    Args:
        fragment: Causal graph fragment. MUST have ``approval_status == "APPROVED"``
            and an ``edges`` list. Each edge: ``{"source": {"id","object_type"},
            "target": {"id","object_type"}, "confidence": float}``.
        identification_strategy: ``"backdoor"`` | ``"iv"`` | ``"frontdoor"``.
        assumptions: Causal assumptions (ignorability, positivity, SUTVA, ...).
        data: Observational data as ``{column: [values]}``, one column per node id.
            Without data there is no estimate: ``status`` is ``refusal``, ``ate`` is null.

    Returns:
        ``{status (result|refusal|apparatus_failure), ate, confidence_interval, estimator,
        identification_strategy, estimand_type, detail, outcome (the full typed contract
        object), ok}``. Relay a refusal's ``detail``; never state an effect that ``ate``
        does not carry.
    """
    try:
        result = await effect_estimate(
            fragment=fragment,
            identification_strategy=identification_strategy,
            assumptions=assumptions or [],
            data=_to_dataframe(data),
        )
        return {**result, "ok": True}
    except Exception as exc:  # noqa: BLE001 — boundary: convert to envelope
        return _error("effect_estimate", exc)


async def tool_counterfactual_reason(
    proposed_edges: list[dict[str, Any]],
    query: str,
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Heuristic triage summary for a counterfactual question -- it does NOT answer it.

    No structural model is fitted, so ``identification_status`` is ``NOT_ASSESSED`` and the
    output is labelled ``heuristic: true``. Do not present it as a counterfactual estimate.

    Args:
        proposed_edges: Candidate causal edges to reason over.
        query: Natural-language counterfactual question.
        assumptions: Optional causal assumptions.

    Returns:
        ``{heuristic, caveat, heuristic_summary, identification_status, sensitivity_labels,
        assumptions, narrative, ok}``.
    """
    try:
        result = await counterfactual_reason(
            proposed_edges=proposed_edges,
            query=query,
            assumptions=assumptions,
        )
        return {**result, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return _error("counterfactual_reason", exc)


async def tool_identification_check(
    fragment: dict[str, Any],
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Check whether a causal effect is *identifiable* from an APPROVED fragment.

    Identifiability is the precondition for trustworthy estimation: if the effect
    is UNIDENTIFIED, an agent should NOT act on any point estimate.

    Returns:
        ``{status, strategy, estimand_type?, reason?, ok}`` where status is
        ``IDENTIFIED`` | ``PARTIALLY_IDENTIFIED`` | ``UNIDENTIFIED``.
    """
    try:
        result = await identification_check(
            fragment=fragment,
            assumptions=assumptions or [],
        )
        return {**result, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return _error("identification_check", exc)


async def tool_sensitivity_analysis(
    ate: float | None,
    confidence_interval: list[float | None],
    assumptions: list[dict[str, Any]] | None = None,
    data: dict[str, list[float]] | None = None,
    effect_scale: str = "difference",
    outcome_sd: float | None = None,
    outcome_column: str | None = None,
) -> dict[str, Any]:
    """E-value: how strong an unmeasured confounder would need to be to explain the effect away.

    The E-value is defined on the risk-ratio scale, so give either
    ``effect_scale="risk_ratio"`` (``ate`` is a risk ratio) or a difference-scale ``ate``
    with the outcome's standard deviation (``outcome_sd``, or ``data`` plus
    ``outcome_column``). Otherwise ``e_value`` is null with the reason. This legacy tool
    passes no matched pairs, so ``rosenbaum_gamma`` is null here; the kernel reports
    Rosenbaum bounds (causal-mcp's ``causal_analyze``, ``sensitivity.rosenbaum``).

    Returns:
        ``{e_value, e_value_ci, robust_to_unmeasured_confounding, method, reason, ok}``.
    """
    try:
        result = await sensitivity_analysis(
            ate=ate,
            confidence_interval=confidence_interval,
            assumptions=assumptions or [],
            data=_to_dataframe(data),
            effect_scale="risk_ratio" if effect_scale == "risk_ratio" else "difference",
            outcome_sd=outcome_sd,
            outcome_column=outcome_column,
        )
        return {**result, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return _error("sensitivity_analysis", exc)


async def tool_drift_detect(
    reference_data: dict[str, list[float]],
    current_data: dict[str, list[float]],
    feature_names: list[str] | None = None,
    kl_threshold: float = 0.1,
    severe_threshold: float = 0.5,
) -> dict[str, Any]:
    """Detect distributional drift between a reference and a current data window.

    Returns:
        ``{kl_divergence, drifted_features, drift_type, feature_scores, ok}``.
    """
    try:
        result = await drift_detect(
            reference_data=reference_data,
            current_data=current_data,
            feature_names=feature_names,
            kl_threshold=kl_threshold,
            severe_threshold=severe_threshold,
        )
        return {**result, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return _error("drift_detect", exc)


async def tool_intervention_estimate(
    source_id: str,
    target_id: str,
    edge_type: str,
    confidence: float,
    assumptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Heuristic triage score for a proposed intervention on one edge -- not an estimate.

    Ranks candidates by the edge's confidence and returns a risk tier (LOW/MEDIUM/HIGH)
    with a recommended action. There are no data behind the score: use
    ``causal_effect_estimate`` with data before claiming any effect.

    Returns:
        ``{heuristic: true, heuristic_score, caveat, risk_tier, action, ok}``. The score ranks
        candidates; it is not a causal effect estimate.
    """
    try:
        result = await intervention_estimate(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            confidence=confidence,
            assumptions=assumptions,
        )
        return {**result, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return _error("intervention_estimate", exc)


async def tool_causal_structure_compare(
    fragment: dict[str, Any],
    data: dict[str, list[float]],
    edge_confidence_threshold: float = 0.05,
) -> dict[str, Any]:
    """Compare an approved causal graph against fresh data to detect *structural* drift.

    Flags edges whose statistical support has weakened and conditional
    independencies that the current data violates — i.e. the graph is going stale.

    Returns:
        ``{structural_drift_detected, affected_edges, edge_scores,
        violated_independencies, ok}``.
    """
    try:
        result = await causal_structure_compare(
            fragment=fragment,
            data=data,
            edge_confidence_threshold=edge_confidence_threshold,
        )
        return {**result, "ok": True}
    except Exception as exc:  # noqa: BLE001
        return _error("causal_structure_compare", exc)


# ── Registry — consumed by server.py and by tests ─────────────────────────────

CAUSAL_TOOLS = {
    "causal_effect_estimate": tool_effect_estimate,
    "causal_counterfactual_reason": tool_counterfactual_reason,
    "causal_identification_check": tool_identification_check,
    "causal_sensitivity_analysis": tool_sensitivity_analysis,
    "causal_drift_detect": tool_drift_detect,
    "causal_intervention_estimate": tool_intervention_estimate,
    "causal_structure_compare": tool_causal_structure_compare,
}
