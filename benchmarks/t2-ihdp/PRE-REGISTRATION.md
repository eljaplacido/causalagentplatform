# T2 IHDP benchmark — pre-registration

**Suite:** T2, semi-synthetic data: real covariates with simulated potential outcomes
**System under test:** `causal_core.analyze`
**Registered:** 2026-09-19, version 1; version 2 on 2026-09-26 (see Changelog)
**Runs:** every release, and on demand with `python -m causal_core.bench.ihdp`. It needs network
access or a warm cache.

## Status of this document

This was written before causal-core was run on these data. The only thing inspected is the array
shapes in the `.npz` files: 672 + 75 units, 25 covariates and 100 realizations.

## Why this benchmark

IHDP (Hill 2011) takes the covariates of 747 children from the Infant Health and Development
Program and simulates potential outcomes on top of them. Response surface "B" is nonlinear and
heterogeneous, so the truth is known for every unit. Two design features make it a sharp test of
honesty:

1. **Overlap is broken on purpose.** Hill removed a nonrandom subset of the treated group: every
   child with a non-white mother. Some covariate profiles therefore have no treated units, and the
   ATE over the whole sample is identified only by extrapolating an outcome model. The **ATT** is
   still supported, because every treated profile has controls.
2. **The response surfaces are nonlinear.** Linear nuisance models are misspecified. A doubly
   robust estimator then depends on the propensity model.

The data come from the widely used 100-realization release by Johansson, Shalit & Sontag (2016).
Train and test splits are pooled into the full 747-unit sample.

## Data

The files are fetched from `http://www.fredjo.com/files/` and must match these SHA-256s:

| file | sha256 |
|---|---|
| `ihdp_npci_1-100.train.npz` | `750697c71b4f8d7a3aafff771b56a4ac4cd83ec649bf69afb04f8a5aee41a240` |
| `ihdp_npci_1-100.test.npz` | `a70a8acbcc4e8deb677cc9bf9e9dabeb17caaa37cdbb1d7ba06be7ffb929c41c` |

**Licence.** None is stated, so the data are not redistributed. They are downloaded on demand and
cached outside the repository.

**Truth per realization.** The effect is averaged over the fixed sample, using the noiseless
surfaces `mu1` and `mu0`:

- ATE truth = mean(mu1 − mu0) over all 747 units;
- ATT truth = the same mean over the treated units.

These are conditional sample-average effects, while causal-core's intervals target the population
estimand and include covariate-sampling variance. **Coverage above 95% is therefore expected and is
not a defect.** Under-coverage is.

**Graph.** Each of the 25 covariates → `t` and → `y`, plus `t` → `y`. Identification is back-door on
all 25.

## Designs and configurations

- Estimands: `ate` and `att`.
- Configurations:
  - `C1`: the default policy (linear nuisance models).
  - `C2`: `learners = "flexible"` (gradient boosting; needs the `ml` extra).
- 100 realizations for each estimand × configuration.

## Pass bars

```toml
[meta]
suite = "T2-IHDP"
version = 2
registered = "2026-09-26"

[data]
base_url = "http://www.fredjo.com/files/"
train = "750697c71b4f8d7a3aafff771b56a4ac4cd83ec649bf69afb04f8a5aee41a240"
test = "a70a8acbcc4e8deb677cc9bf9e9dabeb17caaa37cdbb1d7ba06be7ffb929c41c"

[gate]
# ATT: supported by the design; the kernel must answer and its intervals must cover.
att_min_result_share = 0.90        # share of realizations that return a result, per configuration
att_min_coverage = 0.90            # coverage of the ATT truth among results, per configuration

# ATE: overlap is violated by construction. Refusing is correct. Any ATE result that IS returned
# must still cover the truth at the roadmap's ACIC bar.
ate_min_coverage_if_answered = 0.90

# No confident wrong answer: among `supported` results (either estimand, either configuration),
# the share whose interval misses the truth must stay below this. That is 2x the nominal 5%,
# allowing for Monte Carlo error over 100 realizations.
max_supported_miss_share = 0.10

[overlap]
# v2. Per configuration: the share of realizations whose ATE is refused for POSITIVITY or whose
# result carries the limited-overlap flag (diagnostics.overlap.limited_overlap) must reach
# ate_min_flagged_share; the same share for the ATT must stay at or below att_max_flagged_share.
ate_min_flagged_share = 0.90
att_max_flagged_share = 0.10

[reported]
# Not gated.
mean_abs_error = true              # the usual IHDP metric, eps_ATE / eps_ATT
refusal_reasons = true
status_distribution = true
```

**Why these bars.**

- The ATT is the question this design can answer, so the gate asks that it be answered and
  covered.
- For the ATE, the correct behaviour is to refuse, or to answer honestly. Refusal rates are
  reported rather than gated at 100%: a refusal needs a detectable overlap violation, and whether
  the one-hot race covariates in this release make it detectable is exactly what the run will
  show.
- The supported-miss bar applies the platform's central promise, *never confidently wrong*, to
  data with known truth.

**The overlap flag (v2).** Run 1 showed the ATE answered in every realization, no refusal: the
by-construction violation went undetected. It was not a violation the kernel could have refused.
Hill removed treated children by mother's race, and race is not among the 25 covariates the analysis
adjusts for; no binary covariate level has zero treated units (the smallest treated shares are 1 of
27 and 2 of 55). What the removal leaves in the covariates is *limited* overlap: many profiles with a
propensity near zero, none with none. Contract 0.12.0 adds two checks (pre-registered as T10): honest
overlap regions, which refuse when a region of the covariates lacks an arm, and a limited-overlap
flag, set when more than 5% of units have an estimated propensity outside [0.1, 0.9] (Crump et al.
2009; for the ATT, treated units above 0.9). v2 gates that the IHDP ATE is flagged or refused and the
ATT, which the design supports, is not.

Disclosed: before this version was written, both quantities were computed on these data outside the
kernel (a scratch script using the kernel's logistic helper and a prototype of the region check):
the share of units outside [0.1, 0.9] was 0.332 in every realization for the ATE, the share of
treated units above 0.9 was 0 for the ATT, and the prototype found no region without treated units.
The bars were set from the design's logic, not from those figures, but the figures were seen.

**Reported, not gated: rival estimators (v1.1).** Beside the kernel, each realization is also
estimated by (a) the naive difference in means and (b) an OLS regression of `y` on `t` and all 25
covariates, with a heteroskedasticity-robust (HC1) 95% interval — the estimate a careful analyst
without the kernel would report. Their mean absolute error against the ATE and the ATT, and OLS's
interval coverage of the ATE, are reported beside the kernel's. No bar reads them: they position
the kernel's figures, they do not judge them. IHDP is known to rank estimators inconsistently
across its realizations, so a ranking here is weak evidence either way.

## Changelog

- v1 (2026-09-19): initial registration.
- v1.1 (2026-09-25): rival estimators reported beside the kernel (naive difference in means;
  OLS with covariates and HC1 intervals). No bar or design changed, and no bar reads them.
  Prompted by an external review's point that a benchmark result means more positioned against
  what an analyst would otherwise use.
- v2 (2026-09-26): the `[overlap]` bars (register entry `kernel-overlap-detection-limits`; ledger
  septa-34, with T10). Prompted by every run so far (runs 1–5), in which the ATE was answered in
  100 of 100 realizations with no overlap diagnostic at all. The ATE must now be refused for
  POSITIVITY or flagged for limited overlap in at least 0.90 of realizations per configuration, and
  the ATT flagged in at most 0.10. The data, the configurations and every v1 bar are unchanged.
