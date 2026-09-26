+++
name = "benchmark discipline"
description = "Freeze the bars before the run, keep every failing run, move a bar only with a version bump and a changelog, and prove the gate can fail before trusting a pass"
match = '\b(benchmark|benchmarks|pre-?registration|pre-?register\w*|bars?|gate|t0|t2|t3|t4|t5|t6|t7|t8|d0|a0|c0|ihdp|lalonde|seeded[- ]defects?|negative control|coverage|power|results?)\b'
roles = ["coordinator", "implementor", "implement", "plan", "critique", "verifier"]
+++

Adapted from gnomon's own benchmark-discipline skill (MIT, same author),
narrowed to this repository's `benchmarks/`. Every rule there was paid for by
a run that measured nothing; these are the ones that apply to a causal
kernel's correctness gates.

**1. Pre-register, then run -- in two commits.** `benchmarks/<gate>/PRE-REGISTRATION.md`
states the system under test, the cases, the pass bars as a fenced TOML block
(`[meta] suite/version/registered`, then one table per section, plus
`[seeded_defects] required = [...]`), and "what this gate does not establish".
It is committed BEFORE the capability produces a number -- see
`git log -- benchmarks/t4-segments/` for the shape: pre-register, then the
code. Choosing a bar after seeing output is the largest researcher degree of
freedom there is. The gate test reads its bars from that TOML block; a bar
written anywhere else is a second, unregistered bar. In the gnomon chain,
writing a pre-registration is the coordinator's job; the implementor's
write_allow excludes it.

**2. Keep every run, especially the failing ones.** Results go in
`benchmarks/<gate>/results/<YYYY-MM-DD>-v<prereg version>-run<N>.json`,
written by the gate's `--out` and carrying `prereg_sha256`, `prereg_version`
and the `git` commit it ran on (`-dirty` if the tree was). A failing run is
committed and never deleted or overwritten: `t0/results/2026-09-19-v1-run1.json`
and `t2-ihdp/results/2026-09-19-v1-run1.json` failed, and they are why v2 and
the pinv fix exist. A results directory with only passes is a finding.

**3. Bars move only with a version bump and a changelog.** Increment
`[meta] version`, add a dated `## Changelog` entry saying what changed, why,
and which run prompted it, and keep the old runs under their old version. If
an implementation misses a bar, the default is that the implementation is
wrong. A bar that turns out to be unmeasurable (T4 v2's defect that ran the
wrong way) is recorded as a limit, not quietly loosened.

**4. Prove the gate can fail.** Every gate has seeded defects -- plausible
wrong implementations, each registered with the bar that must catch it
(`causal_core/bench/defects.py`, `defects_t4.py`,
`causal_decision/bench/defects.py`). A test asserts the gate FAILS under each one. A
defect that the suite cannot see is a blind spot to write down, not a
defect to drop.

**5. Assert against known truth.** Cases are simulated from a structural
model whose effect is set by construction (every gate but T2 and T3; A0's
truth is exact algebra over the graphs), or real data with an experimental
benchmark (T3 LaLonde) or simulated outcomes on real covariates (T2 IHDP).
Never against what the code returned last time.

**6. Read the noise floor before a delta.** A coverage of 0.94 against a band
of 0.925-0.975 at 400 reps is inside Monte Carlo error; so is a one-run
change. State the reps and seed (they are in the TOML), and do not read a
difference between two runs smaller than their Monte Carlo SE as a finding.

**7. Record the apparatus.** Which commit, which venv, whether a dataset
came from cache. A green run that depends on a local cache
(`~/.cache/causal-core`, needed by T2/T3) measures that cache; say so, and
never commit the data -- IHDP and LaLonde state no licence.

**8. Report what a gate does not establish.** Each pre-registration has that
section. Quote it when you cite the gate; "T0 passes" is not "the estimator
is right for your data".

Pre-flight:

    [ ] PRE-REGISTRATION.md committed before any results file for its version
    [ ] bars in the TOML block, read by the gate test
    [ ] seeded defects registered, each asserted to fail the gate
    [ ] results file named <date>-v<version>-run<N>.json, with git + prereg_sha256
    [ ] failing runs committed, not overwritten
    [ ] any bar change: version bump + changelog entry
