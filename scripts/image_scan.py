#!/usr/bin/env python3
"""image_scan -- no HIGH or CRITICAL vulnerability with a published fix in a container image, unless reviewed.

CI's `images` job builds backend/Dockerfile and frontend/Dockerfile and scans each image
with Trivy (a pinned version), writing a JSON report with every package listed:

    trivy image --scanners vuln --severity HIGH,CRITICAL --list-all-pkgs --format json -o R.json IMAGE
    scripts/image_scan.py R.json --image backend

This judges that report. A HIGH or CRITICAL finding whose package has a FIXED VERSION
published FAILS the job unless scripts/image-scan-allowlist.json has an entry for it:
`id` (the CVE/GHSA id), `image` (backend | frontend), `package`, `reason` and
`review_by` (YYYY-MM-DD). An entry past its review_by date FAILS, and so does one whose
finding is no longer reported (the fix landed; strike it off). The answer to a finding
is almost always a base-image bump or a lock upgrade; the allowlist is for the rest.

A finding with NO fixed version published (Trivy status affected, fix_deferred,
will_not_fix, ...) cannot be fixed by an upgrade. It is listed and counted, not failed:
failing on it would fail every build on a distribution's backlog until someone
allowlisted the lot unread. A report that lists no package FAILS -- a scan that
inspected nothing may not pass.

Exit: 0 no unexcused fixable finding; 1 one, an expired, stale or malformed allowlist
entry, or a report that does not parse or lists nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "scripts" / "image-scan-allowlist.json"
GATED = {"HIGH", "CRITICAL"}


@dataclass(frozen=True)
class Finding:
    vuln: str
    package: str
    installed: str
    fixed: str
    severity: str
    target: str


def read_report(path: Path) -> tuple[int, list[Finding]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    packages = 0
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    for result in data.get("Results") or []:
        packages += len(result.get("Packages") or [])
        for v in result.get("Vulnerabilities") or []:
            if str(v.get("Severity", "")).upper() not in GATED:
                continue
            f = Finding(str(v["VulnerabilityID"]), str(v["PkgName"]), str(v.get("InstalledVersion", "")),
                        str(v.get("FixedVersion") or ""), str(v["Severity"]).upper(), str(result.get("Target", "")))
            key = (f.vuln, f.package, f.installed)
            if key not in seen:  # one advisory on one package, however many layers report it
                seen.add(key)
                findings.append(f)
    return packages, findings


def load_allowlist(path: Path, image: str, today: dt.date) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        return [], [f"{path.name} is absent (an empty `entries` list is how to say none)"]
    entries = json.loads(path.read_text(encoding="utf-8")).get("entries")
    if not isinstance(entries, list):
        return [], [f"{path.name}: no `entries` list"]
    mine: list[dict[str, str]] = []
    problems: list[str] = []
    for i, e in enumerate(entries):
        label = f"{path.name} entry {i} ({e.get('id', '?')})"
        missing = [k for k in ("id", "image", "package", "reason", "review_by") if not str(e.get(k, "")).strip()]
        if missing:
            problems.append(f"{label}: missing {', '.join(missing)}")
            continue
        try:
            review_by = dt.date.fromisoformat(str(e["review_by"]))
        except ValueError:
            problems.append(f"{label}: review_by {e['review_by']!r} is not YYYY-MM-DD")
            continue
        if review_by < today:
            problems.append(f"{label}: review_by {review_by} has passed -- re-review the exception or fix it")
            continue
        if e["image"] == image:
            mine.append(e)
    return mine, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("report", help="Trivy's JSON report (with --list-all-pkgs)")
    ap.add_argument("--image", required=True, help="which image the report is of (backend | frontend)")
    ap.add_argument("--today", default="", help=argparse.SUPPRESS)  # tests pin the date
    ap.add_argument("--allowlist", default=str(ALLOWLIST), help=argparse.SUPPRESS)
    args = ap.parse_args()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    try:
        packages, findings = read_report(Path(args.report))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"FAIL {args.report} is not a Trivy JSON report: {exc}")
        return 1
    entries, problems = load_allowlist(Path(args.allowlist), args.image, today)
    failed = bool(problems)
    for p in problems:
        print(f"FAIL {p}")
    fixable = [f for f in findings if f.fixed]
    unfixed = [f for f in findings if not f.fixed]
    used: set[int] = set()
    blocking: list[Finding] = []
    for f in fixable:
        hit = next((i for i, e in enumerate(entries) if e["id"] == f.vuln and e["package"] == f.package), None)
        if hit is None:
            blocking.append(f)
        else:
            used.add(hit)
            print(f"note {f.package} {f.installed}: {f.vuln} allowlisted until {entries[hit]['review_by']} "
                  f"-- {entries[hit]['reason']}")
    for f in sorted(blocking, key=lambda x: (x.package, x.vuln)):
        print(f"FAIL {f.severity} {f.vuln} {f.package} {f.installed} -- fixed in {f.fixed} ({f.target})")
        failed = True
    for i, e in enumerate(entries):
        if i not in used:
            print(f"FAIL {Path(args.allowlist).name}: {e['id']} ({e['package']}) is no longer reported "
                  f"in the {args.image} image -- strike it off")
            failed = True
    for f in sorted(unfixed, key=lambda x: (x.package, x.vuln)):
        print(f"note {f.severity} {f.vuln} {f.package} {f.installed} -- no fixed version published ({f.target})")
    if packages == 0:
        print(f"FAIL {args.image}: the report lists 0 packages -- a scan that inspected nothing may not pass")
        failed = True
    summary = (f"image-scan ({args.image}): {packages} packages scanned; HIGH/CRITICAL with a fix: "
               f"{len(blocking)} blocking, {len(used)} allowlisted; without a published fix: "
               f"{len(unfixed)} (listed, not gated)")
    print(("FAIL " if failed else "ok   ") + summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
