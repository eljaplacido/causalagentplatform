"use client";

/**
 * NodeDetail — what the workbench knows about a node selected on DAGCanvas.
 *
 * A node on the workbench canvas is an endpoint of the fragment's edges: an
 * object reference (id and object type) read from GET /api/semantic/edges/{id}.
 * So this shows only what those edges say about it: its id, the object type the
 * edges reference it as, the domain, and the edges into and out of it with their
 * relation type, stated confidence and approval status. It shows no approval
 * status or Guardian verdict of the node itself: the edges carry none for their
 * endpoints, and the canvas's node colour is the fragment's status, not the
 * node's (septa-87).
 */

import { X, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DAGNode, DAGEdge } from "./DAGCanvas";

export interface NodeDetailProps {
  node: DAGNode;
  /** The graph's edges: the ones touching `node` are listed. */
  allEdges?: DAGEdge[];
  /** The graph's nodes: used to label an edge's other end. */
  allNodes?: DAGNode[];
  onClose: () => void;
  /** Select the node at the other end of a listed edge. */
  onNavigateTo?: (nodeId: string) => void;
  className?: string;
}

function EdgeFacts({ edge }: { edge: DAGEdge }) {
  return (
    <span className="ml-auto shrink-0 text-xs text-muted-foreground">
      {edge.edgeType} · {(edge.confidence * 100).toFixed(0)}% stated ·{" "}
      {edge.approvalStatus}
    </span>
  );
}

export function NodeDetail({
  node,
  allEdges = [],
  allNodes = [],
  onClose,
  onNavigateTo,
  className,
}: NodeDetailProps) {
  const nodeById = new Map(allNodes.map((n) => [n.id, n]));
  const incoming = allEdges.filter((e) => e.targetId === node.id);
  const outgoing = allEdges.filter((e) => e.sourceId === node.id);

  return (
    <aside
      data-testid="node-detail"
      data-node-id={node.id}
      aria-label={`Node detail: ${node.label}`}
      className={cn("rounded-lg border border-border bg-card p-4 text-sm", className)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <h3 className="truncate font-mono text-sm font-bold text-foreground">{node.label}</h3>
          <p className="text-xs text-muted-foreground">
            Referenced as <span className="font-medium text-foreground">{node.nodeType}</span>
            {" · "}
            <span className="font-mono">{node.domain}</span>
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close node detail"
          className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      <section className="mt-3 space-y-1.5">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Edges in this fragment ({incoming.length + outgoing.length})
        </h4>
        {incoming.length + outgoing.length === 0 ? (
          <p className="text-xs text-muted-foreground">None.</p>
        ) : (
          <ul className="space-y-1.5" data-testid="node-detail-edges">
            {incoming.map((edge) => (
              <li key={edge.id} className="flex items-center gap-1.5">
                <ArrowRight className="h-3.5 w-3.5 shrink-0 text-indigo-400" aria-hidden />
                <button
                  type="button"
                  className="truncate text-left font-mono text-xs text-foreground hover:underline"
                  onClick={() => onNavigateTo?.(edge.sourceId)}
                >
                  from {nodeById.get(edge.sourceId)?.label ?? edge.sourceId}
                </button>
                <EdgeFacts edge={edge} />
              </li>
            ))}
            {outgoing.map((edge) => (
              <li key={edge.id} className="flex items-center gap-1.5">
                <ArrowRight className="h-3.5 w-3.5 shrink-0 text-green-500" aria-hidden />
                <button
                  type="button"
                  className="truncate text-left font-mono text-xs text-foreground hover:underline"
                  onClick={() => onNavigateTo?.(edge.targetId)}
                >
                  to {nodeById.get(edge.targetId)?.label ?? edge.targetId}
                </button>
                <EdgeFacts edge={edge} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}
