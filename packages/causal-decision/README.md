# causal-decision

**The layer between an agent's proposed action and the system that action would change.**
It answers one question: *is the evidence strong enough to do this without asking a human?*

`causal-core` says what the effect is, or refuses. This package says what to do about it:
**act**, **act with approval**, **run an experiment first**, or **refuse**. Evidence does not
depend on who is asking or on what a mistake would cost; a decision depends on both, so the two
are kept in separate packages.

```python
import causal_core as cc
from causal_decision import DecisionRequest, RiskClass, decide, explain

out = cc.analyze(incidents, "load -> playbook; load -> minutes; playbook -> minutes",
                 treatment="playbook", outcome="minutes", estimand="att")

request = DecisionRequest(
    action="apply remediation playbook X",
    outcome="minutes",
    direction="decrease",              # so a benefit is always positive
    risk_class=RiskClass.medium,
    reversible=True,
    minimum_worthwhile_effect=5.0,     # below this, acting costs more than it returns
    randomization_unit="incident",
    units_per_day=40,
)

d = decide(out, request)
d.verdict        # act | act_with_approval | experiment | refuse
print(explain(d, out))
```

```text
ACT: Act: apply remediation playbook X. The estimate is -8.899 on minutes (95% CI -9.495 to
-8.303, n = 4000, estimator aipw), read as a benefit of 8.303 to 9.495 in the requested
direction (decrease).

Proceed. Record the outcome and compare it with this estimate.

Checks:
  [ok ] the whole interval clears the minimum worthwhile effect -- interval [8.303, 9.495] against 5
  [ok ] the interval excludes material harm -- lower bound 8.303 against a harm threshold of 0
  [ok ] the result is supported -- status supported
  [ok ] identification is nonparametric -- tier A
  [ok ] falsification tests passed -- all passed
  [ok ] estimators with different assumptions agree -- agree
  [ok ] robust to unmeasured confounding -- E-value for the interval bound 2.88 against a bar of 1.5
  [ok ] the estimate is precise enough to act on -- interval width is 0.13x the effect (limit 1.00x)
```

Same action, a sixth of the data and six times the noise:

```text
EXPERIMENT: Run an experiment before acting: apply remediation playbook X. The estimate is
-3.979 on minutes (95% CI -12.72 to 4.758, n = 600, estimator aipw), read as a benefit of
-4.758 to 12.72 in the requested direction (decrease).

Checks:
  [no ] the whole interval clears the minimum worthwhile effect -- the interval [-4.758, 12.72]
        straddles 5: the evidence cannot say whether the action is worth taking
  [no ] the interval excludes material harm -- lower bound -4.758 against a harm threshold of 0
  [no ] robust to unmeasured confounding -- E-value for the interval bound 1 against a bar of 1.5
  [no ] the estimate is precise enough to act on -- interval width is 4.39x the effect (limit 1.00x)

Experiment that would settle it:
  Does 'apply remediation playbook X' change minutes by at least 5 (decrease)?
  Randomize incidents, control vs treated, 678 incidents per arm, about 33.9 days.
  Guardrail: stop if minutes moves against the desired direction by more than 0
  Guardrail: randomize the assignment; do not let the agent choose who is treated
  Guardrail: fix the analysis before the data arrive: this is the estimand above
  Sized for 80% power at alpha 0.05, two-sided, equal allocation.
```

## How it decides

A ladder, not a score. Every check names the strongest verdict it still allows, and the verdict is
the **weakest** of those, so no amount of good news outvotes one blocking problem. Every check is
recorded whether it passed or failed: *what did you look at* is part of the answer.

| The check | What lowers the verdict |
|---|---|
| Worth acting on | The interval below the minimum worthwhile effect → `refuse`; straddling it → `experiment` |
| No material harm | The interval reaching past the harm threshold → `experiment` |
| Result status | `provisional` (unreviewed graph edges) → `act_with_approval`; `contested` → `experiment` |
| Identification tier | Tier B (assumptions the graph cannot state, such as IV homogeneity) → `act_with_approval` |
| Falsification tests | A placebo or negative control that fired → `experiment` |
| Triangulation | Estimators resting on different assumptions disagreeing → `experiment` |
| Unmeasured confounding | An E-value below the domain's plausible confounding → `act_with_approval` |
| Precision | An interval wider than the risk class allows, as a multiple of the effect → `experiment` |
| Reversibility | An irreversible action → `act_with_approval`, always |
| Risk class | `high` → `act_with_approval`, always, however good the evidence |

Thresholds live in a `DecisionPolicy` you set, per risk class; the defaults are a starting point,
not a recommendation. Every decision carries digests of the policy, the request and itself, so a
verdict names the rules it followed and can be replayed.

Two rules worth stating plainly, because both are easy to get wrong:

- **A refusal is never an action.** When the analysis could not answer, the verdict is `experiment`
  or `refuse` — never `act`, and never `act_with_approval`.
- **A contested estimate cannot justify a confident refusal either.** If a falsification test fired,
  "not worth doing" is no better supported than "worth doing". The evidence cannot settle it, so
  the answer is the experiment that would. The gate caught this one; see the pre-registration.

## When the evidence cannot settle it

The decision carries the experiment that would: the randomization unit, arms, a size from the
minimum worthwhile effect and the outcome's spread, an estimated duration from the arrival rate,
and guardrails. If no experiment is possible (`can_randomize=False`, or the policy says so), the
open question goes to a person rather than quietly becoming an action.

## Evidence

The D0 gate is pre-registered in `benchmarks/d0-decision-policy/PRE-REGISTRATION.md`, with bars
fixed before the first run, and runs on every change (`pytest -m d0`):

- **15 of 15 scenarios** return exactly the verdict the table names, spanning all four verdicts and
  all three kinds of outcome (result, refusal, apparatus failure).
- **8 properties** hold over a seeded sweep of 400 (request, policy) pairs with zero violations:
  irreversible actions are never automated; raising the risk class or the worthwhile bar never
  raises autonomy; more data never lowers it; no estimate means no action; contested evidence is
  never automated; the same inputs give the same `decision_digest`; and every verdict's prose
  verifies against the analysis.
- **6 seeded defects** each make the gate fail, including the dangerous one: reading a contested
  result as if it were supported.

The first run failed twice, both times on faults in the procedure rather than the bars. Both are
written up in the pre-registration and the failing behaviours are pinned by tests.

## What it does not do

- **Execute anything.** It returns a verdict. Acting on one is the caller's job, and approval is
  not something an agent can grant itself.
- **Choose your thresholds.** Whether a 5-minute effect is worth acting on is a judgement for
  whoever owns the operation. The gate holds the procedure to the policy it was given; it cannot
  tell you the policy is right.
- **Calibrate.** Whether actions taken under `act` produced the predicted effect needs the
  outcome-capture loop, which is not built. Until it is, treat the verdicts as a discipline, not as
  a measured success rate.
- **Cost-benefit arithmetic**, multi-arm choice, or sequential/anytime-valid monitoring. Not yet.

Licence: Apache-2.0.
