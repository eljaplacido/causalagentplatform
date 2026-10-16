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
| Edge origin | An unreviewed edge a language model proposed (`origin=llm_prior`) → `experiment`: the person approving an action is not reviewing the graph, and a randomized test does not depend on it. Approving or refuting the edge lifts it |
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

## For whom, not just whether

An average can describe nobody. A remediation playbook that takes twelve minutes off P1 incidents
and adds six to P3 pools to roughly nothing, and "roughly nothing" is wrong about both groups. So
when the analysis declared subgroups, the verdict reads them:

```python
out = cc.analyze(df, graph, treatment="playbook", outcome="minutes",
                 segment_by=("severity",))      # declared BEFORE any answer is seen
d = decide(out, request)
```

- **A segment the action materially harms blocks `act`.** If a segment's interval lies wholly
  beyond the harm threshold, the ceiling is `refuse` — `experiment` when the estimate is contested,
  because a contested estimate cannot establish harm any more than benefit. If harm merely cannot
  be excluded, the ceiling is `experiment`. This holds whatever the pooled effect says: a large,
  precise, worthwhile pooled saving does not buy the right to hurt an identified group.
- **The interval used is the family-wise one.** Having examined k subgroups, the unadjusted
  interval no longer means what it says, and a verdict is a claim.
- **A segment that could not be estimated is not a segment that was cleared.** It caps at
  `act_with_approval` and is named.
- **Heterogeneity alone caps at `act_with_approval`.** If Cochran's Q separates the segments, the
  pooled effect is an average over groups the action treats differently, so a person sees the
  breakdown before it is automated.

**Declaring no subgroups adds no checks, and means nothing either way.** An empty segment list is
not evidence that the effect is uniform — only that nobody asked. The decision layer will not
invent a reassurance the analysis never produced.

## When the evidence cannot settle it

The decision carries the experiment that would: the randomization unit, arms, a size from the
minimum worthwhile effect and the outcome's spread, an estimated duration from the arrival rate,
and guardrails. If no experiment is possible (`can_randomize=False`, or the policy says so), the
open question goes to a person rather than quietly becoming an action.

## Keeping the decision

A verdict nobody kept is advice. `causal_decision.ledger` seals each decision into a hash chain:
the decision itself, digests of the evidence it rested on (result, graph, data, contract version),
the approval that let it happen, and the digest of the entry before it.

```python
from causal_decision.ledger import append, verify_chain

record = append(tail, decision, outcome, recorded_at=now, approval=approval, executed=True)
verify_chain(records)   # ok, or exactly which entry stopped holding and why
```

`decide` is pure, so a recorded decision can be re-run and compared digest to digest — which is
what makes it disputable rather than merely stored. A decision to act and an action taken are kept
as separate facts (`verdict` and `executed`), because conflating them is how a ledger comes to
overstate what happened.

**What the chain does not do.** It makes tampering *detectable*, not impossible: anyone who can
rewrite the store can rewrite the chain from the altered entry onward, and it will verify again.
There is a test that does exactly that and asserts it verifies. Detection becomes prevention only
when the head digest is anchored somewhere the writer does not control — an append-only table, a
countersignature, another party's copy — which is a deployment decision, so the chain reports its
head and leaves the anchoring to you.

What the code does provide is both ends of that: `checkpoint(records)` says exactly what to
publish (`GET /api/v1/decisions/checkpoint`), and `verify_against(records, checkpoints)` checks a
chain against checkpoints kept elsewhere (`POST /api/v1/decisions/verify-anchored`). A rewrite that
re-seals every later entry passes `verify_chain`; it fails here, at the first checkpoint it
contradicts, and a truncation below a checkpoint fails too.

## Keeping the assumptions

A verdict is only as good as its graph, so the graph is kept too. `causal_decision.assumptions`
stores a causal model as a chain of immutable, content-addressed versions — the kernel's
`CausalGraph`, each edge with its origin and review status, plus the assumptions a graph cannot
state (no interference, consistency, positivity, measurement, timing).

```python
from causal_decision.assumptions import EdgeReview, create, impact, review, revise

v1 = create("incident-remediation", "load -> playbook; load -> minutes; playbook -> minutes",
            author_id="alice", created_at=now, why="from the incident review")
v2 = revise(v1, graph=v1_text + "; backlog -> playbook; backlog -> minutes [origin=llm_prior]",
            author_id="agent-7", created_at=now, why="proposed confounder")
v3 = review(v2, [EdgeReview(edge="backlog -> minutes", status="approved", reason="in the RCA")],
            reviewer_id="carol", created_at=now)      # agent-7 could not have done this
impact(v1, v3, query).status                            # invalidated: {load} no longer suffices
```

- **A revision cannot approve an edge**, and **nobody reviews an edge they introduced** — each
  version carries who added every edge, so the rule holds however many versions later.
- **`impact(old, new, query)`** asks the question a revision raises: if the new graph is true,
  is the estimate a verdict used still an estimate of the effect it claimed? For back-door, the
  *old* adjustment set must be valid under the *new* graph. Other strategies hold only on an
  identical certificate and are otherwise `unverified`.
- The A0 gate (`benchmarks/a0-assumption-impact`) checks `impact` on 800 revised graphs against an
  oracle that shares no code with the kernel: exact linear-Gaussian population algebra on the new
  graph. The first run failed its class-balance floor and is kept beside the passing one.

## Scoring the decisions

`causal_decision.outcome` records, with each decision, what would have to be re-measured to find
out whether it held (`plan`), scores the later window once (`compare`), and aggregates (`report`).
The report is written for the day real follow-ups arrive, so it is strict about what a handful of
them can say:

- **Every rate is a `RateEstimate`**: numerator, denominator and a Clopper-Pearson interval — and
  **no rate at all below ten follow-ups** (`MIN_FOLLOWUPS`), with the reason. One follow-up used to
  read as 100%.
- **Undecidable follow-ups** (the kernel refused the later window — a full rollout leaves no
  comparison group) are counted by reason and kept out of the rates, and
  `acted_worthwhile_bounds` carries them as unknowns in worst-case bounds over every acted
  decision: they are not missing at random.
- **Claimed against confirmed** (`worthwhile_claimed_vs_confirmed`): an exact McNemar test of how
  often the analysis said the effect cleared the worthwhile bar against how often the later window
  confirmed it. With fewer than six discordant pairs no split can reach 0.05, so it says
  `could_not_resolve` — never `no_difference_detected`, which would be a claim nothing tested.

The C0 gate (`benchmarks/c0-calibration`, `pytest -m c0`) holds this to **simulated** decision
streams whose effects are set by construction, run through the real `analyze` → `decide` → `plan`
→ `analyze` → `compare` path: exact interval coverage at every n from 10 to 200, coverage of the
constructed truth over 400 streams per design, the refusal rule, and five seeded defects (a Wald
interval, undecidable follow-ups scored as agreements or dropped, no minimum, "could not resolve"
read as "no difference"). It proves the report honest; it says nothing about how well any real
operation's thresholds are calibrated.

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
- **The record's own tests** cover an entry edited, removed and reordered — each caught, each
  located by index — plus the limit above.

The first run failed twice, both times on faults in the procedure rather than the bars. Both are
written up in the pre-registration and the failing behaviours are pinned by tests.

## What it does not do

- **Execute anything.** It returns a verdict. Acting on one is the caller's job, and approval is
  not something an agent can grant itself.
- **Choose your thresholds.** Whether a 5-minute effect is worth acting on is a judgement for
  whoever owns the operation. The gate holds the procedure to the policy it was given; it cannot
  tell you the policy is right.
- **Claim to be calibrated.** The outcome-capture loop exists and its report is gated (C0, on
  simulated streams), but no follow-up has been taken on a real operation, so every rate is
  refused for want of follow-ups. Until at least ten real ones stand behind a rate, treat the
  verdicts as a discipline, not as a measured success rate.
- **Cost-benefit arithmetic**, multi-arm choice, or sequential/anytime-valid monitoring. Not yet.

Licence: Apache-2.0.
