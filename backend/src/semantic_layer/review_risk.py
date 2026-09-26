"""
Risk tiers for the governance review queue, and what counts as a sensitivity result.

The review queue promised an order "by risk tier" and had no tier (septa-36). A tier
here says how much approving a pending object could change, and is read from stored
fields only -- nothing the submitter states about risk is taken at its word:

  * **CausalEdge.** An approved edge changes the graph every estimate over it is
    identified from.
      - CRITICAL: its stated confidence is below :data:`EDGE_CRITICAL_BELOW` (0.5).
      - HIGH: confidence below :data:`EDGE_HIGH_BELOW` (0.8), or no sensitivity result
        the platform recorded on an assumption it rests on says it is robust to
        unmeasured confounding (with none at all, Guardian PR-007 refuses its approval).
      - MEDIUM: otherwise.
  * **GraphFragment.** Approving it makes it usable by simulations.
      - HIGH: it names an edge that is not APPROVED (or that the reviewer cannot see).
      - MEDIUM: otherwise.
  * **CausalEntity.** A node; it asserts no causal relationship. LOW.

Stated confidence is the proposer's own number, so a low one is a warning worth
putting first; a high one is not evidence, which is why a confident edge still needs
a robust sensitivity result to drop below HIGH.

:func:`recorded_sensitivity` is the one reading of "an edge's sensitivity result",
shared with Guardian PR-007 (src/agents/governance.py): a valid SensitivityResult
(:func:`usable_sensitivity`) stored on an assumption the edge references, that
assumption not REJECTED in review, **and recorded by the platform** for that edge.

Why provenance. A drafter can put a SensitivityResult on an assumption they draft --
its ``computed_by`` is free text -- so counting any stored result let a drafter satisfy
PR-007 with a made-up number. SemanticLayerRegistry.record_edge_sensitivity is the one
path that records the kernel's analysis, and it stores in
``assumptions.sensitivity_verdict_id`` (013) the verdict it spent. A result counts for
edge E only when that audit_log row is APPROVED, issued for RECORD_SENSITIVITY, carries
a signature that verifies (so only the Guardian issued it), names E as its subject, and
was spent by record_edge_sensitivity (verdict_consumption, context
:data:`RECORD_SENSITIVITY_CONTEXT`, subject E). propose_draft never writes the column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import bindparam, column, select, table

from src.db.types import json_value, timestamp_text
from src.semantic_layer.models import ReviewStatus, RiskTier, SensitivityResult
from src.semantic_layer.verdict import RECORD_SENSITIVITY
from src.semantic_layer.verdict import claim as verdict_claim
from src.semantic_layer.verdict import verify as verdict_verify

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

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


#: The verdict_consumption context SemanticLayerRegistry.record_edge_sensitivity spends
#: its verdict under. A RECORD_SENSITIVITY verdict spent anywhere else recorded nothing.
RECORD_SENSITIVITY_CONTEXT = "record_edge_sensitivity(CausalEdge)"

_ASSUMPTIONS = table(
    "assumptions",
    column("id"),
    column("review_status"),
    column("sensitivity_result"),
    column("sensitivity_verdict_id"),
)
_AUDIT = table(
    "audit_log",
    column("id"),
    column("agent_id"),
    column("action_type"),
    column("payload_hash"),
    column("guardian_verdict"),
    column("created_at"),
    column("subject_id"),
    column("signature"),
)
_CONSUMED = table("verdict_consumption", column("verdict_id"), column("subject_id"),
                  column("context"))


def _recorded_for(audit: Mapping[str, Any] | None, spent: Mapping[str, Any] | None) -> str | None:
    """The edge a RECORD_SENSITIVITY verdict recorded a result for, or None if it did not.

    None unless the row is APPROVED, for RECORD_SENSITIVITY, signed by the Guardian (the
    signature verifies over the claim the row makes), and spent by
    record_edge_sensitivity on the subject it names.
    """
    if audit is None or spent is None:
        return None
    if str(audit["guardian_verdict"]) != "APPROVED":
        return None
    if str(audit["action_type"]) != RECORD_SENSITIVITY or not audit["subject_id"]:
        return None
    statement = verdict_claim(
        verdict_id=audit["id"],
        subject_id=audit["subject_id"],
        payload_hash=audit["payload_hash"],
        verdict=str(audit["guardian_verdict"]),
        action_type=audit["action_type"],
        agent_id=audit["agent_id"],
        created_at=timestamp_text(audit["created_at"]) or "",
    )
    if not verdict_verify(statement, audit["signature"]):
        return None
    subject = str(audit["subject_id"])
    if str(spent["context"]) != RECORD_SENSITIVITY_CONTEXT or str(spent["subject_id"]) != subject:
        return None
    return subject


async def recorded_sensitivity(
    session: AsyncSession, edges: Mapping[str, Sequence[str]]
) -> dict[str, list[SensitivityResult]]:
    """For each edge id in ``edges`` (mapped to its assumption refs), the sensitivity
    results the platform recorded for it on those assumptions.

    A result counts for edge E when :func:`usable_sensitivity` accepts it and the
    assumption's ``sensitivity_verdict_id`` names a verdict :func:`_recorded_for`
    says recorded a result for E. Every edge in ``edges`` is a key of the answer.
    """
    found: dict[str, list[SensitivityResult]] = {str(e): [] for e in edges}
    refs = sorted({str(a) for rs in edges.values() for a in rs})
    if not refs:
        return found
    a = _ASSUMPTIONS
    rows = (
        await session.execute(
            select(a.c.id, a.c.review_status, a.c.sensitivity_result,
                   a.c.sensitivity_verdict_id).where(
                a.c.id.in_(bindparam("ids", expanding=True))
            ),
            {"ids": refs},
        )
    ).mappings().all()

    candidates: dict[str, tuple[SensitivityResult, str]] = {}
    for r in rows:
        result = usable_sensitivity(r["review_status"], r["sensitivity_result"])
        if result is not None and r["sensitivity_verdict_id"] is not None:
            candidates[str(r["id"])] = (result, str(r["sensitivity_verdict_id"]))
    if not candidates:
        return found

    verdict_ids = sorted({v for _, v in candidates.values()})
    audits: dict[str, Mapping[str, Any]] = {
        str(r["id"]): dict(r)
        for r in (
            await session.execute(
                select(*_AUDIT.c).where(_AUDIT.c.id.in_(bindparam("vids", expanding=True))),
                {"vids": verdict_ids},
            )
        ).mappings()
    }
    spent: dict[str, Mapping[str, Any]] = {
        str(r["verdict_id"]): dict(r)
        for r in (
            await session.execute(
                select(*_CONSUMED.c).where(
                    _CONSUMED.c.verdict_id.in_(bindparam("vids", expanding=True))
                ),
                {"vids": verdict_ids},
            )
        ).mappings()
    }
    recorded_for = {v: _recorded_for(audits.get(v), spent.get(v)) for v in verdict_ids}

    for edge, assumption_refs in edges.items():
        for ref in assumption_refs:
            hit = candidates.get(str(ref))
            if hit is not None and recorded_for.get(hit[1]) == str(edge):
                found[str(edge)].append(hit[0])
    return found


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
            "no sensitivity result recorded by the platform on an assumption it rests on "
            "(a drafter's own result does not count; Guardian PR-007 refuses its approval "
            "until one is recorded)"
        )
    elif not any(r.robust_to_unmeasured_confounding for r in results):
        reasons.append(
            "no sensitivity result recorded by the platform says it is robust to "
            "unmeasured confounding"
        )
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
