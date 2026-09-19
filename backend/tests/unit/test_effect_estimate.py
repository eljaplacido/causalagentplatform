"""
Causal skills backed by the causal-core kernel, and the heuristics that remain labelled.

Pins the honest behaviour that replaced the heuristic one:
  * effect_estimate returns the kernel's estimate and interval with data, a refusal
    without data, and never a constant interval;
  * identification_check runs a real identification algorithm on the fragment graph;
  * sensitivity_analysis computes an E-value only on a known scale;
  * intervention_estimate and counterfactual_reason are labelled heuristics whose
    fields are not named like estimates.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.skills._kernel import fragment_to_graph, outcome_id, treatment_id
from src.skills.counterfactual_reason import counterfactual_reason
from src.skills.effect_estimate import EffectEstimationError, effect_estimate
from src.skills.identification_check import identification_check
from src.skills.intervention_estimate import intervention_estimate
from src.skills.sensitivity_analysis import sensitivity_analysis

SRC = Path(__file__).resolve().parents[2] / "src"


def _edge(src: str, src_type: str, dst: str, dst_type: str, kind: str = "CAUSES") -> dict[str, Any]:
    return {
        "source": {"id": src, "object_type": src_type},
        "target": {"id": dst, "object_type": dst_type},
        "edge_type": kind,
        "confidence": 0.9,
    }


# Long ids on purpose: the old path truncated names to 8 characters.
T, Y, Z = "discount_campaign", "weekly_revenue", "customer_segment"
CONFOUNDED = {
    "approval_status": "APPROVED",
    "edges": [
        _edge(Z, "Confounder", T, "Treatment", "CONFOUNDS"),
        _edge(Z, "Confounder", Y, "Outcome", "CONFOUNDS"),
        _edge(T, "Treatment", Y, "Outcome"),
    ],
}


def _data(n: int = 2000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    z = rng.normal(size=n)
    t = (0.8 * z + rng.logistic(size=n) > 0).astype(float)
    y = 2.0 * t + 1.2 * z + rng.normal(size=n)
    return pd.DataFrame({Z: z, T: t, Y: y})


# ── adapter ────────────────────────────────────────────────────────────────────


def test_fragment_to_graph_keeps_full_ids_and_direction() -> None:
    frag = {
        "approval_status": "APPROVED",
        "edges": [{**_edge("a", "Treatment", "b", "Outcome"), "direction": "BACKWARD"}],
    }
    g = fragment_to_graph(frag)
    assert [(e.source, e.target) for e in g.edges] == [("b", "a")]
    assert all(e.status.value == "approved" for e in g.edges)
    assert treatment_id(CONFOUNDED) == T and outcome_id(CONFOUNDED) == Y


# ── effect_estimate ────────────────────────────────────────────────────────────


async def test_unapproved_fragment_is_refused_by_governance() -> None:
    with pytest.raises(EffectEstimationError, match="APPROVED"):
        await effect_estimate({"approval_status": "DRAFT"}, "backdoor", [])


async def test_empty_fragment_is_a_refusal() -> None:
    out = await effect_estimate({"approval_status": "APPROVED", "edges": []}, "backdoor", [])
    assert out["status"] == "refusal" and out["ate"] is None and out["estimator"] == "none"


async def test_no_data_is_a_refusal_not_a_heuristic_number() -> None:
    out = await effect_estimate(CONFOUNDED, "backdoor", [])
    assert out["status"] == "refusal" and out["refusal_reason"] == "NO_DATA"
    assert out["ate"] is None and out["confidence_interval"] == [None, None]


async def test_with_data_returns_the_kernel_estimate_and_a_real_interval() -> None:
    out = await effect_estimate(CONFOUNDED, "iv", [], data=_data())
    assert out["status"] == "result"
    assert out["identification_strategy"] == "backdoor"  # the graph decides, not the hint
    assert out["requested_strategy"] == "iv"
    lo, hi = out["confidence_interval"]
    assert lo < 2.0 < hi and hi - lo < 1.0
    assert out["outcome"]["kind"] == "result"
    assert out["outcome"]["certificate"]["adjustment_set"] == [Z]


async def test_design_failures_come_back_as_refusals() -> None:
    small = _data(n=20)
    out = await effect_estimate(CONFOUNDED, "backdoor", [], data=small)
    assert out["status"] == "refusal" and out["refusal_reason"] == "LOW_N" and out["ate"] is None


def test_no_code_path_returns_a_constant_interval() -> None:
    pattern = re.compile(r"\(\s*-0\.5\s*,\s*0\.5\s*\)|\[\s*-0\.5\s*,\s*0\.5\s*\]")
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# ── identification_check ───────────────────────────────────────────────────────


async def test_identification_uses_the_graph() -> None:
    out = await identification_check(CONFOUNDED, [])
    assert out["status"] == "IDENTIFIED" and out["strategy"] == "backdoor"
    assert out["adjustment_set"] == [Z] and out["tier"] == "A"


async def test_identification_unapproved_short_circuits() -> None:
    out = await identification_check({"approval_status": "DRAFT", "edges": []}, [])
    assert out == {"status": "UNIDENTIFIED", "strategy": "none", "reason": "fragment_not_approved"}


# ── sensitivity_analysis ───────────────────────────────────────────────────────


async def test_e_value_needs_a_scale() -> None:
    out = await sensitivity_analysis(0.35, [0.2, 0.5], [])
    assert out["e_value"] is None and "standard deviation" in out["reason"]
    assert out["rosenbaum_gamma"] is None


async def test_e_value_on_the_risk_ratio_scale() -> None:
    out = await sensitivity_analysis(2.0, [1.5, 2.7], [], effect_scale="risk_ratio")
    assert out["e_value"] == pytest.approx(2.0 + np.sqrt(2.0), abs=1e-4)
    assert out["e_value_ci"] == pytest.approx(1.5 + np.sqrt(1.5 * 0.5), abs=1e-4)
    assert out["robust_to_unmeasured_confounding"] is True


async def test_e_value_is_scale_free_given_the_outcome_sd() -> None:
    dollars = await sensitivity_analysis(10.0, [5.0, 15.0], [], outcome_sd=20.0)
    cents = await sensitivity_analysis(1000.0, [500.0, 1500.0], [], outcome_sd=2000.0)
    assert dollars["e_value"] == pytest.approx(cents["e_value"])
    crosses = await sensitivity_analysis(10.0, [-5.0, 25.0], [], outcome_sd=20.0)
    assert crosses["e_value_ci"] == 1.0 and crosses["robust_to_unmeasured_confounding"] is False


# ── labelled heuristics ────────────────────────────────────────────────────────


async def test_intervention_score_is_labelled_heuristic() -> None:
    out = await intervention_estimate(
        source_id="a", target_id="b", edge_type="CAUSES", confidence=0.9
    )
    assert out["heuristic"] is True and "not a causal effect" in out["caveat"]
    assert not {"ate", "estimated_effect", "signed_effect", "confidence_interval"} & set(out)


async def test_counterfactual_summary_does_not_claim_identification_or_numbers() -> None:
    out = await counterfactual_reason(proposed_edges=[{"confidence": 0.95}], query="what if?")
    assert out["heuristic"] is True and out["identification_status"] == "NOT_ASSESSED"
    assert "%" not in out["narrative"] and "Not answered" in out["narrative"]
