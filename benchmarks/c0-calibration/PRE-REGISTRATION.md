# C0 calibration-report gate — pre-registration

**Suite:** C0, whether the calibration report states what a body of follow-ups can and cannot say
**System under test:** `packages/causal-decision` (`causal_decision.outcome.report` and the
estimates it returns), fed by the real decision path (`causal_core.analyze` → `decide` → `plan` →
`causal_core.analyze` over a later window → `compare`)
**Registered:** 2026-09-26, version 1
**Runs:** every PR, via `pytest -m c0` in `packages/causal-decision`

## Status of this document

Written and committed before the report carries intervals, a minimum follow-up count or a paired
comparison. Today `report()` returns bare proportions: a rate of 1.0 from one follow-up, no
interval, and no way to tell "the data showed no over-claiming" from "the data could not have shown
it". No bar below was chosen by looking at output from the code under test, because the code under
test does not exist yet.

The stream simulator's parameters (effect 12, 1000 rows per window, harm threshold 2) were checked
on a pilot of the *kernel* alone, before this was written: at those settings the realized standard
error is about 0.7, so every gated flag's threshold is at least 4.8 standard errors from where the
type puts the estimate (the `null` type's zero against ±(2 + 1.96·0.7)), a full rollout comes back
`DESIGN_CHECK_FAILED`, and about 5% of strong pre-decision analyses come back `experiment` because
a refuter raised a false alarm (placebo treatment, estimator disagreement). That last one does not
bias anything here: the verdict is fixed before the later window is drawn, and the type that sets
the truth is drawn independently of it.

## What this gate is for — and what it is not

This gate is **simulated**. Every number it produces is about simulated decision streams whose true
effects were set by construction. It says nothing about how well the platform's thresholds are
calibrated on real operations; that needs real follow-ups, and there are none
(`decision-thresholds-uncalibrated` on the register stays open for exactly that reason).

What it can establish is that when real follow-ups arrive, **the report computed from them will be
honest**: its intervals cover the rate they claim to estimate at the nominal level, it refuses to
state a rate from too few follow-ups, it keeps the follow-ups the later data could not answer out of
the rates but counts them and carries them into the bounds, and it never says "no difference" when
the comparison could not have found one. A report that is wrong in any of those ways would be read
as evidence about the thresholds; this gate is what stands between that and the first real number.

## Design

### The report under test (the contract this registers)

- Every rate is a **Clopper-Pearson (exact) interval** at `level`, with its numerator and
  denominator. Below `min_followups` in the denominator the rate is **refused**: no point, no
  interval, and a stated reason. Rates: realized point inside the predicted interval; the two
  intervals overlap; acted and the realized interval cleared the minimum worthwhile effect; acted
  and the realized interval lay wholly beyond the harm threshold. The last two are over *acted*
  (`act`, `act_with_approval`) *decidable* follow-ups.
- **Undecidable follow-ups** (the kernel refused the later window, or it failed) are counted, by
  reason, and never enter a rate's numerator or denominator.
- **Bounds over every acted decision** for the worthwhile rate. The undecidable ones are not missing
  at random (migration `004_decision_followups.sql`: the action rolled out to everybody is the one
  that stops being measurable), so the rate over all acted decisions is only partially identified:
  worst-case bounds `[s/N, (s+u)/N]` (`s` acted-and-cleared, `u` acted without an answer, `N`
  acted), with the confidence set `[CP_lo(s, N), CP_hi(s+u, N)]`. Refused below `min_followups`.
- **A paired comparison** over decidable follow-ups that carry a predicted interval: did the
  pre-decision analysis claim the effect cleared the minimum worthwhile effect (predicted lower bound
  above it) as often as the later window confirmed it (realized lower bound above it)? `b` = claimed
  but not confirmed, `c` = confirmed but not claimed; exact two-sided McNemar test (binomial on the
  `b + c` discordant pairs, p = 0.5). Its status is one of
  - `difference` — p ≤ `alpha`;
  - `no_difference_detected` — enough discordant pairs that the test *could* have rejected, and it
    did not;
  - `could_not_resolve` — fewer than `min_discordant` discordant pairs (then even the most lopsided
    split, all `b` or all `c`, has p = 2·0.5^(b+c) > `alpha`, so "no difference" would be a claim
    the data could never have contradicted), or fewer than `min_followups` pairs.

  `min_discordant` = 6 is derived, not chosen: the smallest d with 2·0.5^d ≤ 0.05 (0.03125; d = 5
  gives 0.0625).

### Part 1 — exact checks (no sampling)

1. **Interval coverage.** For every n in [`min_followups`, `n_max`] and every p on a grid of
   `p_grid` points (0.005, 0.010, …, 0.995), the exact coverage
   Σₖ Binom(k; n, p)·1[p ∈ CI(k, n)] of the report's interval function is at least `level`
   (tolerance `coverage_tolerance` for floating point). Clopper-Pearson guarantees this; a Wald
   interval fails it badly (its minimum on this grid is below 0.1).
2. **Refusal.** For every n < `min_followups` the report's rate estimate is refused (no point, no
   interval, a reason); for every n ≥ it, it is stated.
3. **Paired comparison.** For every d in [0, `n_max`] and every split b + c = d: status is
   `could_not_resolve` exactly when d < `min_discordant` (with at least `min_followups` pairs); never
   `no_difference_detected` below it; and the probability under H0 (b ~ Binom(d, 0.5)) that the
   status is `difference` is at most `alpha` (+1e-12).

### Part 2 — decision streams through the real code path

**Types, and the truth by construction.** Each decision is one of three types, drawn independently:

| type | pre-decision window (benefit) | later window (benefit) | realized flags |
|---|---|---|---|
| `works` | 12 | 12 | clears the worthwhile bar; not harmful |
| `null` | 12 | 0 | does not clear; not harmful |
| `harms` | 12 | −12 | does not clear; harmful |

Both windows are `incidents`-style data: `load ~ N(0,1)`, playbook assigned with probability
`logistic(0.9·load)`, `minutes = 60 + 8·load − benefit·playbook + N(0, 10²)`, 1000 rows, graph
`load -> playbook; load -> minutes; playbook -> minutes`, direction `decrease`, minimum worthwhile
effect 2, harm threshold 2. The later window is instead a **full rollout** (every row treated) with
a per-type probability — `works` more often than the others, because what worked gets rolled out —
and the kernel refuses it (undecidable). With realized standard errors near 0.7, each gated flag's
threshold is at least 4.8 standard errors from where the type puts the estimate (12 for `works` and
`harms`), so the flag is a function of the type: the chance that any of the 240 pool decisions
crosses is below 1e-3. The gate *verifies* that on every pool member (`type_violations_allowed = 0`)
rather than assuming it.

So, with type shares π and rollout probabilities r, the truths are exact:

- acted-and-worthwhile among decidable: π_w(1−r_w) / Σₜ πₜ(1−rₜ)
- acted-and-harmful among decidable: π_h(1−r_h) / Σₜ πₜ(1−rₜ)
- worthwhile over **all** acted decisions (what the bounds must cover): π_w
- the paired comparison: every pre-decision window claims the bar is cleared, so every decidable
  `null` or `harms` follow-up is a `b` and `c` = 0 — whenever any `null` or `harms` type is possible
  the true answer is "over-claimed", and a correct report says `difference` or `could_not_resolve`,
  never `no_difference_detected`.

The verdict (whether a decision was acted on) is fixed by the pre-decision window, which is
identically distributed across types, so conditioning on acted leaves the truths unchanged.

**The pool, and why resampling it is exact for the gated flags.** `per_cell` decisions are run
through the full path for each (type × rolled out or not) cell — 6 cells, 240 decisions, each with
two kernel analyses. A stream of k decisions draws each decision's type and rollout from the
config's probabilities and takes a realization from that cell's pool (with replacement, a fresh
digest per stream position, so the report's one-realization-per-decision rule holds). Because every
gated flag is a function of the cell — verified on every pool member — resampling within a cell
changes no gated flag, and the stream's gated counts are exactly binomial in the type mixture. The
ungated flags (point inside the predicted interval, overlap) do vary within the `works` cell; their
stream-level truth is not known by construction and is not gated here (Part 1 gates the interval
method they share; the accounting bar gates their counts).

**Configs** (in the TOML): `small` (k = 12: the decidable-acted count straddles `min_followups`, so
both the refusal and the stated path run), `medium` (k = 30), `large` (k = 200), `high` (k = 15,
π_w = 0.95, no rollouts: a high rate at small n, where Wald intervals collapse to [1, 1]).

**Checks, every stream:**

- **Accounting** (exact): `n_decisions` = k; decidable + undecidable = k; undecidable by reason sums
  and equals the simulated rollouts; `n_acted` equals the acted count; each rate's numerator and
  denominator equal what the simulated types imply; the paired `b`, `c` equal the simulated counts.
- **Refusal** (exact): each rate is stated iff its denominator ≥ `min_followups`.
- **Coverage** (sampled; configs listed in `coverage_configs`): over the streams where a rate was
  stated, the share whose interval contains the truth is at least `min_coverage`, for each of
  acted-worthwhile, acted-harmful and the all-acted bounds. Each such config must state the rate in
  at least `min_stated_streams` streams.
- **Paired** (exact given the stream): status is `could_not_resolve` iff pairs < `min_followups` or
  b + c < `min_discordant`; never `no_difference_detected` (every config's truth is "over-claimed"
  or, where the pairs cannot show it, unresolvable).
- **Scope floors** for `small`: at least `min_refused_streams` streams refuse the acted-worthwhile
  rate and at least `min_refused_streams` state it — so both branches were looked at.

**Why `min_coverage = 0.925`.** The Clopper-Pearson interval's coverage is at least 0.95 at every
(n, p) (Part 1 proves it for this implementation), and conditional on the denominator the stated
counts are exactly binomial at the truth, so each stream-level coverage is at least 0.95 in
expectation. Design calculation (exact, from the binomial model above, before any code): expected
coverage 0.971 (`medium`), 0.959 (`large`), 0.995 (`high`) for acted-worthwhile. At 400 streams the
Monte Carlo SE at 0.95 is 0.0109, so 0.925 is 2.3 SE below nominal and about 3.1 SE below the
lowest expected value (0.959); the chance a correct report misses it in any one of the nine gated
cells is about 1e-3. The seeded defects land far below it (next section).

**Why `min_followups = 10`.** A policy floor, not a test: below ten, even the most favourable count
(10/10) cannot exclude a rate of 0.69 at 95%, and the number people would quote is mostly noise.
The report keeps counting below it; it only stops stating a rate. The current report states a rate
from one follow-up; that is the behaviour this replaces.

**Seeded defects.** Each is a plausible shortcut; the gate must fail under every one.

| id | defect | must be caught by | expected under the defect |
|---|---|---|---|
| `wald_interval` | the rate interval is Wald (p̂ ± 1.96·√(p̂(1−p̂)/n)) | exact coverage; stream coverage | exact min < 0.1; `high` coverage ≈ 0.54 |
| `undecidable_as_agreement` | an undecidable follow-up counts as a decidable one whose flags all agree | accounting; stream coverage | `large` acted-worthwhile coverage ≈ 0.02 |
| `drop_undecidable` | undecidable follow-ups are dropped before the report sees them | accounting; bounds coverage | `large` bounds coverage ≈ 0.35 |
| `no_minimum` | rates are stated from any number of follow-ups | refusal | every `small` stream below 10 |
| `unresolved_reads_as_no_difference` | fewer than `min_discordant` discordant pairs reads as `no_difference_detected` | exact paired; stream paired | every `high` stream |

## Pass bars

```toml
[meta]
suite = "C0"
version = 1
registered = "2026-09-26"

[report]
level = 0.95
min_followups = 10
alpha = 0.05
min_discordant = 6               # derived: smallest d with 2 * 0.5**d <= alpha

[exact]
n_max = 200
p_grid = 199                     # p = 0.005, 0.010, ..., 0.995
coverage_tolerance = 1e-9
h0_tolerance = 1e-12

[pool]
seed = 20260926
per_cell = 40
n_rows = 1000
benefit = 12.0
minimum_worthwhile_effect = 2.0
harm_threshold = 2.0
type_violations_allowed = 0

[streams]
seed = 20260927
reps = 400
coverage_configs = ["medium", "large", "high"]
min_coverage = 0.925
min_stated_streams = 300
min_refused_streams = 50         # `small` only: each branch looked at

[[streams.config]]
name = "small"
k = 12
share = { works = 0.60, null = 0.25, harms = 0.15 }
rollout = { works = 0.40, null = 0.10, harms = 0.10 }

[[streams.config]]
name = "medium"
k = 30
share = { works = 0.60, null = 0.25, harms = 0.15 }
rollout = { works = 0.40, null = 0.10, harms = 0.10 }

[[streams.config]]
name = "large"
k = 200
share = { works = 0.60, null = 0.25, harms = 0.15 }
rollout = { works = 0.40, null = 0.10, harms = 0.10 }

[[streams.config]]
name = "high"
k = 15
share = { works = 0.95, null = 0.05, harms = 0.0 }
rollout = { works = 0.0, null = 0.0, harms = 0.0 }

[accounting]
violations_allowed = 0

[refusal]
violations_allowed = 0

[paired]
violations_allowed = 0

[seeded_defects]
required = [
  "wald_interval", "undecidable_as_agreement", "drop_undecidable",
  "no_minimum", "unresolved_reads_as_no_difference",
]
```

## What this gate does not establish

- **Anything about real operations.** Every stream is simulated. No calibration figure about the
  platform's thresholds comes from this gate, and none may be quoted as if it did. The register
  entry `decision-thresholds-uncalibrated` leaves only on real follow-ups.
- **Whether the thresholds are right.** It checks the report about them, not them.
- **The stream-level truth of the two interval-agreement rates** (point inside the predicted
  interval, overlap). Their counts are gated (accounting) and their interval method is gated
  (Part 1), but what fraction they *should* be depends on the kernel's estimator distribution, not
  on anything set by construction here.
- **That the later re-run is the right comparison.** `compare` and the plan's frozen query are
  pinned by `tests/test_outcome.py` and the backend follow-up tests; this gate takes the
  realizations as given.
- **Dependence between decisions.** Decisions in a stream are independent by construction. Real
  decisions share incidents, seasons and teams; correlated follow-ups would make every interval here
  too narrow, and this gate cannot see that.
- **Power of the paired comparison on real data.** Its false-alarm rate is bounded exactly
  (Part 1); how many follow-ups it needs to detect a given over-claiming rate depends on how often
  real predictions sit near the bar.

## Changelog

- v1 (2026-09-26): initial registration.
