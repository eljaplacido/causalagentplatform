"use client";

/**
 * Incident Triage Page — OPERATOR / SRE persona (US-06, US-11).
 *
 * Shows:
 *   - Incident feed (list) on the left
 *   - IncidentBrief panel on the right
 *
 * Submitting a new incident immediately shows ThinkingIndicator (data-testid="submit-incident").
 * ThinkingIndicator appears within 150ms — rendered optimistically before API response.
 *
 * Failure mode:
 *   - On backend error, the incident is NOT silently substituted with a fake record.
 *   - Instead, an explicit error state is shown in the UI (degraded mode).
 */

import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus, Activity, AlertTriangle } from "lucide-react";
import { cn, formatTimestamp } from "@/lib/utils";
import { ThinkingIndicator } from "@/components/shared/ThinkingIndicator";
import { IncidentBrief } from "@/components/incident/IncidentBrief";
import {
  sessionApi,
  interventionApi,
  incidentsApi,
  CausalApiError,
  type IncidentRecord,
  type InterventionDecisionResponse,
} from "@/lib/api";
import type { Incident, ThinkingStage } from "@/lib/types";
import { applyInterventionDecisions } from "@/lib/mappers";


function recordToIncident(record: IncidentRecord): Incident {
  const interventions = record.proposed_interventions.map((raw) => {
    const obj = raw as Record<string, unknown>;
    const partial = obj as Partial<Incident["proposedInterventions"][number]>;
    return {
      id: String(obj["id"] ?? ""),
      title: String(obj["title"] ?? ""),
      description: String(obj["description"] ?? ""),
      confidence: typeof obj["confidence"] === "number" ? (obj["confidence"] as number) : 0,
      riskTier: (obj["riskTier"] as Incident["proposedInterventions"][number]["riskTier"]) ?? "LOW",
      // Fail-closed: a missing verdict is PENDING, never an implied approval.
      guardianVerdict: (obj["guardianVerdict"] as Incident["proposedInterventions"][number]["guardianVerdict"]) ?? "PENDING",
      estimatedEffect: partial.estimatedEffect ?? {
        pointEstimate: 0,
        lowerBound: 0,
        upperBound: 0,
        unit: "% improvement",
        direction: "UNCERTAIN" as const,
      },
      assumptions: partial.assumptions ?? [],
      ...(typeof obj["approvalReason"] === "string" ? { approvalReason: obj["approvalReason"] as string } : {}),
    };
  });

  const hypotheses = record.causal_hypotheses.map((raw) => {
    const obj = raw as Record<string, unknown>;
    return {
      id: String(obj["id"] ?? ""),
      rank: typeof obj["rank"] === "number" ? (obj["rank"] as number) : 1,
      description: String(obj["description"] ?? ""),
      confidence: typeof obj["confidence"] === "number" ? (obj["confidence"] as number) : 0,
      causalChain: [],
      evidenceCitations: [],
    };
  });

  return {
    id: record.id,
    title: record.title,
    affectedServices: record.affected_services,
    severity: record.severity as Incident["severity"],
    status: record.status as Incident["status"],
    acknowledgedAt: record.created_at,
    cynefinDomain: (record.cynefin_domain ?? "DISORDER") as Incident["cynefinDomain"],
    domainConfidence: record.domain_confidence ?? 0,
    causalHypotheses: hypotheses,
    proposedInterventions: interventions,
  };
}


// ─── Submit form schema ────────────────────────────────────────────────────────

const submitIncidentSchema = z.object({
  title: z.string().min(10, "Title must be at least 10 characters"),
  affectedServices: z.string().min(1, "Enter at least one service"),
  severity: z.enum(["SEV1", "SEV2", "SEV3", "SEV4"]),
});

type SubmitIncidentForm = z.infer<typeof submitIncidentSchema>;

// ─── Page component ───────────────────────────────────────────────────────────

export default function IncidentsPage() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);
  const [isThinking, setIsThinking] = useState(false);
  const [thinkingStage, setThinkingStage] = useState<ThinkingStage>(
    "Classifying domain..."
  );
  const [isSubmitOpen, setIsSubmitOpen] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<SubmitIncidentForm>({
    resolver: zodResolver(submitIncidentSchema),
    defaultValues: { severity: "SEV2" },
  });

  const selectedIncident =
    incidents.find((i) => i.id === selectedIncidentId) ?? null;

  useEffect(() => {
    let cancelled = false;
    async function loadIncidents() {
      try {
        // Recorded human decisions come beside the incidents: a decided
        // intervention must not offer Approve/Reject again.
        const [records, decisions] = await Promise.all([
          incidentsApi.list({ limit: 50 }),
          interventionApi.decisions({ limit: 500 }).catch((err: unknown) => {
            console.warn("interventionApi.decisions failed", err instanceof Error ? err.message : String(err));
            return [];
          }),
        ]);
        if (cancelled) return;
        const mapped = records
          .map(recordToIncident)
          .map((inc) => applyInterventionDecisions(inc, decisions));
        setIncidents(mapped);
        if (mapped.length > 0 && !selectedIncidentId) {
          setSelectedIncidentId(mapped[0]!.id);
        }
      } catch (err) {
        // Non-fatal: dashboard still works with in-memory state if the list endpoint is unreachable
        const msg = err instanceof CausalApiError ? err.message : err instanceof Error ? err.message : String(err);
        console.warn("incidentsApi.list failed", msg);
      }
    }
    void loadIncidents();
    return () => {
      cancelled = true;
    };
    // Intentionally fire once on mount — re-running on selection changes would clobber local state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);


  async function onSubmitIncident(data: SubmitIncidentForm) {
    // Clear prior error
    setSubmitError(null);

    // ── UX CONTRACT: ThinkingIndicator must appear within 150ms ──────────────
    // Set isThinking BEFORE the async API call (optimistic, synchronous)
    setIsThinking(true);
    setThinkingStage("Classifying domain...");
    setIsSubmitOpen(false);
    reset();

    const affectedServices = data.affectedServices.split(",").map((s) => s.trim());
    const incidentId = `INC-${String(incidents.length + 1).padStart(3, "0")}`;

    // Map frontend severity (SEV1-4) → backend severity (P0-P3)
    const severityMap: Record<string, string> = {
      SEV1: "P0", SEV2: "P1", SEV3: "P2", SEV4: "P3",
    };

    try {
      setThinkingStage("Analyzing causal paths...");

      const result = await sessionApi.incident({
        incident_id: incidentId,
        title: data.title,
        severity: severityMap[data.severity] ?? "P2",
        affected_services: affectedServices,
        metrics_snapshot: {},
      });

      setThinkingStage("Consulting Guardian...");

      // Map SessionStateResponse → frontend Incident type
      const newIncident: Incident = {
        id: incidentId,
        title: data.title,
        affectedServices,
        severity: data.severity,
        status: result.requires_human_review ? "ACTIVE" : "INVESTIGATING",
        acknowledgedAt: new Date().toISOString(),
        cynefinDomain: result.cynefin_domain as Incident["cynefinDomain"],
        domainConfidence: result.domain_confidence,
        causalHypotheses: result.draft_explanation
          ? [
              {
                id: `hyp-${result.session_id}`,
                rank: 1,
                description: result.draft_explanation,
                confidence: result.overall_confidence,
                causalChain: [],
                evidenceCitations: [],
              },
            ]
          : [],
        proposedInterventions: result.recommendations.map((rec) => ({
          id: `int-${rec.rank}-${result.session_id}`,
          title: rec.action,
          description: rec.rationale,
          estimatedEffect: {
            pointEstimate: 0,
            lowerBound: 0,
            upperBound: 0,
            unit: "% improvement",
            direction: "UNCERTAIN" as const,
          },
          confidence: rec.confidence,
          assumptions: [],
          riskTier: rec.risk_tier as "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
          guardianVerdict: result.guardian_verdict as Incident["proposedInterventions"][0]["guardianVerdict"],
        })),
      };

      setIncidents((prev) => {
        const without = prev.filter((p) => p.id !== newIncident.id);
        return [newIncident, ...without];
      });
      setSelectedIncidentId(newIncident.id);
    } catch (err) {
      // ── Fail-closed: surface the error explicitly, do NOT create a fake incident record
      const errorMsg = err instanceof CausalApiError
        ? err.message
        : err instanceof Error
        ? err.message
        : "Backend unavailable";

      setSubmitError(
        `Incident submission failed: ${errorMsg}. The incident was NOT recorded. Please retry or contact platform engineering.`
      );
    } finally {
      setIsThinking(false);
    }
  }

  /** Reflect a decision in local state — only ever called with a backend-confirmed decision. */
  function applyConfirmedDecision(
    incidentId: string,
    interventionId: string,
    decision: InterventionDecisionResponse["decision"],
    reason: string | undefined
  ) {
    setIncidents((prev) =>
      prev.map((inc) =>
        inc.id !== incidentId
          ? inc
          : {
              ...inc,
              proposedInterventions: inc.proposedInterventions.map((int) =>
                int.id !== interventionId
                  ? int
                  : {
                      ...int,
                      guardianVerdict: decision,
                      humanDecision: decision,
                      ...(reason !== undefined ? { approvalReason: reason } : {}),
                    }
              ),
            }
      )
    );
  }

  /**
   * Fail-closed decision flow: call the backend FIRST and only update the UI
   * with what it recorded. Throws (so InterventionCard shows the error and keeps
   * its reason input) when the decision was not recorded.
   */
  async function decideIntervention(
    incidentId: string,
    interventionId: string,
    attempted: InterventionDecisionResponse["decision"],
    reason: string
  ) {
    const title = incidents
      .find((inc) => inc.id === incidentId)
      ?.proposedInterventions.find((int) => int.id === interventionId)?.title;
    const body = { reason, incident_id: incidentId, intervention_title: title };

    let response: InterventionDecisionResponse;
    try {
      response =
        attempted === "APPROVED"
          ? await interventionApi.approve(interventionId, body)
          : await interventionApi.reject(interventionId, body);
    } catch (err) {
      const msg = err instanceof CausalApiError ? err.message : err instanceof Error ? err.message : "Backend unavailable";
      throw new Error(`${msg.replace(/[.\s]+$/, "")}. The decision was NOT recorded.`);
    }

    // The endpoint is idempotent: an earlier decision wins and is returned as-is.
    applyConfirmedDecision(
      incidentId,
      interventionId,
      response.decision,
      response.already_decided ? undefined : reason
    );
    if (response.already_decided && response.decision !== attempted) {
      throw new Error(
        `This intervention was already ${response.decision} (audit entry ${response.audit_log_id}). The new decision was NOT recorded.`
      );
    }
  }

  async function handleApproveIntervention(
    incidentId: string,
    interventionId: string,
    reason: string
  ) {
    await decideIntervention(incidentId, interventionId, "APPROVED", reason);
  }

  async function handleRejectIntervention(
    incidentId: string,
    interventionId: string,
    reason: string
  ) {
    await decideIntervention(incidentId, interventionId, "REJECTED", reason);
  }

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Top nav */}
      <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-border bg-background/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-sm font-bold text-foreground">Causal Platform</h1>
        <Activity className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <span className="text-sm text-muted-foreground">Incidents</span>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Incident feed sidebar */}
        <aside className="flex w-64 shrink-0 flex-col border-r border-border lg:w-72">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold text-foreground">
              Active Incidents
            </h2>
            <button
              onClick={() => setIsSubmitOpen((p) => !p)}
              data-testid="submit-incident"
              aria-label="Submit new incident"
              aria-expanded={isSubmitOpen}
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-md border border-border",
                "text-muted-foreground hover:bg-muted hover:text-foreground transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              )}
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          {/* Submit incident form */}
          {isSubmitOpen && (
            <form
              onSubmit={handleSubmit(onSubmitIncident)}
              className="space-y-3 border-b border-border bg-muted/30 p-4"
            >
              <div>
                <label
                  htmlFor="incident-title"
                  className="mb-1 block text-xs font-semibold text-foreground"
                >
                  Title
                </label>
                <input
                  id="incident-title"
                  {...register("title")}
                  placeholder="Brief incident description"
                  className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
                {errors.title && (
                  <p className="mt-1 text-xs text-red-600">{errors.title.message}</p>
                )}
              </div>

              <div>
                <label
                  htmlFor="affected-services"
                  className="mb-1 block text-xs font-semibold text-foreground"
                >
                  Affected Services
                </label>
                <input
                  id="affected-services"
                  {...register("affectedServices")}
                  placeholder="service-a, service-b"
                  className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>

              <div>
                <label
                  htmlFor="severity"
                  className="mb-1 block text-xs font-semibold text-foreground"
                >
                  Severity
                </label>
                <select
                  id="severity"
                  {...register("severity")}
                  className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value="SEV1">SEV-1 (Critical)</option>
                  <option value="SEV2">SEV-2 (High)</option>
                  <option value="SEV3">SEV-3 (Medium)</option>
                  <option value="SEV4">SEV-4 (Low)</option>
                </select>
              </div>

              <button
                type="submit"
                className={cn(
                  "w-full rounded-md bg-primary px-3 py-2 text-sm font-semibold text-primary-foreground",
                  "hover:opacity-90 transition-opacity",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                )}
              >
                Submit Incident
              </button>

              {/* Fail-closed: explicit error display */}
              {submitError && (
                <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                  <span>{submitError}</span>
                </div>
              )}
            </form>
          )}

          {/* Global ThinkingIndicator for new submissions */}
          {isThinking && (
            <div className="border-b border-border p-3">
              <ThinkingIndicator
                stage={thinkingStage}
                elapsed={0}
                className="text-xs"
              />
            </div>
          )}

          {/* Incident list */}
          <ul className="flex-1 overflow-y-auto" role="list">
            {incidents.map((incident) => {
              const isSelected = incident.id === selectedIncidentId;
              const hasActiveStatus = incident.status === "ACTIVE";

              return (
                <li key={incident.id}>
                  <button
                    onClick={() => setSelectedIncidentId(incident.id)}
                    aria-current={isSelected ? "true" : undefined}
                    className={cn(
                      "flex w-full flex-col gap-1.5 border-b border-border px-4 py-3 text-left",
                      "hover:bg-muted/40 transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                      isSelected ? "bg-primary/5" : ""
                    )}
                  >
                    <div className="flex items-center gap-2">
                      {hasActiveStatus ? (
                        <span className="h-2 w-2 rounded-full bg-red-500 animate-thinking-pulse" aria-hidden="true" />
                      ) : (
                        <span className="h-2 w-2 rounded-full bg-amber-400" aria-hidden="true" />
                      )}
                      <span className="font-mono text-xs text-muted-foreground">
                        {incident.id}
                      </span>
                      <span
                        className={cn(
                          "ml-auto rounded-full px-1.5 py-0.5 text-xs font-bold",
                          incident.severity === "SEV1"
                            ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                            : incident.severity === "SEV2"
                            ? "bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300"
                            : "bg-muted text-muted-foreground"
                        )}
                      >
                        {incident.severity}
                      </span>
                    </div>
                    <span className="line-clamp-2 text-xs font-medium text-foreground">
                      {incident.title}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {formatTimestamp(incident.acknowledgedAt, { timeStyle: "short" })}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        {/* Incident brief main panel */}
        <main className="flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          <IncidentBrief
            incident={selectedIncident}
            isLoading={isThinking}
            loadingStage={thinkingStage}
            onApproveIntervention={handleApproveIntervention}
            onRejectIntervention={handleRejectIntervention}
          />
        </main>
      </div>
    </div>
  );
}
