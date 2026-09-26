"""
Intervention Outcome Feedback Loop — closes the Phase 4 requirement.

When an intervention is executed in the real world and the outcome is observed,
this module closes the feedback loop by:
  1. Recording the actual observed effect against the predicted effect
  2. Computing the prediction error (expected_effect − actual_effect)
  3. Triggering causal graph re-validation when errors are significant
  4. Feeding outcome data into the experience buffer and drift monitor

Endpoints:
  POST /api/v1/interventions/feedback    — close feedback loop for an intervention
  GET  /api/v1/interventions/pending     — list interventions with open feedback loops
  GET  /api/v1/interventions/accuracy    — prediction accuracy metrics over time

EpistemicState constraints:
  - audit_log_required: Every feedback closure logged
  - no_semantic_layer_writes (read-only via Guardian — writes only to interventions table)
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.db.factory import CloudSqlFactory
from src.db.types import TEXT_ARRAY, TIMESTAMP, timestamp_text, typed_text
from src.middleware.rbac import DECISION_ROLES, guard

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/interventions", tags=["Interventions"])

#: DECISION_ROLES (src/middleware/rbac.py) decide an intervention and record what
#: it did. Reading them needs only authentication; deciding one writes a row that
#: says a human approved it.
__all__ = ["DECISION_ROLES", "router"]


# ── Request/Response ──────────────────────────────────────────────────────────


class FeedbackRequest(BaseModel):
    # An executed intervention's id: interventions.id is a uuid column. A proposal id
    # (``int-<rank>-<session>``) or any other string is refused here as a 422 rather
    # than reaching PostgreSQL's uuid column and failing there as a 500.
    intervention_id: UUID
    actual_effect: float
    measurement_method: str = Field(default="observed")
    notes: str | None = None


class FeedbackResponse(BaseModel):
    intervention_id: str
    expected_effect: float
    actual_effect: float
    prediction_error: float
    error_severity: str   # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    feedback_loop_closed: bool
    re_validation_triggered: bool
    drift_alert_emitted: bool
    closed_at: str


class PendingIntervention(BaseModel):
    intervention_id: str
    name: str
    initiated_at: str
    expected_effect: float  # from JSONB field
    hours_open: float


class InterventionDecisionRequest(BaseModel):
    reason: str = Field(default="", description="Free-text justification recorded in audit_log")
    incident_id: str | None = Field(default=None, description="Originating incident ID for traceability")
    intervention_title: str | None = Field(default=None, description="Snapshot of the intervention title")


class InterventionDecisionResponse(BaseModel):
    intervention_id: str
    decision: str  # "APPROVED" | "REJECTED"
    audit_log_id: str
    recorded_at: str
    already_decided: bool = False


class InterventionDecisionRecord(BaseModel):
    intervention_id: str
    decision: str
    audit_log_id: str
    recorded_at: str
    incident_id: str | None = None
    decided_by: str


# ── Feedback loop closure ─────────────────────────────────────────────────────


def _compute_error_severity(expected: float, actual: float) -> str:
    """Classify prediction error severity."""
    if expected == 0 and actual == 0:
        return "LOW"
    if expected == 0:
        error_ratio = abs(actual)
    else:
        error_ratio = abs(actual - expected) / max(abs(expected), 0.001)

    if error_ratio > 1.0:
        return "CRITICAL"
    elif error_ratio > 0.5:
        return "HIGH"
    elif error_ratio > 0.2:
        return "MEDIUM"
    return "LOW"


async def _append_unruled_audit_row(
    session: Any,
    *,
    agent_id: str,
    action_type: str,
    payload: dict[str, Any],
    rules: list[str],
    session_id: str,
    subject_id: str,
    notes: str,
    at: datetime,
) -> None:
    """Append an audit row that records an event without claiming a verdict on it."""
    await session.execute(
        typed_text(
            "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
            "guardian_verdict, policy_rules_cited, session_id, created_at, subject_id, notes) "
            "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :session, :ts, :subject, "
            ":notes)",
            rules=TEXT_ARRAY,
            ts=TIMESTAMP,
        ),
        {
            "id": str(uuid4()),
            "agent": agent_id,
            "action": action_type,
            "hash": "sha256:"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest(),
            "verdict": "PENDING",
            "rules": list(rules),
            "session": session_id,
            "ts": at,
            "subject": subject_id,
            "notes": notes,
        },
    )


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="Close the feedback loop for an intervention",
)
async def close_feedback_loop(
    body: FeedbackRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/interventions/feedback")),
) -> FeedbackResponse:
    """
    Record the actual observed effect of an intervention.

    When the actual effect deviates significantly from the predicted effect:
      - an escalation request is appended to the audit log (CRITICAL deviations)
      - the response reports that re-validation is warranted (HIGH or CRITICAL)

    Every closure is appended to the audit log as a PENDING row: a record of what
    was observed, not a Guardian verdict on it.
    """
    intervention_id = str(body.intervention_id)
    now = datetime.now(UTC)

    async with CloudSqlFactory.get_session_factory()() as session:
        # Look up the intervention
        row = await session.execute(
            text(
                "SELECT id, name, expected_effect, treatment_ref, feedback_loop_closed "
                "FROM interventions WHERE id = :id"
            ),
            {"id": intervention_id},
        )
        record = row.mappings().fetchone()
        if not record:
            raise HTTPException(status_code=404, detail=f"Intervention {intervention_id} not found")

        if record["feedback_loop_closed"]:
            raise HTTPException(
                status_code=409,
                detail=f"Feedback loop already closed for intervention {intervention_id}",
            )

        # Parse expected effect from JSONB
        expected_raw = record["expected_effect"]
        if isinstance(expected_raw, str):
            expected_data = json.loads(expected_raw)
        else:
            expected_data = expected_raw or {}

        expected_effect = float(
            expected_data.get("ate", expected_data.get("expected_effect", 0.0) or 0.0)
        )

        # Compute prediction error
        prediction_error = round(abs(body.actual_effect - expected_effect), 4)
        error_severity = _compute_error_severity(expected_effect, body.actual_effect)

        # Build actual effect JSONB
        actual_effect_data = {
            "observed_value": body.actual_effect,
            "measurement_method": body.measurement_method,
            "measured_at": now.isoformat(),
            "notes": body.notes,
        }

        # Trigger re-validation for significant errors
        re_validation = error_severity in ("HIGH", "CRITICAL")
        drift_emitted = error_severity == "CRITICAL"

        # Close it only if it is still open: the check above is a read, and a
        # concurrent closure may have passed it too. The row lock makes the loser's
        # UPDATE re-read the flag and match nothing, and it writes no audit row.
        closed = await session.execute(
            typed_text(
                "UPDATE interventions SET "
                "actual_effect = :actual, "
                "feedback_loop_closed = TRUE, "
                "outcome_observed_at = :now "
                "WHERE id = :id AND feedback_loop_closed = FALSE",
                now=TIMESTAMP,
            ),
            {
                "actual": json.dumps(actual_effect_data),
                "now": now,
                "id": intervention_id,
            },
        )
        if not closed.rowcount:  # type: ignore[attr-defined]
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Feedback loop already closed for intervention {intervention_id}",
            )

        # Record the closure in the audit log -- as a record, not a ruling. This
        # route used to write guardian_verdict=APPROVED (and REQUIRES_ESCALATION for
        # the drift alert) without the Guardian ever being asked, so the trail showed
        # approvals nobody issued. PENDING is what "nobody has ruled on this" means
        # (002_verdict_binding.sql); the row is unsigned and authorises nothing.
        closure_payload = {
            "intervention_id": intervention_id,
            "expected_effect": expected_effect,
            "actual_effect": actual_effect_data,
            "prediction_error": prediction_error,
            "error_severity": error_severity,
            "closed_by": user.get("uid") or "unknown",
        }
        await _append_unruled_audit_row(
            session,
            agent_id=user.get("uid", "feedback-user"),
            action_type="FEEDBACK_LOOP_CLOSED",
            payload=closure_payload,
            rules=[],
            session_id=f"feedback-{intervention_id}",
            subject_id=intervention_id,
            notes="Recorded by the feedback route; not a Guardian verdict.",
            at=now,
        )

        if drift_emitted:
            await _append_unruled_audit_row(
                session,
                agent_id="FeedbackLoopProcessor",
                action_type="DRIFT_ALERT_OUTCOME_DEVIATION",
                payload=closure_payload,
                rules=["PR-008"],
                session_id=f"drift-{intervention_id}",
                subject_id=intervention_id,
                notes=(
                    "Escalation requested: the observed effect deviates CRITICALLY from "
                    "the prediction. Requested by the feedback route, not ruled on by "
                    "the Guardian."
                ),
                at=now,
            )

        await session.commit()

        log.info(
            "intervention.feedback_closed",
            intervention_id=intervention_id,
            expected=expected_effect,
            actual=body.actual_effect,
            error=prediction_error,
            severity=error_severity,
            re_validation=re_validation,
        )

        return FeedbackResponse(
            intervention_id=intervention_id,
            expected_effect=expected_effect,
            actual_effect=body.actual_effect,
            prediction_error=prediction_error,
            error_severity=error_severity,
            feedback_loop_closed=True,
            re_validation_triggered=re_validation,
            drift_alert_emitted=drift_emitted,
            closed_at=now.isoformat(),
        )


@router.get(
    "/pending",
    response_model=list[PendingIntervention],
    summary="List interventions with open feedback loops",
)
async def list_pending(
    domain: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/interventions/pending")),
) -> list[PendingIntervention]:
    """List interventions that have been executed but whose outcomes are not yet observed."""
    now = datetime.now(UTC)

    async with CloudSqlFactory.get_session_factory()() as session:
        rows = await session.execute(
            text(
                "SELECT id, name, initiated_at, expected_effect "
                "FROM interventions "
                "WHERE feedback_loop_closed = FALSE "
                "ORDER BY initiated_at DESC "
                "LIMIT :limit"
            ),
            {"limit": limit},
        )

        result: list[PendingIntervention] = []
        for row in rows.mappings():
            expected_raw = row["expected_effect"]
            if isinstance(expected_raw, str):
                expected_data = json.loads(expected_raw)
            else:
                expected_data = expected_raw or {}

            initiated_str = timestamp_text(row["initiated_at"]) or ""
            try:
                initiated_dt = datetime.fromisoformat(initiated_str.replace("Z", "+00:00"))
                hours_open = round(
                    (now - initiated_dt.replace(tzinfo=UTC)).total_seconds() / 3600, 1
                )
            except Exception:
                hours_open = 0.0

            result.append(
                PendingIntervention(
                    intervention_id=str(row["id"]),
                    name=str(row["name"]),
                    initiated_at=initiated_str,
                    expected_effect=float(
                        expected_data.get("ate", expected_data.get("expected_effect", 0.0) or 0.0)
                    ),
                    hours_open=hours_open,
                )
            )

        return result


_DECISION_ACTIONS = ("INTERVENTION_APPROVED", "INTERVENTION_REJECTED")


async def _find_existing_decision(
    intervention_id: str,
) -> InterventionDecisionResponse | None:
    """Look up a prior approve/reject decision for this intervention_id.

    audit_log is queried directly because proposed interventions aren't persisted
    as their own rows — the immutable log is the source of truth. The LIKE only
    narrows the candidates (its wildcards escaped, the id as the payload encodes
    it); a row counts when its payload's intervention_id equals this one exactly,
    since SQLite's LIKE ignores ASCII case.
    """
    encoded = json.dumps(intervention_id)
    needle = "%" + _like_escape(f'"intervention_id": {encoded}') + "%"
    async with CloudSqlFactory.get_session_factory()() as session:
        rows = await session.execute(
            text(
                "SELECT id, action_type, created_at, payload_hash "
                "FROM audit_log "
                "WHERE action_type IN ('INTERVENTION_APPROVED', 'INTERVENTION_REJECTED') "
                "AND payload_hash LIKE :needle ESCAPE '\\' "
                "ORDER BY created_at ASC"
            ),
            {"needle": needle},
        )
        record = next(
            (r for r in rows.mappings() if _payload_id(r["payload_hash"]) == intervention_id),
            None,
        )
        if record is None:
            return None
        decision = "APPROVED" if record["action_type"] == "INTERVENTION_APPROVED" else "REJECTED"
        return InterventionDecisionResponse(
            intervention_id=intervention_id,
            decision=decision,
            audit_log_id=str(record["id"]),
            recorded_at=timestamp_text(record["created_at"]) or "",
            already_decided=True,
        )


def _like_escape(value: str) -> str:
    """`value` as a literal inside a LIKE pattern whose escape character is ``\\``."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _payload_id(payload_hash: str | None) -> str | None:
    """The intervention_id a decision row's payload names (None if unreadable)."""
    try:
        value = json.loads(payload_hash or "").get("intervention_id")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None


async def _record_decision(
    intervention_id: str,
    decision: str,
    body: InterventionDecisionRequest,
    user: dict[str, Any],
) -> InterventionDecisionResponse:
    """Write an immutable audit_log entry capturing the human decision on a proposed intervention.

    Idempotent: if a prior decision exists for this intervention_id, the original
    decision is returned with `already_decided=True` and no new audit entry is written.
    Audit_log is append-only, so this prevents accidental duplicates without violating immutability.
    """
    existing = await _find_existing_decision(intervention_id)
    if existing is not None:
        log.info(
            "intervention.decision_idempotent",
            intervention_id=intervention_id,
            attempted=decision,
            recorded=existing.decision,
        )
        return existing

    now = datetime.now(UTC)
    audit_id = str(uuid4())
    action = "INTERVENTION_APPROVED" if decision == "APPROVED" else "INTERVENTION_REJECTED"
    verdict = "APPROVED" if decision == "APPROVED" else "REJECTED"

    payload = {
        "intervention_id": intervention_id,
        "incident_id": body.incident_id,
        "intervention_title": body.intervention_title,
        "reason": body.reason,
        "decided_by": user.get("uid") or user.get("email") or "unknown",
    }
    payload_hash = json.dumps(payload, sort_keys=True)

    async with CloudSqlFactory.get_session_factory()() as session:
        await session.execute(
            typed_text(
                "INSERT INTO audit_log (id, agent_id, action_type, payload_hash, "
                "guardian_verdict, policy_rules_cited, session_id, created_at) "
                "VALUES (:id, :agent, :action, :hash, :verdict, :rules, :session, :ts)",
                rules=TEXT_ARRAY,
                ts=TIMESTAMP,
            ),
            {
                "id": audit_id,
                "agent": user.get("uid", "human-reviewer"),
                "action": action,
                "hash": payload_hash,
                "verdict": verdict,
                "rules": ["PR-006"],
                "session": body.incident_id or f"intervention-{intervention_id}",
                "ts": now,
            },
        )
        await session.commit()

    log.info(
        "intervention.decision_recorded",
        intervention_id=intervention_id,
        decision=decision,
        incident_id=body.incident_id,
        audit_log_id=audit_id,
    )

    return InterventionDecisionResponse(
        intervention_id=intervention_id,
        decision=decision,
        audit_log_id=audit_id,
        recorded_at=now.isoformat(),
        already_decided=False,
    )


@router.post(
    "/{intervention_id}/approve",
    response_model=InterventionDecisionResponse,
    summary="Approve a proposed intervention (records audit_log entry)",
)
async def approve_intervention(
    intervention_id: str,
    body: InterventionDecisionRequest,
    user: dict[str, Any] = Depends(
        guard("POST", "/api/v1/interventions/{intervention_id}/approve")
    ),
) -> InterventionDecisionResponse:
    """Approving an intervention is a governance act, so it needs a governance role.

    This endpoint required authentication and nothing else, while writing a row
    into audit_log with `guardian_verdict = APPROVED`. CYBERSECURITY.md has always
    claimed "RBAC enforced at API layer, database layer, and semantic layer".
    """
    return await _record_decision(intervention_id, "APPROVED", body, user)


@router.post(
    "/{intervention_id}/reject",
    response_model=InterventionDecisionResponse,
    summary="Reject a proposed intervention (records audit_log entry)",
)
async def reject_intervention(
    intervention_id: str,
    body: InterventionDecisionRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/interventions/{intervention_id}/reject")),
) -> InterventionDecisionResponse:
    """Rejecting is as much a decision of record as approving, so the same role."""
    return await _record_decision(intervention_id, "REJECTED", body, user)


# Whole literal statements, one per filter shape, never assembled from strings:
# the incident filter only ever reaches them as a bound parameter.
_LIST_DECISIONS = (
    "SELECT id, action_type, payload_hash, session_id, created_at, agent_id "
    "FROM audit_log "
    "WHERE action_type IN ('INTERVENTION_APPROVED', 'INTERVENTION_REJECTED') "
    "ORDER BY created_at DESC LIMIT :limit"
)
_LIST_DECISIONS_FOR_INCIDENT = (
    "SELECT id, action_type, payload_hash, session_id, created_at, agent_id "
    "FROM audit_log "
    "WHERE action_type IN ('INTERVENTION_APPROVED', 'INTERVENTION_REJECTED') "
    "AND session_id = :incident ORDER BY created_at DESC LIMIT :limit"
)


@router.get(
    "/decisions",
    response_model=list[InterventionDecisionRecord],
    summary="List prior intervention approve/reject decisions",
)
async def list_decisions(
    incident_id: str | None = Query(default=None, description="Filter to a single incident's decisions"),
    limit: int = Query(default=100, ge=1, le=500),
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/interventions/decisions")),
) -> list[InterventionDecisionRecord]:
    """Return intervention decisions from audit_log, newest first.

    Lets the UI rehydrate which interventions have been approved/rejected — useful
    after a page reload or for the governance audit trail.
    """
    params: dict[str, Any] = {"limit": limit}
    sql = _LIST_DECISIONS
    if incident_id:
        sql = _LIST_DECISIONS_FOR_INCIDENT
        params["incident"] = incident_id

    async with CloudSqlFactory.get_session_factory()() as session:
        rows = await session.execute(text(sql), params)

        results: list[InterventionDecisionRecord] = []
        for r in rows.mappings():
            try:
                payload = json.loads(r["payload_hash"])
            except (json.JSONDecodeError, TypeError):
                # Legacy entries may have hashed payload_hash — skip parsing
                payload = {}
            results.append(
                InterventionDecisionRecord(
                    intervention_id=str(payload.get("intervention_id") or ""),
                    decision="APPROVED" if r["action_type"] == "INTERVENTION_APPROVED" else "REJECTED",
                    audit_log_id=str(r["id"]),
                    recorded_at=timestamp_text(r["created_at"]) or "",
                    incident_id=(str(r["session_id"]) if r["session_id"] and not str(r["session_id"]).startswith("intervention-") else None),
                    decided_by=str(payload.get("decided_by") or r["agent_id"]),
                )
            )
        return results


@router.get(
    "/accuracy",
    response_model=dict[str, Any],
    summary="Intervention prediction accuracy over time",
)
async def get_prediction_accuracy(
    period_days: int = Query(default=30, ge=1, le=365),
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/interventions/accuracy")),
) -> dict[str, Any]:
    """
    Compute prediction accuracy for closed feedback loops.

    Returns MAE (Mean Absolute Error), RMSE, and directional accuracy
    (did the prediction get the sign right?) over the specified period.
    """
    cutoff = (datetime.now(UTC) - __import__("datetime").timedelta(days=period_days))

    async with CloudSqlFactory.get_session_factory()() as session:
        rows = await session.execute(
            typed_text(
                "SELECT expected_effect, actual_effect, outcome_observed_at "
                "FROM interventions "
                "WHERE feedback_loop_closed = TRUE "
                "AND outcome_observed_at >= :cutoff "
                "ORDER BY outcome_observed_at DESC",
                cutoff=TIMESTAMP,
            ),
            {"cutoff": cutoff},
        )

        errors: list[float] = []
        directional_correct = 0
        total = 0

        for row in rows.mappings():
            expected_raw = row["expected_effect"]
            actual_raw = row["actual_effect"]

            if isinstance(expected_raw, str):
                expected_data = json.loads(expected_raw)
            else:
                expected_data = expected_raw or {}
            if isinstance(actual_raw, str):
                actual_data = json.loads(actual_raw)
            else:
                actual_data = actual_raw or {}

            expected = float(expected_data.get("ate", expected_data.get("expected_effect", 0.0) or 0.0))
            actual = float(actual_data.get("observed_value", 0.0) or 0.0)

            error = abs(actual - expected)
            errors.append(error)

            # Directional: did the prediction and actual have the same sign?
            if (expected >= 0 and actual >= 0) or (expected < 0 and actual < 0):
                directional_correct += 1
            total += 1

        if not errors:
            return {"MAE": 0.0, "RMSE": 0.0, "directional_accuracy": 0.0, "sample_count": 0}

        mae = sum(errors) / len(errors)
        rmse = (sum(e**2 for e in errors) / len(errors)) ** 0.5
        dir_acc = directional_correct / total if total > 0 else 0.0

        return {
            "MAE": round(mae, 4),
            "RMSE": round(rmse, 4),
            "directional_accuracy": round(dir_acc, 4),
            "sample_count": total,
            "period_days": period_days,
        }
