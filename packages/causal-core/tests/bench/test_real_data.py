"""
The real-data benchmarks as tests: T2 IHDP and T3 LaLonde, with their pre-registered bars.

The data have no redistribution licence and are never committed. These tests use the
local cache (~/.cache/causal-core, or $CAUSAL_CORE_CACHE) and download into it only when
CAUSAL_FETCH=1, so an offline run skips instead of failing.
"""

from __future__ import annotations

import os

import pytest

from causal_core.bench import ihdp, lalonde

pytestmark = pytest.mark.real_data


def _cached(names: list[str], sub: str) -> bool:
    base = os.environ.get("CAUSAL_CORE_CACHE") or os.path.expanduser("~/.cache/causal-core")
    return all(os.path.exists(os.path.join(base, sub, n)) for n in names)


def _need(names: list[str], sub: str) -> None:
    if not _cached(names, sub) and os.environ.get("CAUSAL_FETCH") != "1":
        pytest.skip(f"{sub} data not cached; set CAUSAL_FETCH=1 to download it")


def test_lalonde_no_confident_wrong_answer() -> None:
    _need([f"{n}.dta" for n in lalonde.FILES], "lalonde")
    cfg, _ = lalonde.load_prereg()
    result = lalonde.run(cfg, lalonde.load(lalonde.fetch(cfg)))
    assert result["passed"], result["failures"]
    assert not result["confident_wrong"]


def test_ihdp_att_answered_and_covered_no_confident_misses() -> None:
    _need([f"ihdp_npci_1-100.{s}.npz" for s in ("train", "test")], "ihdp")
    cfg, _ = lalonde.load_prereg(ihdp.prereg_path())
    result = ihdp.run(cfg, ihdp.realizations(ihdp.fetch(cfg)))
    assert result["passed"], result["failures"]
