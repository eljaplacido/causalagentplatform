# Changelog -- causal-core

Versions follow the claim contract (`CONTRACT_VERSION`): a change to the published schemas in
`conformance/causal-claims/` is a version bump. Earlier version numbers were development
milestones inside the repository and were never published.

## 0.15.0 -- 2026-11-13, first release (GitHub release v2026.11.13; PyPI to follow)

Contract 0.15.0. 0.14.0 was never published; this entry and the next are one release.

- **Blocked and paired designs:** `analyze(..., design="randomized", block=<column>)` compares
  within each pair or block (size-weighted within-block differences, blocks as the independent
  observations, t(K - 1)); a binary outcome in pairs gets the continuity-corrected score interval
  (Tango 1998), which excludes zero only when McNemar's test rejects, with the discordant pairs
  and the exact McNemar p-value in `CausalResult.blocks`. Gated by T11, which reproduces gnomon's
  S1 table exactly. `causal_core.blocked.paired_binary_power` gives the exact power of that
  analysis, for sizing.
- **`NO_VARIATION`:** a treatment that takes one value is refused with its own reason, the
  outcome's spread and what would resolve it, instead of `DESIGN_CHECK_FAILED` with nothing in
  `missing_information`.
- **`target_population`:** declared, not implemented. The result keeps its own population and
  says the estimate was not transported (register: transportability).
- **Reproducible digests:** `analyze` runs BLAS on one thread (`causal_core.numerics`, new
  runtime dependency `threadpoolctl`) and the ID expression lists its c-components in sorted
  order, so a result digest no longer depends on the host's thread count or the string hash
  seed (septa-89). The pin covers every path `analyze` takes, the blocked and paired designs
  and the not-transported warning included. Same build and CPU family only; the digest format
  is unchanged.

## 0.14.0 (never published; folded into 0.15.0)

- **Identify:** the complete ID algorithm (Shpitser & Pearl 2006), back-door with the
  efficiency-optimal O-set, front-door, conditional IV and proximal inference; a user-chosen
  adjustment set is validated, never silently replaced.
- **Check the design:** missing data, positivity and overlap, covariate balance, sample-ratio
  mismatch and first-stage strength, each failing as a typed refusal.
- **Estimate:** difference in means, Lin regression, Hajek IPW, cross-fitted AIPW and DML, linear
  front-door, 2SLS and proximal 2SLS, with influence-function standard errors; staggered
  difference-in-differences, anytime-valid monitoring, cluster-randomized and switchback designs.
- **Triangulate, refute, sensitivity:** contested results flagged; placebo, negative-control and
  implied-independence refuters; E-values, Cinelli-Hazlett robustness values, Rosenbaum Γ*.
- **Segments:** declared subgroups with family-wise intervals and Cochran's Q.
- **Claims and provenance:** every number is a claim bound to a field (`cc.verify`); RFC 8785
  digests of graph, query, data, policy and result.
- **Evidence:** the pre-registered gates T0 and T4-T11, each with seeded defects, pass on the
  tagged commit before anything is published (`.github/workflows/release.yml`).
