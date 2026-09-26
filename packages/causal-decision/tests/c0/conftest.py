"""The C0 pool is 240 decisions through the whole path; build it once per session."""

from __future__ import annotations

from typing import Any

import pytest

from causal_decision.bench import c0


@pytest.fixture(scope="session")
def cfg() -> dict[str, Any]:
    return c0.load_prereg()[0]


@pytest.fixture(scope="session")
def pool_and_violations(cfg: dict[str, Any]) -> tuple[dict[tuple[str, bool], list[c0.Member]],
                                                      list[str]]:
    return c0.build_pool(cfg)


@pytest.fixture(scope="session")
def pool(pool_and_violations: tuple[Any, list[str]]) -> dict[tuple[str, bool], list[c0.Member]]:
    return pool_and_violations[0]  # type: ignore[no-any-return]
