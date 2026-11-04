"""
The three gaps the gnomon analysis found, in the decision layer
(gnomon benchmarks/results/causal-2026-11/REPORT.md, "Tool findings").

1. A treatment that never varied (NO_VARIATION) gets a sized experiment; a failed design
   check still refuses.
2. Paired evidence gets a paired experiment, sized from what the pairs showed, and the size
   is right: the sized trial has the stated power under the kernel's own analysis.
3. Evidence from one population is not treated as evidence about another.

Preconditions: outcomes from `causal_core.analyze` on simulated data whose truth is set.
Postconditions, from the contract and D0 v5 (not from the code): the verdicts registered
there; a paired trial of K pairs reaches `policy.power` and K - 1 does not; "not
transported" is said whenever the evidence's population differs from the target.
Undefined (no assertions): the wording of notes beyond the facts they must carry.
"""

from __future__ import annotations

import math

import causal_core as cc
import numpy as np
import pandas as pd
import pytest
from causal_core.bench import t11
from causal_core.blocked import paired_binary_interval, paired_binary_pairs, paired_binary_power
from causal_core.contract import AbstentionReason, CausalResult
from causal_decision import DecisionPolicy, DecisionRequest, RiskClass, Verdict, decide, explain
from causal_decision.bench.d0 import BASE, sample_ratio_mismatch, strong_effect
from scipy import stats

GNOMON = DecisionRequest(
    action="stop delivering the idle nudge", outcome="solved", direction="increase",
    risk_class=RiskClass.medium, reversible=True, minimum_worthwhile_effect=0.10,
    harm_threshold=0.0, can_randomize=True, randomization_unit="(task, attempt) cell",
    units_per_day=48,
)


# ── 1. a lever nobody has tried ───────────────────────────────────────────────────────


def _never_tried(n: int = 81, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    z = rng.normal(size=n)
    return pd.DataFrame({"difficulty": z, "replay": 0.0,
                         "solved": (rng.random(n) < 0.35).astype(float)})


def test_a_never_varied_lever_gets_a_sized_experiment() -> None:
    frame = _never_tried()
    out = cc.analyze(frame, "replay -> solved; difficulty -> solved; difficulty -> replay",
                     treatment="replay", outcome="solved")
    assert isinstance(out, cc.Abstention) and out.reason is AbstentionReason.NO_VARIATION
    d = decide(out, GNOMON)
    assert d.verdict is Verdict.experiment
    spec = d.experiment
    assert spec is not None and spec.design == "randomized_units"
    sd = frame.solved.std(ddof=1)
    z = stats.norm.ppf(0.975) + stats.norm.ppf(0.8)
    assert spec.units_per_arm == math.ceil(2 * z**2 * sd**2 / 0.10**2)
    assert spec.estimated_days == round(2 * spec.units_per_arm / 48, 1)
    assert "never varied" in spec.note
    explain(d, out)


def test_a_never_varied_lever_that_cannot_be_tried_is_refused() -> None:
    out = cc.analyze(_never_tried(), "replay -> solved", treatment="replay", outcome="solved")
    d = decide(out, GNOMON.model_copy(update={"can_randomize": False}))
    assert d.verdict is Verdict.refuse and d.experiment is None
    assert decide(out, GNOMON, DecisionPolicy(experiments_available=False)).verdict \
        is Verdict.refuse


def test_a_failed_design_check_still_refuses() -> None:
    out = sample_ratio_mismatch()
    assert isinstance(out, cc.Abstention)
    assert out.reason is AbstentionReason.DESIGN_CHECK_FAILED
    d = decide(out, BASE)
    assert d.verdict is Verdict.refuse
    assert "an experiment would not resolve this" in d.checks[0].detail


# ── 2. paired evidence, paired experiment ─────────────────────────────────────────────


@pytest.mark.parametrize(("k", "p_benefit", "p_harm"), [(30, 0.1167, 0.0167), (40, 0.05, 0.0),
                                                        (25, 0.2, 0.15)])
def test_the_exact_power_is_the_kernels_own_rejection_rate(k: int, p_benefit: float,
                                                           p_harm: float) -> None:
    """Enumerate every (b, c) table, run the kernel's interval on it, add up the tables whose
    interval lies wholly above zero: that is the power, and the closed form must equal it."""
    brute = 0.0
    for b in range(k + 1):
        for c in range(k + 1 - b):
            pr = stats.multinomial.pmf([b, c, k - b - c], k, [p_benefit, p_harm,
                                                              1 - p_benefit - p_harm])
            if pr > 0 and paired_binary_interval(b, c, k, 0.95)[0] > 0:
                brute += pr
    assert paired_binary_power(k, p_benefit, p_harm, 0.05) == pytest.approx(brute, abs=1e-9)


def test_the_sized_number_of_pairs_is_the_first_with_the_power() -> None:
    for disc, effect in [(4 / 30, 0.10), (12 / 29, 0.10), (0.2, 0.05)]:
        k = paired_binary_pairs(disc, effect, 0.05, 0.8)
        assert k is not None
        pb, ph = (disc + effect) / 2, (disc - effect) / 2
        assert paired_binary_power(k, pb, ph, 0.05) >= 0.8 > paired_binary_power(k - 1, pb, ph,
                                                                                    0.05)


def _s1_withheld() -> CausalResult:
    """The S1 pairs with the treatment flipped to `withheld`, the action D-N asks about."""
    frame = t11.s1_frame().assign(withheld=lambda f: 1.0 - f.delivered)
    out = cc.analyze(frame, "withheld -> solved", treatment="withheld", outcome="solved",
                     design="randomized", block="pair", policy=cc.AnalysisPolicy(missing="drop"))
    assert isinstance(out, CausalResult)
    return out


def test_s1_is_sized_as_matched_pairs_from_its_discordance() -> None:
    out = _s1_withheld()
    d = decide(out, GNOMON)
    assert d.verdict is Verdict.experiment
    spec = d.experiment
    assert spec is not None and spec.design == "paired_units"
    assert spec.discordance == pytest.approx(4 / 30)
    assert spec.pairs == paired_binary_pairs(4 / 30, 0.10, 0.05, 0.8)
    assert spec.units_per_arm == spec.pairs
    assert spec.estimated_days == round(2 * spec.pairs / 48, 1)
    unpaired = math.ceil(2 * (stats.norm.ppf(0.975) + stats.norm.ppf(0.8)) ** 2
                         * out.diagnostics.outcome_sd**2 / 0.10**2)  # type: ignore[operator]
    assert spec.pairs * 2 < unpaired * 2, "pairing must cost fewer cells here"
    assert f"{unpaired} per arm" in spec.note
    assert "matched pairs" in explain(d, out)


def _simulated_power(k: int, sample: object, reps: int, seed: int) -> float:
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        frame = sample(k, rng)  # type: ignore[operator]
        out = cc.analyze(frame, "t -> y", treatment="t", outcome="y", design="randomized",
                         block="pair", policy=cc.AnalysisPolicy(sensitivity=False))
        assert isinstance(out, CausalResult) and out.primary.ci is not None
        hits += out.primary.ci.lo > 0
    return hits / reps


def test_a_sized_binary_paired_trial_has_its_power_through_the_kernel() -> None:
    disc, effect = 4 / 30, 0.10
    k = paired_binary_pairs(disc, effect, 0.05, 0.8)
    assert k is not None
    pb, ph = (disc + effect) / 2, (disc - effect) / 2

    def sample(k: int, rng: np.random.Generator) -> pd.DataFrame:
        cell = rng.choice(3, size=k, p=[pb, ph, 1 - pb - ph])
        y1 = np.where(cell == 0, 1.0, np.where(cell == 1, 0.0, rng.integers(0, 2, k)))
        y0 = np.where(cell == 0, 0.0, np.where(cell == 1, 1.0, y1))
        return pd.DataFrame({"pair": np.r_[np.arange(k), np.arange(k)],
                             "t": np.r_[np.ones(k), np.zeros(k)], "y": np.r_[y1, y0]})

    power = _simulated_power(k, sample, reps=400, seed=20261104)
    # 400 replications: Monte Carlo SE ~0.02 around the exact 0.80
    assert 0.74 <= power <= 0.87, power


def test_a_sized_continuous_paired_trial_has_its_power_through_the_kernel() -> None:
    rng = np.random.default_rng(1)
    k0 = 60
    base = rng.normal(0, 3, k0)
    frame = pd.DataFrame({"pair": np.r_[np.arange(k0), np.arange(k0)],
                          "t": np.r_[np.ones(k0), np.zeros(k0)],
                          "y": np.r_[base + 0.2 + rng.normal(0, 1, k0),
                                     base + rng.normal(0, 1, k0)]})
    out = cc.analyze(frame, "t -> y", treatment="t", outcome="y", design="randomized",
                     block="pair")
    assert isinstance(out, CausalResult) and out.blocks is not None
    req = GNOMON.model_copy(update={"outcome": "y", "minimum_worthwhile_effect": 0.5})
    spec = decide(out, req).experiment
    assert spec is not None and spec.design == "paired_units" and spec.pairs
    sd_d = out.blocks.sd_difference
    assert spec.sd_difference == pytest.approx(sd_d)

    def sample(k: int, rng: np.random.Generator) -> pd.DataFrame:
        b = rng.normal(0, 3, k)
        d = rng.normal(0.5, sd_d, k)  # within-pair differences with the evidence's spread
        return pd.DataFrame({"pair": np.r_[np.arange(k), np.arange(k)],
                             "t": np.r_[np.ones(k), np.zeros(k)], "y": np.r_[b + d, b]})

    power = _simulated_power(spec.pairs, sample, reps=400, seed=7)
    assert 0.74 <= power <= 0.87, power


# ── 3. not transported ────────────────────────────────────────────────────────────────

LOCAL = "qwen3.6 on the local lane (llama.cpp, 900 s cap)"
HOSTED = "deepseek-v4.1-flash on the hosted lane"


def _local(**kw: object) -> CausalResult:
    rng = np.random.default_rng(3)
    n = 2000
    t = (rng.random(n) < 0.5).astype(float)
    frame = pd.DataFrame({"t": t, "solved": (rng.random(n) < 0.3 + 0.25 * t).astype(float)})
    out = cc.analyze(frame, "t -> solved", treatment="t", outcome="solved",
                     design="randomized", population=LOCAL, **kw)
    assert isinstance(out, CausalResult)
    return out


def test_evidence_from_another_population_is_not_transported() -> None:
    out = _local()
    req = GNOMON.model_copy(update={"population": HOSTED, "risk_class": RiskClass.low})
    d = decide(out, req)
    assert d.verdict is Verdict.experiment
    check = next(c for c in d.checks if c.name.startswith("the evidence is about"))
    assert not check.passed and check.allows is Verdict.experiment
    assert check.detail.startswith(f"not transported; evidence is from {LOCAL}")
    assert "Not transported" in d.rationale
    assert d.experiment is not None and d.experiment.population == HOSTED
    assert HOSTED in d.experiment.question
    explain(d, out)  # population labels carry digits; they are not claims


def test_a_declared_target_population_caps_without_a_request_population() -> None:
    out = _local(target_population=HOSTED)
    d = decide(out, GNOMON.model_copy(update={"risk_class": RiskClass.low}))
    assert d.verdict is Verdict.experiment
    assert d.evidence.population == LOCAL and d.evidence.target_population == HOSTED


def test_the_same_population_caps_nothing_and_an_unstated_one_goes_to_a_person() -> None:
    low = GNOMON.model_copy(update={"risk_class": RiskClass.low})
    same = decide(_local(), low.model_copy(update={"population": LOCAL}))
    unstated = decide(strong_effect(), BASE.model_copy(update={"population": HOSTED}))
    plain = decide(_local(), low)
    assert same.verdict is plain.verdict
    assert unstated.verdict is Verdict.act_with_approval


def test_records_without_the_new_fields_serialize_as_before() -> None:
    d = decide(strong_effect(), BASE.model_copy(update={"minimum_worthwhile_effect": 9.0}))
    dumped = d.model_dump(mode="json")
    for late in ("population", "target_population", "paired"):
        assert late not in dumped["evidence"]
    if dumped["experiment"] is not None:
        for late in ("pairs", "discordance", "sd_difference", "population"):
            assert late not in dumped["experiment"]
