"""Scoring a verdict against what followed, including when nothing can be scored.

The interesting half of this module is the refusals. A platform that only
reported calibration for the decisions whose effect stayed measurable would be
scored on the most flattering subset available -- and "we rolled it out to
everyone" is exactly the case that disappears.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import causal_core as cc
import numpy as np
import pandas as pd
import pytest
from causal_core.contract import Abstention, ApparatusFailure, CausalResult, Provenance

from causal_decision import (
    DecisionRequest,
    Verdict,
    compare,
    decide,
    plan,
    report,
)
from causal_decision.outcome import (
    MIN_FOLLOWUPS,
    Realization,
    min_discordant,
    paired_comparison,
    rate_bounds,
    rate_estimate,
)

GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"
NOW = datetime.now(UTC).isoformat()


def incidents(effect: float, *, treated_share: float | None = None, n: int = 3000, seed: int = 5):
    """Incidents with a confounded playbook. `treated_share` forces a rollout level."""
    rng = np.random.default_rng(seed)
    load = rng.normal(0, 1, n)
    if treated_share is None:
        playbook = rng.binomial(1, 1 / (1 + np.exp(-0.9 * load))).astype(float)
    else:
        playbook = (rng.random(n) < treated_share).astype(float)
    minutes = 60 + 8.0 * load + effect * playbook + rng.normal(0, 10, n)
    return pd.DataFrame({"load": load, "playbook": playbook, "minutes": minutes})


def analyse(df: pd.DataFrame):
    return cc.analyze(df, GRAPH, treatment="playbook", outcome="minutes")


REQUEST = DecisionRequest(
    action="apply remediation playbook X",
    outcome="minutes",
    direction="decrease",
    minimum_worthwhile_effect=2.0,
)


@pytest.fixture(scope="module")
def decided():
    """A decision on a real analysis, with the follow-up plan recorded beside it."""
    out = analyse(incidents(-12.0))
    assert isinstance(out, CausalResult)
    d = decide(out, REQUEST)
    return d, out, plan(d, out, graph=GRAPH, observe_from=NOW)


# ── the plan ──────────────────────────────────────────────────────────────────


def test_the_plan_copies_the_prediction_rather_than_referencing_it(decided) -> None:
    """A follow-up is evidence about a verdict only if it scores the number that verdict used."""
    d, out, fu = decided
    assert fu.decision_digest == d.decision_digest
    assert fu.predicted_benefit == d.evidence.benefit
    assert fu.predicted_ci == d.evidence.benefit_ci
    assert fu.graph == GRAPH
    assert fu.query == out.query, "re-running with a different estimand scores a different claim"


def test_a_decision_without_a_digest_cannot_be_followed_up(decided) -> None:
    d, out, _ = decided
    orphan = d.model_copy(update={"decision_digest": None})
    with pytest.raises(ValueError, match="no digest"):
        plan(orphan, out, graph=GRAPH, observe_from=NOW)


# ── a comparison that can be made ─────────────────────────────────────────────


def test_a_prediction_that_held(decided) -> None:
    _, _, fu = decided
    later = analyse(incidents(-12.0, seed=99))
    r = compare(fu, later)

    assert r.decidable and r.undecidable_reason is None
    assert r.realized_benefit is not None and r.realized_benefit > 8
    assert r.point_in_predicted_ci is True
    assert r.intervals_overlap is True
    assert r.cleared_worthwhile is True
    assert r.realized_harm is False


def test_a_prediction_that_did_not_hold(decided) -> None:
    """The action turns out to make things worse. This is the expensive case."""
    _, _, fu = decided
    later = analyse(incidents(+9.0, seed=99))
    r = compare(fu, later)

    assert r.decidable
    assert r.realized_benefit is not None and r.realized_benefit < 0
    assert r.point_in_predicted_ci is False
    assert r.intervals_overlap is False, "predicting -12 and realising +9 is not a near miss"
    assert r.cleared_worthwhile is False
    assert r.realized_harm is True


def test_the_realized_effect_is_read_in_the_requested_direction(decided) -> None:
    """`direction="decrease"` means fewer minutes is a positive benefit, both times."""
    _, _, fu = decided
    later = analyse(incidents(-12.0, seed=77))
    r = compare(fu, later)
    assert isinstance(later, CausalResult)
    assert later.primary.point < 0 < r.realized_benefit  # type: ignore[operator]


# ── the comparisons that cannot be made ───────────────────────────────────────


def test_a_full_rollout_is_undecidable_with_the_kernel_s_own_reason(decided) -> None:
    """Everyone treated: there is no comparison group left, and no later data recovers one."""
    _, _, fu = decided
    later = analyse(incidents(-12.0, treated_share=1.0, seed=99))
    assert isinstance(later, Abstention), "the kernel should refuse a single-armed design"

    r = compare(fu, later)
    assert not r.decidable
    assert r.undecidable_reason == later.reason.value
    assert r.realized_benefit is None
    assert r.point_in_predicted_ci is None, "an undecidable case must not score as a miss"


def test_a_near_total_rollout_is_undecidable_too(decided) -> None:
    _, _, fu = decided
    later = analyse(incidents(-12.0, treated_share=0.995, seed=99))
    assert isinstance(later, Abstention)
    r = compare(fu, later)
    assert not r.decidable and r.undecidable_reason == later.reason.value


def test_an_apparatus_failure_is_undecidable_and_names_its_stage(decided) -> None:
    _, _, fu = decided
    boom = ApparatusFailure(
        stage="estimation",
        error_type="LinAlgError",
        detail="singular matrix",
        provenance=Provenance(contract_version=cc.CONTRACT_VERSION, core_version=cc.__version__),
    )
    r = compare(fu, boom)
    assert not r.decidable
    assert r.undecidable_reason == "LinAlgError"
    assert "estimation" in r.undecidable_detail


# ── the report ────────────────────────────────────────────────────────────────


_digests = itertools.count()


def _realization(**over) -> Realization:
    """One realization per decision, so each gets its own digest unless a test says otherwise."""
    base = dict(
        decision_digest=f"sha256:{next(_digests):064d}",
        verdict=Verdict.act,
        predicted_benefit=12.0,
        predicted_ci=(8.0, 16.0),
        realized_benefit=11.0,
        realized_ci=(7.0, 15.0),
        decidable=True,
        point_in_predicted_ci=True,
        intervals_overlap=True,
        cleared_worthwhile=True,
        realized_harm=False,
    )
    base.update(over)
    return Realization(**base)  # type: ignore[arg-type]


def test_a_report_over_nothing_reports_nothing() -> None:
    """The rule the gates hold themselves to, applied to the number the platform is judged on."""
    empty = report([])
    assert empty.n_decisions == 0
    assert empty.point_in_interval_rate.rate is None, "no data must not read as perfect calibration"
    assert empty.interval_overlap_rate.rate is None
    assert empty.acted_and_worthwhile_rate.rate is None
    assert empty.acted_worthwhile_bounds.ci is None
    assert empty.worthwhile_claimed_vs_confirmed.status == "could_not_resolve"


def test_a_report_of_only_undecidable_cases_reports_no_rate() -> None:
    entries = [
        _realization(
            decidable=False,
            undecidable_reason="DESIGN_CHECK_FAILED",
            realized_benefit=None,
            realized_ci=None,
            point_in_predicted_ci=None,
            intervals_overlap=None,
            cleared_worthwhile=None,
            realized_harm=None,
        )
        for _ in range(4)
    ]
    rep = report(entries)
    assert rep.n_undecidable == 4 and rep.n_decidable == 0
    assert rep.point_in_interval_rate.rate is None
    assert rep.point_in_interval_rate.denominator == 0
    assert rep.undecidable_by_reason == {"DESIGN_CHECK_FAILED": 4}


def test_undecidable_cases_are_counted_beside_the_rates_not_hidden() -> None:
    """They are not missing at random: a full rollout is exactly what becomes unmeasurable."""
    entries = [_realization(), _realization(point_in_predicted_ci=False)] + [
        _realization(
            decidable=False,
            undecidable_reason="POSITIVITY",
            point_in_predicted_ci=None,
            intervals_overlap=None,
            cleared_worthwhile=None,
            realized_harm=None,
        )
        for _ in range(6)
    ]
    rep = report(entries, min_followups=1)
    assert rep.n_decisions == 8
    assert rep.point_in_interval_rate.rate == pytest.approx(0.5), "rates are over the decidable two"
    assert rep.point_in_interval_rate.denominator == 2
    assert rep.n_undecidable == 6
    assert rep.undecidable_by_reason == {"POSITIVITY": 6}
    assert "not missing at random" in rep.note


def test_harm_under_an_act_verdict_is_counted() -> None:
    entries = [
        _realization(),
        _realization(verdict=Verdict.act, realized_harm=True, cleared_worthwhile=False),
        _realization(verdict=Verdict.refuse, realized_harm=True, cleared_worthwhile=False),
    ]
    rep = report(entries, min_followups=1)
    assert rep.acted_and_harmful == 1, "a refused action that would have harmed is not our harm"
    assert rep.n_acted == 2
    assert rep.acted_and_worthwhile_rate.rate == pytest.approx(0.5)
    assert rep.acted_and_harmful_rate.rate == pytest.approx(0.5)


def test_the_note_says_what_the_rate_is_not() -> None:
    """This number will be quoted; the caveat travels with it or it will not travel."""
    rep = report([_realization()])
    assert "not interval coverage of the truth" in rep.note


def test_one_decision_measured_twice_is_refused_not_averaged() -> None:
    """Otherwise a caller unhappy with one window outvotes it with a friendlier one."""
    digest = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="more than one realization"):
        report([_realization(decision_digest=digest), _realization(decision_digest=digest)])


def test_turned_down_opportunities_are_counted() -> None:
    """The mirror of acted_and_harmful: a policy tuned only against harm hides these."""
    entries = [
        _realization(verdict=Verdict.refuse, cleared_worthwhile=True),
        _realization(verdict=Verdict.experiment, cleared_worthwhile=True),
        _realization(verdict=Verdict.refuse, cleared_worthwhile=False),
        _realization(verdict=Verdict.act, cleared_worthwhile=True),
    ]
    rep = report(entries)
    assert rep.not_acted_but_worthwhile == 2
    assert rep.by_verdict == {"refuse": 2, "experiment": 1, "act": 1}


def test_a_re_run_that_asked_a_different_question_is_refused(decided) -> None:
    """The plan fixes the estimand; the library holds that, not only the HTTP route."""
    _, _, fu = decided
    later = cc.analyze(
        incidents(-12.0, seed=9), GRAPH, treatment="playbook", outcome="minutes", estimand="att"
    )
    with pytest.raises(ValueError, match="different query"):
        compare(fu, later)


def test_a_free_text_horizon_is_not_passed_off_as_a_timestamp(decided) -> None:
    d, out, _ = decided
    loose = d.model_copy(update={"request": d.request.model_copy(update={"horizon": "30 days"})})
    assert plan(loose, out, graph=GRAPH, observe_from=NOW).observe_until is None
    exact = d.model_copy(
        update={"request": d.request.model_copy(update={"horizon": "2026-12-31T00:00:00+00:00"})}
    )
    assert plan(exact, out, graph=GRAPH, observe_from=NOW).observe_until == (
        "2026-12-31T00:00:00+00:00"
    )


# ── how much a rate may say ───────────────────────────────────────────────────


def test_a_rate_from_fewer_than_the_minimum_is_refused_not_stated() -> None:
    """The old report said 100% from one follow-up. Counts stay; the division does not."""
    rep = report([_realization() for _ in range(MIN_FOLLOWUPS - 1)])
    est = rep.point_in_interval_rate
    assert est.successes == est.denominator == MIN_FOLLOWUPS - 1
    assert est.rate is None and est.ci is None
    assert est.refused is not None and str(MIN_FOLLOWUPS) in est.refused


def test_at_the_minimum_a_rate_is_stated_with_its_interval() -> None:
    rep = report([_realization() for _ in range(MIN_FOLLOWUPS)])
    est = rep.point_in_interval_rate
    assert est.rate == 1.0 and est.refused is None
    assert est.ci is not None and est.ci[1] == 1.0


def test_the_interval_is_clopper_pearson() -> None:
    """Closed forms at the edges: k = n gives lower = (alpha/2)**(1/n), k = 0 the mirror."""
    ten_of_ten = rate_estimate(10, 10)
    none_of_ten = rate_estimate(0, 10)
    assert ten_of_ten.ci == pytest.approx((0.025 ** (1 / 10), 1.0))
    assert none_of_ten.ci == pytest.approx((0.0, 1 - 0.025 ** (1 / 10)))
    # 10/10 cannot exclude 0.69: the reason the minimum is where it is.
    assert ten_of_ten.ci[0] < 0.70  # type: ignore[index]


def test_the_interval_does_not_collapse_where_wald_does() -> None:
    est = rate_estimate(15, 15)
    assert est.ci is not None and est.ci[0] < 0.8, "Wald would say [1, 1]"


def test_bounds_carry_the_unmeasured_as_unknowns() -> None:
    b = rate_bounds(6, 4, 20)
    assert (b.lower, b.upper) == (pytest.approx(0.3), pytest.approx(0.5))
    assert b.ci is not None
    assert b.ci[0] == pytest.approx(rate_estimate(6, 20).ci[0])  # type: ignore[index]
    assert b.ci[1] == pytest.approx(rate_estimate(10, 20).ci[1])  # type: ignore[index]


def test_undecidable_acted_decisions_widen_the_bounds_not_the_rate() -> None:
    decided_ = [_realization() for _ in range(10)]
    rolled = [
        _realization(decidable=False, undecidable_reason="DESIGN_CHECK_FAILED",
                     realized_benefit=None, realized_ci=None, point_in_predicted_ci=None,
                     intervals_overlap=None, cleared_worthwhile=None, realized_harm=None)
        for _ in range(10)
    ]
    rep = report(decided_ + rolled)
    assert rep.acted_and_worthwhile_rate.rate == 1.0
    assert rep.acted_worthwhile_bounds.unresolved == 10
    assert (rep.acted_worthwhile_bounds.lower, rep.acted_worthwhile_bounds.upper) == (0.5, 1.0)


# ── claimed against confirmed ─────────────────────────────────────────────────


def test_the_fewest_discordant_pairs_that_can_reach_significance() -> None:
    """2 * 0.5**5 = 0.0625 > 0.05; 2 * 0.5**6 = 0.03125."""
    assert min_discordant(0.05) == 6
    assert min_discordant(0.01) == 8


def test_too_few_discordant_pairs_cannot_resolve_rather_than_read_as_no_difference() -> None:
    got = paired_comparison(5, 0, 40)
    assert got.status == "could_not_resolve", "5 of 5 one way still has p = 0.0625"
    assert got.p_value is None


def test_enough_discordant_pairs_all_one_way_is_a_difference() -> None:
    got = paired_comparison(6, 0, 40)
    assert got.status == "difference"
    assert got.p_value == pytest.approx(2 * 0.5**6)


def test_a_balanced_split_that_could_have_shown_one_is_no_difference_detected() -> None:
    got = paired_comparison(5, 5, 40)
    assert got.status == "no_difference_detected"
    assert got.p_value == pytest.approx(1.0)


def test_too_few_pairs_cannot_resolve_whatever_the_split() -> None:
    assert paired_comparison(8, 0, MIN_FOLLOWUPS - 1).status == "could_not_resolve"


def test_compare_records_what_the_analysis_claimed(decided) -> None:
    _, _, fu = decided
    r = compare(fu, analyse(incidents(0.0, seed=78)))
    assert fu.predicted_ci is not None
    assert r.claimed_worthwhile is (fu.predicted_ci[0] > fu.minimum_worthwhile_effect)
    assert r.claimed_worthwhile is True and r.cleared_worthwhile is False


def test_a_realization_stored_before_the_claim_was_kept_still_loads() -> None:
    """Payloads recorded by 0.4.x lack the field; they load, and simply do not pair."""
    old = _realization().model_dump(exclude={"claimed_worthwhile"})
    r = Realization.model_validate(old)
    assert r.claimed_worthwhile is None
    assert report([r]).worthwhile_claimed_vs_confirmed.n_pairs == 0
