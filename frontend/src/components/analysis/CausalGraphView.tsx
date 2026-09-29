"use client";

/**
 * CausalGraphView — a causal graph drawn left to right, causes before effects.
 *
 * Nodes are placed in layers by their longest path from a root, so every
 * arrow points rightwards, and the treatment and the outcome sit on the middle
 * row. Roles are shown by colour *and* by a text tag, never by colour alone:
 * treatment, outcome, "adjust" (the adjustment set the identification chose)
 * and "unmeasured" (no data column, or declared latent; dashed border).
 * Bidirected edges (X <-> Y, an unmeasured common cause) are dashed arcs;
 * unreviewed edges are dashed arrows.
 */

import { useMemo } from "react";
import type { GraphEdge } from "@/lib/analysis";

export interface CausalGraphViewProps {
  nodes: string[];
  edges: GraphEdge[];
  treatment?: string;
  outcome?: string;
  adjustmentSet?: string[];
  unmeasured?: string[];
  className?: string;
}

const NODE_H = 34;
const COL_GAP = 70;
const ROW_GAP = 22;
const PAD = 16;

function nodeWidth(label: string): number {
  return Math.max(84, Math.min(200, label.length * 7.4 + 26));
}

function layers(nodes: string[], edges: GraphEdge[]): Map<string, number> {
  const directed = edges.filter((e) => e.kind === "directed");
  const parents = new Map<string, string[]>(nodes.map((n) => [n, []]));
  for (const e of directed) parents.get(e.target)?.push(e.source);
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const visit = (n: string): number => {
    const known = depth.get(n);
    if (known !== undefined) return known;
    if (visiting.has(n)) return 0; // a cycle: the check reports it; draw anyway
    visiting.add(n);
    const d = Math.max(-1, ...(parents.get(n) ?? []).map(visit)) + 1;
    visiting.delete(n);
    depth.set(n, d);
    return d;
  };
  nodes.forEach(visit);
  return depth;
}

export function CausalGraphView({
  nodes,
  edges,
  treatment,
  outcome,
  adjustmentSet = [],
  unmeasured = [],
  className,
}: CausalGraphViewProps) {
  const layout = useMemo(() => {
    const depth = layers(nodes, edges);
    const cols = new Map<number, string[]>();
    for (const n of nodes) {
      const d = depth.get(n) ?? 0;
      cols.set(d, [...(cols.get(d) ?? []), n]);
    }
    const colIdx = [...cols.keys()].sort((a, b) => a - b);
    const colWidth = colIdx.map((c) => Math.max(...(cols.get(c) ?? []).map(nodeWidth)));
    const maxRows = Math.max(1, ...[...cols.values()].map((c) => c.length));
    const height = PAD * 2 + maxRows * NODE_H + (maxRows - 1) * ROW_GAP;
    const pos = new Map<string, { x: number; y: number; w: number }>();
    let x = PAD;
    colIdx.forEach((c, i) => {
      // The treatment and the outcome go to the middle row of their column;
      // everything else around them, in name order.
      const members = [...(cols.get(c) ?? [])].sort((a, b) => a.localeCompare(b));
      const focus = members.filter((m) => m === treatment || m === outcome);
      const rest = members.filter((m) => m !== treatment && m !== outcome);
      const half = Math.floor(rest.length / 2);
      const ordered = [...rest.slice(0, half), ...focus, ...rest.slice(half)];
      const colHeight = ordered.length * NODE_H + (ordered.length - 1) * ROW_GAP;
      const top = (height - colHeight) / 2;
      ordered.forEach((n, r) => {
        const w = nodeWidth(n);
        pos.set(n, { x: x + ((colWidth[i] ?? w) - w) / 2, y: top + r * (NODE_H + ROW_GAP), w });
      });
      x += (colWidth[i] ?? 0) + COL_GAP;
    });
    return { pos, width: Math.max(x - COL_GAP + PAD, 200), height };
  }, [nodes, edges, treatment, outcome]);

  const adjust = new Set(adjustmentSet);
  const hidden = new Set(unmeasured);

  const role = (n: string) =>
    n === treatment
      ? { tag: "treatment", cls: "fill-teal-50 stroke-teal-600 dark:fill-teal-950" }
      : n === outcome
        ? { tag: "outcome", cls: "fill-sky-50 stroke-sky-600 dark:fill-sky-950" }
        : hidden.has(n)
          ? { tag: "unmeasured", cls: "fill-zinc-50 stroke-zinc-400 dark:fill-zinc-900" }
          : adjust.has(n)
            ? { tag: "adjust", cls: "fill-amber-50 stroke-amber-500 dark:fill-amber-950" }
            : { tag: "", cls: "fill-background stroke-zinc-400 dark:stroke-zinc-500" };

  if (nodes.length === 0) {
    return (
      <div className={className}>
        <p className="p-6 text-center text-sm text-muted-foreground">
          The graph will appear here as you describe it.
        </p>
      </div>
    );
  }

  const { pos, width, height } = layout;
  return (
    <div className={className}>
      <svg
        viewBox={`0 0 ${width} ${height + 12}`}
        className="h-auto w-full max-w-full"
        style={{ maxHeight: 420 }}
        role="img"
        aria-label={`Causal graph: ${edges
          .map((e) => `${e.source} ${e.kind === "directed" ? "causes" : "shares a hidden cause with"} ${e.target}`)
          .join("; ")}`}
      >
        <defs>
          <marker id="cg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-zinc-500 dark:fill-zinc-400" />
          </marker>
          <marker id="cg-arrow-focus" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-teal-600" />
          </marker>
        </defs>
        {edges.map((e, i) => {
          const s = pos.get(e.source);
          const t = pos.get(e.target);
          if (!s || !t) return null;
          const unreviewed = e.status === "proposed";
          if (e.kind === "bidirected") {
            const x1 = s.x + s.w / 2;
            const x2 = t.x + t.w / 2;
            const y1 = s.y;
            const y2 = t.y;
            const lift = Math.max(28, Math.abs(x2 - x1) / 4);
            return (
              <path
                key={i}
                d={`M ${x1} ${y1} C ${x1} ${y1 - lift}, ${x2} ${y2 - lift}, ${x2} ${y2}`}
                className="fill-none stroke-rose-500"
                strokeWidth={1.5}
                strokeDasharray="5 4"
                markerStart="url(#cg-arrow)"
                markerEnd="url(#cg-arrow)"
              >
                <title>{`${e.source} <-> ${e.target}: an unmeasured common cause`}</title>
              </path>
            );
          }
          const focus = e.source === treatment && e.target === outcome;
          const back = t.x <= s.x;
          const x1 = back ? s.x : s.x + s.w;
          const y1 = s.y + NODE_H / 2;
          const x2 = back ? t.x + t.w : t.x;
          const y2 = t.y + NODE_H / 2;
          const mx = (x1 + x2) / 2;
          return (
            <path
              key={i}
              d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2 - 2} ${y2}`}
              className={focus ? "fill-none stroke-teal-600" : "fill-none stroke-zinc-400 dark:stroke-zinc-500"}
              strokeWidth={focus ? 2.2 : 1.4}
              strokeDasharray={unreviewed ? "4 4" : undefined}
              markerEnd={focus ? "url(#cg-arrow-focus)" : "url(#cg-arrow)"}
            >
              <title>{`${e.source} -> ${e.target}${unreviewed ? " (proposed, not yet reviewed)" : ""}`}</title>
            </path>
          );
        })}
        {[...pos.entries()].map(([n, p]) => {
          const r = role(n);
          return (
            <g key={n}>
              <rect
                x={p.x}
                y={p.y}
                width={p.w}
                height={NODE_H}
                rx={8}
                className={r.cls}
                strokeWidth={n === treatment || n === outcome ? 2 : 1.3}
                strokeDasharray={hidden.has(n) ? "4 3" : undefined}
              />
              <text
                x={p.x + p.w / 2}
                y={p.y + (r.tag ? 14 : NODE_H / 2 + 4)}
                textAnchor="middle"
                className="fill-foreground text-[12px] font-medium"
              >
                {n}
              </text>
              {r.tag && (
                <text
                  x={p.x + p.w / 2}
                  y={p.y + 27}
                  textAnchor="middle"
                  className="fill-muted-foreground text-[9.5px] uppercase tracking-wide"
                >
                  {r.tag}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
