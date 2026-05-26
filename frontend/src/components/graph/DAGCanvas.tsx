"use client";

/**
 * DAGCanvas — Directed Acyclic Graph visualisation canvas.
 *
 * Renders a causal DAG using SVG with:
 *   - Nodes as labelled rectangles (coloured by approval status)
 *   - Directed edges as SVG paths with arrowhead markers
 *   - Node hover = show tooltip (type, domain, confidence)
 *   - Edge colour = approval status (DRAFT=zinc, APPROVED=green, REJECTED=red)
 *   - CHAOTIC domain shows a locked overlay (no edits permitted)
 *
 * Layout: simple force-directed via CSS grid columns for MVP; replace with
 * d3-force or elkjs for production layout.
 *
 * Accessibility: role="img" with aria-label on the SVG, nodes have role="button"
 * and keyboard navigation (Enter/Space → select).
 */

import React, { useCallback, useMemo, useState } from "react";
import { cn } from "@/lib/utils";
import type { CausalEdge, CynefinDomain } from "@/lib/types";
import { GuardianVerdictBadge } from "@/components/shared/GuardianVerdictBadge";
import { CynefinBadge } from "@/components/shared/CynefinBadge";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface DAGNode {
  id: string;
  label: string;
  nodeType: "CausalEntity" | "Outcome" | "Confounder" | "Mediator" | "Treatment";
  domain: string;
  approvalStatus: "DRAFT" | "UNDER_REVIEW" | "APPROVED" | "REJECTED";
  confidence?: number;
  /** Position in the layout (0–1 normalised, set by parent or layout algorithm) */
  x?: number;
  y?: number;
}

export interface DAGEdge {
  id: string;
  sourceId: string;
  targetId: string;
  edgeType: string;
  confidence: number;
  approvalStatus: "DRAFT" | "UNDER_REVIEW" | "APPROVED" | "REJECTED";
}

export interface DAGCanvasProps {
  nodes: DAGNode[];
  edges: DAGEdge[];
  cynefinDomain?: CynefinDomain;
  /** Called when the user clicks a node */
  onNodeSelect?: (node: DAGNode) => void;
  /** Called when the user clicks an edge */
  onEdgeSelect?: (edge: DAGEdge) => void;
  width?: number;
  height?: number;
  className?: string;
}

// ── Colour maps ───────────────────────────────────────────────────────────────

const NODE_COLOUR: Record<DAGNode["approvalStatus"], string> = {
  DRAFT: "#a1a1aa",      // zinc-400
  UNDER_REVIEW: "#fbbf24", // amber-400
  APPROVED: "#22c55e",   // green-500
  REJECTED: "#ef4444",   // red-500
};

const NODE_TYPE_COLOUR: Record<DAGNode["nodeType"], string> = {
  CausalEntity: "#6366f1",  // indigo-500
  Outcome: "#0ea5e9",       // sky-500
  Confounder: "#f97316",    // orange-500
  Mediator: "#a855f7",      // purple-500
  Treatment: "#14b8a6",     // teal-500
};

const EDGE_COLOUR: Record<DAGEdge["approvalStatus"], string> = {
  DRAFT: "#71717a",         // zinc-500
  UNDER_REVIEW: "#f59e0b",  // amber-500
  APPROVED: "#16a34a",      // green-600
  REJECTED: "#dc2626",      // red-600
};

// ── Node dimensions ───────────────────────────────────────────────────────────

const NODE_W = 130;
const NODE_H = 44;
const PADDING = 60;

// ── Default layout (simple grid, columns by domain) ───────────────────────────

function defaultLayout(
  nodes: DAGNode[],
  width: number,
  height: number
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const cols = Math.ceil(Math.sqrt(nodes.length));

  nodes.forEach((node, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    const usableW = width - PADDING * 2 - NODE_W;
    const usableH = height - PADDING * 2 - NODE_H;
    const colCount = Math.min(cols, nodes.length);
    const rowCount = Math.ceil(nodes.length / cols);

    positions.set(node.id, {
      x: PADDING + (colCount > 1 ? (col * usableW) / (colCount - 1) : usableW / 2),
      y: PADDING + (rowCount > 1 ? (row * usableH) / (rowCount - 1) : usableH / 2),
    });
  });

  return positions;
}

/** Build a quadratic bezier path between two rectangles. */
function edgePath(
  sx: number,
  sy: number,
  tx: number,
  ty: number
): string {
  const mx = (sx + tx) / 2;
  const my = (sy + ty) / 2;
  return `M ${sx + NODE_W / 2} ${sy + NODE_H / 2} Q ${mx} ${my} ${tx + NODE_W / 2} ${ty + NODE_H / 2}`;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function DAGCanvas({
  nodes,
  edges,
  cynefinDomain,
  onNodeSelect,
  onEdgeSelect,
  width = 800,
  height = 500,
  className,
}: DAGCanvasProps) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [tooltipNode, setTooltipNode] = useState<DAGNode | null>(null);

  const isChaotic = cynefinDomain === "CHAOTIC";
  const isEmpty = nodes.length === 0;

  // Compute positions using provided x/y or fallback to default layout
  const positions = useMemo(() => {
    const hasCustomPositions = nodes.some(
      (n) => n.x !== undefined && n.y !== undefined
    );
    if (hasCustomPositions) {
      const map = new Map<string, { x: number; y: number }>();
      nodes.forEach((n) => {
        map.set(n.id, {
          x: (n.x ?? 0) * (width - PADDING * 2 - NODE_W) + PADDING,
          y: (n.y ?? 0) * (height - PADDING * 2 - NODE_H) + PADDING,
        });
      });
      return map;
    }
    return defaultLayout(nodes, width, height);
  }, [nodes, width, height]);

  const nodeById = useMemo(() => {
    const m = new Map<string, DAGNode>();
    nodes.forEach((n) => m.set(n.id, n));
    return m;
  }, [nodes]);

  const handleNodeClick = useCallback(
    (node: DAGNode) => {
      setSelectedNodeId(node.id);
      onNodeSelect?.(node);
    },
    [onNodeSelect]
  );

  const handleNodeKey = useCallback(
    (e: React.KeyboardEvent, node: DAGNode) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handleNodeClick(node);
      }
    },
    [handleNodeClick]
  );

  return (
    <div
      className={cn("relative rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 overflow-hidden", className)}
      data-testid="dag-canvas"
      data-domain={cynefinDomain}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-100 dark:border-zinc-800">
        <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
          Causal Graph
        </span>
        {cynefinDomain && (
          <CynefinBadge domain={cynefinDomain} size="sm" />
        )}
        <span className="ml-auto text-xs text-zinc-400">
          {nodes.length} nodes · {edges.length} edges
        </span>
      </div>

      {/* SVG canvas */}
      <div className="relative" style={{ width, height }}>
        {isEmpty && (
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-sm text-zinc-400 dark:text-zinc-600">
              No causal graph loaded. Propose edges to begin.
            </p>
          </div>
        )}

        <svg
          width={width}
          height={height}
          role="img"
          aria-label={`Causal DAG with ${nodes.length} nodes and ${edges.length} edges`}
          data-testid="dag-svg"
        >
          {/* Arrowhead marker */}
          <defs>
            {(["DRAFT", "UNDER_REVIEW", "APPROVED", "REJECTED"] as const).map(
              (status) => (
                <marker
                  key={status}
                  id={`arrow-${status}`}
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="6"
                  markerHeight="6"
                  orient="auto-start-reverse"
                >
                  <path
                    d="M 0 0 L 10 5 L 0 10 z"
                    fill={EDGE_COLOUR[status]}
                  />
                </marker>
              )
            )}
          </defs>

          {/* Edges */}
          {edges.map((edge) => {
            const srcPos = positions.get(edge.sourceId);
            const tgtPos = positions.get(edge.targetId);
            if (!srcPos || !tgtPos) return null;
            const colour = EDGE_COLOUR[edge.approvalStatus];

            return (
              <g key={edge.id} data-testid={`dag-edge-${edge.id}`}>
                <path
                  d={edgePath(srcPos.x, srcPos.y, tgtPos.x, tgtPos.y)}
                  stroke={colour}
                  strokeWidth={2}
                  fill="none"
                  strokeDasharray={
                    edge.approvalStatus === "DRAFT" ? "5 3" : undefined
                  }
                  markerEnd={`url(#arrow-${edge.approvalStatus})`}
                  opacity={0.8}
                  className="cursor-pointer hover:stroke-[3px]"
                  onClick={() => onEdgeSelect?.(edge)}
                  aria-label={`Edge ${edge.sourceId} → ${edge.targetId}, ${edge.edgeType}, confidence ${(edge.confidence * 100).toFixed(0)}%`}
                />
                {/* Confidence label at midpoint */}
                <text
                  x={
                    (positions.get(edge.sourceId)!.x +
                      positions.get(edge.targetId)!.x) /
                      2 +
                    NODE_W / 2
                  }
                  y={
                    (positions.get(edge.sourceId)!.y +
                      positions.get(edge.targetId)!.y) /
                      2 +
                    NODE_H / 2 -
                    8
                  }
                  textAnchor="middle"
                  fontSize="10"
                  fill={colour}
                  className="select-none"
                >
                  {(edge.confidence * 100).toFixed(0)}%
                </text>
              </g>
            );
          })}

          {/* Nodes */}
          {nodes.map((node) => {
            const pos = positions.get(node.id);
            if (!pos) return null;
            const isSelected = selectedNodeId === node.id;
            const isHovered = hoveredNodeId === node.id;
            const borderColour = isSelected
              ? NODE_TYPE_COLOUR[node.nodeType]
              : NODE_COLOUR[node.approvalStatus];

            return (
              <g
                key={node.id}
                data-testid={`dag-node-${node.id}`}
                transform={`translate(${pos.x}, ${pos.y})`}
                role="button"
                tabIndex={0}
                aria-selected={isSelected}
                aria-label={`Node: ${node.label}, type: ${node.nodeType}, status: ${node.approvalStatus}`}
                onClick={() => handleNodeClick(node)}
                onKeyDown={(e) => handleNodeKey(e, node)}
                onMouseEnter={() => {
                  setHoveredNodeId(node.id);
                  setTooltipNode(node);
                }}
                onMouseLeave={() => {
                  setHoveredNodeId(null);
                  setTooltipNode(null);
                }}
                className="cursor-pointer"
              >
                {/* Node rectangle */}
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  rx={6}
                  ry={6}
                  fill="white"
                  stroke={borderColour}
                  strokeWidth={isSelected ? 2.5 : 1.5}
                  className="dark:fill-zinc-900"
                />
                {/* Type colour bar */}
                <rect
                  width={6}
                  height={NODE_H}
                  rx={4}
                  ry={4}
                  fill={NODE_TYPE_COLOUR[node.nodeType]}
                />
                {/* Label */}
                <text
                  x={NODE_W / 2 + 3}
                  y={NODE_H / 2 - 4}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize="11"
                  fontWeight={isSelected ? "600" : "400"}
                  fill="currentColor"
                  className="select-none text-zinc-800 dark:text-zinc-200"
                >
                  {node.label.length > 14
                    ? node.label.slice(0, 13) + "…"
                    : node.label}
                </text>
                {/* Status label */}
                <text
                  x={NODE_W / 2 + 3}
                  y={NODE_H / 2 + 9}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize="9"
                  fill={NODE_COLOUR[node.approvalStatus]}
                  className="select-none"
                >
                  {node.approvalStatus}
                </text>
              </g>
            );
          })}
        </svg>

        {/* Node tooltip */}
        {tooltipNode && (() => {
          const pos = positions.get(tooltipNode.id);
          if (!pos) return null;
          return (
            <div
              role="tooltip"
              aria-live="polite"
              className="absolute z-10 pointer-events-none bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg shadow-lg p-2 text-xs max-w-[200px]"
              style={{
                left: Math.min(pos.x + NODE_W + 4, width - 210),
                top: pos.y,
              }}
            >
              <p className="font-semibold text-zinc-800 dark:text-zinc-200">
                {tooltipNode.label}
              </p>
              <p className="text-zinc-500 dark:text-zinc-400">
                Type: {tooltipNode.nodeType}
              </p>
              <p className="text-zinc-500 dark:text-zinc-400">
                Domain: {tooltipNode.domain}
              </p>
              <p className="text-zinc-500 dark:text-zinc-400">
                Status: {tooltipNode.approvalStatus}
              </p>
              {tooltipNode.confidence !== undefined && (
                <p className="text-zinc-500 dark:text-zinc-400">
                  Confidence: {(tooltipNode.confidence * 100).toFixed(0)}%
                </p>
              )}
            </div>
          );
        })()}

        {/* CHAOTIC domain overlay */}
        {isChaotic && (
          <div
            role="alert"
            data-testid="dag-chaotic-overlay"
            className="absolute inset-0 bg-red-950/60 flex flex-col items-center justify-center gap-2 backdrop-blur-[2px]"
          >
            <span className="text-red-300 text-2xl" aria-hidden>⚠</span>
            <p className="text-red-200 font-semibold text-sm">
              CHAOTIC domain — graph editing suspended
            </p>
            <p className="text-red-300 text-xs">
              Route to human incident commander
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
