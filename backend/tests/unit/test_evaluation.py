"""
Unit tests for production evaluation pipeline.

Tests:
  - ConsistencyRunner with N-run variance
  - CalibrationProbe with confidence score distribution
  - CynefinStabilityProbe with domain history
  - EdgeReproducibilityProbe with proposal sets
  - compute_decision_quality composite index
  - Jaccard distance computation
"""

from __future__ import annotations

import pytest

from src.evaluation.consistency import (
    ConsistencyResult,
    ConsistencyRunner,
    _jaccard_distance,
    _compute_recommendation_variance,
)
from src.evaluation.metrics import compute_decision_quality, DecisionQualityIndex
from src.evaluation.probes import (
    CalibrationProbe,
    CynefinStabilityProbe,
    EdgeReproducibilityProbe,
    ProbeResult,
)
from src.semantic_layer.models import Recommendation


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_rec(action: str = "test", confidence: float = 0.8) -> Recommendation:
    return Recommendation(
        rank=1,
        action=action,
        rationale="test",
        confidence=confidence,
        risk_tier="MEDIUM",
    )


# ── Jaccard distance ──────────────────────────────────────────────────────────


def test_jaccard_identical() -> None:
    """Jaccard distance between identical sets is 0."""
    assert _jaccard_distance({"a", "b"}, {"a", "b"}) == 0.0


def test_jaccard_disjoint() -> None:
    """Jaccard distance between disjoint sets is 1."""
    assert _jaccard_distance({"a"}, {"b"}) == 1.0


def test_jaccard_both_empty() -> None:
    """Jaccard distance between two empty sets is 0."""
    assert _jaccard_distance(set(), set()) == 0.0


def test_jaccard_one_empty() -> None:
    """Jaccard distance when one set is empty is 1."""
    assert _jaccard_distance({"a"}, set()) == 1.0


# ── ConsistencyRunner ─────────────────────────────────────────────────────────


async def test_consistency_empty_runs() -> None:
    """Consistency with no runs returns safe defaults."""
    runner = ConsistencyRunner(num_runs=8)
    result = await runner.evaluate("sess-1", [])
    assert result.total_runs == 0
    assert result.is_consistent is False


async def test_consistency_identical_runs() -> None:
    """Identical recommendations across runs = consistent."""
    runner = ConsistencyRunner(num_runs=3)
    recs = [[_make_rec("Restart", 0.9)]]
    result = await runner.evaluate("sess-1", recs)
    assert result.recommendation_variance == 0.0


async def test_consistency_varying_runs() -> None:
    """Differing recommendations produce detectable variance."""
    runner = ConsistencyRunner(num_runs=3)
    recs = [
        [_make_rec("Scale up", 0.9)],
        [_make_rec("Scale up", 0.9)],
        [_make_rec("Rollback", 0.5)],
    ]
    result = await runner.evaluate("sess-1", recs)
    assert result.recommendation_variance > 0.0


async def test_consistency_with_failed_runs() -> None:
    """Failed (empty) runs are tracked."""
    runner = ConsistencyRunner(num_runs=4)
    recs: list[list[Recommendation]] = [
        [_make_rec("A", 0.9)],
        [],
        [_make_rec("A", 0.9)],
        [],
    ]
    result = await runner.evaluate("sess-1", recs)
    assert result.successful_runs == 2
    assert len(result.failed_runs) == 2


# ── CalibrationProbe ──────────────────────────────────────────────────────────


def test_calibration_empty() -> None:
    """Empty recommendations → probe fails."""
    probe = CalibrationProbe(threshold=0.6)
    result = probe.evaluate([])
    assert result.passed is False
    assert result.score == 0.0


def test_calibration_well_calibrated() -> None:
    """Narrow confidence distribution = well-calibrated."""
    probe = CalibrationProbe(threshold=0.4)
    recs = [_make_rec(f"a{i}", 0.75) for i in range(10)]
    result = probe.evaluate(recs)
    assert result.score > 0.5  # Low stdev, near mid-range → good score
    assert result.metadata["sample_count"] == 10


def test_calibration_high_variance() -> None:
    """Wide confidence spread = worse score."""
    probe = CalibrationProbe(threshold=0.4)
    recs = [
        _make_rec("a", 0.1),
        _make_rec("b", 0.3),
        _make_rec("c", 0.5),
        _make_rec("d", 0.7),
        _make_rec("e", 0.9),
    ]
    result = probe.evaluate(recs)
    assert result.score < 0.8  # Higher variance should reduce score


# ── CynefinStabilityProbe ────────────────────────────────────────────────────


def test_cynefin_stability_empty() -> None:
    """Empty history → fails."""
    probe = CynefinStabilityProbe(threshold=0.8)
    result = probe.evaluate([])
    assert result.passed is False


def test_cynefin_stability_all_same() -> None:
    """Same domain every run → stable."""
    probe = CynefinStabilityProbe(threshold=0.7)
    result = probe.evaluate(["COMPLICATED"] * 10)
    assert result.passed is True
    assert result.score == 1.0


def test_cynefin_stability_mixed() -> None:
    """Mixed domains → lower stability."""
    probe = CynefinStabilityProbe(threshold=0.8)
    result = probe.evaluate(["COMPLICATED", "COMPLEX", "COMPLICATED", "COMPLICATED"])
    assert result.score == 0.75  # 3 out of 4 agree
    assert result.passed is False  # 0.75 < 0.8 threshold


# ── EdgeReproducibilityProbe ──────────────────────────────────────────────────


def test_edge_reproducibility_identical() -> None:
    """Same edges across runs → high reproducibility."""
    probe = EdgeReproducibilityProbe(threshold=0.5)
    edges1 = [{"id": "e1", "source": "a", "target": "b"}]
    edges2 = [{"id": "e1", "source": "a", "target": "b"}]
    result = probe.evaluate([edges1, edges2])
    assert result.score == 1.0
    assert result.passed is True


def test_edge_reproducibility_different() -> None:
    """Different edges → lower reproducibility."""
    probe = EdgeReproducibilityProbe(threshold=0.3)
    edges1 = [{"id": "e1"}]
    edges2 = [{"id": "e2"}]
    result = probe.evaluate([edges1, edges2])
    assert result.score == 0.0


def test_edge_reproducibility_single_run() -> None:
    """Single run → can't evaluate reproducibility."""
    probe = EdgeReproducibilityProbe()
    result = probe.evaluate([[{"id": "e1"}]])
    assert result.passed is False


# ── Decision Quality Index ────────────────────────────────────────────────────


def test_decision_quality_high() -> None:
    """High approval + consistency → high DQI."""
    dqi = compute_decision_quality(
        approval_rate=0.9,
        escalation_rate=0.02,
        consistency_score=0.85,
        calibration_score=0.8,
    )
    assert dqi.score > 0.7
    assert dqi.trend == "improving"


def test_decision_quality_declining() -> None:
    """Low approval + high escalation → declining DQI."""
    dqi = compute_decision_quality(
        approval_rate=0.4,
        escalation_rate=0.3,
        consistency_score=0.5,
        calibration_score=0.4,
    )
    assert dqi.score < 0.5
    assert dqi.trend == "declining"


def test_decision_quality_metadata() -> None:
    """DQI carries period and score metadata."""
    dqi = compute_decision_quality(0.7, 0.1, period_days=7)
    assert isinstance(dqi.score, float)
    assert 0.0 <= dqi.score <= 1.0
    assert dqi.period_start is not None
    assert dqi.period_end is not None
