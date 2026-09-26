#!/usr/bin/env python3
"""skill_refs -- the agent-facing instructions still point at things that exist.

The skills (.gnomon/skills/*.md), AGENTS.md, CONTRIBUTING.md,
.github/copilot-instructions.md, .gnomon/system.md (the harness's system
prompt) and the prose in .gnomon/roles.toml, tools.toml and policy.toml tell an
agent what to run and where things are. They are prose, so when code moves they drift in silence: a
gate list stops naming a gate, a removed step is still described, a script is
renamed. This checks what can be checked mechanically. Standard library only.

WHAT IT CHECKS, per surface file:

  paths      every repository path the text names resolves: in a code span, a
             code block, a markdown link, or -- stricter, below -- bare prose.
             Globs are allowed if they match something. A directory is a path
             when a file under it exists.
  commands   every command it tells an agent to run resolves:
               python3 scripts/X.py, bash X, scripts/X.py, .gnomon/X.sh
                                     the script exists
               septacore <sub>       <sub> is in the pinned list (below)
               <dir>/.venv/bin/<tool>
                                     <dir> holds a pyproject.toml, and <tool>
                                     is python/pip or a distribution that
                                     pyproject depends on (any extra)
               pytest -m <expr>      every marker in <expr> is declared in the
                                     pyproject of the directory pytest runs in
               npm run <script>      <script> is in the package.json of the
                                     directory npm runs in
  markers    a table with Where and Marker columns: each marker is declared in
             the pyproject.toml of the package in its Where cell.
  steps      a code span naming a ci.sh step (a kebab-case name with "step" or
             "row" near it) is a step .gnomon/ci.sh records; and every step
             ci.sh records by a literal name is named in verifying-changes.md,
             so a new step cannot be missing from the skill.
  gates      verifying-changes.md's gate table (the one with a Pre-registration
             column): every row's benchmarks/<dir>/ holds a PRE-REGISTRATION.md,
             and every benchmarks/<dir>/PRE-REGISTRATION.md has a row. A gate id
             (T0, D0, A0, ...) named anywhere is one of the table's; a list of
             three or more gate ids names all of them (a partial enumeration is
             how a gate list goes stale); and a skill whose `match` fires on one
             gate id fires on every one.
  skills     every .gnomon/skills/*.md is named in AGENTS.md's list of skills.
  front matter  every skill opens with TOML between +++ lines that parses, has
             string name, description and match, `match` compiles as a regular
             expression (case-insensitive, as gnomon applies it), and any
             `roles` are roles .gnomon/roles.toml declares.

HOW A PATH IS RECOGNISED. A token in a code span, a code block or a link is a
path when it has a slash and (its first segment is a top-level entry of the
repository, or it ends in a file extension, or it ends in `/`), or it has no
slash and ends in a file extension (a bare file name) or is a dotfile. In bare
prose and TOML comments only the first form counts -- a slash token anchored to a
top-level entry -- because "read/write" is prose. Not paths: anything holding a
placeholder (<gate>, {name}, $VAR, ...), URLs, ~/..., absolute paths (/models
is a gnomon command), @scope/packages, flags. `.venv` paths are environments,
never tracked: the directory that holds the venv is checked instead.

HOW A PATH RESOLVES. Against the files git would commit (tracked, plus untracked
and not ignored), as they are on disk. A link resolves against its document's
directory, as a renderer does. A code line resolves against the directory a
`cd` in it moved to (a line that is only `cd X` moves the rest of its block).
Anything else anchored to a top-level entry resolves against the root. A
relative path (causal_core/bench/defects.py, src/db/types.py) resolves against
the document's directory, the root, or a package root: backend/,
packages/<name>/, packages/<name>/src/, benchmarks/. A bare file name must be
the name of some file in the tree -- a weaker check, counted separately. A
path that does not resolve but that git ignores (backend/.coverage, a local
.env) is a build output or a local file, never in the tree by design: it is
counted as ignored, not checked.

THE CONFIG (scripts/skill-refs.json) holds two reviewed lists:

  not_paths   deliberate non-paths that look like paths ({ref or refs, in,
              reason}).
              An entry that no longer matches a failing reference fails the
              check, so the list cannot outlive what it excuses.
  septacore   the subcommands `septacore <sub>` may name. Pinned, not read live:
              septacore is the operator's harness and CI does not install it,
              so a live-only check would be SKIPPED on every CI run and hold
              nothing there. Where septacore IS on PATH, its --help is read and
              the pin must match it: a pinned name it no longer lists fails, a
              name it lists that the pin lacks is a note.

Exit: 0 every reference resolved; 1 a reference did not, the config is wrong,
or nothing was checked (a check that inspected nothing may not pass).

    scripts/skill_refs.py [--root DIR] [--config FILE] [--septacore BIN|'']
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gatelib import bad, ok, warn  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "scripts/skill-refs.json"
SURFACES = (
    ".gnomon/skills/*.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    ".github/copilot-instructions.md",
    ".gnomon/system.md",
    ".gnomon/roles.toml",
    ".gnomon/tools.toml",
    ".gnomon/policy.toml",
)
GATE_SKILL = ".gnomon/skills/verifying-changes.md"
CI_SH = ".gnomon/ci.sh"

EXTENSIONS = frozenset(
    "py md json jsonl toml sh ts tsx mts cts mjs cjs js sql yml yaml txt csv cfg ini lock".split()
)
PLACEHOLDER = re.compile(r"[<>{}$…]|\.\.\.")
GATE_ID = re.compile(r"\b([TDA]\d+)\b")
_GATE_ITEM = r"\b[TDA]\d+\b(?:\s*[–-]\s*[TDA]\d+\b)?"  # T0, T10, or a range T2–T8
GATE_RUN = re.compile(rf"{_GATE_ITEM}(?:\s*(?:,|/|\band\b|\bor\b)\s*{_GATE_ITEM})*")
GATE_RANGE = re.compile(r"\b([TDA])(\d+)\s*[–-]\s*([TDA])(\d+)\b")
STEP_NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
STEP_CONTEXT = re.compile(r"\b(?:step|steps|row|rows)\b")
CODE_SPAN = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s]+)\)")
ALWAYS_THERE_TOOLS = frozenset({"python", "python3", "pip", "pip3"})
BUILTIN_MARKERS = frozenset(
    {"skip", "skipif", "xfail", "parametrize", "usefixtures", "filterwarnings"}
)
MARKER_WORDS = frozenset({"and", "or", "not"})


# ── The tree ─────────────────────────────────────────────────────────────────


class Tree:
    """The files git would commit from this working tree, and their directories."""

    def __init__(self, root: Path) -> None:
        self.root = root
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git ls-files failed: {proc.stderr.decode(errors='replace').strip()[:200]}")
        names = [p for p in proc.stdout.decode("utf-8", errors="replace").split("\0") if p]
        self.files = {p for p in names if (root / p).is_file()}
        self.dirs: set[str] = set()
        for f in self.files:
            parts = f.split("/")
            for i in range(1, len(parts)):
                self.dirs.add("/".join(parts[:i]))
        self.basenames = {f.rsplit("/", 1)[-1] for f in self.files}
        self.top = {f.split("/", 1)[0] for f in self.files}
        # Package roots a relative path inside a package may be written against.
        bases = ["backend", "benchmarks"]
        for d in sorted(self.dirs):
            if re.fullmatch(r"packages/[^/]+", d):
                bases.append(d)
                if f"{d}/src" in self.dirs:
                    bases.append(f"{d}/src")
        self.package_bases = [b for b in bases if b in self.dirs]

    def exists(self, path: str) -> bool:
        rel = norm(path)
        if rel is None:
            return False
        if rel == "":
            return True
        if "*" in rel or "?" in rel:
            rx = glob_regex(rel)
            return any(rx.fullmatch(p) for p in self.files | self.dirs)
        return rel in self.files or rel in self.dirs

    def is_file(self, rel: str) -> bool:
        r = norm(rel)
        return r is not None and r in self.files


def norm(rel: str) -> str | None:
    """A repository-relative posix path, or None when it leaves the repository."""
    rel = rel.strip()
    if rel in ("", ".", "./"):
        return ""
    out = posixpath.normpath(rel)
    if out == ".":
        return ""
    if out.startswith("../") or out == ".." or out.startswith("/"):
        return None
    return out


def join(base: str, rel: str) -> str:
    return posixpath.join(base, rel) if base else rel


def glob_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


# ── Findings ─────────────────────────────────────────────────────────────────


@dataclass
class Finding:
    file: str
    line: int
    kind: str
    ref: str
    why: str


@dataclass
class Report:
    counts: Counter[str] = field(default_factory=Counter)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    placeholders: int = 0
    seen: set[tuple[str, int, str, str]] = field(default_factory=set)

    def check(self, file: str, line: int, kind: str, ref: str, good: bool, why: str) -> None:
        key = (file, line, kind, ref)
        if key in self.seen:
            return
        self.seen.add(key)
        self.counts[kind] += 1
        if not good:
            self.findings.append(Finding(file, line, kind, ref, why))


# ── Tokens ───────────────────────────────────────────────────────────────────


def clean(tok: str) -> str:
    tok = tok.strip().strip("`\"'*").lstrip("(")
    tok = tok.rstrip(".,;:)!?'\"*")
    tok = split_extras(tok)
    if tok.count("[") < tok.count("]"):
        tok = tok.rstrip("]")
    tok = tok.lstrip("[")
    return re.sub(r":\d+(?:-\d+)?$", "", tok)  # file:line


def split_extras(tok: str) -> str:
    """`packages/causal-mcp[dev,http]` -> `packages/causal-mcp` (a pip requirement)."""
    return re.sub(r"\[[A-Za-z0-9_,.-]*\]$", "", tok)


def is_path(tok: str, tree: Tree, strict: bool) -> bool:
    if not tok or tok.startswith(("-", "~", "/", "@", "#")) or "://" in tok or "=" in tok:
        return False
    if "mcp__" in tok or "::" in tok or " " in tok:
        return False
    first = tok.split("/", 1)[0]
    anchored = "/" in tok and (first in tree.top or first in ("..", "."))
    if strict:
        return anchored
    stem, _, ext = tok.rstrip("/").rsplit("/", 1)[-1].rpartition(".")
    has_ext = bool(stem) and ext.lower() in EXTENSIONS
    if "/" in tok:
        return anchored or has_ext or tok.endswith("/")
    # A dotfile (.gitignore), but not an extension named as such (".sh").
    dotfile = bool(re.fullmatch(r"\.[A-Za-z][A-Za-z0-9_.-]*", tok)) and tok[1:].lower() not in EXTENSIONS
    return has_ext or dotfile


# ── Units of text ────────────────────────────────────────────────────────────


@dataclass
class Unit:
    """One line of a surface, and how to read it."""

    line: int
    text: str
    mode: str  # "code" (a code-block line), "prose" (markdown or a TOML comment)
    ctx: str = ""  # the directory a standalone `cd` moved this code block to


def markdown_units(text: str) -> tuple[list[Unit], int]:
    """The body's lines as units. Returns (units, first body line)."""
    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "+++":
        for i in range(1, len(lines)):
            if lines[i].strip() == "+++":
                start = i + 1
                break
    units: list[Unit] = []
    fence = False
    indented = False
    ctx = ""
    prev_blank = True
    i = start
    while i < len(lines):
        raw = lines[i]
        n = i + 1
        stripped = raw.strip()
        if stripped.startswith("```"):
            fence = not fence
            ctx = ""
            i += 1
            prev_blank = False
            continue
        code = fence or ((raw.startswith("    ") or raw.startswith("\t")) and (prev_blank or indented))
        if code:
            # Join backslash continuations into one logical line.
            logical = raw
            while logical.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                logical = logical.rstrip()[:-1] + " " + lines[i].strip()
            indented = not fence
            units.append(Unit(n, logical, "code", ctx))
            ctx = standalone_cd(logical, ctx)
        else:
            if stripped:
                indented = False
                ctx = "" if not fence else ctx
            units.append(Unit(n, raw, "prose"))
        prev_blank = not stripped
        i += 1
    return units, start


def standalone_cd(line: str, ctx: str) -> str:
    m = re.fullmatch(r"\s*cd\s+(\S+)\s*(?:#.*)?", line)
    if not m:
        return ctx
    new = norm(join(ctx, m.group(1)))
    return new if new is not None else ctx


def toml_units(text: str) -> list[Unit]:
    units = []
    for i, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if s.startswith("#"):
            units.append(Unit(i, s.lstrip("#"), "prose"))
    return units


# ── ci.sh steps ──────────────────────────────────────────────────────────────


def ci_steps(root: Path, tree: Tree) -> tuple[set[str], set[str]]:
    """(steps recorded under a literal name, every step name ci.sh can record)."""
    path = root / CI_SH
    if not path.is_file():
        return set(), set()
    src = path.read_text(encoding="utf-8")
    literal: set[str] = set()
    for m in re.finditer(r"^\s*(?:record|ratchet_step|audit_step)\s+\"?([a-z][a-z0-9-]+)\"?\s", src, re.M):
        literal.add(m.group(1))
    for m in re.finditer(r"for\s+\w+\s+in\s+([a-z0-9 -]+);\s*do\s+record\s+\"\$\w+\"", src):
        literal.update(m.group(1).split())
    for m in re.finditer(r"\bstep=([a-z][a-z0-9-]+)", src):
        literal.add(m.group(1))
    dynamic = set()
    for d in tree.dirs:
        pkg = re.fullmatch(r"packages/([^/]+)", d)
        if pkg and tree.is_file(f"{d}/pyproject.toml"):
            dynamic.update({pkg.group(1), f"{pkg.group(1)}-mypy"})
    return literal, literal | dynamic


# ── pyproject facts ──────────────────────────────────────────────────────────


def load_toml(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def dist_name(req: str) -> str:
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
    return re.sub(r"[-_.]+", "-", m.group(1)).lower() if m else ""


def project_dists(data: dict[str, Any]) -> set[str]:
    proj = data.get("project", {})
    reqs: list[str] = list(proj.get("dependencies", []))
    for extra in proj.get("optional-dependencies", {}).values():
        reqs.extend(extra)
    for group in data.get("dependency-groups", {}).values():
        reqs.extend(r for r in group if isinstance(r, str))
    return {dist_name(r) for r in reqs} - {""}


def markers(data: dict[str, Any]) -> set[str]:
    raw = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
    return {str(m).split(":", 1)[0].split("(", 1)[0].strip() for m in raw}


def marker_names(expr: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z_]\w*", expr) if w not in MARKER_WORDS]


# ── The checker ──────────────────────────────────────────────────────────────


class Checker:
    def __init__(self, root: Path, config: dict[str, Any], septacore_live: set[str] | None) -> None:
        self.root = root
        self.tree = Tree(root)
        self.report = Report()
        self.not_paths: list[dict[str, str]] = list(config.get("not_paths", []))
        self.used_not_paths: set[tuple[int, str]] = set()
        self.septacore = set(config.get("septacore", {}).get("subcommands", []))
        self.septacore_live = septacore_live
        self.steps_literal, self.steps_all = ci_steps(root, self.tree)
        self.gate_ids: list[str] = []
        self.ignore_cache: dict[str, bool] = {}

    # -- paths --

    def excused(self, file: str, ref: str) -> bool:
        for i, e in enumerate(self.not_paths):
            if ref in entry_refs(e) and e.get("in") == file:
                self.used_not_paths.add((i, ref))
                return True
        return False

    def ignored(self, rel: str) -> bool:
        """A path git ignores is a build output or a local file: never in the tree by design."""
        r = norm(rel)
        if not r:
            return False
        if r not in self.ignore_cache:
            proc = subprocess.run(["git", "check-ignore", "-q", "--no-index", r],
                                  cwd=self.root, capture_output=True, check=False)
            self.ignore_cache[r] = proc.returncode == 0
        return self.ignore_cache[r]

    def path_ref(self, file: str, unit: Unit, raw: str, strict: bool, link: bool = False) -> None:
        tok = clean(raw)
        if link:
            tok = tok.split("#", 1)[0]
            if not tok or tok.startswith(("http:", "https:", "mailto:")):
                return
        if PLACEHOLDER.search(tok):
            if is_path(PLACEHOLDER.sub("x", tok), self.tree, strict):
                self.report.placeholders += 1
            return
        if not link and not is_path(tok, self.tree, strict):
            return
        docdir = posixpath.dirname(file)
        if "/.venv" in f"/{tok}":
            self.venv_ref(file, unit, tok)
            return
        bases = [docdir] if link else self.bases(tok, unit, docdir)
        if link:
            kind, good = "link", self.tree.exists(join(docdir, tok))
        elif "/" not in tok:
            kind = "bare name"
            name = tok.rstrip("/")
            good = name in self.tree.basenames or any(d.rsplit("/", 1)[-1] == name for d in self.tree.dirs)
            bases = [""]
        else:
            kind = "glob" if ("*" in tok or "?" in tok) else "path"
            good = any(self.tree.exists(join(b, tok)) for b in bases)
        if not good and any(self.ignored(join(b, tok)) for b in bases):
            self.report.counts["ignored"] += 1
            return
        if not good and self.excused(file, tok):
            self.report.counts["allowlisted"] += 1
            return
        self.report.check(file, unit.line, kind, tok, good, "no such file or directory in the tree")

    def bases(self, tok: str, unit: Unit, docdir: str) -> list[str]:
        if unit.mode == "code" and unit.ctx:
            return [unit.ctx]
        first = tok.split("/", 1)[0]
        if first in self.tree.top or first in (".", ".."):
            return ["", docdir]
        return [docdir, "", *self.tree.package_bases]

    def venv_ref(self, file: str, unit: Unit, tok: str) -> tuple[str, dict[str, Any] | None]:
        """A `.venv` path: the directory holding it must hold a pyproject.toml."""
        owner = tok.split(".venv", 1)[0].rstrip("/")
        base = unit.ctx if unit.mode == "code" else ""
        where = norm(join(base, owner)) if owner else base
        data = None
        if where is not None:
            data = load_toml(self.root / where / "pyproject.toml") if self.tree.is_file(
                join(where, "pyproject.toml")) else None
        self.report.check(file, unit.line, "venv", tok, data is not None,
                          f"{where or '.'}/ holds no pyproject.toml -- no documented environment lives there")
        return where or "", data

    # -- commands --

    def code_line(self, file: str, unit: Unit) -> None:
        line = re.sub(r"(^|\s)#.*$", "", unit.text)
        ctx = unit.ctx
        for seg in re.split(r"\s*(?:&&|\|\||;|\|)\s*", line):
            try:
                words = shlex.split(seg)
            except ValueError:
                words = seg.split()
            while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                words.pop(0)
            if not words:
                continue
            seg_unit = Unit(unit.line, seg, "code", ctx)
            for word in words[1:] if words[0] == "cd" else words:
                self.path_ref(file, seg_unit, word, strict=False)
            ctx = self.command(file, seg_unit, words)

    def command(self, file: str, unit: Unit, words: list[str]) -> str:
        """Check one simple command; return the directory it leaves the line in."""
        head, args = words[0], words[1:]
        ctx = unit.ctx
        if head == "cd":
            target = args[0] if args else ""
            new = norm(join(ctx, target))
            if target and not PLACEHOLDER.search(target):
                self.report.check(file, unit.line, "cd", target, new is not None and self.tree.exists(new),
                                  "cd into a directory that is not in the tree")
            return new if new is not None else ctx
        if "/.venv/bin/" in f"/{head}":
            where, data = self.venv_ref(file, unit, head)
            tool = head.rsplit("/", 1)[-1]
            if data is not None and tool not in ALWAYS_THERE_TOOLS:
                self.report.check(file, unit.line, "venv tool", head, dist_name(tool) in project_dists(data),
                                  f"{where or '.'}/pyproject.toml installs no `{tool}` (not a dependency or extra)")
            if tool in ("python", "python3"):
                self.python(file, unit, args)
            if tool == "pytest":
                self.pytest(file, unit, args)
            return ctx
        if head in ("python", "python3"):
            self.python(file, unit, args)
        elif head in ("bash", "sh"):
            script = next((a for a in args if not a.startswith("-")), None)
            if script and "-c" not in args and not PLACEHOLDER.search(script):
                self.script(file, unit, script)
        elif head == "septacore":
            sub = next((a for a in args if not a.startswith("-")), None)
            if sub:
                self.report.check(file, unit.line, "septacore", f"septacore {sub}", sub in self.septacore,
                                  f"not a septacore subcommand in {CONFIG} (septacore.subcommands)")
        elif head == "pytest":
            self.pytest(file, unit, args)
        elif head == "npm" and args[:1] == ["run"] and len(args) > 1:
            self.npm_run(file, unit, args[1])
        elif self.script_head(head, ctx) and not PLACEHOLDER.search(head):
            self.script(file, unit, head)
        return ctx

    def script_head(self, head: str, ctx: str) -> bool:
        """`scripts/x.py args` or `.gnomon/ci.sh` run as a command. A relative
        `causal_core/contract.py` mentioned in a span is a path, checked as one."""
        return "/" in head and head.endswith((".py", ".sh")) and (
            bool(ctx) or head.split("/", 1)[0] in self.tree.top or head.startswith(("./", "../")))

    def python(self, file: str, unit: Unit, args: list[str]) -> None:
        for a in args:
            if a == "-m" or a == "-c":
                return
            if a.startswith("-"):
                continue
            if a.endswith(".py") and not PLACEHOLDER.search(a):
                self.script(file, unit, a)
            return

    def npm_run(self, file: str, unit: Unit, name: str) -> None:
        pkg = join(unit.ctx, "package.json")
        scripts: dict[str, Any] = {}
        if self.tree.is_file(pkg):
            try:
                scripts = json.loads((self.root / pkg).read_text(encoding="utf-8")).get("scripts", {})
            except json.JSONDecodeError:
                scripts = {}
        self.report.check(file, unit.line, "npm script", f"npm run {name}", name in scripts,
                          f"{pkg} declares no `{name}` script")

    def script(self, file: str, unit: Unit, target: str) -> None:
        rel = norm(join(unit.ctx, target))
        self.report.check(file, unit.line, "script", target, rel is not None and self.tree.is_file(rel),
                          f"no such script{' under ' + unit.ctx + '/' if unit.ctx else ''}")

    def pytest(self, file: str, unit: Unit, args: list[str]) -> None:
        exprs = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-m"]
        exprs += [a[3:] for a in args if a.startswith("-m=")]
        if not exprs:
            return
        # pytest's rootdir: the nearest pyproject.toml at or above where it runs.
        where = unit.ctx
        data = None
        while True:
            if self.tree.is_file(join(where, "pyproject.toml")):
                data = load_toml(self.root / where / "pyproject.toml")
                break
            if not where:
                break
            where = posixpath.dirname(where)
        for expr in exprs:
            for m in marker_names(expr):
                if data is None:
                    self.report.check(file, unit.line, "marker", m, False,
                                      "pytest -m with no package to run in (no pyproject.toml at or above the cd)")
                    continue
                self.report.check(file, unit.line, "marker", m, m in markers(data) | BUILTIN_MARKERS,
                                  f"not declared in {where or '.'}/pyproject.toml [tool.pytest.ini_options] markers")

    # -- prose --

    def prose_line(self, file: str, unit: Unit) -> None:
        text = unit.text
        for m in LINK.finditer(text):
            self.path_ref(file, unit, m.group(1), strict=False, link=True)
        for m in CODE_SPAN.finditer(text):
            span = m.group(1)
            if self.looks_like_command(span):
                self.code_line(file, Unit(unit.line, span, "code", ""))
            else:
                for word in span.split():
                    self.path_ref(file, unit, word, strict=False)
            window = text[max(0, m.start() - 50): m.end() + 50]
            if STEP_NAME.match(span) and STEP_CONTEXT.search(window):
                self.report.check(file, unit.line, "ci step", span,
                                  span in self.steps_all or bool(re.fullmatch(r"pip-audit-packages-\d+", span)),
                                  f"{CI_SH} records no step by this name")
        bare = LINK.sub(" ", CODE_SPAN.sub(" ", text))
        for word in bare.split():
            self.path_ref(file, unit, word, strict=True)
        for g in GATE_ID.findall(bare):
            if self.gate_ids:
                self.report.check(file, unit.line, "gate id", g, g in self.gate_ids,
                                  f"not a gate in {GATE_SKILL}'s gate table")

    def looks_like_command(self, span: str) -> bool:
        words = span.split()
        head = words[0] if words else ""
        # A lone `scripts/x.py` in a sentence is a mention (checked as a path);
        # with arguments it is a command.
        return head in ("python", "python3", "bash", "sh", "septacore", "pytest", "cd", "npm") or (
            "/.venv/bin/" in f"/{head}") or (len(words) > 1 and self.script_head(head, ""))

    def gate_ids_in(self, text: str) -> set[str]:
        """The gates a piece of text names; a range (T2–T8) names every gate inside it."""
        ids = set(GATE_ID.findall(text))
        for m in GATE_RANGE.finditer(text):
            letter, lo, hi = m.group(1), int(m.group(2)), int(m.group(4))
            if m.group(3) == letter:
                ids |= {g for g in self.gate_ids if g[0] == letter and lo <= int(g[1:]) <= hi}
        return ids & set(self.gate_ids)

    def gate_lists(self, file: str, paragraphs: list[tuple[int, str]]) -> None:
        """A paragraph that enumerates three or more gates must name every gate."""
        if not self.gate_ids:
            return
        known = set(self.gate_ids)
        for line, text in paragraphs:
            runs = [m.group(0) for m in GATE_RUN.finditer(text) if len(self.gate_ids_in(m.group(0))) >= 3]
            if not runs:
                continue
            missing = sorted(known - self.gate_ids_in(text))
            self.report.check(file, line, "gate list", "; ".join(runs), not missing,
                              f"an enumeration of gates that leaves out {', '.join(missing)} -- name them all, or none")

    # -- tables --

    def tables(self, file: str, units: list[Unit]) -> list[tuple[int, dict[str, str]]]:
        """Every markdown table row as (line, {header: cell})."""
        rows: list[tuple[int, dict[str, str]]] = []
        header: list[str] | None = None
        for u in units:
            s = u.text.strip()
            if u.mode != "prose" or not s.startswith("|"):
                header = None
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if header is None:
                header = cells
            elif all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                continue
            else:
                rows.append((u.line, dict(zip(header, cells, strict=False))))
        return rows

    def marker_table(self, file: str, rows: list[tuple[int, dict[str, str]]]) -> None:
        for line, row in rows:
            if "Marker" not in row or "Where" not in row:
                continue
            where = norm(row["Where"].strip("` /")) or ""
            data = load_toml(self.root / where / "pyproject.toml") if self.tree.is_file(
                join(where, "pyproject.toml")) else None
            for m in marker_names(row["Marker"].replace("`", "")):
                self.report.check(file, line, "marker", m, data is not None and m in markers(data),
                                  f"not declared in {where}/pyproject.toml [tool.pytest.ini_options] markers")

    def gate_table(self, rows: list[tuple[int, dict[str, str]]]) -> None:
        file = GATE_SKILL
        listed: set[str] = set()
        found = False
        for line, row in rows:
            if "Pre-registration" not in row:
                continue
            found = True
            gate = row.get("Gate", "").split()
            if gate:
                self.gate_ids.append(gate[0])
            cell = row["Pre-registration"].strip("` ")
            m = re.fullmatch(r"benchmarks/([^/]+)/?", cell)
            if not m:
                self.report.check(file, line, "gate", cell, False, "the Pre-registration cell is not benchmarks/<dir>/")
                continue
            listed.add(m.group(1))
            self.report.check(file, line, "gate", cell, self.tree.is_file(f"benchmarks/{m.group(1)}/PRE-REGISTRATION.md"),
                              "no PRE-REGISTRATION.md there")
        if not found:
            self.report.check(file, 0, "gate", "gate table", False, "no table with a Pre-registration column")
            return
        for f in sorted(self.tree.files):
            m = re.fullmatch(r"benchmarks/([^/]+)/PRE-REGISTRATION\.md", f)
            if m:
                self.report.check(file, 0, "gate", f"benchmarks/{m.group(1)}/", m.group(1) in listed,
                                  "a pre-registered gate the skill's gate table does not name")

    # -- front matter --

    def front_matter(self, file: str, text: str, roles: set[str]) -> dict[str, Any] | None:
        lines = text.splitlines()
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "+++"), None) if (
            lines and lines[0].strip() == "+++") else None
        if end is None:
            self.report.check(file, 1, "front matter", file, False, "no +++ front matter block")
            return None
        try:
            fm = tomllib.loads("\n".join(lines[1:end]))
        except tomllib.TOMLDecodeError as exc:
            self.report.check(file, 1, "front matter", file, False, f"not TOML: {exc}")
            return None
        problems = [f"`{k}` missing or not a non-empty string" for k in ("name", "description", "match")
                    if not isinstance(fm.get(k), str) or not fm[k].strip()]
        if not problems:
            try:
                re.compile(fm["match"], re.IGNORECASE)
            except re.error as exc:
                problems.append(f"`match` does not compile: {exc}")
        r = fm.get("roles")
        if r is not None:
            if not isinstance(r, list) or not all(isinstance(x, str) for x in r):
                problems.append("`roles` is not a list of strings")
            else:
                problems += [f"role `{x}` is not declared in .gnomon/roles.toml" for x in r if x not in roles]
        self.report.check(file, 1, "front matter", file, not problems, "; ".join(problems))
        return None if problems else fm

    # -- run --

    def run(self) -> None:
        files: list[str] = []
        for pattern in SURFACES:
            rx = glob_regex(pattern)
            files += sorted(f for f in self.tree.files if rx.fullmatch(f))
        roles_data = load_toml(self.root / ".gnomon/roles.toml") or {}
        roles = set(roles_data.get("roles", {}))
        # The gate table first: gate ids named elsewhere are checked against it.
        if self.tree.is_file(GATE_SKILL):
            units, _ = markdown_units((self.root / GATE_SKILL).read_text(encoding="utf-8"))
            self.gate_table(self.tables(GATE_SKILL, units))
        else:
            self.report.check(GATE_SKILL, 0, "gate", GATE_SKILL, False, "the skill that holds the gate table is missing")
        matches: dict[str, str] = {}
        for file in files:
            text = (self.root / file).read_text(encoding="utf-8")
            self.report.counts["files"] += 1
            if file.endswith(".md"):
                if file.startswith(".gnomon/skills/"):
                    fm = self.front_matter(file, text, roles)
                    if fm:
                        matches[file] = fm["match"]
                units, _ = markdown_units(text)
                self.marker_table(file, self.tables(file, units))
            else:
                units = toml_units(text)
                if file == ".gnomon/policy.toml":
                    cmd = (load_toml(self.root / file) or {}).get("verify", {}).get("command")
                    if isinstance(cmd, str):
                        self.code_line(file, Unit(0, cmd, "code", ""))
            for u in units:
                if u.mode == "code":
                    self.code_line(file, u)
                else:
                    self.prose_line(file, u)
            self.gate_lists(file, paragraphs(units))
        self.match_consistency(matches)
        self.skills_listed(files)
        self.steps_named()
        self.septacore_pin()
        self.stale_allowlist()

    def match_consistency(self, matches: dict[str, str]) -> None:
        for file, pattern in matches.items():
            rx = re.compile(pattern, re.IGNORECASE)
            fires = [g for g in self.gate_ids if rx.search(g.lower())]
            if fires:
                missing = [g for g in self.gate_ids if not rx.search(g.lower())]
                self.report.check(file, 1, "match", "match", not missing,
                                  f"`match` fires on {fires[0].lower()} but not on {', '.join(g.lower() for g in missing)}")

    def skills_listed(self, files: list[str]) -> None:
        """AGENTS.md lists the skills; a new skill cannot be missing from it."""
        if not self.tree.is_file("AGENTS.md"):
            return
        spans = set(CODE_SPAN.findall((self.root / "AGENTS.md").read_text(encoding="utf-8")))
        for f in files:
            if f.startswith(".gnomon/skills/") and f.endswith(".md"):
                name = f.rsplit("/", 1)[-1]
                self.report.check("AGENTS.md", 0, "skill listed", name, name in spans,
                                  "a skill AGENTS.md's list of skills does not name")

    def steps_named(self) -> None:
        path = self.root / GATE_SKILL
        if not path.is_file():
            return
        spans = set(CODE_SPAN.findall(path.read_text(encoding="utf-8")))
        if not self.steps_literal:
            self.report.check(CI_SH, 0, "ci step", CI_SH, False, "no step names found in ci.sh")
        for step in sorted(self.steps_literal):
            self.report.check(GATE_SKILL, 0, "ci step", step, step in spans,
                              f"a step {CI_SH} records that the skill never names (in a code span)")

    def septacore_pin(self) -> None:
        if not self.septacore:
            self.report.check(CONFIG, 0, "septacore", "septacore.subcommands", False, "the pinned list is empty")
            return
        if self.septacore_live is None:
            self.report.notes.append("septacore not available here: subcommands checked against the pin only")
            return
        for sub in sorted(self.septacore - self.septacore_live):
            self.report.check(CONFIG, 0, "septacore pin", sub, False,
                              "pinned, but `septacore --help` no longer lists it")
        extra = sorted(self.septacore_live - self.septacore)
        if extra:
            self.report.notes.append(f"septacore lists subcommand(s) the pin lacks: {', '.join(extra)}")

    def stale_allowlist(self) -> None:
        for i, e in enumerate(self.not_paths):
            refs = entry_refs(e)
            if not refs or not all(isinstance(e.get(k), str) and e[k].strip() for k in ("in", "reason")):
                self.report.check(CONFIG, 0, "allowlist", str(refs or e), False,
                                  "an entry needs ref (or refs), in and a reason")
                continue
            for r in refs:
                if (i, r) not in self.used_not_paths:
                    self.report.check(CONFIG, 0, "allowlist", r, False,
                                      f"stale: {e['in']} no longer names it as a failing path -- remove it")


def entry_refs(entry: dict[str, Any]) -> list[str]:
    refs = entry.get("refs", [entry["ref"]] if "ref" in entry else [])
    return [r for r in refs if isinstance(r, str) and r] if isinstance(refs, list) else []


def paragraphs(units: list[Unit]) -> list[tuple[int, str]]:
    """Consecutive prose lines as (first line, text without code spans or links).
    A table row is a paragraph of its own."""
    out: list[tuple[int, str]] = []
    cur: list[str] = []
    start = 0
    for u in [*units, Unit(0, "", "code")]:
        s = u.text.strip()
        row = s.startswith("|")
        if u.mode != "prose" or not s or row:
            if cur:
                out.append((start, " ".join(cur)))
                cur = []
            if u.mode == "prose" and row:
                out.append((u.line, LINK.sub(" ", CODE_SPAN.sub(" ", s))))
            continue
        if not cur:
            start = u.line
        cur.append(LINK.sub(" ", CODE_SPAN.sub(" ", s)))
    return out


def septacore_subcommands(binary: str) -> set[str]:
    proc = subprocess.run([binary, "--help"], capture_output=True, text=True, check=False, timeout=60)
    m = re.search(r"\{([a-z0-9_,-]+)\}", proc.stdout)
    if proc.returncode != 0 or not m:
        raise RuntimeError(f"`{binary} --help` exited {proc.returncode} without a subcommand list")
    return set(m.group(1).split(","))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--config", type=Path, default=None, help=f"default: <root>/{CONFIG}")
    ap.add_argument("--septacore", default=None,
                    help="septacore binary to cross-check the pin against; '' to skip (default: PATH)")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    config_path = args.config or root / CONFIG
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        bad(f"cannot read {config_path}: {exc}")
        return 1
    binary = shutil.which("septacore") if args.septacore is None else (args.septacore or None)
    live = None
    if binary:
        try:
            live = septacore_subcommands(binary)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            bad(f"septacore is present but its subcommands could not be read: {exc}")
            return 1
    try:
        checker = Checker(root, config, live)
        checker.run()
    except RuntimeError as exc:
        bad(str(exc))
        return 1
    rep = checker.report
    for f in rep.findings:
        bad(f"{f.file}:{f.line}: {f.kind} `{f.ref}` -- {f.why}")
    for n in rep.notes:
        warn(n)
    c = rep.counts
    kinds = [k for k in c if k not in ("files", "allowlisted", "ignored")]
    total = sum(c[k] for k in kinds)
    detail = ", ".join(f"{c[k]} {k}" for k in sorted(kinds, key=lambda k: -c[k]))
    summary = (f"skill-refs: {total} references checked in {c['files']} files ({detail}); "
               f"{c['allowlisted']} allowlisted, {c['ignored']} git-ignored and "
               f"{rep.placeholders} placeholders not checked")
    if c["files"] == 0 or total == 0:
        bad(f"{summary} -- nothing checked, which is not a pass")
        return 1
    if rep.findings:
        bad(f"{summary} -- {len(rep.findings)} did not resolve")
        return 1
    ok(summary)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
