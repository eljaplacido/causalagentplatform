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

The kernel treats each block as a cluster — the unit that was randomized — and reuses T7's
cluster-robust analysis (CR2 intervals on `t(K − 2)`, `K` the number of blocks). A declared
**washout** drops, from every block whose arm differs from the block before it, the minutes within
`washout` of its start. The estimand is the steady-state effect: what the system would do running
one arm permanently.

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
| `minute_level_se` | minutes are treated as independent observations | coverage, `instant` |

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
