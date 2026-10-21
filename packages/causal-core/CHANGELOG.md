# Changelog -- causal-core

Versions follow the claim contract (`CONTRACT_VERSION`): a change to the published schemas in
`conformance/causal-claims/` is a version bump. Earlier version numbers were development
milestones inside the repository and were never published.

## 0.14.0 -- first PyPI release (unreleased)

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
- **Evidence:** the pre-registered gates T0 and T4-T10, each with seeded defects, pass on the
  tagged commit before anything is published (`.github/workflows/release.yml`).
