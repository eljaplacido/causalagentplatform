"""
Effect estimation skill, backed by the causal-core kernel.

The kernel identifies the effect from the fragment's graph before estimating
it, checks the design (overlap, sample size, missing data), estimates with a
portfolio of estimators whose intervals come from influence functions, and
returns a typed outcome: a result, a refusal with a reason, or an apparatus
failure. This skill maps that outcome onto the dict its callers read and adds
the full contract object under ``"outcome"``.

What it no longer does:
  * return a hard-coded constant interval when a bootstrap failed;
  * return a "graph heuristic" effect when no data are given. Without data
    there is no estimate, and the result says so.

EpistemicState constraints enforced:
- require_approved_fragment: only APPROVED fragments can be estimated
- audit_log_required: every estimation is logged with its outcome kind
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import structlog
from causal_core import analyze
from causal_core.contract import Abstention, CausalResult

from src.skills._kernel import fragment_to_graph, outcome_id, treatment_id

logger = structlog.get_logger(__name__)


class EffectEstimationError(Exception):
    """Raised when effect estimation is not allowed (e.g. an unapproved fragment)."""


class InsufficientDataError(EffectEstimationError):
    """Raised when data are insufficient for estimation."""


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "status": "refusal",
        "ate": None,
        "confidence_interval": [None, None],
        "estimator": "none",
        "treatment_id": None,
        "outcome_id": None,
        "refusal_reason": "INVALID_INPUT",
        "detail": reason,
    }


async def effect_estimate(
    fragment: dict[str, Any],
    identification_strategy: str,
    assumptions: list[dict[str, Any]],
    data: pd.DataFrame | None = None,
    estimand: str = "ate",
) -> dict[str, Any]:
    """Estimate the effect of the fragment's treatment on its outcome, or say why not.

    Args:
        fragment: APPROVED graph fragment; its Treatment and Outcome nodes define the query.
        identification_strategy: the caller's preferred strategy. Kept as
            ``requested_strategy``; the kernel chooses the strategy the graph supports.
        assumptions: causal assumptions (recorded; the graph carries the testable ones).
        data: observational data with one column per node id. Required for an estimate.
        estimand: ``"ate"`` or ``"att"``.

    Returns:
        ``status`` (result | refusal | apparatus_failure), ``ate`` (the point estimate or
        None), ``confidence_interval`` (the kernel's interval or [None, None]),
        ``estimator``, ``identification_strategy``, ``estimand_type``, ``detail``, and the
        full contract object under ``outcome``.

    Raises:
        EffectEstimationError: the fragment is not APPROVED (a governance rule).
    """
    if fragment.get("approval_status") != "APPROVED":
        logger.error("effect_estimate.unapproved_fragment")
        raise EffectEstimationError("Only APPROVED fragments can be used for estimation")
    if not fragment.get("edges"):
        return _empty_result("the fragment has no edges")
    t_id, y_id = treatment_id(fragment), outcome_id(fragment)
    if not t_id or not y_id:
        return _empty_result("the fragment names no Treatment or no Outcome node")

    base = {
        "treatment_id": t_id,
        "outcome_id": y_id,
        "requested_strategy": identification_strategy,
    }
    if data is None:
        logger.info("effect_estimate.no_data", treatment=t_id, outcome=y_id)
        return {
            **base,
            "status": "refusal",
            "ate": None,
            "confidence_interval": [None, None],
            "estimator": "none",
            "identification_strategy": None,
            "refusal_reason": "NO_DATA",
            "detail": "no observational data supplied: an effect cannot be estimated from "
            "graph structure alone",
        }

    out = analyze(
        data, fragment_to_graph(fragment), treatment=t_id, outcome=y_id, estimand=estimand
    )
    logger.info("effect_estimate.done", kind=out.kind, treatment=t_id, outcome=y_id)
    common = {**base, "status": out.kind, "outcome": out.model_dump(mode="json")}
    if isinstance(out, CausalResult):
        ci = out.primary.ci
        return {
            **common,
            "ate": out.primary.point,
            "confidence_interval": [ci.lo, ci.hi] if ci else [None, None],
            "estimator": out.primary.estimator,
            "identification_strategy": out.certificate.strategy.value
            if out.certificate.strategy
            else None,
            "estimand_type": out.primary.estimand,
            "result_status": out.status.value,
            "detail": "; ".join(out.warnings),
        }
    return {
        **common,
        "ate": None,
        "confidence_interval": [None, None],
        "estimator": "none",
        "identification_strategy": None,
        "refusal_reason": out.reason.value if isinstance(out, Abstention) else None,
        "detail": out.detail,
    }
