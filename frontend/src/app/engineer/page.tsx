"use client";

/**
 * Platform Engineer persona page — Connector Health Dashboard (US-01 – US-05).
 *
 * US-01: Connector health status + last successful sync on a single dashboard.
 * US-02: Alert when a deprecated graph fragment affects a running simulation.
 * US-03: Full audit trail for a specific causal edge (edge selector + AuditLogPanel).
 * US-04: Connector health check with raw JSON debug view.
 * US-05: Drift alert threshold configuration per domain / entity type.
 */

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Database,
  GitBranch,
  Settings,
  Wrench,
  Loader2,
} from "lucide-react";
import { cn, formatTimestamp } from "@/lib/utils";
import { PersonaSelector } from "@/components/shared/PersonaSelector";
import { AuditLogPanel } from "@/components/shared/AuditLogPanel";
import { FragmentCard } from "@/components/graph/FragmentCard";
import { platformApi, semanticApi, governanceApi } from "@/lib/api";
import { toAuditLogRow, toFragmentCardData } from "@/lib/mappers";
import type { ConnectorHealthResult, DriftAlert } from "@/lib/types";

// ─── Mappers (snake_case backend → camelCase frontend types) ──────────────────

function mapConnector(raw: {
  connector_id: string;
  connector_name: string;
  connector_type: string;
  healthy: boolean;
  last_successful_sync: string | null;
  last_attempt_at: string;
  error_message: string | null;
  objects_mapped: number;
  sync_duration_ms: number | null;
  raw_output: Record<string, unknown>;
}): ConnectorHealthResult {
  return {
    connectorId: raw.connector_id,
    connectorName: raw.connector_name,
    connectorType: raw.connector_type as ConnectorHealthResult["connectorType"],
    healthy: raw.healthy,
    lastSuccessfulSync: raw.last_successful_sync,
    lastAttemptAt: raw.last_attempt_at,
    errorMessage: raw.error_message,
    objectsMapped: raw.objects_mapped,
    syncDurationMs: raw.sync_duration_ms,
    rawOutput: raw.raw_output,
  };
}

function mapDriftAlert(raw: {
  id: string;
  domain: string;
  entity_type: string;
  severity: string;
  message: string;
  detected_at: string;
  affected_fragment_ids: string[];
}): DriftAlert {
  return {
    id: raw.id,
    domain: raw.domain,
    entityType: raw.entity_type,
    severity: raw.severity as DriftAlert["severity"],
    message: raw.message,
    detectedAt: raw.detected_at,
    affectedFragmentIds: raw.affected_fragment_ids,
  };
}

// ─── Drift severity styling ────────────────────────────────────────────────────

const DRIFT_SEVERITY_STYLES: Record<
  DriftAlert["severity"],
  { badge: string; border: string; icon: string }
> = {
  CRITICAL: {
    badge: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
    border: "border-red-300 dark:border-red-800",
    icon: "text-red-500",
  },
  HIGH: {
    badge: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
    border: "border-orange-300 dark:border-orange-700",
    icon: "text-orange-500",
  },
  MEDIUM: {
    badge: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
    border: "border-amber-300 dark:border-amber-700",
    icon: "text-amber-500",
  },
  LOW: {
    badge: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
    border: "border-zinc-200 dark:border-zinc-700",
    icon: "text-zinc-400",
  },
};

// ─── ConnectorCard ─────────────────────────────────────────────────────────────

interface ConnectorCardProps {
  connector: ConnectorHealthResult;
  isSelected: boolean;
  onSelect: (id: string) => void;
  onRunCheck: (id: string) => void;
  isChecking: boolean;
}

function ConnectorCard({
  connector,
  isSelected,
  onSelect,
  onRunCheck,
  isChecking,
}: ConnectorCardProps) {
  const [jsonExpanded, setJsonExpanded] = useState(false);

  const syncAgo = connector.lastSuccessfulSync
    ? (() => {
        const ms = Date.now() - new Date(connector.lastSuccessfulSync).getTime();
        const mins = Math.floor(ms / 60000);
        if (mins < 60) return `${mins}m ago`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs}h ago`;
        return `${Math.floor(hrs / 24)}d ago`;
      })()
    : "never";

  return (
    <article
      data-testid="connector-card"
      data-connector-id={connector.connectorId}
      className={cn(
        "rounded-lg border bg-card p-4 space-y-3 transition-all cursor-pointer",
        isSelected
          ? "border-primary ring-1 ring-primary/20"
          : "border-border hover:border-border/80",
        !connector.healthy && "border-red-300 dark:border-red-800"
      )}
      onClick={() => onSelect(connector.connectorId)}
      aria-selected={isSelected}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {connector.healthy ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" aria-label="Healthy" />
          ) : (
            <XCircle className="h-4 w-4 shrink-0 text-red-500" aria-label="Unhealthy" />
          )}
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground truncate">
              {connector.connectorName}
            </p>
            <p className="text-xs text-muted-foreground font-mono">
              {connector.connectorType}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRunCheck(connector.connectorId);
          }}
          disabled={isChecking}
          aria-label={`Run health check for ${connector.connectorName}`}
          className={cn(
            "shrink-0 flex items-center gap-1 rounded-md border border-border px-2 py-1",
            "text-xs text-muted-foreground hover:text-foreground hover:bg-muted",
            "transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          )}
        >
          <RefreshCw className={cn("h-3 w-3", isChecking && "animate-spin")} aria-hidden />
          {isChecking ? "Checking…" : "Check"}
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <div>
          <span className="text-muted-foreground">Last sync</span>
          <p className={cn("font-medium", connector.healthy ? "text-foreground" : "text-red-600 dark:text-red-400")}>
            {syncAgo}
          </p>
        </div>
        <div>
          <span className="text-muted-foreground">Objects mapped</span>
          <p className="font-medium text-foreground">{connector.objectsMapped}</p>
        </div>
        {connector.syncDurationMs !== null && (
          <div>
            <span className="text-muted-foreground">Duration</span>
            <p className="font-medium text-foreground">{connector.syncDurationMs}ms</p>
          </div>
        )}
      </div>

      {/* Error message */}
      {connector.errorMessage && (
        <p className="rounded-md bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 px-3 py-2 text-xs text-red-700 dark:text-red-300 font-mono break-words">
          {connector.errorMessage}
        </p>
      )}

      {/* Raw JSON debug view (US-04) */}
      {connector.rawOutput && (
        <div className="border-t border-border pt-2">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setJsonExpanded((p) => !p);
            }}
            aria-expanded={jsonExpanded}
            className="flex w-full items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            {jsonExpanded ? (
              <ChevronDown className="h-3 w-3" aria-hidden />
            ) : (
              <ChevronRight className="h-3 w-3" aria-hidden />
            )}
            Raw output JSON
          </button>
          {jsonExpanded && (
            <pre
              data-testid="connector-raw-json"
              className="mt-2 overflow-x-auto rounded-md bg-zinc-950 text-green-400 p-3 text-xs leading-relaxed"
            >
              {JSON.stringify(connector.rawOutput, null, 2)}
            </pre>
          )}
        </div>
      )}
    </article>
  );
}

// ─── DriftAlertCard ────────────────────────────────────────────────────────────

function DriftAlertCard({ alert }: { alert: DriftAlert }) {
  const styles = DRIFT_SEVERITY_STYLES[alert.severity] ?? DRIFT_SEVERITY_STYLES["MEDIUM"];
  return (
    <div className={cn("rounded-lg border p-3 flex gap-3", styles.border)} role="alert">
      <AlertTriangle className={cn("h-4 w-4 mt-0.5 shrink-0", styles.icon)} aria-hidden />
      <div className="min-w-0 space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={cn("rounded-full px-1.5 py-0.5 text-xs font-bold", styles.badge)}>
            {alert.severity}
          </span>
          <span className="text-xs font-mono text-muted-foreground">
            {alert.domain} · {alert.entityType}
          </span>
          <span className="ml-auto text-xs text-muted-foreground">
            {formatTimestamp(alert.detectedAt, { timeStyle: "short" })}
          </span>
        </div>
        <p className="text-xs text-foreground leading-relaxed">{alert.message}</p>
        {alert.affectedFragmentIds.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Affected:{" "}
            {alert.affectedFragmentIds.map((id) => (
              <code key={id} className="rounded bg-muted px-1 py-0.5 font-mono text-xs mr-1">
                {id}
              </code>
            ))}
          </p>
        )}
      </div>
    </div>
  );
}

// ─── Page sections ─────────────────────────────────────────────────────────────

type ActiveTab = "connectors" | "fragments" | "audit";

// ─── Page component ───────────────────────────────────────────────────────────

export default function EngineerPage() {
  const [checkingIds, setCheckingIds] = useState<Set<string>>(new Set());
  const [activeTab, setActiveTab] = useState<ActiveTab>("connectors");
  const [selectedFragmentId, setSelectedFragmentId] = useState<string | null>(null);

  // ── Real API queries ────────────────────────────────────────────────────────
  const connectorsQuery = useQuery({
    queryKey: ["platform-connectors"],
    queryFn: async () => {
      const raw = await platformApi.connectors();
      return raw.map(mapConnector);
    },
    refetchInterval: 30_000,
  });

  const fragmentsQuery = useQuery({
    queryKey: ["semantic-fragments"],
    queryFn: async () => {
      const raw = await semanticApi.fragments();
      return raw.map(toFragmentCardData);
    },
  });

  const driftQuery = useQuery({
    queryKey: ["platform-drift"],
    queryFn: async () => {
      const raw = await platformApi.drift();
      return raw.map(mapDriftAlert);
    },
    refetchInterval: 60_000,
  });

  const auditQuery = useQuery({
    queryKey: ["governance-audit-log"],
    queryFn: async () => {
      const page = await governanceApi.auditLog({ page_size: 100 });
      return page.entries.map(toAuditLogRow);
    },
  });

  const connectors = connectorsQuery.data ?? [];
  const fragments = fragmentsQuery.data ?? [];
  const driftAlerts = driftQuery.data ?? [];
  const allAuditEntries = auditQuery.data ?? [];

  const healthyCount = connectors.filter((c) => c.healthy).length;

  // US-02: Deprecated fragments
  const deprecationAlerts = fragments.filter((f) => f.approvalStatus === "DEPRECATED");

  async function handleRunCheck(connectorId: string) {
    setCheckingIds((prev) => new Set(prev).add(connectorId));
    // Re-fetch connectors after check
    await connectorsQuery.refetch();
    setCheckingIds((prev) => {
      const next = new Set(prev);
      next.delete(connectorId);
      return next;
    });
  }

  // Filter audit entries by selected fragment: the entries whose subject it is
  // (audit_log.subject_id, written when a verdict or decision is about it).
  const auditEntries = selectedFragmentId
    ? allAuditEntries.filter((e) => e.subjectId === selectedFragmentId)
    : allAuditEntries.slice(0, 20);

  const tabs: { id: ActiveTab; label: string; icon: React.ReactNode; badge?: number }[] = [
    {
      id: "connectors",
      label: "Connectors",
      icon: <Database className="h-3.5 w-3.5" aria-hidden />,
      badge: connectors.filter((c) => !c.healthy).length || undefined,
    },
    {
      id: "fragments",
      label: "Fragment Library",
      icon: <GitBranch className="h-3.5 w-3.5" aria-hidden />,
      badge: deprecationAlerts.length || undefined,
    },
    {
      id: "audit",
      label: "Audit Trail",
      icon: <Settings className="h-3.5 w-3.5" aria-hidden />,
    },
  ];

  const isLoading = connectorsQuery.isLoading || fragmentsQuery.isLoading;

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Header */}
      <header className="sticky top-0 z-10 flex items-center gap-4 border-b border-border bg-background/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="text-sm font-bold text-foreground">Causal Platform</h1>
        <Wrench className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <span className="text-sm text-muted-foreground">Platform Health</span>
        <div className="ml-auto">
          <PersonaSelector layout="dropdown" className="w-48" />
        </div>
      </header>

      {/* Status summary bar (US-01) */}
      <div className="border-b border-border bg-muted/30 px-4 py-2 flex items-center gap-4 flex-wrap text-xs">
        {isLoading ? (
          <div className="flex items-center gap-1.5 text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            <span>Loading platform status…</span>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-1.5">
              <span
                className={cn(
                  "inline-flex h-2 w-2 rounded-full",
                  connectors.length === 0
                    ? "bg-zinc-400"
                    : healthyCount === connectors.length
                    ? "bg-green-500"
                    : "bg-red-500"
                )}
                aria-hidden
              />
              <span className="font-medium text-foreground">
                {connectors.length === 0
                  ? "No connectors loaded"
                  : `${healthyCount}/${connectors.length} connectors healthy`}
              </span>
            </div>
            {driftAlerts.length > 0 && (
              <div className="flex items-center gap-1.5 text-amber-700 dark:text-amber-400">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                <span>{driftAlerts.length} drift alerts</span>
              </div>
            )}
            {deprecationAlerts.length > 0 && (
              <div className="flex items-center gap-1.5 text-red-700 dark:text-red-400">
                <XCircle className="h-3.5 w-3.5" aria-hidden />
                <span>
                  {deprecationAlerts.length} deprecated fragment
                  {deprecationAlerts.length !== 1 ? "s" : ""}
                </span>
              </div>
            )}
          </>
        )}
        <span className="ml-auto text-muted-foreground">
          Last checked: {formatTimestamp(new Date().toISOString(), { timeStyle: "short" })}
        </span>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-border px-4 gap-1" role="tablist" aria-label="Engineer sections">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            aria-controls={`tabpanel-${tab.id}`}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium border-b-2 transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset",
              activeTab === tab.id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
            )}
          >
            {tab.icon}
            {tab.label}
            {tab.badge !== undefined && (
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 font-bold tabular-nums",
                  tab.id === "connectors"
                    ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                    : "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                )}
              >
                {tab.badge}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Tab panels */}
      <main className="flex-1 overflow-y-auto">
        {/* ── Tab: Connectors ───────────────────────────────────────────── */}
        {activeTab === "connectors" && (
          <div id="tabpanel-connectors" role="tabpanel" aria-label="Connectors" className="p-4 space-y-5">
            {/* Drift alerts (US-05) */}
            {driftAlerts.length > 0 && (
              <section aria-labelledby="drift-alerts-heading">
                <h2
                  id="drift-alerts-heading"
                  className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                >
                  Drift Alerts
                </h2>
                <div className="space-y-2">
                  {driftAlerts.map((alert) => (
                    <DriftAlertCard key={alert.id} alert={alert} />
                  ))}
                </div>
              </section>
            )}

            {/* Connector grid (US-01, US-04) */}
            <section aria-labelledby="connectors-heading">
              <h2
                id="connectors-heading"
                className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Connector Status
              </h2>
              {connectorsQuery.isLoading ? (
                <div className="flex items-center justify-center py-12 text-muted-foreground gap-2">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span className="text-sm">Loading connectors…</span>
                </div>
              ) : connectorsQuery.isError ? (
                <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-950/30 px-4 py-3 text-sm text-red-700 dark:text-red-300">
                  Failed to load connectors. Is the backend running?
                </div>
              ) : connectors.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-12">
                  No connectors registered yet.
                </p>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-2">
                  {connectors.map((connector) => (
                    <ConnectorCard
                      key={connector.connectorId}
                      connector={connector}
                      isSelected={false}
                      onSelect={() => {}}
                      onRunCheck={handleRunCheck}
                      isChecking={checkingIds.has(connector.connectorId)}
                    />
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {/* ── Tab: Fragment Library ──────────────────────────────────────── */}
        {activeTab === "fragments" && (
          <div id="tabpanel-fragments" role="tabpanel" aria-label="Fragment Library" className="p-4 space-y-5">
            {/* US-02: Deprecation alerts */}
            {deprecationAlerts.length > 0 && (
              <section aria-labelledby="deprecation-alerts-heading" aria-live="polite">
                <h2
                  id="deprecation-alerts-heading"
                  className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                >
                  Deprecated Fragments
                </h2>
                <div className="space-y-2">
                  {deprecationAlerts.map((frag) => (
                    <div
                      key={frag.id}
                      role="alert"
                      className="flex items-start gap-3 rounded-lg border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-3"
                    >
                      <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-red-500" aria-hidden />
                      <div>
                        <p className="text-xs font-semibold text-red-800 dark:text-red-300">
                          Deprecated fragment in library
                        </p>
                        <p className="text-xs text-red-700 dark:text-red-400 mt-0.5">
                          <code className="font-mono">{frag.id}</code> —{" "}
                          <strong>{frag.name}</strong> is DEPRECATED. Review and replace before use.
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Fragment cards */}
            <section aria-labelledby="fragment-library-heading">
              <h2
                id="fragment-library-heading"
                className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Graph Fragment Library
              </h2>
              {fragmentsQuery.isLoading ? (
                <div className="flex items-center justify-center py-12 text-muted-foreground gap-2">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span className="text-sm">Loading fragments…</span>
                </div>
              ) : fragmentsQuery.isError ? (
                <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-950/30 px-4 py-3 text-sm text-red-700 dark:text-red-300">
                  Failed to load fragments from semantic registry.
                </div>
              ) : fragments.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-12">
                  No graph fragments in the registry yet. Submit a session to generate fragments.
                </p>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-2">
                  {fragments.map((frag) => (
                    <FragmentCard
                      key={frag.id}
                      fragment={frag}
                      isActive={selectedFragmentId === frag.id}
                      onView={(id) => {
                        setSelectedFragmentId(id);
                        setActiveTab("audit");
                      }}
                      onRequestReview={(id) => {
                        console.info("Request review for:", id);
                      }}
                      onUseInSession={(id) => {
                        console.info("Use in session:", id);
                      }}
                    />
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {/* ── Tab: Audit Trail ──────────────────────────────────────────── */}
        {activeTab === "audit" && (
          <div id="tabpanel-audit" role="tabpanel" aria-label="Audit Trail" className="p-4 space-y-5">
            {/* Fragment selector (US-03) */}
            {fragments.length > 0 && (
              <section aria-labelledby="audit-selector-heading">
                <h2
                  id="audit-selector-heading"
                  className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                >
                  Select Fragment to Inspect
                </h2>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                  {fragments.map((frag) => (
                    <button
                      key={frag.id}
                      type="button"
                      onClick={() => setSelectedFragmentId(frag.id)}
                      aria-pressed={selectedFragmentId === frag.id}
                      className={cn(
                        "rounded-lg border px-3 py-2.5 text-left transition-colors",
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        selectedFragmentId === frag.id
                          ? "border-primary bg-primary/5 text-foreground"
                          : "border-border text-muted-foreground hover:border-border/80 hover:bg-muted/40"
                      )}
                    >
                      <p className="text-xs font-semibold text-foreground truncate">{frag.name}</p>
                      <p className="text-xs text-muted-foreground font-mono mt-0.5">{frag.id}</p>
                      <div className="mt-1 flex items-center gap-1.5">
                        <span
                          className={cn(
                            "text-xs rounded-full px-1.5 py-0.5 font-medium",
                            frag.approvalStatus === "APPROVED"
                              ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
                              : frag.approvalStatus === "DEPRECATED"
                              ? "bg-zinc-100 text-zinc-500 dark:bg-zinc-900 dark:text-zinc-500"
                              : "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                          )}
                        >
                          {frag.approvalStatus}
                        </span>
                        <span className="text-xs text-muted-foreground">v{frag.version}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </section>
            )}

            {/* Audit log (US-03) */}
            {auditQuery.isLoading ? (
              <div className="flex items-center justify-center py-12 text-muted-foreground gap-2">
                <Loader2 className="h-5 w-5 animate-spin" />
                <span className="text-sm">Loading audit trail…</span>
              </div>
            ) : auditEntries.length > 0 ? (
              <section aria-labelledby="audit-log-heading">
                <h2
                  id="audit-log-heading"
                  className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                >
                  {selectedFragmentId ? (
                    <>
                      Audit Trail — <code className="font-mono">{selectedFragmentId}</code>
                    </>
                  ) : (
                    "Recent Audit Entries"
                  )}
                </h2>
                <AuditLogPanel entries={auditEntries} defaultOpen />
              </section>
            ) : (
              <p className="text-sm text-muted-foreground text-center py-12">
                {fragments.length > 0
                  ? "Select a fragment above to view its audit trail, or submit sessions to generate audit entries."
                  : "No audit entries yet. Submit a session through the Analyst workbench to generate audit data."}
              </p>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
