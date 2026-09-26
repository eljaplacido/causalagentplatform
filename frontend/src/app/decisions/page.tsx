"use client";

/**
 * Decision ledger — what the decision layer ruled, and whether it was right.
 *
 * Personas: Executive (decision summary + outcome tracking), Governance
 * Officer (audit), Causal Analyst (follow-ups). UIX §2.
 *
 * Data (all from the backend; numbers in the kernel's 4-significant-digit format
 * with the exact value as the title, and nothing here estimates a number):
 *   - GET  /api/v1/decisions?action=&limit=      the sealed records, newest chain
 *   - GET  /api/v1/decisions/verify               recompute the hash chain
 *   - GET  /api/v1/decisions/calibration          predicted vs measured
 *   - POST /api/v1/decisions/{entry_digest}/followup   (in DecisionDetail)
 *
 * Fail-closed: a failed request shows its error; there is no mock ledger. The
 * chain-verification result is the backend's ChainReport, and a pass is
 * labelled for what it is -- the chain is internally consistent, which is not
 * the same as untampered (anchoring is a deployment step).
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BookLock, RefreshCw, ShieldCheck, ShieldX } from "lucide-react";

import { CalibrationReportPanel } from "@/components/decisions/CalibrationReportPanel";
import { DecisionDetail } from "@/components/decisions/DecisionDetail";
import { VerdictBadge } from "@/components/decisions/VerdictBadge";
import {
  decisionsApi,
  type CalibrationReportResponse,
  type ChainReportResponse,
  type DecisionRecordList,
} from "@/lib/api";
import { cn, formatTimestamp } from "@/lib/utils";

const LIMITS = [25, 50, 100, 500] as const;

export default function DecisionsPage() {
  const [actionInput, setActionInput] = useState("");
  const [action, setAction] = useState<string | undefined>(undefined);
  const [limit, setLimit] = useState<number>(50);
  const [selectedDigest, setSelectedDigest] = useState<string | null>(null);
  const [verifyRequested, setVerifyRequested] = useState(false);

  const records = useQuery<DecisionRecordList>({
    queryKey: ["decisions", "list", action ?? null, limit],
    queryFn: () => decisionsApi.list({ action, limit }),
    retry: 1,
  });

  const calibration = useQuery<CalibrationReportResponse>({
    queryKey: ["decisions", "calibration"],
    queryFn: () => decisionsApi.calibration(),
    retry: 1,
  });

  const chain = useQuery<ChainReportResponse>({
    queryKey: ["decisions", "verify"],
    queryFn: () => decisionsApi.verify(),
    enabled: verifyRequested,
    retry: false,
    staleTime: 0,
  });

  const rows = useMemo(
    () => [...(records.data?.records ?? [])].sort((a, b) => b.sequence - a.sequence),
    [records.data]
  );
  const selected = rows.find((r) => r.entry_digest === selectedDigest) ?? null;

  function applyFilter(e: React.FormEvent) {
    e.preventDefault();
    setAction(actionInput.trim() || undefined);
    setSelectedDigest(null);
  }

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-border bg-background/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="flex items-center gap-2 text-sm font-bold text-foreground">
          <BookLock className="h-4 w-4 text-muted-foreground" aria-hidden />
          Decision ledger
        </h1>
      </header>

      <main className="grid flex-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:p-6">
        <div className="space-y-4">
          <section aria-labelledby="records-heading" className="space-y-3 rounded-lg border border-border bg-card p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h2 id="records-heading" className="text-sm font-bold text-foreground">
                Recorded decisions
              </h2>
              {records.data && (
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground" data-testid="records-total">
                  {records.data.total} returned
                </span>
              )}
              <button
                type="button"
                onClick={() => void records.refetch()}
                className="ml-auto flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <RefreshCw className="h-3 w-3" aria-hidden />
                Refresh
              </button>
            </div>

            <form onSubmit={applyFilter} className="flex flex-wrap items-end gap-2 text-xs">
              <label className="flex flex-col gap-1">
                <span className="font-semibold text-foreground">Action</span>
                <input
                  type="search"
                  value={actionInput}
                  onChange={(e) => setActionInput(e.target.value)}
                  placeholder="exact action text"
                  className="w-56 rounded-md border border-input bg-background px-2 py-1.5"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="font-semibold text-foreground">Limit</span>
                <select
                  value={limit}
                  onChange={(e) => setLimit(Number(e.target.value))}
                  className="rounded-md border border-input bg-background px-2 py-1.5"
                >
                  {LIMITS.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              <button type="submit" className="rounded-md border border-border bg-background px-3 py-1.5 font-semibold hover:bg-muted">
                Filter
              </button>
            </form>

            {records.isLoading && (
              <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
                <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden />
                Loading the ledger…
              </p>
            )}

            {records.isError && (
              <div role="alert" data-testid="records-error" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span>Could not load the decision ledger: {records.error.message}</span>
              </div>
            )}

            {records.data && rows.length === 0 && (
              <p className="text-xs text-muted-foreground" data-testid="records-empty">
                {action
                  ? `No decision is recorded for the action "${action}".`
                  : "No decision has been recorded yet. Decisions are made through POST /api/v1/decisions (or causal_decide over MCP)."}
              </p>
            )}

            {rows.length > 0 && (
              <ul className="divide-y divide-border" aria-label="Decisions">
                {rows.map((r) => {
                  const isSelected = r.entry_digest === selectedDigest;
                  return (
                    <li key={r.entry_digest}>
                      <button
                        type="button"
                        onClick={() => setSelectedDigest(r.entry_digest)}
                        aria-current={isSelected ? "true" : undefined}
                        data-testid="decision-row"
                        className={cn(
                          "flex w-full flex-wrap items-center gap-2 px-2 py-2 text-left text-xs hover:bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          isSelected && "bg-muted"
                        )}
                      >
                        <span className="font-mono text-muted-foreground">#{r.sequence}</span>
                        <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                          {r.decision.request.action}
                        </span>
                        <VerdictBadge verdict={r.decision.verdict} />
                        <span className="text-muted-foreground">{formatTimestamp(r.recorded_at)}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section aria-labelledby="chain-heading" className="space-y-2 rounded-lg border border-border bg-card p-4">
            <div className="flex items-center gap-2">
              <h2 id="chain-heading" className="text-sm font-bold text-foreground">
                Chain integrity
              </h2>
              <button
                type="button"
                onClick={() => (verifyRequested ? void chain.refetch() : setVerifyRequested(true))}
                disabled={chain.isFetching}
                className="ml-auto rounded-md border border-border bg-background px-3 py-1 text-xs font-semibold hover:bg-muted disabled:opacity-50"
              >
                {chain.isFetching ? "Verifying…" : "Verify the chain"}
              </button>
            </div>
            <p className="text-xs text-muted-foreground">
              Recomputes every digest and link from the stored records. A pass means the chain
              is internally consistent; whoever can write these rows can rewrite the whole
              chain and it verifies again, which is why the head digest is shown to publish
              elsewhere.
            </p>
            {chain.isError && (
              <div role="alert" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span>Could not verify the chain: {chain.error.message}</span>
              </div>
            )}
            {chain.data && (
              <div role="status" data-testid="chain-report" className="space-y-1 text-xs">
                <p className="flex items-center gap-1.5 font-semibold text-foreground">
                  {chain.data.ok ? (
                    <ShieldCheck className="h-4 w-4 text-green-600" aria-hidden />
                  ) : (
                    <ShieldX className="h-4 w-4 text-red-600" aria-hidden />
                  )}
                  {chain.data.ok
                    ? `Consistent: ${chain.data.length} entries recompute.`
                    : `Broken at entry ${chain.data.broken_at ?? "?"} of ${chain.data.length}: ${chain.data.reason}`}
                </p>
                {chain.data.head_digest && (
                  <p className="break-all font-mono text-muted-foreground">head {chain.data.head_digest}</p>
                )}
              </div>
            )}
          </section>
        </div>

        <div className="space-y-4">
          {selected ? (
            <DecisionDetail record={selected} />
          ) : (
            <div className="rounded-lg border border-dashed border-border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
              Select a decision to see its verdict, checks, evidence and follow-up.
            </div>
          )}
          <CalibrationReportPanel
            report={calibration.data}
            isLoading={calibration.isLoading}
            error={calibration.error}
          />
        </div>
      </main>
    </div>
  );
}
