"use client";

/**
 * DecisionDetail — one sealed ledger entry, and its follow-up.
 *
 * Everything shown is a field of the DecisionRecord GET /api/v1/decisions
 * returned: the verdict, the checks that produced it, the evidence it rested
 * on, and the chain digests. Numbers are shown in the kernel's own format
 * (4 significant digits, causal_core.claims.format_number) with the exact value
 * as the title; an absent value is "—".
 *
 * The follow-up form posts rows covering the period AFTER the decision to
 * POST /api/v1/decisions/{entry_digest}/followup; the graph, the query and the
 * prediction come from the plan sealed at decision time, not from here. It is
 * offered only when the decision rested on a result: a refusal predicted
 * nothing, so the backend holds no plan for it. The first measurement stands;
 * a second is refused with 409, and that refusal is shown as the backend
 * worded it. The realization appears only once the backend returns it.
 */

import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import {
  decisionsApi,
  type DecisionEvidence,
  type DecisionRecordResponse,
  type DecisionRequestResponse,
  type FollowUpColumns,
  type FollowUpResponse,
} from "@/lib/api";
import { formatFigure, formatKernelNumber, formatTimestamp, verbatim } from "@/lib/utils";
import { VerdictBadge } from "./VerdictBadge";

function Field({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-all font-mono text-foreground" data-testid={testId}>
        {children}
      </dd>
    </div>
  );
}

type FigureValue = Parameters<typeof formatFigure>[0];

/** A figure as read (formatFigure), its exact value as the title. */
function FigureField({ label, value, testId }: { label: string; value: FigureValue; testId?: string }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-all font-mono text-foreground" data-testid={testId} title={verbatim(value)}>
        {formatFigure(value)}
      </dd>
    </div>
  );
}

const CLAIM = /⟦claim:([^⟧]+)⟧/;

/**
 * The value of each claim marker the decision layer writes into a rationale
 * (causal_decision.policy._rationale: effect, ci.level, effect.ci.lo,
 * effect.ci.hi, n), read back from the record's own evidence -- the claims
 * themselves are not stored. The evidence holds the effect as a benefit in the
 * requested direction (benefit = sign * effect, the interval sorted after the
 * sign), so the effect is sign * benefit and its interval the signed interval
 * re-sorted; nothing is estimated here. ci.level is a percentage, as the
 * kernel's claim is. Null when the evidence lacks the field.
 */
export function claimValue(
  id: string,
  ev: DecisionEvidence,
  request: Pick<DecisionRequestResponse, "direction">
): string | null {
  const sign = request.direction === "increase" ? 1 : -1;
  const ci = ev.benefit_ci ?? null;
  const signedCi = ci ? [sign * ci[0], sign * ci[1]].sort((a, b) => a - b) : null;
  const value: number | null | undefined = {
    effect: ev.benefit == null ? null : sign * ev.benefit,
    "effect.ci.lo": signedCi ? signedCi[0] : null,
    "effect.ci.hi": signedCi ? signedCi[1] : null,
    "ci.level": ev.ci_level == null ? null : ev.ci_level * 100,
    n: ev.n_used,
  }[id];
  if (value == null || !Number.isFinite(value)) return null;
  return formatKernelNumber(value) + (id === "ci.level" ? "%" : "");
}

/**
 * The recorded rationale keeps the analysis's figures as claim markers
 * (⟦claim:effect⟧), filled in by causal_decision.explain from the outcome,
 * which the ledger does not store whole. When every marker maps to a field of
 * this record's evidence (claimValue), the rationale is shown with those
 * figures bound; otherwise it is shown as what it is, a template, with the
 * markers named.
 */
export function RationaleText({
  text,
  evidence,
  request,
}: {
  text: string;
  evidence: DecisionEvidence;
  request: Pick<DecisionRequestResponse, "direction">;
}) {
  const parts = text.split(new RegExp(CLAIM.source, "g"));
  const ids = parts.filter((_, i) => i % 2 === 1);
  const bound = ids.map((id) => claimValue(id, evidence, request));
  const allBound = bound.every((v) => v !== null);
  return (
    <div className="space-y-1">
      {!allBound && (
        <p data-testid="rationale-template-label" className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Rationale template: the record does not carry every figure it names
        </p>
      )}
      <p className="text-sm text-foreground" data-testid="decision-rationale">
        {parts.map((part, i) => {
          if (i % 2 === 0) return <span key={i}>{part}</span>;
          const value = allBound ? bound[(i - 1) / 2] : null;
          return value !== null && value !== undefined ? (
            <span key={i} title={`${part}, from this decision's recorded evidence`} className="font-mono">
              {value}
            </span>
          ) : (
            <code key={i} title="a figure of the analysis the record does not carry" className="rounded bg-muted px-1 font-mono text-[11px] text-muted-foreground">
              {part}
            </code>
          );
        })}
      </p>
    </div>
  );
}

/**
 * Parse the follow-up rows the user pasted. Only the shape is checked here --
 * an object whose values are arrays; what the columns mean is the backend's to
 * judge. Returns the columns, or why they cannot be sent.
 */
export function parseFollowUpColumns(text: string): { data: FollowUpColumns } | { error: string } {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: "The rows are not valid JSON." };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { error: 'Give the rows as columns: {"column": [values, ...], ...}.' };
  }
  const entries = Object.entries(parsed as Record<string, unknown>);
  if (entries.length === 0) return { error: "The rows name no column." };
  for (const [name, col] of entries) {
    if (!Array.isArray(col)) return { error: `Column "${name}" is not a list.` };
  }
  return { data: parsed as FollowUpColumns };
}

function FollowUpForm({ record }: { record: DecisionRecordResponse }) {
  const queryClient = useQueryClient();
  const textareaId = useId();
  const [text, setText] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  const mutation = useMutation<FollowUpResponse, Error, FollowUpColumns>({
    mutationFn: (data) => decisionsApi.followup(record.entry_digest, data),
    onSuccess: () => {
      // A new realization changes the calibration report.
      void queryClient.invalidateQueries({ queryKey: ["decisions", "calibration"] });
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = parseFollowUpColumns(text);
    if ("error" in parsed) {
      setParseError(parsed.error);
      return;
    }
    setParseError(null);
    mutation.mutate(parsed.data);
  }

  const result = mutation.data;
  const r = result?.realization;

  return (
    <section aria-labelledby={`${textareaId}-h`} className="space-y-2 border-t border-border pt-3">
      <h4 id={`${textareaId}-h`} className="text-xs font-bold text-foreground">
        What happened next (follow-up)
      </h4>
      <p className="text-xs text-muted-foreground">
        Measured once: the first follow-up stands and a second is refused. The graph,
        query and predicted effect come from the plan sealed with this decision.
      </p>
      <form onSubmit={submit} className="space-y-2">
        <label htmlFor={textareaId} className="block text-xs font-semibold text-foreground">
          Rows after the decision, as columns (JSON)
        </label>
        <textarea
          id={textareaId}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          placeholder='{"treated": [0, 1, ...], "outcome": [3.1, 2.4, ...]}'
          aria-describedby={parseError ? `${textareaId}-err` : undefined}
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-ring"
        />
        {parseError && (
          <p id={`${textareaId}-err`} role="alert" className="text-xs text-red-700 dark:text-red-300">
            {parseError}
          </p>
        )}
        <button
          type="submit"
          disabled={mutation.isPending || text.trim() === "" || Boolean(result)}
          className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          {mutation.isPending ? "Measuring…" : "Measure the follow-up"}
        </button>
      </form>

      {mutation.isError && (
        <div role="alert" data-testid="followup-error" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>The follow-up was not recorded: {mutation.error.message}</span>
        </div>
      )}

      {result && r && (
        <div role="status" data-testid="followup-result" className="space-y-2 rounded-md border border-border bg-muted/30 p-2 text-xs">
          <p className="text-foreground">{result.explanation}</p>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
            <FigureField label="Decidable" value={r.decidable} />
            {!r.decidable && <FigureField label="Undecidable reason" value={r.undecidable_reason} />}
            <FigureField label="Predicted benefit" value={r.predicted_benefit} />
            <FigureField label="Predicted interval" value={r.predicted_ci} />
            <FigureField label="Realized benefit" value={r.realized_benefit} testId="followup-realized-benefit" />
            <FigureField label="Realized interval" value={r.realized_ci} />
            <FigureField label="Point in predicted interval" value={r.point_in_predicted_ci} />
            <FigureField label="Intervals overlap" value={r.intervals_overlap} />
            <FigureField label="Cleared worthwhile" value={r.cleared_worthwhile} />
            <FigureField label="Realized harm" value={r.realized_harm} />
          </dl>
          {r.undecidable_detail && <p className="text-muted-foreground">{r.undecidable_detail}</p>}
        </div>
      )}
    </section>
  );
}

export function DecisionDetail({ record }: { record: DecisionRecordResponse }) {
  const d = record.decision;
  const ev = d.evidence;
  return (
    <article aria-label={`Decision ${record.sequence}`} data-testid="decision-detail" className="space-y-3 rounded-lg border border-border bg-card p-4">
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-bold text-foreground">{d.request.action}</h3>
          <VerdictBadge verdict={d.verdict} />
        </div>
        <p className="text-xs text-muted-foreground">
          #{record.sequence} · recorded {formatTimestamp(record.recorded_at)} ·{" "}
          {record.executed ? "executed" : "not recorded as executed"}
        </p>
      </header>

      <RationaleText text={d.rationale} evidence={ev} request={d.request} />

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3">
        <Field label="Outcome">{d.request.outcome} ({d.request.direction})</Field>
        <Field label="Risk class">{d.request.risk_class}</Field>
        <FigureField label="Reversible" value={d.request.reversible} />
        <FigureField label="Minimum worthwhile effect" value={d.request.minimum_worthwhile_effect} />
        <FigureField label="Harm threshold" value={d.request.harm_threshold} />
        <Field label="Evidence kind">{ev.kind}</Field>
        {ev.kind === "result" ? (
          <>
            <FigureField label="Benefit" value={ev.benefit} testId="decision-benefit" />
            <FigureField label="Benefit interval" value={ev.benefit_ci} testId="decision-benefit-ci" />
            <FigureField label="Interval level" value={ev.ci_level} />
            <FigureField label="Estimator" value={ev.estimator} />
            <FigureField label="Estimand" value={ev.estimand} />
            <Field label="Identification">
              {formatFigure(ev.identification_strategy)} (tier {formatFigure(ev.identification_tier)})
            </Field>
            <FigureField label="E-value (interval)" value={ev.e_value_ci} />
            <FigureField label="Rows used" value={ev.n_used} />
          </>
        ) : (
          <FigureField label="Refusal reason" value={ev.refusal_reason} testId="decision-refusal-reason" />
        )}
        {record.evidence.model && (
          <Field label="Model version">
            {record.evidence.model.name} v{record.evidence.model.version}
          </Field>
        )}
      </dl>

      {d.checks.length > 0 && (
        <table className="w-full text-xs">
          <caption className="mb-1 text-left font-semibold text-foreground">Checks (each can only lower the verdict)</caption>
          <thead>
            <tr className="border-b border-border text-left text-muted-foreground">
              <th scope="col" className="py-1 pl-0 pr-3 font-semibold">Check</th>
              <th scope="col" className="px-3 py-1 font-semibold">Passed</th>
              <th scope="col" className="px-3 py-1 font-semibold">Allows</th>
              <th scope="col" className="px-3 py-1 font-semibold">Detail</th>
            </tr>
          </thead>
          <tbody>
            {d.checks.map((c) => (
              <tr key={c.name} className="border-t border-border align-top">
                <th scope="row" className="py-1 pl-0 pr-3 text-left font-mono font-normal text-foreground">{c.name}</th>
                <td className="px-3 py-1 font-mono">{c.passed ? "yes" : "no"}</td>
                <td className="px-3 py-1 font-mono">{c.allows}</td>
                <td className="px-3 py-1 text-muted-foreground">{c.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {d.what_would_change_it.length > 0 && (
        <div className="text-xs">
          <h4 className="font-semibold text-foreground">What would change it</h4>
          <ul className="list-disc pl-5 text-muted-foreground">
            {d.what_would_change_it.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {d.experiment && (
        <div className="text-xs">
          <h4 className="font-semibold text-foreground">The experiment that would settle it</h4>
          <p className="text-muted-foreground">{d.experiment.question}</p>
          <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
            <Field label="Unit">{d.experiment.unit}</Field>
            <FigureField label="Units per arm" value={d.experiment.units_per_arm} />
            <FigureField label="Estimated days" value={d.experiment.estimated_days} />
            <FigureField label="Minimum detectable effect" value={d.experiment.minimum_detectable_effect} />
          </dl>
        </div>
      )}

      {record.approval && (
        <p className="text-xs text-muted-foreground">
          Approved by <span className="font-mono">{record.approval.approver_id}</span> at{" "}
          {formatTimestamp(record.approval.approved_at)}
          {record.approval.notes ? ` — ${record.approval.notes}` : ""}
        </p>
      )}

      <dl className="grid gap-1 text-[11px]">
        <Field label="Entry digest">{record.entry_digest}</Field>
        <Field label="Previous digest">{record.prev_digest}</Field>
      </dl>

      {record.evidence.outcome_kind === "result" ? (
        <FollowUpForm key={record.entry_digest} record={record} />
      ) : (
        <p className="border-t border-border pt-3 text-xs text-muted-foreground" data-testid="followup-unavailable">
          No follow-up: the analysis behind this decision did not produce a result, so it
          predicted nothing to measure and the ledger holds no plan for it.
        </p>
      )}
    </article>
  );
}
