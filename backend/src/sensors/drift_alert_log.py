"""
The SQL record of drift alerts (septa-62).

Drift alerts used to be stored only as ``(:DriftAlert)`` nodes in Neo4j, and only
when ``NEO4J_URI`` was set. Neo4j is an optional side store; without it an alert
was logged by the in-process AlertEmitter and kept nowhere. DriftMonitorAgent now
writes every alert that Neo4j did not store (not configured, storage disabled, or
the write failed) to the ``drift_alerts`` table here, so every alert leaves a
record in the platform's own database (``015_drift_alerts.sql``, and the
``SQLITE_DDL`` rendering of it).

The table is append-only: a record is inserted once and never edited. There is
no acknowledgement here; acknowledging an alert is a Neo4j-only feature.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import bindparam, column, select, table

from src.db.factory import CloudSqlFactory
from src.db.types import TIMESTAMP, json_value, timestamp_text, typed_text

log = structlog.get_logger(__name__)

#: The severities the table accepts (015's CHECK constraint).
SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})

_DRIFT_ALERTS = table(
    "drift_alerts",
    column("id"),
    column("alert_type"),
    column("severity"),
    column("drift_type"),
    column("fragment_id"),
    column("drift_score"),
    column("drifted_features"),
    column("description"),
    column("recommendation"),
    column("context"),
    column("session_id"),
    column("agent_id"),
    column("created_at"),
)


class DriftAlertLog:
    """Write and read the ``drift_alerts`` table."""

    async def record(
        self,
        *,
        alert_type: str,
        severity: str,
        drift_type: str,
        drift_score: float,
        description: str,
        agent_id: str,
        alert_id: str | None = None,
        fragment_id: str | None = None,
        drifted_features: list[str] | None = None,
        recommendation: str | None = None,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Insert one alert and return its id. Raises if the row is not written."""
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {sorted(SEVERITIES)}, not {severity!r}")
        row_id = alert_id or str(uuid4())
        async with CloudSqlFactory.get_session_factory()() as session:
            await session.execute(
                typed_text(
                    "INSERT INTO drift_alerts (id, alert_type, severity, drift_type, "
                    " fragment_id, drift_score, drifted_features, description, "
                    " recommendation, context, session_id, agent_id, created_at) "
                    "VALUES (:id, :alert_type, :severity, :drift_type, :fragment_id, "
                    " :drift_score, :drifted_features, :description, :recommendation, "
                    " :context, :session_id, :agent_id, :created_at)",
                    created_at=TIMESTAMP,
                ),
                {
                    "id": row_id,
                    "alert_type": alert_type,
                    "severity": severity,
                    "drift_type": drift_type,
                    "fragment_id": fragment_id or None,
                    "drift_score": float(drift_score),
                    "drifted_features": json.dumps(list(drifted_features or [])),
                    "description": description,
                    "recommendation": recommendation,
                    "context": json.dumps(context or {}, default=str),
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "created_at": datetime.now(UTC),
                },
            )
            await session.commit()
        log.info("drift_alert.recorded_sql", alert_id=row_id, alert_type=alert_type)
        return row_id

    async def recent(
        self,
        *,
        fragment_id: str | None = None,
        drift_type: str | None = None,
        severity: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """The most recent alerts, newest first, optionally filtered."""
        t = _DRIFT_ALERTS
        clauses = []
        params: dict[str, Any] = {"limit": int(limit)}
        if fragment_id:
            clauses.append(t.c.fragment_id == bindparam("fragment_id"))
            params["fragment_id"] = fragment_id
        if drift_type:
            clauses.append(t.c.drift_type == bindparam("drift_type"))
            params["drift_type"] = drift_type
        if severity:
            clauses.append(t.c.severity == bindparam("severity"))
            params["severity"] = severity
        stmt = select(*t.c).where(*clauses).order_by(t.c.created_at.desc()).limit(
            bindparam("limit")
        )
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = (await session.execute(stmt, params)).mappings().all()
        return [
            {
                "alert_id": str(r["id"]),
                "alert_type": r["alert_type"],
                "severity": r["severity"],
                "drift_type": r["drift_type"],
                "fragment_id": r["fragment_id"],
                "drift_score": float(r["drift_score"]),
                "drifted_features": json_value(r["drifted_features"], []),
                "description": r["description"],
                "recommendation": r["recommendation"],
                "context": json_value(r["context"], {}),
                "session_id": r["session_id"],
                "agent_id": r["agent_id"],
                "created_at": timestamp_text(r["created_at"]),
            }
            for r in rows
        ]
