"""The T10 positivity and overlap gate as tests. Bars from
benchmarks/t10-positivity/PRE-REGISTRATION.md.

    pytest -m t10
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t10

pytestmark = pytest.mark.t10


def test_the_pre_registration_is_the_one_this_gate_runs(cfg: dict[str, Any]) -> None:
    assert cfg["meta"]["suite"] == "T10"
    assert set(t10.all_design_ids(cfg)) == set(t10.DESIGNS)


def test_the_registered_thresholds_are_the_defaults(cfg: dict[str, Any]) -> None:
    assert not t10.check_method(cfg)


def test_violations_are_refused_and_answerable_designs_answered(
    designs: tuple[list[t10.DesignReport], list[str]],
) -> None:
    _, failures = designs
    assert not failures, failures[:8]


def test_nothing_is_trimmed_and_every_region_refusal_names_its_region(
    designs: tuple[list[t10.DesignReport], list[str]],
) -> None:
    for r in designs[0]:
        assert r.trimmed_results == 0, r
        assert r.unnamed_region_refusals == 0, r
