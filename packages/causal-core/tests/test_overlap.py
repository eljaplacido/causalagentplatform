"""The overlap checks (contract 0.13.0), on data whose positivity structure is set by construction.

The gate is T10 (tests/t10); these pin the pieces: the Clopper-Pearson arithmetic behind
``min_region_units``, the rules a region is reported as, the ATE/ATT asymmetry, that nothing is
trimmed, and that a sample too small for any region to be judged says so.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit

import causal_core as cc
from causal_core import overlap
from causal_core.contract import AbstentionReason, AnalysisPolicy, CausalResult

LEAN = AnalysisPolicy(refuters=(), sensitivity=False)


def _frame(n: int, rng: np.random.Generator, never: str | None = None,
           always: str | None = None) -> pd.DataFrame:
    x0 = rng.normal(size=n)
    b0 = (rng.random(n) < 0.5).astype(float)
    b1 = (rng.random(n) < 0.5).astype(float)
    e = expit(0.5 * x0 + 0.3 * b0 - 0.3 * b1)
    stratum = (b0 == 1) & (b1 == 1)
    if never == "stratum":
        e[stratum] = 0.0
    if always == "stratum":
        e[stratum] = 1.0
    t = (rng.random(n) < e).astype(float)
    y = 1.0 + 2.0 * t + x0 + 0.5 * b0 + 0.5 * b1 + rng.normal(size=n)
    return pd.DataFrame({"x0": x0, "b0": b0, "b1": b1, "t": t, "y": y})


def _ran(out: CausalResult) -> bool:
    """The region check judged something: its row is in the design checks."""
    return any(c.name == "overlap regions (positivity)" for c in out.diagnostics.checks)


GRAPH = "x0 -> t; b0 -> t; b1 -> t; x0 -> y; b0 -> y; b1 -> y; t -> y"


def test_min_region_units_is_where_an_absence_first_reaches_the_bound() -> None:
    pol = AnalysisPolicy()
    n_min = overlap.min_region_units(pol)
    assert n_min == math.ceil(math.log(0.05) / math.log(0.95)) == 59
    # With none of an arm among n_min held-out units the bound is below 0.05; one fewer, not.
    assert overlap.upper_bound(0, n_min, 0.05) < 0.05
    assert overlap.upper_bound(0, n_min - 1, 0.05) >= 0.05
    # Exact: the one-sided Clopper-Pearson bound for 0 of m solves (1 - p)^m = alpha.
    assert overlap.upper_bound(0, 100, 0.05) == pytest.approx(1 - 0.05 ** (1 / 100))
    assert overlap.upper_bound(5, 5, 0.05) == 1.0
    assert overlap.min_region_units(AnalysisPolicy(overlap_region_bound=0.0)) == 0


def test_rules_are_one_clause_per_covariate() -> None:
    names, binary = ["x0", "b0"], [False, True]
    path = ((0, False, -0.5), (1, False, 0.5), (0, False, 1.8), (0, True, 2.5))
    assert overlap.rule(path, names, binary) == "1.8 < x0 <= 2.5 and b0 = 1"
    assert overlap.rule(((1, True, 0.5),), names, binary) == "b0 = 0"
    assert overlap.rule((), names, binary) == "all units"


def test_a_stratum_without_treated_units_is_refused_for_the_ate_and_named() -> None:
    frame = _frame(3000, np.random.default_rng(11), never="stratum")
    out = cc.analyze(frame, GRAPH, treatment="t", outcome="y", policy=LEAN)
    assert out.kind == "refusal" and out.reason is AbstentionReason.POSITIVITY, cc.report(out)
    ov = out.diagnostics.overlap if out.diagnostics else None
    assert ov is not None and ov.violation_share > 0.05
    assert any(r.rule == "b0 = 1 and b1 = 1" and r.missing_arm == "treated" for r in ov.regions)
    assert "b0 = 1 and b1 = 1" in out.detail
    assert any("population" in m for m in out.missing_information)
    check = next(c for c in out.diagnostics.checks if c.name == "overlap regions (positivity)")
    assert not check.passed


def test_the_att_needs_overlap_one_way_only() -> None:
    never = _frame(3000, np.random.default_rng(12), never="stratum")
    out = cc.analyze(never, GRAPH, treatment="t", outcome="y", estimand="att", policy=LEAN)
    assert isinstance(out, CausalResult), cc.report(out)
    assert out.diagnostics.overlap is not None and out.diagnostics.overlap.violation_share == 0.0
    always = _frame(3000, np.random.default_rng(13), always="stratum")
    out = cc.analyze(always, GRAPH, treatment="t", outcome="y", estimand="att", policy=LEAN)
    assert out.kind == "refusal" and out.reason is AbstentionReason.POSITIVITY, cc.report(out)
    assert out.diagnostics is not None and out.diagnostics.overlap is not None
    assert all(r.missing_arm == "control" for r in out.diagnostics.overlap.regions)


def test_clean_overlap_is_answered_on_every_unit() -> None:
    frame = _frame(3000, np.random.default_rng(14))
    out = cc.analyze(frame, GRAPH, treatment="t", outcome="y", policy=LEAN)
    assert isinstance(out, CausalResult), cc.report(out)
    ov = out.diagnostics.overlap
    assert ov is not None and _ran(out)
    assert ov.violation_share == 0.0 and not ov.regions and not ov.limited_overlap
    assert out.diagnostics.n_used == len(frame)


def test_turning_the_region_check_off_restores_the_logistic_check_alone() -> None:
    frame = _frame(3000, np.random.default_rng(11), never="stratum")
    pol = LEAN.model_copy(update={"overlap_region_bound": 0.0})
    out = cc.analyze(frame, GRAPH, treatment="t", outcome="y", policy=pol)
    # The main-effects logistic cannot express a two-covariate stratum: it answers.
    assert isinstance(out, CausalResult), cc.report(out)
    assert out.diagnostics.overlap is not None and not _ran(out)


def test_a_sample_too_small_to_judge_any_region_says_so() -> None:
    frame = _frame(100, np.random.default_rng(15))
    out = cc.analyze(frame, GRAPH, treatment="t", outcome="y", policy=LEAN)
    assert isinstance(out, CausalResult), cc.report(out)
    ov = out.diagnostics.overlap
    assert ov is not None and not _ran(out) and ov.violation_share == 0.0


def test_limited_overlap_is_flagged_not_refused() -> None:
    rng = np.random.default_rng(16)
    n = 3000
    x0 = rng.normal(size=n)
    e = np.clip(expit(2.5 * x0), 0.06, 0.94)
    t = (rng.random(n) < e).astype(float)
    y = 1.0 + 2.0 * t + x0 + rng.normal(size=n)
    frame = pd.DataFrame({"x0": x0, "t": t, "y": y})
    out = cc.analyze(frame, "x0 -> t; x0 -> y; t -> y", treatment="t", outcome="y", policy=LEAN)
    assert isinstance(out, CausalResult), cc.report(out)
    ov = out.diagnostics.overlap
    assert ov is not None and ov.limited_overlap and ov.limited_share > 0.05
    assert any(w.startswith("limited overlap") for w in out.warnings)
