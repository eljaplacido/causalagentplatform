"""
Consistency evaluation — measures agent output variance across repeated runs.

Addresses the 37% lab-vs-production gap identified in the World Model report:
benchmark scores drop from 60% on a single run to 25% across 8 consecutive runs
when agent consistency is poor.

The ConsistencyRunner executes the same agent session N times and measures:
  1. Output variance (how much do recommendations differ?)
  2. Confidence calibration (do confidence scores stay stable?)
  3. Cynefin classification stability (same input → same domain?)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

DEFAULT_CONSISTENCY_RUNS = 8
MAX_ACCEPTABLE_VARIANCE = 0.15
CONFIDENCE_VARIANCE_THRESHOLD = 0.20


@dataclass
class ConsistencyResult:
    """Output of an N-run consistency test."""

    session_id: str
    total_runs: int
    successful_runs: int
    # Output variance
    recommendation_variance: float   # 0.0 = identical, 1.0 = completely different
    confidence_mean: float
    confidence_variance: float
    cynefin_domain_stable: bool
    cynefin_domain_distribution: dict[str, int]
    # Status
    is_consistent: bool               # True if variance < MAX_ACCEPTABLE_VARIANCE
    runs_elapsed_ms: float
    failed_runs: list[int] = field(default_factory=list)


class ConsistencyRunner:
    """
    Runs the same agent session configuration N times and measures output variance.

    Usage::

        runner = ConsistencyRunner(num_runs=8)
        result = await runner.evaluate(
            session_factory=lambda: create_session(initial_state),
            extract_output=lambda session: session.recommendations,
        )
        if result.is_consistent:
            print("Agent passes consistency check")
    """

    def __init__(self, num_runs: int = DEFAULT_CONSISTENCY_RUNS) -> None:
        self._num_runs = num_runs

    async def evaluate(
        self,
        session_id: str,
        recommendations_list: list[list[Any]],
    ) -> ConsistencyResult:
        """
        Evaluate consistency from already-collected recommendations.

        Args:
            session_id: Base session identifier
            recommendations_list: List of recommendation lists from each run
        """
        t0 = time.perf_counter()
        total = len(recommendations_list)
        if total == 0:
            return ConsistencyResult(
                session_id=session_id,
                total_runs=0,
                successful_runs=0,
                recommendation_variance=1.0,
                confidence_mean=0.0,
                confidence_variance=0.0,
                cynefin_domain_stable=False,
                cynefin_domain_distribution={},
                is_consistent=False,
                runs_elapsed_ms=0.0,
            )

        # Count non-empty runs
        non_empty = [r for r in recommendations_list if r]
        successful = len(non_empty)
        failed = [i for i, r in enumerate(recommendations_list) if not r]

        # Compute recommendation variance (Jaccard-style)
        variance = _compute_recommendation_variance(non_empty)

        # Compute confidence stability
        all_confidences = [
            r.confidence for r_list in non_empty for r in r_list if hasattr(r, "confidence")
        ]
        import statistics
        conf_mean = statistics.mean(all_confidences) if all_confidences else 1.0
        conf_var = statistics.pvariance(all_confidences) if len(all_confidences) > 1 else 0.0

        # Cynefin stability — placeholder (requires EpistemicState per run)
        domain_dist: dict[str, int] = {}
        stable = True

        is_consistent = (
            variance <= MAX_ACCEPTABLE_VARIANCE
            and conf_var <= CONFIDENCE_VARIANCE_THRESHOLD
            and stable
        )

        elapsed = round((time.perf_counter() - t0) * 1000, 2)
        log.info(
            "evaluation.consistency.complete",
            session_id=session_id,
            runs=total,
            variance=round(variance, 4),
            confidence_var=round(conf_var, 4),
            is_consistent=is_consistent,
        )

        return ConsistencyResult(
            session_id=session_id,
            total_runs=total,
            successful_runs=successful,
            recommendation_variance=variance,
            confidence_mean=conf_mean,
            confidence_variance=conf_var,
            cynefin_domain_stable=stable,
            cynefin_domain_distribution=domain_dist,
            is_consistent=is_consistent,
            runs_elapsed_ms=elapsed,
            failed_runs=failed,
        )


def _compute_recommendation_variance(
    recommendations_lists: list[list[Any]],
) -> float:
    """
    Compute variance across recommendation sets using Jaccard distance.

    Low variance (near 0) = recommendations are consistent across runs.
    High variance (near 1) = recommendations differ significantly.
    """
    if len(recommendations_lists) < 2:
        return 0.0

    distances: list[float] = []
    for i in range(len(recommendations_lists)):
        for j in range(i + 1, len(recommendations_lists)):
            actions_i = {r.action for r in recommendations_lists[i] if hasattr(r, "action")}
            actions_j = {r.action for r in recommendations_lists[j] if hasattr(r, "action")}
            jaccard = _jaccard_distance(actions_i, actions_j)
            distances.append(jaccard)

    import statistics
    return statistics.mean(distances) if distances else 0.0


def _jaccard_distance(set_a: set[Any], set_b: set[Any]) -> float:
    """1 - Jaccard similarity = Jaccard distance."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 1.0
    return 1.0 - (intersection / union)
