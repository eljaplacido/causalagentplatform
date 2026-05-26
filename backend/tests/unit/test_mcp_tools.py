"""
Unit tests for the MCP tool wrappers (backend/mcp_server/tools.py).

These tests call the wrapper functions directly — no MCP stdio transport — so a
broken transport binding can't mask a broken skill call, and vice versa. The
registry parity test guards against drift between the wrapper module and what
``server.py`` advertises.

Contract under test:
  * Every wrapper returns JSON-native dicts (no DataFrames / numpy / Pydantic).
  * On skill exceptions, wrappers return the structured envelope
    ``{ok: False, error, error_type, tool}`` instead of raising.
  * Happy-path returns include ``ok: True``.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_server.tools import (
    CAUSAL_TOOLS,
    tool_causal_structure_compare,
    tool_counterfactual_reason,
    tool_drift_detect,
    tool_effect_estimate,
    tool_identification_check,
    tool_intervention_estimate,
    tool_sensitivity_analysis,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def approved_fragment() -> dict[str, Any]:
    """Minimal APPROVED fragment with a single treatment→outcome edge.

    IDs are kept ≤8 chars because the effect_estimate skill truncates names to
    8 characters when matching against DataFrame columns — longer IDs would
    silently mismatch the column lookup and fail estimation.
    """
    return {
        "approval_status": "APPROVED",
        "edges": [
            {
                "source": {"id": "tx", "object_type": "Treatment"},
                "target": {"id": "oy", "object_type": "Outcome"},
                "confidence": 0.8,
                "edge_type": "CAUSES",
            }
        ],
    }


@pytest.fixture
def observational_data() -> dict[str, list[float]]:
    """Simple two-column observational dataset, JSON-native (column-oriented)."""
    return {
        "tx": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0],
        "oy": [0.2, 0.9, 0.1, 1.1, 0.3, 1.0, 0.0, 1.2],
    }


# ── Registry parity ───────────────────────────────────────────────────────────


def test_causal_tools_registry_matches_module() -> None:
    """CAUSAL_TOOLS contains exactly the 7 wrappers and no extras."""
    expected = {
        "causal_effect_estimate",
        "causal_counterfactual_reason",
        "causal_identification_check",
        "causal_sensitivity_analysis",
        "causal_drift_detect",
        "causal_intervention_estimate",
        "causal_structure_compare",
    }
    assert set(CAUSAL_TOOLS.keys()) == expected
    assert all(callable(fn) for fn in CAUSAL_TOOLS.values())


# ── tool_effect_estimate ──────────────────────────────────────────────────────


async def test_effect_estimate_happy_path(approved_fragment: dict[str, Any]) -> None:
    """Approved fragment without data returns a graph-heuristic ATE."""
    result = await tool_effect_estimate(
        fragment=approved_fragment,
        identification_strategy="backdoor",
    )
    assert result["ok"] is True
    assert "ate" in result
    assert "estimator" in result


async def test_effect_estimate_with_data(
    approved_fragment: dict[str, Any], observational_data: dict[str, list[float]]
) -> None:
    """JSON-native data dict is accepted and produces a numeric ATE."""
    result = await tool_effect_estimate(
        fragment=approved_fragment,
        identification_strategy="backdoor",
        data=observational_data,
    )
    assert result["ok"] is True
    assert isinstance(result["ate"], (int, float)) or result["ate"] is None


async def test_effect_estimate_unapproved_returns_error_envelope() -> None:
    """Unapproved fragments produce a structured error, not a raised exception."""
    result = await tool_effect_estimate(
        fragment={"approval_status": "DRAFT", "edges": []},
        identification_strategy="backdoor",
    )
    assert result["ok"] is False
    assert result["tool"] == "effect_estimate"
    assert "APPROVED" in result["error"]
    assert result["error_type"]  # non-empty class name


# ── tool_identification_check ─────────────────────────────────────────────────


async def test_identification_check_approved(approved_fragment: dict[str, Any]) -> None:
    """Approved fragment + ignorability assumption identifies via backdoor."""
    result = await tool_identification_check(
        fragment=approved_fragment,
        assumptions=[
            {"assumption_type": "IGNORABILITY", "statement": "no unobserved confounders"}
        ],
    )
    assert result["ok"] is True
    assert result["status"] in {"IDENTIFIED", "PARTIALLY_IDENTIFIED", "UNIDENTIFIED"}
    assert "strategy" in result


async def test_identification_check_unapproved_returns_unidentified() -> None:
    """Unapproved fragment short-circuits to UNIDENTIFIED — not raised."""
    result = await tool_identification_check(
        fragment={"approval_status": "DRAFT", "edges": []},
    )
    assert result["ok"] is True  # skill returns a result rather than raising
    assert result["status"] == "UNIDENTIFIED"
    assert result["reason"] == "fragment_not_approved"


# ── tool_counterfactual_reason ────────────────────────────────────────────────


async def test_counterfactual_reason_happy_path() -> None:
    """A simple counterfactual query returns a structured narrative."""
    result = await tool_counterfactual_reason(
        proposed_edges=[
            {
                "source": {"id": "price", "object_type": "Treatment"},
                "target": {"id": "demand", "object_type": "Outcome"},
                "confidence": 0.7,
            }
        ],
        query="What if we lower price by 10%?",
        assumptions=[],
    )
    assert result["ok"] is True
    assert "identification_status" in result
    assert "narrative" in result


# ── tool_sensitivity_analysis ─────────────────────────────────────────────────


async def test_sensitivity_analysis_happy_path() -> None:
    """Point estimate + CI yields an E-value and a robustness verdict."""
    result = await tool_sensitivity_analysis(
        ate=0.35,
        confidence_interval=[0.20, 0.50],
        assumptions=[{"assumption_type": "IGNORABILITY"}],
    )
    assert result["ok"] is True
    assert "e_value" in result
    assert "robust_to_unmeasured_confounding" in result


async def test_sensitivity_analysis_none_ate() -> None:
    """A null ATE still returns a structured result rather than raising."""
    result = await tool_sensitivity_analysis(
        ate=None,
        confidence_interval=[None, None],
    )
    # Either ok=True with a degenerate result OR ok=False with envelope —
    # whichever path the skill chooses, the wrapper must not raise.
    assert "ok" in result


# ── tool_drift_detect ─────────────────────────────────────────────────────────


async def test_drift_detect_no_drift() -> None:
    """Identical reference and current data report drift_type='none'."""
    same = {"feature_a": [0.1, 0.2, 0.3, 0.4, 0.5] * 4}
    result = await tool_drift_detect(reference_data=same, current_data=same)
    assert result["ok"] is True
    assert result["drift_type"] == "none"
    assert result["drifted_features"] == []


async def test_drift_detect_shifted() -> None:
    """A clearly shifted distribution registers in feature_scores."""
    ref = {"feature_a": [0.0, 0.1, 0.2, 0.0, 0.1] * 4}
    cur = {"feature_a": [5.0, 5.1, 5.2, 5.0, 5.1] * 4}
    result = await tool_drift_detect(
        reference_data=ref, current_data=cur, kl_threshold=0.01
    )
    assert result["ok"] is True
    assert "feature_a" in result["feature_scores"]


async def test_drift_detect_empty_inputs_returns_envelope_or_none() -> None:
    """Empty inputs must not raise — skill returns drift_type='none'."""
    result = await tool_drift_detect(reference_data={}, current_data={})
    assert "ok" in result


# ── tool_intervention_estimate ────────────────────────────────────────────────


async def test_intervention_estimate_happy_path() -> None:
    """A proposed CAUSES edge returns a risk tier and recommended action."""
    result = await tool_intervention_estimate(
        source_id="email_send_count",
        target_id="conversion_rate",
        edge_type="CAUSES",
        confidence=0.75,
    )
    assert result["ok"] is True
    assert "risk_tier" in result
    assert result["risk_tier"] in {"LOW", "MEDIUM", "HIGH"}
    assert "action" in result


# ── tool_causal_structure_compare ─────────────────────────────────────────────


async def test_causal_structure_compare_happy_path(
    approved_fragment: dict[str, Any], observational_data: dict[str, list[float]]
) -> None:
    """Fragment + matching data returns a structural-drift verdict."""
    result = await tool_causal_structure_compare(
        fragment=approved_fragment,
        data=observational_data,
    )
    assert result["ok"] is True
    assert "structural_drift_detected" in result
    assert isinstance(result["structural_drift_detected"], bool)


async def test_causal_structure_compare_bad_input_returns_envelope() -> None:
    """A malformed fragment shape produces the structured error envelope."""
    result = await tool_causal_structure_compare(
        fragment={"approval_status": "APPROVED"},  # missing 'edges'
        data={"x": [1.0, 2.0]},
    )
    # Either the skill handles missing edges gracefully (ok=True) or raises
    # (ok=False) — both are acceptable; what matters is no exception escapes.
    assert "ok" in result
