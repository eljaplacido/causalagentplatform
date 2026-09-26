/**
 * ImpactReportView — GET /api/v1/models/{name}/impact, as returned.
 *
 * For each decision made on an older version: does the estimate its verdict
 * used still stand if the target version is true? The status and detail are
 * the kernel's (causal_decision.assumptions.impact, gated by A0); anything but
 * `holds` means re-analyse before relying on the verdict, and the view says so
 * in words.
 */

import type { ImpactReportResponse } from "@/lib/api";

export function ImpactReportView({ report }: { report: ImpactReportResponse }) {
  const counts = Object.entries(report.by_status);
  return (
    <div data-testid="impact-report" className="space-y-2">
      <h3 className="text-xs font-bold text-foreground">
        Decisions judged against {report.model} v{report.to.version}
      </h3>
      <p className="text-xs text-muted-foreground">{report.note}</p>

      {report.decisions.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="impact-empty">
          No decision was recorded on an earlier version of this model.
        </p>
      ) : (
        <>
          {counts.length > 0 && (
            <p className="text-xs" data-testid="impact-by-status">
              {counts.map(([s, n]) => (
                <span key={s} className="mr-3 font-mono">
                  {s}: {n}
                </span>
              ))}
            </p>
          )}
          <table className="w-full text-xs">
            <caption className="sr-only">Impact per recorded decision</caption>
            <thead>
              <tr className="text-left text-muted-foreground">
                <th scope="col" className="py-1 font-normal">Decision</th>
                <th scope="col" className="py-1 font-normal">On</th>
                <th scope="col" className="py-1 font-normal">Status</th>
                <th scope="col" className="py-1 font-normal">Detail</th>
              </tr>
            </thead>
            <tbody>
              {report.decisions.map((d) => (
                <tr key={d.entry_digest} className="border-t border-border align-top" data-testid="impact-row">
                  <th scope="row" className="py-1 pr-2 text-left font-normal">
                    <span className="block text-foreground">{d.action}</span>
                    <span className="block font-mono text-muted-foreground">{d.verdict}</span>
                  </th>
                  <td className="py-1 pr-2 font-mono">v{d.model_version}</td>
                  <td className="py-1 pr-2 font-mono">
                    {d.impact.status}
                    {d.impact.status !== "holds" && (
                      <span className="block font-sans text-amber-700 dark:text-amber-400">re-analyse</span>
                    )}
                  </td>
                  <td className="py-1 text-muted-foreground">
                    {d.impact.detail}
                    {(d.impact.old_adjustment_set.length > 0 || d.impact.new_adjustment_set.length > 0) && (
                      <span className="mt-0.5 block font-mono">
                        {"{"}{d.impact.old_adjustment_set.join(", ")}{"}"} → {"{"}{d.impact.new_adjustment_set.join(", ")}{"}"}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
