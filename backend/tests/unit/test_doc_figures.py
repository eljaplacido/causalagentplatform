"""doc_figures: the numbers the docs quote match the runs they came from -- and it can fail.

A gate nobody has seen fail certifies nothing. These tests hold today's figures, and
show the checker refusing a figure that drifted, a quote the doc no longer contains,
and a manifest that checks nothing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    import pytest

Manifest = dict[str, Any]

REPO = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doc_figures_under_test",
                                                  REPO / "scripts" / "doc_figures.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: Callable[[Manifest], None]
) -> int:
    df = _load()
    manifest = json.loads((REPO / "scripts" / "doc-figures.json").read_text())
    mutate(manifest)
    target = tmp_path / "doc-figures.json"
    target.write_text(json.dumps(manifest))
    monkeypatch.setattr(df, "MANIFEST", target)
    return int(df.main())


def test_every_bound_figure_matches_today() -> None:
    assert _load().main() == 0


def test_a_figure_that_drifted_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def drift(m: Manifest) -> None:
        entry = next(e for e in m["figures"] if e["figure"] == "94.9")
        entry["path"] = "/sections/estimation/reported_band/0"  # 0.925 -> 92.5, not 94.9

    assert _with(tmp_path, monkeypatch, drift) == 1


def test_a_quote_the_doc_no_longer_says_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(m: Manifest) -> None:
        m["figures"][0]["says"] = "Pooled 95% coverage is 99.9%"
        m["figures"][0]["figure"] = "99.9"

    assert _with(tmp_path, monkeypatch, gone) == 1


def test_a_manifest_that_checks_nothing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def empty(m: Manifest) -> None:
        m["figures"] = []

    assert _with(tmp_path, monkeypatch, empty) == 1


def test_a_range_is_rounded_outward() -> None:
    df = _load()
    assert df.matches(0.9351, "0.93", "min", "outward")
    assert df.matches(0.9649, "0.97", "max", "outward")
    assert not df.matches(0.9351, "0.94", "min", "outward")
    assert df.matches(0.9491, "94.9", "one", "nearest") is False  # unscaled
    assert df.matches(94.91, "94.9", "one", "nearest")


def test_a_run_whose_commit_is_unknown_does_not_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: `git: "unknown"` has no -dirty suffix, and once counted as a clean
    tree. A standing run must name a 40-hex commit that is an ancestor of HEAD."""
    from pytest import raises

    df = _load()
    bench = tmp_path / "benchmarks" / "tx"
    (bench / "results").mkdir(parents=True)
    prereg = b"# tx\n"
    (bench / "PRE-REGISTRATION.md").write_bytes(prereg)
    run = {"prereg_sha256": "sha256:" + hashlib.sha256(prereg).hexdigest(), "passed": True}
    for i, git in enumerate(["unknown", "0" * 40, "abc1234"]):
        (bench / "results" / f"r{i}.json").write_text(json.dumps({**run, "git": git}))
    monkeypatch.setattr(df, "ROOT", tmp_path)
    with raises(LookupError):
        df.standing_run("tx")


def test_the_untraceable_t8_run_is_skipped_for_a_later_one() -> None:
    path, run = _load().standing_run("t8-switchback")
    assert run["git"] != "unknown"
    assert path.name != "2026-09-26-v2-run1.json"
