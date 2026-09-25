# T5 difference-in-differences gate — pre-registration

**Suite:** T5, staggered-adoption difference-in-differences on simulated panels with known effects
**System under test:** `packages/causal-core` (`causal_core.panel`, reached through `analyze(...,
design="difference_in_differences")`)
**Registered:** 2026-09-25, version 1; versions 2 and 3 the same day (see Changelog)
**Runs:** every PR, via `pytest -m t5` in `packages/causal-core`

## Status of this document

Written and committed before `causal_core.panel` exists. No bar below was chosen by looking at
output from the code under test.

## What this gate is for

Operations rarely get to randomize a rollout. They ship a change to some services this week and
others next month, and ask afterwards what it did. That is staggered adoption, and the estimator
most people reach for — a two-way fixed-effects regression of the outcome on unit and period
dummies and a treatment indicator — is known to be wrong there: when effects differ across
cohorts or grow over time, it compares late adopters against early adopters who are already
treated, and can report the wrong sign (Goodman-Bacon 2021; de Chaisemartin & D'Haultfœuille
2020; Callaway & Sant'Anna 2021).

The kernel estimates group-time average effects `ATT(g, t)` — cohort `g` (the period a unit is
first treated) against units never treated, each from the change since period `g − 1` — and
aggregates them into an overall ATT and an event study. Inference comes from each unit's
influence function, so it is clustered by unit by construction and accounts for the weights being
estimated. The identifying assumptions, parallel trends and no anticipation, cannot be written in
a causal graph, so the certificate is **tier C**, and the decision layer treats it accordingly.

This gate establishes that the numbers are right where they can be checked, that the estimator
refuses where the design cannot answer, and that a pre-trends test fires when trends are not
parallel — while saying plainly that passing a pre-trends test does not establish parallel trends.

## Design

**Panels.** `N = 400` units observed in periods `1..8`. Each replication draws every unit's cohort
independently with the probabilities given by the table's sizes (so `4: 80, …, never: 160` means
0.2, 0.2, 0.2, 0.4), matching the estimator's inference, which treats units as sampled and cohort
shares as estimated. (v1 fixed the sizes; see the Changelog.) The outcome is

`Y_it = α_i + λ_t + τ(g_i, t)·1[t ≥ g_i] + ε_it`,

with unit effects `α_i ~ N(c_g, 1)` whose mean `c_g` differs by cohort (DiD allows selection on
levels), period effects `λ_t ~ N(0, 1)` drawn once per replication, and `ε_it` AR(1) within unit
with `ρ = 0.5` and unit variance (so an analysis that treats a unit's periods as independent is
wrong about its uncertainty).

| design | cohorts (first treated period: units) | effect `τ(g, t)` | what it tests |
|---|---|---|---|
| `staggered_dynamic` | 4: 80, 5: 80, 6: 80, never: 160 | `δ_g · (1 + 0.5·(t − g))`, `δ = 1, 2, 3` | heterogeneous, growing effects: two-way FE is biased here |
| `staggered_constant` | 4: 80, 5: 80, 6: 80, never: 160 | 2 | homogeneous effect |
| `single_cohort` | 5: 200, never: 200 | 1.5 | the 2×2-like case |
| `null_effect` | 4: 80, 5: 80, 6: 80, never: 160 | 0 | no effect: coverage of zero |
| `trends_violated` | 4: 80, 5: 80, 6: 80, never: 160 | 2, plus a cohort-specific linear trend `0.3·(t − 1)` for every treated cohort | parallel trends fails |

**Truth.** The overall ATT is `Σ_{g, t ≥ g} π_g · τ(g, t) / Σ_{g, t ≥ g} π_g` over observed
post-treatment cells, with `π_g` the population cohort probabilities — the same simple aggregation the estimator targets. The event-study truth
`θ(e)` for `e ≥ 0` is the cohort-size-weighted mean of `τ(g, g + e)` over cohorts with `g + e ≤ 8`.
Pre-period truths are 0 under parallel trends.

**Replications.** 500 per design, seeded from `seed` below.

**Refusals** (one constructed panel each; the kernel must refuse with the named reason):

| case | panel | reason |
|---|---|---|
| `unbalanced` | 5% of unit-periods deleted at random | `DESIGN_CHECK_FAILED` |
| `treatment_turns_off` | 20 units treated in periods 4–5 and untreated after | `DESIGN_CHECK_FAILED` |
| `no_never_treated` | every unit eventually treated | `DESIGN_CHECK_FAILED` |
| `treated_from_the_start` | one cohort treated in period 1, no other cohort | `DESIGN_CHECK_FAILED` |
| `tiny_cohort` | a cohort of 3 units beside the rest | `LOW_N` |

**Sensitivity to parallel-trends violations (v3).** Beside the point estimate, the kernel reports
intervals valid under the restriction that the post-adoption bias of the overall ATT is at most
`M̄` times the largest pre-adoption deviation from the base period — a simplified form of the
"relative magnitudes" restriction of Rambachan & Roth (2023), stated on event-time deviations.
For each `M̄` in a grid the interval is

`θ̂ ± ( M̄ · U + z_{1−α₂/2} · se(θ̂) )`,

where `U` is an upper confidence bound on the largest absolute pre-adoption event-time effect
(Bonferroni across the pre-periods, at level `α₁`), and `α₁ + α₂ = α`. By a union bound the
interval covers the ATT with probability at least `1 − α` whenever the restriction holds. The
**breakdown** `M̄*` is the largest `M̄` at which the interval still excludes zero.

In `trends_violated` the ratio the restriction must accommodate is known from the design: the
aggregate post-adoption bias is 0.775 and the largest pre-adoption deviation (event time −5,
cohort 6 only) is 1.2, so `M̄_true = 0.646`. The interval at the smallest grid point at or above
`M̄_true` must cover. Under parallel trends (`M̄_true = 0`) every grid point must.

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `twfe_static` | the primary estimate is the two-way fixed-effects coefficient | bias bar, `staggered_dynamic` |
| `forbidden_comparisons` | already-treated units are used as controls | bias bar, `staggered_dynamic` |
| `cells_independent` | the aggregate's variance ignores the covariance between cells of the same unit | coverage bar, `staggered_dynamic` |
| `pretrend_blind` | the pre-trends test always passes | power bar, `trends_violated` |
| `last_pre_period_only` | `U` is bounded from event time −2 alone, the period next to the base (v3) | sensitivity coverage, `trends_violated` |

## Pass bars

```toml
[meta]
suite = "T5"
version = 3
registered = "2026-09-25"

[panels]
units = 400
periods = 8
reps = 500
seed = 20260925
ar1_rho = 0.5
cohort_membership = "drawn"    # v1: "fixed" (sizes exactly as tabled)

[estimation]
designs = ["staggered_dynamic", "staggered_constant", "single_cohort", "null_effect"]
bias_mcse_max = 3.0            # |mean estimate - truth| <= 3 Monte Carlo SEs, per design
coverage_band = [0.925, 0.975] # nominal 95% interval coverage of the overall ATT, per design
event_coverage_band = [0.925, 0.975]  # pooled over post-period event times, per design

[pretrends]
alpha = 0.01                   # the kernel's refutation_alpha default
false_alarm_max = 0.03         # rejection rate in each design where trends are parallel
power_min = 0.80               # rejection rate in `trends_violated`

[sensitivity]
grid = [0.0, 0.5, 1.0, 1.5, 2.0]
alpha_pre = 0.025              # α₁, for the bound on pre-adoption deviations
alpha_effect = 0.025           # α₂, for the estimate; α₁ + α₂ = 0.05
m_true = { staggered_dynamic = 0.0, staggered_constant = 0.0, single_cohort = 0.0, null_effect = 0.0, trends_violated = 0.646 }
coverage_min = 0.93            # at the smallest grid point >= m_true, per design

[refusals]
must_refuse = ["unbalanced", "treatment_turns_off", "no_never_treated",
               "treated_from_the_start", "tiny_cohort"]

[seeded_defects]
required = ["twfe_static", "forbidden_comparisons", "cells_independent", "pretrend_blind",
            "last_pre_period_only"]
```

**Why these bars.** With 500 replications a coverage estimate near 0.95 has a standard error near
0.0097, so the band is about ±2.6 standard errors. The bias bar scales with Monte Carlo error, as
T0's does. The pre-trends false-alarm bar allows three times the nominal 1% for finite-sample
deviation; the power bar is a claim that a violation this large (0.3 per period, against noise of
unit variance, over three pre-periods for the latest cohort) is not missed.

**What this gate does not establish.**

- **That parallel trends hold anywhere real.** A pre-trends test that passes is weak evidence
  (Roth 2022): it has limited power against violations that matter, and conditioning on passing
  it biases what follows. The kernel reports it as a falsification test, and a failure makes the
  result `contested`; a pass does not make the assumption true, which is why the tier is C.
- Covariate-conditional parallel trends, not-yet-treated comparison groups, anticipation,
  continuous or reversible treatments, and repeated cross-sections. Each is refused or not
  offered in v1.
- The full Rambachan & Roth machinery: their restriction is on consecutive changes, not levels,
  and their confidence sets are sharper than a union bound. v3's intervals are valid and
  conservative, not optimal.

## Changelog

- v1 (2026-09-25): initial registration.
- **v2 (2026-09-25): cohort membership is drawn, not fixed. No bar changed.** v1 run 1
  (`results/2026-09-25-v1-run1.json`) failed: `staggered_dynamic` covered 0.980 overall and 0.979
  across event times, over the 0.975 ceiling; every other design, every refusal and every seeded
  defect passed. The estimator's intervals come from influence functions that include the
  uncertainty of the estimated cohort shares — the right inference when units are sampled and
  cohort composition is random, which is the situation on real data. v1's panels fixed the sizes,
  so the gate asked about a composition-conditional estimand the intervals are not for, and the
  share term, proportional to how much cell effects vary around their average, over-covered in the
  one design where they vary. On a development seed (not this document's) the same estimator
  covers 0.948 with drawn membership and 0.975 with fixed sizes, SE 0.113 against an SD of 0.109
  and 0.093 respectively.

  The alternatives were rejected: widening the band would accept a real over-coverage the gate
  exists to catch; dropping the share term from the estimator would make intervals too narrow on
  real data whenever composition is itself random. Disclosed: this change was chosen after seeing
  v1's result. It changes what the simulation draws, not what any bar requires; the refusal panels
  keep their fixed sizes, since a "tiny cohort of 3" must be exactly 3.
- **v3 (2026-09-25): sensitivity to parallel-trends violations. No v2 bar changed.** Adds the
  `[sensitivity]` section, its coverage bar, and the `last_pre_period_only` defect — the first of
  the extensions the register's `did-extensions` entry names. Written before the sensitivity code
  exists. `m_true` for `trends_violated` is computed from the design (bias 0.775, largest
  pre-period deviation 1.2), not observed; the v2 run's measured bias of 0.770 agrees with it.
