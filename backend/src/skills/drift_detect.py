"""
Drift detection skill using KL divergence and statistical tests.

This module provides production implementation of drift detection for
monitoring distribution changes in causal graphs.

Drift types supported:
- Distribution drift: KL divergence on feature distributions
- Semantic drift: Entity definition changes
- Causal drift: Graph structure vs data pattern divergence
- Concept drift: Label distribution changes

EpistemicState constraints enforced:
- audit_log_required: All drift detections logged
- no_semantic_layer_writes: Read-only (alert-only agent)
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import structlog

from src.db.neo4j_factory import Neo4jFactory

logger = structlog.get_logger(__name__)

# Try to import scipy for statistical tests
try:
    from scipy import stats
    from scipy.spatial.distance import jensenshannon
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger.warning("SciPy not installed. Using fallback drift detection.")


class DriftDetectionError(Exception):
    """Raised when drift detection fails."""
    pass


class DriftAlertStorageError(Exception):
    """Raised when drift alert storage fails."""
    pass


# Drift alert TTL configuration (90 days default, matching experience buffer)
DRIFT_ALERT_TTL_DAYS = int(os.getenv("DRIFT_ALERT_TTL_DAYS", "90"))


async def store_drift_alert(
    *,
    alert_id: str | None = None,
    alert_type: str,
    severity: str,  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    fragment_id: str | None = None,
    drift_score: float,
    drifted_features: list[str] | None = None,
    drift_type: str,  # "distribution", "semantic", "causal", "concept"
    description: str,
    recommendation: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a drift alert in Neo4j for audit and tracking.
    
    Args:
        alert_id: Optional unique identifier (auto-generated if not provided)
        alert_type: Type of alert (e.g., "SEVERE_DISTRIBUTION_DRIFT")
        severity: Alert severity level
        fragment_id: Associated graph fragment ID
        drift_score: Numerical drift score (e.g., KL divergence)
        drifted_features: List of features that drifted
        drift_type: Category of drift detected
        description: Human-readable description
        recommendation: Suggested action
        metadata: Additional metadata dict
    
    Returns:
        Dict with storage result
    
    EpistemicState constraints:
        - audit_log_required: All alerts stored with full context
        - no_semantic_layer_writes: Alerts are audit-only, not semantic layer
    """
    driver = Neo4jFactory.get_driver()
    if not driver:
        logger.warning("drift_alert.storage_skipped", reason="neo4j_not_available")
        return {"stored": False, "reason": "neo4j_not_available"}

    # Generate alert ID if not provided
    if alert_id is None:
        alert_id = hashlib.sha256(
            f"{alert_type}:{fragment_id}:{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:16]

    now = datetime.now(UTC)
    ttl_at = now + timedelta(days=DRIFT_ALERT_TTL_DAYS)

    try:
        async with driver.session() as session:
            # Create drift alert node
            result = await session.run(
                """
                CREATE (a:DriftAlert {
                    id: $alert_id,
                    alert_type: $alert_type,
                    severity: $severity,
                    fragment_id: $fragment_id,
                    drift_score: $drift_score,
                    drifted_features: $drifted_features,
                    drift_type: $drift_type,
                    description: $description,
                    recommendation: $recommendation,
                    metadata: $metadata,
                    created_at: $created_at,
                    ttl_at: $ttl_at,
                    acknowledged: false
                })
                RETURN a
                """,
                {
                    "alert_id": alert_id,
                    "alert_type": alert_type,
                    "severity": severity,
                    "fragment_id": fragment_id or "",
                    "drift_score": drift_score,
                    "drifted_features": drifted_features or [],
                    "drift_type": drift_type,
                    "description": description,
                    "recommendation": recommendation or "",
                    "metadata": str(metadata or {}),
                    "created_at": now.isoformat(),
                    "ttl_at": ttl_at.isoformat(),
                }
            )
            await result.single()

            # Link to fragment if provided
            if fragment_id:
                await session.run(
                    """
                    MATCH (a:DriftAlert {id: $alert_id})
                    MERGE (f:GraphFragment {id: $fragment_id})
                    CREATE (a)-[:ALERT_FOR]->(f)
                    """,
                    {"alert_id": alert_id, "fragment_id": fragment_id}
                )

        logger.info(
            "drift_alert.stored",
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            drift_score=drift_score,
        )

        return {
            "stored": True,
            "alert_id": alert_id,
            "ttl_at": ttl_at.isoformat(),
        }

    except Exception as e:
        logger.error("drift_alert.storage_failed", error=str(e), alert_id=alert_id)
        raise DriftAlertStorageError(f"Failed to store drift alert: {e}") from e


async def get_recent_drift_alerts(
    *,
    fragment_id: str | None = None,
    drift_type: str | None = None,
    severity: str | None = None,
    limit: int = 100,
    include_acknowledged: bool = False,
) -> dict[str, Any]:
    """Retrieve recent drift alerts from Neo4j.
    
    Args:
        fragment_id: Filter by graph fragment ID
        drift_type: Filter by drift type
        severity: Filter by severity level
        limit: Maximum number of results
        include_acknowledged: Whether to include acknowledged alerts
    
    Returns:
        Dict with alerts list and metadata
    """
    driver = Neo4jFactory.get_driver()
    if not driver:
        return {
            "alerts": [],
            "retrieval_source": "neo4j_not_available",
            "count": 0,
        }

    now = datetime.now(UTC).isoformat()

    try:
        async with driver.session() as session:
            # Build query dynamically
            where_clauses = ["a.ttl_at > $now"]
            params: dict[str, Any] = {"now": now, "limit": limit}

            if fragment_id:
                where_clauses.append("a.fragment_id = $fragment_id")
                params["fragment_id"] = fragment_id

            if drift_type:
                where_clauses.append("a.drift_type = $drift_type")
                params["drift_type"] = drift_type

            if severity:
                where_clauses.append("a.severity = $severity")
                params["severity"] = severity

            if not include_acknowledged:
                where_clauses.append("a.acknowledged = false")

            cypher = f"""
                MATCH (a:DriftAlert)
                WHERE {' AND '.join(where_clauses)}
                RETURN a.id as id,
                       a.alert_type as alert_type,
                       a.severity as severity,
                       a.fragment_id as fragment_id,
                       a.drift_score as drift_score,
                       a.drifted_features as drifted_features,
                       a.drift_type as drift_type,
                       a.description as description,
                       a.recommendation as recommendation,
                       a.created_at as created_at,
                       a.acknowledged as acknowledged
                ORDER BY a.created_at DESC
                LIMIT $limit
            """

            result = await session.run(cypher, params)
            records = await result.data()

            alerts = [
                {
                    "alert_id": r["id"],
                    "alert_type": r["alert_type"],
                    "severity": r["severity"],
                    "fragment_id": r["fragment_id"],
                    "drift_score": r["drift_score"],
                    "drifted_features": r["drifted_features"],
                    "drift_type": r["drift_type"],
                    "description": r["description"],
                    "recommendation": r["recommendation"],
                    "created_at": r["created_at"],
                    "acknowledged": r["acknowledged"],
                }
                for r in records
            ]

            return {
                "alerts": alerts,
                "retrieval_source": "neo4j_drift_alerts",
                "count": len(alerts),
                "ttl_days": DRIFT_ALERT_TTL_DAYS,
            }

    except Exception as e:
        logger.error("drift_alert.retrieval_failed", error=str(e))
        return {
            "alerts": [],
            "retrieval_source": "error",
            "count": 0,
            "error": str(e),
        }


async def acknowledge_drift_alert(alert_id: str, acknowledged_by: str) -> dict[str, Any]:
    """Mark a drift alert as acknowledged.
    
    Args:
        alert_id: The alert ID to acknowledge
        acknowledged_by: User or agent ID acknowledging the alert
    
    Returns:
        Dict with acknowledgement result
    """
    driver = Neo4jFactory.get_driver()
    if not driver:
        return {"acknowledged": False, "reason": "neo4j_not_available"}

    try:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (a:DriftAlert {id: $alert_id})
                SET a.acknowledged = true,
                    a.acknowledged_at = $acknowledged_at,
                    a.acknowledged_by = $acknowledged_by
                RETURN a
                """,
                {
                    "alert_id": alert_id,
                    "acknowledged_at": datetime.now(UTC).isoformat(),
                    "acknowledged_by": acknowledged_by,
                }
            )
            record = await result.single()

            if record:
                logger.info("drift_alert.acknowledged", alert_id=alert_id, by=acknowledged_by)
                return {"acknowledged": True, "alert_id": alert_id}
            else:
                return {"acknowledged": False, "reason": "alert_not_found"}

    except Exception as e:
        logger.error("drift_alert.acknowledge_failed", error=str(e), alert_id=alert_id)
        return {"acknowledged": False, "reason": str(e)}


async def drift_detect(
    reference_data: dict[str, list[float]],
    current_data: dict[str, list[float]],
    feature_names: list[str] | None = None,
    kl_threshold: float = 0.1,
    severe_threshold: float = 0.5,
) -> dict[str, Any]:
    """
    Compute KL divergence between reference and current distributions.

    Args:
        reference_data: Baseline feature distributions {feature: values}
        current_data: Current feature distributions {feature: values}
        feature_names: List of features to check (None = all)
        kl_threshold: Threshold for moderate drift detection
        severe_threshold: Threshold for severe drift detection

    Returns:
        dict with:
            - kl_divergence: Maximum KL divergence across features
            - drifted_features: List of features exceeding threshold
            - drift_type: "none", "distribution", or "severe_distribution"
            - feature_scores: Per-feature divergence scores

    EpistemicState constraints:
        - audit_log_required: Detection results logged
        - no_semantic_layer_writes: Read-only operation
    """
    # Validate inputs
    if not reference_data or not current_data:
        logger.warning("drift_detect: empty data provided")
        return {
            "kl_divergence": 0.0,
            "drifted_features": [],
            "drift_type": "none",
            "feature_scores": {},
        }

    # Determine features to check
    if feature_names is None:
        feature_names = list(reference_data.keys())

    drifted_features = []
    feature_scores = {}
    max_kl = 0.0

    for feature in feature_names:
        ref_values = reference_data.get(feature, [])
        curr_values = current_data.get(feature, [])

        if not ref_values or not curr_values:
            continue

        # Compute KL divergence for this feature
        kl_div = _compute_kl_divergence(ref_values, curr_values)
        feature_scores[feature] = round(kl_div, 4)
        max_kl = max(max_kl, kl_div)

        # Check if drifted
        if kl_div > kl_threshold:
            drifted_features.append(feature)

    # Determine drift type
    if max_kl > severe_threshold:
        drift_type = "severe_distribution"
    elif max_kl > kl_threshold:
        drift_type = "distribution"
    else:
        drift_type = "none"

    logger.info(
        "drift_detect.complete",
        max_kl=max_kl,
        drifted_count=len(drifted_features),
        drift_type=drift_type,
    )

    return {
        "kl_divergence": round(max_kl, 4),
        "drifted_features": drifted_features,
        "drift_type": drift_type,
        "feature_scores": feature_scores,
    }


def _compute_kl_divergence(
    reference: list[float],
    current: list[float],
    n_bins: int = 20,
) -> float:
    """
    Compute KL divergence between two distributions using histograms.

    Args:
        reference: Baseline distribution samples
        current: Current distribution samples
        n_bins: Number of histogram bins

    Returns:
        KL divergence value (symmetrized)
    """
    if not SCIPY_AVAILABLE:
        # Fallback: use simple variance comparison
        ref_var = np.var(reference) if len(reference) > 1 else 1e-10
        curr_var = np.var(current) if len(current) > 1 else 1e-10
        ref_mean = np.mean(reference)
        curr_mean = np.mean(current)

        # Approximate KL for Gaussians
        kl = 0.5 * (
            curr_var / ref_var +
            (ref_mean - curr_mean) ** 2 / ref_var -
            1 +
            np.log(ref_var / curr_var)
        )
        return float(max(0, kl))

    try:
        # Create histograms with shared bins
        min_val = min(min(reference), min(current))
        max_val = max(max(reference), max(current))

        if min_val == max_val:
            return 0.0

        bins = np.linspace(min_val, max_val, n_bins + 1)

        ref_hist, _ = np.histogram(reference, bins=bins, density=True)
        curr_hist, _ = np.histogram(current, bins=bins, density=True)

        # Add small epsilon to avoid log(0)
        epsilon = 1e-10
        ref_hist = ref_hist + epsilon
        curr_hist = curr_hist + epsilon

        # Normalize
        ref_hist = ref_hist / ref_hist.sum()
        curr_hist = curr_hist / curr_hist.sum()

        # Compute symmetrized KL divergence
        kl_ref_curr = np.sum(ref_hist * np.log(ref_hist / curr_hist))
        kl_curr_ref = np.sum(curr_hist * np.log(curr_hist / ref_hist))

        # Return average of both directions (symmetrized)
        return float((kl_ref_curr + kl_curr_ref) / 2)

    except Exception as e:
        logger.error("kl_divergence.computation_failed", error=str(e))
        return 0.0


def _compute_js_divergence(
    reference: list[float],
    current: list[float],
    n_bins: int = 20,
) -> float:
    """
    Compute Jensen-Shannon divergence (symmetric and bounded).

    Args:
        reference: Baseline distribution samples
        current: Current distribution samples
        n_bins: Number of histogram bins

    Returns:
        JS divergence value (0 to 1)
    """
    if not SCIPY_AVAILABLE:
        # Fallback to KL
        return _compute_kl_divergence(reference, current, n_bins)

    try:
        min_val = min(min(reference), min(current))
        max_val = max(max(reference), max(current))

        if min_val == max_val:
            return 0.0

        bins = np.linspace(min_val, max_val, n_bins + 1)

        ref_hist, _ = np.histogram(reference, bins=bins, density=True)
        curr_hist, _ = np.histogram(current, bins=bins, density=True)

        # Use scipy's Jensen-Shannon
        js_div = jensenshannon(ref_hist, curr_hist)

        return float(js_div if js_div is not np.nan else 0.0)

    except Exception as e:
        logger.error("js_divergence.computation_failed", error=str(e))
        return 0.0


def detect_concept_drift(
    reference_labels: list[int],
    current_labels: list[int],
) -> dict[str, Any]:
    """
    Detect concept drift using chi-squared test on label distributions.

    Args:
        reference_labels: Baseline class labels
        current_labels: Current class labels

    Returns:
        dict with chi2 statistic, p-value, and drift flag
    """
    if not SCIPY_AVAILABLE or len(reference_labels) < 2 or len(current_labels) < 2:
        return {
            "chi2": 0.0,
            "p_value": 1.0,
            "concept_drift": False,
        }

    try:
        # Get unique classes
        all_classes = sorted(set(reference_labels) | set(current_labels))

        # Count frequencies
        ref_counts = [reference_labels.count(c) for c in all_classes]
        curr_counts = [current_labels.count(c) for c in all_classes]

        # Chi-squared test
        chi2, p_value = stats.chisquare(curr_counts, ref_counts)

        # Drift if p < 0.05
        concept_drift = bool(p_value < 0.05)

        return {
            "chi2": round(float(chi2), 4),
            "p_value": round(float(p_value), 4),
            "concept_drift": concept_drift,
        }

    except Exception as e:
        logger.error("concept_drift.failed", error=str(e))
        return {
            "chi2": 0.0,
            "p_value": 1.0,
            "concept_drift": False,
        }


def detect_semantic_drift(
    reference_definitions: dict[str, str],
    current_definitions: dict[str, str],
) -> dict[str, Any]:
    """
    Detect semantic drift by comparing entity definitions.

    Uses simple string similarity to detect changes in entity definitions
    that might indicate semantic drift.

    Args:
        reference_definitions: Baseline entity definitions {entity_id: definition}
        current_definitions: Current entity definitions {entity_id: definition}

    Returns:
        dict with drifted_entities list and overall drift score
    """
    try:
        drifted_entities = []
        total_similarity = 0.0
        count = 0

        for entity_id, ref_def in reference_definitions.items():
            curr_def = current_definitions.get(entity_id)
            if not curr_def:
                # Entity removed - significant drift
                drifted_entities.append({
                    "entity_id": entity_id,
                    "change_type": "removed",
                    "similarity": 0.0,
                })
                continue

            # Simple Jaccard similarity on word tokens
            ref_tokens = set(ref_def.lower().split())
            curr_tokens = set(curr_def.lower().split())

            if not ref_tokens and not curr_tokens:
                similarity = 1.0
            elif not ref_tokens or not curr_tokens:
                similarity = 0.0
            else:
                intersection = len(ref_tokens & curr_tokens)
                union = len(ref_tokens | curr_tokens)
                similarity = intersection / union if union > 0 else 0.0

            total_similarity += similarity
            count += 1

            # Threshold for semantic drift
            if similarity < 0.7:
                drifted_entities.append({
                    "entity_id": entity_id,
                    "change_type": "modified",
                    "similarity": round(similarity, 4),
                    "reference_snippet": ref_def[:100],
                    "current_snippet": curr_def[:100],
                })

        # Check for new entities
        for entity_id, curr_def in current_definitions.items():
            if entity_id not in reference_definitions:
                drifted_entities.append({
                    "entity_id": entity_id,
                    "change_type": "added",
                    "similarity": 0.0,
                })

        avg_similarity = total_similarity / count if count > 0 else 1.0
        semantic_drift = len(drifted_entities) > 0 or avg_similarity < 0.8

        return {
            "semantic_drift": semantic_drift,
            "avg_similarity": round(avg_similarity, 4),
            "drifted_entities": drifted_entities,
            "drifted_count": len(drifted_entities),
        }

    except Exception as e:
        logger.error("semantic_drift.failed", error=str(e))
        return {
            "semantic_drift": False,
            "avg_similarity": 1.0,
            "drifted_entities": [],
            "drifted_count": 0,
            "error": str(e),
        }


async def detect_causal_drift(
    reference_graph_edges: list[dict[str, Any]],
    current_data_correlations: dict[str, float],
    correlation_threshold: float = 0.5,
) -> dict[str, Any]:
    """
    Detect causal drift by comparing graph structure against data correlations.

    Identifies edges in the causal graph that may no longer be supported
    by the current data correlations.

    Args:
        reference_graph_edges: List of edges from reference graph
        current_data_correlations: Dict of edge_key -> correlation values
        correlation_threshold: Minimum correlation to support an edge

    Returns:
        dict with drifted_edges list and causal drift flag
    """
    try:
        drifted_edges = []

        for edge in reference_graph_edges:
            source = edge.get("source", "")
            target = edge.get("target", "")
            edge_key = f"{source}->{target}"

            current_corr = current_data_correlations.get(edge_key, 0.0)
            expected_corr = edge.get("expected_correlation", correlation_threshold)

            # Check if correlation has dropped significantly
            if current_corr < correlation_threshold and expected_corr >= correlation_threshold:
                drifted_edges.append({
                    "edge_id": edge.get("id", edge_key),
                    "source": source,
                    "target": target,
                    "edge_type": edge.get("edge_type", "CAUSES"),
                    "expected_correlation": expected_corr,
                    "current_correlation": current_corr,
                    "confidence_drop": round(expected_corr - current_corr, 4),
                })

        return {
            "causal_drift": len(drifted_edges) > 0,
            "drifted_edges": drifted_edges,
            "drifted_count": len(drifted_edges),
            "total_edges_checked": len(reference_graph_edges),
        }

    except Exception as e:
        logger.error("causal_drift.failed", error=str(e))
        return {
            "causal_drift": False,
            "drifted_edges": [],
            "drifted_count": 0,
            "total_edges_checked": 0,
            "error": str(e),
        }


def calculate_drift_severity(
    kl_divergence: float,
    drifted_features: list[str],
    structural_drift: bool = False,
    semantic_drift: bool = False,
    concept_drift: bool = False,
) -> dict[str, Any]:
    """
    Calculate overall drift severity score based on multiple drift indicators.

    Args:
        kl_divergence: Maximum KL divergence detected
        drifted_features: List of features that drifted
        structural_drift: Whether structural drift was detected
        semantic_drift: Whether semantic drift was detected
        concept_drift: Whether concept drift was detected

    Returns:
        dict with severity level, score, and recommendations
    """
    # Base score from KL divergence — thresholds: CRITICAL ≥75, HIGH ≥50, MEDIUM ≥25, LOW <25
    if kl_divergence > 0.5:
        base_score = 75
        severity = "CRITICAL"
    elif kl_divergence > 0.3:
        base_score = 50
        severity = "HIGH"
    elif kl_divergence > 0.1:
        base_score = 25
        severity = "MEDIUM"
    else:
        base_score = 0
        severity = "LOW"

    # Adjust score based on additional drift types
    if structural_drift:
        base_score = min(100, base_score + 25)
    if semantic_drift:
        base_score = min(100, base_score + 15)
    if concept_drift:
        base_score = min(100, base_score + 10)

    # Adjust based on number of drifted features
    feature_count = len(drifted_features)
    if feature_count > 5:
        base_score = min(100, base_score + 10)
    elif feature_count > 2:
        base_score = min(100, base_score + 5)

    # Recalculate severity based on final score
    if base_score >= 75:
        severity = "CRITICAL"
    elif base_score >= 50:
        severity = "HIGH"
    elif base_score >= 25:
        severity = "MEDIUM"
    else:
        severity = "LOW"

    # Generate recommendation
    if severity == "CRITICAL":
        recommendation = "Immediate governance review required. Consider halting automated decisions."
    elif severity == "HIGH":
        recommendation = "Schedule urgent re-validation of affected graph fragments."
    elif severity == "MEDIUM":
        recommendation = "Monitor closely and plan re-validation within next sprint."
    else:
        recommendation = "No immediate action required. Continue monitoring."

    return {
        "severity": severity,
        "score": base_score,
        "drift_indicators": {
            "distribution_drift": kl_divergence > 0.1,
            "structural_drift": structural_drift,
            "semantic_drift": semantic_drift,
            "concept_drift": concept_drift,
        },
        "recommendation": recommendation,
    }
