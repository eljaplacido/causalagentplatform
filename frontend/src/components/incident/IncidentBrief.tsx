"use client";

/**
 * IncidentBrief — Primary incident surface (US-11).
 *
 * Single screen showing:
 *   - CynefinBadge (domain + confidence)
 *   - Top causal hypotheses list
 *   - Proposed interventions
 *   - EscalationBanner when CHAOTIC domain
 *   - ThinkingIndicator while loading
 *
 * UX contract: CHAOTIC domain suppresses AI recommendations entirely.
 */

import { useState, useEffect, useRef } from "react";
import { Clock, AlertCircle, RefreshCw } from "lucide-react";
import { cn, formatTimestamp } from "@/lib/utils";
import { ThinkingIndicator } from "@/components/shared/ThinkingIndicator";
import { CynefinBadge } from "@/components/shared/CynefinBadge";
import { EpistemicConfidenceBadge } from "@/components/shared/EpistemicConfidenceBadge";
import { CausalHypothesisList } from "./CausalHypothesisList";
import { InterventionCard } from "./InterventionCard";
import { EscalationBanner } from "./EscalationBanner";
import type { Incident, IncidentSeverity } from "@/lib/types";

export interface IncidentBriefProps {
  incident: Incident | null;
  isLoading: boolean;
  loadingStage?: string;
  loadingElapsed?: number;
  onApproveIntervention: (
    incidentId: string,
    interventionId: string,
    reason: string
  ) => Promise<void>;
  onRejectIntervention: (
    incidentId: string,
    interventionId: string,
    reason: string
  ) => Promise<void>;
  onEscalate?: (incidentId: string | undefined) => void;
  className?: string;
}

const SEVERITY_CONFIG: Record<IncidentSeverity, { label: string; class: string }> = {
  P0: { label: "P0", class: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300 border-red-300 dark:border-red-800" },
  P1: { label: "P1", class: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300 border-orange-300 dark:border-orange-800" },
  P2: { label: "P2", class: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-300 border-yellow-300 dark:border-yellow-800" },
  P3: { label: "P3", class: "bg-zinc-100 text-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 border-zinc-300 dark:border-zinc-700" },
};

/** A severity outside P0-P3 is shown as sent, neutrally styled. */
function severityConfig(severity: string): { label: string; class: string } {
  return Object.prototype.hasOwnProperty.call(SEVERITY_CONFIG, severity)
    ? SEVERITY_CONFIG[severity as IncidentSeverity]
    : {
        label: severity || "—",
        class: "bg-muted text-muted-foreground border-border",
      };
}

export function IncidentBrief({
  incident,
  isLoading,
  loadingStage = "Classifying domain...",
  loadingElapsed = 0,
  onApproveIntervention,
  onRejectIntervention,
  onEscalate,
  className,
}: IncidentBriefProps) {
  // ThinkingIndicator elapsed time tracked locally
  const [elapsed, setElapsed] = useState(loadingElapsed);
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (isLoading) {
      if (!startedAtRef.current) {
        startedAtRef.current = Date.now();
      }
      const tick = setInterval(() => {
        setElapsed(Date.now() - (startedAtRef.current ?? Date.now()));
      }, 100);
      return () => clearInterval(tick);
    } else {
      startedAtRef.current = null;
      setElapsed(0);
    }
  }, [isLoading]);

  const isChaotic = incident?.cynefinDomain === "CHAOTIC";

  return (
    <section
      data-testid="incident-brief"
      aria-label="Incident brief"
      className={cn("space-y-4", className)}
    >
      {/* ThinkingIndicator — shown while loading */}
      {isLoading && (
        <ThinkingIndicator
          stage={loadingStage}
          elapsed={elapsed}
          cynefinDomain={incident?.cynefinDomain}
          cynefinConfidence={incident?.domainConfidence}
        />
      )}

      {/* Escalation banner — CHAOTIC domain */}
      {isChaotic && (
        <EscalationBanner
          incidentId={incident?.id}
          onEscalate={onEscalate}
        />
      )}

      {incident && (
        <>
          {/* Header */}
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <span
                  data-testid="incident-severity"
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-xs font-bold",
                    severityConfig(incident.severity).class
                  )}
                >
                  {severityConfig(incident.severity).label}
                </span>
                <span
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-xs font-medium",
                    incident.status === "ACTIVE"
                      ? "border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300"
                      : incident.status === "INVESTIGATING"
                      ? "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
                      : "border-green-300 bg-green-50 text-green-700 dark:border-green-800 dark:bg-green-950 dark:text-green-300"
                  )}
                >
                  {incident.status}
                </span>
              </div>
              <h2 className="text-xl font-bold text-foreground">
                {incident.title}
              </h2>
              <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" aria-hidden="true" />
                  Acknowledged: {formatTimestamp(incident.acknowledgedAt)}
                </span>
                <span className="flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" aria-hidden="true" />
                  Affected: {incident.affectedServices.join(", ")}
                </span>
              </div>
            </div>

            {/* Cynefin badge — Level 0 */}
            {incident.cynefinDomain && (
              <CynefinBadge
                domain={incident.cynefinDomain}
                confidence={incident.domainConfidence ?? 0}
                size="lg"
              />
            )}
          </div>

          {/* Epistemic confidence — shown if EpistemicState available */}
          {incident.epistemicState && (
            <EpistemicConfidenceBadge
              overallConfidence={incident.epistemicState.overallConfidence}
              hallucinationRiskScore={
                incident.epistemicState.hallucinationRiskScore
              }
            />
          )}

          {/* CHAOTIC domain: suppress AI recommendations */}
          {!isChaotic && (
            <>
              {/* Causal hypotheses */}
              {incident.causalHypotheses.length > 0 && (
                <div className="space-y-2">
                  <h3 className="text-sm font-semibold text-foreground">
                    Causal Hypotheses
                  </h3>
                  <CausalHypothesisList
                    hypotheses={incident.causalHypotheses}
                  />
                </div>
              )}

              {/* Proposed interventions */}
              {incident.proposedInterventions.length > 0 && (
                <div className="space-y-2">
                  <h3 className="text-sm font-semibold text-foreground">
                    Proposed Interventions
                  </h3>
                  <div className="space-y-3">
                    {incident.proposedInterventions.map((intervention) => (
                      <InterventionCard
                        key={intervention.id}
                        intervention={intervention}
                        onApprove={(interventionId, reason) =>
                          onApproveIntervention(
                            incident.id,
                            interventionId,
                            reason
                          )
                        }
                        onReject={(interventionId, reason) =>
                          onRejectIntervention(
                            incident.id,
                            interventionId,
                            reason
                          )
                        }
                      />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {/* Chaotic: message explaining why AI recs are suppressed */}
          {isChaotic && (
            <div className="rounded-lg border border-border bg-muted/50 p-4">
              <p className="text-sm text-muted-foreground">
                AI-generated hypotheses and interventions are not shown for
                Chaotic-domain incidents. Stabilize the situation first, then
                reclassify once a cause-effect pattern is visible.
              </p>
            </div>
          )}
        </>
      )}

      {/* Empty state */}
      {!incident && !isLoading && (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border p-12 text-center">
          <RefreshCw className="h-8 w-8 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm text-muted-foreground">
            Select an incident to view its brief.
          </p>
        </div>
      )}
    </section>
  );
}
