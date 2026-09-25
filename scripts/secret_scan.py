#!/usr/bin/env python3
"""secret_scan -- no credential in the working tree. Standard library only.

WHAT IT SCANS. Every file git would commit from this working tree: the tracked
files (`git ls-files --cached`) and the untracked ones that are not ignored
(`--others --exclude-standard`), read as they are on disk now. Not the history:
a key that was committed and removed is still in the history, and the answer to
that is rotating the key (its owner's action), never rewriting the history.
Binary files (a NUL byte in the first 8 KiB) are counted and skipped.

WHAT IT LOOKS FOR. Private-key blocks; provider token formats (AWS, Google,
Azure, GitHub, Slack, OpenAI, Anthropic, Stripe) whose shape is distinctive
enough to match on its own; and a generic rule: a name ending in KEY, SECRET,
TOKEN or PASSWORD (and their variants) assigned a quoted literal of 20+
characters whose Shannon entropy is high, which is what a real credential looks
like and a placeholder, a variable name or a URL template does not.

WHAT IT PRINTS. The path, line, rule and a fingerprint (the first 16 hex of the
SHA-256 of the matched value) -- never the value. The fingerprint is what an
allowlist entry names, so the allowlist does not hold the secret either.

THE ALLOWLIST (scripts/secret-scan-allowlist.json) is for test fixtures that
must look like a credential. Each entry names `path`, `rule`, `fingerprint` and
a `reason`. An entry that no longer matches anything fails the scan, so the
list cannot outlive what it excuses.

Exit: 0 clean; 1 a finding, a stale or malformed allowlist entry, or nothing
scanned (a scan that inspected nothing may not pass).

    scripts/secret_scan.py [--root DIR] [--allowlist FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = "scripts/secret-scan-allowlist.json"
BINARY_SNIFF = 8192
MAX_BYTES = 5_000_000  # a text file bigger than this is data (package-lock.json is ~0.4 MB)

#: Provider formats. Each pattern's LAST group is the credential; it is what
#: the fingerprint is taken over.
RULES: dict[str, re.Pattern[str]] = {
    "private-key": re.compile(
        r"(-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED |)PRIVATE KEY(?: BLOCK)?-----)"
    ),
    "aws-access-key-id": re.compile(r"\b((?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16})\b"),
    "aws-secret-access-key": re.compile(
        r"(?i)aws_?secret_?(?:access_?)?key[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])"
    ),
    "gcp-api-key": re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})(?![0-9A-Za-z_\-])"),
    "gcp-service-account": re.compile(r"\"private_key_id\"\s*:\s*\"([0-9a-f]{40})\""),
    "azure-storage-key": re.compile(r"AccountKey=([A-Za-z0-9+/]{86}==)"),
    "azure-client-secret": re.compile(r"(?<![A-Za-z0-9_~.-])([A-Za-z0-9_~.]{3}\dQ~[A-Za-z0-9_~.-]{31,34})(?![A-Za-z0-9_~.-])"),
    "github-token": re.compile(r"\b((?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255})\b"),
    "github-fine-grained-pat": re.compile(r"\b(github_pat_[A-Za-z0-9_]{82})\b"),
    "slack-token": re.compile(r"\b(xox[abeoprs]-[A-Za-z0-9-]{10,})\b"),
    "slack-webhook": re.compile(r"(https://hooks\.slack\.com/services/T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/[A-Za-z0-9]{20,})"),
    "anthropic-key": re.compile(r"\b(sk-ant-(?:api|admin|oat)\d{2}-[A-Za-z0-9_\-]{80,})"),
    "openai-key": re.compile(
        r"\b(sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{16,}T3BlbkFJ[A-Za-z0-9_\-]{16,})"
        r"|\b(sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{58,})"
    ),
    "stripe-live-key": re.compile(r"\b((?:sk|rk)_live_[0-9A-Za-z]{24,})\b"),
}

#: The generic rule: NAME = "literal". The name's last word is the credential word.
GENERIC = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|SECRET(?:_?KEY)?|TOKEN|PASSWORD|PASSWD|PWD|CLIENT_?SECRET|SIGNING_?KEY))"
    r"[\"']?\s*(?::=|=|:)\s*[bru]?[\"']([^\"'\s]{20,})[\"']"
)
GENERIC_MIN_ENTROPY = 3.5  # bits per character
#: Values that are shaped like a credential and are not one.
PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|changeme|change[_-]?me|dummy|sample|your[_-]|redacted|xxxx|\*\*\*\*"
    r"|<[a-z_-]+>|\$\{|\{\{|%\(|^test[_-]|[_-]test$|fake|not[_-]?a[_-]?real)"
)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    fingerprint: str


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def entropy(value: str) -> float:
    counts = Counter(value)
    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _looks_generated(value: str) -> bool:
    """A literal a person did not type: high entropy, and letters mixed with digits."""
    if PLACEHOLDER.search(value):
        return False
    if entropy(value) < GENERIC_MIN_ENTROPY:
        return False
    has_digit = any(ch.isdigit() for ch in value)
    has_alpha = any(ch.isalpha() for ch in value)
    return has_digit and has_alpha


def scan_text(path: str, text: str) -> list[Finding]:
    found: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in RULES.items():
            for m in pattern.finditer(line):
                value = next(g for g in reversed(m.groups()) if g) if m.groups() else m.group(0)
                found.append(Finding(path, lineno, rule, fingerprint(value)))
        for m in GENERIC.finditer(line):
            value = m.group(2)
            if _looks_generated(value):
                found.append(Finding(path, lineno, "generic-credential-assignment", fingerprint(value)))
    return found


def worktree_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {proc.stderr.decode(errors='replace').strip()[:200]}")
    return sorted({p for p in proc.stdout.decode("utf-8", errors="replace").split("\0") if p})


def load_allowlist(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """The entries, and what is wrong with any of them."""
    if not path.exists():
        return [], []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        return [], [f"{path.name}: no `entries` list"]
    problems = []
    for i, e in enumerate(entries):
        missing = [k for k in ("path", "rule", "fingerprint", "reason") if not str(e.get(k, "")).strip()]
        if missing:
            problems.append(f"{path.name} entry {i}: missing {', '.join(missing)}")
    return entries, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT), help="the git working tree to scan")
    ap.add_argument("--allowlist", default="", help=f"default: <root>/{ALLOWLIST}")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    allow_path = Path(args.allowlist) if args.allowlist else root / ALLOWLIST

    try:
        files = worktree_files(root)
    except RuntimeError as exc:
        print(f"FAIL secret-scan: {exc}")
        return 1

    scanned = binary = 0
    findings: list[Finding] = []
    for rel in files:
        p = root / rel
        if not p.is_file() or p.is_symlink():
            continue  # deleted in the working tree, or a link (its target is scanned where it lives)
        if p.stat().st_size > MAX_BYTES:
            binary += 1
            continue
        blob = p.read_bytes()
        if b"\0" in blob[:BINARY_SNIFF]:
            binary += 1
            continue
        scanned += 1
        findings.extend(scan_text(rel, blob.decode("utf-8", errors="replace")))

    entries, problems = load_allowlist(allow_path)
    allowed = {(e["path"], e["rule"], e["fingerprint"]) for e in entries if not problems}
    used: set[tuple[str, str, str]] = set()
    blocking = []
    for f in findings:
        key = (f.path, f.rule, f.fingerprint)
        if key in allowed:
            used.add(key)
        else:
            blocking.append(f)
    stale = sorted(allowed - used)

    failed = False
    for problem in problems:
        print(f"FAIL {problem}")
        failed = True
    for f in blocking:
        # Never the value: the path, the line, the rule and a fingerprint.
        print(f"FAIL {f.path}:{f.line}: {f.rule} (fingerprint {f.fingerprint})")
        failed = True
    for path, rule, fp in stale:
        print(f"FAIL {ALLOWLIST}: the entry for {path} ({rule}, {fp}) matches nothing -- strike it off")
        failed = True
    if scanned == 0:
        print("FAIL secret-scan: 0 files scanned -- a scan that inspected nothing may not pass")
        failed = True
    if blocking:
        print("     A real credential here is exposed: revoke and reissue it, then remove it from the")
        print("     tree. Removing it does not remove it from history; rotation is the fix for that.")

    summary = (f"secret-scan: {scanned} files scanned ({binary} binary skipped), "
               f"{len(blocking)} finding(s), {len(used)} allowlisted")
    print(("FAIL " if failed else "ok   ") + summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
