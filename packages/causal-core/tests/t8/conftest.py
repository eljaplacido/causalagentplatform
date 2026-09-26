"""One run of the registered T8 designs per session, shared by the gate and the
carryover-power tests (the carryover bars are measured on the same replications)."""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t8


@pytest.fixture(scope="session")
def cfg() -> dict[str, Any]:
    return t8.load_prereg()[0]


@pytest.fixture(scope="session")
def estimation(cfg: dict[str, Any]) -> tuple[list[t8.DesignReport], list[str]]:
    return t8.check_estimation(cfg)
