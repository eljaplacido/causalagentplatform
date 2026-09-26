"""The IHDP run is shared by every test that reads it: the T2 bars and the overlap bars (v2)."""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import ihdp, lalonde
from tests.bench._data import IHDP_FILES, need


@pytest.fixture(scope="session")
def ihdp_cfg() -> dict[str, Any]:
    return lalonde.load_prereg(ihdp.prereg_path())[0]


@pytest.fixture(scope="session")
def ihdp_result(ihdp_cfg: dict[str, Any]) -> dict[str, Any]:
    need(IHDP_FILES, "ihdp")
    return ihdp.run(ihdp_cfg, ihdp.realizations(ihdp.fetch(ihdp_cfg)))
