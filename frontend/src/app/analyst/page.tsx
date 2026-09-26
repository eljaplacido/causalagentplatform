"use client";

/**
 * Analyst Workbench — Causal Analyst persona (UIX.md §2.5, US-19–US-23).
 *
 * Layout:
 *   ┌────────────────────────────────────────────────────────────┐
 *   │  Header: title, persona switcher, session status           │
 *   ├────────────┬────────────────────────┬──────────────────────┤
 *   │ Fragment   │   Session result       │  Recommendations     │
 *   │ Library    │   (centre panel)       │  (right panel)       │
 *   │ (left)     │                        │                      │
 *   └────────────┴────────────────────────┴──────────────────────┘
 *
 * UX contract:
 *   - ThinkingIndicator must render synchronously before any await
 *     (setIsThinking(true) called before the API call)
 *   - CHAOTIC domain immediately shows EscalationBanner, suppresses AI analysis
 *   - Only what the backend returned is shown. SessionStateResponse carries
 *     the NUMBER of proposed edges, not the edges, so no graph is drawn: the
 *     centre panel says how many edges were proposed and why none is drawn.
 *     (The page used to draw a two-node "Root Cause → Observable Effect"
 *     graph and edge proposals it made up from the response's confidence.)
 *   - A fragment-library failure is shown as a failure, never as "no fragments".
 *   - One classification: the response's cynefin_domain, labelled
 *     "Classification" in the header. The explanation and recommendations are
 *     the backend's prose; when that prose names a different Cynefin domain the
 *     page says so rather than letting two classifications stand side by side.
 *     A new run clears the previous run's result, so its domain is never shown
 *     beside the new run's progress.
 *   - Recommendations are numbered by their position in the list (the backend's
 *     ranks can repeat). A proposed edge's endpoints are shown as short labelled
 *     ids: the response carries ids, not entity names.
 *
 * Data flow:
 *   1. User enters problem description
 *   2. POST /api/v1/session/run → backend LangGraph pipeline
 *   3. SessionStateResponse → classification, verdict, confidence, explanation,
 *      proposed-edge count and recommendations
 */

import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Play, RefreshCw, GitBranch, Layers, Loader2, AlertTriangle } from "lucide-react";
import { cn, formatConfidence } from "@/lib/utils";
import {
  CausalApiError,
  isNotAuthorised,
  semanticApi,
  sessionApi,
  type SessionStateResponse,
} from "@/lib/api";
import { toFragmentCardData } from "@/lib/mappers";
import { ThinkingIndicator } from "@/components/shared/ThinkingIndicator";
import { PersonaSelector } from "@/components/shared/PersonaSelector";
import { CynefinBadge } from "@/components/shared/CynefinBadge";
import { GuardianVerdictBadge } from "@/components/shared/GuardianVerdictBadge";
import { EpistemicConfidenceBadge } from "@/components/shared/EpistemicConfidenceBadge";
import { EscalationBanner } from "@/components/incident/EscalationBanner";
import { FragmentCard } from "@/components/graph/FragmentCard";

// ── Form schema ───────────────────────────────────────────────────────────────

const sessionSchema = z.object({
  description: z
    .string()
    .min(5, "Please describe the problem in at least 5 characters")
    .max(2000),
  activeFragmentId: z.string().optional(),
});

type SessionFormValues = z.infer<typeof sessionSchema>;


// ── Helpers ───────────────────────────────────────────────────────────────────

const CYNEFIN_DOMAINS = ["CLEAR", "COMPLICATED", "COMPLEX", "CHAOTIC", "DISORDER"] as const;

/**
 * The Cynefin domains the backend's prose names that are not the run's
 * classification. The prose is the backend's to write; the page only says when
 * it disagrees with the classification field.
 */
function domainsNamedOtherThan(
  classification: string,
  texts: readonly string[]
): string[] {
  const named = new Set<string>();
  for (const text of texts) {
    for (const d of CYNEFIN_DOMAINS) {
      if (d !== classification && new RegExp(`\\b${d}\\b`).test(text)) named.add(d);
    }
  }
  return CYNEFIN_DOMAINS.filter((d) => named.has(d));
}

const PROPOSE_EDGE = /^Propose edge: (\S+) → (\S+) \(([^)]+)\)$/;

/** An id cut to its first 8 characters, labelled, with the full id on hover. */
function ShortId({ id }: { id: string }) {
  return (
    <span title={id} className="font-mono">
      entity id {id.length > 8 ? `${id.slice(0, 8)}…` : id}
    </span>
  );
}

/**
 * A recommendation's action as sent, except that a proposed edge's endpoints
 * (the backend sends "Propose edge: <id> → <id> (<type>)") are shown as short
 * labelled ids: the response carries no entity names.
 */
function RecommendationAction({ action }: { action: string }) {
  const edge = PROPOSE_EDGE.exec(action);
  if (!edge) return <>{action}</>;
  const [, source, target, edgeType] = edge;
  return (
    <>
      Propose edge: <ShortId id={source!} /> → <ShortId id={target!} /> ({edgeType})
      <span className="block text-[10px] font-normal text-muted-foreground">
        The session response names the endpoints by id only.
      </span>
    </>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function AnalystPage() {
  const [isThinking, setIsThinking] = useState(false);
  const [thinkingStage, setThinkingStage] = useState<string>("Initialising...");
  const [sessionState, setSessionState] = useState<SessionStateResponse | null>(null);
  const [activeFragment, setActiveFragment] = useState<string | null>(null);
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

  const fragmentsError = fragmentsQuery.isError
    ? isNotAuthorised(fragmentsQuery.error)
      ? `Not authorised to read graph fragments: ${fragmentsQuery.error.message}`
      : `Graph fragments could not be loaded: ${
          fragmentsQuery.error instanceof Error ? fragmentsQuery.error.message : "unknown error"
        }`
    : null;

  const recommendations = sessionState?.recommendations ?? [];

  // ── Session run ───────────────────────────────────────────────────────────

  const onSubmit = useCallback(
    async (values: SessionFormValues) => {
      // UX contract: setIsThinking(true) synchronously BEFORE any await
      setIsThinking(true);
      setThinkingStage("Classifying domain...");
      setSessionError(null);
      // The previous run's result (and its classification) is not this run's.
      setSessionState(null);

      try {
        setThinkingStage("Running causal discovery...");

        const data = await sessionApi.run({
          description: values.description,
          active_graph_fragment_id: activeFragment,
        });

        setThinkingStage("Guardian check...");
        setSessionState(data);
      } catch (err) {
        setSessionError(
          isNotAuthorised(err)
            ? `Not authorised to run a session: ${(err as CausalApiError).message}`
            : err instanceof CausalApiError
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
  const otherDomainsNamed = sessionState
    ? domainsNamedOtherThan(sessionState.cynefin_domain, [
        sessionState.draft_explanation,
        ...recommendations.flatMap((r) => [r.action, r.rationale]),
      ])
    : [];

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-20 flex items-center gap-4 border-b border-border bg-background/95 px-4 py-2.5 backdrop-blur-sm">
        <h1 className="text-sm font-bold text-foreground">Analyst Workbench</h1>

        {sessionState && (
          <div className="flex items-center gap-2 ml-2">
            <span
              data-testid="session-classification"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground"
            >
              Classification:
              <CynefinBadge
                domain={sessionState.cynefin_domain}
                confidence={sessionState.domain_confidence}
                size="sm"
              />
            </span>
            <GuardianVerdictBadge verdict={sessionState.guardian_verdict} size="sm" />
            <EpistemicConfidenceBadge score={sessionState.overall_confidence} size="sm" />
          </div>
        )}

        <div className="ml-auto">
          <PersonaSelector layout="dropdown" className="w-48 max-w-full" />
        </div>
      </header>

      {/* The backend's prose naming another domain than the classification */}
      {sessionState && otherDomainsNamed.length > 0 && (
        <p
          role="note"
          data-testid="domain-text-mismatch"
          className="flex items-start gap-1.5 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          <span>
            This run was classified {sessionState.cynefin_domain}, but the backend&apos;s
            explanation or recommendations name {otherDomainsNamed.join(", ")}. The
            classification shown is the response&apos;s cynefin_domain; the text below is
            the backend&apos;s, as sent.
          </span>
        </p>
      )}

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
            ) : fragmentsError ? (
              <p
                role="alert"
                data-testid="fragments-error"
                className="flex items-start gap-1.5 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
              >
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                <span>{fragmentsError}</span>
              </p>
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

          {/* Session result. SessionStateResponse carries how many edges were
              proposed, not the edges, so no graph is drawn from it. */}
          <div className="flex-1 overflow-auto p-4">
            {sessionState && !isChaotic ? (
              <div className="flex h-full items-center justify-center text-center">
                <div
                  className="max-w-md space-y-2"
                  data-testid="proposed-edges-summary"
                >
                  <GitBranch className="mx-auto h-8 w-8 text-muted-foreground/40" />
                  <p className="text-sm text-foreground">
                    This session proposed {sessionState.proposed_edge_count} candidate
                    edge{sessionState.proposed_edge_count === 1 ? "" : "s"}.
                  </p>
                  <p className="text-xs text-muted-foreground">
                    The session API returns only the number of proposed edges, not
                    their endpoints, so no causal graph is drawn here.
                  </p>
                </div>
              </div>
            ) : !sessionState ? (
              <div className="flex h-full items-center justify-center text-center">
                <div className="space-y-2">
                  <GitBranch className="mx-auto h-8 w-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">
                    Enter a problem description above and click Analyse to run a session.
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

        {/* ── Right: Recommendations, as the backend sent them ──────────── */}
        <aside
          className="w-80 shrink-0 flex flex-col border-l border-border bg-background overflow-hidden"
          aria-label="Session recommendations"
        >
          <div className="flex items-center gap-1.5 border-b border-border px-3 py-2.5">
            <span className="text-xs font-semibold text-foreground">Recommendations</span>
            {recommendations.length > 0 && (
              <span className="rounded-full bg-primary/10 text-primary px-1.5 py-0.5 text-[10px]">
                {recommendations.length}
              </span>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-3">
            {recommendations.length === 0 || isChaotic ? (
              <p className="text-xs text-muted-foreground text-center pt-8">
                {sessionState
                  ? "This session returned no recommendations."
                  : "Run an analysis to see its recommendations."}
              </p>
            ) : (
              <ol className="space-y-2">
                {recommendations.map((rec, index) => (
                  <li
                    key={`${index}-${rec.action}`}
                    data-testid="session-recommendation"
                    className="rounded-md border border-border p-2.5 text-xs"
                  >
                    <p className="break-words font-medium text-foreground">
                      {index + 1}. <RecommendationAction action={rec.action} />
                    </p>
                    <p className="mt-1 text-muted-foreground">{rec.rationale}</p>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      Confidence {formatConfidence(rec.confidence)} · Risk {rec.risk_tier}
                    </p>
                  </li>
                ))}
              </ol>
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
