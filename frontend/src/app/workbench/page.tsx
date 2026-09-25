"use client";

/**
 * DAG Workbench — unified graph-authoring surface (Phase 2 MVP, read-only).
 *
 * Shows:
 *   - Fragment list grouped by domain (left panel)
 *   - Selected fragment rendered on DAGCanvas (top-right)
 *   - Edge detail when a canvas edge is clicked (bottom-right)
 *
 * Data:
 *   - GET /api/semantic/fragments
 *   - GET /api/semantic/fragments/{id}   (after fragment selection)
 *   - GET /api/semantic/edges/{id}       (fanned out per edge ID in the fragment)
 *
 * Failure mode:
 *   - If backend is unreachable and NEXT_PUBLIC_ENABLE_MOCK_FALLBACK is set,
 *     a single mock fragment is shown with an explicit badge.
 *   - Otherwise, a degraded-state message is shown (no silent mock substitution).
 *
 * Out of scope for MVP (tracked in CURRENT_STAGE.md Phase 2):
 *   - Edge mutation (propose / edit / delete) — needs backend edge CRUD + UI form.
 *   - Agent-suggested edges from CausalDiscoveryAgent.
 *   - Reusable-fragment catalog with cloning.
 */

import { useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { GitBranch, RefreshCw, AlertTriangle, Search } from "lucide-react";

import { DAGCanvas, type DAGEdge, type DAGNode } from "@/components/graph/DAGCanvas";
import { PersonaSelector } from "@/components/shared/PersonaSelector";
import {
  semanticApi,
  type CausalEdgeResponse,
  type GraphFragmentResponse,
} from "@/lib/api";

// Backend response shapes (camelCase, alias_generator applied) are declared
// once, in @/lib/api, against the backend pydantic models.

// ── Mock fallback (dev only, gated by env flag) ───────────────────────────────

const ENABLE_MOCK = process.env["NEXT_PUBLIC_ENABLE_MOCK_FALLBACK"] === "true";

const MOCK_FRAGMENT: GraphFragmentResponse = {
  id: "mock-frag-001",
  name: "deployment_latency_attribution",
  domain: "devops",
  description: "Mock fragment shown when backend is unreachable.",
  edges: ["mock-edge-001", "mock-edge-002"],
  approvalStatus: "APPROVED",
  version: "1.0.0",
  usageCount: 0,
  tags: ["mock"],
  guardianVerdictRef: "audit-mock-frag",
  createdAt: new Date().toISOString(),
};

const MOCK_EDGES: Record<string, CausalEdgeResponse> = {
  "mock-edge-001": {
    id: "mock-edge-001",
    source: { id: "deployment_event", objectType: "Treatment" },
    target: { id: "service_latency", objectType: "Outcome" },
    edgeType: "CAUSES",
    direction: "FORWARD",
    confidence: 0.82,
    evidenceRefs: ["ev-1"],
    assumptionRefs: ["ass-1"],
    approvalStatus: "APPROVED",
    approvedBy: { id: "system" },
    approvedAt: new Date().toISOString(),
    guardianVerdictRef: "audit-mock-1",
    version: "1.0.0",
    domain: "devops",
  },
  "mock-edge-002": {
    id: "mock-edge-002",
    source: { id: "service_latency", objectType: "CausalEntity" },
    target: { id: "checkout_conversion", objectType: "Outcome" },
    edgeType: "CAUSES",
    direction: "FORWARD",
    confidence: 0.74,
    evidenceRefs: [],
    assumptionRefs: [],
    approvalStatus: "APPROVED",
    approvedBy: { id: "system" },
    approvedAt: new Date().toISOString(),
    guardianVerdictRef: "audit-mock-2",
    version: "1.0.0",
    domain: "devops",
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function objectTypeToNodeType(objectType: string): DAGNode["nodeType"] {
  // Map backend CausalObjectRef.objectType → DAGCanvas nodeType.
  switch (objectType) {
    case "Treatment":
      return "Treatment";
    case "Outcome":
      return "Outcome";
    case "Confounder":
      return "Confounder";
    case "Mediator":
      return "Mediator";
    default:
      return "CausalEntity";
  }
}

/**
 * Build the unique set of DAG nodes referenced by a fragment's edges.
 * Nodes inherit their approval status from the fragment for visualisation
 * purposes; the edge approval statuses drive edge colouring.
 */
function buildNodesFromEdges(
  edges: CausalEdgeResponse[],
  fragment: GraphFragmentResponse | null
): DAGNode[] {
  const seen = new Map<string, DAGNode>();
  const fragStatus: DAGNode["approvalStatus"] = fragment
    ? fragment.approvalStatus
    : "APPROVED";

  for (const edge of edges) {
    for (const ref of [edge.source, edge.target]) {
      if (seen.has(ref.id)) continue;
      seen.set(ref.id, {
        id: ref.id,
        label: ref.id,
        nodeType: objectTypeToNodeType(ref.objectType),
        domain: fragment?.domain ?? edge.domain,
        approvalStatus: fragStatus,
      });
    }
  }
  return Array.from(seen.values());
}

function backendEdgeToDagEdge(edge: CausalEdgeResponse): DAGEdge {
  return {
    id: edge.id,
    sourceId: edge.source.id,
    targetId: edge.target.id,
    edgeType: edge.edgeType,
    confidence: edge.confidence,
    approvalStatus: edge.approvalStatus,
  };
}

function confidenceTone(c: number): string {
  if (c >= 0.7) return "text-emerald-700 dark:text-emerald-400";
  if (c >= 0.4) return "text-amber-700 dark:text-amber-400";
  return "text-zinc-600 dark:text-zinc-400";
}

// ── Page component ────────────────────────────────────────────────────────────

export default function WorkbenchPage() {
  const [selectedFragmentId, setSelectedFragmentId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  // ── Fragment list ───────────────────────────────────────────────────────────
  const {
    data: backendFragments,
    isLoading: fragmentsLoading,
    isError: fragmentsError,
    refetch: refetchFragments,
  } = useQuery<GraphFragmentResponse[]>({
    queryKey: ["semantic", "fragments"],
    queryFn: () => semanticApi.fragments(),
    retry: 1,
    staleTime: 30_000,
  });

  const useMock = ENABLE_MOCK && fragmentsError;
  const fragments: GraphFragmentResponse[] = useMemo(
    () => (useMock && !backendFragments ? [MOCK_FRAGMENT] : (backendFragments ?? [])),
    [useMock, backendFragments]
  );

  const dataSource = useMock ? "mock" : backendFragments ? "live" : fragmentsError ? "error" : "loading";

  // ── Selected fragment detail ────────────────────────────────────────────────
  const {
    data: selectedFragment,
  } = useQuery<GraphFragmentResponse>({
    queryKey: ["semantic", "fragment", selectedFragmentId],
    queryFn: () => semanticApi.fragment(selectedFragmentId!),
    enabled: Boolean(selectedFragmentId) && !useMock,
    staleTime: 30_000,
  });

  const effectiveFragment: GraphFragmentResponse | null =
    useMock && selectedFragmentId === MOCK_FRAGMENT.id
      ? MOCK_FRAGMENT
      : selectedFragment ?? null;

  // ── Fan out: fetch each edge in the selected fragment ───────────────────────
  const edgeIds = effectiveFragment?.edges ?? [];
  const edgeQueries = useQueries({
    queries: edgeIds.map((edgeId) => ({
      queryKey: ["semantic", "edge", edgeId],
      queryFn: () =>
        useMock
          ? Promise.resolve(MOCK_EDGES[edgeId]!)
          : semanticApi.edge(edgeId),
      enabled: Boolean(edgeId),
      staleTime: 60_000,
    })),
  });

  const edgesLoading = edgeQueries.some((q) => q.isLoading);
  const edgesError = edgeQueries.some((q) => q.isError);
  const edges: CausalEdgeResponse[] = edgeQueries
    .map((q) => q.data)
    .filter((e): e is CausalEdgeResponse => Boolean(e));

  // ── DAG canvas inputs ───────────────────────────────────────────────────────
  const dagNodes: DAGNode[] = useMemo(
    () => buildNodesFromEdges(edges, effectiveFragment),
    [edges, effectiveFragment]
  );
  const dagEdges: DAGEdge[] = useMemo(
    () => edges.map(backendEdgeToDagEdge),
    [edges]
  );

  // ── Group fragments by domain (for left panel) ──────────────────────────────
  const filteredFragments = useMemo(() => {
    if (!searchQuery.trim()) return fragments;
    const q = searchQuery.toLowerCase();
    return fragments.filter(
      (f) =>
        f.name.toLowerCase().includes(q) ||
        f.domain.toLowerCase().includes(q) ||
        f.tags.some((t) => t.toLowerCase().includes(q))
    );
  }, [fragments, searchQuery]);

  const fragmentsByDomain = useMemo(() => {
    const groups: Record<string, GraphFragmentResponse[]> = {};
    for (const frag of filteredFragments) {
      const d = frag.domain || "uncategorised";
      (groups[d] ??= []).push(frag);
    }
    return groups;
  }, [filteredFragments]);

  // ── Selected edge (for detail pane) ─────────────────────────────────────────
  const selectedEdge: CausalEdgeResponse | null =
    edges.find((e) => e.id === selectedEdgeId) ?? null;

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Top nav */}
      <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-border bg-background/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-sm font-bold text-foreground">Causal Platform</h1>
        <GitBranch className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="text-sm text-muted-foreground">DAG Workbench</span>
        <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
          {fragments.length} fragments
        </span>
        {dataSource === "mock" && (
          <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            mock data
          </span>
        )}
        {dataSource === "error" && (
          <span className="flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-xs font-bold text-red-700 dark:bg-red-950 dark:text-red-300">
            <AlertTriangle className="h-3 w-3" aria-hidden />
            backend unavailable
          </span>
        )}
        <div className="ml-auto flex items-center gap-3">
          {!fragmentsLoading && (
            <button
              type="button"
              onClick={() => void refetchFragments()}
              aria-label="Refresh fragments"
              className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <RefreshCw className="h-3 w-3" aria-hidden />
              Refresh
            </button>
          )}
          <PersonaSelector layout="dropdown" className="w-48" />
        </div>
      </header>

      <div className="flex flex-1 gap-0 overflow-hidden">
        {/* ── Left: fragment list ──────────────────────────────────────────── */}
        <aside className="flex w-72 flex-col gap-3 overflow-y-auto border-r border-border bg-muted/30 p-4">
          <div>
            <h2 className="mb-2 text-sm font-bold text-foreground">Fragments</h2>
            <p className="mb-3 text-xs text-muted-foreground">
              APPROVED reusable sub-DAGs. Click to inspect.
            </p>
            <label className="relative block">
              <Search
                className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <input
                type="search"
                placeholder="Search by name, domain, tag…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-md border border-border bg-background py-1.5 pl-7 pr-2 text-xs text-foreground placeholder:text-muted-foreground focus:border-foreground/40 focus:outline-none"
                aria-label="Filter fragments"
              />
            </label>
          </div>

          {dataSource === "error" && !fragmentsLoading && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
              <div className="mb-1 flex items-center gap-1.5 font-semibold">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                Backend unavailable
              </div>
              <p className="mb-2">
                Could not load fragment list. Set{" "}
                <code className="rounded bg-red-100 px-1 dark:bg-red-900">
                  NEXT_PUBLIC_ENABLE_MOCK_FALLBACK=true
                </code>{" "}
                for a mock fragment in dev.
              </p>
            </div>
          )}

          {fragmentsLoading && (
            <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
              <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden />
              Loading fragments…
            </div>
          )}

          {!fragmentsLoading &&
            dataSource !== "error" &&
            Object.entries(fragmentsByDomain).map(([domain, group]) => (
              <div key={domain} className="space-y-1">
                <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                  {domain}{" "}
                  <span className="font-normal text-muted-foreground/70">
                    ({group.length})
                  </span>
                </h3>
                <ul className="space-y-0.5">
                  {group.map((frag) => {
                    const isSelected = frag.id === selectedFragmentId;
                    return (
                      <li key={frag.id}>
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedFragmentId(frag.id);
                            setSelectedEdgeId(null);
                          }}
                          aria-pressed={isSelected}
                          className={`w-full rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors ${
                            isSelected
                              ? "border-foreground/40 bg-background font-medium text-foreground"
                              : "border-transparent text-foreground/80 hover:border-border hover:bg-background"
                          }`}
                        >
                          <div className="truncate">{frag.name}</div>
                          <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-muted-foreground">
                            <span>v{frag.version}</span>
                            <span>·</span>
                            <span>{frag.edges.length} edges</span>
                            {frag.usageCount > 0 && (
                              <>
                                <span>·</span>
                                <span>{frag.usageCount} uses</span>
                              </>
                            )}
                          </div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}

          {!fragmentsLoading &&
            dataSource !== "error" &&
            filteredFragments.length === 0 && (
              <p className="py-4 text-xs text-muted-foreground">
                No fragments match the current filter.
              </p>
            )}
        </aside>

        {/* ── Right: canvas + edge detail ──────────────────────────────────── */}
        <main className="flex flex-1 flex-col gap-4 overflow-y-auto p-4 lg:p-6">
          {!selectedFragmentId && (
            <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-border bg-muted/30 p-8 text-center">
              <div className="space-y-2">
                <GitBranch
                  className="mx-auto h-8 w-8 text-muted-foreground"
                  aria-hidden
                />
                <h2 className="text-base font-semibold text-foreground">
                  Select a fragment to inspect its DAG
                </h2>
                <p className="text-sm text-muted-foreground">
                  Approved fragments are reusable sub-DAGs that
                  SimulationAgent and external MCP clients can call into.
                </p>
              </div>
            </div>
          )}

          {selectedFragmentId && effectiveFragment && (
            <>
              {/* Fragment header */}
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <h2 className="text-lg font-bold text-foreground">
                    {effectiveFragment.name}
                  </h2>
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                    {effectiveFragment.approvalStatus}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    v{effectiveFragment.version} · {effectiveFragment.domain}
                  </span>
                </div>
                {effectiveFragment.description && (
                  <p className="text-sm text-muted-foreground">
                    {effectiveFragment.description}
                  </p>
                )}
                {effectiveFragment.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1 pt-1">
                    {effectiveFragment.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Canvas */}
              {edgesLoading && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <RefreshCw className="h-4 w-4 animate-spin" aria-hidden />
                  Loading edges…
                </div>
              )}

              {edgesError && !useMock && (
                <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
                  <div className="flex items-center gap-1.5 font-semibold">
                    <AlertTriangle className="h-4 w-4" aria-hidden />
                    Could not load all edges in this fragment.
                  </div>
                </div>
              )}

              {!edgesLoading && (
                <DAGCanvas
                  nodes={dagNodes}
                  edges={dagEdges}
                  onEdgeSelect={(edge) => setSelectedEdgeId(edge.id)}
                  width={760}
                  height={460}
                />
              )}

              {/* Edge detail pane */}
              <section
                aria-label="Edge detail"
                className="rounded-lg border border-border bg-card p-4"
              >
                {!selectedEdge && (
                  <p className="text-sm text-muted-foreground">
                    Click an edge in the canvas to see its confidence,
                    evidence, assumptions, and Guardian verdict.
                  </p>
                )}

                {selectedEdge && (
                  <div className="space-y-3">
                    <header className="flex items-baseline gap-2">
                      <h3 className="text-sm font-bold text-foreground">
                        <span className="font-mono">
                          {selectedEdge.source.id}
                        </span>{" "}
                        <span className="text-muted-foreground">
                          —{selectedEdge.edgeType}→
                        </span>{" "}
                        <span className="font-mono">
                          {selectedEdge.target.id}
                        </span>
                      </h3>
                      <span
                        className={`text-xs font-medium ${confidenceTone(
                          selectedEdge.confidence
                        )}`}
                      >
                        {(selectedEdge.confidence * 100).toFixed(0)}% confidence
                      </span>
                    </header>

                    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                      <div>
                        <dt className="text-muted-foreground">Direction</dt>
                        <dd className="font-mono text-foreground">
                          {selectedEdge.direction}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Status</dt>
                        <dd className="font-mono text-foreground">
                          {selectedEdge.approvalStatus}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Evidence</dt>
                        <dd className="text-foreground">
                          {selectedEdge.evidenceRefs.length} reference
                          {selectedEdge.evidenceRefs.length === 1 ? "" : "s"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Assumptions</dt>
                        <dd className="text-foreground">
                          {selectedEdge.assumptionRefs.length} attached
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Version</dt>
                        <dd className="font-mono text-foreground">
                          v{selectedEdge.version}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">Domain</dt>
                        <dd className="font-mono text-foreground">
                          {selectedEdge.domain}
                        </dd>
                      </div>
                    </dl>

                    {selectedEdge.approvedAt && (
                      <p className="text-xs text-muted-foreground">
                        Approved{" "}
                        {new Date(
                          selectedEdge.approvedAt
                        ).toLocaleDateString()}{" "}
                        by{" "}
                        <span className="font-mono">
                          {selectedEdge.approvedBy?.id ?? "system"}
                        </span>
                      </p>
                    )}

                    {selectedEdge.guardianVerdictRef && (
                      <p className="text-xs">
                        <span className="text-muted-foreground">
                          Guardian verdict ref:{" "}
                        </span>
                        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground">
                          {selectedEdge.guardianVerdictRef}
                        </code>
                      </p>
                    )}
                  </div>
                )}
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
