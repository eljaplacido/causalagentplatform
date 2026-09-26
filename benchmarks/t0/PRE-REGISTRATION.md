# T0 correctness gate — pre-registration

**Suite:** T0, analytic structural causal models with closed-form truth
**System under test:** `packages/causal-core` (`causal_core.analyze` and its parts)
**Registered:** 2026-09-19, version 4 (v1 and v2 the same day; v4 on 2026-09-26; see changelog)
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

*(v4)* Nor does the outcome-model specification test establish that a flagged estimate is biased.
It tests whether the linear outcome model fits, not whether its misfit moves the effect: a
nonlinearity that leaves regression adjustment unbiased is flagged too (the `harmless_interaction`
case below reports how often). It looks only for curvature in the directions of its basis (squares
of each non-binary covariate and the square of the fitted value), so a misfit orthogonal to those
passes. It runs only for binary treatments under back-door adjustment.

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
  - **Reported, not gated (v2, v3):** power at n = 2,000. v1 gated it, and it failed at 0.04 (see
    changelog).
  - **Gated (v4):** power at n = 2,000, the moderate sample size, with the outcome-model
    specification test below. A replication counts as detected only if triangulation disagrees
    *and* the result's status is `contested`, because a flag that does not change the status has
    changed nothing. The data are the same draws as the v2/v3 reported power (same seed and case
    index), so the v4 number is directly comparable with the 0.035 those runs reported.
  - **Gated (v4):** the primary estimate's coverage on that same moderate-n power case. The flag
    changes the status, never the estimate; AIPW with a correct propensity model is valid there,
    and its intervals must still cover (exact binomial test of 0.95, two-sided).
  - **Gated (v4):** status consistency. In every triangulation replication of this section (valid
    designs and both power cases), a disagreement must come with status `contested`.
  - **Reported, not gated (v4):** the flag rate on `harmless_interaction`, a nonlinear design where
    regression adjustment is nonetheless unbiased: Z1, Z2 ~ N(0,1) independent,
    X ~ Bernoulli(logistic(0.8·Z1 + 0.8·Z2)), Y = X + Z1 + Z2 + 0.5·Z1·Z2 + ε, ATE = 1 (by the
    symmetry of the design, the interacted regression's limit is the truth; a 2-million-draw check
    gave 1.0016). It measures what the specification test costs: a flag here is a true statement
    about the model and a needless one about the estimate. Also reported: the power the pairwise
    estimator contrasts alone would have at n = 2,000 (the v3 rule), from the same replications.

**The outcome-model specification test (v4).** Why a specification test rather than a better
comparison of estimates: every estimator contrast is limited by the standard error of the
difference, and under this misspecification that is large. A pre-computed power analysis
(population quantities from 2 million draws of the power design, numpy only, not the kernel)
gave:

| contrast at n = 2,000 | difference | SE of difference | power, α = 0.01 |
|---|---|---|---|
| AIPW − regression adjustment (v3's rule) | −0.139 | 0.119 | 0.08 |
| regression adjustment with vs without the quadratic term (a nested "flexible arm") | 0.149 | 0.075 | 0.27 |

Even at n = 5,000 the nested contrast reaches only 0.70 (one degree of freedom) or 0.52 (a joint
test of three contrasts). The misfit itself enters the misspecified estimator's influence function,
so any comparison of estimates is noisy exactly when the model is wrong. The register entry's
bar (≥ 0.80 at n ≤ 5,000) is not reachable that way.

The test, as implemented in `causal_core/specification.py`:

- *When it runs:* binary treatment, back-door strategy (not `randomized`: under randomization the
  interacted regression is consistent whatever the outcome's shape, so misfit is not a threat), and
  at least one estimator of family `outcome_model` in the portfolio. Otherwise it does not run and
  nothing changes.
- *Which model:* the one regression adjustment fits, arm by arm: for the ATE both arms, for the ATT
  the control arm only (the only one the ATT estimators model).
- *Added terms, per arm:* the square of each non-binary adjustment covariate, standardized, and the
  square of the arm's standardized linear fitted value (Ramsey's RESET term, which carries
  interactions along the outcome's index). Terms collinear with the linear model or with each other
  are dropped. An arm with fewer than 10 observations per parameter of the augmented model is
  skipped.
- *Statistic:* the arm's outcome is regressed on the linear model plus the added terms. The Wald
  statistic tests that all added coefficients are zero, jointly over the arms, with the HC2
  influence-function covariance (the arms are independent samples, so it is block-diagonal). It is
  referred to χ² with as many degrees of freedom as terms retained.
- *Its level:* its p-value joins the pairwise estimator contrasts in the one Holm family at
  `policy.triangulation_alpha` (default 0.01). If any member is rejected, triangulation disagrees.
- *What a flag does:* the same as any other triangulation disagreement and as a failed refuter.
  The result becomes `contested`: the estimate is reported, causal wording is withheld, and
  downstream (causal-decision) a contested result cannot be acted on automatically. The primary
  estimate and its interval are unchanged. The triangulation detail names the test, its statistic,
  degrees of freedom and p-value.

*Predicted power at n = 2,000:* the Z² coefficient is 1, and its standard error in the larger arm is
about 0.024 (the variance of Z² given the linear terms is about 1.44). The prediction is power of
essentially 1, with a wide margin even at n = 500. *Predicted false-flag rate:* the test's nominal
size is 0.01 per design before Holm. HC2 with 4 or fewer degrees of freedom and about 800 to 1,200
observations per arm should be close to nominal, well below the 0.075 bar.

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
| `triangulation_disabled` | triangulation always reports agreement | triangulation power at n = 50,000 (and, v4, at n = 2,000) |
| `specification_test_disabled` *(v4)* | the outcome-model specification test never runs: the v3 kernel | triangulation power at n = 2,000 |
| `specification_pooled_arms` *(v4)* | the specification test fits one model to both arms, with no treatment interactions, so effect heterogeneity reads as misfit | triangulation false alarms |
| `triangulation_flag_ignored` *(v4)* | a triangulation disagreement no longer makes the result `contested` | triangulation power at n = 2,000 (counted by status) and status consistency |

## Statistics and pass bars

```toml
[meta]
suite = "T0"
version = 4
registered = "2026-09-26"

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
# v4: gated at moderate n, with the outcome-model specification test. Detected = disagreement
# AND status contested. Same draws as the v2/v3 reported power at n = 2,000.
moderate_power_n = 2000
moderate_power_reps = 200
moderate_power_min = 0.80
moderate_power_coverage_alpha = 0.01   # exact binomial test of the primary's coverage = 0.95 there
status_consistency_required = 1.0      # share of disagreements whose status is contested

[seeded_defects]
required = [
  "constant_ci", "ignore_adjustment", "suppress_hedge", "halved_se",
  "leaky_estimator", "refuter_always_passes", "triangulation_disabled",
  "specification_test_disabled", "specification_pooled_arms", "triangulation_flag_ignored",
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
- v4 (2026-09-26): scope added, nothing relaxed (register entry `kernel-triangulation-moderate-n`;
  ledger septa-35). Triangulation gains an outcome-model specification test (described above),
  because the power analysis above shows that no comparison of estimates reaches 0.80 at n ≤ 5,000
  against the planted misspecification.
  - Added bars in `[triangulation]`: power ≥ 0.80 at n = 2,000, counted by status; the primary's
    coverage on that case; status consistency. Added seeded defects: `specification_test_disabled`,
    `specification_pooled_arms`, `triangulation_flag_ignored`. Added reported numbers: the
    `harmless_interaction` flag rate, and the power the pairwise contrasts alone would have.
  - Unchanged: every v3 bar, case, coefficient and seed, including the false-alarm bar (which now
    covers the specification test as well) and the gated power at n = 50,000. The moderate-n power
    reuses the v2/v3 reported job's data.
  - The last v3 run passed, in `results/2026-09-25-v3-run5.json`.
  - This version was written before any code for the specification test existed. The only numbers
    seen were the power analysis above: numpy on the power design's population, not the kernel.
