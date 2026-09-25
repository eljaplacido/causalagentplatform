"""
scripts/secret_scan.py -- the gate's secret scan catches a planted credential.

Negative controls: each test builds a throwaway git working tree, plants a
credential in it, and asserts the scan fails, names the file, line and rule, and
never prints the value. The planted values are assembled at runtime from pieces,
so this file does not itself contain anything the scan would match (it scans
this file too, on every gate run).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "secret_scan.py"


def _git_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "tree"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _scan(root: Path, allowlist: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT), "--root", str(root)]
    if allowlist is not None:
        cmd += ["--allowlist", str(allowlist)]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _random_looking(seed: str, n: int = 40) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:n]


PEM_HEADER = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
AWS_KEY_ID = "AK" + "IA" + "Q3EGRTVZ" + "7HX2WQ4M"
GITHUB_TOKEN = "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0" + "k1L2m3N4o5P6q7R8s9T0"
SLACK_TOKEN = "xo" + "xb-" + "1234567890-" + "abcdefABCDEF1234"
ANTHROPIC_KEY = "sk-" + "ant-" + "api03-" + _random_looking("anthropic", 64) + "Aa0" * 10
GOOGLE_KEY = "AI" + "za" + "SyD" + "4f5g6h7j8k9l0" + "zXcVbNmQwErTyUiOp1a"


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        (PEM_HEADER, "private-key"),
        (f"aws_access_key_id = {AWS_KEY_ID}", "aws-access-key-id"),
        (f'token = "{GITHUB_TOKEN}"', "github-token"),
        (f"SLACK={SLACK_TOKEN}", "slack-token"),
        (f"key: {ANTHROPIC_KEY}", "anthropic-key"),
        (f'"{GOOGLE_KEY}"', "gcp-api-key"),
        (f'SERVICE_API_KEY = "{_random_looking("svc")}"', "generic-credential-assignment"),
        (f"db_password: '{_random_looking('db', 24)}'", "generic-credential-assignment"),
    ],
)
def test_a_planted_credential_fails_the_scan(tmp_path: Path, line: str, rule: str) -> None:
    root = _git_tree(tmp_path, {"README.md": "clean\n", "config/settings.py": f"x = 1\n{line}\n"})
    proc = _scan(root)
    assert proc.returncode == 1, proc.stdout
    assert f"config/settings.py:2: {rule}" in proc.stdout
    # The finding names a fingerprint, never the value.
    value = line.split("=")[-1].split(": ")[-1].strip().strip("'\"")
    assert value not in proc.stdout


def test_untracked_files_are_scanned_and_ignored_ones_are_not(tmp_path: Path) -> None:
    root = _git_tree(
        tmp_path,
        {".gitignore": ".env\n", ".env": f"{PEM_HEADER}\n", "new_file.txt": f"{AWS_KEY_ID}\n"},
    )
    proc = _scan(root)
    assert proc.returncode == 1
    assert "new_file.txt:1: aws-access-key-id" in proc.stdout
    assert ".env" not in proc.stdout.replace(".gitignore", "")


def test_placeholders_and_short_values_pass(tmp_path: Path) -> None:
    root = _git_tree(
        tmp_path,
        {
            "a.env.example": "ANTHROPIC_API_KEY=your-anthropic-api-key-here\n",
            "b.py": 'API_KEY = os.environ["API_KEY"]\nTOKEN = "dev-local-token"\n',
            "c.toml": 'api_key_env = "OPENCODE_API_KEY_FOR_THE_ENDPOINT"\n',
            "d.py": 'SECRET_KEY = "${SECRET_KEY_FROM_THE_ENVIRONMENT_1}"\n',
        },
    )
    proc = _scan(root)
    assert proc.returncode == 0, proc.stdout
    assert "4 files scanned" in proc.stdout


def test_an_allowlisted_fixture_passes_and_a_stale_entry_fails(tmp_path: Path) -> None:
    root = _git_tree(tmp_path, {"tests/fixture.pem": f"{PEM_HEADER}\n"})
    first = _scan(root)
    assert first.returncode == 1
    fp = first.stdout.split("fingerprint ")[1].split(")")[0]

    allow = tmp_path / "allow.json"
    entry = {
        "path": "tests/fixture.pem",
        "rule": "private-key",
        "fingerprint": fp,
        "reason": "a parser fixture; the key is not a key to anything",
    }
    allow.write_text(json.dumps({"entries": [entry]}))
    ok = _scan(root, allow)
    assert ok.returncode == 0, ok.stdout
    assert "1 allowlisted" in ok.stdout

    (root / "tests/fixture.pem").write_text("no longer a key\n")
    stale = _scan(root, allow)
    assert stale.returncode == 1
    assert "matches nothing -- strike it off" in stale.stdout


def test_an_allowlist_entry_without_a_reason_fails(tmp_path: Path) -> None:
    root = _git_tree(tmp_path, {"a.txt": "clean\n"})
    allow = tmp_path / "allow.json"
    allow.write_text(
        json.dumps({"entries": [{"path": "a.txt", "rule": "private-key", "fingerprint": "0" * 16}]})
    )
    proc = _scan(root, allow)
    assert proc.returncode == 1
    assert "missing reason" in proc.stdout


def test_a_scan_that_inspected_nothing_fails(tmp_path: Path) -> None:
    root = _git_tree(tmp_path, {})
    proc = _scan(root)
    assert proc.returncode == 1
    assert "0 files scanned" in proc.stdout


def test_the_repository_scan_inspects_this_file() -> None:
    # The gate scans the tree this test lives in; this file is part of it, and
    # clean, which is the point of assembling the planted values at runtime.
    proc = _scan(REPO)
    assert "backend/tests/unit/test_secret_scan.py" not in proc.stdout
    assert " files scanned" in proc.stdout
