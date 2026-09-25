"use client";

/**
 * NodeDetail — side-panel detail view for a selected DAG node.
 *
 * Shows:
 *   - Node label, type badge, domain
 *   - Approval status + Guardian verdict badge
 *   - Confidence score
 *   - Lineage (when available)
 *   - Connected edges (incoming + outgoing)
 *   - Description / attributes
 *
 * Used as a slide-over panel alongside DAGCanvas.
 */

import { X, GitBranch, ArrowRight, ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { GuardianVerdictBadge } from "@/components/shared/GuardianVerdictBadge";
import { EpistemicConfidenceBadge } from "@/components/shared/EpistemicConfidenceBadge";
import type { GuardianVerdict } from "@/lib/types";
import type { DAGNode, DAGEdge } from "./DAGCanvas";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface NodeDetailProps {
  node: DAGNode;
  /** All edges in the graph — used to show connected edges */
  allEdges?: DAGEdge[];
  /** All nodes in the graph — used to resolve edge endpoints */
  allNodes?: DAGNode[];
  guardianVerdict?: GuardianVerdict;
  description?: string;
  attributes?: Record<string, string | number | boolean>;
  lineage?: Array<{ step: string; actor: string; timestamp: string }>;
  onClose: () => void;
  onNavigateTo?: (nodeId: string) => void;
  className?: string;
}

// ── Colour maps ───────────────────────────────────────────────────────────────

const NODE_TYPE_COLOURS: Record<DAGNode["nodeType"], string> = {
  CausalEntity:
    "bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-200",
  Outcome: "bg-sky-100 text-sky-800 dark:bg-sky-900 dark:text-sky-200",
  Confounder:
    "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200",
  Mediator:
    "bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200",
  Treatment: "bg-teal-100 text-teal-800 dark:bg-teal-900 dark:text-teal-200",
};

const STATUS_COLOURS: Record<DAGNode["approvalStatus"], string> = {
  DRAFT: "text-zinc-500",
  UNDER_REVIEW: "text-amber-600",
  APPROVED: "text-green-600",
  DEPRECATED: "text-red-600",
};

// ── Component ─────────────────────────────────────────────────────────────────

export function NodeDetail({
  node,
  allEdges = [],
  allNodes = [],
  guardianVerdict = "PENDING",
  description,
  attributes,
  lineage = [],
  onClose,
  onNavigateTo,
  className,
}: NodeDetailProps) {
  const nodeById = new Map(allNodes.map((n) => [n.id, n]));

  const incomingEdges = allEdges.filter((e) => e.targetId === node.id);
  const outgoingEdges = allEdges.filter((e) => e.sourceId === node.id);

  return (
    <aside
      data-testid="node-detail"
      data-node-id={node.id}
      className={cn(
        "flex flex-col h-full overflow-y-auto",
        "bg-white dark:bg-zinc-900",
        "border-l border-zinc-200 dark:border-zinc-800",
        "w-72 shrink-0",
        className
      )}
      aria-label={`Node detail: ${node.label}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-zinc-100 dark:border-zinc-800">
        <div className="space-y-1 min-w-0 pr-2">
          <h2 className="font-semibold text-sm text-zinc-900 dark:text-zinc-100 truncate">
            {node.label}
          </h2>
          <div className="flex items-center gap-1.5 flex-wrap">
            <span
              className={cn(
                "text-xs font-medium px-2 py-0.5 rounded-full",
                NODE_TYPE_COLOURS[node.nodeType]
              )}
            >
              {node.nodeType}
            </span>
            <span
              className={cn(
                "text-xs font-medium",
                STATUS_COLOURS[node.approvalStatus]
              )}
            >
              {node.approvalStatus}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close node detail"
          className="shrink-0 p-1 rounded-md text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 p-4 space-y-4 text-sm">
        {/* Domain + Confidence */}
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-zinc-500 dark:text-zinc-400">Domain</span>
            <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              {node.domain}
            </span>
          </div>
          {node.confidence !== undefined && (
            <div className="flex items-center justify-between">
              <span className="text-xs text-zinc-500 dark:text-zinc-400">
                Confidence
              </span>
              <EpistemicConfidenceBadge score={node.confidence} size="sm" />
            </div>
          )}
          <div className="flex items-center justify-between">
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              Guardian
            </span>
            <GuardianVerdictBadge verdict={guardianVerdict} size="sm" />
          </div>
        </section>

        {/* Description */}
        {description && (
          <section>
            <h3 className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1">
              Description
            </h3>
            <p className="text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed">
              {description}
            </p>
          </section>
        )}

        {/* Attributes */}
        {attributes && Object.keys(attributes).length > 0 && (
          <section>
            <h3 className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1.5">
              Attributes
            </h3>
            <dl className="space-y-1">
              {Object.entries(attributes).map(([key, value]) => (
                <div key={key} className="flex justify-between gap-2">
                  <dt className="text-xs text-zinc-500 dark:text-zinc-400 truncate">
                    {key}
                  </dt>
                  <dd className="text-xs font-medium text-zinc-700 dark:text-zinc-300 truncate">
                    {String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        {/* Connected edges */}
        {(incomingEdges.length > 0 || outgoingEdges.length > 0) && (
          <section>
            <h3 className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1.5">
              Connections
            </h3>
            <ul className="space-y-1.5">
              {incomingEdges.map((edge) => {
                const peer = nodeById.get(edge.sourceId);
                return (
                  <li key={edge.id} className="flex items-center gap-1.5">
                    <ArrowRight
                      className="h-3.5 w-3.5 text-indigo-400 shrink-0"
                      aria-hidden
                    />
                    <button
                      type="button"
                      className="text-xs text-zinc-600 dark:text-zinc-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:underline truncate text-left"
                      onClick={() => onNavigateTo?.(edge.sourceId)}
                    >
                      ← {peer?.label ?? edge.sourceId}
                    </button>
                    <span className="ml-auto text-xs text-zinc-400 shrink-0">
                      {edge.edgeType}
                    </span>
                  </li>
                );
              })}
              {outgoingEdges.map((edge) => {
                const peer = nodeById.get(edge.targetId);
                return (
                  <li key={edge.id} className="flex items-center gap-1.5">
                    <ArrowRight
                      className="h-3.5 w-3.5 text-green-400 shrink-0"
                      aria-hidden
                    />
                    <button
                      type="button"
                      className="text-xs text-zinc-600 dark:text-zinc-400 hover:text-green-600 dark:hover:text-green-400 hover:underline truncate text-left"
                      onClick={() => onNavigateTo?.(edge.targetId)}
                    >
                      {peer?.label ?? edge.targetId} →
                    </button>
                    <span className="ml-auto text-xs text-zinc-400 shrink-0">
                      {edge.edgeType}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {/* Lineage */}
        {lineage.length > 0 && (
          <section>
            <h3 className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider mb-1.5">
              Lineage
            </h3>
            <ol className="space-y-2">
              {lineage.map((entry, i) => (
                <li key={i} className="flex gap-2">
                  <span className="shrink-0 flex items-center justify-center h-4 w-4 rounded-full bg-indigo-100 dark:bg-indigo-900 text-indigo-700 dark:text-indigo-300 text-[10px] font-bold mt-0.5">
                    {i + 1}
                  </span>
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
                      {entry.step}
                    </p>
                    <p className="text-xs text-zinc-400">
                      {entry.actor} ·{" "}
                      {new Date(entry.timestamp).toLocaleDateString("en-GB", {
                        day: "numeric",
                        month: "short",
                      })}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
    </aside>
  );
}
