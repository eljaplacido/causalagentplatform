"""
Behavioral probes for agent decision quality measurement.

Probes test specific agent properties that matter in production:
  1. Confidence calibration — are confidence scores actually predictive?
  2. Cynefin stability — does the same input produce the same domain classification?
  3. Edge proposal reproducibility — do repeated causal discovery runs converge?

Each probe returns a ProbeResult with a score and diagnostic information.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from src.semantic_layer.models import CynefinDomain, Recommendation

log = structlog.get_logger(__name__)


@dataclass
class ProbeResult:
    """Result of a single behavioral probe."""

    probe_name: str
    score: float                 # 0.0 (worst) to 1.0 (best)
    threshold: float             # Minimum acceptable score
    passed: bool
    detail: str
    probe_duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class CalibrationProbe:
    """
    Tests whether agent confidence scores are calibrated.

    A calibrated agent means: when it says "confidence 0.8", the recommendation
    is correct ~80% of the time. Miscalibrated agents produce confident but
    incorrect outputs — the most dangerous failure mode.
    """

    def __init__(self, threshold: float = 0.6) -> None:
        self._threshold = threshold

    def evaluate(
        self,
        recommendations: list[Recommendation],
    ) -> ProbeResult:
        """
        Evaluate calibration from a set of recommendations.

        For simulation (no ground truth), we use:
        - Variance of confidence scores as a proxy for calibration
        - High variance = poorly calibrated (confidence all over the place)
        - Mean confidence vs. spread relationship
        """
        t0 = time.perf_counter()
        if not recommendations:
            return ProbeResult(
                probe_name="calibration",
                score=0.0,
                threshold=self._threshold,
                passed=False,
                detail="No recommendations to evaluate calibration",
            )

        confidences = [r.confidence for r in recommendations]
        import statistics
        mean_conf = statistics.mean(confidences)
        stdev_conf = statistics.pstdev(confidences) if len(confidences) > 1 else 0.0

        # Calibration score: higher is better
        # Penalty for high variance (agent can't decide)
        # Penalty for mean confidence far from 0.5 (always overconfident or underconfident)
        variance_penalty = min(1.0, stdev_conf * 2.0)
        bias_penalty = abs(mean_conf - 0.5) * 1.5

        score = max(0.0, 1.0 - variance_penalty - bias_penalty)
        passed = score >= self._threshold

        elapsed = round((time.perf_counter() - t0) * 1000, 2)

        return ProbeResult(
            probe_name="calibration",
            score=round(score, 4),
            threshold=self._threshold,
            passed=passed,
            detail=f"Mean confidence: {mean_conf:.2f}, StdDev: {stdev_conf:.2f}",
            probe_duration_ms=elapsed,
            metadata={
                "mean_confidence": round(mean_conf, 4),
                "stdev_confidence": round(stdev_conf, 4),
                "sample_count": len(recommendations),
            },
        )


class CynefinStabilityProbe:
    """
    Tests whether the same or similar inputs produce stable Cynefin classifications.

    Unstable classification (same input → different domains on different runs)
    indicates the agent's domain routing is unreliable, leading to wrong
    reasoning strategies being applied.
    """

    def __init__(self, threshold: float = 0.8) -> None:
        self._threshold = threshold

    def evaluate(
        self,
        domain_history: list[CynefinDomain | str],
    ) -> ProbeResult:
        """
        Evaluate Cynefin stability from a history of domain classifications.

        Args:
            domain_history: List of Cynefin domain classifications across runs
        """
        t0 = time.perf_counter()
        if not domain_history:
            return ProbeResult(
                probe_name="cynefin_stability",
                score=0.0,
                threshold=self._threshold,
                passed=False,
                detail="No domain history to evaluate",
            )

        str_domains = [str(d) for d in domain_history]
        from collections import Counter
        counts = Counter(str_domains)
        most_common_count = counts.most_common(1)[0][1]

        # Stability = proportion of runs that agree with the majority classification
        stability = most_common_count / len(str_domains)
        passed = stability >= self._threshold

        elapsed = round((time.perf_counter() - t0) * 1000, 2)

        return ProbeResult(
            probe_name="cynefin_stability",
            score=round(stability, 4),
            threshold=self._threshold,
            passed=passed,
            detail=(
                f"Majority domain {counts.most_common(1)[0][0]} "
                f"in {most_common_count}/{len(str_domains)} runs"
            ),
            probe_duration_ms=elapsed,
            metadata=dict(counts),
        )


class EdgeReproducibilityProbe:
    """
    Tests whether repeated causal discovery runs converge on the same edges.

    If CausalDiscoveryAgent proposes completely different edges each run,
    the graph is unreliable. This probe measures Jaccard similarity across
    proposed edge sets.
    """

    def __init__(self, threshold: float = 0.5) -> None:
        self._threshold = threshold

    def evaluate(
        self,
        edge_proposals: list[list[dict[str, Any]]],
    ) -> ProbeResult:
        """
        Evaluate edge proposal reproducibility.
        """
        t0 = time.perf_counter()
        if len(edge_proposals) < 2:
            return ProbeResult(
                probe_name="edge_reproducibility",
                score=0.0,
                threshold=self._threshold,
                passed=False,
                detail="Need at least 2 runs to evaluate reproducibility",
            )

        from src.evaluation.consistency import _jaccard_distance

        distances: list[float] = []
        for i in range(len(edge_proposals)):
            for j in range(i + 1, len(edge_proposals)):
                edges_i = {e.get("id", str(e)) for e in edge_proposals[i]}
                edges_j = {e.get("id", str(e)) for e in edge_proposals[j]}
                distances.append(_jaccard_distance(edges_i, edges_j))

        import statistics
        mean_jaccard_dist = statistics.mean(distances) if distances else 1.0
        reproducibility = 1.0 - mean_jaccard_dist  # 1.0 = identical edges every run
        passed = reproducibility >= self._threshold

        elapsed = round((time.perf_counter() - t0) * 1000, 2)

        return ProbeResult(
            probe_name="edge_reproducibility",
            score=round(reproducibility, 4),
            threshold=self._threshold,
            passed=passed,
            detail=f"Mean Jaccard similarity: {reproducibility:.2f} across {len(edge_proposals)} runs",
            probe_duration_ms=elapsed,
            metadata={
                "runs": len(edge_proposals),
                "mean_jaccard_distance": round(mean_jaccard_dist, 4),
            },
        )
