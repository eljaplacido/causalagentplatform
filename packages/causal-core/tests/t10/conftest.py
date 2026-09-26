"""One run of the registered T10 designs per session, shared by the gate tests."""

from __future__ import annotations

from typing import Any

import pytest

from causal_core.bench import t10


@pytest.fixture(scope="session")
def cfg() -> dict[str, Any]:
    return t10.load_prereg()[0]


@pytest.fixture(scope="session")
def designs(cfg: dict[str, Any]) -> tuple[list[t10.DesignReport], list[str]]:
    return t10.check_designs(cfg)
