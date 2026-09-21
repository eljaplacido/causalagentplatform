# T4 segment-effect gate — pre-registration

**Suite:** T4, effects within declared subgroups, where the truth is known by construction
**System under test:** `packages/causal-core` (`causal_core.segments`, reached through `analyze`)
**Registered:** 2026-09-21, version 2 (v1 the same day; see changelog)
**Runs:** every PR, via `pytest -m t4` in `packages/causal-core`

## Status of this document

Written **before the capability exists**, let alone before it was run. Nothing below was chosen by
looking at output, because there is no output yet. The bars are principled rather than empirical:
nominal coverage is what a 95% interval must do, and a family-wise error rate is what a
multiplicity correction is *for*. If the implementation turns out not to meet them, the failing
run is committed and this document gets a changelog entry — the bars do not move to fit the code.

## Why this gate exists

"Does this action work?" is rarely the operational question. "For whom does it work?" is. An
incident playbook that shortens P1 incidents and lengthens P3 ones has an average effect near zero
and two true answers, and a platform that reports only the average will get both wrong.

Segments are also where a careful team quietly ruins its own evidence. Estimate the effect in ten
subgroups, act on the best one, and the best one is mostly noise: at α = 0.05 with ten independent
null segments, the chance of at least one "significant" result is 40%. This gate exists mainly to
hold the platform to the multiplicity discipline that makes segment claims worth anything.

**Segments are declared, never discovered.** The caller names the subgroups before seeing the
answer, in `CausalQuery.segment_by`. Searching for the subgroup with the largest effect is a
different activity with different statistics, and this kernel does not do it. That restriction is
the point, not a limitation to be fixed later.

## Design

**Estimation.** For a declared discrete variable with levels `s₁…s_k`, the effect is estimated
*within each stratum*: the data is subset, and the stratum gets the same identification and the
same design checks as any other analysis. A stratum with too few units, or without overlap, gets
its own typed refusal rather than a number. Strata are disjoint, so their estimates are
independent, which is what makes the tests below simple enough to be trustworthy.

**Multiplicity.** Two figures are reported per segment and both appear in the contract:

- the *unadjusted* interval, which answers "what is the effect here", and
- the *family-wise* interval at a Bonferroni-adjusted level, which answers "what may I claim,
  having looked at k segments".

Per-segment null hypotheses are tested with Holm's step-down procedure (already in
`causal_core.refute.holm`), which controls the family-wise error rate without assuming
independence. Any *claim* about a segment — including anything the decision layer reads — uses the
adjusted figure.

**Heterogeneity.** Before any segment is compared with another, one test asks whether the segments
differ at all: Cochran's Q, `Σ wᵢ(θᵢ − θ̄)²` with `wᵢ = 1/SEᵢ²`, against χ²(k−1). If it does not
reject, the segment estimates are reported with that fact attached, because acting on differences
the data cannot distinguish from noise is the failure this suite is about.

**Cases.** Data-generating processes with closed-form segment truths, in `causal_core/bench/t4.py`:

| id | what it plants |
|---|---|
| `homogeneous` | the same effect in every segment (the null for heterogeneity) |
| `sign_flip` | opposite effects in two segments, averaging to ~0 |
| `one_responder` | an effect in one segment of four, none in the others |
| `graded` | effect increasing across four ordered segments |
| `confounded_segments` | segment-dependent confounding, correct only if each stratum adjusts |
| `sparse_segment` | one segment too small to estimate: must refuse, not guess |
| `no_overlap_segment` | one segment with no treated units: must refuse, not guess |

## Pass bars

```toml
[meta]
suite = "T4"
version = 2
registered = "2026-09-21"

[estimation]
reps = 400                      # Monte Carlo replications per case
seed = 20260921
bias_tolerance_se = 3.0         # |bias| must be within 3 Monte Carlo standard errors of 0
coverage_nominal = 0.95
coverage_lo = 0.925             # per-segment, unadjusted intervals; same band as T0
coverage_hi = 0.975

[family_wise]
# The point of the correction: across all segments of one analysis, the chance that *any*
# adjusted interval misses its segment's truth is at most 5%, up to Monte Carlo error.
simultaneous_coverage_min = 0.94
# With every segment null, the chance that Holm rejects anything at all.
false_discovery_max = 0.075     # alpha 0.05 plus Monte Carlo slack at 400 reps

[heterogeneity]
alpha = 0.05
false_positive_max = 0.075      # on `homogeneous`: the test must not invent differences
power_min = 0.80                # on `sign_flip` at n = 4000: it must find a real one

[refusals]
# A segment that cannot be estimated must produce a typed refusal, every time.
sparse_and_overlap_refusal_rate = 1.0

[seeded_defects]
reps = 120
required = [
  "no_multiplicity_adjustment",
  "pooled_effect_per_segment",
  "heterogeneity_anticonservative_df",
  "silent_segment_drop",
]
```

**Seeded defects.** Each is a plausible way to write this wrong, and the gate must fail under
every one:

| id | defect | must be caught by |
|---|---|---|
| `no_multiplicity_adjustment` | the family-wise interval is the unadjusted one | `family_wise` |
| `pooled_effect_per_segment` | every segment reports the overall effect | `heterogeneity` power, `estimation` bias |
| `heterogeneity_anticonservative_df` | Cochran's Q compared against too few degrees of freedom, so it invents heterogeneity | `heterogeneity` false positives |
| `silent_segment_drop` | a segment that cannot be estimated is omitted instead of refused | `refusals` |

## What this gate does not establish

- That the declared segments are the right ones. That is a domain judgement, and the kernel has no
  way to check it.
- Anything about *discovered* subgroups. Not implemented, deliberately; the statistics are
  different and much harder to get right.
- Continuous effect modification, or interactions between two segment variables.
- Performance on real data. That is T2 and T3's job.
- **That the heterogeneity test is not needlessly conservative.** Every bar here would be met by a
  test that fires slightly too rarely: the power case plants opposite effects, where power is 1.00
  and stays 1.00 under a conservative error. Catching over-conservatism needs a case whose true
  power sits near the bar, which this suite does not have. See the v2 changelog.

## Changelog

- v2 (2026-09-21): **a registered defect turned out to be invisible to this suite.**
  `heterogeneity_wrong_df` was registered as "Cochran's Q compared against χ²(k) instead of
  χ²(k−1)", to be caught by the heterogeneity false-positive bar. Writing it showed that error
  runs the wrong way: too *many* degrees of freedom makes the test more conservative, so it fires
  *less* often and the false-positive bar is satisfied more comfortably than before. The power bar
  cannot see it either, because the power case plants opposite effects where power is 1.00 with or
  without the defect.

  The defect is replaced by `heterogeneity_anticonservative_df`, which errs in the direction that
  does harm — a test that invents heterogeneity that is not there — and is caught by the
  false-positive bar (0.158 against a bar of 0.075). The blind spot the original defect was
  reaching for is now recorded under "what this gate does not establish" rather than left implied
  by a defect that passes.

  No pass bar changed. The seeded-defect list is the only edit to the TOML block.
- v1 (2026-09-21): initial registration, before the capability was written.
