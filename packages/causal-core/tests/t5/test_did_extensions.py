"""
Difference-in-differences with covariates (doubly robust) and not-yet-treated comparisons.

The register's `did-extensions` entry leaves by this file: each extension ships with its own
pre-registered T5 version (v4) and seeded defects, and the gate itself runs in
test_t5_gate.py / test_seeded_defects.py (`pytest -m t5`). What is here is fast and asserts
known truth: panels simulated from a structural model whose effect is set, algebraic identities
the estimator must satisfy, and the refusals the extensions add.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np
import pandas as pd
import pytest

import causal_core as cc
import causal_core.panel as pm
from causal_core.bench import t5
from causal_core.bench.defects_t5 import DEFECTS
from causal_core.contract import (
    AbstentionReason,
    AnalysisPolicy,
    CausalQuery,
    CausalResult,
    Tier,
)

EXTENSION_DESIGNS = {
    "covariate_selection", "outcome_misspecified", "propensity_misspecified",
    "not_yet_treated", "covariates_not_yet_treated",
}
EXTENSION_DEFECTS = {
    "ignore_covariates": ("covariate_selection", "bias"),
    "not_yet_treated_includes_treated": ("not_yet_treated", "bias"),
    "propensity_ignored": ("outcome_misspecified", "bias"),
    "outcome_regression_ignored": ("propensity_misspecified", "bias"),
}


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t5.load_prereg()[0]


def did(frame: pd.DataFrame, **over: Any) -> Any:
    kw: dict[str, Any] = {
        "treatment": "rollout", "outcome": "latency", "unit": "unit", "time": "period",
        "estimand": "att", "design": "difference_in_differences",
    }
    kw.update(over)
    return cc.analyze(frame, t5.GRAPH, **kw)


def bigger(name: str, factor: int) -> t5.Design:
    d = t5.DESIGNS[name]
    return dataclasses.replace(d, cohorts={g: n * factor for g, n in d.cohorts.items()})


# ── registration: each extension has its T5 version, designs and seeded defects ──


def test_each_extension_is_pre_registered_with_designs_refusals_and_defects(
    cfg: dict[str, Any],
) -> None:
    assert cfg["meta"]["version"] >= 4
    assert set(cfg["estimation"]["designs"]) >= EXTENSION_DESIGNS
    assert set(cfg["sensitivity"]["m_true"]) >= EXTENSION_DESIGNS
    assert set(cfg["refusals"]["must_refuse"]) >= {
        "poor_overlap", "time_varying_covariate", "no_comparison_units"
    }
    assert set(cfg["seeded_defects"]["required"]) >= set(EXTENSION_DEFECTS)
    for name, catcher in EXTENSION_DEFECTS.items():
        assert t5.CATCHER[name] == catcher and name in DEFECTS
    ext = cfg["extensions"]
    assert set(ext["covariates"]) >= EXTENSION_DESIGNS - {"not_yet_treated"}
    assert ext["control_group"]["not_yet_treated"] == "not_yet_treated"


def test_every_extension_design_is_analysed_the_way_it_was_registered(
    cfg: dict[str, Any],
) -> None:
    assert t5.options(cfg, "covariate_selection") == {"covariates": ("x", "w")}
    assert t5.options(cfg, "covariates_not_yet_treated") == {
        "covariates": ("x",), "control_group": "not_yet_treated"
    }
    assert t5.options(cfg, "staggered_dynamic") == {}, "v1-v3 designs are analysed as before"


# ── truth ───────────────────────────────────────────────────────────────────────


def test_cohort_probabilities_integrate_the_selection_model() -> None:
    """Quadrature agrees with a large simulated draw from the same selection model."""
    d = t5.DESIGNS["covariate_selection"]
    pi = t5.cohort_probabilities(d)
    assert abs(sum(pi.values()) - 1) < 1e-12
    rng = np.random.default_rng(0)
    x = rng.normal(size=400_000)
    w = (rng.random(400_000) < 0.5).astype(float)
    assert d.scores is not None
    empirical = t5._probabilities(d.scores, x, w).mean(axis=0)
    assert np.allclose(list(pi.values()), empirical, atol=3e-3)


def test_cells_with_no_comparison_are_outside_the_estimand() -> None:
    d = t5.DESIGNS["not_yet_treated"]
    assert t5.estimable(d, 6, 7) and not t5.estimable(d, 6, 8) and not t5.estimable(d, 8, 8)
    _, events = t5.truth(d, 8)
    assert max(events) == 4, "cohort 3 at period 7 is the longest estimable exposure"


# ── the estimator ───────────────────────────────────────────────────────────────


def test_doubly_robust_cells_reduce_to_the_difference_of_means_without_covariates() -> None:
    """With only an intercept, both nuisance models are constants and the DR cell is the
    unconditional one -- estimate and influence function, to rounding."""
    frame = t5.simulate(t5.DESIGNS["staggered_dynamic"], 8, 0.5, np.random.default_rng(1),
                        drawn=True)
    q = CausalQuery(treatment="rollout", outcome="latency", unit="unit", time="period",
                    design="difference_in_differences", estimand="att")
    panel, _, _ = pm.build_panel(frame, q, AnalysisPolicy())
    plain, _ = pm.group_time(panel)
    dr, _ = pm.group_time(dataclasses.replace(panel, x=np.zeros((panel.n, 0))))
    assert all(c.overlap is not None for c in dr)
    for a, b in zip(plain, dr, strict=True):
        assert abs(a.att - b.att) < 1e-10
        assert np.max(np.abs(a.psi - b.psi)) < 1e-9


def test_the_doubly_robust_influence_function_matches_the_jackknife() -> None:
    """Leave-one-out pseudo-values of a DR cell track its influence function, in a cell whose
    outcome model is misspecified (where the propensity model's estimation effect matters)."""
    rng = np.random.default_rng(5)
    n = 600
    x = rng.normal(size=n)
    d = (rng.random(n) < 1 / (1 + np.exp(0.693 - x))).astype(float)
    dy = 0.75 * (x**2 - 1) + 1.2 * rng.normal(size=n) + 2.0 * d
    X = np.column_stack([np.ones(n), x])
    att, psi, _ = pm.dr_cell(X, d, dy, 0.99)
    loo = np.array([pm.dr_cell(np.delete(X, i, 0), np.delete(d, i), np.delete(dy, i), 0.99)[0]
                    for i in range(n)])
    pseudo = (n - 1) * (att - loo)
    assert np.corrcoef(psi, pseudo)[0, 1] > 0.99
    assert 0.8 < np.mean(psi**2) / np.mean(pseudo**2) < 1.05


def test_covariates_remove_the_bias_of_selection_into_timing() -> None:
    """Covariate-driven timing: the unconditional estimate is biased (by 0.54 in the design),
    the doubly robust one is not."""
    design = bigger("covariate_selection", 10)
    frame = t5.simulate(design, 8, 0.5, np.random.default_rng(11))
    truth, _ = t5.truth(design, 8)
    adjusted = did(frame, covariates=("x", "w"))
    plain = did(frame)
    assert isinstance(adjusted, CausalResult) and isinstance(plain, CausalResult)
    se = adjusted.primary.se or 1.0
    assert abs(adjusted.primary.point - truth) < 4 * se
    assert plain.primary.point - truth > 6 * (plain.primary.se or 1.0)
    assert adjusted.primary.estimator == "did_group_time_dr"
    assert adjusted.primary.details["covariates"] == "x, w"
    assert adjusted.diagnostics.propensity is not None
    assert adjusted.diagnostics.propensity.share_outside_bounds <= 0.05


@pytest.mark.parametrize("name", ["outcome_misspecified", "propensity_misspecified"])
def test_either_model_being_right_is_enough(name: str) -> None:
    design = bigger(name, 10)
    frame = t5.simulate(design, 8, 0.5, np.random.default_rng(12))
    out = did(frame, covariates=("x",))
    assert isinstance(out, CausalResult)
    assert abs(out.primary.point - 2.0) < 4 * (out.primary.se or 1.0)


def test_a_rollout_that_reaches_everyone_is_compared_with_the_not_yet_treated() -> None:
    design = bigger("not_yet_treated", 4)
    frame = t5.simulate(design, 8, 0.5, np.random.default_rng(13), drawn=True)
    refused = did(frame)
    assert refused.kind == "refusal" and refused.reason is AbstentionReason.DESIGN_CHECK_FAILED
    assert "not_yet_treated" in refused.detail
    out = did(frame, control_group="not_yet_treated")
    assert isinstance(out, CausalResult), out
    truth, events = t5.truth(design, 8)
    assert abs(out.primary.point - truth) < 4 * (out.primary.se or 1.0)
    assert max(e.event_time for e in out.event_study) == max(events)
    assert any("serve only as comparisons" in w for w in out.warnings)
    assert out.certificate.tier is Tier.C
    assert any("including units serving as comparisons" in a
               for a in out.certificate.assumptions)


def test_the_not_yet_treated_comparison_never_holds_a_unit_treated_by_then() -> None:
    cohort = np.array([-1, 2, 3, 4, 5, 7] * 3)
    panel = pm.Panel(y=np.zeros((cohort.size, 8)), cohort=cohort, periods=tuple(range(8)),
                     n_rows=cohort.size * 8, control_group="not_yet_treated")
    for g in (2, 3, 4, 5, 7):
        for t in range(8):
            if t == g - 1:
                continue
            ctrl = pm.controls(panel, g, t)
            assert not (ctrl & (cohort == g)).any(), "a cohort is not its own comparison"
            treated_then = (cohort >= 0) & ((cohort <= t) | (cohort <= g - 1))
            assert not (ctrl & treated_then).any(), (g, t)
            assert ctrl[cohort == -1].all(), "the never-treated always compare"


def test_the_certificate_says_what_the_covariates_assume() -> None:
    q = CausalQuery(treatment="rollout", outcome="latency", unit="unit", time="period",
                    design="difference_in_differences", estimand="att", covariates=("x",),
                    control_group="not_yet_treated")
    cert = pm.certificate("sha256:x", "test", q)
    assert "X = (x)" in cert.estimand and "C_gt" in cert.estimand
    assert any(a.startswith("conditional parallel trends") for a in cert.assumptions)
    assert any(a.startswith("overlap") for a in cert.assumptions)
    assert pm.certificate("sha256:x", "test").assumptions == pm.ASSUMPTIONS


# ── refusals the extensions add ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def covariate_panel() -> pd.DataFrame:
    return t5.simulate(t5.DESIGNS["covariate_selection"], 8, 0.5, np.random.default_rng(21))


def test_a_covariate_that_varies_within_a_unit_is_refused(covariate_panel: pd.DataFrame) -> None:
    frame = covariate_panel.assign(x=covariate_panel["x"] + 0.1 * covariate_panel["period"])
    out = did(frame, covariates=("x",))
    assert out.kind == "refusal" and out.reason is AbstentionReason.INVALID_INPUT
    assert "vary within a unit" in out.detail and out.missing_information


@pytest.mark.parametrize(
    ("change", "covariates", "says"),
    [
        (lambda f: f.assign(x=f["x"].map(lambda v: "hi" if v > 0 else "lo")), ("x",), "numeric"),
        (lambda f: f, ("nope",), "not found"),
        (lambda f: f, ("period",), "name the unit"),
        (lambda f: f.assign(x=np.where(f.index == 3, np.nan, f["x"])), ("x",), "missing"),
        (lambda f: f.assign(x=1.0), ("x",), "collinear"),
    ],
)
def test_covariates_the_estimator_cannot_use_are_refused(
    covariate_panel: pd.DataFrame, change: Any, covariates: tuple[str, ...], says: str
) -> None:
    out = did(change(covariate_panel), covariates=covariates)
    assert out.kind == "refusal" and out.reason is AbstentionReason.INVALID_INPUT, out
    assert says in out.detail


def test_poor_overlap_is_refused_not_weighted_through(cfg: dict[str, Any]) -> None:
    out = did(t5.refusal_panel("poor_overlap", cfg), covariates=("x",))
    assert out.kind == "refusal" and out.reason is AbstentionReason.POSITIVITY
    assert "poor overlap" in out.detail


def test_panel_options_with_another_design_are_refused_not_ignored() -> None:
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"z": rng.normal(size=200)})
    frame["x"] = (frame["z"] + rng.normal(size=200) > 0).astype(float)
    frame["y"] = frame["x"] + frame["z"] + rng.normal(size=200)
    for over in ({"covariates": ("z",)}, {"control_group": "not_yet_treated"}):
        out = cc.analyze(frame, "z -> x; z -> y; x -> y", treatment="x", outcome="y", **over)
        assert out.kind == "refusal" and out.reason is AbstentionReason.INVALID_INPUT
        assert "difference_in_differences" in out.detail
