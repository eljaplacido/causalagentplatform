#!/usr/bin/env python3
"""gate_scope -- a gate that inspected nothing may not report success.

Python port of gnomon's scripts/gate_scope.mjs, for this repository. The bug it
exists for, as gnomon states it: A SELECTOR THAT MATCHES NOTHING RETURNS
SUCCESS. A renamed `--filter`, a git pathspec whose `**/` needs one more
directory than exists -- each reads green while checking less than it claims,
and a vacuous green gets trusted where a red one gets fixed.

TWO KINDS OF CHECK, and the order matters (gnomon's reasoning, kept):

  identity  The honest check. Derive the EXPECTED set from the code, derive the
            ACTUAL set from the tests or the tool's own report, and name every
            item that is expected and missing. Self-maintaining: adding a skill
            module, an MCP tool or a route raises the requirement by itself.

  floor     The fallback, for sets with nothing to derive from: a smoke alarm
            for scope collapse, set well BELOW today's count.

The identity checks here:

  skills-imported     every module in backend/src/skills/ is imported by at
                      least one test under backend/tests/ (ast imports, not text)
  mcp-tools           every tool the MCP server REGISTERS -- read from the live
                      server object, not from a list someone keeps -- is
                      referenced by a test: its wrapper is called, or its
                      registered name appears as a string
  routes-tested       every state-changing route (POST/PUT/PATCH/DELETE) of the
                      live FastAPI app whose handler lives in backend/src/api/ is
                      referenced by a test: an HTTP call whose URL matches the
                      route template, or a direct call of the handler imported
                      from its module
  routes-mounted      every state-changing route DECORATED in backend/src/api/
                      (ast) is mounted on the app -- a router nobody includes is
                      a gap the live app cannot show
  pytest-files        every backend/tests/**/test_*.py reported >= 1 test in the
                      run (from its JUnit XML)
  vitest-files        every frontend/src/**/*.{test,spec}.ts(x) reported >= 1
                      test in the run (from vitest's JSON report)

KNOWN GAPS. Today's real gaps are listed by name in scripts/gate-scope-floors.json
under `known_gaps`, so the gate passes today and fails on any NEW gap. The list
may only shrink:
  * a gap that is no longer a gap must be removed from the list (stale entry =
    FAIL), so closing a gap and recording it are one change;
  * an entry added since the merge base with main is a FAIL unless the commit
    range carries `Ratchet-Exempt: <why>`.

Usage:
  scripts/gate_scope.py identity-all                 # skills, mcp-tools, routes
  scripts/gate_scope.py pytest-files --junit <xml>
  scripts/gate_scope.py vitest-files --json <json>
  scripts/gate_scope.py floor <gate-id> <count>

Run it with the backend's Python: mcp-tools and routes import the live server
and app. Exit 0 = the gate looked at enough; 1 = it did not; 2 = misuse.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections.abc import Iterable
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

FLOORS_FILE = "scripts/gate-scope-floors.json"
BACKEND = ROOT / "backend"
TESTS = BACKEND / "tests"
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


class ScopeError(Exception):
    """The EXPECTED side could not be derived -- the check proves nothing."""


# ── shared ───────────────────────────────────────────────────────────────────


def test_files() -> list[Path]:
    return sorted(p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts)


def parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        warn(f"cannot parse {path.relative_to(ROOT)}: {exc}")
        return None


def backend_python(code: str) -> str:
    """Run a snippet with this interpreter inside backend/ and return stdout."""
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise ScopeError(f"could not load the live object: {proc.stderr.strip()[-400:]}")
    return proc.stdout


# ── skills-imported ──────────────────────────────────────────────────────────


def expected_skills() -> set[str]:
    return {p.stem for p in (BACKEND / "src" / "skills").glob("*.py") if p.stem != "__init__"}


def imported_skill_modules(tree: ast.Module, skills: set[str]) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[:2] == ["src", "skills"] and len(parts) >= 3:
                    found.add(parts[2])
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[:2] == ["src", "skills"]:
                if len(parts) >= 3:
                    found.add(parts[2])
                else:  # from src.skills import <module>
                    found.update(a.name for a in node.names if a.name in skills)
    return found & skills


def check_skills() -> tuple[set[str], set[str], dict[str, str]]:
    expected = expected_skills()
    if not expected:
        raise ScopeError("found no modules in backend/src/skills/")
    actual: set[str] = set()
    where: dict[str, str] = {}
    for f in test_files():
        tree = parse(f)
        if tree is None:
            continue
        for mod in imported_skill_modules(tree, expected):
            actual.add(mod)
            where.setdefault(mod, str(f.relative_to(ROOT)))
    return expected, actual, where


# ── mcp-tools ────────────────────────────────────────────────────────────────

_MCP_SNIPPET = r"""
import asyncio, json
from mcp_server.server import build_server
s = build_server()
names = [t.name for t in asyncio.run(s.list_tools())]
fns = {}
try:
    for t in s._tool_manager.list_tools():
        fns[t.name] = getattr(t.fn, "__name__", "")
except Exception:
    pass
print(json.dumps({"names": names, "fns": fns}))
"""


def called_names(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                out.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                out.add(fn.attr)
    return out


def string_constants(tree: ast.Module) -> set[str]:
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def check_mcp_tools() -> tuple[set[str], set[str], dict[str, str]]:
    live = json.loads(backend_python(_MCP_SNIPPET).strip().splitlines()[-1])
    expected = set(live["names"])
    if not expected:
        raise ScopeError("the live MCP server registered no tools")
    fns: dict[str, str] = live.get("fns", {})
    actual: set[str] = set()
    where: dict[str, str] = {}
    for f in test_files():
        tree = parse(f)
        if tree is None:
            continue
        calls, strings = called_names(tree), string_constants(tree)
        for tool in expected:
            how = None
            if fns.get(tool) and fns[tool] in calls:
                how = f"calls {fns[tool]}()"
            elif tool in strings:
                how = "names it"
            if how:
                actual.add(tool)
                where.setdefault(tool, f"{f.relative_to(ROOT)} ({how})")
    return expected, actual, where


# ── routes ───────────────────────────────────────────────────────────────────

_ROUTES_SNIPPET = r"""
import json
from fastapi.routing import APIRoute
from src.api.main import app

# FastAPI < 0.141 flattened included routers into app.routes as APIRoute objects.
# From 0.141 an included router stays behind a lazy wrapper, and the APIRoute it
# holds carries the path relative to its own router (no prefix). Handle both: walk
# for the APIRoute objects (they carry module and handler), then take the full path
# each one is served at from the OpenAPI schema, which is public and stable.
def collect(routes, out):
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
            continue
        inner = getattr(r, "original_router", None)
        if inner is not None:
            collect(getattr(inner, "routes", []), out)

found = []
collect(app.routes, found)
out = []
for r in found:
    for m in sorted(r.methods):
        out.append({"method": m, "path": r.path, "module": r.endpoint.__module__,
                    "handler": r.endpoint.__name__})

try:
    schema_paths = app.openapi().get("paths", {})
except Exception:
    schema_paths = {}
by_method = {}
for full, ops in schema_paths.items():
    for method in ops:
        by_method.setdefault(method.upper(), []).append(full)
for rec in out:
    served = by_method.get(rec["method"], [])
    if rec["path"] in served:
        continue
    suffixed = [p for p in served if p.endswith(rec["path"])]
    if suffixed:
        rec["path"] = min(suffixed, key=len)
print(json.dumps(out))
"""


def live_routes() -> list[dict[str, str]]:
    """State-changing routes of the live app. Also returns what it rejected, so a gate
    that finds nothing can say what it saw instead of only that it saw nothing."""
    all_routes = json.loads(backend_python(_ROUTES_SNIPPET).strip().splitlines()[-1])
    kept = [
        r for r in all_routes
        if r["method"] in STATE_CHANGING and r["module"].startswith("src.api.")
    ]
    live_routes.seen = {  # type: ignore[attr-defined]
        "routes": len(all_routes),
        "methods": sorted({r["method"] for r in all_routes}),
        "modules": sorted({r["module"] for r in all_routes})[:8],
    }
    return kept


def decorated_routes() -> set[tuple[str, str]]:
    """(module, handler) for every state-changing route decorator in backend/src/api/."""
    out: set[tuple[str, str]] = set()
    for f in sorted((BACKEND / "src" / "api").glob("*.py")):
        tree = parse(f)
        if tree is None:
            continue
        module = f"src.api.{f.stem}"
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                fn = call.func if call else dec
                if isinstance(fn, ast.Attribute) and fn.attr.upper() in STATE_CHANGING:
                    out.add((module, node.name))
                elif (isinstance(fn, ast.Attribute) and fn.attr == "api_route" and call
                      and any(k.arg == "methods" for k in call.keywords)):
                    out.add((module, node.name))
    return out


def route_key(r: dict[str, str]) -> str:
    return f"{r['method']} {r['path']}"


def template_regex(path: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", path)
    body = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(f"^{body}/?$")


WILD = "\x00"


def url_shape(node: ast.AST, consts: dict[str, str]) -> str | None:
    """A test URL expression as a string, with unknown parts replaced by WILD."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) and v.value.id in consts:
                out.append(consts[v.value.id])
            else:
                out.append(WILD)
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = url_shape(node.left, consts), url_shape(node.right, consts)
        if left is None or right is None:
            return None
        return left + right
    return None


def module_constants(tree: ast.Module) -> dict[str, str]:
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                consts[node.targets[0].id] = node.value.value
    return consts


def http_calls(tree: ast.Module) -> Iterable[tuple[str, str]]:
    """(METHOD, url-shape) for every client.post/put/patch/delete/request call."""
    consts = module_constants(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr.lower()
        args = list(node.args)
        url_kw = next((k.value for k in node.keywords if k.arg == "url"), None)
        if attr in {"post", "put", "patch", "delete"}:
            method, target = attr.upper(), (args[0] if args else url_kw)
        elif attr == "request" and args and isinstance(args[0], ast.Constant):
            method, target = str(args[0].value).upper(), (args[1] if len(args) > 1 else url_kw)
        else:
            continue
        if target is None:
            continue
        shape = url_shape(target, consts)
        if shape:
            yield method, shape.split("?", 1)[0].replace(WILD, "X")


def handler_calls(tree: ast.Module) -> set[tuple[str, str]]:
    """(module, name) for handlers imported from src.api.* AND called in this file."""
    imported: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src.api."):
            for a in node.names:
                imported[a.asname or a.name] = (node.module, a.name)
    calls = called_names(tree)
    return {imported[n] for n in calls if n in imported}


def check_routes() -> tuple[set[str], set[str], dict[str, str], set[str], set[str]]:
    routes = live_routes()
    if not routes:
        seen = getattr(live_routes, "seen", {})
        raise ScopeError(
            "the live FastAPI app exposes no state-changing routes under src.api -- "
            f"it did expose {seen.get('routes', 0)} route(s), methods {seen.get('methods')}, "
            f"modules {seen.get('modules')}"
        )
    expected = {route_key(r) for r in routes}
    regex = {route_key(r): template_regex(r["path"]) for r in routes}
    by_handler = {(r["module"], r["handler"]): route_key(r) for r in routes}

    actual: set[str] = set()
    where: dict[str, str] = {}
    for f in test_files():
        tree = parse(f)
        if tree is None:
            continue
        rel = str(f.relative_to(ROOT))
        for method, url in http_calls(tree):
            for key, rx in regex.items():
                if key.startswith(method + " ") and rx.match(url):
                    actual.add(key)
                    where.setdefault(key, f"{rel} (HTTP {method} {url})")
        for mod_name in handler_calls(tree):
            if mod_name in by_handler:
                key = by_handler[mod_name]
                actual.add(key)
                where.setdefault(key, f"{rel} (calls {mod_name[1]}() directly)")

    mounted = {f"{r['module']}.{r['handler']}" for r in routes}
    decorated = {f"{m}.{h}" for (m, h) in decorated_routes()}
    if not decorated:
        raise ScopeError("found no state-changing route decorators in backend/src/api/")
    return expected, actual, where, decorated, mounted


# ── run-report identities ────────────────────────────────────────────────────


def check_pytest_files(junit: Path) -> tuple[set[str], set[str], dict[str, str]]:
    expected = {str(p.relative_to(BACKEND)) for p in TESTS.rglob("test_*.py")}
    if not expected:
        raise ScopeError("found no backend/tests/**/test_*.py files")
    try:
        text = junit.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScopeError(f"no JUnit report at {junit}: {exc}") from exc
    actual: set[str] = set()
    for cls in set(re.findall(r'classname="([^"]+)"', text)):
        parts = cls.split(".")
        for n in range(len(parts), 0, -1):
            cand = "/".join(parts[:n]) + ".py"
            if cand in expected:
                actual.add(cand)
                break
    return expected, actual, {}


def check_vitest_files(report: Path) -> tuple[set[str], set[str], dict[str, str]]:
    front = ROOT / "frontend" / "src"
    pat = re.compile(r"\.(test|spec)\.tsx?$")
    expected = {
        str(p.relative_to(ROOT / "frontend"))
        for p in front.rglob("*")
        if pat.search(p.name) and "node_modules" not in p.parts
    }
    if not expected:
        raise ScopeError("found no frontend/src/**/*.{test,spec}.ts(x) files")
    try:
        data = load_json(report)
    except (OSError, ValueError) as exc:
        raise ScopeError(f"no vitest JSON report at {report}: {exc}") from exc
    actual: set[str] = set()
    for res in data.get("testResults", []):
        name = str(res.get("name", ""))
        if res.get("assertionResults"):
            marker = "/frontend/"
            rel = name.split(marker, 1)[1] if marker in name else name
            actual.add(rel)
    return expected, actual, {}


# ── verdicts ─────────────────────────────────────────────────────────────────


def judge(label: str, expected: set[str], actual: set[str], where: dict[str, str],
          floors: dict, base: dict | None) -> bool:
    """Compare against the known-gaps allowlist. True = pass."""
    known = set(floors.get("known_gaps", {}).get(label, {}).get("items", []))
    missing = expected - actual
    new = sorted(missing - known)
    stale = sorted(known - missing)
    passed = True

    if new:
        bad(f"{label}: {len(new)} NEW gap(s) of {len(expected)} -- the gate would have passed "
            "without looking at these:")
        for item in new:
            print(f"       {item}")
        passed = False
    if stale:
        bad(f"{label}: {len(stale)} listed gap(s) are no longer gaps -- remove them from "
            f"known_gaps.{label} in {FLOORS_FILE} (the list only shrinks):")
        for item in stale:
            print(f"       {item}{'  <- ' + where[item] if item in where else ''}")
        passed = False

    if base is not None:
        old = set(base.get("known_gaps", {}).get(label, {}).get("items", []))
        grown = sorted(known - old)
        if grown:
            reason = ratchet_exempt_reason()
            if reason:
                warn(f"{label}: known_gaps GREW by {grown}; Ratchet-Exempt: {reason}")
            else:
                bad(f"{label}: known_gaps grew since the merge base ({', '.join(grown)}). "
                    "Close the gap instead, or say why with a `Ratchet-Exempt:` commit line.")
                passed = False

    if passed:
        covered = len(expected) - len(missing)
        extra = f", {len(missing)} known gap(s) listed" if missing else ""
        ok(f"{label}: {covered}/{len(expected)} covered{extra}")
    return passed


def base_floors() -> dict | None:
    status, old = baseline_at_base(FLOORS_FILE)
    if status == "found":
        return old
    warn(describe_base_status(status, FLOORS_FILE))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="gate_scope: identity and floor checks")
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("identity-all")
    pj = sub.add_parser("pytest-files")
    pj.add_argument("--junit", required=True, type=Path)
    vj = sub.add_parser("vitest-files")
    vj.add_argument("--json", required=True, type=Path)
    fl = sub.add_parser("floor")
    fl.add_argument("gate_id")
    fl.add_argument("count")
    args = ap.parse_args()

    floors = load_json(ROOT / FLOORS_FILE)

    if args.mode == "floor":
        try:
            actual = int(args.count)
        except ValueError:
            bad(f"{args.gate_id}: scope count {args.count!r} is not an integer -- the gate did not report a count")
            return 1
        spec = floors.get("floors", {}).get(args.gate_id)
        if not spec or not isinstance(spec.get("floor"), int):
            bad(f"{args.gate_id}: no floor declared in {FLOORS_FILE}. Add one with a `why`.")
            return 1
        if actual < spec["floor"]:
            bad(f"{args.gate_id}: inspected {actual}, floor is {spec['floor']}. {spec['why']} "
                "This is a SCOPE failure: check whatever selects the set before assuming "
                "things were deleted.")
            return 1
        ok(f"scope: {args.gate_id} inspected {actual} (floor {spec['floor']})")
        return 0

    base = base_floors()
    checks: list[tuple[str, object]] = []
    if args.mode == "identity-all":
        checks = [("skills-imported", check_skills), ("mcp-tools", check_mcp_tools)]
    elif args.mode == "pytest-files":
        checks = [("pytest-files", lambda: check_pytest_files(args.junit))]
    elif args.mode == "vitest-files":
        checks = [("vitest-files", lambda: check_vitest_files(args.json))]

    passed = True
    for label, fn in checks:
        try:
            expected, actual, where = fn()  # type: ignore[operator]
        except ScopeError as exc:
            bad(f"{label}: could not derive the sets it compares, so this check proves nothing -- {exc}")
            passed = False
            continue
        passed &= judge(label, expected, actual, where, floors, base)

    if args.mode == "identity-all":
        try:
            expected, actual, where, decorated, mounted = check_routes()
        except ScopeError as exc:
            bad(f"routes: the EXPECTED set could not be derived, so this check proves nothing -- {exc}")
            return 1
        passed &= judge("routes-tested", expected, actual, where, floors, base)
        # Every decorated handler must be mounted: a router nobody includes is
        # invisible to routes-tested, which only sees the live app.
        passed &= judge("routes-mounted", decorated, decorated & mounted, {}, floors, base)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
