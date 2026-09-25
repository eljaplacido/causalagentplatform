# D0 decision-policy gate — pre-registration

**Suite:** D0, the decision procedure on outcomes whose evidence is known by construction
**System under test:** `packages/causal-decision` (`causal_decision.decide`)
**Registered:** 2026-09-21, version 1; version 2 on 2026-09-25; version 3 the same day (see Changelog)
**Runs:** every PR, via `pytest -m d0` in `packages/causal-decision`

## Status of this document

Written before the gate was run. The policy code existed and had been exercised by hand on one
simulated operations case (a remediation playbook), which produced `act`, `experiment`, `refuse`
and `act_with_approval` as the thresholds moved. No bar below was chosen by looking at gate output.

## What this gate is for

The kernel's gates ask whether a number is right. This one asks something different and, for an
assurance layer, more dangerous to get wrong: **given evidence, may an agent act?** A decision
procedure fails in ways an estimator does not. It can be too permissive (automating on evidence
that does not support it), too timid (demanding an experiment when the answer is already clear),
or incoherent (the same evidence yielding different verdicts depending on the order checks ran).

The procedure is a pure function of (outcome, request, policy), so every case here is exactly
reproducible and the properties below are checkable rather than aspirational.

## Design

**Scenario table.** Each scenario fixes an evidence state and a request, and names the verdict the
policy must return. The evidence states come from real `causal_core.analyze` runs on simulated data
wherever a real analysis can produce that state; the remaining ones (an apparatus failure, a result
with no interval) are constructed contract objects, because no honest analysis produces them on
demand. The evidence builders are in `causal_decision/bench/d0.py` (`SCENARIOS`); **the verdict
each scenario must return is registered here**, in `[table.expected]` below, and the gate fails if
the code's table and this one differ in any id or any verdict. (In version 1 the expected verdicts
lived only in the code, where a change to `decide()` and a change to what it was expected to do
could land in the same commit without this document's hash moving.)

Two scenarios declare segments (`segment_by = ("severity",)`), on incident data where the playbook
takes 20 minutes off P1 incidents (65% of them) and adds 6 to P3:

- `harmed_segment` — the pooled saving is large, precise and past the bar, and every pooled check
  passes; the P3 segment is still made materially worse. Expected `refuse`: the policy's ceiling
  for a declared segment whose family-wise interval lies wholly beyond the harm threshold, on a
  supported estimate.
- `uniform_segments` — the same design with a 12-minute saving in both segments. Expected `act`:
  declared segments that agree cap nothing.

Two scenarios (v3) are staggered rollouts analysed by difference-in-differences (T5's simulator,
`staggered_constant` cohorts, drawn membership, parallel trends holding), with the request's
minimum worthwhile effect set to 0.2 and direction `increase`:

- `did_robust_to_trends` — effect 2. The conclusion survives a parallel-trends violation several
  times the largest pre-adoption deviation (breakdown well above 1). Expected
  `act_with_approval`: tier C identification never automates, whatever the robustness.
- `did_fragile_to_trends` — effect 0.5. The interval clears the bar, but a violation *smaller* than
  the pre-adoption deviations the data already shows would reach zero (breakdown below 1).
  Expected `experiment`: the conclusion rests on trends holding more tightly than the data can
  show, and a randomized test is what settles that.

**Properties.** Checked on a seeded sweep of requests and policies (`PROPERTIES`), not only on the
table:

1. **Irreversible actions are never automated.** No `act` when `reversible` is false, at any risk
   class, on any evidence.
2. **Risk monotonicity.** Raising the risk class never increases autonomy.
3. **Bar monotonicity.** Raising the minimum worthwhile effect never increases autonomy.
4. **Evidence monotonicity.** Narrowing the interval around the same point estimate never
   decreases autonomy.
5. **No estimate, no action.** When the analysis refused or failed, the verdict is `experiment` or
   `refuse`, never `act` or `act_with_approval`.
6. **Contested evidence is never automated.** A `contested` result never yields `act`.
7. **Determinism.** The same three inputs produce the same `decision_digest`.
8. **Every verdict is explainable.** `explain()` renders and verifies: every number in the
   rationale is either a claim of the analysis or a threshold the request declared.

**Seeded defects.** Each is a plausible way to write this wrong. The gate must fail under every one
of them, and the table says which check is supposed to catch it.

| id | defect | must be caught by |
|---|---|---|
| `ignore_reversibility` | the reversibility check is dropped | property 1 |
| `ignore_risk_class` | a high-risk class no longer forces human approval | property 2, scenario `strong_effect_high_risk` |
| `best_check_wins` | the verdict takes the most permissive check instead of the least | the table |
| `ignore_worthwhile_effect` | the minimum-worthwhile-effect comparison is skipped | scenario `effect_below_bar` |
| `refusal_becomes_approval` | a refusal is treated as something a person may wave through | property 5 |
| `ignore_contested` | a contested result is treated as supported | property 6, scenario `contested_result` |
| `ignore_segments` | the segment checks are dropped, so only the pooled effect is read | scenario `harmed_segment` |
| `ignore_trend_breakdown` | the parallel-trends breakdown is not read (v3) | scenario `did_fragile_to_trends` |

## Pass bars

```toml
[meta]
suite = "D0"
version = 3
registered = "2026-09-25"

[table]
scenarios_must_match = 1.0      # every scenario returns exactly the verdict it names

[table.expected]                # scenario id -> the verdict it must return
strong_effect_low_risk = "act"
strong_effect_medium_risk = "act"
strong_effect_high_risk = "act_with_approval"
strong_effect_irreversible = "act_with_approval"
effect_below_bar = "refuse"
uncertain_worth = "experiment"
contested_result = "experiment"
provisional_graph = "act_with_approval"
identified_only_by_iv = "act_with_approval"
not_identified_can_test = "experiment"
not_identified_cannot_test = "refuse"
no_overlap_can_test = "experiment"
broken_question = "refuse"
unreviewed_assumptions = "refuse"
analysis_failed = "refuse"
harmed_segment = "refuse"
uniform_segments = "act"
did_robust_to_trends = "act_with_approval"
did_fragile_to_trends = "experiment"

[properties]
sweep = 400                     # random (request, policy) pairs per property
seed = 20260921
violations_allowed = 0

[seeded_defects]
required = [
  "ignore_reversibility", "ignore_risk_class", "best_check_wins",
  "ignore_worthwhile_effect", "refusal_becomes_approval", "ignore_contested",
  "ignore_segments", "ignore_trend_breakdown",
]
```

**Why no statistical bars.** Nothing here is estimated, so there is no sampling error to allow for.
A single counterexample is a failure.

**What this gate does not establish.** That the thresholds are the *right* thresholds. Whether a
0.8 E-value bar or a 5-minute worthwhile effect is correct for a given operation is a judgement for
whoever owns that operation; the gate only holds the procedure to the policy it was given, and
records which policy a verdict followed. Calibration — whether actions taken under `act` actually
produced the predicted effect — is measured by the outcome-capture loop (`causal_decision.outcome`,
`GET /api/v1/decisions/calibration`), not by this gate, and needs follow-ups on real operations
before it says anything.

## First run

The bars above were written before the gate ran, and **no bar in this document was changed
afterwards**. The first run (`results/2026-09-21-v1-run1.json`) did fail, twice, and both failures
were faults in the code under test:

1. **Scenario `contested_result` returned `refuse` where the table says `experiment`.** A hidden
   confounder had biased the estimate toward "no benefit" and a negative control had fired. The
   procedure then read the interval as lying below the worthwhile bar and answered "do not act" --
   drawing a confident conclusion from an estimate it had itself just called contested. A contested
   estimate cannot support *either* direction. Fixed in `policy._result_checks`: the below-the-bar
   finding may only reach `refuse` when the result is supported, its falsification tests passed and
   its estimators agree; otherwise it allows `experiment`.
2. **Property 8 failed on 29 of 400 sweep cases.** The rationale spliced the deciding checks into
   its narrative, and those checks carry this layer's own numbers -- an E-value bar, an interval
   width as a multiple of the effect -- plus, for refusals, the kernel's own prose. Verification
   correctly refused to certify text that promised every figure was a claim of the analysis and
   then contained figures that were not. Fixed in `policy._rationale`: the narrative is
   claim-bound only; the reasons live in `checks` and `what_would_change_it`, which `explain`
   prints beneath it.

Both fixes make the procedure stricter, not the gate looser. Disclosure: this document was written
before the run but committed in the same change as the run that follows it, so the ordering rests
on the text being unchanged, which the diff shows.

## Changelog

- v1 (2026-09-21): initial registration. Two implementation faults found on the first run; no bar
  altered. See **First run**.
- v1.1 (2026-09-24): **segment-aware checks added; no bar in this document changed, and none
  covers them.** `causal_decision.decide` now reads `result.segments` and
  `result.heterogeneity`, so an action that materially harms a declared subgroup is not
  automated on a favourable pooled effect. Every D0 scenario and every sweep case analyses
  without `segment_by`, which is why the suite stayed green through the change: it cannot see
  these checks at all.

  That is stated here rather than quietly relied on. The new behaviour is pinned by
  `packages/causal-decision/tests/test_segments.py`, whose load-bearing case is the one this
  table could never contain — data where the pooled evidence is unambiguously good (a large,
  precise, worthwhile saving, every pooled check passing, verdict `act`) and one declared
  segment is still made worse. Removing the checks turns five of its ten tests red, including
  that one.

  **What is still not established:** that the ceilings are the right ones. A segment whose
  family-wise interval lies wholly beyond the harm threshold caps the verdict at `refuse`
  (`experiment` when the estimate is contested, by the same reasoning as the worthwhile-effect
  check); one that merely cannot exclude harm caps at `experiment`; detected heterogeneity on
  its own caps at `act_with_approval`. Those are judgements, and like every other threshold in
  this layer they are consistent rather than calibrated until outcome capture exists. A future
  version of this gate should carry segment scenarios in the table proper.
- **v2 (2026-09-25): the expected verdicts move into this document, and segments join the table.**
  No existing bar or expected verdict changed: the fifteen v1 scenarios are registered here with
  the verdicts the v1 code already expected, which the v1 run (`results/2026-09-21-v1-run1.json`)
  shows each returning. What changed is who holds them — this file, whose hash every result
  records, rather than the code under test.

  Added: scenarios `harmed_segment` and `uniform_segments`, and seeded defect `ignore_segments`,
  closing the gap v1.1 disclosed (the table could not see the segment checks at all). Written
  before either scenario exists in `d0.py` or has been run. Disclosure: the premise of both is
  known from `packages/causal-decision/tests/test_segments.py` — a harmed segment is not `act`, a
  uniform one is — but that test asserts only "not `act`" for the harmed case, so the `refuse`
  registered above is a prediction from the policy's stated ceilings, not an observed output.
- **v3 (2026-09-25): the decision reads parallel-trends sensitivity.** A difference-in-differences
  result carries (since T5 v3) the breakdown multiple at which its interval reaches zero; a new
  check caps the verdict at `experiment` when that breakdown is below the policy's
  `min_trend_breakdown` (default 1: a violation no larger than the pre-adoption deviations already
  seen). Two DiD scenarios and the `ignore_trend_breakdown` defect join the table. Written before
  the check exists; no earlier scenario or bar changed, and the seventeen v2 verdicts stand.
