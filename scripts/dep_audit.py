#!/usr/bin/env python3
"""dep_audit -- no dependency with a published vulnerability, unless a reviewed entry says why.

Two audits, one rule:

  scripts/dep_audit.py pip --pip-audit BIN --requirement requirements/backend.txt --label backend
      pip-audit over a hash-pinned lock (scripts/py_lock.py), the pins whose markers
      hold here: exactly what CI installs, and what the gate's python-lock step
      requires the tested environment to equal. What .gnomon/ci.sh runs.
  scripts/dep_audit.py pip --pip-audit BIN --python PY --label backend
      pip-audit over the environment PY runs in (its site-packages) -- for an
      environment no lock describes. Not a fresh resolution of the ranges in
      pyproject.toml, either way, which would audit something no step runs.
  scripts/dep_audit.py npm
      npm audit over package-lock.json at the repository root (every workspace).

Every advisory the tool reports FAILS the step unless scripts/audit-allowlist.json
has an entry for it: `id` (the advisory id or one of its aliases), `ecosystem`
(pypi | npm), `package`, `reason` and `review_by` (YYYY-MM-DD). An entry past its
review_by date FAILS -- an exception is re-reviewed or it lapses -- and so does
an entry whose package is audited here and whose advisory is no longer reported
(the fix landed; strike it off). The right answer to a finding is almost always
to raise a floor or upgrade, and the allowlist is for the rest.

Both tools ask a remote advisory database. When it cannot be reached the audit
did not happen, so the exit is 2 (NOT MEASURED), never 0: an offline run is not
a clean one. A tool that ran and crashed for any other reason is a FAIL.

Exit: 0 audited and clean; 1 a finding, an expired, stale or malformed allowlist
entry, or a crash; 2 not measured (the tool is absent or the database unreachable).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "scripts" / "audit-allowlist.json"
TIMEOUT_S = 900

#: What an unreachable advisory database looks like in each tool's output.
NETWORK = re.compile(
    r"ConnectionError|ProxyError|ConnectTimeout|ReadTimeout|Max retries exceeded|NameResolutionError"
    r"|Temporary failure in name resolution|Name or service not known|Network is unreachable"
    r"|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|ETIMEDOUT|ECONNRESET|ENETUNREACH|audit endpoint returned an error"
)


class NotMeasured(Exception):
    """The audit could not run: the tool is absent or the advisory database unreachable."""


class DidNotReport(Exception):
    """The tool ran and did not produce a report (a crash, not the network)."""


@dataclass(frozen=True)
class Advisory:
    package: str
    version: str
    ids: frozenset[str]  # the advisory id and its aliases
    fix: str

    @property
    def primary(self) -> str:
        return sorted(self.ids)[0]


@dataclass
class Audit:
    audited: int
    unaudited: list[str] = field(default_factory=list)  # e.g. local packages not on PyPI
    packages: set[str] = field(default_factory=set)
    advisories: list[Advisory] = field(default_factory=list)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=TIMEOUT_S)
    except FileNotFoundError as exc:
        raise NotMeasured(f"cannot execute {cmd[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise NotMeasured(f"{cmd[0]} did not finish in {TIMEOUT_S}s (advisory database unreachable?)") from exc


def _network_or_crash(tool: str, proc: subprocess.CompletedProcess[str]) -> Exception:
    out = f"{proc.stdout}\n{proc.stderr}"
    m = NETWORK.search(out)
    if m:
        return NotMeasured(f"{tool} could not reach its advisory database ({m.group(0)})")
    tail = " | ".join(line.strip() for line in out.strip().splitlines()[-3:])
    return DidNotReport(f"{tool} exited {proc.returncode} without a report: {tail[:300]}")


def audit_pip(pip_audit: str, python: str = "", requirement: str = "") -> Audit:
    if not Path(pip_audit).is_file():
        raise NotMeasured(f"pip-audit is not installed at {pip_audit}")
    if requirement:
        if not Path(requirement).is_file():
            raise DidNotReport(f"the lock {requirement} is absent")
        requirement = str(Path(requirement).resolve())
        # --disable-pip: read the pins as written (every one hashed), resolve nothing.
        target = ["-r", requirement, "--require-hashes", "--disable-pip"]
    else:
        where = _run([python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"], ROOT)
        site = where.stdout.strip()
        if where.returncode != 0 or not Path(site).is_dir():
            raise DidNotReport(f"cannot locate site-packages for {python}: {where.stderr.strip()[:200]}")
        target = ["--path", site]
    proc = _run([pip_audit, *target, "-f", "json", "--progress-spinner", "off"], ROOT)
    try:
        report = json.loads(proc.stdout)
        deps = report["dependencies"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise _network_or_crash("pip-audit", proc) from None
    # pip-audit: 0 clean, 1 vulnerabilities found; with a parsed report both are measured.
    if proc.returncode not in (0, 1):
        raise _network_or_crash("pip-audit", proc)
    audit = Audit(audited=0)
    for d in deps:
        name = _norm(str(d.get("name", "")))
        if d.get("skip_reason"):
            audit.unaudited.append(name)
            continue
        audit.audited += 1
        audit.packages.add(name)
        for v in d.get("vulns", []):
            ids = frozenset([str(v["id"]), *map(str, v.get("aliases", []))])
            adv = Advisory(name, str(d.get("version")), ids, ",".join(v.get("fix_versions", [])))
            # pip-audit can list one advisory twice (a distribution seen on two paths,
            # or under two fix-version spellings); an advisory is counted once.
            if not any(a.package == adv.package and a.version == adv.version and a.ids & adv.ids
                       for a in audit.advisories):
                audit.advisories.append(adv)
    return audit


def audit_npm() -> Audit:
    try:
        version = _run(["npm", "--version"], ROOT)
    except NotMeasured as exc:
        raise NotMeasured(f"npm is not installed ({exc})") from exc
    if version.returncode != 0:
        raise NotMeasured("npm is not runnable here")
    if not (ROOT / "package-lock.json").is_file():
        raise DidNotReport("package-lock.json is absent: nothing pins what npm would install")
    proc = _run(["npm", "audit", "--json"], ROOT)
    try:
        report: dict[str, Any] = json.loads(proc.stdout)
        vulns = report["vulnerabilities"]
        total = int(report["metadata"]["dependencies"]["total"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise _network_or_crash("npm audit", proc) from None
    audit = Audit(audited=total)
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    for path in lock.get("packages", {}):
        if "node_modules/" in path:
            audit.packages.add(path.rsplit("node_modules/", 1)[1].lower())
    seen: set[tuple[str, str]] = set()
    for entry in vulns.values():
        for via in entry.get("via", []):
            if not isinstance(via, dict):
                continue  # a package name: the advisory itself is listed under that package
            url = str(via.get("url", ""))
            ghsa = url.rstrip("/").rsplit("/", 1)[-1] if "/advisories/" in url else ""
            ids = frozenset(i for i in (ghsa, str(via.get("source", ""))) if i)
            key = (str(via.get("name")), ghsa or str(via.get("source")))
            if key in seen:
                continue
            seen.add(key)
            audit.advisories.append(Advisory(str(via.get("name")).lower(), str(via.get("range", "")), ids,
                                             "yes" if entry.get("fixAvailable") else "none"))
    return audit


def load_allowlist(path: Path, ecosystem: str, today: dt.date) -> tuple[list[dict[str, str]], list[str]]:
    """This ecosystem's entries, and what is wrong with any of them (all ecosystems)."""
    if not path.is_file():
        return [], [f"{path.name} is absent (an empty `entries` list is how to say none)"]
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        return [], [f"{path.name}: no `entries` list"]
    problems: list[str] = []
    mine: list[dict[str, str]] = []
    for i, e in enumerate(entries):
        label = f"{path.name} entry {i} ({e.get('id', '?')})"
        missing = [k for k in ("id", "ecosystem", "package", "reason", "review_by") if not str(e.get(k, "")).strip()]
        if missing:
            problems.append(f"{label}: missing {', '.join(missing)}")
            continue
        if e["ecosystem"] not in ("pypi", "npm"):
            problems.append(f"{label}: ecosystem must be pypi or npm, not {e['ecosystem']!r}")
            continue
        try:
            review_by = dt.date.fromisoformat(str(e["review_by"]))
        except ValueError:
            problems.append(f"{label}: review_by {e['review_by']!r} is not YYYY-MM-DD")
            continue
        if review_by < today:
            problems.append(f"{label}: review_by {review_by} has passed -- re-review the exception or fix it")
            continue
        if e["ecosystem"] == ecosystem:
            mine.append(e)
    return mine, problems


def judge(name: str, ecosystem: str, audit: Audit, today: dt.date, allowlist: Path) -> int:
    entries, problems = load_allowlist(allowlist, ecosystem, today)
    failed = False
    for p in problems:
        print(f"FAIL {p}")
        failed = True
    used: set[int] = set()
    blocking: list[Advisory] = []
    for adv in audit.advisories:
        hit = next((i for i, e in enumerate(entries)
                    if e["id"] in adv.ids and _norm(e["package"]) == _norm(adv.package)), None)
        if hit is None:
            blocking.append(adv)
        else:
            used.add(hit)
            print(f"note {adv.package} {adv.version}: {adv.primary} allowlisted until "
                  f"{entries[hit]['review_by']} -- {entries[hit]['reason']}")
    for adv in sorted(blocking, key=lambda a: (a.package, a.primary)):
        aliases = ", ".join(sorted(adv.ids - {adv.primary}))
        print(f"FAIL {adv.package} {adv.version}: {adv.primary}"
              + (f" ({aliases})" if aliases else "") + f" -- fixed in: {adv.fix or 'no fix published'}")
        failed = True
    for i, e in enumerate(entries):
        if i not in used and _norm(e["package"]) in {_norm(p) for p in audit.packages}:
            print(f"FAIL {allowlist.name}: {e['id']} ({e['package']}) is no longer reported here -- strike it off")
            failed = True
    if audit.audited == 0:
        print(f"FAIL {name}: 0 packages audited -- an audit that inspected nothing may not pass")
        failed = True
    unaudited = f" ({len(audit.unaudited)} local, not on the index: {', '.join(sorted(audit.unaudited))})" \
        if audit.unaudited else ""
    summary = (f"{name}: {audit.audited} packages audited{unaudited}; "
               f"{len(blocking)} advisories in {len({a.package for a in blocking})} packages, "
               f"{len(used)} allowlisted")
    print(("FAIL " if failed else "ok   ") + summary)
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="ecosystem", required=True)
    pip = sub.add_parser("pip", help="pip-audit over a Python environment")
    pip.add_argument("--pip-audit", required=True, help="path to the pip-audit executable")
    what = pip.add_mutually_exclusive_group(required=True)
    what.add_argument("--requirement", help="a hash-pinned lock to audit (requirements/*.txt)")
    what.add_argument("--python", help="the python whose environment is audited")
    pip.add_argument("--label", default="python", help="a name for the environment, for the summary")
    sub.add_parser("npm", help="npm audit over package-lock.json")
    ap.add_argument("--today", default="", help=argparse.SUPPRESS)  # tests pin the date
    ap.add_argument("--allowlist", default=str(ALLOWLIST), help=argparse.SUPPRESS)
    args = ap.parse_args()
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()

    try:
        if args.ecosystem == "pip":
            audit = audit_pip(args.pip_audit, args.python or "", args.requirement or "")
            return judge(f"pip-audit ({args.label})", "pypi", audit, today, Path(args.allowlist))
        audit = audit_npm()
        return judge("npm audit", "npm", audit, today, Path(args.allowlist))
    except NotMeasured as exc:
        print(f"NOT MEASURED {exc}")
        return 2
    except DidNotReport as exc:
        print(f"FAIL {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
