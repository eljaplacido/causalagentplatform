"""The decision layer reads the segments, not only the pooled effect.

An average can describe nobody. On the incident data below, a remediation
playbook takes about twelve minutes off P1 incidents and adds about six to P3;
pooled, that is roughly zero, with an interval straddling it -- "no detectable
effect". Acting on the pooled figure is wrong about both groups, and until
`_segment_checks` existed the decision layer would have said so happily,
because it read `out.primary` and nothing else.

This is the test named in `conformance/declared_not_implemented.json` under
`decision-ignores-segments`.
"""

from __future__ import annotations

import causal_core as cc
import numpy as np
import pandas as pd
import pytest
from causal_core.contract import CausalResult

from causal_decision import DecisionRequest, Verdict, decide


def _incidents(p1_effect: float, p3_effect: float, *, seed: int = 7, n: int = 4000) -> pd.DataFrame:
    """Incidents whose remediation playbook acts differently on P1 and P3.

    `load` confounds the assignment -- busier periods get the playbook more often
    and also run longer -- so the naive comparison is wrong and the analysis has
    something to do.
    """
    rng = np.random.default_rng(seed)
    severity = rng.choice(["P1", "P3"], size=n, p=[0.35, 0.65])
    load = rng.normal(0, 1, n)
    playbook = rng.binomial(1, 1 / (1 + np.exp(-0.9 * load)))
    effect = np.where(severity == "P1", p1_effect, p3_effect)
    minutes = 60 + 8.0 * load + effect * playbook + rng.normal(0, 10, n)
    return pd.DataFrame(
        {"load": load, "playbook": playbook, "minutes": minutes, "severity": severity}
    )


GRAPH = "load -> playbook; load -> minutes; playbook -> minutes"


def _analyse(df: pd.DataFrame, *, segment: bool) -> CausalResult:
    out = cc.analyze(
        df,
        GRAPH,
        treatment="playbook",
        outcome="minutes",
        **({"segment_by": ("severity",)} if segment else {}),
    )
    assert isinstance(out, CausalResult), f"expected a result, got {out.kind}"
    return out


def _request(**over: object) -> DecisionRequest:
    base: dict[str, object] = {
        "action": "apply remediation playbook X",
        "outcome": "minutes",
        "direction": "decrease",
        "minimum_worthwhile_effect": 2.0,
    }
    base.update(over)
    return DecisionRequest(**base)  # type: ignore[arg-type]


def _check(decision: object, name: str) -> object:
    for c in decision.checks:  # type: ignore[attr-defined]
        if c.name == name:
            return c
    raise AssertionError(
        f"no check named {name!r}; got {[c.name for c in decision.checks]}"  # type: ignore[attr-defined]
    )


# ── the case the register describes ───────────────────────────────────────────


@pytest.fixture(scope="module")
def opposed() -> CausalResult:
    """-12 minutes on P1, +6 on P3: the effect reverses across the segments."""
    return _analyse(_incidents(-12.0, 6.0), segment=True)


def test_the_pooled_effect_really_does_describe_nobody(opposed: CausalResult) -> None:
    """The premise of this whole test file, asserted rather than assumed."""
    assert opposed.heterogeneity is not None and opposed.heterogeneity.differs
    levels = {s.level: s for s in opposed.segments}
    assert levels["P1"].estimate is not None and levels["P1"].estimate.point < -8
    assert levels["P3"].estimate is not None and levels["P3"].estimate.point > 3
    # and yet, pooled:
    assert abs(opposed.primary.point) < 3


def test_an_action_that_harms_a_segment_is_not_automated(opposed: CausalResult) -> None:
    decision = decide(opposed, _request())
    assert decision.verdict is not Verdict.act, (
        "the playbook adds about six minutes to P3 incidents; automating it on a "
        f"pooled estimate of {opposed.primary.point:.2f} is the failure this check exists for"
    )


def test_the_harmed_segment_is_named_in_the_check(opposed: CausalResult) -> None:
    check = _check(decide(opposed, _request()), "no declared segment is materially harmed")
    assert not check.passed  # type: ignore[attr-defined]
    assert "P3" in check.detail  # type: ignore[attr-defined]
    assert "P1" not in check.detail, "P1 is helped; naming it as harmed would be wrong"  # type: ignore[attr-defined]


def test_heterogeneity_alone_caps_at_approval(opposed: CausalResult) -> None:
    check = _check(
        decide(opposed, _request()), "the effect is the same across the declared segments"
    )
    assert not check.passed  # type: ignore[attr-defined]
    assert check.allows is Verdict.act_with_approval  # type: ignore[attr-defined]


def test_the_segments_are_carried_into_the_evidence(opposed: CausalResult) -> None:
    """A decision record has to stand alone, so it copies what it rested on."""
    ev = decide(opposed, _request()).evidence
    assert {s.level for s in ev.segments} == {"P1", "P3"}
    assert ev.heterogeneity_differs is True
    p3 = next(s for s in ev.segments if s.level == "P3")
    # read in the requested direction: fewer minutes is the benefit, so P3 is negative
    assert p3.benefit is not None and p3.benefit < 0


def test_the_family_wise_interval_is_the_one_used(opposed: CausalResult) -> None:
    """Having looked at two subgroups, the unadjusted interval no longer means what it says."""
    ev = decide(opposed, _request()).evidence
    p3_seg = next(s for s in opposed.segments if s.level == "P3")
    p3_ev = next(s for s in ev.segments if s.level == "P3")
    assert p3_seg.family_wise_ci is not None and p3_ev.benefit_ci is not None
    width_used = p3_ev.benefit_ci[1] - p3_ev.benefit_ci[0]
    family_wise_width = p3_seg.family_wise_ci.hi - p3_seg.family_wise_ci.lo
    own_width = p3_seg.estimate.ci.hi - p3_seg.estimate.ci.lo  # type: ignore[union-attr]
    assert width_used == pytest.approx(family_wise_width)
    assert family_wise_width > own_width, "the adjusted interval should be the wider one"


# ── the case that isolates what these checks add ──────────────────────────────


def test_a_strong_pooled_benefit_hiding_a_harmed_segment_is_caught() -> None:
    """The test that would fail without `_segment_checks`, and passes with them.

    In `opposed` above the pooled interval also sits below the worthwhile bar,
    so the pooled checks refuse on their own and the segment checks change
    nothing -- a test built on it proves the feature works only by coincidence.

    Here the pooled evidence is unambiguously good: P1 is 65% of the incidents
    and the playbook takes twenty minutes off them, so pooled it is a large,
    precise, worthwhile saving, and every pooled check passes. P3 is still made
    six minutes worse. Declaring the subgroups is the only thing that can see it.
    """
    df = _incidents(-20.0, 6.0, seed=11)
    df["severity"] = np.where(
        np.random.default_rng(11).random(len(df)) < 0.65, "P1", "P3"
    )
    # rebuild the outcome so severity and effect stay consistent after the reweight
    effect = np.where(df["severity"] == "P1", -20.0, 6.0)
    rng = np.random.default_rng(12)
    df["minutes"] = (
        60 + 8.0 * df["load"] + effect * df["playbook"] + rng.normal(0, 10, len(df))
    )

    blind = decide(_analyse(df, segment=False), _request())
    assert blind.verdict is Verdict.act, (
        "this fixture is supposed to look excellent pooled; if it does not, the test "
        f"cannot show what segments add (got {blind.verdict.value})"
    )

    seeing = decide(_analyse(df, segment=True), _request())
    assert seeing.verdict is not Verdict.act, (
        "the same data, with the subgroups declared, still automated an action that "
        "adds six minutes to every P3 incident"
    )
    harmed = _check(seeing, "no declared segment is materially harmed")
    assert not harmed.passed and "P3" in harmed.detail  # type: ignore[attr-defined]


# ── the cases that must not change ────────────────────────────────────────────


def test_a_uniform_effect_passes_the_segment_checks() -> None:
    """Same effect in both groups: the segments agree and nothing is capped."""
    out = _analyse(_incidents(-12.0, -12.0), segment=True)
    decision = decide(out, _request())
    assert _check(decision, "no declared segment is materially harmed").passed  # type: ignore[attr-defined]
    same = _check(decision, "the effect is the same across the declared segments")
    assert same.passed, "Cochran's Q should not separate two identical segments"  # type: ignore[attr-defined]
    assert decision.verdict is Verdict.act


def test_declaring_no_segments_adds_no_segment_checks() -> None:
    """An empty segment list means nobody asked -- which is not evidence of uniformity."""
    out = _analyse(_incidents(-12.0, 6.0), segment=False)
    decision = decide(out, _request())
    names = [c.name for c in decision.checks]
    assert not any("segment" in n for n in names), (
        f"segment checks ran without declared segments: {names}"
    )
    assert decision.evidence.segments == ()
    assert decision.evidence.heterogeneity_differs is None


def test_a_segment_that_could_not_be_estimated_needs_a_person() -> None:
    """A group the analysis could not speak for is not a group it cleared."""
    df = _incidents(-12.0, -12.0)
    # A third severity with a handful of rows, none of them treated: the kernel
    # refuses this segment rather than guessing at it.
    tiny = df.head(6).copy()
    tiny["severity"] = "P5"
    tiny["playbook"] = 0
    out = _analyse(pd.concat([df, tiny], ignore_index=True), segment=True)

    # The fixture is built so the kernel refuses P5; if it ever estimates it, this test
    # has stopped testing what it names, and says so (it used to skip, which read as green).
    unestimated = [s for s in out.segments if s.estimate is None]
    assert unestimated, (
        "the kernel estimated every segment, P5 included: the fixture plants none it refuses"
    )

    decision = decide(out, _request())
    check = _check(decision, "every declared segment could be estimated")
    assert not check.passed  # type: ignore[attr-defined]
    assert check.allows is Verdict.act_with_approval  # type: ignore[attr-defined]
    assert decision.verdict is not Verdict.act
