"""
The C0 calibration-report gate as tests. Bars come from
benchmarks/c0-calibration/PRE-REGISTRATION.md; nothing is hard-coded here.

SIMULATED streams with effects set by construction: this proves the report is
honest about what follow-ups can say, not that the platform's thresholds are
calibrated on real operations.

    pytest -m c0
"""

from __future__ import annotations

from typing import Any

import pytest

import causal_decision.outcome as oc
from causal_decision.bench import c0

pytestmark = pytest.mark.c0


@pytest.fixture(scope="module")
def results(cfg: dict[str, Any], pool: dict[tuple[str, bool], list[c0.Member]]
            ) -> list[c0.ConfigResult]:
    return c0.check_streams(cfg, pool)


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "C0"
    assert cfg["report"]["min_followups"] == oc.MIN_FOLLOWUPS, "the report's floor is registered"
    assert cfg["report"]["level"] == oc.LEVEL
    assert cfg["report"]["alpha"] == oc.ALPHA


def test_the_report_interval_covers_at_its_level_at_every_n_and_p(cfg: dict[str, Any]) -> None:
    mn, worst = c0.check_intervals(cfg)
    assert mn >= cfg["report"]["level"] - cfg["exact"]["coverage_tolerance"], (mn, worst)


def test_no_rate_below_the_minimum_and_one_at_or_above_it(cfg: dict[str, Any]) -> None:
    assert c0.check_refusal_exact(cfg) == []


def test_could_not_resolve_is_kept_apart_from_no_difference(cfg: dict[str, Any]) -> None:
    violations, h0 = c0.check_paired_exact(cfg)
    assert violations == [], violations[:5]
    assert h0 <= cfg["report"]["alpha"] + cfg["exact"]["h0_tolerance"]


def test_every_pool_decision_s_flags_are_what_its_type_implies(
    cfg: dict[str, Any], pool_and_violations: tuple[Any, list[str]]
) -> None:
    """The truth is known by construction only if this holds; it is checked, not assumed."""
    pool, violations = pool_and_violations
    assert len(violations) <= cfg["pool"]["type_violations_allowed"], violations[:5]
    assert all(len(v) == cfg["pool"]["per_cell"] for v in pool.values())


def test_every_report_accounts_for_every_follow_up(results: list[c0.ConfigResult]) -> None:
    found = [v for r in results for v in r.accounting]
    assert found == [], found[:5]


def test_the_intervals_cover_the_truth_by_construction(
    cfg: dict[str, Any], results: list[c0.ConfigResult]
) -> None:
    fails = c0.stream_failures(cfg, results)
    assert fails == [], fails


def test_the_cases_depend_on_the_registered_seeds_and_nothing_else(cfg: dict[str, Any]) -> None:
    """A0 v2 run 1 depended on PYTHONHASHSEED; the stream draws here must not.

    What is compared is what the gate reads -- each pool member's cell, verdict and
    flags, and each config's counts -- not the decision digests or raw estimates:
    those move in the last bits with the BLAS thread count (seen: 1-2 threads vs 4+),
    which says nothing about the seed and changes no flag here.
    """
    import os
    import subprocess
    import sys

    probe = (
        "from causal_decision.bench import c0;"
        "cfg = c0.load_prereg()[0];"
        "cfg['pool']['per_cell'] = 2; cfg['streams']['reps'] = 3;"
        "pool, v = c0.build_pool(cfg);"
        "keep = ('verdict', 'decidable', 'undecidable_reason', 'point_in_predicted_ci', "
        "'intervals_overlap', 'cleared_worthwhile', 'realized_harm', 'claimed_worthwhile');"
        "print([(k, [m.realization.model_dump(mode='json', include=set(keep)) for m in ms]) "
        "for k, ms in sorted(pool.items())], [(r.name, r.stated, r.covered, r.refused, "
        "r.paired_status) for r in c0.check_streams(cfg, pool)])"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", probe],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2", "3")
    }
    assert len(outs) == 1, "the generated streams differ between processes"
