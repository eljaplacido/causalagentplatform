# T3 LaLonde benchmark — pre-registration

**Suite:** T3, real data with an experimental benchmark
**System under test:** `causal_core.analyze`
**Registered:** 2026-09-19, version 1
**Runs:** every release, and on demand with `python -m causal_core.bench.lalonde`. It needs network
access or a warm cache.

## Status of this document

This was written before causal-core was run on these data. The only things looked at so far are
descriptive:

- file sizes and column names;
- mean 1978 earnings by group. In the experimental sample these are 6,349 for the treated and 4,555
  for the controls, a difference of 1,794. The PSID controls average 21,554 and the CPS controls
  14,847.

The benchmark reads every threshold from the TOML block below. Each results file records this
file's SHA-256.

## Why this benchmark

LaLonde (1986) showed that econometric estimators applied to observational comparison groups failed
to recover the effect of the National Supported Work (NSW) job-training programme. The effect was
known from a randomized experiment. It remains the standard test of whether a method gives
confident wrong answers on real data. Dehejia & Wahba (1999, 2002) supply:

- the experimental subsample used here (185 treated, 260 controls);
- two observational comparison groups: PSID-1 (2,490) and CPS-1 (15,992).

What it tests. The property that matters for trust is **not** "always recovers the experimental
number". The observational data may simply not support that, and the literature shows results that
are sensitive to specification (Smith & Todd 2005). The property is that the kernel never makes a
**confident wrong** claim. A `supported` result that disagrees significantly with the experiment is
a failure. So is a missing refusal where one was warranted. A refusal or a `contested` result is not
a failure.

## Data

The files are fetched from `https://users.nber.org/~rdehejia/data/` and must match these SHA-256s:

| file | sha256 |
|---|---|
| `nsw_dw.dta` | `d1bd2680a1c6f799f1c6d2455bf29633fdf19be01cb19490621c20a560b4e072` |
| `psid_controls.dta` | `7beebae8928035d6abf662b994ce77a4fbea7ae6575037ff94e92fe099b43e14` |
| `cps_controls.dta` | `80f0123eaf723870bd060ba9f8569617a7cb519a462dc20badbfb3443cd8374b` |

**Licence.** None is stated. The page says the authors received the original author's "permission
to publish it here". The data are therefore **not redistributed** in this repository. They are
downloaded on demand and cached outside the repository.

**Variables.**

- Outcome: `re78`, earnings in 1978.
- Treatment: `treat`.
- Covariates: `age`, `education`, `black`, `hispanic`, `married`, `nodegree`, `re74`, `re75`, and
  the zero-earnings indicators `u74 = 1[re74 = 0]` and `u75 = 1[re75 = 0]`, as in Dehejia & Wahba.

## Designs

- **E — experiment.** The NSW-DW sample, analysed with `design = randomized` and `estimand = att`.
  Under randomization the ATT equals the ATE, and this is the benchmark. The graph gives the
  covariates as causes of `re78` only.
- **O-PSID and O-CPS — observational.** The NSW-DW treated units are pooled with one comparison
  group. The estimand is the ATT. The graph has every covariate → `treat` and → `re78`, plus
  `treat` → `re78`. Identification is therefore back-door on all ten covariates.
- **Configurations.**
  - `C1`: the default `AnalysisPolicy()` (linear nuisance models).
  - `C2`: `learners = "flexible"` (gradient-boosted nuisance models; needs the `ml` extra).
- **Placebo, reported only.** The outcome is `re75` (earnings before the programme). Covariates are
  as above, minus `re75` and `u75`, on each observational design under `C1`. A clearly non-zero
  "effect" on a pre-programme outcome signals selection that the covariates do not capture.

  It is reported rather than gated. Its validity assumes that selection does not act on the
  placebo outcome itself, and NSW eligibility was partly based on recent unemployment.
- **Naive baselines, reported only.** These are computed directly, not by the kernel:
  - the difference in means;
  - OLS of `re78` on `treat` and the covariates.

  They show the size of the problem.

## Pass bars

```toml
[meta]
suite = "T3-LaLonde"
version = 1
registered = "2026-09-19"

[data]
base_url = "https://users.nber.org/~rdehejia/data/"
nsw_dw = "d1bd2680a1c6f799f1c6d2455bf29633fdf19be01cb19490621c20a560b4e072"
psid_controls = "7beebae8928035d6abf662b994ce77a4fbea7ae6575037ff94e92fe099b43e14"
cps_controls = "80f0123eaf723870bd060ba9f8569617a7cb519a462dc20badbfb3443cd8374b"

[experiment]
published_point = 1794.34        # Dehejia & Wahba (1999), difference in means
max_abs_deviation = 1.0          # data-integrity check: our DiM must reproduce it within $1

[safety]
# A `supported` observational result that disagrees with the experiment beyond sampling noise
# (|obs - exp| > z * sqrt(se_obs^2 + se_exp^2)) is a confident wrong answer. There must be none,
# across both designs and both configurations. z is Bonferroni-corrected over the 4 combinations
# (two-sided family alpha 0.05 -> 2.498), so a correct kernel fails by chance at most 5% of runs.
z = 2.498
max_confident_wrong = 0

[reported]
# Not gated; reported so the result can't be read as more than it is.
bias_in_rct_se = true
covers_experimental_point = true
placebo_re75 = true
naive_baselines = true
```

**Why the safety bar and not a usefulness bar.** The roadmap proposed "|bias| ≤ 1 RCT SE after
trimming for overlap". That rewards a method for being lucky on one dataset, and trimming changes
the estimand. The gate here holds the kernel to its own contract instead: when the data cannot
support a confident answer, it must not give one. How close the estimates come to the experiment
is still reported in full.

## Changelog

- v1 (2026-09-19): initial registration.
