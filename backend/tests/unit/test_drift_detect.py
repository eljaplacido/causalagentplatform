"""
Unit tests for drift detection skills.

Tests for:
  1. KL divergence computation
  2. Drift alert storage in Neo4j
  3. Semantic drift detection
  4. Causal drift detection
  5. Concept drift detection
  6. Drift severity calculation
  7. Alert retrieval and acknowledgment
"""

from __future__ import annotations

import pytest

from src.skills.drift_detect import (
    _compute_js_divergence,
    _compute_kl_divergence,
    calculate_drift_severity,
    detect_causal_drift,
    detect_concept_drift,
    detect_semantic_drift,
    drift_detect,
)


pytestmark = pytest.mark.asyncio


# ── 1. KL divergence computation ─────────────────────────────────────────────


def test_kl_divergence_identical_distributions() -> None:
    """KL divergence between identical distributions should be ~0."""
    data = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
    
    kl = _compute_kl_divergence(data, data)
    
    assert kl < 0.01  # Should be very close to 0


def test_kl_divergence_different_distributions() -> None:
    """KL divergence between different distributions should be > 0."""
    reference = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
    current = [10.0, 11.0, 12.0, 13.0, 14.0] * 10
    
    kl = _compute_kl_divergence(reference, current)
    
    assert kl > 0.1  # Should be significantly > 0


def test_kl_divergence_empty_data() -> None:
    """KL divergence with empty data should handle gracefully."""
    reference = [1.0, 2.0, 3.0]
    current: list[float] = []
    
    # Should not raise, returns 0 or handles gracefully
    result = _compute_kl_divergence(reference, current)
    assert isinstance(result, float)


# ── 2. JS divergence computation ─────────────────────────────────────────────


def test_js_divergence_bounded() -> None:
    """Jensen-Shannon divergence should be bounded [0, 1]."""
    reference = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
    current = [10.0, 11.0, 12.0, 13.0, 14.0] * 10
    
    js = _compute_js_divergence(reference, current)
    
    assert 0.0 <= js <= 1.0


# ── 3. Drift detection orchestration ─────────────────────────────────────────


async def test_drift_detect_no_data() -> None:
    """Drift detection with no data should return safe defaults."""
    result = await drift_detect({}, {})
    
    assert result["kl_divergence"] == 0.0
    assert result["drifted_features"] == []
    assert result["drift_type"] == "none"


async def test_drift_detect_with_drift() -> None:
    """Drift detection should identify drifted features."""
    reference_data = {
        "feature_a": [1.0, 2.0, 3.0, 4.0, 5.0] * 20,
        "feature_b": [1.0, 2.0, 3.0, 4.0, 5.0] * 20,
    }
    current_data = {
        "feature_a": [1.0, 2.0, 3.0, 4.0, 5.0] * 20,  # Same
        "feature_b": [10.0, 11.0, 12.0, 13.0, 14.0] * 20,  # Different
    }
    
    result = await drift_detect(
        reference_data=reference_data,
        current_data=current_data,
        kl_threshold=0.1,
    )
    
    assert "feature_b" in result["drifted_features"]
    assert result["drift_type"] in ["distribution", "severe_distribution"]


# ── 4. Semantic drift detection ──────────────────────────────────────────────


def test_semantic_drift_no_changes() -> None:
    """Semantic drift with identical definitions should return no drift."""
    definitions = {
        "entity_1": "This is a service entity representing user authentication",
        "entity_2": "This is a metric entity for response time",
    }
    
    result = detect_semantic_drift(definitions, definitions)
    
    assert result["semantic_drift"] is False
    assert result["avg_similarity"] == 1.0
    assert result["drifted_count"] == 0


def test_semantic_drift_modified_entity() -> None:
    """Semantic drift should detect modified entity definitions."""
    reference = {
        "entity_1": "This is a service entity representing user authentication",
    }
    current = {
        "entity_1": "This is a database entity representing user profiles",
    }
    
    result = detect_semantic_drift(reference, current)
    
    assert result["semantic_drift"] is True
    assert result["drifted_count"] == 1
    assert result["drifted_entities"][0]["change_type"] == "modified"


def test_semantic_drift_removed_entity() -> None:
    """Semantic drift should detect removed entities."""
    reference = {
        "entity_1": "Service entity",
        "entity_2": "Metric entity",
    }
    current = {
        "entity_1": "Service entity",
    }
    
    result = detect_semantic_drift(reference, current)
    
    assert result["semantic_drift"] is True
    assert any(e["change_type"] == "removed" for e in result["drifted_entities"])


def test_semantic_drift_added_entity() -> None:
    """Semantic drift should detect added entities."""
    reference = {
        "entity_1": "Service entity",
    }
    current = {
        "entity_1": "Service entity",
        "entity_2": "New metric entity",
    }
    
    result = detect_semantic_drift(reference, current)
    
    assert result["semantic_drift"] is True
    assert any(e["change_type"] == "added" for e in result["drifted_entities"])


# ── 5. Causal drift detection ────────────────────────────────────────────────


async def test_causal_drift_no_edges() -> None:
    """Causal drift with no edges should return no drift."""
    result = await detect_causal_drift(
        reference_graph_edges=[],
        current_data_correlations={},
    )
    
    assert result["causal_drift"] is False
    assert result["drifted_count"] == 0


async def test_causal_drift_dropped_correlation() -> None:
    """Causal drift should detect edges with dropped correlations."""
    edges = [
        {
            "id": "edge-1",
            "source": "service_a",
            "target": "service_b",
            "edge_type": "CAUSES",
            "expected_correlation": 0.8,
        }
    ]
    correlations = {
        "service_a->service_b": 0.2,  # Dropped significantly
    }
    
    result = await detect_causal_drift(
        reference_graph_edges=edges,
        current_data_correlations=correlations,
        correlation_threshold=0.5,
    )
    
    assert result["causal_drift"] is True
    assert result["drifted_count"] == 1


async def test_causal_drift_stable_correlation() -> None:
    """Causal drift should not flag stable correlations."""
    edges = [
        {
            "id": "edge-1",
            "source": "service_a",
            "target": "service_b",
            "edge_type": "CAUSES",
            "expected_correlation": 0.8,
        }
    ]
    correlations = {
        "service_a->service_b": 0.75,  # Still strong
    }
    
    result = await detect_causal_drift(
        reference_graph_edges=edges,
        current_data_correlations=correlations,
        correlation_threshold=0.5,
    )
    
    assert result["causal_drift"] is False
    assert result["drifted_count"] == 0


# ── 6. Concept drift detection ───────────────────────────────────────────────


def test_concept_drift_no_labels() -> None:
    """Concept drift with no labels should return safe defaults."""
    result = detect_concept_drift([], [])
    
    # Should handle gracefully
    assert isinstance(result, dict)


def test_concept_drift_same_distribution() -> None:
    """Concept drift with same label distribution should return no drift."""
    labels = [0, 1, 0, 1, 0, 1, 0, 1] * 10
    
    result = detect_concept_drift(labels, labels)
    
    # Same distribution should have high p-value (no drift)
    assert result["p_value"] > 0.05
    assert result["concept_drift"] is False


def test_concept_drift_different_distribution() -> None:
    """Concept drift with different label distribution should detect drift."""
    reference = [0] * 80 + [1] * 20  # 80% class 0
    current = [0] * 20 + [1] * 80    # 80% class 1
    
    result = detect_concept_drift(reference, current)
    
    # Different distribution should indicate drift
    assert result["concept_drift"] is True
    assert result["p_value"] < 0.05


# ── 7. Drift severity calculation ────────────────────────────────────────────


def test_severity_low() -> None:
    """Low KL divergence should produce LOW severity."""
    result = calculate_drift_severity(
        kl_divergence=0.05,
        drifted_features=[],
    )
    
    assert result["severity"] == "LOW"
    assert result["score"] < 25


def test_severity_medium() -> None:
    """Moderate KL divergence should produce MEDIUM severity."""
    result = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=["feature_1"],
    )
    
    assert result["severity"] == "MEDIUM"
    assert 25 <= result["score"] < 50


def test_severity_high() -> None:
    """High KL divergence should produce HIGH severity."""
    result = calculate_drift_severity(
        kl_divergence=0.4,
        drifted_features=["feature_1", "feature_2"],
    )
    
    assert result["severity"] == "HIGH"
    assert 50 <= result["score"] < 75


def test_severity_critical() -> None:
    """Very high KL divergence should produce CRITICAL severity."""
    result = calculate_drift_severity(
        kl_divergence=0.8,
        drifted_features=["f1", "f2", "f3", "f4", "f5", "f6"],
    )
    
    assert result["severity"] == "CRITICAL"
    assert result["score"] >= 75


def test_severity_boosted_by_structural() -> None:
    """Severity should increase with structural drift."""
    base = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=["feature_1"],
        structural_drift=False,
    )
    boosted = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=["feature_1"],
        structural_drift=True,
    )
    
    assert boosted["score"] > base["score"]


def test_severity_boosted_by_semantic() -> None:
    """Severity should increase with semantic drift."""
    base = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=["feature_1"],
        semantic_drift=False,
    )
    boosted = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=["feature_1"],
        semantic_drift=True,
    )
    
    assert boosted["score"] > base["score"]


def test_severity_boosted_by_concept() -> None:
    """Severity should increase with concept drift."""
    base = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=["feature_1"],
        concept_drift=False,
    )
    boosted = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=["feature_1"],
        concept_drift=True,
    )
    
    assert boosted["score"] > base["score"]


def test_severity_many_features() -> None:
    """Severity should increase with many drifted features."""
    base = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=["feature_1"],
    )
    many = calculate_drift_severity(
        kl_divergence=0.2,
        drifted_features=[f"feature_{i}" for i in range(10)],
    )
    
    assert many["score"] >= base["score"]
