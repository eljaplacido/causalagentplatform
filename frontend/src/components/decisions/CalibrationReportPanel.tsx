/**
 * CalibrationReportPanel — GET /api/v1/decisions/calibration, as returned.
 *
 * Predicted against measured, over every follow-up taken. Every rate is the
 * backend's, shown in the kernel's number format with the exact value as the
 * title; a null rate is shown as "—" with the reason the backend gives for it
 * (nothing decidable stands behind it), never as 0% or 100%. The undecidable follow-ups are counted beside the rates, by reason,
 * because they are not missing at random.
 */

import { AlertTriangle, RefreshCw } from "lucide-react";
import type { CalibrationReportResponse } from "@/lib/api";
import { formatFigure, verbatim } from "@/lib/utils";

export interface CalibrationReportPanelProps {
  report: CalibrationReportResponse | undefined;
  isLoading: boolean;
  error: Error | null;
}

const RATES: ReadonlyArray<{ key: keyof CalibrationReportResponse; label: string }> = [
  { key: "point_in_interval_rate", label: "Realized point inside the predicted interval" },
  { key: "interval_overlap_rate", label: "Predicted and realized intervals overlap" },
  { key: "acted_and_worthwhile_rate", label: "Acted, and the realized interval cleared the worthwhile effect" },
];

const COUNTS: ReadonlyArray<{ key: keyof CalibrationReportResponse; label: string }> = [
  { key: "n_decisions", label: "Follow-ups" },
  { key: "n_acted", label: "Acted on" },
  { key: "n_decidable", label: "Decidable" },
  { key: "n_undecidable", label: "Undecidable" },
  { key: "acted_and_harmful", label: "Acted, and harmful" },
  { key: "not_acted_but_worthwhile", label: "Not acted, but worthwhile" },
];

function CountTable({ caption, rows }: { caption: string; rows: Record<string, number> }) {
  const entries = Object.entries(rows);
  if (entries.length === 0) return null;
  return (
    <table className="w-full text-xs">
      <caption className="mb-1 text-left font-semibold text-foreground">{caption}</caption>
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} className="border-t border-border">
            <th scope="row" className="py-1 text-left font-mono font-normal text-muted-foreground">
              {k}
            </th>
            <td className="py-1 text-right font-mono text-foreground">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function CalibrationReportPanel({ report, isLoading, error }: CalibrationReportPanelProps) {
  return (
    <section
      aria-labelledby="calibration-heading"
      data-testid="calibration-panel"
      className="space-y-3 rounded-lg border border-border bg-card p-4"
    >
      <h2 id="calibration-heading" className="text-sm font-bold text-foreground">
        Calibration: were the decisions right?
      </h2>

      {isLoading && (
        <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Loading the calibration report…
        </p>
      )}

      {error && (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>Could not load the calibration report: {error.message}</span>
        </div>
      )}

      {report && (
        <>
          {report.n_decisions === 0 && (
            <p className="text-xs text-muted-foreground" data-testid="calibration-empty">
              No follow-up has been measured yet, so every rate below is null (—). The honest
              reading of this report, until follow-ups exist, is its counts.
            </p>
          )}

          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
            {COUNTS.map(({ key, label }) => (
              <div key={key}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="font-mono text-foreground" data-testid={`calibration-${key}`}>
                  {formatFigure(report[key] as number)}
                </dd>
              </div>
            ))}
          </dl>

          <dl className="space-y-1 text-xs">
            {RATES.map(({ key, label }) => {
              const value = report[key] as number | null;
              return (
                <div key={key} className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border pt-1">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="font-mono text-foreground" data-testid={`calibration-${key}`} title={verbatim(value)}>
                    {formatFigure(value)}
                    {value === null && (
                      <span className="ml-2 font-sans text-muted-foreground">
                        (no decidable follow-up stands behind it)
                      </span>
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>

          <div className="grid gap-3 sm:grid-cols-2">
            <CountTable caption="Follow-ups per verdict" rows={report.by_verdict} />
            <CountTable caption="Undecidable, by the kernel's reason" rows={report.undecidable_by_reason} />
          </div>

          {report.note && (
            <p className="rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
              <span className="font-semibold text-foreground">Note from the report: </span>
              {report.note}
            </p>
          )}
        </>
      )}
    </section>
  );
}
