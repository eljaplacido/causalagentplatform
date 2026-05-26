"""
Continuous production metrics computed from audit_log and EpistemicState.

Provides aggregated views of agent decision quality over time:
  - Approval rate trend (30-day rolling)
  - Policy rule trigger frequency
  - Decision quality index (composite score)
  - Agent session success rate

These metrics fuel the evaluation dashboard and experience buffer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class DecisionQualityIndex:
    """Composite score measuring agent decision quality."""

    score: float                         # 0.0–1.0
    approval_rate: float                 # Guardian APPROVED / total checks
    escalation_rate: float               # REQUIRES_ESCALATION / total checks
    recommendation_consistency: float    # Average consistency across sessions
    calibration_score: float             # From CalibrationProbe
    trend: str                           # "improving", "stable", "declining"
    period_start: str                    # ISO8601
    period_end: str                      # ISO8601
    session_count: int


@dataclass
class MetricsSummary:
    """Aggregate metrics snapshot for a time window."""

    total_sessions: int
    total_audit_entries: int
    approval_rate: float
    rejection_rate: float
    escalation_rate: float
    top_policy_rules: list[dict[str, Any]]
    decision_quality: DecisionQualityIndex | None


def compute_decision_quality(
    approval_rate: float,
    escalation_rate: float,
    consistency_score: float = 0.8,
    calibration_score: float = 0.7,
    *,
    period_days: int = 30,
) -> DecisionQualityIndex:
    """
    Compute a composite decision quality index.

    Weights: approval (0.35), consistency (0.25), calibration (0.25), escalation (0.15).
    """
    escalation_penalty = 1.0 - min(1.0, escalation_rate * 3.0)

    score = (
        (approval_rate * 0.35)
        + (consistency_score * 0.25)
        + (calibration_score * 0.25)
        + (escalation_penalty * 0.15)
    )

    if score >= 0.8:
        trend = "improving"
    elif score >= 0.5:
        trend = "stable"
    else:
        trend = "declining"

    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=period_days)).isoformat()

    return DecisionQualityIndex(
        score=round(score, 4),
        approval_rate=round(approval_rate, 4),
        escalation_rate=round(escalation_rate, 4),
        recommendation_consistency=round(consistency_score, 4),
        calibration_score=round(calibration_score, 4),
        trend=trend,
        period_start=start,
        period_end=now.isoformat(),
        session_count=0,
    )
