"""Rosenbaum bounds: the bound, the matching, and what analyze reports.

The gate (tests/t9, benchmarks/t9-rosenbaum/) checks Γ* against planted hidden bias; these pin
the pieces -- a published example, the algebra of the sensitivity value, the matching rule --
and the contract fields.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

import causal_core as cc
from causal_core.contract import AnalysisPolicy, CausalResult
from causal_core.sensitivity import (
    match_pairs,
    one_sided_alpha,
    rosenbaum_pairs,
    sensitivity_value,
    wilcoxon_upper_p,
)

# Morton et al. (1982) lead pairs (DOS2::lead$dif); Rosenbaum (2002) Table 4.3.
LEAD = np.array([1, -3, 14, 23, -6, 42, 22, 5, 18, 47, 6, 36, 25, 30, 25, 16, 32, 16, 60, 17,
                 15, 9, 4, 1, 23, 0, 25, 13, 2, 7, 14, -9, -3], dtype=float)


def test_published_upper_bounds() -> None:
    for gamma, p in {2.0: 0.0018, 3.0: 0.0136, 4.0: 0.0388, 4.25: 0.0468, 5.0: 0.0740}.items():
        assert wilcoxon_upper_p(LEAD, gamma) == pytest.approx(p, abs=5e-5)


def test_at_gamma_one_the_bound_is_the_usual_wilcoxon_test() -> None:
    rng = np.random.default_rng(3)
    d = rng.normal(0.2, 1, 80)
    ref = stats.wilcoxon(d, alternative="greater", correction=False, method="approx")
    assert wilcoxon_upper_p(d, 1.0) == pytest.approx(ref.pvalue, rel=1e-9)


def test_the_sensitivity_value_is_the_closed_form_root() -> None:
    """(T − πΣr)² = z²π(1−π)Σr², the smaller root, agrees with the bisection."""
    rng = np.random.default_rng(11)
    d = rng.normal(0.4, 1, 300)
    alpha = 0.025
    r = stats.rankdata(np.abs(d))
    t, s1, s2 = r[d > 0].sum(), r.sum(), (r**2).sum()
    z2 = stats.norm.isf(alpha) ** 2
    a, b, c = s1**2 + z2 * s2, 2 * t * s1 + z2 * s2, t**2
    pi = (b - math.sqrt(b * b - 4 * a * c)) / (2 * a)
    assert sensitivity_value(d, alpha) == pytest.approx(pi / (1 - pi), rel=1e-8)


def test_no_gamma_when_the_test_does_not_reject_and_direction_is_symmetric() -> None:
    rng = np.random.default_rng(5)
    assert sensitivity_value(rng.normal(0, 1, 50) * 0 + np.linspace(-1, 1, 50), 0.025) is None
    d = rng.normal(0.5, 1, 200)
    up, _ = rosenbaum_pairs(d, 0.025, 1.0)
    down, table = rosenbaum_pairs(-d, 0.025, -1.0)
    assert up == pytest.approx(down) and up is not None and up > 1
    assert rosenbaum_pairs(d, 0.025, -1.0)[0] is None
    assert list(table) == ["1", "1.25", "1.5", "2", "3", "4", "6"]
    assert list(table.values()) == sorted(table.values())
    assert one_sided_alpha(0.95) == pytest.approx(0.025)


def _greedy_reference(t: np.ndarray, s: np.ndarray, caliper: float) -> set[tuple[int, int]]:
    """The documented rule, written the slow way."""
    treated, control = list(np.flatnonzero(t == 1)), list(np.flatnonzero(t == 0))
    focal_t = len(treated) <= len(control)
    focal, pool = (treated, control) if focal_t else (control, treated)
    focal = sorted(focal, key=lambda i: (-s[i] if focal_t else s[i], i))
    avail = set(pool)
    out = set()
    for i in focal:
        if not avail:
            break
        j = min(avail, key=lambda k: (abs(s[k] - s[i]), -s[k], k))
        if abs(s[j] - s[i]) <= caliper:
            avail.remove(j)
            out.add((i, j) if focal_t else (j, i))
    return out


@pytest.mark.parametrize("seed", range(6))
def test_matching_follows_the_documented_greedy_rule(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = 60
    t = (rng.random(n) < (0.3 if seed % 2 else 0.7)).astype(float)
    s = np.round(rng.normal(size=n), 1)  # ties on purpose
    caliper = 0.15
    ti, ci = match_pairs(t, s, caliper)
    assert set(zip(ti.tolist(), ci.tolist(), strict=True)) == _greedy_reference(t, s, caliper)
    assert len(set(ci.tolist())) == len(ci) and (t[ti] == 1).all() and (t[ci] == 0).all()
    assert (np.abs(s[ti] - s[ci]) <= caliper).all()


def _observational(n: int = 2000, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    x = rng.binomial(1, 0.5, n).astype(float)
    t = (rng.random(n) < 1 / (1 + np.exp(-(-1 + x)))).astype(float)
    return pd.DataFrame({"X": x, "T": t, "Y": 2 * x + 0.4 * t + rng.normal(size=n)})


def test_analyze_reports_the_matched_design_and_a_claim() -> None:
    out = cc.analyze(_observational(), "X -> T; X -> Y; T -> Y", treatment="T", outcome="Y",
                     policy=AnalysisPolicy(refuters=()))
    assert isinstance(out, CausalResult) and out.sensitivity is not None
    rb = out.sensitivity.rosenbaum
    assert rb is not None and out.sensitivity.rosenbaum_null_reason is None
    assert rb.n_pairs > 400 and rb.max_abs_smd == pytest.approx(0.0, abs=1e-12)
    assert rb.gamma_star is not None and rb.gamma_star > 1
    claim = {c.id: c for c in out.claims}["rosenbaum.gamma"]
    assert claim.value == pytest.approx(rb.gamma_star)
    assert claim.path == "/sensitivity/rosenbaum/gamma_star"


def test_too_few_pairs_withholds_the_bound() -> None:
    frame = _observational()
    out = cc.analyze(frame, "X -> T; X -> Y; T -> Y", treatment="T", outcome="Y",
                     policy=AnalysisPolicy(refuters=(), min_per_arm=10, match_caliper=1e-9))
    assert isinstance(out, CausalResult) and out.sensitivity is not None
    # A caliper of ~0 still matches exact ties (X is binary), so the bound is reported ...
    assert out.sensitivity.rosenbaum is not None
    rng = np.random.default_rng(2)
    frame = frame.assign(X=frame["X"] + rng.normal(0, 1, len(frame)))
    out = cc.analyze(frame, "X -> T; X -> Y; T -> Y", treatment="T", outcome="Y",
                     policy=AnalysisPolicy(refuters=(), match_caliper=1e-9))
    # ... and with a continuous X nothing lies within it.
    assert isinstance(out, CausalResult) and out.sensitivity is not None
    assert out.sensitivity.rosenbaum is None
    assert "fewer than min_per_arm" in (out.sensitivity.rosenbaum_null_reason or "")
