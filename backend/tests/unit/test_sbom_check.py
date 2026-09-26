"""
scripts/sbom_check.py -- the release SBOM lists exactly the locked environment.

The committed release lock (requirements/release.txt) is the reference; the SBOM is
written by the test from it, as cyclonedx-py would from an environment installed
from it. A dropped component, a wrong version, an extra one, a missing published
package and a document that is not CycloneDX must each fail.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "sbom_check.py"
PUBLISHED = {"causal-core": "0.9.0", "causal-decision": "0.4.0", "causal-mcp": "0.5.0"}


def _locked() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("py_lock", REPO / "scripts" / "py_lock.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["py_lock"] = mod
    spec.loader.exec_module(mod)
    lock = mod.parse_lock("release", REPO)
    pinned: dict[str, str] = mod.applicable(lock.pins, mod.probe(sys.executable)["markers"])
    return pinned


def _sbom(tmp_path: Path, comps: dict[str, str], **top: Any) -> Path:
    components = [{"name": n, "version": v, "type": "library"} for n, v in comps.items()]
    doc = {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": components, **top}
    p = tmp_path / "sbom.json"
    p.write_text(json.dumps(doc))
    return p


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(SCRIPT), str(path), "--lock", "release"]
    for pkg in PUBLISHED:
        args += ["--package", pkg]
    return subprocess.run(args, capture_output=True, text=True, check=False)


def test_an_sbom_of_the_locked_environment_passes(tmp_path: Path) -> None:
    locked = _locked()
    assert len(locked) > 10
    proc = _run(_sbom(tmp_path, {**locked, **PUBLISHED}))
    assert proc.returncode == 0, proc.stdout
    assert f"{len(locked)} locked pins (release) listed at their versions" in proc.stdout


def test_a_missing_or_misversioned_component_fails(tmp_path: Path) -> None:
    locked = _locked()
    first, second = sorted(locked)[:2]
    comps = {**locked, **PUBLISHED, second: "0.0.0"}
    del comps[first]
    proc = _run(_sbom(tmp_path, comps))
    assert proc.returncode == 1
    assert f"{first}=={locked[first]} is locked; the SBOM does not list it" in proc.stdout
    assert f"{second} is 0.0.0 in the SBOM, {locked[second]} in the lock" in proc.stdout


def test_an_extra_component_or_a_missing_published_package_fails(tmp_path: Path) -> None:
    comps = {**_locked(), "causal-core": "0.9.0", "leftpad": "1.0"}
    proc = _run(_sbom(tmp_path, comps))
    assert proc.returncode == 1
    assert "leftpad==1.0 is in the SBOM but neither locked nor published" in proc.stdout
    assert "the published package causal-mcp is not in the SBOM" in proc.stdout


def test_a_document_that_is_not_cyclonedx_fails(tmp_path: Path) -> None:
    proc = _run(_sbom(tmp_path, {**_locked(), **PUBLISHED}, bomFormat="SPDX"))
    assert proc.returncode == 1 and "not a CycloneDX document" in proc.stdout
