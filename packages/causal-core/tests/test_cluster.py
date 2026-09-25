"""Cluster-randomized designs through the kernel's front door."""

from __future__ import annotations

import numpy as np

import causal_core as cc
from causal_core.bench import t7
from causal_core.contract import AbstentionReason, CausalResult, Strategy


def data(seed: int = 3):
    return t7.simulate(t7.DESIGNS["moderate"], np.random.default_rng(seed))


def test_a_cluster_randomized_rollout_gets_cluster_robust_intervals() -> None:
    frame, _ = data()
    out = cc.analyze(frame, t7.GRAPH, treatment="canary", outcome="latency",
                     design="randomized", cluster="service")
    assert isinstance(out, CausalResult), out
    assert out.certificate.strategy is Strategy.randomized
    assert out.diagnostics.n_clusters == 40 and out.diagnostics.icc is not None
    naive = cc.analyze(frame, t7.GRAPH, treatment="canary", outcome="latency",
                       design="randomized")
    assert isinstance(naive, CausalResult)
    assert out.primary.se > 2 * naive.primary.se, "ignoring the clusters understates uncertainty"
    assert out.primary.ci is not None and out.primary.ci.method.startswith("t(K-2)")


def test_treatment_that_varies_within_a_cluster_is_refused() -> None:
    frame, _ = data()
    frame.loc[frame.index[:5], "canary"] = 1 - frame.loc[frame.index[:5], "canary"]
    out = cc.analyze(frame, t7.GRAPH, treatment="canary", outcome="latency",
                     design="randomized", cluster="service")
    assert out.kind == "refusal" and out.reason is AbstentionReason.DESIGN_CHECK_FAILED
