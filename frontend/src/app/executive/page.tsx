"use client";

/**
 * Executive / Engineering Manager persona page (US-16 – US-18).
 *
 * US-16: Weekly trend view — session volume and approval rate from real audit data.
 *   Each rate says its window: the tile is this week's, the footer all time; both
 *   are the APPROVED share of audit_log verdicts (every row, as platform.py counts
 *   them), so they differ only by window.
 * US-17: Top recurring Guardian policy rule citations over the platform lifetime.
 * US-18: Fragment library health overview (totals by status).
 *
 * All data loaded from GET /api/v1/platform/metrics (real audit_log queries, no mocks).
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  TrendingDown,
  TrendingUp,
  Minus,
  AlertTriangle,
  Target,
  BarChart3,
  BookOpen,
  ChevronDown,
  ChevronRight,
  Briefcase,
  Loader2,
  GitBranch,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PersonaSelector } from "@/components/shared/PersonaSelector";
import { platformApi } from "@/lib/api";

// ─── Local types ──────────────────────────────────────────────────────────────

interface WeeklyMetric {
  week: string;
  sessionCount: number;
  approvalRate: number;
  escalationCount: number;
}

interface PolicyPattern {
  rank: number;
  ruleId: string;
  description: string;
  occurrences: number;
  lastSeen: string;
}

interface FragmentStats {
  total: number;
  approved: number;
  underReview: number;
  draft: number;
  deprecated: number;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function sessionTrend(metrics: WeeklyMetric[]): "up" | "down" | "flat" {
  if (metrics.length < 2) return "flat";
  const last = metrics[metrics.length - 1]!.sessionCount;
  const prev = metrics[metrics.length - 2]!.sessionCount;
  if (last > prev + 0) return "up";
  if (last < prev) return "down";
  return "flat";
}

function approvalTrend(metrics: WeeklyMetric[]): "up" | "down" | "flat" {
  if (metrics.length < 2) return "flat";
  const last = metrics[metrics.length - 1]!.approvalRate;
  const prev = metrics[metrics.length - 2]!.approvalRate;
  if (last > prev + 0.02) return "up";
  if (last < prev - 0.02) return "down";
  return "flat";
}

// ─── Sub-components ───────────────────────────────────────────────────────────

interface KPICardProps {
  label: string;
  value: string;
  subvalue?: string;
  trend?: "up" | "down" | "flat";
  trendGoodDirection?: "up" | "down";
  className?: string;
  testId?: string;
}

function KPICard({
  label,
  value,
  subvalue,
  trend,
  trendGoodDirection = "down",
  className,
  testId,
}: KPICardProps) {
  const isGood =
    trend !== undefined &&
    ((trend === "down" && trendGoodDirection === "down") ||
      (trend === "up" && trendGoodDirection === "up"));
  const isBad =
    trend !== undefined &&
    ((trend === "up" && trendGoodDirection === "down") ||
      (trend === "down" && trendGoodDirection === "up"));

  const trendColour = isGood
    ? "text-green-600 dark:text-green-400"
    : isBad
    ? "text-red-600 dark:text-red-400"
    : "text-muted-foreground";

  const TrendIcon =
    trend === "down" ? TrendingDown : trend === "up" ? TrendingUp : Minus;

  return (
    <div data-testid={testId} className={cn("min-w-0 rounded-lg border border-border bg-card p-4 space-y-1", className)}>
      <p className="text-xs text-muted-foreground uppercase tracking-wide font-semibold">
        {label}
      </p>
      <div className="flex items-end gap-2">
        <p className="text-2xl font-bold text-foreground tabular-nums">{value}</p>
        {trend && trend !== "flat" && (
          <TrendIcon
            className={cn("h-4 w-4 mb-1 shrink-0", trendColour)}
            aria-label={`Trending ${trend}`}
          />
        )}
      </div>
      {subvalue && <p className="text-xs text-muted-foreground">{subvalue}</p>}
    </div>
  );
}

function PolicyRow({ pattern }: { pattern: PolicyPattern }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <li className="border-b border-border last:border-0">
      <button
        type="button"
        onClick={() => setExpanded((p) => !p)}
        aria-expanded={expanded}
        className={cn(
          "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors",
          "hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        )}
      >
        <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full bg-muted text-xs font-bold text-muted-foreground" aria-hidden>
          {pattern.rank}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground font-mono">{pattern.ruleId}</p>
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted-foreground">{pattern.occurrences} citations</span>
          </div>
        </div>
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground mt-0.5" aria-hidden />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground mt-0.5" aria-hidden />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-3 pl-12">
          <div className="flex items-start gap-2 rounded-md bg-muted/40 p-3">
            <Target className="h-4 w-4 shrink-0 text-primary mt-0.5" aria-hidden />
            <div>
              <p className="text-xs font-semibold text-foreground mb-0.5">Policy rule</p>
              <p className="text-xs text-muted-foreground leading-relaxed">{pattern.description}</p>
            </div>
          </div>
        </div>
      )}
    </li>
  );
}

function SessionSparkline({ metrics }: { metrics: WeeklyMetric[] }) {
  const max = Math.max(...metrics.map((m) => m.sessionCount), 1);
  return (
    <div className="mt-3 space-y-1.5" aria-hidden="true">
      {metrics.map((m) => (
        <div key={m.week} className="flex items-center gap-2 text-xs">
          <span className="w-28 shrink-0 text-muted-foreground text-right">
            {m.week.slice(0, 6)}
          </span>
          <div className="flex-1 h-4 rounded bg-muted overflow-hidden">
            <div
              className="h-full rounded bg-primary/70 transition-all"
              style={{ width: `${(m.sessionCount / max) * 100}%` }}
            />
          </div>
          <span className="w-10 text-muted-foreground tabular-nums">{m.sessionCount}</span>
        </div>
      ))}
    </div>
  );
}

function ApprovalSparkline({ metrics }: { metrics: WeeklyMetric[] }) {
  return (
    <div className="mt-3 space-y-1.5" aria-hidden="true">
      {metrics.map((m) => (
        <div key={m.week} className="flex items-center gap-2 text-xs">
          <span className="w-28 shrink-0 text-muted-foreground text-right">
            {m.week.slice(0, 6)}
          </span>
          <div className="flex-1 h-4 rounded bg-muted overflow-hidden">
            <div
              className={cn(
                "h-full rounded transition-all",
                m.approvalRate >= 0.7
                  ? "bg-green-500/70"
                  : m.approvalRate >= 0.5
                  ? "bg-amber-400/70"
                  : "bg-red-400/70"
              )}
              style={{ width: `${m.approvalRate * 100}%` }}
            />
          </div>
          <span className="w-10 text-muted-foreground tabular-nums">
            {Math.round(m.approvalRate * 100)}%
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── Page component ───────────────────────────────────────────────────────────

export default function ExecutivePage() {
  const metricsQuery = useQuery({
    queryKey: ["platform-metrics"],
    queryFn: () => platformApi.metrics(),
    refetchInterval: 60_000,
  });

  const data = metricsQuery.data;

  const weeklyMetrics: WeeklyMetric[] = (data?.weekly_sessions ?? []).map((w) => ({
    week: w.week,
    sessionCount: w.session_count,
    approvalRate: w.approval_rate,
    escalationCount: w.escalation_count,
  }));

  const policyPatterns: PolicyPattern[] = (data?.policy_patterns ?? []).map((p) => ({
    rank: p.rank,
    ruleId: p.rule_id,
    description: p.description,
    occurrences: p.occurrences,
    lastSeen: p.last_seen,
  }));

  const fragmentStats: FragmentStats = data
    ? {
        total: data.fragment_stats.total,
        approved: data.fragment_stats.approved,
        underReview: data.fragment_stats.under_review,
        draft: data.fragment_stats.draft,
        deprecated: data.fragment_stats.deprecated,
      }
    : { total: 0, approved: 0, underReview: 0, draft: 0, deprecated: 0 };

  const latest = weeklyMetrics[weeklyMetrics.length - 1];
  const sessTrendDir = sessionTrend(weeklyMetrics);
  const approvTrendDir = approvalTrend(weeklyMetrics);

  const totalEscalations = weeklyMetrics.reduce((s, w) => s + w.escalationCount, 0);

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Header */}
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-background/95 px-4 py-3 backdrop-blur-sm">
        <Briefcase className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <h1 className="text-sm font-bold text-foreground">Manager Dashboard</h1>
        <div className="ml-auto">
          <PersonaSelector layout="dropdown" className="w-48 max-w-full" />
        </div>
      </header>

      <main className="flex-1 overflow-y-auto p-4 space-y-8 max-w-5xl mx-auto w-full">
        {metricsQuery.isLoading ? (
          <div className="flex items-center justify-center py-24 text-muted-foreground gap-2">
            <Loader2 className="h-6 w-6 animate-spin" />
            <span>Loading platform metrics…</span>
          </div>
        ) : metricsQuery.isError ? (
          <div className="rounded-lg border border-red-200 bg-red-50 dark:bg-red-950/30 px-4 py-3 text-sm text-red-700 dark:text-red-300 mt-8">
            Failed to load metrics. Ensure the backend is running at {process.env["NEXT_PUBLIC_API_BASE_URL"] ?? "http://localhost:49234"}.
          </div>
        ) : (
          <>
            {/* ── US-16: KPI summary + weekly trend ────────────────────────── */}
            <section aria-labelledby="kpi-heading">
              <h2
                id="kpi-heading"
                className="mb-4 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Weekly Performance — Past 4 Weeks
              </h2>

              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 mb-6">
                <KPICard
                  label="Sessions this week"
                  value={String(latest?.sessionCount ?? 0)}
                  subvalue="causal analysis sessions"
                  trend={sessTrendDir}
                  trendGoodDirection="up"
                />
                <KPICard
                  testId="kpi-approval-rate"
                  label="Approval rate · this week"
                  value={`${Math.round((latest?.approvalRate ?? 0) * 100)}%`}
                  subvalue="APPROVED share of this week's audit-log verdicts"
                  trend={approvTrendDir}
                  trendGoodDirection="up"
                />
                <KPICard
                  label="Total sessions"
                  value={String(data?.total_sessions_all_time ?? 0)}
                  subvalue="all time"
                />
                <KPICard
                  testId="kpi-escalations"
                  label="Escalations (4w)"
                  value={String(totalEscalations)}
                  subvalue="REQUIRES_ESCALATION verdicts, last 4 weeks"
                  trend={totalEscalations > 0 ? "up" : "flat"}
                  trendGoodDirection="down"
                />
              </div>

              {weeklyMetrics.length > 0 && (
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="rounded-lg border border-border bg-card p-4">
                    <div className="flex items-center gap-2">
                      <BarChart3 className="h-4 w-4 text-muted-foreground" aria-hidden />
                      <h3 className="text-xs font-semibold text-foreground">
                        Session Volume Trend
                      </h3>
                      <span
                        className={cn(
                          "ml-auto text-xs font-medium",
                          sessTrendDir === "up"
                            ? "text-green-600 dark:text-green-400"
                            : sessTrendDir === "down"
                            ? "text-red-600 dark:text-red-400"
                            : "text-muted-foreground"
                        )}
                      >
                        {sessTrendDir === "up" ? "Growing" : sessTrendDir === "down" ? "Declining" : "Stable"}
                      </span>
                    </div>
                    <SessionSparkline metrics={weeklyMetrics} />
                  </div>

                  <div className="rounded-lg border border-border bg-card p-4">
                    <div className="flex items-center gap-2">
                      <Target className="h-4 w-4 text-muted-foreground" aria-hidden />
                      <h3 className="text-xs font-semibold text-foreground">
                        Guardian Approval Rate Trend
                      </h3>
                      <span
                        className={cn(
                          "ml-auto text-xs font-medium",
                          approvTrendDir === "up"
                            ? "text-green-600 dark:text-green-400"
                            : approvTrendDir === "down"
                            ? "text-red-600 dark:text-red-400"
                            : "text-muted-foreground"
                        )}
                      >
                        {approvTrendDir === "up" ? "Improving" : approvTrendDir === "down" ? "Declining" : "Stable"}
                      </span>
                    </div>
                    <ApprovalSparkline metrics={weeklyMetrics} />
                  </div>
                </div>
              )}
            </section>

            {/* ── US-17: Top cited policy rules ────────────────────────────── */}
            <section aria-labelledby="patterns-heading">
              <div className="mb-3 flex items-center justify-between">
                <h2
                  id="patterns-heading"
                  className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                >
                  Top Guardian Policy Rules — All Time
                </h2>
                <span className="text-xs text-muted-foreground">
                  {data?.total_audit_entries ?? 0} total audit entries
                </span>
              </div>
              {policyPatterns.length === 0 ? (
                <div className="rounded-lg border border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
                  No policy citations yet. Run sessions through the Analyst workbench to generate governance data.
                </div>
              ) : (
                <div className="rounded-lg border border-border bg-card overflow-hidden">
                  <ul role="list">
                    {policyPatterns.map((pattern) => (
                      <PolicyRow key={pattern.ruleId} pattern={pattern} />
                    ))}
                  </ul>
                </div>
              )}
              <p className="mt-2 text-xs text-muted-foreground">
                Expand each rule to see its full description and governance context.
              </p>
            </section>

            {/* ── US-18: Fragment library health ──────────────────────────── */}
            <section aria-labelledby="fragments-heading">
              <div className="mb-3 flex items-center justify-between flex-wrap gap-2">
                <div>
                  <h2
                    id="fragments-heading"
                    className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                  >
                    Causal Model Fragment Health
                  </h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {fragmentStats.total} total fragments in semantic registry
                  </p>
                </div>
                {fragmentStats.deprecated > 0 && (
                  <div className="flex items-center gap-1.5 rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/30 px-3 py-1.5">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
                    <span className="text-xs text-amber-700 dark:text-amber-300 font-medium">
                      {fragmentStats.deprecated} deprecated fragment{fragmentStats.deprecated !== 1 ? "s" : ""}
                    </span>
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-border bg-card overflow-hidden">
                <div className="border-b border-border bg-muted/30 px-4 py-2 flex items-center gap-2">
                  <GitBranch className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                    Fragment registry status breakdown
                  </span>
                </div>
                {fragmentStats.total === 0 ? (
                  <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                    No fragments in registry yet. Create causal models in the Analyst workbench.
                  </div>
                ) : (
                  <div className="grid grid-cols-2 sm:grid-cols-4 divide-x divide-border">
                    {[
                      { label: "Approved", value: fragmentStats.approved, colour: "text-green-600 dark:text-green-400" },
                      { label: "Under Review", value: fragmentStats.underReview, colour: "text-amber-600 dark:text-amber-400" },
                      { label: "Draft", value: fragmentStats.draft, colour: "text-muted-foreground" },
                      { label: "Deprecated", value: fragmentStats.deprecated, colour: "text-red-600 dark:text-red-400" },
                    ].map((stat) => (
                      <div key={stat.label} className="px-4 py-4 text-center">
                        <p className={cn("text-2xl font-bold tabular-nums", stat.colour)}>
                          {stat.value}
                        </p>
                        <p className="text-xs text-muted-foreground mt-0.5">{stat.label}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {data?.overall_approval_rate !== undefined && data.overall_approval_rate > 0 && (
                <div className="mt-3 flex items-center gap-2">
                  <BookOpen className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                  <p className="text-xs text-muted-foreground" data-testid="overall-approval-rate">
                    Approval rate, all time (APPROVED share of every audit-log verdict):{" "}
                    <strong className="text-foreground">
                      {Math.round(data.overall_approval_rate * 100)}%
                    </strong>{" "}
                    · Last activity:{" "}
                    {data.last_activity_at
                      ? new Date(data.last_activity_at).toLocaleString()
                      : "none recorded"}
                  </p>
                </div>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}
