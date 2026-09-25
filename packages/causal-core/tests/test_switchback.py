"""Switchback designs through the kernel's front door."""

from __future__ import annotations

import numpy as np

from causal_core.bench import t8
from causal_core.contract import AbstentionReason, CausalResult


def test_a_switchback_is_analysed_block_by_block_after_a_washout() -> None:
    cfg = t8.load_prereg()[0]
    frame = t8.simulate(t8.DESIGNS["carryover"], cfg, np.random.default_rng(2))
    out = t8.analyse(frame, cfg)
    assert isinstance(out, CausalResult), out
    assert out.diagnostics.n_clusters == 168
    assert out.diagnostics.n_used == 168 * 40, "the first 20 minutes of every block are dropped"
    assert any(c.name == "no carryover beyond the washout" for c in out.diagnostics.checks)


def test_a_washout_as_long_as_a_block_is_refused() -> None:
    cfg = t8.load_prereg()[0]
    frame = t8.simulate(t8.DESIGNS["instant"], cfg, np.random.default_rng(3))
    out = t8.analyse(frame, cfg, washout=60)
    assert out.kind == "refusal" and out.reason is AbstentionReason.INVALID_INPUT
