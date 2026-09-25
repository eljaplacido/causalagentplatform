# T6 anytime-valid monitoring gate — pre-registration

**Suite:** T6, confidence sequences for randomized rollouts that are watched while they run
**System under test:** `packages/causal-core` (`causal_core.sequential`, reached through
`analyze(..., design="randomized", sequence=<arrival-order column>, expected_treated_share=p)`)
**Registered:** 2026-09-25, version 1; version 2 the same day (see Changelog)
**Runs:** every PR, via `pytest -m t6` in `packages/causal-core`

## Status of this document

Written and committed before `causal_core.sequential` exists. No bar below was chosen by looking
at output from the code under test.

## What this gate is for

A canary is watched. Someone looks at the dashboard after every batch and stops the rollout the
moment the interval clears zero — in either direction. A 95% interval computed for a fixed sample
size means nothing under that behaviour: checked at 40 looks, it excludes a true null effect at
*some* look far more often than 5% of the time, and "the canary looked harmful at 10:40" becomes a
coin toss dressed as evidence.

The kernel reports, at every look, an **asymptotic confidence sequence** (Waudby-Smith, Arbour,
Sinha, Kennedy & Ramdas 2023): an interval sequence whose coverage holds *simultaneously over all
looks*, so stopping whenever it excludes zero keeps its error rate. The estimate is the average of
an inverse-probability score with a *predictable* baseline — each unit's outcome centred on the
mean of the outcomes before it — which is unbiased under randomization with a known allocation,
even when the outcome's level drifts over the day, and much less variable than the raw score.

The price is width: at the sample size it is tuned for, a confidence sequence is about 1.55 times
wider than a fixed-n interval. That is the cost of being allowed to look, and the bars below
are set with it in mind rather than pretending otherwise.

## Design

**Streams.** 4,000 units arrive in order; each is assigned to treatment with probability `p`,
independently. Looks are taken every 100 units from unit 200 (the burn-in) to 4,000: 39 looks. The
confidence sequence is tuned for 2,000 units, at `α = 0.05` (the kernel's `ci_level` of 0.95).

| design | `p` | outcome | effect `τ` | what it tests |
|---|---|---|---|---|
| `null` | 0.5 | `10 + N(0, 1)` | 0 | coverage over every look when nothing happens |
| `benefit` | 0.5 | `10 + N(0, 1)` | 0.2 | power to see a real improvement |
| `harm` | 0.5 | `10 + N(0, 1)` | −0.25 | power to see harm, and how soon |
| `skewed_null` | 0.5 | `10 + (LogNormal(0, 0.75) − its mean)` | 0 | a heavy right tail, as latency has |
| `drifting_null` | 0.5 | `10 + 2·(i / 4000) + N(0, 1)` for the i-th unit | 0 | a level that moves during the run, in both arms |
| `unequal_allocation` | 0.2 | `10 + N(0, 1)` | 0.2 | a canary that gives the change to one unit in five |

**Truth** is `τ`. **Replications:** 500 per design, seeded from `seed`.

**Sample-ratio refusals.** The kernel refuses a stream whose treated share is implausible under the
declared allocation (chi-square, α = 0.001), because then the assignment is not what the analysis
assumes. A correctly randomized stream is refused that way about 0.1% of the time, by design.
Coverage and power are computed over the replications that produced a sequence; the share refused
is reported, and bounded by `srm_refusals_max` per design.

**Refusals** (one constructed stream each):

| case | stream | reason |
|---|---|---|
| `not_randomized` | `design="observational"` with `sequence` set | `INVALID_INPUT` |
| `allocation_unknown` | no `expected_treated_share` | `INVALID_INPUT` |
| `repeated_order` | two units share an arrival position | `INVALID_INPUT` |
| `non_binary_treatment` | treatment takes three values | `INVALID_INPUT` |
| `too_short` | 150 units, fewer than the burn-in | `LOW_N` |

**Seeded defects.**

| id | defect | must be caught by |
|---|---|---|
| `fixed_n_interval` | each look reports the ordinary fixed-sample 95% interval | uniform coverage, `null` |
| `log10_boundary` | the boundary's logarithm is base 10 instead of natural (v1 registered `boundary_without_mixture_term` here; see the Changelog) | uniform coverage, `null` |
| `ignore_allocation` | the score uses `p = 0.5` whatever the allocation | uniform coverage, `unequal_allocation` |

## Pass bars

```toml
[meta]
suite = "T6"
version = 2
registered = "2026-09-25"

[streams]
units = 4000
look_every = 100
burn_in = 200
target_n = 2000
reps = 500
seed = 20260926

[coverage]
designs = ["null", "benefit", "harm", "skewed_null", "drifting_null", "unequal_allocation"]
uniform_min = 0.93        # share of runs whose sequence contains τ at EVERY look

srm_refusals_max = 0.01   # share of correctly randomized streams refused as mismatched

[power]
benefit_min = 0.80        # share of runs excluding zero from above at some look
harm_min = 0.90           # share of runs excluding zero from below at some look

[refusals]
must_refuse = ["not_randomized", "allocation_unknown", "repeated_order",
               "non_binary_treatment", "too_short"]

[seeded_defects]
required = ["fixed_n_interval", "log10_boundary", "ignore_allocation"]
```

**Why these bars.** Nominal simultaneous coverage is 0.95; the sequence is asymptotic, so a
finite-sample shortfall is possible, and with 500 replications a rate near 0.95 has a standard
error near 0.010 — 0.93 is two of them below nominal. The power bars follow from the width
arithmetic: at the final look a sequence for `p = 0.5` and unit noise has half-width about 0.097,
so an effect of 0.2 is about two half-widths from zero, and a harm of 0.25 about two and a half.

**What this gate does not establish.**

- **Validity without randomization.** The score is unbiased only because assignment is random with
  a known probability; an observational stream is refused, not monitored.
- Guarantees at fewer units than the burn-in: the sequence is asymptotic, and the gate does not
  test it below 200 units.
- Anything about interference between units (one canary host slowing another) or about outcomes
  that arrive long after assignment. Both are real in operations and both are out of scope here.
- That stopping at the first exclusion is the *right* policy — that is the decision layer's
  question. This gate establishes that the evidence is valid whenever it is read.

## Changelog

- v1 (2026-09-25): initial registration.
- v1, text correction before any run (2026-09-25): the width ratio in "What this gate is for" read
  "roughly 2.5–3"; it is about 1.55 at the tuning size (a unit test of the boundary found it). No
  bar depends on it — the power bars were derived from the final half-width, 0.097, which is
  right — and no T6 run existed when the sentence was corrected.
- **v2 (2026-09-25): one seeded defect replaced, sample-ratio refusals counted. No bar changed.**
  v1 run 1 (`results/2026-09-25-v1-run1.json`) failed on two faults of this registration, not of
  the method: simultaneous coverage was 0.982–0.994 in every design, power 0.998 and 1.00, every
  refusal as registered, and two of the three defects caught.
  1. `boundary_without_mixture_term` covered 0.956 on `null`. The confidence sequence over-covers
     at this setting (as asymptotic sequences with a running intersection do), so a boundary
     narrowed that much is still valid, and a coverage bar is right not to fail it: it was not a
     defect. It is replaced by `log10_boundary` — `math.log10` for `math.log`, an easy slip that
     shrinks the boundary by a factor of about 0.66 and does break validity. Chosen after seeing
     v1, and disclosed as such; what a defect must do is fail the bar, and v1's could not.
  2. Four of 3,000 streams were refused for sample ratio mismatch, the check's designed 0.1%
     false-refusal rate on correctly randomized streams; v1 did not say how to count them and the
     harness failed on any refusal. They are now counted and bounded (`srm_refusals_max = 0.01`),
     and coverage and power are computed over the streams that produced a sequence.

  Not added, and stated rather than hidden: an *upper* bound on coverage. The sequence's
  conservatism (≈0.98–0.99 against 0.95) costs width, and a bar on it would be a width bar; that
  is an efficiency question for a later version, not a validity one.
