#!/usr/bin/env python3
"""lint_ratchet -- hold ruff / mypy / eslint finding counts at or below a committed maximum.

WHY A RATCHET AND NOT "CLEAN". After the safe autofixes (81dc794) ruff still
reports ~450 findings in backend/ and mypy --strict ~46 errors in backend/src.
Three dishonest ways to make CI green were on offer: disable the rules, sprinkle
ignores, or keep a gate nobody can pass. A ratchet is the honest one: the
committed number is today's measurement, the gate fails the moment it goes UP,
and it can only be lowered. (eslint is supported but has no entry yet: the
frontend has no eslint config, so there is nothing honest to measure. See the
comment in scripts/lint-baseline.json.)

WHAT IT CHECKS, per tool:
  1. the tool ran and reported (a crash, a config error or unparseable output is
     a FAIL, never "0 findings") -- fail closed;
  2. it inspected at least one file (a gate that inspected nothing may not
     report success);
  3. findings <= the committed maximum in scripts/lint-baseline.json;
  4. that committed maximum did not go UP since the merge base with main,
     unless a `Ratchet-Exempt: <why>` commit trailer says why.

WHAT IT DOES NOT CHECK: that a finding fixed in one place was not traded for a
new one somewhere else -- the ratchet is on the total. It prints the per-rule
breakdown so a reviewer can see the trade, but it does not gate on it.

Counts depend on the tool version. The version the baseline was measured with is
recorded, and a different version is reported (not failed): a new ruff release
that adds findings is an instrument change, the way gnomon's coverage-floor.json
records a vitest major bump.

Usage (the first argument is an entry in scripts/lint-baseline.json):
  scripts/lint_ratchet.py ruff         --bin backend/.venv/bin/ruff
  scripts/lint_ratchet.py ruff-scripts --bin backend/.venv/bin/ruff
  scripts/lint_ratchet.py mypy         --bin backend/.venv/bin/mypy
  scripts/lint_ratchet.py eslint       # runs `npx --no-install eslint` in frontend/
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gatelib import (  # noqa: E402
    ROOT,
    bad,
    baseline_at_base,
    describe_base_status,
    load_json,
    ok,
    ratchet_exempt_reason,
    warn,
)

BASELINE_FILE = "scripts/lint-baseline.json"


class ToolDidNotReport(Exception):
    """The tool crashed, was misconfigured, or printed nothing parseable."""


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ToolDidNotReport(f"cannot execute {cmd[0]}: {exc}") from exc


def measure_ruff(binary: str, spec: dict) -> tuple[int, int, collections.Counter[str], str]:
    cwd = ROOT / spec["cwd"]
    version = run([binary, "--version"], cwd).stdout.strip()
    files = run([binary, "check", "--show-files", *spec["args"]], cwd)
    inspected = sum(1 for line in files.stdout.splitlines() if line.strip().endswith((".py", ".pyi")))
    proc = run([binary, "check", *spec["args"], "--output-format=json", "--exit-zero"], cwd)
    if proc.returncode != 0:
        raise ToolDidNotReport(f"ruff exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    try:
        findings = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ToolDidNotReport(f"ruff printed no JSON ({exc})") from exc
    by_rule = collections.Counter(str(f.get("code") or "syntax") for f in findings)
    return inspected, len(findings), by_rule, version


MYPY_FOUND = re.compile(r"Found (\d+) errors? in (\d+) files? \(checked (\d+) source files?\)")
MYPY_OK = re.compile(r"Success: no issues found in (\d+) source files?")
MYPY_CODE = re.compile(r"\[([a-z0-9-]+)\]\s*$")


#: The finding lines of the last measurement, printed when a ratchet fails: a gate that
#: says "1 finding" in CI but none on a laptop has to say which, or it cannot be chased.
LAST_FINDINGS: list[str] = []


def measure_mypy(binary: str, spec: dict) -> tuple[int, int, collections.Counter[str], str]:
    cwd = ROOT / spec["cwd"]
    version = run([binary, "--version"], cwd).stdout.strip()
    proc = run([binary, *spec["args"]], cwd)
    out = proc.stdout + proc.stderr
    if proc.returncode not in (0, 1):
        raise ToolDidNotReport(f"mypy exited {proc.returncode} (crash or usage error): {out.strip()[-300:]}")
    found, success = MYPY_FOUND.search(out), MYPY_OK.search(out)
    if found:
        errors, inspected = int(found.group(1)), int(found.group(3))
    elif success:
        errors, inspected = 0, int(success.group(1))
    else:
        raise ToolDidNotReport(f"mypy printed no summary line: {out.strip()[-300:]}")
    by_code = collections.Counter(
        m.group(1) for line in out.splitlines() if ": error:" in line and (m := MYPY_CODE.search(line))
    )
    LAST_FINDINGS[:] = [line for line in out.splitlines() if ": error:" in line]
    return inspected, errors, by_code, version


def measure_eslint(_binary: str, spec: dict) -> tuple[int, int, collections.Counter[str], str]:
    cwd = ROOT / spec["cwd"]
    npx = ["npx", "--no-install", "eslint"]
    version = run([*npx, "--version"], cwd).stdout.strip()
    proc = run([*npx, *spec["args"], "-f", "json"], cwd)
    # eslint: 0 clean, 1 lint findings, 2 config error / crash.
    if proc.returncode not in (0, 1):
        raise ToolDidNotReport(f"eslint exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:400]}")
    try:
        results = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ToolDidNotReport(f"eslint printed no JSON ({exc})") from exc
    findings = sum(r.get("errorCount", 0) + r.get("warningCount", 0) for r in results)
    by_rule = collections.Counter(
        str(m.get("ruleId") or "parse") for r in results for m in r.get("messages", [])
    )
    return len(results), findings, by_rule, f"eslint {version}"


MEASURE = {"ruff": measure_ruff, "mypy": measure_mypy, "eslint": measure_eslint}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("entry", help="a key in scripts/lint-baseline.json (ruff, ruff-scripts, mypy, eslint)")
    ap.add_argument("--bin", default="", help="path to the tool binary (ruff, mypy)")
    args = ap.parse_args()

    baseline = load_json(ROOT / BASELINE_FILE)
    if args.entry not in baseline or args.entry.startswith("_"):
        bad(f"{args.entry}: no such entry in {BASELINE_FILE} -- declare one rather than skip")
        return 2
    spec = baseline[args.entry]
    tool = spec.get("tool", args.entry)
    if tool not in MEASURE:
        bad(f"{args.entry}: unknown tool {tool!r}; expected one of {sorted(MEASURE)}")
        return 2
    limit = int(spec["max_findings"])

    try:
        inspected, findings, by_rule, version = MEASURE[tool](args.bin, spec)
    except ToolDidNotReport as exc:
        bad(f"{args.entry}: the tool did not report -- {exc}")
        return 2

    failed = False
    if inspected == 0:
        bad(f"{args.entry}: inspected 0 files -- a gate that inspected nothing may not report success")
        failed = True

    top = ", ".join(f"{rule} {n}" for rule, n in by_rule.most_common(6))
    if findings > limit:
        bad(f"{args.entry}: {findings} findings over {inspected} files, above the committed "
            f"maximum of {limit} ({BASELINE_FILE}). Top rules: {top}")
        if findings - limit <= 20:
            for line in LAST_FINDINGS[:20]:
                print(f"       {line}")
        print(f"       measured with {version}")
        failed = True
    else:
        ok(f"{args.entry}: {findings} findings over {inspected} files (max {limit})"
           + (f" -- top: {top}" if top else ""))
        if findings < limit:
            warn(f"{args.entry}: lower `max_findings` to {findings} in {BASELINE_FILE} to lock the gain in")

    if spec.get("measured_with") and version and spec["measured_with"] != version:
        warn(f"{args.entry}: baseline measured with {spec['measured_with']!r}, this run used "
             f"{version!r} -- counts across versions are not comparable")

    status, old = baseline_at_base(BASELINE_FILE)
    if status == "found" and args.entry in old:
        old_limit = int(old[args.entry]["max_findings"])
        if limit > old_limit:
            reason = ratchet_exempt_reason()
            if reason:
                warn(f"{args.entry}: max RAISED {old_limit} -> {limit}; Ratchet-Exempt: {reason}")
            else:
                bad(f"{args.entry}: {BASELINE_FILE} raised max_findings {old_limit} -> {limit}. "
                    "The ratchet only goes down; declare `Ratchet-Exempt: <why>` in the commit "
                    "message if this is deliberate.")
                failed = True
    elif status == "found":
        warn(f"monotonic check: {args.entry} is new in {BASELINE_FILE} since the merge base")
    else:
        warn(describe_base_status(status, BASELINE_FILE))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
