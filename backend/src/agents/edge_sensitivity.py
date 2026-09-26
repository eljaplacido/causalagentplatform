"""
The kernel's sensitivity analysis of an edge, recorded where Guardian PR-007 reads it.

PR-007 (src/agents/governance.py) refuses to approve an edge unless an assumption it
rests on carries a stored SensitivityResult. Before septa-62 nothing in the platform
wrote one: SimulationAgent computed the kernel's E-value and dropped it. This module
is the one path from a kernel outcome to that stored result:

1. :func:`sensitivity_from_outcome` reads the kernel's own sensitivity analysis out of
   a ``causal_core.analyze`` outcome: the E-value, and the Rosenbaum gamma* when the
   outcome reports one (``sensitivity.rosenbaum.gamma_star``; kernels before contract
   0.12 have no such field, and then ``rosenbaum_gamma`` is None). No analysis, no
   result -- a refusal is relayed, never turned into a number.
2. :func:`answering_edges` says which stored edges an estimate answers: the effect of
   treatment T on outcome Y is the question of an edge from T to Y, and of no other
   edge in the graph (an edge into T or into Y is part of the adjustment, not the
   question).
3. :func:`record_edge_sensitivity` asks the Guardian for a verdict for
   :data:`RECORD_SENSITIVITY` on the edge and, when it is APPROVED, has the registry
   record the result (SemanticLayerRegistry.record_edge_sensitivity, which defines the
   assumption it attaches to: a new IGNORABILITY assumption, PENDING review, added to
   the edge's assumption_refs; never an existing assumption, never an approved edge).

Robustness follows SimulationAgent's convention: robust when the E-value of the
interval bound nearest the null is at least 2. It is a convention, not a law, and PR-007
does not read it -- PR-007 asks for the analysis; the reviewer sees what it found.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.semantic_layer.models import GuardianVerdict, SensitivityResult
from src.semantic_layer.registry import SemanticLayerRegistry
from src.semantic_layer.verdict import RECORD_SENSITIVITY

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from uuid import UUID

    from src.agents.governance import GovernanceAgent
    from src.semantic_layer.models import EpistemicState, UserRef

__all__ = [
    "RECORD_SENSITIVITY",
    "ROBUST_E_VALUE",
    "Recorded",
    "answering_edges",
    "record_edge_sensitivity",
    "sensitivity_from_outcome",
]

#: The E-value (of the interval bound nearest the null) at or above which a result is
#: labelled robust to unmeasured confounding. SimulationAgent's convention.
ROBUST_E_VALUE = 2.0


def sensitivity_from_outcome(
    outcome: Mapping[str, Any] | None,
    *,
    computed_by: str,
    robust_e_value: float = ROBUST_E_VALUE,
) -> SensitivityResult | None:
    """The kernel's sensitivity analysis in ``outcome`` (its JSON form), or None.

    None when the outcome is not a result, carries no sensitivity block, or the block
    has neither an E-value nor a Rosenbaum gamma*.
    """
    if not outcome or outcome.get("kind") != "result":
        return None
    sens = outcome.get("sensitivity") or {}
    e_value = sens.get("e_value")
    rosenbaum = sens.get("rosenbaum") or {}
    gamma = rosenbaum.get("gamma_star") if isinstance(rosenbaum, dict) else None
    if e_value is None and gamma is None:
        return None
    e_ci = sens.get("e_value_ci")
    methods = [
        f"causal-core: {sens.get('e_value_method') or 'E-value'}" if e_value is not None else "",
        f"Rosenbaum bound: {rosenbaum.get('method') or 'matched pairs'}"
        if gamma is not None
        else "",
    ]
    return SensitivityResult(
        e_value=None if e_value is None else float(e_value),
        rosenbaum_gamma=None if gamma is None else float(gamma),
        robust_to_unmeasured_confounding=e_ci is not None and float(e_ci) >= robust_e_value,
        analysis_method="; ".join(m for m in methods if m),
        computed_at=datetime.now(UTC),
        computed_by=computed_by,
    )


def answering_edges(
    edges: list[dict[str, Any]], treatment: str, outcome: str
) -> list[dict[str, Any]]:
    """The edges among ``edges`` (the semantic layer's JSON form) from treatment to outcome.

    A BACKWARD edge points from its target to its source, as src/skills/_kernel.py reads it.
    """
    found = []
    for edge in edges:
        a = str((edge.get("source") or {}).get("id"))
        b = str((edge.get("target") or {}).get("id"))
        if edge.get("direction") == "BACKWARD":
            a, b = b, a
        if (a, b) == (treatment, outcome):
            found.append(edge)
    return found


@dataclass(frozen=True)
class Recorded:
    """What happened when a sensitivity result was offered for an edge."""

    verdict: GuardianVerdict
    reason: str
    policy_rules_cited: list[str]
    audit_entry_id: UUID
    assumption_id: UUID | None


async def record_edge_sensitivity(
    edge_id: UUID,
    result: SensitivityResult,
    *,
    governance: GovernanceAgent,
    state: EpistemicState,
    actor: UserRef,
    evidence: dict[str, Any] | None = None,
    registry: SemanticLayerRegistry | None = None,
    domains: Collection[str] | None = None,
) -> Recorded:
    """Guardian check for RECORD_SENSITIVITY on the edge, then the registry's write.

    ``evidence`` is added to the checked payload (what the analysis ran on), so the
    audit entry's payload hash binds it with the result. Nothing is written unless the
    verdict is APPROVED. Registry errors (the edge is not reviewable, not found, or the
    verdict does not bind) propagate.
    """
    payload: dict[str, Any] = {
        "action_type": RECORD_SENSITIVITY,
        "subject_id": str(edge_id),
        "object_type": "CausalEdge",
        "sensitivity_result": result.model_dump(mode="json"),
        **(evidence or {}),
    }
    check = await governance.check(payload, state)
    if check.verdict != GuardianVerdict.APPROVED:
        return Recorded(
            verdict=check.verdict,
            reason=check.reason,
            policy_rules_cited=check.policy_rules_cited,
            audit_entry_id=check.audit_entry_id,
            assumption_id=None,
        )
    assumption_id = await (registry or SemanticLayerRegistry()).record_edge_sensitivity(
        edge_id, result, check.audit_entry_id, actor, domains=domains
    )
    return Recorded(
        verdict=check.verdict,
        reason=check.reason,
        policy_rules_cited=check.policy_rules_cited,
        audit_entry_id=check.audit_entry_id,
        assumption_id=assumption_id,
    )
