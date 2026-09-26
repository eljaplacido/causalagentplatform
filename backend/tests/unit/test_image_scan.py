"""
scripts/image_scan.py -- the container scan fails on a fixable HIGH/CRITICAL finding.

Trivy itself needs Docker and its database, so these tests hand the judge a report
the test writes: a planted fixable finding must fail, an allowlisted one pass, an
expired or stale exception fail, a finding with no published fix be listed without
failing, and a report that lists no package fail.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "image_scan.py"
TODAY = "2026-09-26"


def _report(tmp_path: Path, vulns: list[dict[str, Any]], packages: int = 3) -> Path:
    p = tmp_path / "trivy.json"
    p.write_text(json.dumps({"Results": [{
        "Target": "img (debian 13)", "Class": "os-pkgs",
        "Packages": [{"Name": f"p{i}"} for i in range(packages)],
        "Vulnerabilities": vulns,
    }]}))
    return p


def _vuln(vid: str, pkg: str, fixed: str = "", severity: str = "HIGH") -> dict[str, Any]:
    return {"VulnerabilityID": vid, "PkgName": pkg, "InstalledVersion": "1.0",
            "FixedVersion": fixed, "Severity": severity}


def _allow(tmp_path: Path, entries: list[dict[str, str]]) -> Path:
    p = tmp_path / "allow.json"
    p.write_text(json.dumps({"entries": entries}))
    return p


def _run(report: Path, allow: Path, image: str = "backend") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report), "--image", image,
         "--today", TODAY, "--allowlist", str(allow)],
        capture_output=True, text=True, check=False,
    )


ENTRY = {"id": "CVE-2099-1", "image": "backend", "package": "libfoo",
         "reason": "the affected code path is not reachable", "review_by": "2026-12-31"}


def test_a_fixable_high_finding_fails(tmp_path: Path) -> None:
    rep = _report(tmp_path, [_vuln("CVE-2099-1", "libfoo", fixed="1.1")])
    proc = _run(rep, _allow(tmp_path, []))
    assert proc.returncode == 1, proc.stdout
    assert "FAIL HIGH CVE-2099-1 libfoo 1.0 -- fixed in 1.1" in proc.stdout
    assert "1 blocking" in proc.stdout


def test_a_critical_counted_once_across_layers(tmp_path: Path) -> None:
    v = _vuln("CVE-2099-2", "libbar", fixed="2.0", severity="CRITICAL")
    proc = _run(_report(tmp_path, [v, dict(v)]), _allow(tmp_path, []))
    assert proc.returncode == 1 and proc.stdout.count("FAIL CRITICAL CVE-2099-2") == 1


def test_an_allowlisted_finding_passes(tmp_path: Path) -> None:
    rep = _report(tmp_path, [_vuln("CVE-2099-1", "libfoo", fixed="1.1")])
    proc = _run(rep, _allow(tmp_path, [ENTRY]))
    assert proc.returncode == 0, proc.stdout
    assert "1 allowlisted" in proc.stdout


def test_an_entry_for_the_other_image_does_not_excuse(tmp_path: Path) -> None:
    rep = _report(tmp_path, [_vuln("CVE-2099-1", "libfoo", fixed="1.1")])
    proc = _run(rep, _allow(tmp_path, [{**ENTRY, "image": "frontend"}]))
    assert proc.returncode == 1


def test_an_expired_exception_fails(tmp_path: Path) -> None:
    rep = _report(tmp_path, [_vuln("CVE-2099-1", "libfoo", fixed="1.1")])
    proc = _run(rep, _allow(tmp_path, [{**ENTRY, "review_by": "2026-01-01"}]))
    assert proc.returncode == 1 and "has passed" in proc.stdout


def test_a_stale_exception_fails(tmp_path: Path) -> None:
    proc = _run(_report(tmp_path, []), _allow(tmp_path, [ENTRY]))
    assert proc.returncode == 1 and "no longer reported" in proc.stdout


def test_a_malformed_exception_fails(tmp_path: Path) -> None:
    allow = _allow(tmp_path, [{"id": "CVE-2099-1", "image": "backend"}])
    proc = _run(_report(tmp_path, []), allow)
    assert proc.returncode == 1 and "missing package, reason, review_by" in proc.stdout


def test_a_finding_without_a_published_fix_is_listed_not_failed(tmp_path: Path) -> None:
    rep = _report(tmp_path, [_vuln("CVE-2099-3", "util-linux"),
                             _vuln("CVE-2099-4", "zlib", fixed="1.3", severity="MEDIUM")])
    proc = _run(rep, _allow(tmp_path, []))
    assert proc.returncode == 0, proc.stdout
    assert "note HIGH CVE-2099-3 util-linux 1.0 -- no fixed version published" in proc.stdout
    assert "without a published fix: 1" in proc.stdout
    assert "zlib" not in proc.stdout  # MEDIUM is below the gate


def test_a_report_that_lists_no_package_fails(tmp_path: Path) -> None:
    proc = _run(_report(tmp_path, [], packages=0), _allow(tmp_path, []))
    assert proc.returncode == 1 and "0 packages" in proc.stdout


def test_a_report_that_does_not_parse_fails(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    proc = _run(bad, _allow(tmp_path, []))
    assert proc.returncode == 1 and "not a Trivy JSON report" in proc.stdout


def test_the_committed_allowlist_is_well_formed() -> None:
    spec = importlib.util.spec_from_file_location("image_scan", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["image_scan"] = mod
    spec.loader.exec_module(mod)
    for image in ("backend", "frontend"):
        _, problems = mod.load_allowlist(mod.ALLOWLIST, image, dt.date.today())
        assert problems == [], problems
