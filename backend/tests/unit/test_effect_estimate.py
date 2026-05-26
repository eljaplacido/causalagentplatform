"""
Unit tests for effect estimation skill helpers (src/skills/effect_estimate.py).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.skills.effect_estimate import (
    EffectEstimationError,
    _build_causal_graph,
    _compute_confidence_interval,
    _empty_result,
    _extract_outcome_id,
    _extract_treatment_id,
    _find_paths,
    _get_outcome_name,
    _get_treatment_name,
    _graph_heuristic_result,
    _has_confounding_edges,
    _path_strength,
    effect_estimate,
)


# ── effect_estimate ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_effect_estimate_unapproved_fragment() -> None:
    """Unapproved fragments raise EffectEstimationError."""
    with pytest.raises(EffectEstimationError, match="APPROVED"):
        await effect_estimate(
            fragment={"approval_status": "DRAFT"},
            identification_strategy="backdoor",
            assumptions=[],
        )


@pytest.mark.asyncio
async def test_effect_estimate_empty_edges() -> None:
    """Empty edges returns empty result."""
    result = await effect_estimate(
        fragment={"approval_status": "APPROVED", "edges": []},
        identification_strategy="backdoor",
        assumptions=[],
    )
    assert result["ate"] is None
    assert result["estimator"] == "none"


@pytest.mark.asyncio
async def test_effect_estimate_no_data() -> None:
    """No data returns graph heuristic result."""
    fragment = {
        "approval_status": "APPROVED",
        "edges": [
            {
                "source": {"id": "t1", "object_type": "Treatment"},
                "target": {"id": "o1", "object_type": "Outcome"},
                "confidence": 0.8,
            }
        ],
    }
    result = await effect_estimate(
        fragment=fragment,
        identification_strategy="backdoor",
        assumptions=[],
    )
    assert result["ate"] is not None
    assert result["estimator"] == "graph_heuristic_no_observations"
    assert result["estimate_basis"] == "graph_structure"


# ── _empty_result ─────────────────────────────────────────────────────────────


def test_empty_result() -> None:
    """Empty result has None values."""
    result = _empty_result()
    assert result["ate"] is None
    assert result["confidence_interval"] == [None, None]
    assert result["estimator"] == "none"


# ── _extract_treatment_id / _extract_outcome_id ───────────────────────────────


def test_extract_treatment_id_found() -> None:
    """Extract treatment ID from fragment edges."""
    fragment = {
        "edges": [
            {"source": {"id": "t1", "object_type": "Treatment"}, "target": {"id": "o1"}}
        ]
    }
    assert _extract_treatment_id(fragment) == "t1"


def test_extract_treatment_id_not_found() -> None:
    """Returns None when no Treatment found."""
    assert _extract_treatment_id({"edges": []}) is None


def test_extract_outcome_id_found() -> None:
    """Extract outcome ID from fragment edges."""
    fragment = {
        "edges": [
            {"source": {"id": "t1"}, "target": {"id": "o1", "object_type": "Outcome"}}
        ]
    }
    assert _extract_outcome_id(fragment) == "o1"


def test_extract_outcome_id_not_found() -> None:
    """Returns None when no Outcome found."""
    assert _extract_outcome_id({"edges": []}) is None


# ── _get_treatment_name / _get_outcome_name ───────────────────────────────────


def test_get_treatment_name() -> None:
    """Returns truncated treatment name."""
    fragment = {
        "edges": [
            {"source": {"id": "treatment_12345", "object_type": "Treatment"}, "target": {"id": "o1"}}
        ]
    }
    assert _get_treatment_name(fragment) == "treatmen"


def test_get_outcome_name() -> None:
    """Returns truncated outcome name."""
    fragment = {
        "edges": [
            {"source": {"id": "t1"}, "target": {"id": "outcome_12345", "object_type": "Outcome"}}
        ]
    }
    assert _get_outcome_name(fragment) == "outcome_"


# ── _build_causal_graph ───────────────────────────────────────────────────────


def test_build_causal_graph_empty() -> None:
    """Empty edges returns minimal graph."""
    result = _build_causal_graph({"edges": []})
    assert "treatment -> outcome" in result


def test_build_causal_graph_with_edges() -> None:
    """Builds DOT graph from fragment edges."""
    fragment = {
        "edges": [
            {"source": {"id": "a"}, "target": {"id": "b"}},
            {"source": {"id": "b"}, "target": {"id": "c"}},
        ]
    }
    result = _build_causal_graph(fragment)
    assert '"a" -> "b";' in result
    assert '"b" -> "c";' in result


# ── _compute_confidence_interval ──────────────────────────────────────────────


def test_compute_ci_insufficient_data() -> None:
    """Small data returns default wide interval."""
    import pandas as pd

    result = _compute_confidence_interval(None, None, pd.DataFrame({"a": [1, 2, 3]}))
    assert result == (-0.5, 0.5)


# ── _find_paths ───────────────────────────────────────────────────────────────


def test_find_paths_direct() -> None:
    """Finds direct path from treatment to outcome."""
    fragment = {
        "edges": [
            {"source": {"id": "t1"}, "target": {"id": "o1"}, "confidence": 0.8}
        ]
    }
    paths = _find_paths(fragment, "t1", "o1")
    assert len(paths) == 1
    assert paths[0][0]["source"]["id"] == "t1"


def test_find_paths_indirect() -> None:
    """Finds indirect path through mediator."""
    fragment = {
        "edges": [
            {"source": {"id": "t1"}, "target": {"id": "m1"}, "confidence": 0.8},
            {"source": {"id": "m1"}, "target": {"id": "o1"}, "confidence": 0.7},
        ]
    }
    paths = _find_paths(fragment, "t1", "o1")
    assert len(paths) == 1
    assert len(paths[0]) == 2


def test_find_paths_no_path() -> None:
    """Returns empty list when no path exists."""
    fragment = {"edges": []}
    paths = _find_paths(fragment, "t1", "o1")
    assert paths == []


def test_find_paths_max_depth() -> None:
    """Respects max_depth bound."""
    fragment = {
        "edges": [
            {"source": {"id": "t1"}, "target": {"id": "a"}, "confidence": 0.8},
            {"source": {"id": "a"}, "target": {"id": "b"}, "confidence": 0.8},
            {"source": {"id": "b"}, "target": {"id": "c"}, "confidence": 0.8},
            {"source": {"id": "c"}, "target": {"id": "o1"}, "confidence": 0.8},
        ]
    }
    paths = _find_paths(fragment, "t1", "o1", max_depth=2)
    assert len(paths) == 0  # path length 4 > max_depth 2


# ── _path_strength ────────────────────────────────────────────────────────────


def test_path_strength_empty() -> None:
    """Empty path has zero strength."""
    assert _path_strength([]) == 0.0


def test_path_strength_single_edge() -> None:
    """Single edge path uses edge confidence."""
    path = [{"confidence": 0.8}]
    assert _path_strength(path) == 0.8


def test_path_strength_multi_edge() -> None:
    """Multi-edge path multiplies confidences."""
    path = [{"confidence": 0.5}, {"confidence": 0.5}]
    assert _path_strength(path) == 0.25


# ── _has_confounding_edges ────────────────────────────────────────────────────


def test_has_confounding_edges_true() -> None:
    """Detects CONFOUNDS edge type."""
    fragment = {"edges": [{"edge_type": "CONFOUNDS"}]}
    assert _has_confounding_edges(fragment) is True


def test_has_confounding_edges_by_object_type() -> None:
    """Detects Confounder object type."""
    fragment = {"edges": [{"source": {"object_type": "Confounder"}}]}
    assert _has_confounding_edges(fragment) is True


def test_has_confounding_edges_false() -> None:
    """Returns False when no confounding."""
    fragment = {"edges": [{"edge_type": "CAUSES"}]}
    assert _has_confounding_edges(fragment) is False


# ── _graph_heuristic_result ───────────────────────────────────────────────────


def test_graph_heuristic_result_basic() -> None:
    """Graph heuristic produces bounded ATE."""
    fragment = {
        "edges": [
            {
                "source": {"id": "t1", "object_type": "Treatment"},
                "target": {"id": "o1", "object_type": "Outcome"},
                "confidence": 0.8,
            }
        ]
    }
    result = _graph_heuristic_result(
        fragment,
        assumptions=[],
        treatment_id="t1",
        outcome_id="o1",
        identification_strategy="backdoor",
        estimator="test",
    )
    assert result["ate"] is not None
    assert -1.0 <= result["ate"] <= 1.0
    assert result["estimator"] == "test"
    assert result["path_count"] == 1


def test_graph_heuristic_result_with_assumptions() -> None:
    """Assumptions adjust the heuristic score."""
    fragment = {
        "edges": [
            {
                "source": {"id": "t1", "object_type": "Treatment"},
                "target": {"id": "o1", "object_type": "Outcome"},
                "confidence": 0.8,
            }
        ]
    }
    result_with = _graph_heuristic_result(
        fragment,
        assumptions=[{"assumption_type": "IGNORABILITY"}],
        treatment_id="t1",
        outcome_id="o1",
        identification_strategy="backdoor",
        estimator="test",
    )
    result_without = _graph_heuristic_result(
        fragment,
        assumptions=[],
        treatment_id="t1",
        outcome_id="o1",
        identification_strategy="backdoor",
        estimator="test",
    )
    assert result_with["ate"] > result_without["ate"]
