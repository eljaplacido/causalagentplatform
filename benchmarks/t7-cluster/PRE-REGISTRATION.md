# T7 cluster-randomized designs gate — pre-registration

**Suite:** T7, experiments randomized by cluster (service, region, tenant) with outcomes measured
per unit (request, user, incident)
**System under test:** `packages/causal-core` (`causal_core.cluster`, reached through
`analyze(..., design="randomized", cluster=<cluster column>)`)
**Registered:** 2026-09-25, version 1
**Runs:** every PR, via `pytest -m t7` in `packages/causal-core`

## Status of this document

Written and committed before `causal_core.cluster` exists. No bar below was chosen by looking at
output from the code under test.

## What this gate is for

In operations the thing that is randomized is rarely the thing that is measured. A change ships to
some *services*; latency is measured per *request*. Requests within one service share its load,
its hardware and its bad afternoon, so they are not independent observations, and an interval
computed as if they were is too narrow by a factor that grows with the cluster size and the
intra-cluster correlation — easily two or three times at the sizes operations see. "Significant
across four million requests" can be forty services' worth of evidence.

The kernel estimates the unit-weighted average effect (the effect on a request, averaged over
requests) by the difference in means, with standard errors from the influence function summed
within clusters — cluster-robust by construction — a CR1 small-sample correction `K/(K−1)`, and
critical values from a t distribution on `K − 2` degrees of freedom, `K` the number of clusters.
It refuses when treatment varies within a cluster (the design is not what the analysis assumes)
and when there are too few clusters per arm to estimate a cluster-level variance.

## Design

**Clusters.** `K` clusters; cluster sizes `m_j = 10 + Poisson(40)`; treatment assigned to whole
clusters, exactly half of them, completely at random. Unit outcomes

`Y_ij = 10 + τ_j·T_j + u_j + e_ij`, `u_j ~ N(0, σ_u²)`, `e_ij ~ N(0, σ_e²)`, `σ_u² + σ_e² = 1`,

so the intra-cluster correlation is `ICC = σ_u²`.

| design | `K` | ICC | effect `τ_j` | what it tests |
|---|---|---|---|---|
| `many_clusters` | 80 | 0.1 | 0.3 | the comfortable case |
| `moderate` | 40 | 0.3 | 0.3 | typical operations: few dozen services, strong correlation |
| `few_clusters` | 20 | 0.3 | 0.3 | where the t correction matters |
| `null_effect` | 40 | 0.3 | 0 | coverage of zero |
| `size_dependent` | 40 | 0.1 | `0.3 + 0.01·(m_j − 50)` | effects that grow with cluster size: unit-weighted and cluster-weighted estimands differ |

**Truth.** The unit-weighted ATE over the realized clusters, `Σ_j m_j τ_j / Σ_j m_j`, computed per
replication (it varies only in `size_dependent`).

**Replications.** 1,000 per design, seeded from `seed`.

**Refusals.**

| case | data | reason |
|---|---|---|
| `varies_within_cluster` | a quarter of units in treated clusters are untreated | `DESIGN_CHECK_FAILED` |
| `too_few_clusters` | 6 clusters (3 per arm) | `LOW_N` |
| `not_randomized` | `cluster` set with `design="observational"` | `INVALID_INPUT` |

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `unit_level_se` | the standard error treats units as independent | coverage, `moderate` |
| `cluster_weighted_estimate` | the estimate averages cluster means, a different estimand | bias, `size_dependent` |

## Pass bars

```toml
[meta]
suite = "T7"
version = 1
registered = "2026-09-25"

[clusters]
reps = 1000
seed = 20260927
min_clusters_per_arm = 5     # the kernel's refusal threshold under test

[estimation]
designs = ["many_clusters", "moderate", "few_clusters", "null_effect", "size_dependent"]
bias_mcse_max = 3.0
coverage_band = [0.93, 0.97]         # nominal 0.95, per design; 1,000 reps give SE ~0.007
few_clusters_coverage_min = 0.925    # K = 20: a small-sample correction is approximate

[refusals]
must_refuse = ["varies_within_cluster", "too_few_clusters", "not_randomized"]

[seeded_defects]
required = ["unit_level_se", "cluster_weighted_estimate"]
```

**Why these bars.** 1,000 replications put the Monte Carlo SE of a 95% coverage near 0.007, so
[0.93, 0.97] is about ±2.9 SE. At `K = 20` the CR1 + t(K−2) correction is known to be close but not
exact, hence the separate floor, and no ceiling there (conservative is acceptable, anti-conservative
is not). Under `unit_level_se` with ICC 0.3 and clusters of ~50 the design effect is about
`1 + (m − 1)·ICC ≈ 16`, so its intervals are about four times too narrow; the defect cannot hide.

**What this gate does not establish.**

- Interference *between* clusters (a treated service slowing an untreated one that calls it).
  The design assumes clusters do not affect each other; that is the assumption the cluster was
  chosen to make true, and nothing here tests it.
- Stratified or pair-matched cluster randomization, cluster-level covariate adjustment, and
  switchback (time-block) designs. Not in this version.
- Anything below 5 clusters per arm: refused.

## Changelog

- v1 (2026-09-25): initial registration.
