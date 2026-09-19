"""
The T0 correctness gate as tests. Thresholds come from
benchmarks/t0/PRE-REGISTRATION.md; nothing is hard-coded here.

    pytest -m t0
"""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t0

pytestmark = pytest.mark.t0


@pytest.fixture(scope="module")
def cfg() -> dict[str, Any]:
    return t0.load_prereg()[0]


def test_identification_catalog_matches_literature_labels(cfg: dict[str, Any]) -> None:
    results = t0.check_identification()
    assert len(results) >= 29
    bad = [(r.id, r.expected, r.got) for r in results if not r.passed]
    assert not bad
    # The catalog must exercise both outcomes and every strategy it names.
    kinds = {r.expected for r in results}
    assert {k[0] for k in kinds} == {"identified", "not_identified"}
    assert {k[1] for k in kinds} >= {
        "backdoor",
        "frontdoor",
        "iv",
        "proximal",
        "id_general",
        "randomized",
        None,
    }


def test_id_agrees_with_y0(cfg: dict[str, Any]) -> None:
    pytest.importorskip("y0")
    c = cfg["identification"]
    cross = t0.check_against_y0(
        c["random_admgs"], c["random_nodes_min"], c["random_nodes_max"], c["random_seed"]
    )
    assert cross.ran and cross.graphs == c["random_admgs"]
    assert 0 < cross.identified < cross.graphs, "cross-check saw only one class: it tests nothing"
    assert cross.rate >= c["agreement_required"], cross.disagreements[:5]


def test_estimation_bias_and_coverage(cfg: dict[str, Any]) -> None:
    rep = t0.check_estimation(cfg)
    assert len(rep.cases) == len(t0.ESTIMATION_CASES)
    assert rep.passed, rep.failures


def test_refuter_false_alarms_and_power(cfg: dict[str, Any]) -> None:
    rep = t0.check_refuters(cfg)
    assert rep.passed, rep.failures


def test_triangulation_false_alarms_and_power(cfg: dict[str, Any]) -> None:
    rep = t0.check_triangulation(cfg)
    assert rep.passed, rep.failures
