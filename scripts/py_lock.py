#!/usr/bin/env python3
"""py_lock -- the Python environments CI tests install from a hash-pinned lock, and the lock is current.

THE LOCKS. requirements/<name>.in says what an environment holds; `relock` resolves
it with uv into requirements/<name>.txt, every distribution pinned `==` with the
sha256 of each of its files (`uv pip compile --generate-hashes --universal`, one lock
valid on every platform, markers where a dependency is platform-specific):

    backend  backend/.venv in CI: the backend with its dev tools, causal-core, causal-decision
    kernel   packages/causal-core/.venv in CI (and release.yml): the three kernel packages
    image    what backend/Dockerfile installs into the backend image

The repository's own packages (an `-e ./path[extras]` or `./path[extras]` line in a
.in file) are resolved from their pyproject.toml and left out of the lock: there is
no index file to hash, they come from this checkout. So an environment installs in
two steps, and the first one is the only one that fetches anything:

    python -m pip install --require-hashes --no-deps -r requirements/backend.txt
    python -m pip install --no-deps --no-build-isolation -e packages/causal-core ...

`--no-deps` on the second keeps pip from resolving anything the lock did not pin, and
`--no-build-isolation` builds the local packages with the hatchling the lock installed
instead of fetching an unhashed one. `pip check` afterwards proves the pair is whole.

WHAT `check` HOLDS, per lock (the gate step `python-lock`):

  1. the lock parses, and every requirement in it is `name==version` with at least one
     --hash (no ranges, no URLs, no editables);
  2. the lock is current: the fingerprint of its inputs -- the .in file, and for each
     local package it names the dependency tables of its pyproject.toml (dependencies,
     optional-dependencies, requires-python, build-system.requires) -- equals the one
     `relock` recorded in requirements/lock-inputs.json. A pyproject edit that touches a
     dependency, without re-locking, FAILS here;
  3. the lock satisfies what the local packages ask for: each of their requirements
     (with the extras the .in file selects) whose marker holds is met by a pinned
     version (a lock edited by hand below a floor FAILS);
  4. with `--env NAME=PYTHON`: the environment PYTHON runs in IS the lock -- the same
     distributions at the same versions, the local packages aside, nothing missing and
     nothing extra -- and `pip check` passes in it. The pip-audit steps audit the lock
     file, so this is what makes that audit an audit of the environment the tests ran in.

Re-lock (needs uv; the fingerprint is written with the locks):

    python3 scripts/py_lock.py relock              # keep existing pins where they still fit
    python3 scripts/py_lock.py relock --upgrade    # move every pin to the newest that fits

Make a local environment equal its lock (installs, then removes what the lock lacks):

    python3 scripts/py_lock.py sync backend --python backend/.venv/bin/python

Exit (check): 0 every lock holds; 1 a check failed; 2 not measured (packaging is absent
from the interpreter running this, or an --env interpreter cannot run).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOCKS = ("backend", "kernel", "image", "release")
FINGERPRINTS = "requirements/lock-inputs.json"
#: The resolution the locks are made with. Part of the fingerprint: a lock made with
#: other options is not the lock this file describes.
COMPILE_OPTIONS = ["--universal", "--python-version", "3.12", "--generate-hashes"]
RELOCK_COMMAND = "python3 scripts/py_lock.py relock"

#: A local package line in a .in file: `-e ./path[extra,extra]` or `./path[extra]`.
LOCAL_LINE = re.compile(r"^(?:-e\s+)?(\./[^\s\[;]+)(?:\[([^\]]*)\])?\s*$")
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)\s*(?:;\s*(.+?))?\s*$")

#: The marker environment of an interpreter, computed inside it with the standard
#: library only (what packaging.markers.default_environment returns), and its
#: installed distributions.
PROBE = r"""
import importlib.metadata as md, json, os, platform, sys
def fmt(info):
    v = f"{info.major}.{info.minor}.{info.micro}"
    if info.releaselevel != "final":
        v += info.releaselevel[0] + str(info.serial)
    return v
env = {
    "implementation_name": sys.implementation.name,
    "implementation_version": fmt(sys.implementation.version),
    "os_name": os.name,
    "platform_machine": platform.machine(),
    "platform_release": platform.release(),
    "platform_system": platform.system(),
    "platform_version": platform.version(),
    "python_full_version": platform.python_version(),
    "platform_python_implementation": platform.python_implementation(),
    "python_version": ".".join(platform.python_version_tuple()[:2]),
    "sys_platform": sys.platform,
}
dists = {}
for d in md.distributions():
    name = d.metadata["Name"]
    if name:
        dists[name] = d.version
print(json.dumps({"markers": env, "dists": dists}))
"""


class NotMeasured(Exception):
    """The check could not run: a tool or an interpreter it needs is absent."""


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class Local:
    path: str  # relative to the root, e.g. "packages/causal-core"
    extras: tuple[str, ...]
    name: str


@dataclass(frozen=True)
class Pin:
    name: str  # normalised
    version: str
    marker: str
    hashes: int


@dataclass
class Lock:
    name: str
    pins: list[Pin] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def paths(name: str, root: Path) -> tuple[Path, Path]:
    return root / "requirements" / f"{name}.in", root / "requirements" / f"{name}.txt"


def _pyproject(root: Path, rel: str) -> dict[str, Any]:
    with (root / rel / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def locals_of(name: str, root: Path) -> list[Local]:
    src, _ = paths(name, root)
    out: list[Local] = []
    for raw in src.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        m = LOCAL_LINE.match(line)
        if not m:
            continue
        rel = m.group(1)[2:].rstrip("/")
        extras = tuple(sorted(e.strip() for e in (m.group(2) or "").split(",") if e.strip()))
        out.append(Local(rel, extras, norm(_pyproject(root, rel)["project"]["name"])))
    return out


def fingerprint(name: str, root: Path) -> dict[str, str]:
    """A digest per input: the .in file, and the dependency tables of each local pyproject."""
    src, _ = paths(name, root)
    text = "\n".join(line.rstrip() for line in src.read_text(encoding="utf-8").splitlines()).strip()
    digests = {
        "options": hashlib.sha256(json.dumps(COMPILE_OPTIONS).encode()).hexdigest(),
        src.relative_to(root).as_posix(): hashlib.sha256(text.encode()).hexdigest(),
    }
    for loc in locals_of(name, root):
        data = _pyproject(root, loc.path)
        project = data.get("project", {})
        relevant = {
            "name": project.get("name"),
            "requires-python": project.get("requires-python"),
            "dependencies": project.get("dependencies", []),
            "optional-dependencies": project.get("optional-dependencies", {}),
            "build-system.requires": data.get("build-system", {}).get("requires", []),
        }
        blob = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
        digests[f"{loc.path}/pyproject.toml"] = hashlib.sha256(blob.encode()).hexdigest()
    return digests


def parse_lock(name: str, root: Path) -> Lock:
    _, out = paths(name, root)
    lock = Lock(name)
    if not out.is_file():
        lock.problems.append(f"{out.relative_to(root)} is absent -- {RELOCK_COMMAND}")
        return lock
    # Join continuation lines, drop comments.
    logical: list[str] = []
    buf = ""
    for raw in out.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        if buf.strip():
            logical.append(buf.strip())
        buf = ""
    seen: dict[tuple[str, str], str] = {}
    for req in logical:
        if req.startswith("-"):
            lock.problems.append(f"{out.name}: `{req[:60]}` -- an option or editable line; the lock pins index files only")
            continue
        hashes = re.findall(r"--hash=sha256:[0-9a-f]{64}", req)
        spec = re.sub(r"--hash=\S+", "", req).strip()
        m = PIN.match(spec)
        if not m:
            lock.problems.append(f"{out.name}: `{spec[:60]}` is not name==version")
            continue
        if not hashes:
            lock.problems.append(f"{out.name}: {m.group(1)}=={m.group(2)} has no --hash")
            continue
        pin = Pin(norm(m.group(1)), m.group(2), (m.group(3) or "").strip(), len(hashes))
        key = (pin.name, pin.marker)
        if key in seen:
            lock.problems.append(f"{out.name}: {pin.name} pinned twice under the same marker")
        seen[key] = pin.version
        lock.pins.append(pin)
    if not lock.pins and not lock.problems:
        lock.problems.append(f"{out.name}: no pins -- a lock that pins nothing may not pass")
    return lock


def _packaging() -> Any:
    try:
        from packaging.markers import Marker
        from packaging.requirements import Requirement
        from packaging.version import Version
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        raise NotMeasured(f"`packaging` is not importable by {sys.executable}") from exc
    return Marker, Requirement, Version


def applicable(pins: list[Pin], markers: dict[str, str]) -> dict[str, str]:
    """The pins whose marker holds in this environment, as {name: version}."""
    Marker, _, _ = _packaging()
    out: dict[str, str] = {}
    for p in pins:
        if not p.marker or Marker(p.marker).evaluate(markers):
            out[p.name] = p.version
    return out


def roots_satisfied(name: str, root: Path, lock: Lock, markers: dict[str, str]) -> list[str]:
    """Each local package's own requirements (with the selected extras) are met by the lock."""
    _, Requirement, Version = _packaging()
    pinned = applicable(lock.pins, markers)
    local_names = {loc.name for loc in locals_of(name, root)}
    problems: list[str] = []
    for loc in locals_of(name, root):
        project = _pyproject(root, loc.path)["project"]
        reqs = list(project.get("dependencies", []))
        for extra in loc.extras:
            if extra not in project.get("optional-dependencies", {}):
                problems.append(f"{loc.path}: the .in file selects extra [{extra}], which pyproject.toml does not define")
                continue
            reqs += project["optional-dependencies"][extra]
        for text in reqs:
            req = Requirement(text)
            rname = norm(req.name)
            if rname in local_names:
                continue
            if req.marker is not None and not req.marker.evaluate({**markers, "extra": ""}):
                continue
            if rname not in pinned:
                problems.append(f"{loc.path} requires {text!r}; the lock does not pin {rname}")
            elif not req.specifier.contains(Version(pinned[rname]), prereleases=True):
                problems.append(f"{loc.path} requires {text!r}; the lock pins {rname}=={pinned[rname]}")
    return problems


def probe(python: str) -> dict[str, Any]:
    try:
        proc = subprocess.run([python, "-c", PROBE], capture_output=True, text=True, check=False, timeout=120)
    except (FileNotFoundError, PermissionError) as exc:
        raise NotMeasured(f"cannot run {python}: {exc}") from exc
    if proc.returncode != 0:
        raise NotMeasured(f"{python} could not describe its environment: {proc.stderr.strip()[-200:]}")
    info: dict[str, Any] = json.loads(proc.stdout)
    return info


def env_equals_lock(name: str, root: Path, lock: Lock, python: str) -> tuple[list[str], int]:
    info = probe(python)
    expected = applicable(lock.pins, info["markers"])
    local_names = {loc.name for loc in locals_of(name, root)}
    installed = {norm(k): v for k, v in info["dists"].items() if norm(k) not in local_names}
    problems: list[str] = []
    for pkg in sorted(expected.keys() - installed.keys()):
        problems.append(f"env {name}: {pkg}=={expected[pkg]} is locked, not installed")
    for pkg in sorted(installed.keys() - expected.keys()):
        problems.append(f"env {name}: {pkg}=={installed[pkg]} is installed, not in the lock")
    for pkg in sorted(expected.keys() & installed.keys()):
        if expected[pkg] != installed[pkg]:
            problems.append(f"env {name}: {pkg} is {installed[pkg]} installed, {expected[pkg]} locked")
    missing_locals = local_names - {norm(k) for k in info["dists"]}
    for pkg in sorted(missing_locals):
        problems.append(f"env {name}: the local package {pkg} is not installed")
    proc = subprocess.run([python, "-m", "pip", "check"], capture_output=True, text=True, check=False, timeout=300)
    if proc.returncode != 0:
        first = (proc.stdout.strip() or proc.stderr.strip()).splitlines()[:3]
        problems.append(f"env {name}: pip check failed: {' | '.join(first)}")
    if problems:
        problems.append(f"env {name}: make it equal the lock -- python3 scripts/py_lock.py sync {name} --python {python}")
    return problems, len(installed)


def check_one(name: str, root: Path, recorded: dict[str, Any], markers: dict[str, str],
              python: str | None) -> tuple[list[str], int, str]:
    """(problems, pins, a note on the environment) for one lock."""
    src, _ = paths(name, root)
    if not src.is_file():
        return [f"{src.relative_to(root)} is absent"], 0, ""
    lock = parse_lock(name, root)
    problems = list(lock.problems)
    want = recorded.get(name, {}).get("inputs")
    have = fingerprint(name, root)
    if want is None:
        problems.append(f"{name}: no fingerprint in {FINGERPRINTS} -- {RELOCK_COMMAND}")
    elif want != have:
        changed = sorted(k for k in have.keys() | want.keys() if have.get(k) != want.get(k))
        problems.append(f"{name}: the lock is stale -- changed since it was made: {', '.join(changed)}. "
                        f"Re-lock: {RELOCK_COMMAND}")
    problems += roots_satisfied(name, root, lock, markers)
    note = ""
    if python is not None:
        env_problems, n = env_equals_lock(name, root, lock, python)
        problems += env_problems
        note = f"env {name} {'!=' if env_problems else '='} lock ({n} dists)"
    return problems, len(lock.pins), note


def check(names: list[str], envs: dict[str, str], root: Path) -> int:
    recorded: dict[str, Any] = {}
    fp_file = root / FINGERPRINTS
    if fp_file.is_file():
        recorded = json.loads(fp_file.read_text(encoding="utf-8")).get("locks", {})
    failed = False
    total = 0
    env_notes: list[str] = []
    here = probe(sys.executable)["markers"]
    for name in names:
        python = envs.get(name)
        markers = probe(python)["markers"] if python else here
        problems, pins, note = check_one(name, root, recorded, markers, python)
        total += pins
        if note:
            env_notes.append(note)
        for p in problems:
            print(f"FAIL {p}")
        if problems:
            failed = True
        else:
            print(f"ok   {name}: {pins} pins, every one hashed, inputs current"
                  + (f", {note}" if note else ""))
    for extra in sorted(envs.keys() - set(names)):
        print(f"FAIL --env {extra}: no such lock")
        failed = True
    if total == 0:
        print("FAIL 0 pins checked -- a check that inspected nothing may not pass")
        failed = True
    summary = (f"python-lock: {len(names)} locks, {total} pins, every one hashed"
               + (", inputs current" if not failed else "")
               + (f"; {'; '.join(env_notes)}" if env_notes else ""))
    print(("FAIL " if failed else "ok   ") + summary)
    return 1 if failed else 0


def uv_bin() -> str:
    uv = os.environ.get("UV") or shutil.which("uv")
    if not uv:
        sys.exit("uv is not on PATH (or $UV): python3 -m pip install uv -- it is needed to re-lock, not to check")
    return uv


def relock(names: list[str], upgrade: bool, root: Path) -> int:
    uv = uv_bin()
    fp_file = root / FINGERPRINTS
    data: dict[str, Any] = json.loads(fp_file.read_text(encoding="utf-8")) if fp_file.is_file() else {}
    data.setdefault("_comment", [
        "Written by scripts/py_lock.py relock; read by its check (the gate step python-lock).",
        "Per lock, a digest of each input it was made from: the .in file, and the dependency",
        "tables of each local pyproject.toml it names. Do not edit by hand -- re-lock.",
    ])
    locks = data.setdefault("locks", {})
    version = subprocess.run([uv, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    version = version.split(" (", 1)[0]  # "uv 0.11.30", not the build's platform
    for name in names:
        src, out = paths(name, root)
        cmd = [uv, "pip", "compile", str(src.relative_to(root)), "-o", str(out.relative_to(root)),
               *COMPILE_OPTIONS, "--custom-compile-command", RELOCK_COMMAND, "--quiet"]
        for loc in locals_of(name, root):
            # --refresh-package: uv caches the wheel it builds from a local path, and
            # after the package's version bumps it can resolve against that stale
            # build (causal-core 0.10 -> 0.11 did, until UV_NO_CACHE=1). Rebuild the
            # local packages every time; index packages keep their cache.
            cmd += ["--no-emit-package", loc.name, "--refresh-package", loc.name]
        if upgrade:
            cmd.append("--upgrade")
        print("+", " ".join(cmd[1:]))
        proc = subprocess.run(cmd, cwd=root, check=False)
        if proc.returncode != 0:
            print(f"FAIL uv pip compile {name} exited {proc.returncode}")
            return 1
        locks[name] = {"made_with": version, "inputs": fingerprint(name, root)}
    fp_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"ok   re-locked {', '.join(names)}; fingerprints in {FINGERPRINTS}")
    return 0


def sync(name: str, python: str, root: Path) -> int:
    _, out = paths(name, root)
    lock = parse_lock(name, root)
    if lock.problems:
        for p in lock.problems:
            print(f"FAIL {p}")
        return 1
    run = [python, "-m", "pip", "install", "--require-hashes", "--no-deps", "-r", str(out)]
    print("+", " ".join(run[1:]))
    if subprocess.run(run, cwd=root, check=False).returncode != 0:
        return 1
    local_args: list[str] = []
    for loc in locals_of(name, root):
        spec = f"{loc.path}[{','.join(loc.extras)}]" if loc.extras else loc.path
        local_args += (["-e", spec] if name != "image" else [spec])
    run = [python, "-m", "pip", "install", "--no-deps", "--no-build-isolation", *local_args]
    print("+", " ".join(run[1:]))
    if subprocess.run(run, cwd=root, check=False).returncode != 0:
        return 1
    info = probe(python)
    keep = set(applicable(lock.pins, info["markers"])) | {loc.name for loc in locals_of(name, root)}
    extra = sorted(n for n in info["dists"] if norm(n) not in keep)
    if extra:
        run = [python, "-m", "pip", "uninstall", "-y", *extra]
        print("+", " ".join(run[1:]))
        if subprocess.run(run, cwd=root, check=False).returncode != 0:
            return 1
    return check([name], {name: python}, root)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)  # tests point it at a copy
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="every lock is well-formed, current and (with --env) installed")
    c.add_argument("names", nargs="*", default=list(LOCKS))
    c.add_argument("--env", action="append", default=[], metavar="NAME=PYTHON",
                   help="also require the environment of PYTHON to equal lock NAME")
    r = sub.add_parser("relock", help="re-resolve the locks with uv and record their inputs")
    r.add_argument("names", nargs="*", default=list(LOCKS))
    r.add_argument("--upgrade", action="store_true", help="move every pin to the newest version that fits")
    s = sub.add_parser("sync", help="make an environment equal a lock")
    s.add_argument("name", choices=LOCKS)
    s.add_argument("--python", required=True)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    try:
        if args.cmd == "relock":
            return relock(args.names, args.upgrade, root)
        if args.cmd == "sync":
            return sync(args.name, args.python, root)
        envs: dict[str, str] = {}
        for item in args.env:
            key, sep, py = item.partition("=")
            if not sep or not py:
                print(f"FAIL --env {item!r}: expected NAME=PYTHON")
                return 1
            envs[key] = py
        return check(args.names, envs, root)
    except NotMeasured as exc:
        print(f"NOT MEASURED {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
