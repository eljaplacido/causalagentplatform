"""Where the real-data benchmarks' data are, and skipping when they are not."""

from __future__ import annotations

import os

import pytest

IHDP_FILES = [f"ihdp_npci_1-100.{s}.npz" for s in ("train", "test")]


def cached(names: list[str], sub: str) -> bool:
    base = os.environ.get("CAUSAL_CORE_CACHE") or os.path.expanduser("~/.cache/causal-core")
    return all(os.path.exists(os.path.join(base, sub, n)) for n in names)


def need(names: list[str], sub: str) -> None:
    if not cached(names, sub) and os.environ.get("CAUSAL_FETCH") != "1":
        pytest.skip(f"{sub} data not cached; set CAUSAL_FETCH=1 to download it")
