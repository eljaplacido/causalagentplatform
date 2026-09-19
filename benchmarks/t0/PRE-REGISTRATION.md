# T0 correctness gate — pre-registration

**Suite:** T0, analytic structural causal models with closed-form truth
**System under test:** `packages/causal-core` (`causal_core.analyze` and its parts)
**Registered:** 2026-09-19, version 3 (v1 and v2 the same day; see changelog)
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
- *(v3)* effect heterogeneity, where the ATT and the ATE differ:
  - Z ~ N(0,1), X ~ Bernoulli(logistic(Z)), Y = X + 0.8·X·Z + Z + ε
  - ATE = 1
  - ATT = 1 + 0.8·E[Z | X=1] = 1.330594, computed by deterministic quadrature
  - the quadrature was cross-checked against a 20-million-draw Monte Carlo of the data-generating
    process, which gave 1.33033

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
  correctly logistic-linear. Regression adjustment is then biased (by about 0.16) and AIPW is not.
  - **Gated (v2):** at n = 50,000, a pre-computed power analysis predicts about 99% power (below).
  - **Reported, not gated:** power at n = 2,000. v1 gated it, and it failed at 0.04 (see changelog).

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
| `triangulation_disabled` | triangulation always reports agreement | triangulation power at n = 50,000 |

## Statistics and pass bars

```toml
[meta]
suite = "T0"
version = 3
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
n = 2000                         # false alarms, and the reported (not gated) power
reps = 200
seed = 20260921
false_alarm_max = 0.075
power_min = 0.80                 # gated at power_n
power_n = 50000
power_reps = 200

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
SE (`TRIANGULATION_SE_FLOOR` in `causal_core/pipeline.py`). The false-alarm and power bars above
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
- v2 (2026-09-19): changed after the first v1 run, and for these reasons only:
  1. **Triangulation power at n = 2,000 failed.** It was 0.04 against the 0.80 bar. The v1 result is
     kept as `results/2026-09-19-v1-run1.json`.
     - *Diagnosis:* regression adjustment is biased by +0.157, and its intervals cover the truth only
       64% of the time. AIPW stays valid (94.3% coverage). But the standard error of the difference
       between the two is about 0.13 at n = 2,000. A bias of about 1.2 standard errors of that
       difference cannot be detected at the Holm-corrected level. This is a limit of any Hausman-type
       comparison, not a bug.
     - *The claim "triangulation detects moderate misspecification at moderate n" is withdrawn.* It is
       recorded as not established in `conformance/declared_not_implemented.json`. Its power is still
       reported.
     - *What is gated instead:* power at a sample size fixed in advance by a power analysis. The
       standard error of the difference scales as 1/√n, so it is about 0.026 at n = 50,000. The
       minimum detectable bias is then (2.81 + 0.84) × 0.026 ≈ 0.095, which is below 0.157. The
       predicted power is about 99%. That is a prediction, and the gate tests it.
     - *Why the gate needs this:* without a gated power check, the `triangulation_disabled` defect is
       caught only because power already fails. A defect "caught" by a check that fails anyway has
       not been caught.
  2. **A path reference changed.** `causal_core/analyze.py` became `causal_core/pipeline.py`: the
     module was renamed because the re-exported `analyze` function shadowed it. The number and the
     rule it names did not change.
  Nothing else changed: no other threshold, case, coefficient or seed. The v1 run's other sections
  passed and are in the v1 results file.
- v3 (2026-09-19): scope added and nothing relaxed. The kernel gained the ATT estimand (effect on the
  treated), which is needed for the LaLonde real-data benchmark (T3). Two estimation cases are
  appended: `heterogeneous_ate_binary` and `heterogeneous_att_binary`.
  - They also join the valid designs for refuter and triangulation false alarms, because their
    strategy is back-door.
  - Existing cases keep their indices and seeds, so their numbers reproduce the v2 run exactly.
  - No threshold changed. The v2 run passed, in `results/2026-09-19-v2-run1.json`.
