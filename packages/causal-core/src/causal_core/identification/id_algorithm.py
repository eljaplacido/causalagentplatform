"""
The ID algorithm (Shpitser & Pearl, 2006) for P(y | do(x)) in an ADMG.

ID is *complete*: if it fails, no nonparametric identification of the effect
exists from the observational distribution over the observed variables, and the
failure carries a hedge -- a pair of c-forests that witnesses it. Every simpler
strategy in :mod:`causal_core.identification` (back-door, front-door) is checked
against this algorithm, so a disagreement surfaces as an internal error instead
of a wrong certificate.

Expressions are symbolic and only for reading; estimation of the general ID
functional is not implemented (see ``declared_not_implemented``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

import networkx as nx

# ── expressions ───────────────────────────────────────────────────────────────


class Expr:
    def render(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


def _fmt(vs: Iterable[str]) -> str:
    return ", ".join(sorted(vs))


@dataclass(frozen=True)
class Prob(Expr):
    """An observational (conditional) probability P(vars | given)."""

    vars: frozenset[str]
    given: frozenset[str] = frozenset()

    def render(self) -> str:
        if self.given:
            return f"P({_fmt(self.vars)} | {_fmt(self.given)})"
        return f"P({_fmt(self.vars)})"


@dataclass(frozen=True)
class Sum(Expr):
    over: frozenset[str]
    expr: Expr

    def render(self) -> str:
        inner = self.expr.render()
        if not self.over:
            return inner
        return f"Σ_{{{_fmt(self.over)}}} [{inner}]"


@dataclass(frozen=True)
class Product(Expr):
    factors: tuple[Expr, ...]

    def render(self) -> str:
        if not self.factors:
            return "1"
        return " ".join(f.render() for f in self.factors)


@dataclass(frozen=True)
class Frac(Expr):
    num: Expr
    den: Expr

    def render(self) -> str:
        return f"({self.num.render()}) / ({self.den.render()})"


def _sum(over: Iterable[str], expr: Expr) -> Expr:
    o = frozenset(over)
    return Sum(o, expr) if o else expr


def _product(factors: list[Expr]) -> Expr:
    return factors[0] if len(factors) == 1 else Product(tuple(factors))


# ── graph view used by the recursion ─────────────────────────────────────────


@dataclass(frozen=True)
class ADMG:
    nodes: frozenset[str]
    parents: Mapping[str, frozenset[str]]
    bidirected: frozenset[frozenset[str]]

    @classmethod
    def from_edges(
        cls,
        nodes: Iterable[str],
        directed: Iterable[tuple[str, str]],
        bidirected: Iterable[Iterable[str]],
    ) -> ADMG:
        ns = frozenset(nodes)
        par: dict[str, set[str]] = {n: set() for n in ns}
        for a, b in directed:
            par[b].add(a)
        bi = frozenset(frozenset(p) for p in bidirected)
        return cls(ns, {k: frozenset(v) for k, v in par.items()}, bi)

    def sub(self, keep: Iterable[str]) -> ADMG:
        k = frozenset(keep)
        return ADMG(
            k,
            {n: self.parents[n] & k for n in k},
            frozenset(p for p in self.bidirected if p <= k),
        )

    def ancestors(self, ys: Iterable[str], cut_incoming: frozenset[str] = frozenset()) -> set[str]:
        out = set(ys)
        stack = list(out)
        while stack:
            n = stack.pop()
            if n in cut_incoming:
                continue
            for p in self.parents[n]:
                if p not in out:
                    out.add(p)
                    stack.append(p)
        return out

    def c_components(self) -> list[frozenset[str]]:
        g = nx.Graph()
        g.add_nodes_from(self.nodes)
        for p in self.bidirected:
            a, b = tuple(p)
            g.add_edge(a, b)
        return [frozenset(c) for c in nx.connected_components(g)]


# ── distributions carried through the recursion ──────────────────────────────


@dataclass(frozen=True)
class _Dist:
    """A distribution over ``vars`` (other variables are held fixed).

    ``joint``  -- the observational distribution P(vars);
    ``chain``  -- a product of factors in topological order, one per variable,
                  each the conditional of that variable given its predecessors;
    ``general``-- any other expression.
    """

    kind: Literal["joint", "chain", "general"]
    vars: frozenset[str]
    expr: Expr
    factors: Mapping[str, Expr] | None = None


def _marginal(d: _Dist, keep: frozenset[str]) -> _Dist:
    if keep == d.vars:
        return d
    if d.kind == "joint":
        return _Dist("joint", keep, Prob(keep))
    return _Dist("general", keep, _sum(d.vars - keep, d.expr))


def _conditional(d: _Dist, v: str, preds: frozenset[str]) -> Expr:
    """P_d(v | preds), where ``preds`` are v's predecessors within ``d.vars``."""
    if d.kind == "joint":
        return Prob(frozenset({v}), preds)
    if d.kind == "chain" and d.factors is not None:
        return d.factors[v]
    num = _sum(d.vars - preds - {v}, d.expr)
    den = _sum(d.vars - preds, d.expr)
    return Frac(num, den)


class Hedge(Exception):
    """ID failed. ``f`` and ``f_prime`` are the node sets of the witnessing hedge."""

    def __init__(self, f: frozenset[str], f_prime: frozenset[str]) -> None:
        super().__init__(f"hedge F={sorted(f)} F'={sorted(f_prime)}")
        self.f = f
        self.f_prime = f_prime


def _id(
    y: frozenset[str], x: frozenset[str], p: _Dist, g: ADMG, order: list[str]
) -> Expr:
    v = g.nodes
    pi = [n for n in order if n in v]

    # 1. No intervention left: marginalize.
    if not x:
        return _marginal(p, y).expr

    # 2. Drop everything that is not an ancestor of y.
    an_y = frozenset(g.ancestors(y))
    if an_y != v:
        return _id(y, x & an_y, _marginal(p, an_y), g.sub(an_y), order)

    # 3. Intervene on variables that cannot affect y once x is fixed.
    w = (v - x) - frozenset(g.ancestors(y, cut_incoming=x))
    if w:
        return _id(y, x | w, p, g, order)

    comps_minus_x = g.sub(v - x).c_components()
    # 4. Several c-components: factorize.
    if len(comps_minus_x) > 1:
        factors = [_id(s, v - s, p, g, order) for s in comps_minus_x]
        return _sum(v - (y | x), _product(factors))

    (s,) = comps_minus_x
    comps = g.c_components()
    # 5. G is a single c-component: hedge.
    if len(comps) == 1 and comps[0] == v:
        raise Hedge(v, s)
    # 6. S is itself a c-component of G.
    if s in comps:
        factors = [_conditional(p, vi, frozenset(pi[: pi.index(vi)])) for vi in pi if vi in s]
        return _sum(s - y, _product(factors))
    # 7. S sits inside a larger c-component S'.
    s_prime = next(c for c in comps if s < c)
    chain: dict[str, Expr] = {}
    for vi in pi:
        if vi in s_prime:
            chain[vi] = _conditional(p, vi, frozenset(pi[: pi.index(vi)]))
    ordered = [chain[vi] for vi in pi if vi in s_prime]
    new_p = _Dist("chain", s_prime, _product(ordered), chain)
    return _id(y, x & s_prime, new_p, g.sub(s_prime), order)


@dataclass(frozen=True)
class IDResult:
    identified: bool
    expression: str
    hedge: tuple[frozenset[str], frozenset[str]] | None = None


def identify_id(
    g: ADMG, treatment: Iterable[str], outcome: Iterable[str]
) -> IDResult:
    """Run ID for P(outcome | do(treatment)) on the ADMG ``g``."""
    x = frozenset(treatment)
    y = frozenset(outcome)
    if x & y:
        raise ValueError("treatment and outcome must be disjoint")
    missing = (x | y) - g.nodes
    if missing:
        raise ValueError(f"not observed in the graph: {sorted(missing)}")
    dag = nx.DiGraph()
    dag.add_nodes_from(g.nodes)
    dag.add_edges_from((p, n) for n in g.nodes for p in g.parents[n])
    order = list(nx.lexicographical_topological_sort(dag))
    try:
        expr = _id(y, x, _Dist("joint", g.nodes, Prob(g.nodes)), g, order)
    except Hedge as h:
        return IDResult(False, f"FAIL: {h}", (h.f, h.f_prime))
    return IDResult(True, expr.render())
