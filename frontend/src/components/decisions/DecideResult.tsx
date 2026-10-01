"use client";

/**
 * DecideResult — what POST /api/v1/decisions returned, shown as returned.
 *
 * Four parts, each a field of decisions.py DecideResponse:
 *   - the verdict (the policy's, not the page's);
 *   - the explanation causal_decision.explain wrote, verbatim;
 *   - the kernel's outcome: a result's estimate, interval and claims; a
 *     refusal's reason, detail and missing_information (a refusal is a result);
 *     an apparatus failure's stage and error;
 *   - the ledger record it was sealed into, through DecisionDetail, with the
 *     rationale's claim markers filled from this outcome's claims.
 *
 * Every number is shown as the backend sent it; where rounding helps reading,
 * a four-significant-digit display sits beside the raw value, labelled as
 * display-only. Nothing here computes a causal number.
 */

import type { DecideResponse, KernelOutcome } from "@/lib/api";
import { claimsOf, roundedForDisplay } from "@/lib/claims";
import { verbatim } from "@/lib/utils";
import { DecisionDetail } from "./DecisionDetail";
import { VerdictBadge } from "./VerdictBadge";

/** A raw number with its display-only rounding beside it (omitted when they read the same). */
export function RawNumber({ value, testId }: { value: number | null | undefined; testId?: string }) {
  if (value === null || value === undefined) {
    return <span className="font-mono" data-testid={testId}>null</span>;
  }
  const raw = String(value);
  const rounded = roundedForDisplay(value);
  return (
    <span data-testid={testId}>
      <span className="font-mono" data-raw>{raw}</span>
      {rounded !== raw && (
        <span className="ml-1.5 text-muted-foreground" data-rounded title="rounded to 4 significant digits, for display only">
          (≈ {rounded})
        </span>
      )}
    </span>
  );
}

function Row({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-all text-foreground" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}

export function OutcomePanel({ outcome }: { outcome: KernelOutcome }) {
  if (outcome.kind === "refusal") {
    return (
      <section aria-label="Kernel refusal" data-testid="outcome-refusal" className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-4 text-xs dark:border-amber-800 dark:bg-amber-950/30">
        <h3 className="text-sm font-bold text-foreground">The analysis refused</h3>
        <p className="text-muted-foreground">
          A refusal is a result: the data and graph given cannot answer this question, and the
          kernel says what would.
        </p>
        <dl className="grid gap-2">
          <Row label="Reason" testId="refusal-reason">
            <span className="font-mono">{outcome.reason}</span>
          </Row>
          <Row label="Detail" testId="refusal-detail">{outcome.detail}</Row>
        </dl>
        {outcome.missing_information.length > 0 && (
          <div>
            <h4 className="font-semibold text-foreground">Missing information</h4>
            <ul className="list-disc pl-5 text-foreground" data-testid="missing-information">
              {outcome.missing_information.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </div>
        )}
      </section>
    );
  }

  if (outcome.kind === "apparatus_failure") {
    return (
      <section aria-label="Apparatus failure" data-testid="outcome-failure" className="space-y-2 rounded-lg border border-red-300 bg-red-50 p-4 text-xs dark:border-red-900 dark:bg-red-950/30">
        <h3 className="text-sm font-bold text-foreground">The analysis failed to run</h3>
        <dl className="grid gap-2">
          <Row label="Stage"><span className="font-mono">{outcome.stage}</span></Row>
          <Row label="Error type"><span className="font-mono">{outcome.error_type}</span></Row>
          <Row label="Detail">{outcome.detail}</Row>
        </dl>
      </section>
    );
  }

  const p = outcome.primary;
  const cert = outcome.certificate;
  return (
    <section aria-label="Kernel result" data-testid="outcome-result" className="space-y-3 rounded-lg border border-border bg-card p-4 text-xs">
      <h3 className="text-sm font-bold text-foreground">
        Estimate <span className="ml-1 rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">{outcome.status}</span>
      </h3>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        <Row label="Effect (point)"><RawNumber value={p.point} testId="outcome-point" /></Row>
        <Row label="Standard error"><RawNumber value={p.se} /></Row>
        <Row label="Interval level"><span className="font-mono">{verbatim(p.ci?.level ?? null)}</span></Row>
        <Row label="Interval low"><RawNumber value={p.ci?.lo ?? null} testId="outcome-ci-lo" /></Row>
        <Row label="Interval high"><RawNumber value={p.ci?.hi ?? null} testId="outcome-ci-hi" /></Row>
        <Row label="Interval method"><span className="font-mono">{verbatim(p.ci?.method ?? null)}</span></Row>
        <Row label="Estimator"><span className="font-mono">{p.estimator}</span></Row>
        <Row label="Estimand"><span className="font-mono">{p.estimand}</span></Row>
        <Row label="Rows used"><span className="font-mono">{p.n_used}</span></Row>
        <Row label="Identification">
          <span className="font-mono">
            {verbatim(cert.strategy)} (tier {cert.tier})
          </span>
        </Row>
        <Row label="Adjustment set">
          <span className="font-mono">{cert.adjustment_set.length ? cert.adjustment_set.join(", ") : "∅"}</span>
        </Row>
        <Row label="Unreviewed edges">
          <span className="font-mono">{cert.unreviewed_edges.length ? cert.unreviewed_edges.join(", ") : "none"}</span>
        </Row>
      </dl>

      {outcome.warnings.length > 0 && (
        <ul className="list-disc pl-5 text-amber-800 dark:text-amber-300" data-testid="outcome-warnings">
          {outcome.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {outcome.claims.length > 0 && (
        <table className="w-full" data-testid="claims-table">
          <caption className="mb-1 text-left font-semibold text-foreground">
            Claims (the numbers prose may use, each bound to its field)
          </caption>
          <thead>
            <tr className="text-left text-muted-foreground">
              <th scope="col" className="py-1 font-normal">Claim</th>
              <th scope="col" className="py-1 font-normal">Value (raw)</th>
              <th scope="col" className="py-1 font-normal">Kernel text</th>
              <th scope="col" className="py-1 font-normal">Field</th>
            </tr>
          </thead>
          <tbody>
            {outcome.claims.map((c) => (
              <tr key={c.id} className="border-t border-border" data-testid="claim-row">
                <th scope="row" className="py-1 text-left font-mono font-normal">{c.id}</th>
                <td className="py-1 font-mono">{String(c.value)}</td>
                <td className="py-1 font-mono">
                  {c.text}
                  {c.unit ? ` ${c.unit}` : ""}
                </td>
                <td className="py-1 font-mono text-muted-foreground">{c.path}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function DecideResult({ response }: { response: DecideResponse }) {
  return (
    <div className="space-y-4" data-testid="decide-result">
      <section aria-label="Verdict" className="space-y-2 rounded-lg border border-border bg-card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-bold text-foreground">Verdict</h2>
          <span data-testid="decide-verdict">
            <VerdictBadge verdict={response.verdict} />
          </span>
          <span className="text-xs text-muted-foreground">
            sealed as ledger entry #{response.record.sequence}
          </span>
        </div>
        <pre className="whitespace-pre-wrap break-words font-sans text-sm text-foreground" data-testid="decide-explanation">
          {response.explanation}
        </pre>
      </section>
      <OutcomePanel outcome={response.outcome} />
      <DecisionDetail record={response.record} claims={claimsOf(response.outcome)} />
    </div>
  );
}
