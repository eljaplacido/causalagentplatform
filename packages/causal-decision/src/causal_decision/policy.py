"""
The decision procedure: a causal outcome plus a request plus a policy -> one verdict.

It is a ladder, not a score. Every check names the strongest verdict it still allows,
and the result is the weakest of those. A check can lower autonomy and never raise it,
so no amount of good news anywhere can outvote one blocking problem. Each check appears
in the decision whether it passed or failed, because "what did you look at" is part of
the answer.

Four verdicts:

* ``act``               -- automate it.
* ``act_with_approval`` -- a person signs off, with this evidence in front of them.
* ``experiment``        -- the evidence cannot settle it; here is the experiment that would.
* ``refuse``            -- doing nothing is the better answer, or the question is broken.
"""

from __future__ import annotations

import math

from causal_core.canonical import digest
from causal_core.contract import (
    Abstention,
    AbstentionReason,
    ApparatusFailure,
    CausalResult,
    Interval,
    ResultStatus,
    SegmentEffect,
    Tier,
)
from scipy import stats

from causal_decision.contract import (
    SEVERITY,
    CausalOutcome,
    Check,
    Decision,
    DecisionPolicy,
    DecisionRequest,
    Evidence,
    ExperimentSpec,
    RiskRule,
    SegmentEvidence,
    Verdict,
)

#: Refusals an experiment could resolve. The rest are faults in the question or the data,
#: and running a trial would not answer them.
EXPERIMENTABLE = {
    AbstentionReason.NON_IDENTIFIABLE,
    AbstentionReason.POSITIVITY,
    AbstentionReason.LOW_N,
    AbstentionReason.CI_TOO_WIDE,
    AbstentionReason.WEAK_INSTRUMENT,
}

#: When nothing states how small an effect is still worth having, an experiment is sized
#: to detect this fraction of the outcome's standard deviation (a conventional small effect).
DEFAULT_MDE_IN_SD = 0.2


def _evidence(out: CausalOutcome, request: DecisionRequest) -> Evidence:
    if isinstance(out, ApparatusFailure):
        return Evidence(kind="apparatus_failure", refusal_reason=out.error_type)
    if isinstance(out, Abstention):
        return Evidence(
            kind="refusal",
            refusal_reason=out.reason.value,
            n_used=out.diagnostics.n_used if out.diagnostics else None,
            outcome_sd=out.diagnostics.outcome_sd if out.diagnostics else None,
            identification_strategy=out.certificate.strategy.value
            if out.certificate and out.certificate.strategy
            else None,
        )
    sign = 1.0 if request.direction == "increase" else -1.0
    ci = out.primary.ci
    lo_hi = _signed_interval(ci, sign)
    return Evidence(
        kind="result",
        status=out.status.value,
        estimator=out.primary.estimator,
        estimand=out.primary.estimand,
        identification_strategy=out.certificate.strategy.value
        if out.certificate.strategy
        else None,
        identification_tier=out.certificate.tier.value,
        benefit=sign * out.primary.point,
        benefit_ci=lo_hi,
        ci_level=ci.level if ci else None,
        e_value_ci=out.sensitivity.e_value_ci if out.sensitivity else None,
        refuters_failed=tuple(r.name for r in out.refutations if not r.passed),
        triangulation_agree=out.triangulation.agree if out.triangulation else None,
        n_used=out.diagnostics.n_used,
        outcome_sd=out.diagnostics.outcome_sd,
        result_digest=out.result_digest,
        segments=tuple(_segment_evidence(s, sign) for s in out.segments),
        heterogeneity_differs=out.heterogeneity.differs if out.heterogeneity else None,
        trend_breakdown=_trend_breakdown(out),
    )


def _trend_breakdown(out: CausalResult) -> float | None:
    pt = out.sensitivity.parallel_trends if out.sensitivity is not None else None
    if pt is None:
        return None
    return pt.breakdown if pt.breakdown is not None else 0.0


def _trend_check(ev: Evidence, policy: DecisionPolicy) -> Check | None:
    """Would a parallel-trends violation no larger than the data already shows flip it?

    A seam for the seeded defects: `ignore_trend_breakdown` makes this return None.
    """
    if ev.trend_breakdown is None:
        return None
    bar = policy.min_trend_breakdown
    ok = ev.trend_breakdown >= bar
    return Check(
        name="robust to parallel-trends violations as large as those before adoption",
        passed=ok,
        allows=Verdict.act if ok else Verdict.experiment,
        detail=f"the interval reaches zero at {ev.trend_breakdown:.3g}x the largest pre-adoption "
        f"deviation, against a bar of {bar:.3g}x"
        + ("" if ok else ": the conclusion needs trends to hold more tightly than the data can "
           "show, which only a randomized test settles"),
    )


def _signed_interval(ci: Interval | None, sign: float) -> tuple[float, float] | None:
    """Read an interval in the requested direction, so a benefit is always positive."""
    if ci is None:
        return None
    a, b = sign * ci.lo, sign * ci.hi
    return (min(a, b), max(a, b))


def _segment_evidence(seg: SegmentEffect, sign: float) -> SegmentEvidence:
    """Copy one segment across, using the family-wise interval a claim is allowed to use."""
    if seg.estimate is None:
        return SegmentEvidence(
            level=seg.level,
            variable=seg.variable,
            n_used=seg.n_used,
            refusal=seg.refusal.value if seg.refusal else "NO_ESTIMATE",
        )
    return SegmentEvidence(
        level=seg.level,
        variable=seg.variable,
        n_used=seg.n_used,
        benefit=sign * seg.estimate.point,
        benefit_ci=_signed_interval(seg.family_wise_ci, sign),
    )


def _segment_checks(
    ev: Evidence, request: DecisionRequest, trustworthy: bool
) -> list[Check]:
    """What the declared subgroups say, for an action judged on the pooled effect.

    A pooled estimate is an average, and an average can describe nobody: a
    playbook that saves twelve minutes on P1 incidents and costs six on P3 pools
    to roughly nothing, and "roughly nothing" is wrong about both groups. The
    kernel detects that case; until this function existed the decision layer
    could not see it, and would approve such an action on the pooled figure.

    These run only when subgroups were *declared*. An empty segment list means
    nobody asked, which is not evidence that the effect is uniform -- so it
    lowers nothing, and says nothing either way.
    """
    if not ev.segments:
        # Nothing to inspect, so nothing to report. A "no segment is harmed"
        # check that passed over zero segments would be a check that inspected
        # nothing and called it success -- and it would read, in the record, as
        # a statement that the subgroups were fine.
        return []

    checks: list[Check] = []
    harmful = -request.harm_threshold if request.harm_threshold else 0.0

    unestimated = [s for s in ev.segments if s.benefit_ci is None]
    if unestimated:
        named = ", ".join(f"{s.level} ({s.refusal or 'no interval'})" for s in unestimated)
        checks.append(Check(
            name="every declared segment could be estimated",
            passed=False, allows=Verdict.act_with_approval,
            detail=f"no effect for {named}: the action may help or harm these groups and this "
                   "analysis cannot say which",
        ))

    # The family-wise interval is what SegmentEvidence carries, so "harmed" here
    # already accounts for how many subgroups were examined.
    harmed = [s for s in ev.segments if s.benefit_ci is not None and s.benefit_ci[0] < harmful]
    confident = [s for s in harmed if s.benefit_ci is not None and s.benefit_ci[1] < harmful]

    if confident:
        named = ", ".join(
            f"{s.level} [{s.benefit_ci[0]:.4g}, {s.benefit_ci[1]:.4g}]"  # type: ignore[index]
            for s in confident
        )
        checks.append(Check(
            name="no declared segment is materially harmed",
            passed=False,
            # A contested estimate cannot establish harm any more than it can
            # establish benefit -- the same reasoning as the worthwhile-effect
            # check above, which the D0 gate forced.
            allows=Verdict.refuse if trustworthy else Verdict.experiment,
            detail=f"the whole family-wise interval lies beyond the harm threshold "
                   f"{harmful:.4g} for {named}"
                   + ("" if trustworthy else ", but this estimate is contested"),
        ))
    elif harmed:
        named = ", ".join(
            f"{s.level} [{s.benefit_ci[0]:.4g}, {s.benefit_ci[1]:.4g}]"  # type: ignore[index]
            for s in harmed
        )
        checks.append(Check(
            name="no declared segment is materially harmed",
            passed=False, allows=Verdict.experiment,
            detail=f"harm beyond {harmful:.4g} cannot be excluded for {named}",
        ))
    else:
        checks.append(Check(
            name="no declared segment is materially harmed",
            passed=True, allows=Verdict.act,
            detail=f"every family-wise interval stays above {harmful:.4g} "
                   f"across {len(ev.segments)} declared segment(s)",
        ))

    if ev.heterogeneity_differs is not None:
        same = not ev.heterogeneity_differs
        checks.append(Check(
            name="the effect is the same across the declared segments",
            passed=same,
            allows=Verdict.act if same else Verdict.act_with_approval,
            detail="Cochran's Q could not tell the segments apart, so the pooled effect "
                   "stands for all of them"
            if same else
            "Cochran's Q separates the segments: the pooled effect is an average over "
            "groups the action treats differently, so a person should see the breakdown "
            "before this is automated",
        ))
    return checks


def _result_checks(
    out: CausalResult,
    ev: Evidence,
    request: DecisionRequest,
    rule: RiskRule,
    policy: DecisionPolicy,
) -> list[Check]:
    checks: list[Check] = []
    mwe = request.minimum_worthwhile_effect
    add = checks.append
    # "Not worth acting on" is a conclusion drawn from the estimate, so it needs an
    # estimate worth drawing conclusions from. When a falsification test has fired or
    # the estimators disagree, the honest reading is that the evidence cannot settle
    # the question -- not that the action is worthless. Found by the D0 gate: a
    # confounded design was answering `refuse` using the very estimate it had just
    # called contested.
    trustworthy = (
        out.status is ResultStatus.supported
        and not ev.refuters_failed
        and ev.triangulation_agree is not False
    )

    if ev.benefit_ci is None:
        add(Check(name="the effect has an interval", passed=False, allows=Verdict.experiment,
                  detail=out.primary.ci_null_reason or "no interval was computed"))
    else:
        lo, hi = ev.benefit_ci
        if lo > mwe:
            add(Check(name="the whole interval clears the minimum worthwhile effect",
                      passed=True, allows=Verdict.act,
                      detail=f"interval [{lo:.4g}, {hi:.4g}] against {mwe:.4g}"))
        elif hi <= mwe:
            add(Check(name="the whole interval clears the minimum worthwhile effect",
                      passed=False,
                      allows=Verdict.refuse if trustworthy else Verdict.experiment,
                      detail=f"the interval [{lo:.4g}, {hi:.4g}] lies at or below {mwe:.4g}: "
                      + ("on this evidence the action is not worth taking, so doing nothing wins"
                         if trustworthy
                         else "but this estimate is contested, so it cannot support that "
                              "conclusion either")))
        else:
            add(Check(name="the whole interval clears the minimum worthwhile effect",
                      passed=False, allows=Verdict.experiment,
                      detail=f"the interval [{lo:.4g}, {hi:.4g}] straddles {mwe:.4g}: the "
                      "evidence cannot say whether the action is worth taking"))
        harmful = -request.harm_threshold if request.harm_threshold else 0.0
        add(Check(name="the interval excludes material harm", passed=lo >= harmful,
                  allows=Verdict.act if lo >= harmful else Verdict.experiment,
                  detail=f"lower bound {lo:.4g} against a harm threshold of {harmful:.4g}"))

    status = out.status
    add(Check(
        name="the result is supported",
        passed=status is ResultStatus.supported,
        allows=Verdict.act if status is ResultStatus.supported
        else Verdict.act_with_approval if status is ResultStatus.provisional
        else Verdict.experiment,
        detail=f"status {status.value}" + _STATUS_NOTE.get(status, ""),
    ))

    llm = _llm_edge_check(out)
    if llm is not None:
        add(llm)

    if rule.require_identification_tier_a:
        tier_a = out.certificate.tier is Tier.A
        add(Check(name="identification is nonparametric", passed=tier_a,
                  allows=Verdict.act if tier_a else Verdict.act_with_approval,
                  detail=f"tier {out.certificate.tier.value}"
                  + ("" if tier_a else ": rests on assumptions the graph cannot state, such as "
                     "effect homogeneity for instrumental variables")))
    if rule.require_refuters_passed:
        ok = not ev.refuters_failed
        add(Check(name="falsification tests passed", passed=ok,
                  allows=Verdict.act if ok else Verdict.experiment,
                  detail="all passed" if ok else f"failed: {', '.join(ev.refuters_failed)}"))
    if rule.require_triangulation_agreement and ev.triangulation_agree is not None:
        ok = bool(ev.triangulation_agree)
        add(Check(name="estimators with different assumptions agree", passed=ok,
                  allows=Verdict.act if ok else Verdict.experiment,
                  detail="agree" if ok else "disagree beyond sampling noise: at least one model "
                  "is misspecified"))
    if rule.min_e_value is not None:
        bar = max(rule.min_e_value, policy.plausible_confounding_rr)
        got = ev.e_value_ci
        ok = got is not None and got >= bar
        add(Check(name="robust to unmeasured confounding", passed=ok,
                  allows=Verdict.act if ok else Verdict.act_with_approval,
                  detail=f"E-value for the interval bound {got:.3g} against a bar of {bar:.3g}"
                  if got is not None
                  else "no E-value could be computed for this outcome scale"))
    trend = _trend_check(ev, policy)
    if trend is not None:
        add(trend)
    if rule.max_ci_width_over_effect is not None and ev.benefit_ci and ev.benefit:
        lo, hi = ev.benefit_ci
        ratio = (hi - lo) / abs(ev.benefit) if ev.benefit else math.inf
        ok = ratio <= rule.max_ci_width_over_effect
        add(Check(name="the estimate is precise enough to act on", passed=ok,
                  allows=Verdict.act if ok else Verdict.experiment,
                  detail=f"interval width is {ratio:.2f}x the effect "
                  f"(limit {rule.max_ci_width_over_effect:.2f}x)"))
    checks.extend(_segment_checks(ev, request, trustworthy))
    return checks


def _llm_edge_check(out: CausalResult) -> Check | None:
    """Does the estimate rest on edges a language model guessed and nobody reviewed?

    A provisional graph alone allows ``act_with_approval``: a person signs off with the
    evidence in front of them. That is not enough when the unreviewed structure came
    from a language model. The person approving an action is judging the action, not
    auditing the graph, and the estimate's validity is the graph's: an omitted
    confounding path is invisible in every number below it. A randomized test of the
    action does not depend on the graph at all, so it is what this allows; review the
    edges (approve or refute them) and the cap lifts. Edges from people, data,
    experiments or the literature are not capped here -- their review state is.

    A seam for the seeded defects: `ignore_llm_edges` makes this return None.
    """
    edges = out.certificate.unreviewed_llm_edges
    if not edges:
        return None
    return Check(
        name="no unreviewed edge was proposed by a language model",
        passed=False,
        allows=Verdict.experiment,
        detail=f"{', '.join(edges)}: proposed by a language model and not reviewed, so the "
        "estimate rests on a guessed graph; a randomized test does not, and reviewing "
        "the edges lifts this",
    )


#: What a status means for a decision, in the words the caller sees.
_STATUS_NOTE = {
    ResultStatus.provisional: "; the graph has edges nobody has reviewed",
    ResultStatus.contested: "; a falsification test failed or estimators disagree",
}


def _reversibility_check(request: DecisionRequest) -> Check | None:
    if request.reversible:
        return None
    return Check(name="the action is reversible", passed=False, allows=Verdict.act_with_approval,
                 detail="an irreversible action is never automated, whatever the evidence says")


def _risk_class_check(request: DecisionRequest, rule: RiskRule) -> Check | None:
    if rule.allow_auto:
        return None
    return Check(name="this risk class may be automated", passed=False,
                 allows=Verdict.act_with_approval,
                 detail=f"risk class {request.risk_class.value} always goes to a person")


def _refusal_check(out: Abstention, request: DecisionRequest, policy: DecisionPolicy) -> Check:
    can_test = (
        out.reason in EXPERIMENTABLE and request.can_randomize and policy.experiments_available
    )
    return Check(
        name="the effect could be estimated from the data",
        passed=False,
        allows=Verdict.experiment if can_test else Verdict.refuse,
        detail=f"{out.reason.value}: {out.detail}"
        + ("" if can_test else "; an experiment would not resolve this"),
    )


def _failure_check(out: ApparatusFailure) -> Check:
    return Check(name="the analysis ran", passed=False, allows=Verdict.refuse,
                 detail=f"{out.error_type} in stage '{out.stage}'")


def _weakest(checks: list[Check]) -> Verdict:
    """The verdict is the weakest one any failed check allows. Good news never outvotes bad."""
    return max(
        (c.allows for c in checks if not c.passed), key=lambda v: SEVERITY[v], default=Verdict.act
    )


def _sizing(ev: Evidence, request: DecisionRequest, policy: DecisionPolicy) -> ExperimentSpec:
    sd = ev.outcome_sd
    mde = request.minimum_worthwhile_effect
    note = ""
    if mde <= 0:
        if ev.benefit and abs(ev.benefit) > 0:
            mde = abs(ev.benefit)
            note = ("No minimum worthwhile effect was given, so the experiment is sized to "
                    "confirm the effect seen in the observational data.")
        elif sd:
            mde = DEFAULT_MDE_IN_SD * sd
            note = (f"No minimum worthwhile effect was given and no effect was estimated, so the "
                    f"experiment is sized for {DEFAULT_MDE_IN_SD} of the outcome's standard "
                    "deviation, a convention, not a business judgement. State the effect that "
                    "would matter and this gets smaller or larger.")
    per_arm = None
    days = None
    if sd and mde > 0:
        z_a = float(stats.norm.ppf(1 - policy.alpha / 2))
        z_b = float(stats.norm.ppf(policy.power))
        per_arm = math.ceil(2 * (z_a + z_b) ** 2 * sd**2 / mde**2)
        if request.units_per_day:
            days = round(2 * per_arm / request.units_per_day, 1)
    return ExperimentSpec(
        question=f"Does '{request.action}' change {request.outcome} by at least {mde:.4g} "
        f"({request.direction})?",
        design="randomized_units",
        unit=request.randomization_unit,
        allocation=0.5,
        minimum_detectable_effect=mde,
        outcome_sd=sd,
        units_per_arm=per_arm,
        estimated_days=days,
        guardrails=(
            f"stop if {request.outcome} moves against the desired direction by more than "
            f"{request.harm_threshold:.4g}",
            "randomize the assignment; do not let the agent choose who is treated",
            "fix the analysis before the data arrive: this is the estimand above",
        ),
        note=note
        or (f"Sized for {policy.power:.0%} power at alpha {policy.alpha:g}, two-sided, "
            "equal allocation."),
    )


def _rationale(verdict: Verdict, ev: Evidence, request: DecisionRequest) -> str:
    """The narrative, and only the narrative.

    Every number here is a claim of the analysis, so the text can be verified against it.
    The reasons for the verdict are *not* spliced in: they are this layer's own arithmetic
    over its own thresholds, they live in ``checks`` and ``what_would_change_it`` already,
    and ``explain`` prints them beneath this sentence. Found by the D0 gate, which caught
    the tail carrying numbers -- an E-value bar, an interval-width ratio -- that no claim
    of the analysis backed, inside prose that promises the opposite.
    """
    head = {
        Verdict.act: f"Act: {request.action}.",
        Verdict.act_with_approval: f"Ask a person to approve: {request.action}.",
        Verdict.experiment: f"Run an experiment before acting: {request.action}.",
        Verdict.refuse: f"Do not act: {request.action}.",
    }[verdict]
    if ev.kind == "result" and ev.benefit_ci is not None:
        lo, hi = ev.benefit_ci
        body = (
            f" The estimate is ⟦claim:effect⟧ on {request.outcome} (⟦claim:ci.level⟧ CI "
            f"⟦claim:effect.ci.lo⟧ to ⟦claim:effect.ci.hi⟧, n = ⟦claim:n⟧, estimator "
            f"{ev.estimator}), read as a benefit of {lo:.4g} to {hi:.4g} in the requested "
            f"direction ({request.direction})."
        )
    elif ev.kind == "result":
        body = " An effect was estimated but carries no interval, so its precision is unknown."
    elif ev.kind == "refusal":
        body = f" No effect could be estimated: {ev.refusal_reason}."
    else:
        body = " The analysis itself failed, so there is no evidence either way."
    return head + body


def decide(
    outcome: CausalOutcome, request: DecisionRequest, policy: DecisionPolicy | None = None
) -> Decision:
    """Turn a causal outcome into a verdict. Pure: same inputs, same decision."""
    policy = policy or DecisionPolicy()
    rule = policy.rules[request.risk_class]
    ev = _evidence(outcome, request)

    if isinstance(outcome, ApparatusFailure):
        checks = [_failure_check(outcome)]
    elif isinstance(outcome, Abstention):
        checks = [_refusal_check(outcome, request, policy)]
    else:
        checks = _result_checks(outcome, ev, request, rule, policy)
        checks += [
            c
            for c in (_reversibility_check(request), _risk_class_check(request, rule))
            if c is not None
        ]

    verdict = _weakest(checks)
    failed = [c for c in checks if not c.passed]

    # An experiment that cannot be run is not an answer: hand it to a person instead,
    # unless something else already said refuse.
    testable = policy.experiments_available and request.can_randomize
    if verdict is Verdict.experiment and not testable:
        verdict = Verdict.act_with_approval
        checks.append(Check(name="an experiment is possible", passed=False,
                            allows=Verdict.act_with_approval,
                            detail="no experiment is available for this action, so the open "
                            "question goes to a person"))
        failed = [c for c in checks if not c.passed]

    spec = _sizing(ev, request, policy) if verdict is Verdict.experiment else None
    changes = tuple(
        f"{c.name}: {c.detail}" for c in failed if c.allows is not Verdict.act
    )
    decision = Decision(
        verdict=verdict,
        request=request,
        evidence=ev,
        checks=tuple(checks),
        rationale=_rationale(verdict, ev, request),
        experiment=spec,
        what_would_change_it=changes,
        policy_digest=digest(policy),
        request_digest=digest(request),
    )
    return decision.model_copy(
        update={
            "decision_digest": digest(
                decision.model_dump(mode="json", exclude={"decision_digest"})
            )
        }
    )
