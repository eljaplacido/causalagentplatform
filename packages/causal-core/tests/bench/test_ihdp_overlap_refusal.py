"""IHDP's by-construction overlap violation is flagged (T2 v2).

Register entry ``kernel-overlap-detection-limits`` leaves by this file. Hill removed treated
children by mother's race, which is not among the 25 covariates; what it leaves in them is
limited overlap, not a region without treated units. The ATE must be refused for POSITIVITY or
flagged for limited overlap in the registered share of realizations, and the ATT -- which the
design supports -- must not be. Bars from benchmarks/t2-ihdp/PRE-REGISTRATION.md.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.real_data


def test_the_registration_gates_the_overlap_flag(ihdp_cfg: dict[str, Any]) -> None:
    assert ihdp_cfg["meta"]["version"] >= 2
    ov = ihdp_cfg["overlap"]
    assert ov["ate_min_flagged_share"] >= 0.9
    assert ov["att_max_flagged_share"] <= 0.1


def test_the_ate_is_refused_or_flagged_for_the_overlap_violation(
    ihdp_cfg: dict[str, Any], ihdp_result: dict[str, Any]
) -> None:
    ov = ihdp_cfg["overlap"]
    for config in ("C1", "C2"):
        ate = ihdp_result["summary"][f"ate/{config}"]["overlap_flagged_share"]
        att = ihdp_result["summary"][f"att/{config}"]["overlap_flagged_share"]
        assert ate >= ov["ate_min_flagged_share"], (config, ate)
        assert att <= ov["att_max_flagged_share"], (config, att)
    assert not [f for f in ihdp_result["failures"] if "overlap" in f], ihdp_result["failures"]
