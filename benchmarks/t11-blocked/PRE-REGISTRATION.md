# T11 blocked and paired randomized designs gate — pre-registration

**Suite:** T11, randomized experiments analysed within blocks: matched pairs (the same task run under
both arms) and larger blocks (strata), with continuous and binary outcomes
**System under test:** `packages/causal-core` (`causal_core.blocked`, reached through
`analyze(..., design="randomized", block=<block column>)`)
**Registered:** 2026-11-04, version 1
**Runs:** every PR, via `pytest -m t11` in `packages/causal-core`

## Status of this document

Written and committed before `causal_core.blocked` exists. No bar below was chosen by looking at
output from the code under test.

Disclosed, because it chose the binary interval and shaped two designs: before registration, the
exact coverage of four candidate intervals for a paired difference of proportions was computed **by
enumeration of the trinomial distribution of (treated-only, control-only, concordant) pairs**, in a
scratch script outside the kernel (no kernel code was involved, and no simulation on this
document's seed). The candidates were the paired t interval on t(K − 1), Tango's (1998) asymptotic
score interval, the same score interval with a continuity correction of 1 on the `b − c` scale, and
the exact conditional McNemar test as the reference for consistency. Over a grid of 35 (p10, p01)
points with K = 10, 20, 30 and 50 pairs:

- the paired t interval's coverage fell to 0.18 (K = 10) and 0.45 (K = 30) where discordant pairs
  are rare: an interval of zero width whenever no pair is discordant;
- Tango's interval covered 0.930–0.951 at its minimum; on the motivating data below (0 vs 4
  discordant pairs of 30) it excludes zero, where the exact McNemar p-value is 0.125;
- the continuity-corrected score interval covered at least 0.970 everywhere, and over every split of
  up to 120 discordant pairs it never excluded zero where the exact McNemar test did not reject at
  0.05 (it is slightly more conservative in 8 splits whose exact p lies in 0.0488–0.0498).

The kernel therefore uses the continuity-corrected score interval for binary pairs, and this gate
holds it to the property that motivated the choice (bar `mcnemar_inconsistent_max`). The same
enumeration, at the parameters of the registered binary designs, gave the coverages quoted under
**Why these bars**; they are arithmetic about the method, not output of the kernel.

## What this gate is for

When the same task is run under both arms, the comparison that matters is within the task. An
analysis that ignores the pairing compares two arm means whose noise includes every difference
between tasks, and its interval is wide for that reason: on the gnomon steering experiment S1 (30
tasks run under both arms, which agreed on 26 of 30) the independent-samples interval was
[−0.386, +0.119]; the decision layer then sized a follow-up as independent cells, at 391 per arm.
Pairing removes the between-task noise. For a binary outcome it also changes which interval is
honest: with few discordant pairs, the paired t interval is confidently wrong (above), and the
interval must agree with the exact test a statistician would run on the same table — McNemar's.

The kernel estimates the effect within each block and averages the block effects weighted by block
size:

`τ̂ = Σ_b (n_b / N) · (Ȳ_b,treated − Ȳ_b,control)`,

the size-weighted average of within-block differences (the block-fixed-effects estimator with a
treatment-by-block interaction; unbiased for the average effect whatever the treated share of each
block). Blocks are the independent observations: the variance is the between-block variance of the
weighted block effects, `K/(K − 1) · Σ_b (n_b/N)² (τ̂_b − τ̂)²`, with critical values from t(K − 1)
(for pairs this is the paired t interval). For a **binary outcome in pairs**, the interval is
instead the score interval for a paired difference of proportions (Tango 1998) with a continuity
correction, which at a difference of zero reduces to McNemar's test with Edwards' correction; the
exact two-sided McNemar p-value is reported beside it.

It refuses, with typed reasons: a block design that is not randomized; a treatment that never
varies (`NO_VARIATION`, new in contract 0.15.0); blocks in which only one arm was observed, unless
`policy.missing = "drop"` (then they are left out, counted and warned about); fewer than
`policy.min_per_arm` complete blocks.

## Design

**Truth.** The average effect in the population the blocks are drawn from, weighted as the estimand
weights it — per unit: `E[n_b τ_b] / E[n_b]` — computed analytically from the design. Blocks are
drawn independently, so this is the quantity the between-block variance is honest about.

**Continuous outcomes.** Within block `b`, unit outcomes
`Y = 10 + u_b + τ_b·T + e`, `u_b ~ N(0, ρ)`, `e ~ N(0, 1 − ρ)`, so `ρ` is the within-block
correlation. Treatment is assigned at random within each block, to exactly the stated number of
units.

| design | blocks | units per block (treated) | ρ | effect `τ_b` | truth | what it tests |
|---|---|---|---|---|---|---|
| `pairs_continuous` | 40 | 2 (1) | 0.7 | 0.3 | 0.3 | the comfortable case; what pairing buys |
| `pairs_few` | 16 | 2 (1) | 0.7 | 0.3 | 0.3 | where t(K − 1) matters |
| `pairs_heterogeneous` | 40 | 2 (1) | 0.5 | `0.3 + 0.3·z_b`, `z_b ~ N(0, 1)` | 0.3 | effects that vary by pair |
| `blocks_unequal` | 40 | `2·(1 + Poisson(3))` (half) | 0.5 | `0.3 + 0.05·(n_b − 8)` | 0.375 | size weighting with size-dependent effects |
| `blocks_varying_allocation` | 40 | 8 (2, 4 or 6, equally likely) | 0.5 | 0.6 where 2 or 6 are treated, 0 where 4 are | 0.4 | the per-unit estimand when the treated share varies by block |

`blocks_unequal`'s truth: with `n = 2(1 + X)`, `X ~ Poisson(3)`, `E[n] = 8` and `E[n²] = 76`, so
`E[n τ(n)] / E[n] = (0.3·8 + 0.05·(76 − 64)) / 8 = 0.375`. `blocks_varying_allocation`'s truth: equal
block sizes, `E[τ_b] = (0.6 + 0 + 0.6)/3 = 0.4`; a fixed-effects regression without the interaction
weights blocks by `n_b p_b (1 − p_b)` and estimates `0.36` there.

**Binary outcomes in pairs.** Each pair `b` draws a baseline `q_b`; the control unit's outcome is
`Bernoulli(q_b)`.

| design | pairs | baseline `q_b` | treated unit's outcome | (p10, p01) | truth | what it tests |
|---|---|---|---|---|---|---|
| `binary_pairs_correlated` | 30 | 0.05 or 0.85, equally | `Bernoulli(q_b + 0.1)`, independent of the control's given `q_b` | (0.1425, 0.0425) | 0.1 | strongly concordant pairs, as in S1 |
| `binary_pairs_null` | 30 | 0.05 or 0.85 | `Bernoulli(q_b)` | (0.0875, 0.0875) | 0 | the size of the test |
| `binary_pairs_one_sided` | 40 | 0.3 or 0.7 | 1 if the control's is 1, else `Bernoulli(0.05 / (1 − q_b))` | (0.05, 0) | 0.05 | an effect that only ever helps, and rare discordance |
| `binary_pairs_s1_like` | 30 | 19/30 | 0 if the control's is 0, else `Bernoulli(15/19)` | (0, 4/30) | −4/30 | the motivating table's population |

`p10` is the probability that only the treated unit has the outcome, `p01` that only the control
unit has it; the truth is `p10 − p01`.

**Replications.** 2,000 per design, seeded from `seed`. In the two designs with a width bar, each
replication also runs the same data through the kernel without `block` (the unblocked analysis of
a randomized design, whose primary estimate is the difference in means; refuters and sensitivity
off, which do not change its interval), for the width comparison.

**Known answer: the motivating table.** The gnomon S1 steering experiment (gnomon
`benchmarks/results/causal-2026-11/REPORT.md`): treatment "steering delivered", outcome "solved",
30 (task, attempt) pairs valid in both arms plus one valid only in the delivered arm. The pairs:
15 solved in both, 0 solved only when delivered, 4 solved only when dropped, 11 in neither — the
dropped arm 19/30, the delivered arm 15/30 (16/31 with the unpaired cell), discordant 0–4. With
`policy.missing = "drop"` the kernel must report exactly the figures in `[s1]` below; with the
default `"error"` it must refuse `DESIGN_CHECK_FAILED` (one block has only one arm). Every figure is
exact arithmetic on the table: the point is `(0 − 4)/30`, the exact McNemar p-value `2 · 0.5⁴`.

**Refusals.**

| case | data | reason |
|---|---|---|
| `not_randomized` | `pairs_continuous` analysed with `design = "observational"` | `INVALID_INPUT` |
| `constant_treatment` | `pairs_continuous` with every unit untreated | `NO_VARIATION` |
| `incomplete_blocks` | `pairs_continuous` with one unit removed from 3 pairs; default policy | `DESIGN_CHECK_FAILED` |
| `too_few_blocks` | 8 blocks of 4 units (2 treated): 32 units, 8 blocks | `LOW_N` |

And with `policy.missing = "drop"`, `incomplete_blocks` must return a result whose block summary
counts the 3 incomplete blocks and the 3 units left out (bar `drop_counts_exact`).

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `unpaired_se` | the variance ignores the blocks (independent-samples Neyman variance) | width ratio, `pairs_continuous` |
| `wald_for_binary_pairs` | binary pairs get the paired t interval instead of the score interval | coverage, `binary_pairs_one_sided` |
| `fixed_effects_weights` | blocks weighted by `n_b p_b (1 − p_b)`, the plain fixed-effects estimand | bias, `blocks_varying_allocation` |

## Pass bars

```toml
[meta]
suite = "T11"
version = 1
registered = "2026-11-04"

[blocks]
reps = 2000
seed = 20261104

[estimation]
continuous_designs = ["pairs_continuous", "pairs_few", "pairs_heterogeneous", "blocks_unequal",
                      "blocks_varying_allocation"]
binary_designs = ["binary_pairs_correlated", "binary_pairs_null", "binary_pairs_one_sided",
                  "binary_pairs_s1_like"]
bias_mcse_max = 3.0
coverage_band = [0.93, 0.97]          # continuous designs, nominal 0.95; 2,000 reps give SE ~0.005
pairs_few_coverage_min = 0.925        # K = 16: no ceiling, conservative is acceptable
binary_coverage_min = 0.94            # binary pairs: conservative by construction, so a floor only
mcnemar_inconsistent_max = 0          # binary pairs: replications whose interval excludes 0 while
                                      # the exact McNemar p-value is at least 1 - ci_level

[efficiency]
# mean width of the blocked interval / mean width of the unblocked difference-in-means interval,
# on the same replications
width_ratio_max = { pairs_continuous = 0.8, binary_pairs_correlated = 0.9 }

[s1]
pairs = 30
incomplete_blocks = 1
units_left_out = 1
discordant_treated = 0
discordant_control = 4
treated_solved = 15
control_solved = 19
point = -0.13333333333333333
point_tolerance = 1e-12
mcnemar_exact_p = 0.125
interval_contains_zero = true

[refusals]
must_refuse = ["not_randomized", "constant_treatment", "incomplete_blocks", "too_few_blocks"]
drop_counts_exact = true

[seeded_defects]
required = ["unpaired_se", "wald_for_binary_pairs", "fixed_effects_weights"]
```

**Why these bars.** The band and the bias bar are T7's. 2,000 replications put the Monte Carlo SE
of a 95% coverage near 0.005, so [0.93, 0.97] is about ±4 SE. `pairs_heterogeneous`,
`blocks_unequal` and `blocks_varying_allocation` are held to it because their truth is the
population average the between-block variance is honest about. For binary pairs the interval is
conservative on purpose — consistency with the exact test costs coverage above nominal — so only a
floor is held: the enumeration above gave 0.995 (`binary_pairs_correlated`), 0.990 (`null`), 0.999
(`one_sided`) and 0.999 (`s1_like`) for the method, and 0.941, 0.952, **0.868** and 0.919 for the
paired t interval, which is why `wald_for_binary_pairs` is held to the `one_sided` floor. The width
bars are where pairing is supposed to pay: with ρ = 0.7 the paired interval should be about
`sqrt(0.3) ≈ 0.55` of the unpaired one; on `binary_pairs_correlated` the enumeration's expected
width (0.418) against the unpaired interval's at the arms' expected proportions (0.514) is 0.81. The
`unpaired_se` defect puts the ratio near 1.

**What this gate does not establish.**

- **The paired sizing in the decision layer.** `causal_decision` sizes a paired follow-up from the
  observed discordance; that is pinned by its own tests (`tests/test_paired_sizing.py`), not here.
- **Exactness.** The binary interval agrees with the exact McNemar test about zero and covers
  conservatively in these designs; it is not an exact unconditional interval, and its coverage at
  parameter values far from these designs rests on the enumeration disclosed above.
- Covariate adjustment within blocks, blocks combined with clusters, sequences or segments, a
  non-binary treatment, and interference between units of one block (the treated run of a task
  affecting its control run): not in this version; the combinations are refused.
- A binary outcome in blocks larger than pairs uses the between-block t interval, held here only by
  the continuous designs' coverage; no binary design with larger blocks is registered.
- Fewer than `policy.min_per_arm` (10) complete blocks: refused, not estimated.

## Changelog

- v1 (2026-11-04): initial registration.
