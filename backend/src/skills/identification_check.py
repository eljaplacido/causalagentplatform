"""
Identification check skill, backed by the causal-core kernel.

Decides from the fragment's graph alone whether the effect of its Treatment on
its Outcome is identifiable, and how: back-door (with the adjustment set),
front-door, instrumental variables, or the general ID algorithm. Every strategy
is cross-checked against the complete ID algorithm inside the kernel.

The old implementation flagged identification from the *names* of assumptions
over dummy "treatment"/"outcome" columns; it is gone.

EpistemicState constraints enforced:
- require_approved_fragment: only APPROVED fragments are checked
- audit_log_required: identification results logged
"""

from __future__ import annotations

from typing import Any

import structlog
from causal_core.contract import CausalQuery, IdentificationStatus
from causal_core.graph import Graph, GraphSyntaxError
from causal_core.identification import (
    IdentificationError as KernelIdentificationError,
)
from causal_core.identification import identify, missing_information

from src.skills._kernel import fragment_to_graph, outcome_id, treatment_id

logger = structlog.get_logger(__name__)


class IdentificationError(Exception):
    """Raised when an identification check cannot be run at all."""


def _unidentified(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "UNIDENTIFIED", "strategy": "none", "reason": reason, **extra}


async def identification_check(
    fragment: dict[str, Any],
    assumptions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Is the effect identifiable from this APPROVED fragment's graph?

    Returns:
        ``status`` IDENTIFIED | UNIDENTIFIED; ``strategy`` (backdoor, frontdoor, iv,
        proximal, id_general, randomized, or none); ``adjustment_set``; ``tier`` (A:
        nonparametric; B: needs parametric assumptions such as effect homogeneity for
        IV); ``estimand`` (the identifying expression); ``missing_information`` when
        not identified; and the kernel's certificate under ``certificate``.
    """
    if fragment.get("approval_status") != "APPROVED":
        logger.error("identification_check.unapproved_fragment")
        return _unidentified("fragment_not_approved")
    if not fragment.get("edges"):
        return _unidentified("empty_graph")
    t_id, y_id = treatment_id(fragment), outcome_id(fragment)
    if not t_id or not y_id:
        return _unidentified("no_treatment_or_outcome")
    try:
        g = Graph.from_spec(fragment_to_graph(fragment))
        cert = identify(g, CausalQuery(treatment=t_id, outcome=y_id))
    except (GraphSyntaxError, KernelIdentificationError, ValueError) as exc:
        return _unidentified("invalid_graph", detail=str(exc))

    logger.info("identification_check.done", status=cert.status.value, strategy=cert.strategy)
    if cert.status is IdentificationStatus.identified:
        return {
            "status": "IDENTIFIED",
            "strategy": cert.strategy.value if cert.strategy else "none",
            "adjustment_set": list(cert.adjustment_set),
            "tier": cert.tier.value,
            "estimand": cert.estimand,
            "estimand_type": "nonparametric-ate" if cert.tier.value == "A" else "parametric-ate",
            "certificate": cert.model_dump(mode="json"),
        }
    return _unidentified(
        "not_identified_from_graph",
        estimand=cert.general_id,
        missing_information=list(missing_information(g, t_id, y_id)),
        certificate=cert.model_dump(mode="json"),
    )
