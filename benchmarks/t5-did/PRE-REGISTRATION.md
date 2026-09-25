# T5 difference-in-differences gate — pre-registration

**Suite:** T5, staggered-adoption difference-in-differences on simulated panels with known effects
**System under test:** `packages/causal-core` (`causal_core.panel`, reached through `analyze(...,
design="difference_in_differences")`)
**Registered:** 2026-09-25, version 1
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

**Panels.** `N = 400` units observed in periods `1..8`. Cohort sizes are fixed within a design
(not drawn), so the true aggregate effect is exact. The outcome is

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

**Truth.** The overall ATT is `Σ_{g, t ≥ g} n_g · τ(g, t) / Σ_{g, t ≥ g} n_g` over observed
post-treatment cells — the same simple aggregation the estimator targets. The event-study truth
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

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `twfe_static` | the primary estimate is the two-way fixed-effects coefficient | bias bar, `staggered_dynamic` |
| `forbidden_comparisons` | already-treated units are used as controls | bias bar, `staggered_dynamic` |
| `cells_independent` | the aggregate's variance ignores the covariance between cells of the same unit | coverage bar, `staggered_dynamic` |
| `pretrend_blind` | the pre-trends test always passes | power bar, `trends_violated` |

## Pass bars

```toml
[meta]
suite = "T5"
version = 1
registered = "2026-09-25"

[panels]
units = 400
periods = 8
reps = 500
seed = 20260925
ar1_rho = 0.5

[estimation]
designs = ["staggered_dynamic", "staggered_constant", "single_cohort", "null_effect"]
bias_mcse_max = 3.0            # |mean estimate - truth| <= 3 Monte Carlo SEs, per design
coverage_band = [0.925, 0.975] # nominal 95% interval coverage of the overall ATT, per design
event_coverage_band = [0.925, 0.975]  # pooled over post-period event times, per design

[pretrends]
alpha = 0.01                   # the kernel's refutation_alpha default
false_alarm_max = 0.03         # rejection rate in each design where trends are parallel
power_min = 0.80               # rejection rate in `trends_violated`

[refusals]
must_refuse = ["unbalanced", "treatment_turns_off", "no_never_treated",
               "treated_from_the_start", "tiny_cohort"]

[seeded_defects]
required = ["twfe_static", "forbidden_comparisons", "cells_independent", "pretrend_blind"]
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
- Sensitivity to violations of parallel trends (e.g. Rambachan & Roth bounds). Not implemented.

## Changelog

- v1 (2026-09-25): initial registration.
