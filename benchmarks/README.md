# benchmarks/

The pre-registered correctness gates for the causal kernel and the decision layer, and every run
they have produced. A gate here is how this repository earns the right to say a number is correct:
the bars are fixed before the code is run against them, and the runs that failed are kept.

## The gates

| Directory | Gate | What it tests | System under test | Runs |
|---|---|---|---|---|
| `t0/` | T0 | Analytic structural causal models with closed-form truth: identification against the literature and y0, estimator bias and coverage, refuter false-alarm rate and power | `packages/causal-core` | every PR: `pytest -m t0` |
| `t2-ihdp/` | T2 | Semi-synthetic: real IHDP covariates with simulated potential outcomes | `causal_core.analyze` | CI, and on demand; needs the data cached or `CAUSAL_FETCH=1` |
| `t3-lalonde/` | T3 | Real observational data against an experimental benchmark (LaLonde / NSW) | `causal_core.analyze` | CI, and on demand; needs the data cached or `CAUSAL_FETCH=1` |
| `t4-segments/` | T4 | Effects within declared segments: per-segment and family-wise coverage, heterogeneity test, Holm | `causal_core.segments` via `analyze` | every PR: `pytest -m t4` |
| `d0-decision-policy/` | D0 | The decision procedure on outcomes whose evidence is known by construction: a scenario table and properties | `packages/causal-decision` | every PR: `pytest -m d0` |

Each directory holds a `PRE-REGISTRATION.md` and a `results/` directory. The gate code lives in the
packages: `packages/causal-core/src/causal_core/bench/` and
`packages/causal-decision/src/causal_decision/bench/`, with tests under each package's `tests/`.

## The discipline

1. **Freeze the bars first.** `PRE-REGISTRATION.md` states the system under test, the cases, the
   pass bars as a fenced TOML block (`[meta]` with `suite`, `version`, `registered`, then one table
   per section and `[seeded_defects]`), and a section saying what the gate does *not* establish. It
   is committed before the capability produces a result -- the git history of `t4-segments/` shows
   the order: pre-registration, then the code. The gate reads its bars from that TOML block, so
   there is exactly one copy of each.
2. **Keep failing runs.** Every run is committed, pass or fail, and none is deleted or overwritten.
   `t0/results/2026-09-19-v1-run1.json` and `t2-ihdp/results/2026-09-19-v1-run1.json` failed; they
   are why T0 is at version 3 and why the AIPW solver changed.
3. **Bars change only with a version bump and a changelog.** Increment `[meta] version` and add a
   dated entry to the pre-registration's `## Changelog` saying what changed, why, and which run
   prompted it. Runs under an old version stay, named with their version.
4. **Seeded defects.** Every gate registers plausible wrong implementations
   (`[seeded_defects] required`), each with the bar that must catch it, and a test asserts the gate
   fails under every one. A gate that has never been seen to fail proves nothing. A defect the
   suite turns out not to see is recorded as a limit (T4 v2 is the example), not quietly dropped.

## Result files

```
<gate>/results/<YYYY-MM-DD>-v<pre-registration version>-run<N>.json
```

for example `t0/results/2026-09-19-v3-run2.json`: the second run on that date against version 3.
Each file records the suite, `prereg_sha256` (a hash of the pre-registration it was judged by),
`prereg_version`, the `git` commit it ran on (with `-dirty` if the tree was), the per-section
figures, and whether it passed.

## Running

The gates run inside `.gnomon/ci.sh`, in each package's row. To run one by hand (kernel venv, from
the repository root; see `docs/QUICKSTART.md` for setting it up):

```bash
cd packages/causal-core && .venv/bin/pytest -m t0      # or -m t4, or -m real_data (T2, T3)
cd packages/causal-decision && ../causal-core/.venv/bin/pytest -m d0
```

To write a results file:

```bash
V=packages/causal-core/.venv/bin/python
$V -m causal_core.bench.t0      --out benchmarks/t0/results/<date>-v<version>-run<N>.json
$V -m causal_core.bench.t4      --out benchmarks/t4-segments/results/<date>-v<version>-run<N>.json
$V -m causal_core.bench.ihdp    --out benchmarks/t2-ihdp/results/<date>-v<version>-run<N>.json
$V -m causal_core.bench.lalonde --out benchmarks/t3-lalonde/results/<date>-v<version>-run<N>.json
$V -m causal_decision.bench.d0  --out benchmarks/d0-decision-policy/results/<date>-v<version>-run<N>.json
```

T0 needs the `crosscheck` extra (y0). T2 and T3 download their data by pinned SHA-256 into
`~/.cache/causal-core` (or `$CAUSAL_CORE_CACHE`) when `CAUSAL_FETCH=1`; neither dataset states a
licence, so the data is never committed.

The working rules for agents are in `.gnomon/skills/benchmark-discipline.md`.
