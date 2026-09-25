"use client";

/**
 * Analyst Workbench — Causal Analyst persona (UIX.md §2.5, US-19–US-23).
 *
 * Layout:
 *   ┌────────────────────────────────────────────────────────────┐
 *   │  Header: title, persona switcher, session status           │
 *   ├────────────┬────────────────────────┬──────────────────────┤
 *   │ Fragment   │      DAG Canvas        │  Proposals / Detail  │
 *   │ Library    │   (centre panel)       │  (right panel)       │
 *   │ (left)     │                        │                      │
 *   └────────────┴────────────────────────┴──────────────────────┘
 *
 * UX contract:
 *   - ThinkingIndicator must render synchronously before any await
 *     (setIsThinking(true) called before the API call)
 *   - CHAOTIC domain immediately shows EscalationBanner, suppresses AI analysis
 *   - Selecting a node opens NodeDetail in the right panel
 *   - Edge proposals listed with approve/reject for governance_officer role
 *
 * Data flow:
 *   1. User enters problem description
 *   2. POST /api/v1/session/run → backend LangGraph pipeline
 *   3. Result EpistemicState → populates canvas + proposals + explanation
 */

import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Play, RefreshCw, GitBranch, Layers, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { CausalApiError, semanticApi, sessionApi, type SessionStateResponse } from "@/lib/api";
import { toFragmentCardData } from "@/lib/mappers";
import { ThinkingIndicator } from "@/components/shared/ThinkingIndicator";
import { PersonaSelector } from "@/components/shared/PersonaSelector";
import { CynefinBadge } from "@/components/shared/CynefinBadge";
import { GuardianVerdictBadge } from "@/components/shared/GuardianVerdictBadge";
import { EpistemicConfidenceBadge } from "@/components/shared/EpistemicConfidenceBadge";
import { EscalationBanner } from "@/components/incident/EscalationBanner";
import { DAGCanvas } from "@/components/graph/DAGCanvas";
import { EdgeProposal } from "@/components/graph/EdgeProposal";
import { NodeDetail } from "@/components/graph/NodeDetail";
import { FragmentCard } from "@/components/graph/FragmentCard";
import { GraphDiff } from "@/components/graph/GraphDiff";
import type { DAGNode, DAGEdge } from "@/components/graph/DAGCanvas";
import type { EdgeProposalData } from "@/components/graph/EdgeProposal";

// ── Form schema ───────────────────────────────────────────────────────────────

const sessionSchema = z.object({
  description: z
    .string()
    .min(5, "Please describe the problem in at least 5 characters")
    .max(2000),
  activeFragmentId: z.string().optional(),
});

type SessionFormValues = z.infer<typeof sessionSchema>;

// ── Component ─────────────────────────────────────────────────────────────────

export default function AnalystPage() {
  const [isThinking, setIsThinking] = useState(false);
  const [thinkingStage, setThinkingStage] = useState<string>("Initialising...");
  const [sessionState, setSessionState] = useState<SessionStateResponse | null>(null);
  const [selectedNode, setSelectedNode] = useState<DAGNode | null>(null);
  const [activeFragment, setActiveFragment] = useState<string | null>(null);
  const [rightTab, setRightTab] = useState<"proposals" | "detail" | "diff">("proposals");
  const [sessionError, setSessionError] = useState<string | null>(null);

  const { register, handleSubmit, formState: { errors } } = useForm<SessionFormValues>({
    resolver: zodResolver(sessionSchema),
    defaultValues: { description: "" },
  });

  const fragmentsQuery = useQuery({
    queryKey: ["semantic-fragments"],
    queryFn: async () => {
      const raw = await semanticApi.fragments();
      return raw.map(toFragmentCardData);
    },
  });

  const fragments = fragmentsQuery.data ?? [];

  // ── Mock canvas data derived from session state ───────────────────────────

  const dagNodes: DAGNode[] = sessionState
    ? [
        {
          id: "src-1",
          label: "Root Cause",
          nodeType: "CausalEntity",
          domain: "devops",
          approvalStatus: "DRAFT",
          confidence: sessionState.overall_confidence,
          x: 0.1,
          y: 0.5,
        },
        {
          id: "tgt-1",
          label: "Observable Effect",
          nodeType: "Outcome",
          domain: "devops",
          approvalStatus: "DRAFT",
          confidence: sessionState.overall_confidence * 0.9,
          x: 0.7,
          y: 0.5,
        },
      ]
    : [];

  const dagEdges: DAGEdge[] = sessionState
    ? [
        {
          id: "edge-1",
          sourceId: "src-1",
          targetId: "tgt-1",
          edgeType: "CAUSES",
          confidence: sessionState.overall_confidence,
          approvalStatus: "DRAFT",
        },
      ]
    : [];

  const edgeProposals: EdgeProposalData[] = (sessionState?.recommendations ?? []).map(
    (rec, i) => ({
      id: `proposal-${i}`,
      sourceLabel: "Root Cause Candidate",
      targetLabel: "Observed Outcome",
      edgeType: "CAUSES",
      confidence: rec.confidence,
      rationale: rec.rationale,
      evidenceCitations: [],
      proposedBy: sessionState?.session_id ?? "agent",
      proposedAt: new Date().toISOString(),
      guardianVerdict: sessionState?.guardian_verdict ?? "PENDING",
      policyRulesCited: sessionState?.policy_rules_cited ?? [],
    })
  );

  // ── Session run ───────────────────────────────────────────────────────────

  const onSubmit = useCallback(
    async (values: SessionFormValues) => {
      // UX contract: setIsThinking(true) synchronously BEFORE any await
      setIsThinking(true);
      setThinkingStage("Classifying domain...");
      setSessionError(null);
      setSelectedNode(null);

      try {
        setThinkingStage("Running causal discovery...");

        const data = await sessionApi.run({
          description: values.description,
          active_graph_fragment_id: activeFragment,
        });

        setThinkingStage("Guardian check...");
        setSessionState(data);

        // Auto-switch right panel to proposals if we got recommendations
        if (data.recommendation_count > 0) {
          setRightTab("proposals");
        }
      } catch (err) {
        setSessionError(
          err instanceof CausalApiError
            ? `API error ${err.status}: ${err.message}`
            : err instanceof Error
            ? err.message
            : "Unknown error during session run"
        );
      } finally {
        setIsThinking(false);
      }
    },
    [activeFragment]
  );

  const isChaotic = sessionState?.cynefin_domain === "CHAOTIC";

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-20 flex items-center gap-4 border-b border-border bg-background/95 px-4 py-2.5 backdrop-blur-sm">
        <span className="font-bold text-sm text-foreground">Causal Platform</span>
        <span className="text-sm text-muted-foreground">Analyst Workbench</span>

        {sessionState && (
          <div className="flex items-center gap-2 ml-2">
            <CynefinBadge domain={sessionState.cynefin_domain} size="sm" />
            <GuardianVerdictBadge verdict={sessionState.guardian_verdict} size="sm" />
            <EpistemicConfidenceBadge score={sessionState.overall_confidence} size="sm" />
          </div>
        )}

        <div className="ml-auto">
          <PersonaSelector layout="dropdown" className="w-48" />
        </div>
      </header>

      {/* ── CHAOTIC escalation banner ───────────────────────────────────────── */}
        {isChaotic && (
        <EscalationBanner
          incidentId={sessionState!.session_id}
        />
      )}

      {/* ── Main layout ────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Left: Fragment library ──────────────────────────────────────── */}
        <aside
          className="w-64 shrink-0 flex flex-col border-r border-border bg-background overflow-y-auto"
          aria-label="Graph fragment library"
        >
          <div className="flex items-center gap-1.5 px-3 py-2.5 border-b border-border">
            <Layers className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-xs font-semibold text-foreground">Fragment Library</span>
          </div>
          <div className="p-2 space-y-2 flex-1">
            {fragmentsQuery.isLoading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground gap-1.5">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-xs">Loading…</span>
              </div>
            ) : fragments.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-6 px-2">
                No fragments yet. Run a session to generate causal models.
              </p>
            ) : (
              fragments.map((fragment) => (
                <FragmentCard
                  key={fragment.id}
                  fragment={fragment}
                  isActive={activeFragment === fragment.id}
                  onUseInSession={(id) => { setActiveFragment(id); }}
                  onView={(id) => { setActiveFragment(id); }}
                  onRequestReview={(id) => { console.log("Request review for", id); }}
                />
              ))
            )}
          </div>
        </aside>

        {/* ── Centre: Session input + DAG Canvas ──────────────────────────── */}
        <main className="flex flex-1 flex-col overflow-hidden min-w-0">
          {/* Session input form */}
          <div className="border-b border-border bg-background px-4 py-3">
            <form
              onSubmit={handleSubmit(onSubmit)}
              className="flex items-start gap-2"
              data-testid="session-form"
            >
              <div className="flex-1 min-w-0">
                <textarea
                  {...register("description")}
                  placeholder="Describe the problem to analyse… (e.g. 'High p99 latency on checkout after deployment')"
                  rows={2}
                  disabled={isThinking}
                  className={cn(
                    "w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                    "placeholder:text-muted-foreground focus-visible:outline-none",
                    "focus-visible:ring-1 focus-visible:ring-ring resize-none",
                    "disabled:cursor-not-allowed disabled:opacity-50",
                    errors.description && "border-destructive"
                  )}
                  aria-label="Problem description"
                />
                {errors.description && (
                  <p className="mt-1 text-xs text-destructive">
                    {errors.description.message}
                  </p>
                )}
              </div>
              <button
                type="submit"
                disabled={isThinking}
                data-testid="run-session-button"
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium",
                  "bg-primary text-primary-foreground hover:bg-primary/90",
                  "disabled:opacity-50 disabled:cursor-not-allowed transition-colors shrink-0"
                )}
              >
                {isThinking ? (
                  <RefreshCw className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                Analyse
              </button>
            </form>

            {/* ThinkingIndicator — renders synchronously before API response */}
            {isThinking && (
              <div className="mt-2">
                <ThinkingIndicator
                  stage={thinkingStage}
                  cynefinDomain={sessionState?.cynefin_domain}
                />
              </div>
            )}

            {sessionError && (
              <p
                role="alert"
                className="mt-2 text-xs text-destructive"
              >
                {sessionError}
              </p>
            )}
          </div>

          {/* DAG Canvas */}
          <div className="flex-1 overflow-auto p-4">
            {sessionState && !isChaotic ? (
              <DAGCanvas
                nodes={dagNodes}
                edges={dagEdges}
                cynefinDomain={sessionState.cynefin_domain}
                onNodeSelect={(node) => {
                  setSelectedNode(node);
                  setRightTab("detail");
                }}
                width={700}
                height={400}
              />
            ) : !sessionState ? (
              <div className="flex h-full items-center justify-center text-center">
                <div className="space-y-2">
                  <GitBranch className="mx-auto h-8 w-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">
                    Enter a problem description above and click Analyse to build a causal graph.
                  </p>
                  {activeFragment && (
                    <p className="text-xs text-muted-foreground">
                      Fragment context: {fragments.find(f => f.id === activeFragment)?.name ?? activeFragment}
                    </p>
                  )}
                </div>
              </div>
            ) : null}
          </div>

          {/* Explanation panel */}
          {sessionState?.draft_explanation && !isChaotic && (
            <div className="border-t border-border bg-muted/30 px-4 py-2.5">
              <p className="text-xs text-muted-foreground leading-relaxed">
                <span className="font-medium text-foreground">Analysis: </span>
                {sessionState.draft_explanation}
              </p>
            </div>
          )}
        </main>

        {/* ── Right: Proposals / Node detail / Diff ───────────────────────── */}
        <aside
          className="w-80 shrink-0 flex flex-col border-l border-border bg-background overflow-hidden"
          aria-label="Edge proposals and details"
        >
          {/* Tab bar */}
          <div className="flex items-center border-b border-border px-2 pt-1">
            {(
              [
                { key: "proposals", label: "Proposals", count: edgeProposals.length },
                { key: "detail", label: "Node", count: undefined },
                { key: "diff", label: "Diff", count: undefined },
              ] as const
            ).map(({ key, label, count }) => (
              <button
                key={key}
                type="button"
                onClick={() => setRightTab(key)}
                className={cn(
                  "px-3 py-2 text-xs font-medium transition-colors",
                  "border-b-2 -mb-px",
                  rightTab === key
                    ? "border-primary text-primary"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
                aria-selected={rightTab === key}
                role="tab"
              >
                {label}
                {count !== undefined && count > 0 && (
                  <span className="ml-1 rounded-full bg-primary/10 text-primary px-1.5 py-0.5 text-[10px]">
                    {count}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-3">
            {/* Proposals tab */}
            {rightTab === "proposals" && (
              <div className="space-y-2">
                {edgeProposals.length === 0 ? (
                  <p className="text-xs text-muted-foreground text-center pt-8">
                    No edge proposals yet. Run an analysis to generate candidates.
                  </p>
                ) : (
                  edgeProposals.map((proposal) => (
                    <EdgeProposal
                      key={proposal.id}
                      proposal={proposal}
                      canApprove={false}
                      onViewDetails={() => setRightTab("detail")}
                    />
                  ))
                )}
              </div>
            )}

            {/* Node detail tab */}
            {rightTab === "detail" && (
              selectedNode ? (
                <NodeDetail
                  node={selectedNode}
                  allEdges={dagEdges}
                  allNodes={dagNodes}
                  guardianVerdict={sessionState?.guardian_verdict ?? "PENDING"}
                  description={`Root cause candidate identified by CausalDiscoveryAgent in ${selectedNode.domain} domain.`}
                  onClose={() => {
                    setSelectedNode(null);
                    setRightTab("proposals");
                  }}
                  onNavigateTo={(nodeId) => {
                    const node = dagNodes.find(n => n.id === nodeId);
                    if (node) setSelectedNode(node);
                  }}
                />
              ) : (
                <p className="text-xs text-muted-foreground text-center pt-8">
                  Select a node in the DAG canvas to view its details.
                </p>
              )
            )}

            {/* Diff tab */}
            {rightTab === "diff" && (
              sessionState ? (
                <GraphDiff
                  nodes={dagNodes.map(n => ({ ...n, diffStatus: "added" as const }))}
                  edges={dagEdges.map(e => ({ ...e, diffStatus: "added" as const }))}
                  fromLabel="Previous"
                  toLabel="Current session"
                />
              ) : (
                <p className="text-xs text-muted-foreground text-center pt-8">
                  Run an analysis to see the graph diff.
                </p>
              )
            )}
          </div>

          {/* Session metadata footer */}
          {sessionState && (
            <div className="border-t border-border px-3 py-2 space-y-0.5">
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>Session</span>
                <span className="font-mono">{sessionState.session_id.slice(0, 8)}…</span>
              </div>
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>Cycles</span>
                <span>{sessionState.cycle_count}</span>
              </div>
              {sessionState.requires_human_review && (
                <p className="text-[10px] text-amber-600 dark:text-amber-400 font-medium">
                  ⚠ Human review required
                </p>
              )}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
