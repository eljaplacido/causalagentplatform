"""Anytime-valid monitoring through the kernel's front door."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

import causal_core as cc
from causal_core.bench import t6
from causal_core.contract import AbstentionReason, CausalResult, Strategy, Tier
from causal_core.sequential import half_width, rho_for


def stream(tau: float = 0.2, p: float = 0.5, n: int = 4000, seed: int = 1):
    return t6.simulate(t6.Stream("x", p, tau), n, np.random.default_rng(seed))


def watch(frame, p: float | None = 0.5, **kw):
    return cc.analyze(frame, t6.GRAPH, treatment="canary", outcome="latency",
                      design="randomized", sequence="arrival", expected_treated_share=p, **kw)


def test_a_watched_canary_gets_a_sequence_valid_at_every_look() -> None:
    out = watch(stream())
    assert isinstance(out, CausalResult), out
    assert out.certificate.strategy is Strategy.randomized and out.certificate.tier is Tier.A
    seq = out.sequential
    assert seq is not None and len(seq.looks) == 39
    assert [lk.n for lk in seq.looks][:2] == [200, 300]
    assert out.primary.ci is not None and out.primary.ci.method.startswith("asymptotic")
    assert out.primary.ci.lo == seq.looks[-1].lo
    widths = [lk.hi - lk.lo for lk in seq.looks]
    assert all(b <= a + 1e-12 for a, b in itertools.pairwise(widths)), (
        "the running intersection never widens"
    )
    assert seq.first_above_zero is not None


def test_the_sequence_is_wider_than_a_fixed_n_interval_and_says_why() -> None:
    """At its target size it costs about 1.55x the fixed-n width: the price of peeking."""
    rho = rho_for(2000, 0.05)
    ratio = half_width(1.0, 2000, rho, 0.05) / (1.959964 / math.sqrt(2000))
    assert 1.45 < ratio < 1.65


def test_monitoring_needs_randomization_and_a_known_allocation() -> None:
    frame = stream()
    obs = cc.analyze(frame, t6.GRAPH, treatment="canary", outcome="latency",
                     sequence="arrival", expected_treated_share=0.5)
    assert obs.kind == "refusal" and "randomized" in obs.detail
    unknown = watch(frame, p=None)
    assert unknown.kind == "refusal" and unknown.reason is AbstentionReason.INVALID_INPUT


def test_a_mismatched_allocation_is_refused() -> None:
    out = watch(stream(p=0.5), p=0.2)
    assert out.kind == "refusal" and out.reason is AbstentionReason.DESIGN_CHECK_FAILED


def test_the_decision_layer_reads_a_sequence_like_any_interval() -> None:
    pytest.importorskip("causal_decision")
    from causal_decision import DecisionRequest, decide

    out = watch(stream(tau=-0.5))
    assert isinstance(out, CausalResult)
    d = decide(out, DecisionRequest(action="continue the canary", outcome="latency",
                                    direction="increase", minimum_worthwhile_effect=0.0))
    assert d.verdict.value == "refuse", d.verdict
