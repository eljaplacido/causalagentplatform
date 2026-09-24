"""Scoring a verdict against what followed, including when nothing can be scored.

The interesting half of this module is the refusals. A platform that only
reported calibration for the decisions whose effect stayed measurable would be
scored on the most flattering subset available -- and "we rolled it out to
everyone" is exactly the case that disappears.
"""

from __future__ import annotations

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
from causal_decision.outcome import Realization

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


def _realization(**over) -> Realization:
    base = dict(
        decision_digest="sha256:" + "0" * 64,
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
    assert empty.point_in_interval_rate is None, "no data must not read as perfect calibration"
    assert empty.interval_overlap_rate is None
    assert empty.acted_and_worthwhile_rate is None


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
    assert rep.point_in_interval_rate is None
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
    rep = report(entries)
    assert rep.n_decisions == 8
    assert rep.point_in_interval_rate == pytest.approx(0.5), "rates are over the decidable two"
    assert rep.n_undecidable == 6
    assert rep.undecidable_by_reason == {"POSITIVITY": 6}
    assert "not missing at random" in rep.note


def test_harm_under_an_act_verdict_is_counted() -> None:
    entries = [
        _realization(),
        _realization(verdict=Verdict.act, realized_harm=True, cleared_worthwhile=False),
        _realization(verdict=Verdict.refuse, realized_harm=True, cleared_worthwhile=False),
    ]
    rep = report(entries)
    assert rep.acted_and_harmful == 1, "a refused action that would have harmed is not our harm"
    assert rep.n_acted == 2
    assert rep.acted_and_worthwhile_rate == pytest.approx(0.5)


def test_the_note_says_what_the_rate_is_not() -> None:
    """This number will be quoted; the caveat travels with it or it will not travel."""
    rep = report([_realization()])
    assert "not interval coverage of the truth" in rep.note
