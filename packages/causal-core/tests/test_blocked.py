"""Blocked and paired designs (`block=`), the NO_VARIATION refusal, and a declared target
population. The gate is T11 (tests/t11); these pin the pieces against known answers.

Preconditions: a randomized design, a binary 0/1 treatment, a block column.
Postconditions taken from the contract and the T11 registration, not from the code:
  * pairs, continuous outcome: the point is the mean within-pair difference and the interval
    the paired t interval;
  * blocks: the point is the size-weighted mean of within-block differences;
  * binary pairs: the interval is the score interval for p10 - p01 with a continuity
    correction, and it excludes 0 only when the exact McNemar test rejects;
  * a constant treatment is NO_VARIATION, carrying the outcome's spread and what would
    resolve it; other failed design checks stay DESIGN_CHECK_FAILED;
  * a declared target population is stated on the result and never estimated for.
Undefined here (no assertions): the exact standard error reported for binary pairs, the
wording of warnings beyond the facts they must state.
"""

from __future__ import annotations

import math

import causal_core as cc
import numpy as np
import pandas as pd
import pytest
from causal_core.bench import t11
from causal_core.blocked import mcnemar_exact_p, paired_binary_interval, score_z
from causal_core.contract import AbstentionReason, CausalResult
from causal_core.report import report_is_clean
from scipy import optimize, stats


def _pairs(k: int, seed: int, tau: float = 0.3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    u = rng.normal(0, 1, k)
    y1 = 10 + u + tau + rng.normal(0, 0.5, k)
    y0 = 10 + u + rng.normal(0, 0.5, k)
    return pd.DataFrame({"pair": np.r_[np.arange(k), np.arange(k)],
                         "t": np.r_[np.ones(k), np.zeros(k)], "y": np.r_[y1, y0]})


def _blocked(frame: pd.DataFrame, **kw: object) -> object:
    return cc.analyze(frame, "t -> y", treatment="t", outcome="y", design="randomized",
                      block="pair", **kw)


# ── the S1 table (gnomon), exact arithmetic ───────────────────────────────────────────


def test_s1_paired_numbers_are_the_tables() -> None:
    out = t11.s1_analyse(cc.AnalysisPolicy(missing="drop"))
    assert isinstance(out, CausalResult) and out.blocks is not None and out.primary.ci
    bs = out.blocks
    assert (bs.n_blocks, bs.pairs, bs.incomplete_blocks, bs.units_left_out) == (30, True, 1, 1)
    assert (bs.discordant_treated, bs.discordant_control) == (0, 4)
    assert out.primary.point == pytest.approx(-4 / 30, abs=1e-15)
    assert bs.mcnemar_exact_p == pytest.approx(2 * 0.5**4, abs=1e-15)
    assert out.primary.ci.lo < 0 < out.primary.ci.hi  # the exact test does not reject
    assert out.primary.estimator == "paired_risk_difference"
    assert any("left out" in w for w in out.warnings)
    # what pairing bought: the unblocked analysis of the same 60 units is wider
    assert out.blocks.unblocked_se is not None
    plain = cc.analyze(t11.s1_frame().query("pair != 'task30#1'"), "delivered -> solved",
                       treatment="delivered", outcome="solved", design="randomized")
    assert isinstance(plain, CausalResult) and plain.primary.ci
    assert plain.primary.point == pytest.approx(-4 / 30)
    assert (out.primary.ci.hi - out.primary.ci.lo) < (plain.primary.ci.hi - plain.primary.ci.lo)


def test_an_incomplete_block_refuses_unless_dropping_is_declared() -> None:
    out = t11.s1_analyse(cc.AnalysisPolicy())
    assert isinstance(out, cc.Abstention)
    assert out.reason is AbstentionReason.DESIGN_CHECK_FAILED
    assert any("missing='drop'" in m for m in out.missing_information)


# ── the binary-pairs interval, against an independent implementation ──────────────────


def _profile_score(b: int, c: int, k: int, d0: float) -> float:
    """Tango's score statistic with the nuisance found numerically, not in closed form."""
    a_d = k - b - c

    def nll(p01: float) -> float:
        p10 = p01 + d0
        conc = 1 - p10 - p01
        if min(p01, p10, conc) <= 0:
            return math.inf
        return -(b * math.log(p10) + c * math.log(p01) + a_d * math.log(conc))

    lo_p = max(0.0, -d0) + 1e-12
    hi_p = (1 - d0) / 2 - 1e-12
    p01 = optimize.minimize_scalar(nll, bounds=(lo_p, hi_p), method="bounded",
                                   options={"xatol": 1e-12}).x
    var = k * (2 * p01 + d0 * (1 - d0))
    num = b - c - k * d0
    num = math.copysign(max(abs(num) - 1.0, 0.0), num)
    return num / math.sqrt(var)


@pytest.mark.parametrize(("b", "c", "k"), [(0, 4, 30), (3, 9, 40), (12, 5, 29), (7, 7, 50)])
def test_the_score_statistic_matches_a_numerical_profile_likelihood(b: int, c: int,
                                                                      k: int) -> None:
    for d0 in (-0.3, -0.1, 0.05, 0.2):
        if b + c > 0:
            assert score_z(b, c, k, d0) == pytest.approx(_profile_score(b, c, k, d0), abs=1e-4)


@pytest.mark.parametrize(("b", "c", "k"), [(0, 4, 30), (3, 9, 40), (12, 5, 29), (0, 0, 20)])
def test_the_interval_is_every_difference_the_test_does_not_reject(b: int, c: int,
                                                                     k: int) -> None:
    lo, hi = paired_binary_interval(b, c, k, 0.95)
    z = stats.norm.ppf(0.975)
    grid = np.linspace(-0.999, 0.999, 3997)
    accepted = [d for d in grid if abs(score_z(b, c, k, float(d))) <= z]
    assert lo == pytest.approx(min(accepted), abs=1e-3)
    assert hi == pytest.approx(max(accepted), abs=1e-3)


def test_at_zero_the_statistic_is_mcnemars_with_edwards_correction() -> None:
    for b, c in [(0, 4), (3, 9), (12, 5), (20, 31)]:
        expected = math.copysign(max(abs(b - c) - 1, 0), b - c) / math.sqrt(b + c)
        assert score_z(b, c, 60, 0.0) == pytest.approx(expected, abs=1e-12)


def test_the_interval_never_claims_what_the_exact_mcnemar_test_does_not() -> None:
    """Over every split of up to 80 discordant pairs: excludes 0 => exact p < 0.05."""
    for d in range(1, 81):
        for b in range(d + 1):
            lo, hi = paired_binary_interval(b, d - b, 100, 0.95)
            if lo > 0 or hi < 0:
                assert mcnemar_exact_p(b, d - b) < 0.05, (b, d - b)


# ── continuous pairs and blocks: the textbook answers ─────────────────────────────────


def test_continuous_pairs_give_the_paired_t_interval() -> None:
    frame = _pairs(40, seed=1)
    out = _blocked(frame)
    assert isinstance(out, CausalResult) and out.primary.ci and out.blocks
    d = (frame[frame.t == 1].set_index("pair").y - frame[frame.t == 0].set_index("pair").y)
    half = stats.t.ppf(0.975, 39) * d.std(ddof=1) / math.sqrt(40)
    assert out.primary.point == pytest.approx(d.mean(), abs=1e-12)
    assert out.primary.ci.lo == pytest.approx(d.mean() - half, abs=1e-10)
    assert out.primary.ci.hi == pytest.approx(d.mean() + half, abs=1e-10)
    assert out.blocks.sd_difference == pytest.approx(d.std(ddof=1), abs=1e-12)
    assert out.status is cc.ResultStatus.supported and report_is_clean(out)


def test_blocks_are_weighted_by_size_not_by_their_treated_share() -> None:
    rows = []
    # block 0: 4 units, 1 treated; block 1: 8 units, 4 treated; outcomes set by hand
    for blk, ys, ts in [(0, [5, 1, 1, 1], [1, 0, 0, 0]), (1, [3, 3, 3, 3, 1, 1, 1, 1],
                                                          [1, 1, 1, 1, 0, 0, 0, 0])]:
        rows += [(blk, t, y) for y, t in zip(ys, ts, strict=True)]
    frame = pd.concat([pd.DataFrame(rows, columns=["pair", "t", "y"]).assign(
        pair=lambda f, r=r: f.pair + 2 * r) for r in range(6)], ignore_index=True)
    out = _blocked(frame, policy=cc.AnalysisPolicy(min_n=10))
    assert isinstance(out, CausalResult)
    # within-block differences 4 and 2, block sizes 4 and 8: (4*4 + 8*2) / 12
    assert out.primary.point == pytest.approx((4 * 4 + 8 * 2) / 12, abs=1e-12)


# ── refusals ──────────────────────────────────────────────────────────────────────────


def test_a_treatment_that_never_varies_is_no_variation_with_what_would_resolve_it() -> None:
    frame = _pairs(40, seed=2).assign(t=0.0)
    for out in (_blocked(frame), cc.analyze(frame, "t -> y", treatment="t", outcome="y")):
        assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.NO_VARIATION
        assert any("experiment" in m for m in out.missing_information)
        assert out.diagnostics is not None
        assert out.diagnostics.outcome_sd == pytest.approx(frame.y.std(ddof=1))
        assert out.diagnostics.n_control == 80 and out.diagnostics.n_treated == 0


def test_a_never_varied_lever_under_backdoor_adjustment_is_no_variation() -> None:
    rng = np.random.default_rng(3)
    z = rng.normal(size=300)
    frame = pd.DataFrame({"z": z, "replay": 0, "solved": (rng.random(300) < 0.4).astype(int)})
    out = cc.analyze(frame, "replay -> solved; z -> solved; z -> replay", treatment="replay",
                     outcome="solved")
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.NO_VARIATION
    assert out.diagnostics and out.diagnostics.outcome_sd
    assert out.diagnostics.checks[0].name == "treatment varies"
    assert not out.diagnostics.checks[0].passed


def test_other_failed_design_checks_stay_design_check_failed() -> None:
    frame = _pairs(200, seed=4).drop(columns="pair")
    srm = cc.analyze(frame, "t -> y", treatment="t", outcome="y", design="randomized",
                     expected_treated_share=0.3)
    assert isinstance(srm, cc.Abstention)
    assert srm.reason is AbstentionReason.DESIGN_CHECK_FAILED
    flat = frame.assign(y=1.0)
    out = cc.analyze(flat, "t -> y", treatment="t", outcome="y", design="randomized")
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.DESIGN_CHECK_FAILED


@pytest.mark.parametrize("extra", [{"design": "observational"}, {"cluster": "pair"},
                                   {"estimand": "att"}, {"segment_by": ("pair",)}])
def test_what_this_version_does_not_combine_with_blocks_is_refused(extra: dict[str, object]
                                                                    ) -> None:
    kw: dict[str, object] = {"design": "randomized", **extra}
    out = cc.analyze(_pairs(40, seed=5), "t -> y", treatment="t", outcome="y", block="pair",
                     **kw)
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.INVALID_INPUT


def test_too_few_blocks_is_low_n() -> None:
    frame = pd.concat([_pairs(8, seed=6), _pairs(8, seed=7).assign(pair=lambda f: f.pair)])
    out = _blocked(frame)
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.LOW_N


def test_a_negative_control_that_moves_contests_the_result() -> None:
    frame = _pairs(60, seed=8)
    frame["placebo"] = frame["y"] * 0.0 + np.random.default_rng(9).normal(size=len(frame))
    frame["leaky"] = frame["t"] * 2.0 + np.random.default_rng(10).normal(0, 0.1, len(frame))
    clean = _blocked(frame, negative_control_outcomes=("placebo",))
    leaky = _blocked(frame, negative_control_outcomes=("leaky",))
    assert isinstance(clean, CausalResult) and clean.status is cc.ResultStatus.supported
    assert isinstance(leaky, CausalResult) and leaky.status is cc.ResultStatus.contested


# ── a declared target population ──────────────────────────────────────────────────────


def test_a_declared_target_population_is_stated_and_not_estimated_for() -> None:
    frame = _pairs(40, seed=11)
    base = _blocked(frame, population="local lane")
    moved = _blocked(frame, population="local lane", target_population="hosted lane")
    same = _blocked(frame, population="local lane", target_population="Local Lane ")
    assert isinstance(base, CausalResult) and isinstance(moved, CausalResult)
    assert isinstance(same, CausalResult)
    assert moved.primary == base.primary  # nothing is transported
    note = [w for w in moved.warnings if w.startswith("not transported")]
    assert len(note) == 1 and "local lane" in note[0] and "hosted lane" in note[0]
    assert not any(w.startswith("not transported") for w in (*base.warnings, *same.warnings))
    assert moved.result_digest != base.result_digest
    assert "not transported" in cc.report(moved)
