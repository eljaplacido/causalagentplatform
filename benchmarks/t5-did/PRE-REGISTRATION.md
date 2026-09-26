# T5 difference-in-differences gate — pre-registration

**Suite:** T5, staggered-adoption difference-in-differences on simulated panels with known effects
**System under test:** `packages/causal-core` (`causal_core.panel`, reached through `analyze(...,
design="difference_in_differences")`)
**Registered:** 2026-09-25, version 1; versions 2 and 3 the same day; version 4 on 2026-09-26
(see Changelog)
**Runs:** every PR, via `pytest -m t5` in `packages/causal-core`

## Status of this document

Written and committed before `causal_core.panel` exists. No bar below was chosen by looking at
output from the code under test. Version 4's additions (covariates, not-yet-treated comparisons)
were written and committed before the code that implements them existed; the population biases
quoted for its designs come from a large-sample calculation on the designs themselves, with its
own logistic and least-squares fits, not from `causal_core.panel`.

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
| `poor_overlap` (v4) | cohorts 4, 5, 6 with scores `−0.693 + 4·x`, drawn; `covariates = [x]` | `POSITIVITY` |
| `time_varying_covariate` (v4) | `covariate_selection`'s panel with `x_it = x_i + N(0, 0.5²)` each period | `INVALID_INPUT` |
| `no_comparison_units` (v4) | every unit adopts in period 5; `control_group = not_yet_treated` | `DESIGN_CHECK_FAILED` |

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

**Covariates and not-yet-treated comparisons (v4).** Two extensions, after Callaway & Sant'Anna
(2021) and Sant'Anna & Zhao (2020), reached through two new `CausalQuery` fields: `covariates`
(time-invariant columns under which parallel trends is assumed to hold) and `control_group`
(`never_treated`, the default and v1–v3's behaviour, or `not_yet_treated`).

*The comparison group.* For cohort `g` at period `t` (base period `g − 1` throughout), the
never-treated comparison is the units never treated; the not-yet-treated comparison is the
never-treated plus every cohort `c ≠ g` with `c > max(t, g − 1)` — units untreated at both `t`
and the base. A cell with fewer than `min_per_arm` comparison units is not estimated and a
warning names it; a cohort with no estimable cell serves only as a comparison. With no estimable
post-adoption cell the analysis refuses.

*The estimator.* With covariates `X`, each cell is the doubly robust DiD of Sant'Anna & Zhao
(2020, the "traditional" panel estimator) on the units of cohort `g` and its comparison group,
`D = 1[G = g]`, `ΔY = Y_t − Y_{g−1}`:

`ATT(g, t) = E[w₁ (ΔY − μ₀(X))] − E[w₀ (ΔY − μ₀(X))]`, `w₁ = D / E[D]`,
`w₀ = (p(X)(1 − D)/(1 − p(X))) / E[p(X)(1 − D)/(1 − p(X))]`,

with `p(X)` a logistic regression of `D` on `(1, X)` by maximum likelihood and `μ₀(X)` a
least-squares regression of `ΔY` on `(1, X)` among the comparison units. It is consistent if
either model is right. Its influence function includes the estimation effects of both models
(their asymptotically linear representations), is scaled to the whole sample
(`N / n_gt` on the cell's units, zero elsewhere) and enters the existing aggregation, pre-trends
test and sensitivity bound unchanged. Without covariates the cell is v1–v3's difference of mean
changes, against whichever comparison group was asked for.

*Refusals the extensions add.* A covariate that is not numeric, missing anywhere, or not
constant within a unit is refused (`INVALID_INPUT`): this version supports time-invariant,
pre-treatment characteristics only. Overlap: in any estimated cell, if more than
`max_extreme_propensity_share` (0.05) of cohort `g`'s units have a fitted propensity above the
upper `propensity_bounds` (0.99), the analysis refuses (`POSITIVITY`) — for those units no
comparable untreated unit exists and the weights would rest on a handful of controls.
Covariates or `control_group = "not_yet_treated"` with any other design are refused
(`INVALID_INPUT`), never ignored.

*Designs (v4).* The v1–v3 panels above are unchanged. The new designs add a covariate `x ~ N(0, 1)`
(and, in one design, `w ~ Bernoulli(0.5)`), constant within unit, and draw each unit's cohort from
a multinomial logit whose scores are tabled (the never-treated score is 0; in the two
not-yet-treated designs there are no never-treated units and cohort 8's score is 0). The outcome is

`Y_it = α_i + λ_t + h(X_i)·(t − 1) + τ(g_i, t)·1[t ≥ g_i] + ε_it`,

with `α_i ~ N(0.3·g_i + 0.5·x_i, 1)` (0 in place of `0.3·g` for the never-treated), `λ_t` and
`ε_it` as before, and `h` the covariate-specific trend. Parallel trends holds conditional on `X`
and fails unconditionally wherever the cohorts differ in `X`. Effects: "dynamic" is
`δ_g · (1 + 0.5·(t − g))`.

| design | cohorts and selection scores | trend `h(X)` | effect | analysed with | what it tests |
|---|---|---|---|---|---|
| `covariate_selection` | 4, 5, 6: `−0.943 + b_g·x + 0.5·w`, `b = 0.5, 0.75, 1.0`; never | `0.3·x + 0.3·w` | dynamic, `δ = 1, 2, 3` | `covariates = [x, w]` | selection into timing on covariates that drive trends; the unconditional estimator is biased by 0.54 |
| `outcome_misspecified` | 4, 5, 6: `−0.693 + b_g·x`, `b = 0.5, 0.75, 1.0`; never | `0.25·(x² − 1)` | 2 | `covariates = [x]` | the outcome model (linear in `x`) is wrong, the propensity model right; regression adjustment alone is biased by 0.25 |
| `propensity_misspecified` | 4, 5, 6: `−0.693 + b_g·x + 0.5·(x² − 1)`, `b = 0.5, 0.75, 1.0`; never | `0.3·x` | 2 | `covariates = [x]` | the propensity model (logistic in `x`) is wrong, the outcome model right; weighting alone is biased by 0.08 |
| `not_yet_treated` | 3, 4, 5, 6, 8 with probability 0.2 each; no never-treated | 0 | dynamic, `δ = 1, 1.5, 2, 2.5, 3` | `control_group = not_yet_treated` | every unit is eventually treated; cells after period 7 have no comparison and are not estimated |
| `covariates_not_yet_treated` | 3, 4, 5, 6: `0.9·x, 0.6·x, 0.3·x, 0.15·x`; 8: 0; no never-treated | `0.3·x` | dynamic, as `not_yet_treated` | `covariates = [x]`, `control_group = not_yet_treated` | both at once; the comparison mixes later cohorts, so the logistic propensity model is only approximate and the outcome model carries the double robustness |

*Truth (v4).* Effects do not depend on `X`, so `ATT(g, t) = τ(g, t)`. The overall ATT and the
event-time truths are the same cohort-probability-weighted means as before, over the
post-adoption cells the design can estimate (for the two not-yet-treated designs, `g ≤ 6` and
`t ≤ 7`), with `π_g` the population cohort probabilities under the selection model: 80-point
Gauss–Hermite quadrature over `x`, exact over `w`.

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `twfe_static` | the primary estimate is the two-way fixed-effects coefficient | bias bar, `staggered_dynamic` |
| `forbidden_comparisons` | already-treated units are used as controls | bias bar, `staggered_dynamic` |
| `cells_independent` | the aggregate's variance ignores the covariance between cells of the same unit | coverage bar, `staggered_dynamic` |
| `pretrend_blind` | the pre-trends test always passes | power bar, `trends_violated` |
| `last_pre_period_only` | `U` is bounded from event time −2 alone, the period next to the base (v3) | sensitivity coverage, `trends_violated` |
| `ignore_covariates` | the covariates are dropped and the unconditional estimator runs (v4; population bias 0.54) | bias bar, `covariate_selection` |
| `not_yet_treated_includes_treated` | the not-yet-treated comparison is every cohort untreated at the base period, so it leaks units treated by `t` (v4; population bias −1.05) | bias bar, `not_yet_treated` |
| `propensity_ignored` | the propensity score is a constant, leaving regression adjustment alone (v4; population bias 0.25) | bias bar, `outcome_misspecified` |
| `outcome_regression_ignored` | the outcome model is zero, leaving normalised inverse-probability weighting alone (v4; population bias 0.08) | bias bar, `propensity_misspecified` |

## Pass bars

```toml
[meta]
suite = "T5"
version = 4
registered = "2026-09-26"

[panels]
units = 400
periods = 8
reps = 500
seed = 20260925
ar1_rho = 0.5
cohort_membership = "drawn"    # v1: "fixed" (sizes exactly as tabled)

[estimation]
designs = ["staggered_dynamic", "staggered_constant", "single_cohort", "null_effect",
           "covariate_selection", "outcome_misspecified", "propensity_misspecified",
           "not_yet_treated", "covariates_not_yet_treated"]   # the last five: v4
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
m_true = { staggered_dynamic = 0.0, staggered_constant = 0.0, single_cohort = 0.0, null_effect = 0.0, trends_violated = 0.646, covariate_selection = 0.0, outcome_misspecified = 0.0, propensity_misspecified = 0.0, not_yet_treated = 0.0, covariates_not_yet_treated = 0.0 }
coverage_min = 0.93            # at the smallest grid point >= m_true, per design

[refusals]
must_refuse = ["unbalanced", "treatment_turns_off", "no_never_treated",
               "treated_from_the_start", "tiny_cohort",
               "poor_overlap", "time_varying_covariate", "no_comparison_units"]

[extensions]                   # v4: how each new design is analysed
covariates = { covariate_selection = ["x", "w"], outcome_misspecified = ["x"], propensity_misspecified = ["x"], covariates_not_yet_treated = ["x"], poor_overlap = ["x"], time_varying_covariate = ["x", "w"] }
control_group = { not_yet_treated = "not_yet_treated", covariates_not_yet_treated = "not_yet_treated", no_comparison_units = "not_yet_treated" }
quadrature_nodes = 80

[seeded_defects]
required = ["twfe_static", "forbidden_comparisons", "cells_independent", "pretrend_blind",
            "last_pre_period_only", "ignore_covariates", "not_yet_treated_includes_treated",
            "propensity_ignored", "outcome_regression_ignored"]
```

**Why these bars.** With 500 replications a coverage estimate near 0.95 has a standard error near
0.0097, so the band is about ±2.6 standard errors. The bias bar scales with Monte Carlo error, as
T0's does. The pre-trends false-alarm bar allows three times the nominal 1% for finite-sample
deviation; the power bar is a claim that a violation this large (0.3 per period, against noise of
unit variance, over three pre-periods for the latest cohort) is not missed.

The v4 designs are held to the same bars as v1–v3's: an extension that needs a looser bar is not
yet an extension. Each v4 seeded defect has a population bias (from the design, above) that is
large against the bias bar: the v3 designs' Monte Carlo SEs were 0.004–0.005, and the covariate
designs carry more outcome variance, so allowing an MCSE up to about 0.01 the bar sits near 0.03.
The smallest defect bias, 0.08 (`outcome_regression_ignored`), is about eight such MCSEs; the
others are 25 or more. The v4 designs keep the default overlap policy and none comes near its
refusal: in the large-sample calculation the share of treated units with a fitted propensity
above 0.99 is under 0.001% in every design, and 18% in `poor_overlap`.

**What this gate does not establish.**

- **That parallel trends hold anywhere real.** A pre-trends test that passes is weak evidence
  (Roth 2022): it has limited power against violations that matter, and conditioning on passing
  it biases what follows. The kernel reports it as a falsification test, and a failure makes the
  result `contested`; a pass does not make the assumption true, which is why the tier is C.
- Anticipation, continuous or reversible treatments, repeated cross-sections, and time-varying
  covariates. Each is refused or not offered.
- (v4) That conditional parallel trends hold given the covariates supplied, or that those are the
  right covariates: the gate plants the conditioning set it asks for. Double robustness is
  checked against one misspecification of each model, not every one, with linear-index models
  only (logistic propensity, linear outcome regression); nothing here says which of the two is
  right on real data. No seeded defect targets the nuisance-estimation terms of the doubly robust
  influence function on their own; they are exercised only through the coverage bars of the
  misspecified designs, where they matter. Not-yet-treated comparisons additionally assume no
  anticipation among the units that serve as comparisons before their own adoption.
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
- **v4 (2026-09-26): covariate-conditional parallel trends (doubly robust) and not-yet-treated
  comparisons. No v3 bar changed.** Adds five designs to `[estimation]` and `[sensitivity]`, three
  refusals, the `[extensions]` table that says how each is analysed, and four seeded defects —
  the next two extensions the register's `did-extensions` entry names. Written and committed
  before the code that implements them. The selection models were sized with a large-sample
  calculation on the designs (a scratch script with its own logistic and least-squares fits,
  independent of `causal_core.panel`) so that each defect's population bias is large against the
  bias bar and no non-refusal design approaches the overlap refusal; the figures quoted above are
  that calculation's. The v1–v3 designs keep their per-design random streams, so their figures
  should reproduce exactly.
