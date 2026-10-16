/**
 * CalibrationReportPanel — GET /api/v1/decisions/calibration, as returned.
 *
 * Predicted against measured, over every follow-up taken. Each rate is the
 * kernel's RateEstimate: its counts always, and a rate with its Clopper-Pearson
 * interval only from `min_followups` up. Figures are in the kernel's number
 * format with the exact value as the title; a rate the report does not state is
 * "—" with the reason it gives, never 0% or 100%. The undecidable follow-ups are
 * counted beside the rates, by reason, because they are not missing at random.
 * Decisions past their review date with nothing re-checking them are listed
 * last: they are in none of the rates, and they still stand in the ledger.
 */

import { AlertTriangle, RefreshCw } from "lucide-react";
import type { CalibrationReportResponse, RateEstimateResponse } from "@/lib/api";
import { formatFigure, verbatim } from "@/lib/utils";

export interface CalibrationReportPanelProps {
  report: CalibrationReportResponse | undefined;
  isLoading: boolean;
  error: Error | null;
}

type RateKey =
  | "point_in_interval_rate"
  | "interval_overlap_rate"
  | "acted_and_worthwhile_rate"
  | "acted_and_harmful_rate";

const RATES: ReadonlyArray<{ key: RateKey; label: string }> = [
  { key: "point_in_interval_rate", label: "Realized point inside the predicted interval" },
  { key: "interval_overlap_rate", label: "Predicted and realized intervals overlap" },
  { key: "acted_and_worthwhile_rate", label: "Acted, and the realized interval cleared the worthwhile effect" },
  { key: "acted_and_harmful_rate", label: "Acted, and the realized interval showed harm" },
];

/** A RateEstimate: the rate and interval when stated, the counts always, else why not. */
function RateValue({ estimate }: { estimate: RateEstimateResponse }) {
  const counts = `${estimate.successes} of ${estimate.denominator}`;
  return (
    <>
      <span title={verbatim(estimate.rate)}>{formatFigure(estimate.rate)}</span>
      <span className="ml-2 font-sans text-muted-foreground">({counts})</span>
      {estimate.rate !== null && estimate.ci && (
        <span className="ml-2" title={verbatim(estimate.ci)}>
          {formatFigure(estimate.level * 100)}% CI {formatFigure(estimate.ci)}
        </span>
      )}
      {estimate.refused && (
        <span className="block font-sans text-muted-foreground">{estimate.refused}</span>
      )}
    </>
  );
}

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
              No follow-up has been measured yet, so no rate below is stated (—). The honest
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
            {RATES.map(({ key, label }) => (
              <div key={key} className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border pt-1">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="text-right font-mono text-foreground" data-testid={`calibration-${key}`}>
                  <RateValue estimate={report[key]} />
                </dd>
              </div>
            ))}
          </dl>

          <div className="grid gap-3 sm:grid-cols-2">
            <CountTable caption="Follow-ups per verdict" rows={report.by_verdict} />
            <CountTable caption="Undecidable, by the kernel's reason" rows={report.undecidable_by_reason} />
          </div>

          {report.past_review.length > 0 && (
            <table className="w-full text-xs" data-testid="calibration-past-review">
              <caption className="mb-1 text-left font-semibold text-foreground">
                Past their review date, with no follow-up and no newer decision (
                {report.past_review.length})
              </caption>
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th scope="col" className="py-1 font-normal">Action</th>
                  <th scope="col" className="py-1 font-normal">Verdict</th>
                  <th scope="col" className="py-1 font-normal">Review by</th>
                  <th scope="col" className="py-1 font-normal">Executed</th>
                </tr>
              </thead>
              <tbody>
                {report.past_review.map((p) => (
                  <tr key={p.entry_digest} className="border-t border-border" data-testid="past-review-row">
                    <td className="py-1 text-foreground">{p.action}</td>
                    <td className="py-1 font-mono text-foreground">{p.verdict}</td>
                    <td className="py-1 font-mono text-foreground">{p.review_by}</td>
                    <td className="py-1 text-foreground">{p.executed ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

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
