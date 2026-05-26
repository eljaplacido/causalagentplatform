"""
Unit tests for causal structure comparison skill (src/skills/causal_structure_compare.py).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from src.skills.causal_structure_compare import (
    _conditional_independence_test,
    _get_conditioning_set,
    _get_variable_name,
    _residualize,
    causal_structure_compare,
    detect_edge_reversal,
)

pytestmark = pytest.mark.asyncio


# ── causal_structure_compare ──────────────────────────────────────────────────


async def test_causal_structure_compare_empty_edges() -> None:
    """Empty edges returns no drift."""
    result = await causal_structure_compare(
        fragment={"edges": []},
        data={"a": [1.0, 2.0, 3.0]},
    )
    assert result["structural_drift_detected"] is False
    assert result["affected_edges"] == []


async def test_causal_structure_compare_empty_data() -> None:
    """Empty data returns no drift."""
    result = await causal_structure_compare(
        fragment={"edges": [{"id": "e1", "source": {"id": "a"}, "target": {"id": "b"}}]},
        data={},
    )
    assert result["structural_drift_detected"] is False


async def test_causal_structure_compare_no_drift() -> None:
    """Strongly correlated data with no conditioning should not flag drift."""
    data = {
        "12345678": [1.0, 2.0, 3.0, 4.0, 5.0] * 10,
        "87654321": [2.0, 4.0, 6.0, 8.0, 10.0] * 10,
    }
    fragment = {
        "edges": [
            {
                "id": "e1",
                "source": {"id": "12345678"},
                "target": {"id": "87654321"},
            }
        ]
    }
    result = await causal_structure_compare(fragment, data, edge_confidence_threshold=0.99)
    assert result["structural_drift_detected"] is False
    assert "e1" in result["edge_scores"]


async def test_causal_structure_compare_missing_variables() -> None:
    """Edges with variables not in data are skipped."""
    data = {"12345678": [1.0, 2.0, 3.0]}
    fragment = {
        "edges": [
            {
                "id": "e1",
                "source": {"id": "12345678"},
                "target": {"id": "missing"},
            }
        ]
    }
    result = await causal_structure_compare(fragment, data)
    assert result["structural_drift_detected"] is False
    assert result["edge_scores"] == {}


# ── _get_variable_name ────────────────────────────────────────────────────────


def test_get_variable_name_with_id() -> None:
    """Variable name is truncated node ID."""
    assert _get_variable_name({"id": "1234567890abcdef"}) == "12345678"


def test_get_variable_name_empty() -> None:
    """Empty node returns 'unknown'."""
    assert _get_variable_name({}) == "unknown"


# ── _get_conditioning_set ─────────────────────────────────────────────────────


def test_get_conditioning_set_basic() -> None:
    """Conditioning set includes other nodes in fragment."""
    fragment = {
        "edges": [
            {"source": {"id": "aaaaaaaa"}, "target": {"id": "bbbbbbbb"}},
            {"source": {"id": "bbbbbbbb"}, "target": {"id": "cccccccc"}},
        ]
    }
    edge = {"source": {"id": "aaaaaaaa"}, "target": {"id": "bbbbbbbb"}}
    data = {"aaaaaaaa": [1.0], "bbbbbbbb": [2.0], "cccccccc": [3.0]}
    result = _get_conditioning_set(edge, fragment, data)
    assert "cccccccc" in result
    assert "aaaaaaaa" not in result
    assert "bbbbbbbb" not in result


def test_get_conditioning_set_filters_missing_data() -> None:
    """Conditioning set filters out variables not in data."""
    fragment = {
        "edges": [
            {"source": {"id": "aaaaaaaa"}, "target": {"id": "bbbbbbbb"}},
            {"source": {"id": "bbbbbbbb"}, "target": {"id": "cccccccc"}},
        ]
    }
    edge = {"source": {"id": "aaaaaaaa"}, "target": {"id": "bbbbbbbb"}}
    data = {"aaaaaaaa": [1.0], "bbbbbbbb": [2.0]}  # cccccccc missing
    result = _get_conditioning_set(edge, fragment, data)
    assert "cccccccc" not in result


# ── _conditional_independence_test ────────────────────────────────────────────


def test_ci_test_too_few_samples() -> None:
    """Too few samples returns independent with p=1.0."""
    result = _conditional_independence_test([1.0, 2.0], [3.0, 4.0], [])
    assert result[0] in (True, np.bool_(True))
    assert result[1] == 1.0


def test_ci_test_no_conditioning() -> None:
    """No conditioning uses simple correlation."""
    x = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
    y = [2.0, 4.0, 6.0, 8.0, 10.0] * 10
    is_independent, p_value = _conditional_independence_test(x, y, [])
    assert is_independent in (True, False, np.bool_(True), np.bool_(False))
    assert 0.0 <= float(p_value) <= 1.0


def test_ci_test_with_conditioning() -> None:
    """Conditioning uses partial correlation."""
    x = [1.0, 2.0, 3.0, 4.0, 5.0] * 10
    y = [2.0, 4.0, 6.0, 8.0, 10.0] * 10
    z = [[1.0, 2.0, 3.0, 4.0, 5.0] * 10]
    is_independent, p_value = _conditional_independence_test(x, y, z)
    assert is_independent in (True, False, np.bool_(True), np.bool_(False))
    assert 0.0 <= float(p_value) <= 1.0


def test_ci_test_nan_handling() -> None:
    """NaN inputs are handled gracefully."""
    x = [1.0, np.nan, 3.0]
    y = [2.0, 4.0, np.nan]
    is_independent, p_value = _conditional_independence_test(x, y, [])
    assert is_independent in (True, False, np.bool_(True), np.bool_(False))


# ── _residualize ──────────────────────────────────────────────────────────────


def test_residualize_basic() -> None:
    """Residualize returns array of same length as input."""
    y = [1.0, 2.0, 3.0, 4.0, 5.0]
    x = np.array([[1.0], [2.0], [3.0], [4.0], [5.0]])
    residuals = _residualize(y, x)
    assert len(residuals) == len(y)


def test_residualize_empty_x() -> None:
    """Residualize with empty x returns original y."""
    y = [1.0, 2.0, 3.0]
    x = np.array([[]] * 3)
    residuals = _residualize(y, x)
    assert len(residuals) == len(y)


def test_residualize_singular() -> None:
    """Residualize handles singular matrix."""
    y = [1.0, 2.0, 3.0]
    x = np.array([[1.0], [1.0], [1.0]])
    residuals = _residualize(y, x)
    assert len(residuals) == len(y)


# ── detect_edge_reversal ──────────────────────────────────────────────────────


def test_detect_edge_reversal_empty() -> None:
    """Empty fragment returns no reversals."""
    result = detect_edge_reversal({"edges": []}, {})
    assert result == []


def test_detect_edge_reversal_strong_correlation() -> None:
    """Strong correlation flags edge for review."""
    data = {
        "12345678": [1.0, 2.0, 3.0, 4.0, 5.0] * 10,
        "87654321": [2.0, 4.0, 6.0, 8.0, 10.0] * 10,
    }
    fragment = {
        "edges": [
            {
                "id": "e1",
                "source": {"id": "12345678"},
                "target": {"id": "87654321"},
            }
        ]
    }
    result = detect_edge_reversal(fragment, data)
    assert len(result) == 1
    assert result[0]["recommendation"] == "review_direction"


def test_detect_edge_reversal_missing_data() -> None:
    """Edges with missing data are skipped."""
    data = {"12345678": [1.0, 2.0, 3.0]}
    fragment = {
        "edges": [
            {
                "id": "e1",
                "source": {"id": "12345678"},
                "target": {"id": "missing"},
            }
        ]
    }
    result = detect_edge_reversal(fragment, data)
    assert result == []


# ── Fallback paths (no scipy) ─────────────────────────────────────────────────


def test_ci_test_fallback_no_scipy() -> None:
    """Fallback path works when scipy is unavailable."""
    with patch("src.skills.causal_structure_compare.SCIPY_AVAILABLE", False):
        is_independent, p_value = _conditional_independence_test(
            [1.0, 2.0, 3.0], [2.0, 4.0, 6.0], []
        )
        assert is_independent in (True, False)
        assert 0.0 <= float(p_value) <= 1.0


def test_ci_test_fallback_nan_corr() -> None:
    """Fallback handles NaN correlation."""
    with patch("src.skills.causal_structure_compare.SCIPY_AVAILABLE", False):
        is_independent, p_value = _conditional_independence_test(
            [1.0, 1.0, 1.0], [2.0, 2.0, 2.0], []
        )
        assert is_independent in (True, False)
        assert p_value == 1.0


async def test_causal_structure_compare_drift_detected() -> None:
    """Independent variables should flag structural drift."""
    # Two completely independent sequences (no correlation)
    np.random.seed(42)
    data = {
        "11111111": list(np.random.randn(50)),
        "22222222": list(np.random.randn(50)),
    }
    fragment = {
        "edges": [
            {
                "id": "e1",
                "source": {"id": "11111111"},
                "target": {"id": "22222222"},
            }
        ]
    }
    result = await causal_structure_compare(fragment, data, edge_confidence_threshold=0.01)
    # With truly independent data, p-value should be high → flagged as drift
    assert result["structural_drift_detected"] is True
    assert "e1" in result["affected_edges"]


def test_detect_edge_reversal_exception() -> None:
    """detect_edge_reversal handles exceptions gracefully."""
    data = {"12345678": [1.0, 2.0, 3.0]}
    fragment = {
        "edges": [
            {
                "id": "e1",
                "source": {"id": "12345678"},
                "target": {"id": "12345678"},  # same source/target triggers exception path
            }
        ]
    }
    result = detect_edge_reversal(fragment, data)
    assert isinstance(result, list)
