"""
Graphs: a small text syntax, and the ADMG operations identification needs.

Syntax (DOT-compatible for the simple cases)::

    Z -> X -> Y          # a chain of directed edges
    X <- W               # reverse arrow
    X <-> Y              # unmeasured common cause of X and Y
    latent U             # U is never observed
    U -> X; U -> Y
    M                    # an isolated node
    X -> Y [origin=llm_prior]   # edge attributes: origin, status

Statements are separated by newlines or ``;``. A ``digraph { ... }`` wrapper
is ignored. Edges with ``origin=llm_prior`` default to ``status=proposed``.

Internally every graph is held two ways:

* ``dag``   -- a DAG over observed *and* latent nodes, where each bidirected
               edge ``a <-> b`` becomes a fresh latent parent of ``a`` and ``b``.
               d-separation is computed here, so latent nodes are never
               conditioned on by accident.
* ``admg``  -- the latent projection onto observed nodes (directed + bidirected
               edges), which is what the ID algorithm works on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

import networkx as nx

from causal_core.contract import CausalGraph, Edge, EdgeOrigin, EdgeStatus

_ARROW = re.compile(r"\s*(<->|->|<-)\s*")
_ATTRS = re.compile(r"\[([^\]]*)\]\s*$")
_NAME = re.compile(r"^[A-Za-z_][\w.:\-]*$|^\"[^\"]+\"$")


class GraphSyntaxError(ValueError):
    pass


def _clean_name(raw: str, stmt: str) -> str:
    name = raw.strip()
    if not _NAME.match(name):
        raise GraphSyntaxError(f"invalid node name {name!r} in statement {stmt!r}")
    return name.strip('"')


def parse_graph(text: str) -> CausalGraph:
    """Parse the text syntax above into a :class:`CausalGraph`."""
    body = text.strip()
    m = re.match(r"^(?:strict\s+)?(?:di)?graph\s*[\w\"]*\s*\{(.*)\}\s*$", body, re.S)
    if m:
        body = m.group(1)
    nodes: dict[str, None] = {}
    latent: dict[str, None] = {}
    edges: list[Edge] = []
    for raw_stmt in re.split(r"[;\n]", body):
        stmt = raw_stmt.split("#", 1)[0].strip()
        if not stmt:
            continue
        if stmt.startswith("latent "):
            for name in re.split(r"[,\s]+", stmt[len("latent ") :].strip()):
                if name:
                    n = _clean_name(name, stmt)
                    latent[n] = None
                    nodes[n] = None
            continue
        attrs: dict[str, str] = {}
        am = _ATTRS.search(stmt)
        if am:
            stmt_body = stmt[: am.start()]
            for kv in am.group(1).split(","):
                if not kv.strip():
                    continue
                if "=" not in kv:
                    raise GraphSyntaxError(f"edge attribute must be key=value: {kv!r}")
                k, v = (s.strip() for s in kv.split("=", 1))
                attrs[k] = v
        else:
            stmt_body = stmt
        parts = _ARROW.split(stmt_body)
        names = [_clean_name(p, stmt) for p in parts[0::2]]
        arrows = parts[1::2]
        for n in names:
            nodes[n] = None
        for (a, b), arrow in zip(zip(names, names[1:], strict=False), arrows, strict=True):
            origin = EdgeOrigin(attrs.get("origin", "human"))
            default_status = "proposed" if origin is EdgeOrigin.llm_prior else "asserted"
            status = EdgeStatus(attrs.get("status", default_status))
            if arrow == "->":
                edges.append(Edge(source=a, target=b, origin=origin, status=status))
            elif arrow == "<-":
                edges.append(Edge(source=b, target=a, origin=origin, status=status))
            else:
                edges.append(
                    Edge(source=a, target=b, kind="bidirected", origin=origin, status=status)
                )
    return CausalGraph(nodes=tuple(nodes), edges=tuple(edges), latent=tuple(latent))


def as_causal_graph(graph: CausalGraph | str | Any) -> CausalGraph:
    """Accept a CausalGraph, the text syntax, a networkx DiGraph, or ``[(a, b), ...]``."""
    if isinstance(graph, CausalGraph):
        return graph
    if isinstance(graph, str):
        return parse_graph(graph)
    if isinstance(graph, nx.DiGraph):
        return CausalGraph(
            nodes=tuple(str(n) for n in graph.nodes),
            edges=tuple(Edge(source=str(a), target=str(b)) for a, b in graph.edges),
            latent=tuple(str(n) for n, d in graph.nodes(data=True) if d.get("latent")),
        )
    if isinstance(graph, Mapping):
        return CausalGraph.model_validate(graph)
    if isinstance(graph, Sequence):
        edges = tuple(Edge(source=str(a), target=str(b)) for a, b in graph)
        seen: dict[str, None] = {}
        for e in edges:
            seen[e.source] = None
            seen[e.target] = None
        return CausalGraph(nodes=tuple(seen), edges=edges)
    raise TypeError(f"cannot interpret {type(graph).__name__} as a causal graph")


def _bidirected_latent(a: str, b: str) -> str:
    x, y = sorted((a, b))
    return f"⟨U:{x},{y}⟩"


@dataclass(frozen=True)
class Graph:
    """An analysis-ready view of a :class:`CausalGraph`."""

    spec: CausalGraph
    observed: frozenset[str]
    latent: frozenset[str]
    dag: nx.DiGraph = field(compare=False)

    @classmethod
    def from_spec(cls, spec: CausalGraph) -> Graph:
        latent = set(spec.latent)
        dag = nx.DiGraph()
        dag.add_nodes_from(spec.nodes)
        for e in spec.edges:
            if e.status is EdgeStatus.refuted:
                continue
            for n in (e.source, e.target):
                if n not in dag:
                    dag.add_node(n)
            if e.source == e.target:
                raise GraphSyntaxError(f"self-loop on {e.source!r}")
            if e.kind == "directed":
                dag.add_edge(e.source, e.target)
            else:
                u = _bidirected_latent(e.source, e.target)
                latent.add(u)
                dag.add_edge(u, e.source)
                dag.add_edge(u, e.target)
        if not nx.is_directed_acyclic_graph(dag):
            cycle = nx.find_cycle(dag)
            raise GraphSyntaxError(f"graph has a directed cycle: {cycle}")
        observed = frozenset(n for n in dag.nodes if n not in latent)
        return cls(spec=spec, observed=observed, latent=frozenset(latent), dag=dag)

    # ── basic relations on the latent-augmented DAG ──────────────────────────

    def parents(self, nodes: Iterable[str]) -> set[str]:
        return {p for n in nodes for p in self.dag.predecessors(n)}

    def children(self, nodes: Iterable[str]) -> set[str]:
        return {c for n in nodes for c in self.dag.successors(n)}

    def ancestors(self, nodes: Iterable[str], dag: nx.DiGraph | None = None) -> set[str]:
        g = self.dag if dag is None else dag
        out = set(nodes)
        for n in list(out):
            out |= nx.ancestors(g, n)
        return out

    def descendants(self, nodes: Iterable[str], dag: nx.DiGraph | None = None) -> set[str]:
        g = self.dag if dag is None else dag
        out = set(nodes)
        for n in list(out):
            out |= nx.descendants(g, n)
        return out

    def d_separated(
        self,
        xs: Iterable[str],
        ys: Iterable[str],
        zs: Iterable[str] = (),
        dag: nx.DiGraph | None = None,
    ) -> bool:
        g = self.dag if dag is None else dag
        x, y, z = set(xs), set(ys), set(zs)
        if x & y:
            return False
        return bool(nx.is_d_separator(g, x, y, z))

    def without_incoming(self, nodes: Iterable[str]) -> nx.DiGraph:
        g = self.dag.copy()
        g.remove_edges_from([(p, n) for n in nodes for p in list(self.dag.predecessors(n))])
        return g

    def without_outgoing(self, nodes: Iterable[str]) -> nx.DiGraph:
        g = self.dag.copy()
        g.remove_edges_from([(n, c) for n in nodes for c in list(self.dag.successors(n))])
        return g

    # ── adjustment-criterion sets (Perković et al. 2018) ─────────────────────

    def causal_nodes(self, xs: Iterable[str], ys: Iterable[str]) -> set[str]:
        """cn(X, Y): nodes other than X on proper causal paths from X to Y."""
        x = set(xs)
        de = self.descendants(x, self.without_incoming(x)) - x
        an = self.ancestors(set(ys), self.without_outgoing(x))
        return de & an

    def forbidden(self, xs: Iterable[str], ys: Iterable[str]) -> set[str]:
        """Forb(X, Y): descendants of causal nodes, plus X. Never adjust for these."""
        x = set(xs)
        return self.descendants(self.causal_nodes(x, ys)) | x

    def proper_backdoor_dag(self, xs: Iterable[str], ys: Iterable[str]) -> nx.DiGraph:
        """The DAG with the first edge of every proper causal path from X to Y removed."""
        x = set(xs)
        cn = self.causal_nodes(x, ys)
        g = self.dag.copy()
        g.remove_edges_from([(a, b) for a in x for b in list(self.dag.successors(a)) if b in cn])
        return g

    # ── latent projection ─────────────────────────────────────────────────────

    @cached_property
    def admg(self) -> tuple[nx.DiGraph, set[frozenset[str]]]:
        """Latent projection onto observed nodes: (directed graph, bidirected pairs)."""
        directed = nx.DiGraph()
        directed.add_nodes_from(sorted(self.observed))
        bidirected: set[frozenset[str]] = set()
        # a -> b if a directed path a -> ... -> b exists with only latent intermediates.
        for a in self.observed:
            stack = list(self.dag.successors(a))
            seen: set[str] = set()
            while stack:
                n = stack.pop()
                if n in seen:
                    continue
                seen.add(n)
                if n in self.observed:
                    directed.add_edge(a, n)
                else:
                    stack.extend(self.dag.successors(n))
        # a <-> b if a latent node reaches both through latent-only directed paths.
        for u in self.latent:
            reach: set[str] = set()
            stack = list(self.dag.successors(u))
            seen = set()
            while stack:
                n = stack.pop()
                if n in seen:
                    continue
                seen.add(n)
                if n in self.observed:
                    reach.add(n)
                else:
                    stack.extend(self.dag.successors(n))
            ordered = sorted(reach)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    bidirected.add(frozenset((a, b)))
        return directed, bidirected

    def edge_label(self, e: Edge) -> str:
        arrow = "<->" if e.kind == "bidirected" else "->"
        return f"{e.source} {arrow} {e.target}"

    def unreviewed_edges(self) -> list[str]:
        return [
            self.edge_label(e)
            for e in self.spec.edges
            if e.status is EdgeStatus.proposed
        ]
