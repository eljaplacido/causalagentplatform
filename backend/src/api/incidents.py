"""
Incident persistence — Phase-5 follow-up to fix the in-memory-only Incidents page.

Endpoints:
  GET  /api/v1/incidents/            — list recent incidents (newest first)
  GET  /api/v1/incidents/{id}        — single incident with merged decision verdicts

`save_incident()` is called from the triage endpoint after the LangGraph pipeline
returns a final EpistemicState, so submitting an incident through the UI
durably stores the record (title, severity, services, hypotheses, interventions).

Intervention verdicts (approve/reject) are stored in audit_log via interventions.py;
this module joins them in on read so the UI rehydrates the post-decision state.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.db.factory import CloudSqlFactory
from src.middleware.auth import get_current_user

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/incidents", tags=["Incidents"])


# ── Models ────────────────────────────────────────────────────────────────────


class IncidentRecord(BaseModel):
    id: str
    title: str
    severity: str
    affected_services: list[str] = Field(default_factory=list)
    status: str
    session_id: str | None = None
    cynefin_domain: str | None = None
    domain_confidence: float | None = None
    overall_confidence: float | None = None
    guardian_verdict: str | None = None
    draft_explanation: str = ""
    causal_hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    proposed_interventions: list[dict[str, Any]] = Field(default_factory=list)
    submitted_by: str
    created_at: str
    updated_at: str


# ── Persistence helper (called from session.py after triage) ──────────────────


async def save_incident(
    *,
    incident_id: str,
    title: str,
    severity: str,
    affected_services: list[str],
    session_id: str,
    cynefin_domain: str | None,
    domain_confidence: float | None,
    overall_confidence: float | None,
    guardian_verdict: str | None,
    draft_explanation: str,
    causal_hypotheses: list[dict[str, Any]],
    proposed_interventions: list[dict[str, Any]],
    submitted_by: str,
    requires_human_review: bool,
) -> None:
    """Upsert an incident row. Idempotent on `id` (latest payload wins)."""
    now = datetime.now(timezone.utc).isoformat()
    status_value = "ACTIVE" if requires_human_review else "INVESTIGATING"

    async with CloudSqlFactory.get_session_factory()() as session:
        # INSERT OR REPLACE — safe because incident IDs come from upstream (PagerDuty etc.)
        await session.execute(
            text(
                "INSERT OR REPLACE INTO incidents "
                "(id, title, severity, affected_services, status, session_id, "
                "cynefin_domain, domain_confidence, overall_confidence, guardian_verdict, "
                "draft_explanation, causal_hypotheses, proposed_interventions, "
                "submitted_by, created_at, updated_at) "
                "VALUES (:id, :title, :sev, :svcs, :status, :session_id, "
                ":cynefin, :dc, :oc, :verdict, :explain, :hyp, :ints, "
                ":by, COALESCE((SELECT created_at FROM incidents WHERE id = :id), :now), :now)"
            ),
            {
                "id": incident_id,
                "title": title,
                "sev": severity,
                "svcs": json.dumps(affected_services),
                "status": status_value,
                "session_id": session_id,
                "cynefin": cynefin_domain,
                "dc": domain_confidence,
                "oc": overall_confidence,
                "verdict": guardian_verdict,
                "explain": draft_explanation,
                "hyp": json.dumps(causal_hypotheses),
                "ints": json.dumps(proposed_interventions),
                "by": submitted_by,
                "now": now,
            },
        )
        await session.commit()

    log.info(
        "incident.persisted",
        incident_id=incident_id,
        severity=severity,
        verdict=guardian_verdict,
    )


# ── Read paths ────────────────────────────────────────────────────────────────


async def _load_decisions_by_incident(incident_ids: list[str]) -> dict[str, dict[str, str]]:
    """Return {incident_id: {intervention_id: verdict}} from audit_log."""
    if not incident_ids:
        return {}

    placeholders = ", ".join(f":id{i}" for i in range(len(incident_ids)))
    params: dict[str, Any] = {f"id{i}": iid for i, iid in enumerate(incident_ids)}

    async with CloudSqlFactory.get_session_factory()() as session:
        rows = await session.execute(
            text(
                f"SELECT action_type, payload_hash, session_id "
                f"FROM audit_log "
                f"WHERE action_type IN ('INTERVENTION_APPROVED', 'INTERVENTION_REJECTED') "
                f"AND session_id IN ({placeholders})"
            ),
            params,
        )
        out: dict[str, dict[str, str]] = {}
        for r in rows.mappings():
            try:
                payload = json.loads(r["payload_hash"])
            except (json.JSONDecodeError, TypeError):
                continue
            int_id = payload.get("intervention_id")
            inc_id = r["session_id"]
            if not int_id or not inc_id:
                continue
            verdict = "APPROVED" if r["action_type"] == "INTERVENTION_APPROVED" else "REJECTED"
            out.setdefault(inc_id, {})[int_id] = verdict
        return out


def _merge_decisions(
    record: IncidentRecord, decisions: dict[str, str]
) -> IncidentRecord:
    """Apply intervention verdicts from audit_log onto the stored proposed_interventions."""
    if not decisions or not record.proposed_interventions:
        return record
    updated = []
    for intervention in record.proposed_interventions:
        int_id = intervention.get("id")
        if int_id and int_id in decisions:
            intervention = {**intervention, "guardianVerdict": decisions[int_id]}
        updated.append(intervention)
    return record.model_copy(update={"proposed_interventions": updated})


def _row_to_record(row: Any) -> IncidentRecord:
    return IncidentRecord(
        id=str(row["id"]),
        title=str(row["title"]),
        severity=str(row["severity"]),
        affected_services=json.loads(row["affected_services"] or "[]"),
        status=str(row["status"]),
        session_id=row["session_id"],
        cynefin_domain=row["cynefin_domain"],
        domain_confidence=row["domain_confidence"],
        overall_confidence=row["overall_confidence"],
        guardian_verdict=row["guardian_verdict"],
        draft_explanation=row["draft_explanation"] or "",
        causal_hypotheses=json.loads(row["causal_hypotheses"] or "[]"),
        proposed_interventions=json.loads(row["proposed_interventions"] or "[]"),
        submitted_by=str(row["submitted_by"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


@router.get(
    "/",
    response_model=list[IncidentRecord],
    summary="List recent incidents (newest first) with intervention verdicts merged",
)
async def list_incidents(
    limit: int = Query(default=50, ge=1, le=500),
    status_filter: str | None = Query(default=None, alias="status", description="Filter by status (ACTIVE, INVESTIGATING, RESOLVED)"),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[IncidentRecord]:
    params: dict[str, Any] = {"limit": limit}
    where = ""
    if status_filter:
        where = "WHERE status = :status"
        params["status"] = status_filter

    async with CloudSqlFactory.get_session_factory()() as session:
        rows = await session.execute(
            text(
                f"SELECT id, title, severity, affected_services, status, session_id, "
                f"cynefin_domain, domain_confidence, overall_confidence, guardian_verdict, "
                f"draft_explanation, causal_hypotheses, proposed_interventions, "
                f"submitted_by, created_at, updated_at "
                f"FROM incidents {where} ORDER BY created_at DESC LIMIT :limit"
            ),
            params,
        )
        records = [_row_to_record(r) for r in rows.mappings()]

    decisions_by_incident = await _load_decisions_by_incident([r.id for r in records])
    return [
        _merge_decisions(r, decisions_by_incident.get(r.id, {})) for r in records
    ]


@router.get(
    "/{incident_id}",
    response_model=IncidentRecord,
    summary="Fetch a single incident with intervention verdicts merged",
)
async def get_incident(
    incident_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> IncidentRecord:
    async with CloudSqlFactory.get_session_factory()() as session:
        row = await session.execute(
            text(
                "SELECT id, title, severity, affected_services, status, session_id, "
                "cynefin_domain, domain_confidence, overall_confidence, guardian_verdict, "
                "draft_explanation, causal_hypotheses, proposed_interventions, "
                "submitted_by, created_at, updated_at "
                "FROM incidents WHERE id = :id"
            ),
            {"id": incident_id},
        )
        record_row = row.mappings().fetchone()
        if not record_row:
            raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
        record = _row_to_record(record_row)

    decisions = await _load_decisions_by_incident([record.id])
    return _merge_decisions(record, decisions.get(record.id, {}))
