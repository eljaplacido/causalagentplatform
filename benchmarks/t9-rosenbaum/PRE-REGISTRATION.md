# T9 Rosenbaum sensitivity gate — pre-registration

**Suite:** T9, sensitivity of a matched comparison to an unmeasured confounder (Rosenbaum bounds),
on designs with a planted confounder of known strength
**System under test:** `packages/causal-core` (`causal_core.sensitivity`: the pair-level bound
`rosenbaum_pairs`, and the matched design built inside `analyze(...)` for back-door designs with a
binary treatment, reported as `CausalResult.sensitivity.rosenbaum`)
**Registered:** 2026-09-26, version 1
**Runs:** every PR, via `pytest -m t9` in `packages/causal-core`

## Status of this document

Written and committed before Rosenbaum bounds exist in the kernel. No bar below was chosen by
looking at output from the code under test. The truths quoted below are closed-form (the paired
designs, `e2e_effect`) or an exact finite sum over the structural model (`e2e_hidden_bias`), and
the power figures behind the bars come from the large-sample theory of the statistic, computed
without the kernel.

## What this gate is for

Every back-door estimate rests on "no unmeasured confounding", which data cannot check. The
kernel already reports how strong a confounder would have to be on the risk-ratio scale
(E-values) and in shares of variance (Cinelli–Hazlett). The platform's documents also claim
**Rosenbaum bounds**, the design-based statement: in a study matched on the measured
covariates, suppose two units with the same covariates may differ in their odds of treatment by
a factor of at most `Γ` because of something unmeasured. How large may `Γ` be before the
evidence of an effect is gone?

**Method.** For a back-door design with a binary treatment the kernel builds a matched design:
a logistic propensity score on the adjustment set, then greedy 1:1 nearest-neighbour matching on
its logit, without replacement, each unit of the smaller arm matched to the nearest unmatched unit
of the larger arm within a caliper of 0.2 standard deviations of the logit (Austin 2011); units
without a partner inside the caliper are dropped and counted. The treated-minus-control
differences of the pairs are tested for no effect with Wilcoxon's signed-rank statistic, and
Rosenbaum's upper bound on its one-sided p-value at each `Γ` uses the large-sample normal
approximation (Rosenbaum 2002, §4.3.3): with `π = Γ/(1+Γ)`, ranks `r_i` of the nonzero `|D_i|`
(average ranks for ties, zero differences dropped) and `T = Σ r_i·1[D_i > 0]`,
`p⁺(Γ) = 1 − Φ((T − π Σ r_i) / sqrt(π(1−π) Σ r_i²))`. The test's direction is the sign of the
primary estimate (differences are negated for a negative one).

**The estimand of the sensitivity statement** is `Γ*`, the *sensitivity value*: the largest
`Γ ≥ 1` at which `p⁺(Γ) ≤ α₁`, with `α₁ = (1 − ci_level)/2` — the one-sided half of the level the
primary interval and its E-value (`e_value_ci`) use, so the three statements answer the same
question at the same level. `Γ*` is `null`, with the reason, when the test does not reject even at
`Γ = 1`. `p⁺` is increasing in `Γ`, so `Γ* ≥ Γ` exactly when the bound test at `Γ` rejects.

Two facts make `Γ*` checkable against truth (Rosenbaum 2002 §4; 2004):

1. **Validity.** If the hidden bias is at most `Γ₀` and there is no effect, the bound test at
   `Γ₀` rejects with probability at most `α₁`. So `P(Γ* ≥ Γ₀) ≤ α₁`: a reported `Γ*` that is at
   least the planted bias is a false claim of robustness.
2. **Consistency.** As the number of pairs grows, `Γ*` converges to the *design sensitivity*
   `Γ̃ = p/(1−p)`, `p = P(D₁ + D₂ > 0)` for two independent pair differences (the Wilcoxon
   statistic divided by `Σ r_i` estimates `p`; the bound's mean is `π`). `Γ̃` is a property of the
   data-generating process alone and is computed here without the kernel.

## Design

### Paired designs (the bound itself)

`n_pairs = 500` pair differences per replication, given to `rosenbaum_pairs` directly, with the
direction the sign of their mean.

- **Sharp hidden bias.** `|D_i|` independent half-normal (the absolute value of a standard normal);
  `D_i > 0` with probability `Γ₀/(1+Γ₀)`, independently of `|D_i|`; no effect. This is exactly
  Rosenbaum's model at its worst case: every pair has one unit with `u = 1`, the unit with
  `u = 1` has the higher response, and it is the treated one with odds `Γ₀`. So the true
  `Γ` at which the apparent effect is explained away is `Γ₀`, analytically, and `Γ̃ = Γ₀`
  (`p = π₀² + π₀(1−π₀) = π₀`).
- **Effect, no hidden bias.** `D_i ~ N(0.5, 1)`: an additive effect, no confounding. `Γ̃ =
  Φ(0.5·√2)/(1 − Φ(0.5·√2)) = 3.171`.

| design | `Γ₀` | truth for `Γ*` | what it tests |
|---|---|---|---|
| `pairs_null` | 1 | no effect, no bias: `Γ*` should be `null` | the size of the level-`ci_level` test |
| `pairs_bias_2` | 2 | `Γ₀ = 2` | validity and consistency at a moderate bias |
| `pairs_bias_4` | 4 | `Γ₀ = 4` | the same at a strong bias |
| `pairs_effect` | 1 | `Γ̃ = 3.171` | consistency when the effect is real |

### End-to-end designs (the matched design the kernel builds)

`n = 3000` rows per replication, analysed with
`analyze(frame, "X -> T; X -> Y; T -> Y", treatment="T", outcome="Y",
policy=AnalysisPolicy(refuters=()))` (refuters off only for speed; they do not touch this
field). A covariate `X`, an unmeasured confounder `U ~ Bernoulli(0.5)` independent of `X` and not
in the data or the graph,

`T ~ Bernoulli(σ(a + b·X + ln(Γ₀)·U))`,   `Y = c·X + β·U + τ·T + ε`,   `ε ~ N(0, 1)`.

Within two units with the same `X`, the odds of treatment differ by at most `Γ₀` (`u ∈ {0, 1}`),
so Rosenbaum's model holds with `Γ₀`; it is not sharp (pairs with equal `U`, and the noise), so
`Γ̃ < Γ₀`. **Oracle.** With `X` binary and controls outnumbering the treated in both strata, the
matched pairs are exact on `X` and every treated unit is matched; a pair's difference is then
`β(U_t − U_c) + τ + (ε_t − ε_c)` with `U_t`, `U_c` drawn from `U | T, X` and `X` from its
distribution among the treated, and `p = P(D₁ + D₂ > 0)` is an exact finite sum of normal
probabilities over the mixture. The gate computes it from the structural parameters, not from any
fit.

| design | `X` | `a`, `b` | `Γ₀` | `c`, `β`, `τ` | truth | what it tests |
|---|---|---|---|---|---|---|
| `e2e_null_measured` | Bernoulli(0.5) | −1.5, 1.2 | 1 | 2, 0, 0 | `Γ̃ = 1`: `Γ*` should be `null` | matching removes the measured confounding |
| `e2e_hidden_bias` | Bernoulli(0.5) | −1.5, 0.8 | 3 | 1, 2, 0 | `Γ₀ = 3`; `Γ̃ = 1.880` | validity and consistency through the pipeline |
| `e2e_effect` | Bernoulli(0.5) | −1.5, 0.8 | 1 | 1, 0, 0.5 | `Γ̃ = Φ(0.5)/(1−Φ(0.5)) = 2.241` | consistency when the effect is real |
| `e2e_continuous` | N(0, 1) | −1.0, 0.8 | 3 | 1, 2, 0 | `Γ₀ = 3` | inexact matching — **reported, not gated** |

Every end-to-end replication must return a result whose `sensitivity.rosenbaum` is present.

**Replications.** 2,000 per paired design, 1,000 per end-to-end design, seeded from `seed`.

**Withheld.** Where the matched design does not apply, the kernel must return its result with
`sensitivity.rosenbaum` null and a non-empty `sensitivity.rosenbaum_null_reason` — never a `Γ`:

| case | data | why |
|---|---|---|
| `continuous_treatment` | the end-to-end graph, `T = X + U + N(0, 1)` continuous | no treated/control pairs |
| `randomized` | `T ~ Bernoulli(0.5)` independent of everything, graph `X -> Y; T -> Y`, `design="randomized"` | assignment is known; hidden bias is excluded by design |
| `instrument` | graph `Z -> T; T -> Y; U -> T; U -> Y`, `Z ~ Bernoulli(0.5)`, `U` unmeasured (identified by IV) | the matched comparison is not the identifying design |

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `random_pairs` | the matched structure is ignored: treated units are paired with controls at random | false rate, `e2e_null_measured` |
| `lower_bound` | the favourable bound is used (`π = 1/(1+Γ)`, the least biased assignment) | exceedance, `pairs_bias_2` |
| `alpha_not_halved` | `Γ*` is computed one-sided at `1 − ci_level` instead of `(1 − ci_level)/2` | exceedance, `pairs_bias_2` |

## Pass bars

```toml
[meta]
suite = "T9"
version = 1
registered = "2026-09-26"

[run]
seed = 20260926
pairs_reps = 2000
e2e_reps = 1000
n_pairs = 500
n_rows = 3000

[pairs]
null_designs = ["pairs_null"]
bias_designs = ["pairs_bias_2", "pairs_bias_4"]
effect_designs = ["pairs_effect"]
false_rate_max = 0.065
exceed_truth_rate_max = 0.035
retain_factor = 1.5
retain_rate_min = 0.85
effect_exceed_factor = 1.5
effect_exceed_rate_max = 0.035

[e2e]
null_designs = ["e2e_null_measured"]
bias_designs = ["e2e_hidden_bias"]
effect_designs = ["e2e_effect"]
reported = ["e2e_continuous"]
false_rate_max = 0.07
exceed_truth_rate_max = 0.04
retain_factor = 1.5
retain_rate_min = 0.90
oracle_exceed_factor = 1.5
oracle_exceed_rate_max = 0.05

[withheld]
must_withhold = ["continuous_treatment", "randomized", "instrument"]

[seeded_defects]
required = ["random_pairs", "lower_bound", "alpha_not_halved"]
```

Bar by bar, with `Γ*` the reported sensitivity value (`null` counts as below every `Γ ≥ 1`):

- **false rate** (`*_null*`): the share of replications with a non-null `Γ*`, i.e. the test rejects
  no effect at `Γ = 1` in the primary estimate's direction.
- **exceedance of truth** (bias designs): the share with `Γ* ≥ Γ₀`.
- **retention below truth** (bias and effect designs): the share with `Γ* ≥ Γ_ref / retain_factor`,
  `Γ_ref` being `Γ₀` in the paired bias designs and `Γ̃` in the effect designs and
  `e2e_hidden_bias`.
- **exceedance of the design sensitivity** (effect designs; `e2e_hidden_bias`): the share with
  `Γ* ≥ factor · Γ̃`.

**Why these bars.** The level is `ci_level = 0.95`, so `α₁ = 0.025`.

- *False rate.* The test rejects in the direction the data point, so its size is about
  `2α₁ = 0.05`. Monte Carlo SE at 0.05: 0.0049 (2,000 reps), 0.0069 (1,000); the bars 0.065 and
  0.07 are three SEs above 0.05.
- *Exceedance of truth.* In the sharp paired designs the bound is attained, so the rate is about
  `α₁ = 0.025` (normal approximation); Monte Carlo SE 0.0035 at 2,000 reps, bar 0.035 about three
  SEs above. End-to-end, the bound is not sharp and the true rate is far below `α₁` (the theory
  gives under 10⁻¹⁰ at 1,135 pairs); the bar 0.04 is `α₁` plus three SEs at 1,000 reps (0.0049),
  so it tests validity, not sharpness.
- *Retention.* The power of the bound test at `Γ₀/1.5` is 0.968 (`Γ₀ = 2`) and 0.910 (`Γ₀ = 4`)
  with 500 sharp pairs (exact mean and variance of `T` under the model), and 0.946 at `Γ̃/1.5` in
  `pairs_effect` (U-statistic approximation); the bar 0.85 sits nine Monte Carlo SEs (0.0064)
  below the lowest. End-to-end the same calculation gives above 0.997 for `e2e_hidden_bias`
  (≈1,135 pairs) and `e2e_effect` (≈771 pairs); the bar 0.90 leaves room for the normal
  approximation and for pair counts that vary by replication.
- *Exceedance of `Γ̃`.* The theory puts the chance of `Γ* ≥ 1.5·Γ̃` below 10⁻⁴ in every design
  it applies to; the bars (0.035, 0.05) only need to catch a `Γ*` that does not converge to the
  design sensitivity.
- *Defects.* `random_pairs` leaves `e2e_null_measured`'s measured confounding in the pairs (a
  mean difference near `2·(P(X=1 | T=1) − P(X=1 | T=0)) ≈ 0.6` with ~911 pairs), so the false rate
  goes to 1. `lower_bound` makes `p⁺` the minimum over assignments, so `Γ*` far exceeds `Γ₀`
  whenever the data favour treatment. `alpha_not_halved` moves the sharp exceedance rate from 0.025
  to about 0.05, three SEs above the bar — so its catch is deterministic under the registered seed
  but not by a wide margin.

**What this gate does not establish.**

- That a matched design is balanced on real data. The gated designs have one binary covariate,
  so matching on the propensity score is exact; `e2e_continuous` (reported) shows what inexact
  nearest-neighbour matching does, and the result carries the matched sample's largest
  standardized difference for the reader to judge. Residual imbalance is bias the bound does not
  cover.
- Anything about the primary estimator. `Γ*` is a statement about the matched comparison's test
  of no effect (the sharp null), in the direction of the primary estimate; the matched units are
  the smaller arm and its partners, not the primary's population, and `Γ*` does not bound the
  bias of the AIPW or IPW estimate. A marginal sensitivity model for the weighted estimators
  (Tan 2006; Zhao, Small & Bhattacharya 2019) is not implemented.
- The best statistic. Wilcoxon's test understates design sensitivity relative to other
  statistics (Rosenbaum 2010, 2011); `Γ*` here is a lower bar, not the study's full robustness.
- Exactness in small samples. The bound uses the normal approximation throughout.
- Matching with multiple controls, full matching, or optimal (non-greedy) matching.

## Changelog

- v1 (2026-09-26): initial registration.
