# T0 correctness gate — pre-registration

**Suite:** T0, analytic structural causal models with closed-form truth
**System under test:** `packages/causal-core` (`causal_core.analyze` and its parts)
**Registered:** 2026-09-19, version 1
**Runs:** every PR (this configuration), via `pytest -m t0` in `packages/causal-core`

## Status of this document

This was written after the kernel was implemented and smoke-tested on one simulated dataset (one
confounded binary-treatment design), and before any run of this gate. No threshold below was chosen
by looking at gate results. The gate code reads every number from the TOML block below, and each
results file records this file's SHA-256. Changing a bar means editing this file in a visible diff.
The version number goes up, and a changelog entry at the bottom says why.

## What the gate claims to establish

If the gate passes, the kernel does five things correctly on designs where the truth is known
exactly:

1. It identifies effects correctly. It agrees with a complete identification algorithm, and it
   refuses when identification is impossible.
2. Its estimates are unbiased within Monte Carlo error.
3. Its nominal 95% intervals cover the truth about 95% of the time: neither the constant or
   too-narrow intervals of the past, nor needlessly wide ones.
4. Its falsification tests rarely fire on valid designs and usually fire on planted violations.
5. It catches its own seeded defects. A suite that cannot catch them publishes nothing.

It does **not** establish performance on real data, on nonlinear or high-dimensional designs, or
under model misspecification beyond the planted cases. Those are T1–T3.

## Design

**Identification catalog.** 29 canonical graphs from the identification literature, each labelled
in advance with the expected status and strategy. The list is in `causal_core/bench/t0.py`
(`IDENTIFICATION_CASES`). The labels come from the literature, not from running the code:

- back-door, M-bias, butterfly, front-door, napkin, bow arc, IV
- conditional IV, invalid-exclusion IV, proxies
- colliders, precision variables, and randomized designs, including a randomized design that the
  graph contradicts

**Random cross-check.** Random ADMGs are drawn from a fixed seed. causal-core's ID status is
compared with y0's `identify_outcomes`, an independent implementation of ID.

**Estimation cases.** These are linear SCMs with fixed coefficients (`ESTIMATION_CASES`). Truth is
the sum over causal paths of the products of path coefficients, which is exact for binary and
continuous treatments. The cases cover every built-in strategy:

- randomized
- back-door, with binary and continuous treatment
- M-bias
- butterfly
- front-door
- IV
- conditional IV
- proximal
- a null effect

**Refuter cases.**

- *False alarms:* the valid back-door and randomized estimation designs.
- *Power:*
  - (a) an unmeasured confounder, omitted from the analyst's graph, that also drives a declared
    negative-control outcome;
  - (b) an edge omitted from the analyst's graph, which contradicts a conditional independence
    that graph implies.

**Triangulation cases.**

- *False alarms:* the valid binary back-door designs.
- *Power:* a confounder that enters the outcome nonlinearly, as Z², while the propensity is
  correctly logistic-linear. Regression adjustment is then biased and AIPW is not.

**Seeded defects.** Each defect is injected with a context manager in `causal_core/bench/defects.py`.
The gate must fail under every one of them:

| id | defect | must be caught by |
|---|---|---|
| `constant_ci` | every interval replaced by the constant (−0.5, 0.5), as the platform once did | coverage |
| `ignore_adjustment` | the adjustment set is discarded after identification | bias |
| `suppress_hedge` | ID reports "identified" even when it finds a hedge | identification |
| `halved_se` | standard errors divided by 2 | coverage |
| `leaky_estimator` | an estimator that uses the outcome to build its answer | placebo refuter |
| `refuter_always_passes` | refuters report success without testing | refuter power |
| `triangulation_disabled` | triangulation always reports agreement | triangulation power |

## Statistics and pass bars

```toml
[meta]
suite = "T0"
version = 1
registered = "2026-09-19"

[identification]
catalog_match_required = 1.0     # share of catalog cases whose status AND strategy match the label
random_admgs = 200               # random ADMGs cross-checked against y0 (skipped only if y0 absent)
random_nodes_min = 4
random_nodes_max = 7
random_seed = 20260919
agreement_required = 1.0

[estimation]
n = 1000
reps = 500
seed = 20260919
bias_max_mcse = 3.0              # |mean(est) - truth| <= 3 * sd(est) / sqrt(reps), every case
coverage_family_alpha = 0.01     # exact two-sided binomial test of coverage = 0.95 per case, Holm
pooled_coverage_min = 0.935      # coverage pooled over all cases
pooled_coverage_max = 0.965
reported_cell_band = [0.925, 0.975]   # the roadmap's per-cell band: reported, not gated (see below)

[refuters]
n = 1000
reps = 200
seed = 20260920
false_alarm_max = 0.075          # per refuter, pooled over valid designs
power_min = 0.80                 # per planted violation

[triangulation]
n = 2000
reps = 200
seed = 20260921
false_alarm_max = 0.075
power_min = 0.80

[seeded_defects]
required = [
  "constant_ci", "ignore_adjustment", "suppress_hedge", "halved_se",
  "leaky_estimator", "refuter_always_passes", "triangulation_disabled",
]
reps = 200                       # reduced replications when re-running the gate under a defect
```

**Why Holm-binomial rather than the fixed per-cell band.** The roadmap's bar is 92.5–97.5% coverage
per cell. With 500 replications the standard error of a coverage estimate is about 0.97 points, so
the fixed band rejects a perfectly calibrated cell about 1% of the time. Across a dozen cells, a
correct kernel would then fail the gate roughly one run in nine. The gate therefore uses:

- an exact binomial test per case, Holm-corrected at family α = 0.01;
- pooled coverage within [93.5%, 96.5%].

The per-cell band is still computed and reported next to the gate. Under-coverage and over-coverage
both fail. Needlessly wide intervals are a defect too.

**Noise floor.** Every replication uses an independent seed derived from `seed` and the replication
index, so a rerun reproduces the same numbers exactly. Run-to-run variation is zero by construction.
The Monte Carlo error is what the bars above account for.

**Triangulation standard-error floor.** When two estimators are first-order equivalent on a design
(for example AIPW and interacted regression under randomization), the standard error of their
difference from influence functions is close to zero. Second-order noise would then read as
disagreement. The difference's standard error is therefore floored at 0.25 × the smaller estimator
SE (`TRIANGULATION_SE_FLOOR` in `causal_core/analyze.py`). The false-alarm and power bars above
measure the consequences of that choice.

## Results

Results go to `benchmarks/t0/results/` as JSON. Each file records:

- this file's SHA-256
- the git SHA
- the per-case numbers

Nothing is published as a claim unless the seeded-defect run for the same commit shows every
defect caught.

## Changelog

- v1 (2026-09-19): initial registration.
