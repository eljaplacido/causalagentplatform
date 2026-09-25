"""
scripts/dep_audit.py -- the gate's dependency audits fail on a known advisory.

The tools themselves (pip-audit, npm audit) need the network, so these tests stand
in fakes that print a report the test writes: a planted vulnerable package must
fail the step, an allowlisted one pass, an expired or stale exception fail, and an
unreachable advisory database be NOT MEASURED (exit 2), never a pass.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "dep_audit.py"
TODAY = "2026-09-25"


def _fake_tool(tmp_path: Path, name: str, stdout: str, stderr: str = "", code: int = 0) -> Path:
    """An executable that prints what the test wrote, and exits with `code`."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (tmp_path / f"{name}.out").write_text(stdout)
    (tmp_path / f"{name}.err").write_text(stderr)
    tool = bindir / name
    tool.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:2] == ['--version']:\n"
        "    print('10.0.0'); sys.exit(0)\n"
        f"sys.stdout.write(open({str(tmp_path / f'{name}.out')!r}).read())\n"
        f"sys.stderr.write(open({str(tmp_path / f'{name}.err')!r}).read())\n"
        f"sys.exit({code})\n"
    )
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return tool


def _allowlist(tmp_path: Path, entries: list[dict[str, str]]) -> Path:
    p = tmp_path / "allow.json"
    p.write_text(json.dumps({"entries": entries}))
    return p


def _pip_report(*deps: dict[str, Any]) -> str:
    return json.dumps({"dependencies": list(deps), "fixes": []})


VULN = {
    "name": "examplepkg",
    "version": "1.0.0",
    "vulns": [
        {
            "id": "PYSEC-2099-1",
            "aliases": ["CVE-2099-0001"],
            "fix_versions": ["1.0.1"],
            "description": "x",
        },
        # the same advisory a second time, as pip-audit sometimes reports it: counted once
        {
            "id": "PYSEC-2099-1",
            "aliases": ["CVE-2099-0001"],
            "fix_versions": ["1.0.1.0"],
            "description": "x",
        },
    ],
}
CLEAN = {"name": "otherpkg", "version": "2.0.0", "vulns": []}
LOCAL = {"name": "causal-core", "skip_reason": "Dependency not found on PyPI"}


def _pip(tmp_path: Path, tool: Path, allow: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--today",
            TODAY,
            "--allowlist",
            str(allow),
            "pip",
            "--pip-audit",
            str(tool),
            "--python",
            sys.executable,
            "--label",
            "test",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_vulnerable_package_fails(tmp_path: Path) -> None:
    tool = _fake_tool(tmp_path, "pip-audit", _pip_report(VULN, CLEAN, LOCAL), code=1)
    proc = _pip(tmp_path, tool, _allowlist(tmp_path, []))
    assert proc.returncode == 1, proc.stdout
    assert "FAIL examplepkg 1.0.0: CVE-2099-0001" in proc.stdout
    assert "2 packages audited (1 local" in proc.stdout
    assert "1 advisories in 1 packages" in proc.stdout


def test_a_clean_environment_passes(tmp_path: Path) -> None:
    tool = _fake_tool(tmp_path, "pip-audit", _pip_report(CLEAN, LOCAL))
    proc = _pip(tmp_path, tool, _allowlist(tmp_path, []))
    assert proc.returncode == 0, proc.stdout
    assert proc.stdout.startswith("ok ")


def test_an_allowlisted_advisory_passes_by_any_alias(tmp_path: Path) -> None:
    tool = _fake_tool(tmp_path, "pip-audit", _pip_report(VULN, CLEAN), code=1)
    allow = _allowlist(
        tmp_path,
        [
            {
                "id": "CVE-2099-0001",
                "ecosystem": "pypi",
                "package": "ExamplePkg",
                "reason": "the affected function is not called",
                "review_by": "2026-12-31",
            }
        ],
    )
    proc = _pip(tmp_path, tool, allow)
    assert proc.returncode == 0, proc.stdout
    assert "1 allowlisted" in proc.stdout


def test_an_expired_exception_fails(tmp_path: Path) -> None:
    tool = _fake_tool(tmp_path, "pip-audit", _pip_report(VULN), code=1)
    allow = _allowlist(
        tmp_path,
        [
            {
                "id": "PYSEC-2099-1",
                "ecosystem": "pypi",
                "package": "examplepkg",
                "reason": "r",
                "review_by": "2026-09-24",
            }
        ],
    )
    proc = _pip(tmp_path, tool, allow)
    assert proc.returncode == 1
    assert "has passed" in proc.stdout


def test_a_stale_exception_fails_and_one_for_an_absent_package_does_not(tmp_path: Path) -> None:
    tool = _fake_tool(tmp_path, "pip-audit", _pip_report(CLEAN))
    stale = {
        "id": "PYSEC-2099-7",
        "ecosystem": "pypi",
        "package": "otherpkg",
        "reason": "r",
        "review_by": "2026-12-31",
    }
    absent = {
        "id": "PYSEC-2099-8",
        "ecosystem": "pypi",
        "package": "notinstalled",
        "reason": "r",
        "review_by": "2026-12-31",
    }
    proc = _pip(tmp_path, tool, _allowlist(tmp_path, [stale]))
    assert proc.returncode == 1
    assert "no longer reported here -- strike it off" in proc.stdout
    assert _pip(tmp_path, tool, _allowlist(tmp_path, [absent])).returncode == 0


def test_a_malformed_exception_fails(tmp_path: Path) -> None:
    tool = _fake_tool(tmp_path, "pip-audit", _pip_report(CLEAN))
    proc = _pip(
        tmp_path, tool, _allowlist(tmp_path, [{"id": "X", "ecosystem": "pypi", "package": "p"}])
    )
    assert proc.returncode == 1
    assert "missing reason, review_by" in proc.stdout


def test_an_unreachable_database_is_not_measured(tmp_path: Path) -> None:
    err = (
        "requests.exceptions.ConnectionError: "
        "HTTPSConnectionPool(host='pypi.org'): Max retries exceeded"
    )
    tool = _fake_tool(tmp_path, "pip-audit", "", stderr=err, code=1)
    proc = _pip(tmp_path, tool, _allowlist(tmp_path, []))
    assert proc.returncode == 2, proc.stdout
    assert proc.stdout.startswith("NOT MEASURED")


def test_a_crash_is_a_failure_not_a_skip(tmp_path: Path) -> None:
    tool = _fake_tool(tmp_path, "pip-audit", "", stderr="Traceback: KeyError 'x'", code=1)
    proc = _pip(tmp_path, tool, _allowlist(tmp_path, []))
    assert proc.returncode == 1
    assert "without a report" in proc.stdout


def test_an_audit_of_nothing_fails(tmp_path: Path) -> None:
    tool = _fake_tool(tmp_path, "pip-audit", _pip_report(LOCAL))
    proc = _pip(tmp_path, tool, _allowlist(tmp_path, []))
    assert proc.returncode == 1
    assert "0 packages audited" in proc.stdout


def _npm(tmp_path: Path, allow: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--today", TODAY, "--allowlist", str(allow), "npm"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _npm_report(vulns: dict[str, Any]) -> str:
    return json.dumps({"vulnerabilities": vulns, "metadata": {"dependencies": {"total": 3}}})


NPM_VULN = {
    "examplejs": {
        "name": "examplejs",
        "severity": "high",
        "fixAvailable": True,
        "via": [
            {
                "source": 1,
                "name": "examplejs",
                "url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                "range": "<1.2.3",
            }
        ],
    },
    "dependent": {
        "name": "dependent",
        "severity": "high",
        "fixAvailable": True,
        "via": ["examplejs"],
    },
}


def test_npm_a_vulnerable_package_fails_and_an_exception_passes(tmp_path: Path) -> None:
    _fake_tool(tmp_path, "npm", _npm_report(NPM_VULN), code=1)
    proc = _npm(tmp_path, _allowlist(tmp_path, []))
    assert proc.returncode == 1, proc.stdout
    assert "FAIL examplejs <1.2.3: 1 (GHSA-aaaa-bbbb-cccc)" in proc.stdout
    assert "3 packages audited" in proc.stdout

    allow = _allowlist(
        tmp_path,
        [
            {
                "id": "GHSA-aaaa-bbbb-cccc",
                "ecosystem": "npm",
                "package": "examplejs",
                "reason": "dev-only, never bundled",
                "review_by": "2026-10-31",
            }
        ],
    )
    assert _npm(tmp_path, allow).returncode == 0


def test_npm_an_unreachable_registry_is_not_measured(tmp_path: Path) -> None:
    body = json.dumps(
        {"message": "request to https://registry/ failed, reason: getaddrinfo ENOTFOUND"}
    )
    _fake_tool(tmp_path, "npm", body, code=1)
    proc = _npm(tmp_path, _allowlist(tmp_path, []))
    assert proc.returncode == 2, proc.stdout
