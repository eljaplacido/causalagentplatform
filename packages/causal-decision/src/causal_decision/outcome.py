"""What happened after the verdict, and whether the verdict deserved to be trusted.

Every threshold in this layer is *consistent* -- the procedure follows the policy
it was given, and the D0 gate proves it. None of them is *calibrated*, because
nothing has ever compared a prediction with what followed. This module is the
record that would close that, and the comparison over it.

**Calibrating a causal prediction needs a causal estimate afterwards.** That is
the whole difficulty, and it is not a detail to be arranged later: if the action
was applied to everybody, there is no untreated group left and no amount of
subsequent data recovers the effect. A comparison made anyway would be a
before-and-after difference wearing the word "calibration", which is how
`evaluation-endpoints-not-measured` happened one floor down.

So the follow-up is not a new statistical instrument. It is **the same analysis,
re-run over the window after the decision**, and the kernel's own refusals do the
hard part:

    everyone treated  -> DESIGN_CHECK_FAILED   the rollout removed the comparison
    99.5% treated     -> POSITIVITY            what is left cannot carry the weight
    too few rows      -> LOW_N
    normal mix        -> a result, and a comparison worth making

A refusal there becomes an *undecidable* realization, carrying that reason, and
it is counted rather than dropped. A calibration report that silently omitted
every decision whose effect could no longer be measured would be scored on the
subset that happened to stay measurable, which is the most flattering subset
available.

**A rate from a handful of follow-ups is not a calibration figure.** Every rate
the report states is a Clopper-Pearson interval with its numerator and
denominator, and below ``MIN_FOLLOWUPS`` it states none -- it counts, and says
why it will not divide. The acted-worthwhile rate is also bounded over *every*
acted decision, the undecidable ones included, because those are not missing at
random. And the paired comparison of "claimed it would clear" against "cleared"
says ``could_not_resolve`` when the discordant pairs are too few for the exact
test ever to reject: "no difference" from data that could not have shown one is
the most quotable wrong sentence available. The C0 gate
(``benchmarks/c0-calibration``) holds all of this to simulated streams with a
known truth; it establishes nothing about real operations.

Everything here is pure: no clock, no store, no network. The caller supplies
timestamps and performs the re-run, so a report can be rebuilt from its records
and re-checked anywhere.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Literal

from causal_core.contract import (
    Abstention,
    ApparatusFailure,
    CausalGraph,
    CausalQuery,
    CausalResult,
)
from pydantic import BaseModel, ConfigDict, Field
from scipy.stats import beta, binom

from causal_decision.contract import CausalOutcome, Decision, Verdict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


#: Verdicts under which the action may actually have been taken. The others
#: produce nothing to observe, so there is nothing to calibrate against.
ACTED = {Verdict.act, Verdict.act_with_approval}

#: Confidence level of every interval the report states.
LEVEL = 0.95
#: Below this many follow-ups in a rate's denominator the report states no rate.
#: A policy floor, registered in benchmarks/c0-calibration: even 10/10 cannot
#: exclude a rate of 0.69 at 95%, so anything smaller is mostly noise to quote.
MIN_FOLLOWUPS = 10
#: Significance level of the paired claimed-versus-confirmed comparison.
ALPHA = 0.05


class FollowUp(_Model):
    """What to re-run, when, and what the decision predicted before it ran.

    The prediction is copied in rather than referenced, so the comparison cannot
    be quietly re-based on a later analysis: a follow-up is only evidence about
    a verdict if the number it is scored against is the number that verdict used.
    """

    decision_digest: str
    verdict: Verdict
    action: str
    outcome: str
    direction: Literal["increase", "decrease"]
    graph: str | CausalGraph = Field(
        description="The graph as given to the original analysis: verbatim text, or the "
        "registered model version's graph."
    )
    query: CausalQuery = Field(description="Re-run with this, so the estimand cannot drift.")
    predicted_benefit: float | None = None
    predicted_ci: tuple[float, float] | None = None
    minimum_worthwhile_effect: float = 0.0
    harm_threshold: float = 0.0
    observe_from: str = Field(description="ISO 8601. The window starts after the action.")
    observe_until: str | None = Field(
        default=None,
        description="ISO 8601, from the request's horizon where it gave one. None means the "
        "caller decides when enough time has passed -- which is a judgement, not a measurement.",
    )
    note: str = ""


class Realization(_Model):
    """One decision, set beside what the later data said about it."""

    decision_digest: str
    verdict: Verdict
    predicted_benefit: float | None = None
    predicted_ci: tuple[float, float] | None = None
    realized_benefit: float | None = None
    realized_ci: tuple[float, float] | None = None

    decidable: bool = Field(
        description="False when the later data could not answer the question. The reason is "
        "the kernel's, not this module's."
    )
    undecidable_reason: str | None = None
    undecidable_detail: str = ""

    point_in_predicted_ci: bool | None = Field(
        default=None,
        description="Whether the realized point estimate fell inside the predicted interval. "
        "NOT coverage of the truth: the realized figure is an estimate with its own error, "
        "which this comparison ignores.",
    )
    intervals_overlap: bool | None = Field(
        default=None,
        description="Whether the two intervals share any value. Weaker than the above and "
        "honest about both uncertainties; a pair that fails this disagrees beyond either "
        "analysis's own noise.",
    )
    cleared_worthwhile: bool | None = Field(
        default=None,
        description="Whether the whole realized interval clears the minimum worthwhile effect "
        "the request declared -- the thing an `act` verdict asserted would happen.",
    )
    realized_harm: bool | None = Field(
        default=None,
        description="Whether the whole realized interval lies beyond the harm threshold, in "
        "the requested direction. True is the expensive case: the platform said act, and the "
        "action made things worse.",
    )
    claimed_worthwhile: bool | None = Field(
        default=None,
        description="Whether the whole PREDICTED interval cleared the minimum worthwhile effect: "
        "what the analysis behind the verdict claimed. Set beside `cleared_worthwhile`, it is "
        "the pair the report's claimed-versus-confirmed comparison counts. None on a "
        "realization recorded before causal-decision 0.5.0, which did not keep it.",
    )


class RateEstimate(_Model):
    """A proportion with its interval -- or the reason none is stated.

    ``rate`` and ``ci`` are None exactly when ``refused`` says why: a denominator
    below the report's minimum. The counts are always there, so a reader can see
    how close the platform is to having a number.
    """

    successes: int
    denominator: int
    rate: float | None = None
    ci: tuple[float, float] | None = None
    level: float = LEVEL
    method: Literal["clopper-pearson"] = "clopper-pearson"
    refused: str | None = None


class RateBounds(_Model):
    """A rate over a population some of whose members could not be measured.

    Worst-case (no-assumption) bounds: every unmeasured member failed, or every
    one succeeded. ``ci`` is the confidence set for the interval they bound --
    the lower Clopper-Pearson limit of the first, the upper of the second -- so
    it covers the true rate at ``level`` whatever the unmeasured ones would have
    shown.
    """

    successes: int
    unresolved: int
    denominator: int
    lower: float | None = None
    upper: float | None = None
    ci: tuple[float, float] | None = None
    level: float = LEVEL
    method: Literal["clopper-pearson worst-case bounds"] = "clopper-pearson worst-case bounds"
    refused: str | None = None


class PairedComparison(_Model):
    """Did the analyses claim the worthwhile bar was cleared as often as it was?

    One pair per decidable follow-up that carries both intervals: claimed (the
    predicted interval cleared the minimum worthwhile effect) and confirmed (the
    realized one did). Exact two-sided McNemar test on the discordant pairs.

    ``could_not_resolve`` is not ``no_difference_detected``. With fewer than
    ``min_discordant`` discordant pairs even the most lopsided split cannot reach
    ``alpha``, so the data could never have shown a difference, and saying there
    was none would be a claim nothing tested.
    """

    n_pairs: int
    claimed_not_confirmed: int
    confirmed_not_claimed: int
    min_discordant: int
    alpha: float = ALPHA
    p_value: float | None = None
    status: Literal["difference", "no_difference_detected", "could_not_resolve"]
    detail: str


class CalibrationReport(_Model):
    """What a set of realizations says about the thresholds that produced them.

    Every rate carries its counts and a Clopper-Pearson interval, and states no
    value at all below ``min_followups`` -- a report over no decidable
    realizations says so; it does not say 100%, and one over three does not say
    67% either. This is the same rule the repository's gates hold themselves to:
    a measurement that inspected nothing may not report success, and it matters
    most here, because this is the number the platform would be judged on.
    """

    n_decisions: int
    n_acted: int
    n_decidable: int
    n_undecidable: int
    undecidable_by_reason: dict[str, int] = Field(default_factory=dict)
    min_followups: int = MIN_FOLLOWUPS

    point_in_interval_rate: RateEstimate
    interval_overlap_rate: RateEstimate
    acted_and_worthwhile_rate: RateEstimate
    acted_and_harmful_rate: RateEstimate
    acted_worthwhile_bounds: RateBounds = Field(
        description="The acted-worthwhile rate over EVERY acted decision, the undecidable ones "
        "included as unknowns. The rate above is over the decidable ones only, and those are "
        "not a random subset: an action rolled out to everybody is the one that stops being "
        "measurable."
    )
    worthwhile_claimed_vs_confirmed: PairedComparison
    acted_and_harmful: int = 0
    not_acted_but_worthwhile: int = Field(
        default=0,
        description="Decidable realizations whose verdict did not act, yet whose whole realized "
        "interval cleared the minimum worthwhile effect: the opportunities the thresholds "
        "turned down. The mirror of `acted_and_harmful`; a policy tuned only against harm "
        "drives this up without showing it anywhere else.",
    )
    by_verdict: dict[str, int] = Field(
        default_factory=dict,
        description="Realizations per verdict. The pooled interval rates mix verdicts; this is "
        "how a reader sees what they are pooled over.",
    )

    note: str = Field(
        default="",
        description="What these numbers are not, in the words a reader needs before quoting them.",
    )


# ── building the plan ─────────────────────────────────────────────────────────


def plan(
    decision: Decision,
    result: CausalResult,
    *,
    graph: str | CausalGraph,
    observe_from: str,
    observe_until: str | None = None,
) -> FollowUp:
    """Record what would have to be re-run to find out whether this verdict held.

    Built from the decision and the analysis behind it, at the moment of
    deciding -- not reconstructed later from memory of what was intended.
    """
    if decision.decision_digest is None:
        raise ValueError(
            "the decision carries no digest, so a follow-up could not be tied back to it"
        )
    ev = decision.evidence
    return FollowUp(
        decision_digest=decision.decision_digest,
        verdict=decision.verdict,
        action=decision.request.action,
        outcome=decision.request.outcome,
        direction=decision.request.direction,
        graph=graph,
        query=result.query,
        predicted_benefit=ev.benefit,
        predicted_ci=ev.benefit_ci,
        minimum_worthwhile_effect=decision.request.minimum_worthwhile_effect,
        harm_threshold=decision.request.harm_threshold,
        observe_from=observe_from,
        observe_until=observe_until or _horizon_end(decision),
    )


def _horizon_end(decision: Decision) -> str | None:
    """The request's horizon, when it is an ISO 8601 timestamp; otherwise None.

    ``observe_until`` is documented as ISO 8601 and read as a time. The horizon is
    free text ("30 days", "Q3"), and copying it across unparsed put prose in a
    field that callers compare with clocks. A horizon that does not parse is left
    in the request, where it came from, rather than guessed into a date here.
    """
    horizon = decision.request.horizon
    if not horizon:
        return None
    try:
        datetime.fromisoformat(horizon)
    except ValueError:
        return None
    return horizon


# ── comparing against what followed ───────────────────────────────────────────


def compare(followup: FollowUp, later: CausalOutcome) -> Realization:
    """Set a prediction beside the re-run, or record why no comparison is possible.

    ``later`` is whatever ``causal_core.analyze`` returned for the observation
    window, run with ``followup.query``. Its refusal is the answer when it
    refuses: this module does not second-guess the kernel about whether the data
    can carry an estimate.
    """
    later_query = getattr(later, "query", None)
    if later_query is not None and later_query != followup.query:
        # The plan exists so the estimand cannot drift after the verdict. A re-run
        # asked a different question is not evidence about this one, whatever it
        # found -- refusing it here holds the rule for every caller, not only the
        # HTTP route that happens to pass the plan's query through.
        raise ValueError(
            "the re-run answered a different query than the one this decision was planned "
            f"with ({later_query.model_dump(exclude_defaults=True)} vs "
            f"{followup.query.model_dump(exclude_defaults=True)})"
        )

    if isinstance(later, ApparatusFailure):
        return Realization(
            decision_digest=followup.decision_digest,
            verdict=followup.verdict,
            predicted_benefit=followup.predicted_benefit,
            predicted_ci=followup.predicted_ci,
            decidable=False,
            undecidable_reason=later.error_type,
            undecidable_detail=f"the re-run failed at {later.stage}: {later.detail}"[:500],
        )
    if isinstance(later, Abstention):
        return Realization(
            decision_digest=followup.decision_digest,
            verdict=followup.verdict,
            predicted_benefit=followup.predicted_benefit,
            predicted_ci=followup.predicted_ci,
            decidable=False,
            undecidable_reason=later.reason.value,
            undecidable_detail=later.detail[:500],
        )

    sign = 1.0 if followup.direction == "increase" else -1.0
    realized = sign * later.primary.point
    ci = later.primary.ci
    realized_ci: tuple[float, float] | None = None
    if ci is not None:
        a, b = sign * ci.lo, sign * ci.hi
        realized_ci = (min(a, b), max(a, b))

    predicted_ci = followup.predicted_ci
    in_ci = (
        predicted_ci[0] <= realized <= predicted_ci[1] if predicted_ci is not None else None
    )
    overlap = (
        None
        if predicted_ci is None or realized_ci is None
        else realized_ci[0] <= predicted_ci[1] and predicted_ci[0] <= realized_ci[1]
    )

    claimed = (
        predicted_ci[0] > followup.minimum_worthwhile_effect if predicted_ci is not None else None
    )
    cleared = harmed = None
    if realized_ci is not None:
        harmful = -followup.harm_threshold if followup.harm_threshold else 0.0
        cleared = realized_ci[0] > followup.minimum_worthwhile_effect
        harmed = realized_ci[1] < harmful

    return Realization(
        decision_digest=followup.decision_digest,
        verdict=followup.verdict,
        predicted_benefit=followup.predicted_benefit,
        predicted_ci=predicted_ci,
        realized_benefit=realized,
        realized_ci=realized_ci,
        decidable=True,
        point_in_predicted_ci=in_ci,
        intervals_overlap=overlap,
        cleared_worthwhile=cleared,
        realized_harm=harmed,
        claimed_worthwhile=claimed,
    )


_NOTE = (
    "Rates are over decidable realizations only, each with a Clopper-Pearson interval, and none "
    "is stated below `min_followups`. The undecidable ones are counted beside them because they "
    "are not missing at random: a decision that was acted on everywhere is exactly the one whose "
    "effect can no longer be measured -- `acted_worthwhile_bounds` carries them as unknowns. "
    "`point_in_interval_rate` is not interval coverage of the truth -- the realized figure is "
    "itself an estimate, with error this comparison ignores; `interval_overlap_rate` is the "
    "weaker statement that accounts for both. Follow-ups are treated as independent; decisions "
    "that share incidents or seasons would make every interval here too narrow. The "
    "report's arithmetic is gated on simulated streams (C0); no figure here is evidence about "
    "real operations until real follow-ups stand behind it."
)


def report(
    realizations: list[Realization],
    *,
    level: float = LEVEL,
    min_followups: int | None = None,
    alpha: float = ALPHA,
) -> CalibrationReport:
    """Aggregate realizations: counts always, rates only with enough behind them.

    One realization per decision. A decision measured twice would count twice, and
    a caller unhappy with one window could outvote it with a more flattering one;
    the store enforces "the first measurement stands", and this refuses a list
    that breaks it rather than silently picking one.
    """
    minimum = MIN_FOLLOWUPS if min_followups is None else min_followups
    seen = Counter(r.decision_digest for r in realizations)
    repeated = sorted(d for d, n in seen.items() if n > 1)
    if repeated:
        raise ValueError(
            f"{len(repeated)} decision(s) have more than one realization, e.g. {repeated[0]}; "
            "calibration takes exactly one per decision"
        )
    acted = [r for r in realizations if r.verdict in ACTED]
    decidable = [r for r in realizations if r.decidable]
    undecidable = [r for r in realizations if not r.decidable]
    acted_decidable = [r for r in acted if r.decidable]

    in_ci = [r.point_in_predicted_ci for r in decidable if r.point_in_predicted_ci is not None]
    overlap = [r.intervals_overlap for r in decidable if r.intervals_overlap is not None]
    worthwhile = [r.cleared_worthwhile for r in acted_decidable if r.cleared_worthwhile is not None]
    harmful = [r.realized_harm for r in acted_decidable if r.realized_harm is not None]

    pairs = [
        (r.claimed_worthwhile, r.cleared_worthwhile)
        for r in decidable
        if r.claimed_worthwhile is not None and r.cleared_worthwhile is not None
    ]

    def est(flags: list[bool]) -> RateEstimate:
        return rate_estimate(sum(flags), len(flags), level=level, min_followups=minimum)

    return CalibrationReport(
        n_decisions=len(realizations),
        n_acted=len(acted),
        n_decidable=len(decidable),
        n_undecidable=len(undecidable),
        undecidable_by_reason=dict(
            Counter(r.undecidable_reason or "UNKNOWN" for r in undecidable)
        ),
        min_followups=minimum,
        point_in_interval_rate=est(in_ci),
        interval_overlap_rate=est(overlap),
        acted_and_worthwhile_rate=est(worthwhile),
        acted_and_harmful_rate=est(harmful),
        acted_worthwhile_bounds=rate_bounds(
            sum(worthwhile), len(acted) - len(worthwhile), len(acted),
            level=level, min_followups=minimum,
        ),
        worthwhile_claimed_vs_confirmed=paired_comparison(
            sum(1 for claimed, confirmed in pairs if claimed and not confirmed),
            sum(1 for claimed, confirmed in pairs if confirmed and not claimed),
            len(pairs),
            alpha=alpha,
            min_followups=minimum,
        ),
        acted_and_harmful=sum(harmful),
        not_acted_but_worthwhile=sum(
            1
            for r in decidable
            if r.verdict not in ACTED and r.cleared_worthwhile
        ),
        by_verdict=dict(Counter(str(r.verdict) for r in realizations)),
        note=_NOTE,
    )


# ── the statistics ────────────────────────────────────────────────────────────


def _interval(successes: int, n: int, level: float) -> tuple[float, float]:
    """Clopper-Pearson: the exact binomial interval, at least `level` coverage at every p.

    Not Wald. p-hat +/- 1.96 se collapses to [1, 1] at 15 of 15 and to [0, 0] at
    none, and its coverage near 0 or 1 is nowhere near its name (C0 measures
    under 0.1 on its grid) -- at the sample sizes a young platform has.
    """
    tail = (1.0 - level) / 2.0
    lo = 0.0 if successes == 0 else float(beta.ppf(tail, successes, n - successes + 1))
    hi = 1.0 if successes == n else float(beta.ppf(1.0 - tail, successes + 1, n - successes))
    return lo, hi


def _refused(n: int, min_followups: int, what: str = "follow-ups") -> str | None:
    """Why no figure is stated from `n`, or None when there are enough."""
    if n >= min_followups:
        return None
    return (
        f"{n} {what} behind this figure; the report states none below {min_followups}, "
        "because a rate from so few is mostly noise to quote"
    )


def rate_estimate(
    successes: int, n: int, *, level: float = LEVEL, min_followups: int | None = None
) -> RateEstimate:
    """``successes`` of ``n`` as a rate with its interval, or refused below the minimum."""
    if not 0 <= successes <= n:
        raise ValueError(f"{successes} successes of {n} is not a proportion")
    minimum = MIN_FOLLOWUPS if min_followups is None else min_followups
    why = _refused(n, minimum)
    if why is not None or n == 0:
        return RateEstimate(
            successes=successes, denominator=n, level=level,
            refused=why or _refused(0, 1),
        )
    return RateEstimate(
        successes=successes,
        denominator=n,
        rate=successes / n,
        ci=_interval(successes, n, level),
        level=level,
    )


def rate_bounds(
    successes: int,
    unresolved: int,
    n: int,
    *,
    level: float = LEVEL,
    min_followups: int | None = None,
) -> RateBounds:
    """Worst-case bounds on a rate when ``unresolved`` of ``n`` could not be measured."""
    if min(successes, unresolved) < 0 or successes + unresolved > n:
        raise ValueError(f"{successes} successes and {unresolved} unresolved of {n} do not add up")
    minimum = MIN_FOLLOWUPS if min_followups is None else min_followups
    why = _refused(n, minimum, "acted decisions")
    if why is not None or n == 0:
        return RateBounds(
            successes=successes, unresolved=unresolved, denominator=n, level=level,
            refused=why or _refused(0, 1, "acted decisions"),
        )
    top = successes + unresolved
    return RateBounds(
        successes=successes,
        unresolved=unresolved,
        denominator=n,
        lower=successes / n,
        upper=top / n,
        ci=(_interval(successes, n, level)[0], _interval(top, n, level)[1]),
        level=level,
    )


def min_discordant(alpha: float = ALPHA) -> int:
    """The fewest discordant pairs at which the exact two-sided test can reach ``alpha``.

    With d discordant pairs the smallest p-value available is 2 * 0.5**d (all of
    them one way). Below this d, no outcome can be significant.
    """
    d = 1
    while 2.0 * 0.5**d > alpha:
        d += 1
    return d


def paired_comparison(
    claimed_not_confirmed: int,
    confirmed_not_claimed: int,
    n_pairs: int,
    *,
    alpha: float = ALPHA,
    min_followups: int | None = None,
) -> PairedComparison:
    """Exact McNemar on the discordant pairs, with "could not resolve" kept apart."""
    b, c = claimed_not_confirmed, confirmed_not_claimed
    if min(b, c) < 0 or b + c > n_pairs:
        raise ValueError(f"{b} + {c} discordant pairs of {n_pairs} do not add up")
    minimum = MIN_FOLLOWUPS if min_followups is None else min_followups
    m = min_discordant(alpha)
    d = b + c
    def result(
        status: Literal["difference", "no_difference_detected", "could_not_resolve"],
        detail: str,
        p_value: float | None = None,
    ) -> PairedComparison:
        return PairedComparison(
            n_pairs=n_pairs, claimed_not_confirmed=b, confirmed_not_claimed=c,
            min_discordant=m, alpha=alpha, p_value=p_value, status=status, detail=detail,
        )

    why = _refused(n_pairs, minimum, "paired follow-ups")
    if why is not None:
        return result("could_not_resolve", why)
    if d < m:
        return result(
            "could_not_resolve",
            f"{d} discordant pair(s): with fewer than {m}, even a split all one way "
            f"has p > {alpha}, so this data could not have shown a difference -- which is "
            "not the same as showing there is none",
        )
    p = min(1.0, 2.0 * float(binom.cdf(min(b, c), d, 0.5)))
    if p <= alpha:
        direction = (
            "claimed more often than confirmed" if b > c else "confirmed more often than claimed"
        )
        return result(
            "difference",
            f"{direction}: {b} claimed but not confirmed, {c} confirmed but not claimed "
            f"(exact McNemar p = {p:.3g})",
            p,
        )
    return result(
        "no_difference_detected",
        f"{b} claimed but not confirmed, {c} confirmed but not claimed (exact McNemar "
        f"p = {p:.3g}); {d} discordant pairs were enough to have shown a difference, and did not",
        p,
    )
