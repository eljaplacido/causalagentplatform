"""Rosenbaum bounds against a published matched-pairs example.

Morton et al. (1982): blood lead of 33 children of battery-plant workers, each matched to a
control child of the same age and neighbourhood (the pair differences below are DOS2's `lead$dif`,
CRAN package DOS2 0.5.2). Rosenbaum (2002, Observational Studies, 2nd ed., §4.3.3, Table 4.3)
reports the upper bound on the one-sided p-value of Wilcoxon's signed-rank test at each Γ:
0.0018, 0.0136, 0.0388, 0.0468 and 0.0740 at Γ = 2, 3, 4, 4.25 and 5 -- so at the 0.05 level
the conclusion survives Γ = 4.25 and not Γ = 5. The skill (and the kernel it calls) must
reproduce those figures; the E-value half of the skill is pinned by test_effect_estimate.py.
"""

from __future__ import annotations

import numpy as np
import pytest
from causal_core.sensitivity import rosenbaum_pairs, wilcoxon_upper_p

from src.skills.sensitivity_analysis import SensitivityAnalysisError, sensitivity_analysis

LEAD_DIF = [1, -3, 14, 23, -6, 42, 22, 5, 18, 47, 6, 36, 25, 30, 25, 16, 32, 16, 60, 17,
            15, 9, 4, 1, 23, 0, 25, 13, 2, 7, 14, -9, -3]
D = np.asarray(LEAD_DIF, dtype=float)
PUBLISHED = {2.0: 0.0018, 3.0: 0.0136, 4.0: 0.0388, 4.25: 0.0468, 5.0: 0.0740}


def test_the_bound_reproduces_rosenbaum_table_4_3() -> None:
    assert len(LEAD_DIF) == 33
    for gamma, p in PUBLISHED.items():
        assert wilcoxon_upper_p(D, gamma) == pytest.approx(p, abs=5e-5), gamma
    assert wilcoxon_upper_p(D, 1.0) < 1e-4


def test_the_sensitivity_value_lies_where_the_table_says() -> None:
    gamma_star, _ = rosenbaum_pairs(D, 0.05)
    assert gamma_star is not None and 4.25 < gamma_star < 5.0
    assert wilcoxon_upper_p(D, gamma_star) == pytest.approx(0.05, abs=1e-8)


async def test_the_skill_reports_rosenbaum_gamma_from_matched_pairs() -> None:
    # ci_level 0.90 -> one-sided 0.05, the level of the published statement
    out = await sensitivity_analysis(10.0, [5.0, 15.0], [], pair_differences=LEAD_DIF,
                                     ci_level=0.90)
    assert out["rosenbaum_gamma"] is not None and 4.25 < out["rosenbaum_gamma"] < 5.0
    assert out["rosenbaum"]["p_upper"]["2"] == pytest.approx(PUBLISHED[2.0], abs=5e-5)
    assert out["rosenbaum"]["alternative"] == "greater" and out["rosenbaum"]["n_pairs"] == 33


async def test_the_default_level_is_the_interval_level_halved() -> None:
    out = await sensitivity_analysis(10.0, [5.0, 15.0], [], pair_differences=LEAD_DIF)
    assert out["rosenbaum"]["alpha"] == pytest.approx(0.025)
    assert 1.0 < out["rosenbaum_gamma"] < 4.25


async def test_the_direction_follows_the_estimate() -> None:
    neg = await sensitivity_analysis(-10.0, [-15.0, -5.0], [],
                                     pair_differences=[-x for x in LEAD_DIF], ci_level=0.90)
    assert neg["rosenbaum"]["alternative"] == "less"
    assert 4.25 < neg["rosenbaum_gamma"] < 5.0
    wrong_way = await sensitivity_analysis(-10.0, [-15.0, -5.0], [], pair_differences=LEAD_DIF)
    assert wrong_way["rosenbaum_gamma"] is None


async def test_without_pairs_there_is_no_gamma() -> None:
    out = await sensitivity_analysis(10.0, [5.0, 15.0], [])
    assert out["rosenbaum_gamma"] is None and out["rosenbaum"] is None
    with pytest.raises(SensitivityAnalysisError):
        await sensitivity_analysis(10.0, [5.0, 15.0], [], pair_differences=[1.0])
