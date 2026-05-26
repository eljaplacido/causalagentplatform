"use client";

/**
 * GraphDiff — visual diff between two versions of a causal graph.
 *
 * Shows:
 *   - Added nodes (green)
 *   - Removed nodes (red strikethrough)
 *   - Added edges (green dashed)
 *   - Removed edges (red dashed)
 *   - Modified edges (amber — confidence or type changed)
 *   - Summary counts at top
 *
 * Used in the governance review flow to compare proposed vs. approved graph.
 */

import { Plus, Minus, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DAGNode, DAGEdge } from "./DAGCanvas";

// ── Types ─────────────────────────────────────────────────────────────────────

export type DiffStatus = "added" | "removed" | "unchanged" | "modified";

export interface DiffNode extends DAGNode {
  diffStatus: DiffStatus;
}

export interface DiffEdge extends DAGEdge {
  diffStatus: DiffStatus;
  previousConfidence?: number;
}

export interface GraphDiffProps {
  /** Nodes with diffStatus computed by the parent */
  nodes: DiffNode[];
  /** Edges with diffStatus computed by the parent */
  edges: DiffEdge[];
  /** Label for the "before" state */
  fromLabel?: string;
  /** Label for the "after" state */
  toLabel?: string;
  className?: string;
}

// ── Diff computation helper (exported for testing) ────────────────────────────

export function computeGraphDiff(
  fromNodes: DAGNode[],
  fromEdges: DAGEdge[],
  toNodes: DAGNode[],
  toEdges: DAGEdge[]
): { nodes: DiffNode[]; edges: DiffEdge[] } {
  const fromNodeIds = new Set(fromNodes.map((n) => n.id));
  const toNodeIds = new Set(toNodes.map((n) => n.id));

  const diffNodes: DiffNode[] = [
    ...toNodes.map((n) => ({
      ...n,
      diffStatus: (fromNodeIds.has(n.id) ? "unchanged" : "added") as DiffStatus,
    })),
    ...fromNodes
      .filter((n) => !toNodeIds.has(n.id))
      .map((n) => ({ ...n, diffStatus: "removed" as DiffStatus })),
  ];

  const fromEdgeMap = new Map(fromEdges.map((e) => [e.id, e]));
  const toEdgeMap = new Map(toEdges.map((e) => [e.id, e]));

  const diffEdges: DiffEdge[] = [
    ...toEdges.map((e) => {
      const prev = fromEdgeMap.get(e.id);
      if (!prev) return { ...e, diffStatus: "added" as DiffStatus };
      if (prev.confidence !== e.confidence || prev.edgeType !== e.edgeType) {
        return {
          ...e,
          diffStatus: "modified" as DiffStatus,
          previousConfidence: prev.confidence,
        };
      }
      return { ...e, diffStatus: "unchanged" as DiffStatus };
    }),
    ...fromEdges
      .filter((e) => !toEdgeMap.has(e.id))
      .map((e) => ({ ...e, diffStatus: "removed" as DiffStatus })),
  ];

  return { nodes: diffNodes, edges: diffEdges };
}

// ── Status config ─────────────────────────────────────────────────────────────

const DIFF_ICON: Record<DiffStatus, typeof Plus | null> = {
  added: Plus,
  removed: Minus,
  modified: RefreshCw,
  unchanged: null,
};

const DIFF_COLOURS: Record<
  DiffStatus,
  { row: string; icon: string; label: string }
> = {
  added: {
    row: "bg-green-50 dark:bg-green-950 border-l-2 border-green-500",
    icon: "text-green-600 dark:text-green-400",
    label: "Added",
  },
  removed: {
    row: "bg-red-50 dark:bg-red-950 border-l-2 border-red-500 opacity-75",
    icon: "text-red-600 dark:text-red-400",
    label: "Removed",
  },
  modified: {
    row: "bg-amber-50 dark:bg-amber-950 border-l-2 border-amber-500",
    icon: "text-amber-600 dark:text-amber-400",
    label: "Modified",
  },
  unchanged: {
    row: "",
    icon: "text-zinc-400",
    label: "Unchanged",
  },
};

// ── Component ─────────────────────────────────────────────────────────────────

export function GraphDiff({
  nodes,
  edges,
  fromLabel = "Previous",
  toLabel = "Proposed",
  className,
}: GraphDiffProps) {
  const addedNodes = nodes.filter((n) => n.diffStatus === "added");
  const removedNodes = nodes.filter((n) => n.diffStatus === "removed");
  const addedEdges = edges.filter((e) => e.diffStatus === "added");
  const removedEdges = edges.filter((e) => e.diffStatus === "removed");
  const modifiedEdges = edges.filter((e) => e.diffStatus === "modified");

  const hasChanges =
    addedNodes.length > 0 ||
    removedNodes.length > 0 ||
    addedEdges.length > 0 ||
    removedEdges.length > 0 ||
    modifiedEdges.length > 0;

  return (
    <section
      data-testid="graph-diff"
      className={cn("space-y-4", className)}
      aria-label="Graph diff"
    >
      {/* Summary header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h3 className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">
          Graph Diff
          <span className="ml-2 text-xs font-normal text-zinc-400">
            {fromLabel} → {toLabel}
          </span>
        </h3>
        <div className="flex items-center gap-2 ml-auto">
          {addedNodes.length > 0 && (
            <Pill colour="green" icon={Plus} count={addedNodes.length} label="nodes added" />
          )}
          {removedNodes.length > 0 && (
            <Pill colour="red" icon={Minus} count={removedNodes.length} label="nodes removed" />
          )}
          {addedEdges.length > 0 && (
            <Pill colour="green" icon={Plus} count={addedEdges.length} label="edges added" />
          )}
          {removedEdges.length > 0 && (
            <Pill colour="red" icon={Minus} count={removedEdges.length} label="edges removed" />
          )}
          {modifiedEdges.length > 0 && (
            <Pill colour="amber" icon={RefreshCw} count={modifiedEdges.length} label="edges modified" />
          )}
        </div>
      </div>

      {!hasChanges && (
        <p className="text-sm text-zinc-400 dark:text-zinc-600 italic">
          No changes between versions.
        </p>
      )}

      {/* Nodes */}
      {(addedNodes.length > 0 || removedNodes.length > 0) && (
        <div className="space-y-1">
          <p className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">
            Nodes
          </p>
          <ul className="space-y-1" role="list">
            {[...addedNodes, ...removedNodes].map((node) => (
              <NodeDiffRow key={node.id} node={node} />
            ))}
          </ul>
        </div>
      )}

      {/* Edges */}
      {(addedEdges.length > 0 ||
        removedEdges.length > 0 ||
        modifiedEdges.length > 0) && (
        <div className="space-y-1">
          <p className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">
            Edges
          </p>
          <ul className="space-y-1" role="list">
            {[...addedEdges, ...removedEdges, ...modifiedEdges].map((edge) => (
              <EdgeDiffRow key={edge.id} edge={edge} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function NodeDiffRow({ node }: { node: DiffNode }) {
  const colours = DIFF_COLOURS[node.diffStatus];
  const Icon = DIFF_ICON[node.diffStatus];
  return (
    <li
      data-testid={`diff-node-${node.id}`}
      data-diff-status={node.diffStatus}
      className={cn("flex items-center gap-2 rounded px-2 py-1 text-sm", colours.row)}
    >
      {Icon && (
        <Icon className={cn("h-3.5 w-3.5 shrink-0", colours.icon)} aria-hidden />
      )}
      <span
        className={cn(
          "font-medium text-zinc-800 dark:text-zinc-200",
          node.diffStatus === "removed" && "line-through text-zinc-500"
        )}
      >
        {node.label}
      </span>
      <span className="text-xs text-zinc-400 ml-auto">{node.nodeType}</span>
    </li>
  );
}

function EdgeDiffRow({ edge }: { edge: DiffEdge }) {
  const colours = DIFF_COLOURS[edge.diffStatus];
  const Icon = DIFF_ICON[edge.diffStatus];
  return (
    <li
      data-testid={`diff-edge-${edge.id}`}
      data-diff-status={edge.diffStatus}
      className={cn("flex items-center gap-2 rounded px-2 py-1 text-sm", colours.row)}
    >
      {Icon && (
        <Icon className={cn("h-3.5 w-3.5 shrink-0", colours.icon)} aria-hidden />
      )}
      <span
        className={cn(
          "text-zinc-700 dark:text-zinc-300",
          edge.diffStatus === "removed" && "line-through text-zinc-500"
        )}
      >
        {edge.sourceId.slice(0, 6)}… → {edge.targetId.slice(0, 6)}…
      </span>
      <span className="text-xs text-zinc-500">{edge.edgeType}</span>
      <span className="ml-auto text-xs text-zinc-400">
        {edge.diffStatus === "modified" && edge.previousConfidence !== undefined
          ? `${(edge.previousConfidence * 100).toFixed(0)}% → ${(edge.confidence * 100).toFixed(0)}%`
          : `${(edge.confidence * 100).toFixed(0)}%`}
      </span>
    </li>
  );
}

function Pill({
  colour,
  icon: Icon,
  count,
  label,
}: {
  colour: "green" | "red" | "amber";
  icon: typeof Plus;
  count: number;
  label: string;
}) {
  const classes = {
    green:
      "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300",
    red: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300",
    amber:
      "bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300",
  };
  return (
    <span
      title={`${count} ${label}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        classes[colour]
      )}
    >
      <Icon className="h-3 w-3" aria-hidden />
      {count}
    </span>
  );
}
