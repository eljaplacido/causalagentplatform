"""
Risk tiers for the governance review queue, and what counts as a sensitivity result.

The review queue promised an order "by risk tier" and had no tier (septa-36). A tier
here says how much approving a pending object could change, and is read from stored
fields only -- nothing the submitter states about risk is taken at its word:

  * **CausalEdge.** An approved edge changes the graph every estimate over it is
    identified from.
      - CRITICAL: its stated confidence is below :data:`EDGE_CRITICAL_BELOW` (0.5).
      - HIGH: confidence below :data:`EDGE_HIGH_BELOW` (0.8), or no stored sensitivity
        result on an assumption it rests on says it is robust to unmeasured
        confounding (with none at all, Guardian PR-007 refuses its approval).
      - MEDIUM: otherwise.
  * **GraphFragment.** Approving it makes it usable by simulations.
      - HIGH: it names an edge that is not APPROVED (or that the reviewer cannot see).
      - MEDIUM: otherwise.
  * **CausalEntity.** A node; it asserts no causal relationship. LOW.

Stated confidence is the proposer's own number, so a low one is a warning worth
putting first; a high one is not evidence, which is why a confident edge still needs
a robust sensitivity result to drop below HIGH.

:func:`usable_sensitivity` is the one reading of "an edge's sensitivity result",
shared with Guardian PR-007 (src/agents/governance.py): a valid SensitivityResult
stored on an assumption the edge references, that assumption not REJECTED in review.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.db.types import json_value
from src.semantic_layer.models import ReviewStatus, RiskTier, SensitivityResult

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

EDGE_CRITICAL_BELOW = 0.5
EDGE_HIGH_BELOW = 0.8

#: Queue order: most at risk first.
TIER_RANK: dict[RiskTier, int] = {
    RiskTier.CRITICAL: 0,
    RiskTier.HIGH: 1,
    RiskTier.MEDIUM: 2,
    RiskTier.LOW: 3,
}


def usable_sensitivity(review_status: Any, stored: Any) -> SensitivityResult | None:
    """The assumption's stored sensitivity result, if it counts for its edges.

    It does not count when there is none, when it does not validate as a
    SensitivityResult, or when the assumption was REJECTED in review.
    """
    if stored is None or str(review_status) == ReviewStatus.REJECTED.value:
        return None
    try:
        return SensitivityResult.model_validate(json_value(stored))
    except (ValidationError, ValueError, TypeError):
        return None


def edge_risk(
    confidence: float, results: Sequence[SensitivityResult]
) -> tuple[RiskTier, list[str]]:
    """An edge's tier and the facts that decided it."""
    if confidence < EDGE_CRITICAL_BELOW:
        return RiskTier.CRITICAL, [
            f"stated confidence {confidence:.2f} is below {EDGE_CRITICAL_BELOW}"
        ]
    reasons: list[str] = []
    if confidence < EDGE_HIGH_BELOW:
        reasons.append(f"stated confidence {confidence:.2f} is below {EDGE_HIGH_BELOW}")
    if not results:
        reasons.append(
            "no stored sensitivity result on an assumption it rests on "
            "(Guardian PR-007 refuses its approval until one is recorded)"
        )
    elif not any(r.robust_to_unmeasured_confounding for r in results):
        reasons.append("no stored sensitivity result says it is robust to unmeasured confounding")
    if reasons:
        return RiskTier.HIGH, reasons
    return RiskTier.MEDIUM, [
        f"stated confidence {confidence:.2f} and a sensitivity result that says it is robust"
    ]


def fragment_risk(
    edge_ids: Iterable[str], edge_status: Mapping[str, Any]
) -> tuple[RiskTier, list[str]]:
    """A fragment's tier: HIGH while it would carry an unapproved edge into simulations."""
    pending = [e for e in edge_ids if str(edge_status.get(e)) != "APPROVED"]
    if pending:
        return RiskTier.HIGH, [
            f"{len(pending)} of its edges not APPROVED; approving it would put them "
            "in simulations"
        ]
    return RiskTier.MEDIUM, ["approving it makes it usable by simulations"]


def entity_risk() -> tuple[RiskTier, list[str]]:
    """An entity's tier."""
    return RiskTier.LOW, ["an entity asserts no causal relationship"]
