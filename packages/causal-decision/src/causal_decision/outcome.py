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

Everything here is pure: no clock, no store, no network. The caller supplies
timestamps and performs the re-run, so a report can be rebuilt from its records
and re-checked anywhere.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from causal_core.contract import (
    Abstention,
    ApparatusFailure,
    CausalQuery,
    CausalResult,
)
from pydantic import BaseModel, ConfigDict, Field

from causal_decision.contract import CausalOutcome, Decision, Verdict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


#: Verdicts under which the action may actually have been taken. The others
#: produce nothing to observe, so there is nothing to calibrate against.
ACTED = {Verdict.act, Verdict.act_with_approval}


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
    graph: str = Field(description="The graph as given to the original analysis, verbatim.")
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


class CalibrationReport(_Model):
    """What a set of realizations says about the thresholds that produced them.

    Every rate is ``None`` when its denominator is zero. A report over no
    decidable realizations says so; it does not say 100%. This is the same rule
    the repository's gates hold themselves to -- a measurement that inspected
    nothing may not report success -- and it matters most here, because this is
    the number the platform would be judged on.
    """

    n_decisions: int
    n_acted: int
    n_decidable: int
    n_undecidable: int
    undecidable_by_reason: dict[str, int] = Field(default_factory=dict)

    point_in_interval_rate: float | None = None
    interval_overlap_rate: float | None = None
    acted_and_worthwhile_rate: float | None = None
    acted_and_harmful: int = 0

    note: str = Field(
        default="",
        description="What these numbers are not, in the words a reader needs before quoting them.",
    )


# ── building the plan ─────────────────────────────────────────────────────────


def plan(
    decision: Decision,
    result: CausalResult,
    *,
    graph: str,
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
    """The request's horizon, when it gave one. It is free text, so it is not parsed."""
    horizon = decision.request.horizon
    return horizon or None


# ── comparing against what followed ───────────────────────────────────────────


def compare(followup: FollowUp, later: CausalOutcome) -> Realization:
    """Set a prediction beside the re-run, or record why no comparison is possible.

    ``later`` is whatever ``causal_core.analyze`` returned for the observation
    window, run with ``followup.query``. Its refusal is the answer when it
    refuses: this module does not second-guess the kernel about whether the data
    can carry an estimate.
    """
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
    )


_NOTE = (
    "Rates are over decidable realizations only, and the undecidable ones are counted beside "
    "them because they are not missing at random: a decision that was acted on everywhere is "
    "exactly the one whose effect can no longer be measured. `point_in_interval_rate` is not "
    "interval coverage of the truth -- the realized figure is itself an estimate, with error "
    "this comparison ignores; `interval_overlap_rate` is the weaker statement that accounts "
    "for both."
)


def report(realizations: list[Realization]) -> CalibrationReport:
    """Aggregate realizations, reporting None for any rate with nothing behind it."""
    acted = [r for r in realizations if r.verdict in ACTED]
    decidable = [r for r in realizations if r.decidable]
    undecidable = [r for r in realizations if not r.decidable]
    acted_decidable = [r for r in acted if r.decidable]

    in_ci = [r.point_in_predicted_ci for r in decidable if r.point_in_predicted_ci is not None]
    overlap = [r.intervals_overlap for r in decidable if r.intervals_overlap is not None]
    worthwhile = [r.cleared_worthwhile for r in acted_decidable if r.cleared_worthwhile is not None]

    return CalibrationReport(
        n_decisions=len(realizations),
        n_acted=len(acted),
        n_decidable=len(decidable),
        n_undecidable=len(undecidable),
        undecidable_by_reason=dict(
            Counter(r.undecidable_reason or "UNKNOWN" for r in undecidable)
        ),
        point_in_interval_rate=_rate(in_ci),
        interval_overlap_rate=_rate(overlap),
        acted_and_worthwhile_rate=_rate(worthwhile),
        acted_and_harmful=sum(1 for r in acted_decidable if r.realized_harm),
        note=_NOTE,
    )


def _rate(flags: list[bool]) -> float | None:
    """A proportion, or None when there is nothing to take a proportion of."""
    return sum(flags) / len(flags) if flags else None
