"""A pytest plugin .gnomon/ci.sh loads into every package suite (`-p junit_markers`).

It writes each test's markers into the JUnit report as a `markers` property, so
scripts/gate_scope.py gate-markers can tell from the run's own report whether every
pre-registered gate marker (t0, t4, ..., d0, a0) had a test that PASSED. A JUnit
report alone does not carry markers, and a gate whose tests stop being collected --
a `def test_` renamed, a module-level `pytestmark` dropped -- otherwise leaves the
row green on the strength of every other test in the package.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        names = sorted({m.name for m in item.iter_markers()})
        item.user_properties.append(("markers", ",".join(names)))
