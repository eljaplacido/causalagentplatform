# causal-core

A causal inference kernel that **identifies before it estimates, abstains when it cannot, and binds
every number it returns to the computation that produced it.**

`causal-core` answers one question: *what is the effect of X on Y, given this data and this causal
graph?* Or it explains, in typed and machine-readable form, why that question cannot be answered.
It has no web framework, no LLM client and no database dependency. It ships as a library and a
CLI. An MCP server and a REST API consume the same contract.

```python
import causal_core as cc

out = cc.analyze(df, "Z -> X; Z -> Y; X -> Y; W -> Y", treatment="X", outcome="Y")
out.kind            # "result" | "refusal" | "apparatus_failure", never anything else
print(cc.report(out))
```

```text
Estimated effect of X on Y: 1.915 (95% CI 1.821 to 2.008), n = 2000, estimator aipw.
Estimand: ATE: E[Y(T=1) - Y(T=0)].
Identification: backdoor adjusting for {W, Z}, tier A.
Cross-checked against regression_adjustment, ipw_hajek: estimators agree.
Falsification tests passed: placebo_treatment, implied_independences.
An unmeasured confounder would need a risk-ratio association of 4.017 with both treatment and
outcome to explain the point estimate away (3.831 for the interval bound).
```

```bash
causal-core analyze data.csv --graph "Z -> X -> Y; Z -> Y" -t X -y Y --json
```

The command exits 0 for a result, 2 for a refusal and 1 for an apparatus failure.

## What it does

| Stage | What happens |
|---|---|
| **Identify** | Runs the complete ID algorithm (Shpitser & Pearl 2006), cross-checked against y0 on random graphs. Back-door uses the generalized adjustment criterion and prefers the efficiency-optimal O-set. It also handles front-door, conditional IV and proximal inference. A user-chosen adjustment set is validated, never silently replaced: an invalid one is refused, with the open path named. |
| **Check the design** | Missing data, positivity and overlap, covariate balance, sample-ratio mismatch and first-stage strength are all checked *before* estimating. Any failure produces a typed refusal that says what would fix it. |
| **Estimate** | A portfolio of difference in means, Lin regression, Hajek IPW, cross-fitted AIPW and DML, linear front-door, 2SLS and proximal 2SLS. Every standard error comes from the estimator's influence function, and every interval is coverage-tested. |
| **Triangulate** | Estimators that rest on different assumptions are compared, with the correlation between them taken into account. If they disagree, the result is marked *contested*. |
| **Refute** | A placebo treatment, negative-control outcomes and conditional independences implied by the graph. Their false-alarm rates and power are measured, not assumed. |
| **Sensitivity** | E-values (VanderWeele & Ding) and Cinelli–Hazlett robustness values. |
| **Claims** | Every number that may appear in prose is a claim bound to a field. `cc.verify(text, out)` rejects any number that isn't one. It also rejects causal wording ("caused", "will reduce") unless the result is *supported*. |
| **Provenance** | Digests of the graph, query, data and policy, all in RFC 8785 canonical JSON, plus a digest of the result itself. The same inputs reproduce the same `result_digest`. |

## For whom does it work?

An average effect can describe nobody. Declare the subgroups and the kernel estimates within each:

```python
out = cc.analyze(incidents, "load -> playbook; load -> minutes; playbook -> minutes",
                 treatment="playbook", outcome="minutes", segment_by=("severity",))

out.primary.point        # -3.07  -- the pooled effect, an average of two opposite truths
out.heterogeneity.differs  # True
for s in out.segments:
    print(s.level, s.estimate.point, s.family_wise_ci)
# P1 -11.90  (-12.73, -11.06)
# P3   5.90  (  5.03,   6.78)
```

The playbook shortens P1 incidents by twelve minutes and lengthens P3 incidents by six. Acting on
the pooled −3.07 gets both wrong.

Three rules make segment claims worth something:

- **Declared, never discovered.** You name the subgroups before seeing the answer. Searching for
  the subgroup with the largest effect is a different activity with different statistics, and this
  kernel will not pretend the correction covers it.
- **Two intervals, and the honest one is wider.** Each segment carries its own interval and a
  family-wise interval adjusted for how many segments were examined. Claims use the second.
  Per-segment nulls are tested with Holm across the family.
- **Ask whether they differ before saying they do.** One Cochran's Q runs before any comparison.
  When it does not reject, the result says the segments could not be told apart.

A segment too small to estimate, or with no treated units, gets its own typed refusal — it is not
dropped, and not guessed.

## What it refuses to do, and says so

A refusal is a correct answer, not an error. Each one has a typed reason:

- `NON_IDENTIFIABLE`
- `POSITIVITY`
- `LOW_N`
- `DESIGN_CHECK_FAILED` (for example a sample-ratio mismatch or missing data)
- `INVALID_ADJUSTMENT_SET`
- `WEAK_INSTRUMENT`
- `UNAPPROVED_ASSUMPTION` (unreviewed LLM-proposed edges under a strict policy)
- `RUNG_MISMATCH` (counterfactual queries are not implemented)
- `NO_ESTIMATOR`
- `CI_TOO_WIDE`
- `INVALID_INPUT`

Each refusal also lists `missing_information`: what would make the question answerable.

## Evidence

The T0 correctness gate is pre-registered in `benchmarks/t0/PRE-REGISTRATION.md`, with bars fixed
before any run. It runs on every change:

- **Identification:** agrees with literature labels on 29 of 29 canonical graphs, and with y0 on
  200 of 200 random ADMGs.
- **Estimation:** across 14 designs (randomized, back-door, M-bias, butterfly, front-door, IV,
  conditional IV and proximal), bias is within Monte Carlo error. Pooled 95% interval coverage is
  94.9%, and every design lies inside 92.5–97.5% (observed 93.6–96.6%; T0 v3,
  `benchmarks/t0/results/2026-09-19-v3-run3.json`).
- **Refuters:** false-alarm rates are about 1% at α = 0.01, and power is 100% against the planted
  violations.
- **Seeded defects:** each of seven defects makes the gate fail. The defects include:
  - the constant (−0.5, 0.5) interval the platform once shipped
  - halved standard errors
  - discarded adjustment sets
  - a suppressed hedge

- **T4 (segments):** per-segment interval coverage 0.93–0.97; family-wise coverage across all
  segments of one analysis 0.945–0.98; the heterogeneity test fires on 4.5% of homogeneous designs
  (α = 0.05) and 100% of planted differences; with every segment null, Holm rejects something in
  4% of runs. Each of four seeded defects fails the specific bar it was registered against.

**Known limit.** Triangulation does *not* reliably detect moderate outcome-model misspecification
at moderate n. Its power was 0.04 at n = 2,000 against a bias of about 1.2 standard errors. The v1
run that showed this is kept in `benchmarks/t0/results/`. The doubly robust primary estimator stays
valid in that case (94.3% coverage), but do not read "estimators agree" as proof that the model is
correct.

Running `pytest -m t0` needs the `crosscheck` extra (`pip install -e '.[dev,crosscheck]'`).

## Extending it

Estimators are plug-ins. See `causal_core.estimators`:

```toml
[project.entry-points."causal_core.estimators"]
my_forest = "my_pkg.estimators:ForestEstimator"
```

Graphs can be text (`"Z -> X; X <-> Y; latent U"`), a `networkx.DiGraph`, or a list of edges.
Data can be pandas, a dict of columns, pyarrow, polars, or any dataframe-interchange object.

## Not implemented yet

- Unit-level counterfactuals (rung 3)
- Estimation for general ID functionals such as the napkin graph: identified, then refused with
  `NO_ESTIMATOR`
- Nonparametric front-door
- Subgroup *discovery* (declared segments are supported; searching for them is not)
- Discovery
- Time series
- Sequential (anytime-valid) tests
- Interference-aware designs

Each is listed in the repository's `conformance/declared_not_implemented.json`, and leaves that
list only by being implemented and tested.

Licence: Apache-2.0. Core dependencies are numpy, scipy, pandas, networkx and pydantic, all
permissively licensed.
