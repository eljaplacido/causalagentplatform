# T8 switchback designs gate — pre-registration

**Suite:** T8, experiments that switch the whole system between arms in randomized time blocks
**System under test:** `packages/causal-core` (`causal_core.cluster`, reached through
`analyze(..., design="randomized", cluster=<block>, time=<minute>, washout=<minutes>)`)
**Registered:** 2026-09-25, version 1; version 2 on 2026-09-26 (see Changelog)
**Runs:** every PR, via `pytest -m t8` in `packages/causal-core`

## Status of this document

Written and committed before switchback support exists in the kernel. No bar below was chosen by
looking at output from the code under test. Version 2's additions (a carryover check with a gated
power and false-alarm rate, and a flag that changes the result's status) were written and
committed before the code that implements them existed. The power figures quoted for them come
from a calculation on paper (below) and from a simulation of the proposed statistic, written
outside the kernel, on development seeds 4040, 5050 and 55 — never on this document's seed.

## What this gate is for

Some changes cannot be split by service or by user: a new autoscaling policy, a routing algorithm, a
cache configuration — there is one system and it runs one of them at a time. The experiment is then
a **switchback**: time is cut into blocks (an hour, say), each block is randomized to an arm, and
the whole system runs that arm for the block.

Two things make the naive analysis wrong. **Carryover**: after a switch the system takes time to
settle (caches warm, queues drain, autoscalers catch up), so the first minutes of a block still
carry the previous arm. **Dependence**: minutes within a block share a shock, and a day has a cycle
that both arms ride; minutes are not independent observations.

The kernel treats each block — the unit that was randomized — as one observation: a declared
**washout** drops the first `washout` minutes of *every* block, each block is summarised by its
mean, and the effect is the difference of the arms' block means (time-weighted, every block
counting equally), with a Welch variance across blocks and a `t(K − 2)` critical value, `K` the
number of blocks. The estimand is the steady-state effect: what the system would do running one
arm permanently. (See the Changelog for why the washout is uniform and the weighting by block.)

## Design

**Streams.** 7 days of minutes (10,080), blocks of 60 minutes (168 blocks), each block treated with
probability 0.5 independently (a draw with every block in one arm is redrawn). Per-minute outcome

`Y_t = 10 + sin(2πt / 1440) + τ·r_t + ε_t`,

with a daily cycle of unit amplitude, `ε_t` AR(1) at the minute level with coefficient `ρ` and unit
marginal variance, and `r_t` the carryover response: after a switch into treatment it rises as
`1 − exp(−s/λ)`, after a switch out it decays as `exp(−s/λ)`, `s` minutes since the switch
(`λ = 0`: instantaneous). The kernel is given `washout = 20` minutes in every design.

| design | `τ` | `λ` (min) | `ρ` | what it tests |
|---|---|---|---|---|
| `instant` | 0.3 | 0 | 0.8 | no carryover |
| `carryover` | 0.3 | 5 | 0.8 | carryover the 20-minute washout covers (residual under 2%) |
| `null_effect` | 0 | 5 | 0.8 | coverage of zero |
| `strong_autocorrelation` | 0.3 | 5 | 0.97 | noise correlated across adjacent blocks (lag-60 correlation ≈ 0.16) |
| `long_carryover` | 0.3 | 30 | 0.8 | carryover the washout does NOT cover — reported, not gated |
| `material_carryover` | 1.5 | 30 | 0.8 | the same uncovered carryover with an effect large enough that it matters (v2) |

**Truth** is `τ`, the steady-state effect. **Replications:** 1,000 per design, seeded from `seed`.

**The carryover check (v1: reported, not gated).** In v1 the kernel compared the effect in the
first `washout` minutes after the washout of switched blocks with the effect later in blocks, at
`refutation_alpha` (0.01), and only reported it. v1 run 1 measured it: flagged in 0.7–1.8% of runs
where the washout covers the carryover, and in 2.0% of `long_carryover` runs, where the estimate was
biased by −0.086 and covered 0.887.

**The carryover check (v2: gated, and it changes the result).**

*Statistic.* For each *switched* block (its arm differs from the previous block's), the
least-squares slope of the outcome on minutes since the block began, over the retained minutes
`[washout, block end)`. With no carryover left after the washout, the expected slope is the same
in blocks switched into treatment as in blocks switched out of it (the daily cycle is common to
both, and which blocks switch is randomized); a carryover still settling makes the effect rise
through blocks switched in and fall through blocks switched out, so the two mean slopes separate,
in opposite directions. The statistic is the difference of mean slopes, into minus out, with a
Welch variance across blocks and a two-sided `t` on `K_s − 2` degrees of freedom, `K_s` the number
of switched blocks — the same block-level inference as the estimate, the block the independent
observation. The check needs at least two switched blocks in each direction; with fewer it is
reported as not run and flags nothing.

*Level.* A new policy field, `carryover_alpha`, default **0.05**, looser than `refutation_alpha`
(0.01) on purpose, as `segment_alpha` is: a missed carryover leaves a biased number marked
`supported`, while a false alarm only withholds the causal reading of a number that is still
reported.

*What a flag does.* The result's status becomes **`contested`** (the status for a failed
falsification test), with the check failed in `diagnostics.checks` and a warning naming it. The
estimate and its interval are unchanged and reported: the check changes what the number may be
used for, not the number. Downstream, a contested result cannot be described in causal language
(`causal_core.claims`) and the decision layer does not act on it (D0's P6).

*Power, on paper.* Take a switched block whose previous block had settled. The residual
carryover in its retained window is `τ·exp(−s/λ)`, `s ∈ [20, 60)`; its least-squares slope is
`0.00934·τ` per minute at `λ = 30` and `0.0003·τ` at `λ = 5`. Under minute-level AR(1) with
`ρ = 0.8` and unit variance, the slope's standard deviation over 40 minutes is 0.0340 (exact, from
the AR(1) covariance). With 168 blocks switching with probability ½, about 42 switch each way, so
the into-minus-out difference has signal `2 × 0.00934·τ` and standard error
`0.0340·√(2/42) = 0.0074`: `z ≈ 2.5·τ`, reduced by about 7% because the previous block has itself
not always settled (`e^{−60/30}` of the step remains when it too was switched), so
`z ≈ 2.3·τ`. At `λ = 5` the signal is 3% of that: the check's rejection rate there is its level.

| design | `z` on paper | power at 0.05, on paper | development seeds (3 × 1,000) |
|---|---|---|---|
| `long_carryover` (`τ = 0.3`) | 0.70 | 0.11 | 0.088, 0.127, 0.100 |
| `material_carryover` (`τ = 1.5`) | 3.5 | 0.94 | 0.919, 0.920, 0.904 |
| covered designs (`λ ≤ 5`) | ≈ 0 | 0.05 | 0.040–0.075 (highest: `strong_autocorrelation`) |

*Why not `long_carryover`.* No check that looks only at the minutes after the washout can reach a
power of 0.8 there. Even an oracle that knows `λ = 30` and `ρ = 0.8` — a generalised-least-squares
matched filter for the exact residual shape — has `z ≈ 0.84` on paper (0.77 in the development
simulation), a power near 0.13. The residual carryover in the retained window is about 0.09 against
unit minute-level noise. The minutes *inside* the washout carry more information, but also carry
the carryover the washout was declared to absorb, so a check using them fires on every covered
carryover as well (see `check_includes_washout` below). The honest statement is therefore: at
these sizes the check reliably catches a carryover whose bias is several standard errors of the
estimate (`material_carryover`: bias about −0.40, three standard errors, coverage near 0.13 if
unchecked), and not one whose bias is a fraction of a standard error (`long_carryover`: 0.66).
`long_carryover`'s rejection rate stays reported, not gated.

*Estimation bars when the check fires.* Bias and coverage in the four gated designs are computed,
as in v1, over every replication's estimate, flagged or not: a contested result carries the same
estimate and interval as it would have supported. The estimation designs and bars are v1's,
unchanged. In `long_carryover` and `material_carryover` bias and coverage are reported, not gated
(the estimate is biased by design there; the gate is whether that is caught).

*Status bar.* In every design, every replication whose carryover check failed must have status
`contested`, and every replication whose check passed must not (these graphs have no unreviewed
edges and there are no other falsification tests on this path, so it is `supported`).

**Refusals.**

| case | data | reason |
|---|---|---|
| `varies_within_block` | a treated block with a quarter of its minutes untreated | `DESIGN_CHECK_FAILED` |
| `washout_consumes_blocks` | `washout = 60` with 60-minute blocks | `INVALID_INPUT` |
| `too_few_blocks` | 8 blocks (4 per arm) | `LOW_N` |
| `not_randomized` | `design="observational"` | `INVALID_INPUT` |

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `no_washout` | the washout is ignored | bias, `carryover` |
| `minute_level_se` | minutes are treated as independent observations (the variance of minute-level means) | coverage, `instant` |
| `carryover_check_blind` | the check tests whether the mean slope over all switched blocks is zero, not signing by direction — carryover into and out of treatment cancels (v2) | power, `material_carryover` |
| `check_includes_washout` | the check's slopes are fitted over the whole block, washout included, so it sees the carryover the washout absorbs (v2) | false alarms, `carryover` |
| `flag_not_gating` | the check is computed and reported but the status stays `supported` — v1's behaviour (v2) | status, `material_carryover` |

## Pass bars

```toml
[meta]
suite = "T8"
version = 2
registered = "2026-09-26"

[streams]
days = 7
block_minutes = 60
washout = 20
reps = 1000
seed = 20260928

[estimation]
designs = ["instant", "carryover", "null_effect", "strong_autocorrelation"]
bias_mcse_max = 3.0
coverage_band = [0.93, 0.97]

[carryover]
alpha = 0.05
false_alarm_designs = ["instant", "carryover", "null_effect", "strong_autocorrelation"]
false_alarm_max = 0.10
power_designs = ["material_carryover"]
power_min = 0.8
reported = ["long_carryover"]
flagged_status = "contested"

[refusals]
must_refuse = ["varies_within_block", "washout_consumes_blocks", "too_few_blocks",
               "not_randomized"]

[seeded_defects]
required = ["no_washout", "minute_level_se", "carryover_check_blind", "check_includes_washout",
            "flag_not_gating"]
```

**Why these bars.** 1,000 replications put a 95% coverage estimate's SE near 0.007, so the band is
about ±2.9 SE. Ignoring a 20-minute washout under `λ = 5` biases the estimate by about −0.025,
roughly six Monte Carlo SEs at this sample size; treating minutes as independent, with minute-level
AR(1) at 0.8 and a daily cycle, understates the block-level variance several-fold.

v2's carryover bars. **Power ≥ 0.8** in `material_carryover`: expected about 0.94 on paper and
0.90–0.92 on development seeds; at 1,000 replications a rejection rate near 0.9 has a Monte Carlo
SE near 0.009, so the bar sits more than ten SEs below the expectation and a check whose power fell
to the conventional 0.8 would sit at it. **False alarms ≤ 0.10** in each covered design: the
check's level is 0.05; at 1,000 replications a rate of 0.05 has an SE of 0.007, so 0.10 is about
seven SEs above the level. It is set at twice the level rather than at it because the Welch `t`
is approximate with about 40 blocks per group and adjacent blocks are not quite independent: the
development seeds gave 0.056–0.075 in `strong_autocorrelation` (noise correlated across the
washout into the neighbouring block), inside the bar with room; a check whose false-alarm rate
doubled would reach it. `check_includes_washout` gave 0.145–0.163 in `carryover` on development
seeds, four or more SEs past the bar. **Status**: exact — a single flagged-but-supported or
unflagged-but-contested replication fails it. `carryover_check_blind` gave 0.029–0.043 power in
`material_carryover` (into and out cancel), far below 0.8.

**What this gate does not establish.**

- That a washout is long enough on real systems. The v2 check catches a carryover large enough to
  matter to the conclusion (`material_carryover`) and **not** one whose bias is small next to the
  estimate's standard error (`long_carryover`, power about 0.1): a passed carryover check is weak
  evidence, and the washout must still be chosen from knowledge of the system.
- Carryover of other shapes (a delayed response, an overshoot, a transient that is the same after
  every switch). The check looks for an effect still moving through the retained window,
  in opposite directions after opposite switches; a transient common to both directions biases
  nothing in the difference of arms and is not looked for.
- Optimal block lengths, or designs balanced over the daily cycle. Blocks are randomized
  independently here.
- Carryover longer than one block. The analysis assumes an effect settles within a block.

## Changelog

- v1 (2026-09-25): initial registration.
- v1, method correction before any run (2026-09-25). The registration first described the washout
  as applying only to blocks after a switch, with T7's unit-weighted estimate and CR2 variance.
  Development seeds (4040, 5050, 55 — not this document's), with no T8 run yet made, showed that
  design under-covering: 0.893–0.906 in `instant`, `carryover` and `strong_autocorrelation`, the
  standard error about 16% below the spread of estimates, and the cause isolated to the washout —
  without it coverage was 0.957–0.967. Trimming only switched blocks makes a block's retained
  minutes, and so its weight and the part of the daily cycle it covers, depend on its
  *neighbour's* arm; the blocks stop being independent observations. Dropping the first `washout`
  minutes of every block and weighting blocks equally removes that dependence: the same seeds then
  gave 0.938–0.950, with the standard error within Monte Carlo error of the spread. The method text
  above is corrected accordingly; the designs, bars, refusals and defect ids are unchanged.
- v2 (2026-09-26): the carryover check is gated and changes the result (register entry
  `carryover-check-weak`; ledger septa-9). Prompted by v1 run 1 (`results/2026-09-25-v1-run1.json`),
  where the reported check flagged 2.0% of `long_carryover` runs. The check becomes a slope test
  over the retained window of switched blocks at a new policy level `carryover_alpha` = 0.05 (it
  was an early-minus-late comparison at `refutation_alpha` = 0.01; on paper the slope carries
  about 15% more signal against this carryover, and the level is the larger part of the change); a
  flag makes the result `contested`. Added: design `material_carryover`; the `[carryover]` bars
  (power ≥ 0.8 there, false alarms ≤ 0.10 in the four covered designs, status); seeded defects
  `carryover_check_blind`, `check_includes_washout`, `flag_not_gating`. Unchanged: the streams, the
  seed, the five v1 designs, the estimation bars (over every replication, flagged or not), the
  refusals and the two v1 defects. Recorded, not hidden: no check on the retained minutes has
  power near 0.8 against `long_carryover` (oracle about 0.13), so that design stays reported.
