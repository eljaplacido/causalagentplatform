# T8 switchback designs gate — pre-registration

**Suite:** T8, experiments that switch the whole system between arms in randomized time blocks
**System under test:** `packages/causal-core` (`causal_core.cluster`, reached through
`analyze(..., design="randomized", cluster=<block>, time=<minute>, washout=<minutes>)`)
**Registered:** 2026-09-25, version 1
**Runs:** every PR, via `pytest -m t8` in `packages/causal-core`

## Status of this document

Written and committed before switchback support exists in the kernel. No bar below was chosen by
looking at output from the code under test.

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

**Truth** is `τ`, the steady-state effect. **Replications:** 1,000 per design, seeded from `seed`.

**The carryover check (reported, not gated).** The kernel also compares the effect in the first
`washout` minutes after the washout of switched blocks with the effect later in blocks, and reports
the difference with its interval. Its rejection rates are reported for every design, including
`long_carryover`; v1 sets no bar on them, because its power at these sizes was not worked out
before registration.

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

## Pass bars

```toml
[meta]
suite = "T8"
version = 1
registered = "2026-09-25"

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

[refusals]
must_refuse = ["varies_within_block", "washout_consumes_blocks", "too_few_blocks",
               "not_randomized"]

[seeded_defects]
required = ["no_washout", "minute_level_se"]
```

**Why these bars.** 1,000 replications put a 95% coverage estimate's SE near 0.007, so the band is
about ±2.9 SE. Ignoring a 20-minute washout under `λ = 5` biases the estimate by about −0.025,
roughly six Monte Carlo SEs at this sample size; treating minutes as independent, with minute-level
AR(1) at 0.8 and a daily cycle, understates the block-level variance several-fold.

**What this gate does not establish.**

- That a washout is long enough on real systems: `long_carryover` shows what happens when it is
  not, and the carryover check is reported, not gated, in this version.
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
