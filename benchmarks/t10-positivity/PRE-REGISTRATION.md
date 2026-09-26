# T10 positivity and overlap gate — pre-registration

**Suite:** T10, observational designs whose positivity structure is set by construction
**System under test:** `packages/causal-core` (`causal_core.design`'s overlap checks, reached through
`analyze(...)` with a binary treatment and a back-door adjustment set)
**Registered:** 2026-09-26, version 1
**Runs:** every PR, via `pytest -m t10` in `packages/causal-core`

## Status of this document

Written and committed before the checks it gates exist in the kernel (register entry
`kernel-overlap-detection-limits`; ledger septa-34). No bar below was chosen by looking at output
from the code under test.

Disclosed, because it shaped the designs and the bars: before registration the proposed region
check was prototyped **outside the kernel** (a scratch script with its own tree; it borrows only the
kernel's logistic-regression helper) and run on development seeds 4040, 5050, 6060 and 7070 — never
on this document's seed. Those figures are quoted below where they justify a bar. The kernel as it
stands (contract 0.11.0) was run on development seed 4040 only to time an analysis and to check that
its existing estimators cover in four of the accepted designs (200 replications each: 0.925–0.96);
the estimators are not changed by this work and the coverage band below is the one T8 uses, not one
fitted to those numbers.

## What this gate is for

The ATE of a binary treatment is identified by back-door adjustment only where every stratum of the
adjustment set has a chance of each arm — positivity. Where one arm never occurs, the kernel's
estimate there is an extrapolation of an outcome model, and its interval does not know it.

Today the kernel reads positivity off one logistic propensity model with main effects: it refuses
when more than `max_extreme_propensity_share` (5%) of units have an estimated propensity outside
`propensity_bounds` ([0.01, 0.99]), or when the covariates separate the arms perfectly (ATE only).
That misses every violation a main-effects logistic cannot express: a stratum defined by two or
three covariates jointly, a threshold on a continuous covariate. In the development prototype the
existing check refused **none** of the four violation designs below (0 of 1,200 replications per
design). On IHDP (T2) it detected nothing in 100 of 100 realizations.

## The method under test

Registered here so the implementation is bound to it. All thresholds are `AnalysisPolicy` fields
(hashed into provenance), with the defaults below.

**1. The existing logistic check** is kept unchanged, and runs first.

**2. Honest overlap regions (new; refuses).** For a binary treatment with a non-empty back-door
(or randomized) adjustment set:

- The units are split at random into two halves (seeded from `policy.seed`). On one half a
  classification tree for the treatment is grown on the encoded adjustment covariates: binary splits
  chosen by Gini impurity, depth at most `overlap_region_depth` = **4**, every child at least
  `n_min` units, a node split only if the split lowers the impurity. Its leaves are candidate
  regions — conjunctions of conditions on the covariates, so a stratum defined by several covariates
  jointly is one leaf.
- The other half's units are routed into those leaves (honest evaluation: the data that chose a
  region never judge it). In a leaf with `m` held-out units of which `k` received arm `a`, the
  one-sided `1 − α` Clopper–Pearson upper bound on the probability of arm `a` is computed, with
  `α` = `overlap_region_alpha` = **0.05**. The leaf is a **violation region for arm `a`** when that
  bound is below `overlap_region_bound` = **0.05**: the held-out data give 95% confidence that fewer
  than one unit in twenty in this region receives arm `a`.
- The halves then swap roles, and each unit is judged by the tree grown on the half it is not in.
- `n_min` = ⌈ln α / ln(1 − bound)⌉ = **59**: the smallest number of held-out units with which a
  region with none of an arm can reach that confidence at all. Fewer, and the check cannot tell a
  structural absence from chance, so it does not try.
- **ATE**: both arms are checked; the violation share is the share of all units lying in a violation
  region for either arm. **ATT**: only regions without controls matter (a region never treated gets
  zero weight in the ATT), so only arm 0 is checked, and the share is among treated units.
- **What it does.** A violation share above `max_extreme_propensity_share` (5%, the tolerance the
  logistic check already uses) is a `POSITIVITY` refusal whose detail and `missing_information`
  name the regions as rules (`b0 = 1 and b1 = 1 and b2 = 1`). A share above zero but within the
  tolerance produces an answer with a warning naming the regions: the estimate extrapolates over that
  share of units, and says so.
- **Never silent trimming.** The kernel does not drop the flagged units and go on to estimate:
  removing them changes the population, and so the estimand, from the one asked about. The refusal
  names the regions so the caller can ask the narrower question (state it in `query.population`);
  an answer's `n_used` is always the full analysed sample.

**3. Limited overlap (new; flags, does not refuse).** From the same logistic propensity, the share of
units outside `limited_overlap_bounds` = **[0.1, 0.9]** (the rule of thumb of Crump, Hotz, Imbens &
Mitnik 2009, *Dealing with limited overlap*; for the ATT, only treated units above 0.9). A share
above `max_extreme_propensity_share` sets a limited-overlap flag and a warning: the ATE rests heavily
on a few comparable units, or on an outcome model, for that share of the population. It does not
change the result's status or estimate.

**Contract (additive).** `Diagnostics.overlap` (new, optional) carries the method, the three
thresholds, `n_min`, the violation share, the regions (rule, held-out units, of them treated, the
missing arm, the upper bound), the limited-overlap bounds, share and flag. A new design check,
`overlap regions (positivity)`, records pass or fail. `AnalysisPolicy` gains `overlap_region_bound`,
`overlap_region_alpha`, `overlap_region_depth` and `limited_overlap_bounds`.

## Designs

Every design: `n = 4,000` units, graph each covariate → `t` and → `y`, plus `t → y`; identification
back-door on every covariate.

**Covariates.** *Low-dimensional* (4): `x0, x1 ~ N(0, 1)`, `b0, b1 ~ Bernoulli(0.5)`, independent.
*High-dimensional* (20): `x0 … x5 ~ N(0, 1)`, `b0 … b13 ~ Bernoulli(0.5)`, independent.

**Propensity** (before any violation): `e = expit(ℓ)`,
`ℓ = 0.6·x0 − 0.4·x1 + 0.4·b0 − 0.4·b1`, plus, in high-dimensional designs,
`0.1·(x2 + … + x5) + 0.15·(b2 + b3 + b4 + b5 − 2)`. A logistic model with main effects is correctly
specified here: the clean designs are the existing check's home ground.

**Outcome:** `y = 1 + τ·t + μ(X) + ε`, `ε ~ N(0, 1)`,
`μ = x0 + 0.5·x1 + 0.5·b0 + 0.5·b1` (+ `0.25` times every other covariate, high-dimensional), and
`τ = 1 + 0.5·b_h` with `b_h` the last binary covariate (`b1`, low; `b13`, high). **Truth:** the ATE is
`1 + 0.5·P(b_h = 1)` = **1.25** in every design. In the high-dimensional ATT designs `b13` enters
neither the propensity nor any violation, so the ATT is also **1.25**.

| design | covariates | estimand | positivity structure (set by construction) | share of units affected (analytic) | correct behaviour |
|---|---|---|---|---|---|
| `clean` | low | ATE | none; `e ∈ [0.024, 0.975]`, 0.4% outside [0.1, 0.9] | 0 | answer, cover |
| `clean_highdim` | high | ATE | none; 0.7% outside [0.1, 0.9] | 0 | answer, cover |
| `limited_overlap` | high | ATE | `e = clip(expit(2·x0 − 0.4·x1 + 0.4·b0 − 0.4·b1), 0.06, 0.94)`: no unit below 0.06, 28.6% outside [0.1, 0.9] | 0 below the bound | answer, cover, **flag limited overlap** |
| `small_violation` | low | ATE | `e = 0` where `x0 > 1.8` | 3.6% (below the 5% tolerance) | **answer with a warning** |
| `structural_stratum` | low | ATE | `e = 0` where `b0 = 1 and b1 = 1` | 25% | **refuse** |
| `joint_violation_highdim` | high | ATE | `e = 0` where `b0 = b1 = b2 = 1`; every single covariate still has both arms at every level (P(t = 1 \| b0 = 1) ≈ 0.41) | 12.5% | **refuse** |
| `continuous_violation` | low | ATE | `e = 0` where `x0 > 1.15` | 12.5% | **refuse** |
| `att_no_controls` | high | ATT | `e = 1` where `b0 = b1 = b2 = 1` | 22.3% of treated units | **refuse** |
| `att_never_treated` | high | ATT | `e = 0` where `b0 = b1 = b2 = 1` | 12.5% of units, none treated | answer, cover (the ATT needs overlap one way only) |

**Replications:** 500 per design, seeded from `seed`; each seeded defect is run on its catching design
with `defect_reps` = 200 replications. **Policy:** the default `AnalysisPolicy` with `refuters = ()`
and `sensitivity = false` — neither changes whether the kernel answers or what it estimates, only
the status and the sensitivity block, and together they are most of the running time.

## Bars

- **Detection power.** In each violation design, the share of replications refused with reason
  `POSITIVITY` must be at least **0.90**. Development prototype: 0.990–1.000 in each (2 seeds × 300).
- **False refusals.** In each design where the correct behaviour is to answer, the share of
  replications refused for any reason must be at most **0.02**. Development prototype: 0–0.003.
- **Warning below the tolerance.** In `small_violation`, the share of replications answered with a
  violation share above zero (and so with the overlap-region warning) must be at least **0.70**. Development prototype: 0.853–0.873. (A region of
  3.6% holds about 72 units per half, close to `n_min`: the check sees it most of the time, not
  always, and the bar says so.)
- **Limited-overlap flag.** At least **0.90** of `limited_overlap`'s results flag limited overlap; at
  most **0.05** of `clean`'s and `clean_highdim`'s do. Development prototype: 1.0 and 0.
- **Estimation on accepted designs.** In `clean`, `clean_highdim`, `limited_overlap` and
  `att_never_treated`: coverage of the truth by the primary 95% interval within **[0.925, 0.975]**,
  and |mean error| at most **3** Monte Carlo SEs. `small_violation`'s coverage is reported, not
  gated (its estimate extrapolates over 3.6% of units by design; with this correctly specified
  outcome model the extrapolation happens to be right, which would not make a bar mean much).
- **No silent trimming.** Every result, in every design, has `diagnostics.n_used` equal to `n` and
  the estimand asked for.
- **Refusals name their regions.** Every `POSITIVITY` refusal whose overlap-region check failed
  carries at least one region with a non-empty rule in `diagnostics.overlap.regions`.
- **The registered thresholds are the defaults.** `AnalysisPolicy()` has the three region thresholds
  and the limited-overlap bounds registered here.

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `no_region_check` | the kernel as it was: only the logistic check | detection, `joint_violation_highdim` |
| `marginal_only` | regions searched one covariate at a time (a depth-1 split per covariate, evaluated the same honest way): a stratum defined jointly is invisible | detection, `joint_violation_highdim` |
| `in_sample_regions` | the tree's leaves judged on the same half that grew them — the search's own selection read as evidence | false refusals, `limited_overlap` |
| `share_threshold_ignored` | any violation region refuses, whatever share it holds | false refusals, `small_violation` |
| `att_checks_both_arms` | the ATT refused for regions without treated units, as the ATE is | false refusals, `att_never_treated` |
| `silent_trimming` | units in violation regions dropped and the ATE estimated on the rest, still labelled the ATE | detection, `structural_stratum` |

## Pass bars

```toml
[meta]
suite = "T10"
version = 1
registered = "2026-09-26"

[designs]
n = 4000
reps = 500
defect_reps = 200
seed = 20261001
truth = 1.25

[method]
overlap_region_bound = 0.05
overlap_region_alpha = 0.05
overlap_region_depth = 4
limited_overlap_bounds = [0.1, 0.9]
min_region_units = 59

[detection]
designs = ["structural_stratum", "joint_violation_highdim", "continuous_violation",
           "att_no_controls"]
reason = "POSITIVITY"
power_min = 0.90

[answer]
designs = ["clean", "clean_highdim", "limited_overlap", "small_violation", "att_never_treated"]
false_refusal_max = 0.02
warning_designs = ["small_violation"]
warning_min = 0.70

[limited_overlap]
flag_designs = ["limited_overlap"]
flag_min = 0.90
quiet_designs = ["clean", "clean_highdim"]
false_flag_max = 0.05

[estimation]
designs = ["clean", "clean_highdim", "limited_overlap", "att_never_treated"]
reported = ["small_violation"]
coverage_band = [0.925, 0.975]
bias_mcse_max = 3.0

[seeded_defects]
required = ["no_region_check", "marginal_only", "in_sample_regions", "share_threshold_ignored",
            "att_checks_both_arms", "silent_trimming"]
```

**Why these bars (Monte Carlo error).** At 500 replications a rate near 0.99 has an SE of about
0.0045, so the 0.90 detection bar sits about twenty SEs below the development figure; a check whose
power fell to 0.90 would sit on it. A false-refusal rate near 0.003 has an SE of 0.0024: 0.02 is
seven of them above; `in_sample_regions` refused 0.217–0.22 of `limited_overlap` in development
(at 200 replications, SE about 0.03, six SEs past the bar), `share_threshold_ignored` 0.853–0.873 of
`small_violation`, and `att_checks_both_arms` 0.997 of `att_never_treated`. The warning bar of 0.70
is nine SEs (0.016) below 0.853. A coverage of 0.95 at 500 replications has an SE of 0.0097: the
band is ±2.6 SE. `no_region_check` and `marginal_only` refused 0 of `joint_violation_highdim` in
development; `silent_trimming` answers every replication of `structural_stratum` by construction.

**What this gate does not establish.**

- **Violations the tree cannot isolate.** A stratum needs about `2·n_min` ≈ 118 units before it can
  be flagged at all, so small violations in small samples are invisible; one defined by four or more
  covariates jointly is usually missed at `n = 4,000` (development prototype: a 6.25% four-way
  stratum was flagged in 0–1% of replications); an exclusive-or pattern, where no single split
  separates anything, defeats a greedy tree entirely.
- **Violations in unmeasured variables.** The check sees the adjustment set. IHDP's removal of treated
  children by mother's race — race is not among its 25 covariates — leaves limited overlap in those
  covariates, not a region without treated units (T2 v2 gates that it is flagged, not refused).
- **That the tolerance is right for any given question.** An answer with a warning below 5% still
  extrapolates over that share; whether that is acceptable depends on how different the effect
  might be there, which no overlap check can know.
- **Continuous treatments** (their check, the share of treatment variance left after the
  covariates, is unchanged) and **panel, cluster and sequential designs** (their own paths).
- **That trimming is wrong.** Estimating on the overlap population is often the right analysis; it is
  a different question, and the kernel leaves asking it to the caller.

## Changelog

- v1 (2026-09-26): initial registration.
