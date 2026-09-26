"""
Platform operational API.

Computed at request time from the live database (SQLite in local development,
PostgreSQL deployed), with no static values:
  GET /api/v1/platform/connectors  — health of each backend component (DB, registry, pipeline,
                                     Neo4j graph store)
  GET /api/v1/platform/metrics     — computed operational metrics from audit_log + sessions
  GET /api/v1/platform/drift       — drift alerts derived from recent rejected verdicts

NOT measured from platform state, and on the register as
`evaluation-endpoints-not-measured` — each computes its answer from inputs the
caller supplied or from a constant, so none of them observes an agent:
  GET  /api/v1/platform/evaluation/consistency       — scores N empty lists against each other
  POST /api/v1/platform/evaluation/calibration       — scores the confidences in the request body
  GET  /api/v1/platform/evaluation/decision-quality  — combines four query parameters

`calibration` here is confidence *spread*, not calibration: calibration needs
realized outcomes to compare predictions against, and nothing in this repository
records one. The decision layer's real calibration gap is a separate register
entry, `decision-calibration`.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.db.factory import CloudSqlFactory
from src.db.neo4j_factory import Neo4jFactory
from src.db.types import TIMESTAMP, timestamp_text, typed_text
from src.middleware.rbac import guard
from src.orchestration.graph import compile_platform_graph
from src.semantic_layer.registry import SemanticLayerRegistry

log = structlog.get_logger(__name__)
router = APIRouter()

#: What the database connector calls itself, by SQLAlchemy dialect name.
_DATABASE_NAMES = {
    "sqlite": "SQLite (local dev database)",
    "postgresql": "PostgreSQL",
}


def _as_utc(value: Any) -> datetime | None:
    """A stored timestamp as an aware UTC datetime (TIMESTAMPTZ or SQLite's ISO text)."""
    text_value = timestamp_text(value)
    if not text_value:
        return None
    try:
        moment = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)

_registry = SemanticLayerRegistry()

#: How long the connectors check waits for Neo4j to answer before calling it
#: unreachable. The route answers a dashboard; it must not hang on a dead host.
NEO4J_CHECK_TIMEOUT_SECONDS = 3.0

#: What the response says when Neo4j is configured and does not answer. Fixed
#: text: the driver's own error names hosts and ports, and can carry credentials;
#: it goes to the server log only.
_NEO4J_UNREACHABLE = "Neo4j is configured (NEO4J_URI) but did not answer a connectivity check."


# ── Response models ───────────────────────────────────────────────────────────


class ConnectorStatus(BaseModel):
    connector_id: str
    connector_name: str
    connector_type: str
    healthy: bool
    last_successful_sync: str | None
    last_attempt_at: str
    error_message: str | None
    objects_mapped: int
    sync_duration_ms: int | None
    raw_output: dict[str, Any]


class DriftAlert(BaseModel):
    id: str
    domain: str
    entity_type: str
    severity: str
    message: str
    detected_at: str
    affected_fragment_ids: list[str]


class WeeklySessionMetric(BaseModel):
    week: str           # Human-readable label e.g. "Mar 31 – Apr 6"
    week_start: str     # ISO8601
    session_count: int
    approval_rate: float
    escalation_count: int


class PolicyPattern(BaseModel):
    rank: int
    rule_id: str
    description: str
    occurrences: int
    last_seen: str


class FragmentStats(BaseModel):
    total: int
    approved: int
    under_review: int
    draft: int
    deprecated: int


class PlatformMetricsResponse(BaseModel):
    weekly_sessions: list[WeeklySessionMetric]
    policy_patterns: list[PolicyPattern]
    fragment_stats: FragmentStats
    total_audit_entries: int
    total_sessions_all_time: int
    overall_approval_rate: float
    last_activity_at: str | None


# ── Policy rule registry (mirrors GovernanceAgent) ────────────────────────────

_POLICY_DESCRIPTIONS = {
    "PR-001": "No cycle detection bypass: DAG acyclicity must hold before any edge is approved.",
    "PR-002": (
        "Grounding risk: prose whose numbers or causal wording the kernel outcome it "
        "reports does not back (score > 0.7) cannot be APPROVED."
    ),
    "PR-003": "Chaotic domain mandatory escalation: CHAOTIC inputs route to human.",
    "PR-004": "Double-rejection escalation: same payload rejected twice triggers REQUIRES_ESCALATION.",
    "PR-005": "Cycle count limit: cycle_count >= 5 requires REQUIRES_ESCALATION.",
    "PR-006": "DISORDER domain: no verdict can be APPROVED in DISORDER domain.",
    "PR-007": (
        "Sensitivity analysis: an edge is not APPROVED without a sensitivity result the "
        "platform recorded for it (RECORD_SENSITIVITY) on an assumption it rests on."
    ),
    "PR-010": (
        "Planner escalation: a run whose lookahead found no safe action "
        "(plan_outcome no_safe_action) cannot be APPROVED; it requires escalation."
    ),
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _week_label(dt: datetime) -> str:
    """Return 'Mon D – Mon D' label for the ISO week containing dt."""
    start = dt - timedelta(days=dt.weekday())
    end = start + timedelta(days=6)
    # Use %d and strip leading zero manually (cross-platform: avoids %-d on Windows)
    s_day = start.strftime("%b") + " " + str(start.day)
    e_day = end.strftime("%b") + " " + str(end.day)
    return f"{s_day} – {e_day}"


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get(
    "/connectors",
    response_model=list[ConnectorStatus],
    summary="Real-time health of all backend components",
)
async def get_connector_health(
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/platform/connectors")),
) -> list[ConnectorStatus]:
    """
    Check the health of every backend component and return current status.

    Components checked:
      - The database (connection + record counts), named by its dialect
      - Semantic layer registry (entities, fragments, assumptions)
      - Audit log (entry count, last entry timestamp)
      - Neo4j graph store: unconfigured / reachable / unreachable (optional;
        never echoes the driver's error text)
      - LangGraph agent pipeline (compiled graph check)
    """
    results: list[ConnectorStatus] = []

    # ── 1. Database ──────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    db_healthy = False
    db_error: str | None = None
    db_raw: dict[str, Any] = {}
    db_last_sync: str | None = None
    objects_mapped = 0

    try:
        async with CloudSqlFactory.get_session_factory()() as session:
            # Collect actual counts from all tables
            tables = {
                "audit_log": "SELECT COUNT(*) FROM audit_log",
                "causal_entities": "SELECT COUNT(*) FROM causal_entities",
                "graph_fragments": "SELECT COUNT(*) FROM graph_fragments",
                "causal_edges": "SELECT COUNT(*) FROM causal_edges",
                "assumptions": "SELECT COUNT(*) FROM assumptions",
            }
            for tbl, sql in tables.items():
                try:
                    r = await session.execute(text(sql))
                    db_raw[tbl] = r.scalar() or 0
                except Exception:
                    db_raw[tbl] = 0

            # Last audit entry timestamp
            try:
                row = await session.execute(
                    text("SELECT MAX(created_at) FROM audit_log")
                )
                db_last_sync = timestamp_text(row.scalar())
            except Exception:
                pass

        objects_mapped = sum(db_raw.values())
        db_healthy = True
    except Exception as exc:
        db_error = str(exc)
        db_raw["error"] = str(exc)

    db_duration = round((time.perf_counter() - t0) * 1000)

    # Named for the database it is connected to. It said "SQLite (local dev
    # database)" whatever the engine was, so a PostgreSQL deployment reported
    # itself as SQLite.
    dialect = CloudSqlFactory.get_engine().dialect.name
    results.append(
        ConnectorStatus(
            connector_id=f"db-{dialect}",
            connector_name=_DATABASE_NAMES.get(dialect, dialect),
            connector_type="DATABASE",
            healthy=db_healthy,
            last_successful_sync=db_last_sync if db_healthy else None,
            last_attempt_at=_now_iso(),
            error_message=db_error,
            objects_mapped=objects_mapped,
            sync_duration_ms=db_duration,
            raw_output=db_raw,
        )
    )

    # ── 2. Semantic Layer Registry ───────────────────────────────────────────
    t0 = time.perf_counter()
    sem_healthy = False
    sem_error: str | None = None
    sem_raw: dict[str, Any] = {}
    sem_objects = 0
    sem_last_sync: str | None = db_last_sync

    try:
        entities = await _registry.list_entities()
        fragments = await _registry.list_fragments()
        sem_raw = {
            "entities": len(list(entities)),
            "fragments": len(list(fragments)),
            "status": "registry_operational",
        }
        sem_objects = sem_raw["entities"] + sem_raw["fragments"]
        sem_healthy = True
    except Exception as exc:
        sem_error = str(exc)
        sem_raw["error"] = str(exc)

    sem_duration = round((time.perf_counter() - t0) * 1000)

    results.append(
        ConnectorStatus(
            connector_id="semantic-registry",
            connector_name="Semantic Layer Registry",
            connector_type="CUSTOM",
            healthy=sem_healthy,
            last_successful_sync=sem_last_sync if sem_healthy else None,
            last_attempt_at=_now_iso(),
            error_message=sem_error,
            objects_mapped=sem_objects,
            sync_duration_ms=sem_duration,
            raw_output=sem_raw,
        )
    )

    # ── 3. Audit Log ─────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    audit_healthy = False
    audit_error: str | None = None
    audit_raw: dict[str, Any] = {}
    audit_objects = 0
    audit_last: str | None = None

    try:
        async with CloudSqlFactory.get_session_factory()() as session:
            r = await session.execute(
                text(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN guardian_verdict='APPROVED' THEN 1 ELSE 0 END) as approved, "
                    "SUM(CASE WHEN guardian_verdict='REJECTED' THEN 1 ELSE 0 END) as rejected, "
                    "SUM(CASE WHEN guardian_verdict='REQUIRES_ESCALATION' THEN 1 ELSE 0 END) as escalated, "
                    "MAX(created_at) as last_entry "
                    "FROM audit_log"
                )
            )
            row = r.mappings().fetchone()
            if row:
                audit_raw = {
                    "total_entries": row["total"] or 0,
                    "approved": row["approved"] or 0,
                    "rejected": row["rejected"] or 0,
                    "escalated": row["escalated"] or 0,
                }
                audit_objects = audit_raw["total_entries"]
                audit_last = timestamp_text(row["last_entry"])
        audit_healthy = True
    except Exception as exc:
        audit_error = str(exc)
        audit_raw["error"] = str(exc)

    audit_duration = round((time.perf_counter() - t0) * 1000)

    results.append(
        ConnectorStatus(
            connector_id="audit-log",
            connector_name="Guardian Audit Log",
            connector_type="CUSTOM",
            healthy=audit_healthy,
            last_successful_sync=audit_last if audit_healthy else None,
            last_attempt_at=_now_iso(),
            error_message=audit_error,
            objects_mapped=audit_objects,
            sync_duration_ms=audit_duration,
            raw_output=audit_raw,
        )
    )

    # ── 4. Neo4j graph store ─────────────────────────────────────────────────
    results.append(await _neo4j_status())

    # ── 5. LangGraph Agent Pipeline ──────────────────────────────────────────
    t0 = time.perf_counter()
    pipeline_healthy = False
    pipeline_error: str | None = None
    pipeline_raw: dict[str, Any] = {}

    try:
        graph = compile_platform_graph()
        # Introspect graph nodes
        pipeline_raw = {
            "status": "compiled",
            "nodes": list(graph.nodes) if hasattr(graph, "nodes") else ["graph_ready"],
        }
        pipeline_healthy = True
    except Exception as exc:
        pipeline_error = str(exc)
        pipeline_raw["error"] = str(exc)

    pipeline_duration = round((time.perf_counter() - t0) * 1000)

    results.append(
        ConnectorStatus(
            connector_id="agent-pipeline",
            connector_name="LangGraph Agent Pipeline",
            connector_type="CUSTOM",
            healthy=pipeline_healthy,
            last_successful_sync=_now_iso() if pipeline_healthy else None,
            last_attempt_at=_now_iso(),
            error_message=pipeline_error,
            objects_mapped=0,
            sync_duration_ms=pipeline_duration,
            raw_output=pipeline_raw,
        )
    )

    log.info(
        "platform.connectors.checked",
        healthy=sum(1 for r in results if r.healthy),
        total=len(results),
    )
    return results


async def _neo4j_status() -> ConnectorStatus:
    """Neo4j as one of three states: unconfigured, reachable, unreachable.

    Neo4j is optional: with NEO4J_URI unset the graph and experience-buffer paths
    fall back to SQL, so "unconfigured" is healthy. Configured and not answering
    (no driver, a connectivity error, or no answer within
    NEO4J_CHECK_TIMEOUT_SECONDS) is unhealthy, reported with a fixed message.
    """
    t0 = time.perf_counter()
    configured = bool(os.getenv("NEO4J_URI"))
    reachable: bool | None = None
    if configured:
        reachable = False
        driver = Neo4jFactory.get_driver()
        if driver is None:
            log.warning("platform.connectors.neo4j_unreachable", reason="no_driver")
        else:
            try:
                await asyncio.wait_for(
                    driver.verify_connectivity(), timeout=NEO4J_CHECK_TIMEOUT_SECONDS
                )
                reachable = True
            except TimeoutError:
                log.warning("platform.connectors.neo4j_unreachable", reason="timeout")
            except Exception as exc:
                # The error text stays here, in the server log.
                log.warning(
                    "platform.connectors.neo4j_unreachable",
                    reason="connectivity_error",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
    status = "unconfigured" if not configured else ("reachable" if reachable else "unreachable")
    healthy = status != "unreachable"
    return ConnectorStatus(
        connector_id="graph-neo4j",
        connector_name="Neo4j graph store",
        connector_type="CUSTOM",
        healthy=healthy,
        last_successful_sync=_now_iso() if reachable else None,
        last_attempt_at=_now_iso(),
        error_message=None if healthy else _NEO4J_UNREACHABLE,
        objects_mapped=0,
        sync_duration_ms=round((time.perf_counter() - t0) * 1000),
        raw_output={"configured": configured, "reachable": reachable, "status": status},
    )


@router.get(
    "/metrics",
    response_model=PlatformMetricsResponse,
    summary="Operational metrics computed from live session and audit data",
)
async def get_platform_metrics(
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/platform/metrics")),
) -> PlatformMetricsResponse:
    """
    Compute real platform metrics from audit_log and semantic layer.

    Returns:
      - Weekly session counts and approval rates (last 4 weeks)
      - Most-cited Guardian policy rules (top 5)
      - Semantic layer object counts by approval status
      - Aggregate totals
    """
    now = datetime.now(UTC)
    # The four weeks ending with this one, oldest first, each keyed by its Monday.
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    mondays = [this_monday - timedelta(weeks=k) for k in (3, 2, 1, 0)]

    weekly_sessions: list[WeeklySessionMetric] = []
    policy_patterns: list[PolicyPattern] = []
    fragment_stats = FragmentStats(total=0, approved=0, under_review=0, draft=0, deprecated=0)
    total_entries = 0
    total_sessions = 0
    overall_approval_rate = 0.0
    last_activity: str | None = None

    async with CloudSqlFactory.get_session_factory()() as session:
        # ── Weekly session metrics ───────────────────────────────────────────
        # Group audit_log entries by week. "Sessions" are the distinct session_ids
        # among them; the verdict figures (approval rate, escalations) count every
        # row, as overall_approval_rate does. They used to count only rows with a
        # session_id, so this week's rate and the overall one were over different
        # rows (the executive page showed 76% beside 74%), and escalations recorded
        # without a session -- the seeder's, the governance API's -- were never
        # counted ("Escalations (4w) 0" beside an audit trail listing them).
        # Grouped here, not in SQL: strftime() is SQLite's, and on PostgreSQL the
        # failed statement also aborted the transaction, so every query after it in
        # this session failed too and the endpoint returned nothing but padding.
        weeks: dict[datetime, dict[str, Any]] = {
            m: {"sessions": set(), "approved": 0, "total": 0, "escalated": 0} for m in mondays
        }
        try:
            weekly_rows = await session.execute(
                typed_text(
                    "SELECT created_at, session_id, guardian_verdict "
                    "FROM audit_log "
                    "WHERE created_at >= :cutoff",
                    cutoff=TIMESTAMP,
                ),
                {"cutoff": mondays[0]},
            )
            for row in weekly_rows.mappings():
                at = _as_utc(row["created_at"])
                if at is None:
                    continue
                monday = (at - timedelta(days=at.weekday())).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                week = weeks.get(monday)
                if week is None:
                    continue
                if row["session_id"] is not None:
                    week["sessions"].add(row["session_id"])
                week["total"] += 1
                week["approved"] += row["guardian_verdict"] == "APPROVED"
                week["escalated"] += row["guardian_verdict"] == "REQUIRES_ESCALATION"
        except Exception as exc:
            log.warning("platform.metrics.weekly_query_failed", error=str(exc))

        for monday in mondays:
            week = weeks[monday]
            weekly_sessions.append(
                WeeklySessionMetric(
                    week=_week_label(monday),
                    week_start=monday.isoformat(),
                    session_count=len(week["sessions"]),
                    approval_rate=round(week["approved"] / max(week["total"], 1), 3),
                    escalation_count=week["escalated"],
                )
            )

        # ── Policy rule patterns ─────────────────────────────────────────────
        try:
            policy_rows = await session.execute(
                text(
                    "SELECT policy_rules_cited, MAX(created_at) as last_seen "
                    "FROM audit_log "
                    # No "!= '[]'": that literal is not an array on PostgreSQL (TEXT[]),
                    # so the filter failed there. Empty citations are skipped below.
                    "WHERE policy_rules_cited IS NOT NULL "
                    "GROUP BY policy_rules_cited "
                    "ORDER BY COUNT(*) DESC "
                    "LIMIT 20"
                )
            )
            rule_counts: dict[str, dict[str, Any]] = {}
            for row in policy_rows.mappings():
                raw = row["policy_rules_cited"]
                if isinstance(raw, str):
                    import json as _json
                    try:
                        rules = _json.loads(raw)
                    except Exception:
                        rules = [raw]
                else:
                    rules = raw or []
                for rule in rules:
                    if rule not in rule_counts:
                        rule_counts[rule] = {"count": 0, "last_seen": row["last_seen"]}
                    rule_counts[rule]["count"] += 1
                    if row["last_seen"] > rule_counts[rule]["last_seen"]:
                        rule_counts[rule]["last_seen"] = row["last_seen"]

            sorted_rules = sorted(rule_counts.items(), key=lambda x: x[1]["count"], reverse=True)
            for rank, (rule, data) in enumerate(sorted_rules[:5], start=1):
                policy_patterns.append(
                    PolicyPattern(
                        rank=rank,
                        rule_id=rule,
                        description=_POLICY_DESCRIPTIONS.get(rule, f"Policy rule {rule}"),
                        occurrences=data["count"],
                        last_seen=timestamp_text(data["last_seen"]) or "",
                    )
                )
        except Exception as exc:
            log.warning("platform.metrics.policy_query_failed", error=str(exc))

        # ── Aggregate totals ─────────────────────────────────────────────────
        try:
            totals = await session.execute(
                text(
                    "SELECT COUNT(*) as total, "
                    "MAX(created_at) as last_at, "
                    "SUM(CASE WHEN guardian_verdict='APPROVED' THEN 1 ELSE 0 END) as approved "
                    "FROM audit_log"
                )
            )
            t = totals.mappings().fetchone()
            if t:
                total_entries = t["total"] or 0
                last_activity = timestamp_text(t["last_at"])
                if total_entries > 0:
                    overall_approval_rate = round((t["approved"] or 0) / total_entries, 3)
        except Exception as exc:
            log.warning("platform.metrics.totals_query_failed", error=str(exc))

        try:
            sessions_row = await session.execute(
                text(
                    "SELECT COUNT(DISTINCT session_id) as cnt "
                    "FROM audit_log WHERE session_id IS NOT NULL"
                )
            )
            total_sessions = sessions_row.scalar() or 0
        except Exception:
            pass

    # ── Fragment stats from semantic layer ───────────────────────────────────
    try:
        fragments = list(await _registry.list_fragments())
        fragment_stats = FragmentStats(
            total=len(fragments),
            approved=sum(1 for f in fragments if str(f.approval_status) == "APPROVED"),
            under_review=sum(1 for f in fragments if str(f.approval_status) == "UNDER_REVIEW"),
            draft=sum(1 for f in fragments if str(f.approval_status) == "DRAFT"),
            deprecated=sum(1 for f in fragments if str(f.approval_status) == "DEPRECATED"),
        )
    except Exception as exc:
        log.warning("platform.metrics.fragment_stats_failed", error=str(exc))

    return PlatformMetricsResponse(
        weekly_sessions=weekly_sessions,
        policy_patterns=policy_patterns,
        fragment_stats=fragment_stats,
        total_audit_entries=total_entries,
        total_sessions_all_time=total_sessions,
        overall_approval_rate=overall_approval_rate,
        last_activity_at=last_activity,
    )


@router.get(
    "/drift",
    response_model=list[DriftAlert],
    summary="Drift alerts derived from recent rejected Guardian verdicts",
)
async def get_drift_alerts(
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/platform/drift")),
) -> list[DriftAlert]:
    """
    Compute drift alerts from recent audit_log activity.

    A drift alert is raised when:
      - Multiple REJECTED verdicts occurred for the same policy rule within 24h
      - The REQUIRES_ESCALATION verdict was triggered (indicates state anomaly)
    """
    alerts: list[DriftAlert] = []
    cutoff = datetime.now(UTC) - timedelta(hours=24)

    try:
        async with CloudSqlFactory.get_session_factory()() as session:
            rows = await session.execute(
                typed_text(
                    "SELECT id, agent_id, guardian_verdict, policy_rules_cited, "
                    "session_id, created_at "
                    "FROM audit_log "
                    "WHERE guardian_verdict IN ('REJECTED', 'REQUIRES_ESCALATION') "
                    "  AND created_at >= :cutoff "
                    "ORDER BY created_at DESC "
                    "LIMIT 50",
                    cutoff=TIMESTAMP,
                ),
                {"cutoff": cutoff},
            )
            import json as _json

            rule_rejections: dict[str, list[dict[str, Any]]] = {}
            for row in rows.mappings():
                raw = row["policy_rules_cited"]
                rules: list[str] = []
                if isinstance(raw, str):
                    try:
                        rules = _json.loads(raw)
                    except Exception:
                        rules = [raw] if raw else []
                elif isinstance(raw, list):
                    rules = raw

                for rule in rules or ["UNKNOWN"]:
                    if rule not in rule_rejections:
                        rule_rejections[rule] = []
                    rule_rejections[rule].append(dict(row))

                # REQUIRES_ESCALATION always surfaces as a drift alert
                if row["guardian_verdict"] == "REQUIRES_ESCALATION":
                    alerts.append(
                        DriftAlert(
                            id=str(row["id"]),
                            domain="platform",
                            entity_type="Session",
                            severity="HIGH",
                            # Only what the row records: its agent, its session
                            # when it has one ("Session unknown" named a session
                            # that does not exist), and the rules it cites. It
                            # used to add "agent circuit breaker or
                            # double-rejection detected", a cause the row does
                            # not record (PR-003, a CHAOTIC run, escalates too).
                            message=(
                                f"{row['agent_id']} recorded a REQUIRES_ESCALATION verdict"
                                + (f" in session {row['session_id']}" if row["session_id"] else "")
                                + (f", citing {', '.join(rules)}" if rules else "")
                                + "."
                            ),
                            detected_at=timestamp_text(row["created_at"]) or "",
                            affected_fragment_ids=[],
                        )
                    )

            # Aggregate rejection clusters by policy rule
            for rule, entries in rule_rejections.items():
                if len(entries) >= 2:
                    severity = "CRITICAL" if len(entries) >= 5 else "HIGH" if len(entries) >= 3 else "MEDIUM"
                    desc = _POLICY_DESCRIPTIONS.get(rule, f"Policy rule {rule}")
                    alerts.append(
                        DriftAlert(
                            id=f"drift-{rule}-{int(time.time())}",
                            domain="governance",
                            entity_type="CausalEdge",
                            severity=severity,
                            message=(
                                f"Guardian rule {rule} triggered {len(entries)} times in 24h. "
                                f"Rule: {desc}"
                            ),
                            detected_at=timestamp_text(entries[0]["created_at"]) or "",
                            affected_fragment_ids=[
                                e["session_id"] for e in entries if e.get("session_id")
                            ][:5],
                        )
                    )

    except Exception as exc:
        log.warning("platform.drift.query_failed", error=str(exc))

    # De-duplicate by id
    seen: set[str] = set()
    unique: list[DriftAlert] = []
    for alert in alerts:
        if alert.id not in seen:
            seen.add(alert.id)
            unique.append(alert)

    return sorted(unique, key=lambda a: a.detected_at, reverse=True)


# ── Evaluation (Phase 3B) ─────────────────────────────────────────────────────


class ConsistencyResponse(BaseModel):
    session_id: str
    total_runs: int
    successful_runs: int
    recommendation_variance: float
    confidence_mean: float
    confidence_variance: float
    is_consistent: bool
    runs_elapsed_ms: float


class ProbeResponse(BaseModel):
    probe_name: str
    score: float
    threshold: float
    passed: bool
    detail: str
    probe_duration_ms: float


class DecisionQualityResponse(BaseModel):
    score: float
    approval_rate: float
    escalation_rate: float
    recommendation_consistency: float
    calibration_score: float
    trend: str
    period_start: str
    period_end: str


class CalibrationRequest(BaseModel):
    """Body for POST /evaluation/calibration — supply recommendation confidence scores."""
    confidences: list[float]
    threshold: float | None = None


@router.get(
    "/evaluation/consistency",
    response_model=ConsistencyResponse,
    summary="Agent output consistency across repeated runs",
)
async def get_consistency(
    session_id: str = "test",
    num_runs: int = Query(default=8, ge=2, le=100),
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/platform/evaluation/consistency")),
) -> ConsistencyResponse:
    """
    Measure agent output variance across N consecutive runs.

    A passing score (variance < 0.15) means the agent produces stable
    recommendations for the same input.
    """
    from src.evaluation.consistency import ConsistencyRunner

    # Simulate N identical empty sessions for baseline measurement
    # In production: run the actual session pipeline N times
    mock_recommendations: list[list[Any]] = [[] for _ in range(num_runs)]

    runner = ConsistencyRunner(num_runs=num_runs)
    result = await runner.evaluate(session_id, mock_recommendations)

    return ConsistencyResponse(
        session_id=result.session_id,
        total_runs=result.total_runs,
        successful_runs=result.successful_runs,
        recommendation_variance=result.recommendation_variance,
        confidence_mean=result.confidence_mean,
        confidence_variance=result.confidence_variance,
        is_consistent=result.is_consistent,
        runs_elapsed_ms=result.runs_elapsed_ms,
    )


@router.post(
    "/evaluation/calibration",
    response_model=ProbeResponse,
    summary="Agent confidence calibration probe",
)
async def post_calibration(
    body: CalibrationRequest,
    user: dict[str, Any] = Depends(guard("POST", "/api/v1/platform/evaluation/calibration")),
) -> ProbeResponse:
    """
    Evaluate whether agent confidence scores are calibrated.

    A well-calibrated agent means confidence 0.8 ≈ 80% correct.
    """
    from src.evaluation.probes import CalibrationProbe
    from src.semantic_layer.models import Recommendation

    threshold = body.threshold if body.threshold is not None else 0.6
    probe = CalibrationProbe(threshold=threshold)

    recs = [
        Recommendation(
            rank=i + 1,
            action=f"action_{i}",
            rationale="evaluation probe",
            confidence=c,
            risk_tier="MEDIUM",
        )
        for i, c in enumerate(body.confidences)
    ]
    result = probe.evaluate(recs)

    return ProbeResponse(
        probe_name=result.probe_name,
        score=result.score,
        threshold=result.threshold,
        passed=result.passed,
        detail=result.detail,
        probe_duration_ms=result.probe_duration_ms,
    )


@router.get(
    "/evaluation/decision-quality",
    response_model=DecisionQualityResponse,
    summary="Composite decision quality index",
)
async def get_decision_quality(
    approval_rate: float = Query(default=0.75, ge=0.0, le=1.0),
    escalation_rate: float = Query(default=0.05, ge=0.0, le=1.0),
    consistency_score: float = Query(default=0.80, ge=0.0, le=1.0),
    calibration_score: float = Query(default=0.70, ge=0.0, le=1.0),
    period_days: int = Query(default=30, ge=1, le=365),
    user: dict[str, Any] = Depends(guard("GET", "/api/v1/platform/evaluation/decision-quality")),
) -> DecisionQualityResponse:
    """
    Compute a composite decision quality index from platform metrics.

    In production, approval_rate and escalation_rate are computed from
    audit_log data. Here they are supplied as query params for API testing.
    """
    from src.evaluation.metrics import compute_decision_quality

    dqi = compute_decision_quality(
        approval_rate=approval_rate,
        escalation_rate=escalation_rate,
        consistency_score=consistency_score,
        calibration_score=calibration_score,
        period_days=period_days,
    )

    return DecisionQualityResponse(
        score=dqi.score,
        approval_rate=dqi.approval_rate,
        escalation_rate=dqi.escalation_rate,
        recommendation_consistency=dqi.recommendation_consistency,
        calibration_score=dqi.calibration_score,
        trend=dqi.trend,
        period_start=dqi.period_start,
        period_end=dqi.period_end,
    )
