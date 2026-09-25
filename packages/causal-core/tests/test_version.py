"""The version the code reports is the version it was built as.

It used to be read from installed metadata, which an editable install writes
once: after a bump to 0.4.0 both development environments still reported 0.3.0
(and a bare source checkout "0.2.0.dev0+src"). causal-core hashes that string
into every result's provenance, so the same analysis carried a different
result_digest on a laptop than in CI.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

import causal_core as cc
from causal_core._version import __version__

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_reported_version_is_the_source_version() -> None:
    assert cc.__version__ == __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+([ab]\d+|rc\d+|\.dev\d+)?", __version__)


def test_pyproject_reads_its_version_from_the_same_file() -> None:
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in meta["project"], "a second, static copy would drift again"
    assert meta["project"]["dynamic"] == ["version"]
    assert meta["tool"]["hatch"]["version"]["path"] == "src/causal_core/_version.py"


def test_provenance_carries_the_source_version() -> None:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(0)
    x = rng.binomial(1, 0.5, 200).astype(float)
    df = pd.DataFrame({"x": x, "y": x + rng.normal(size=200)})
    out = cc.analyze(df, "x -> y", treatment="x", outcome="y", design="randomized")
    assert out.provenance.core_version == __version__
