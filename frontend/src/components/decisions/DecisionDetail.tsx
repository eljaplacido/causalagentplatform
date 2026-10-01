"use client";

/**
 * DecisionDetail — one sealed ledger entry, and its follow-up.
 *
 * Everything shown is a field of the DecisionRecord GET /api/v1/decisions
 * returned: the verdict, the checks that produced it, the evidence it rested
 * on, and the chain digests. Numbers are shown verbatim.
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
  type DecisionRecordResponse,
  type FollowUpColumns,
  type FollowUpResponse,
  type KernelClaim,
} from "@/lib/api";
import { renderClaim, splitRationale } from "@/lib/claims";
import { formatTimestamp, verbatim } from "@/lib/utils";
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

/**
 * The recorded rationale keeps the analysis's figures as claim markers
 * (⟦claim:effect⟧): causal_decision.explain fills them from the outcome, which
 * the ledger does not store whole. A record read back from the ledger has no
 * outcome, so its markers are shown as named placeholders -- the recorded
 * evidence below carries the numbers. When the caller holds the outcome that
 * produced the decision (the "New decision" flow), `claims` fills each marker
 * from the claim of that id, as causal_core.claims.render does; a marker with
 * no such claim stays a placeholder (src/lib/claims.ts).
 */
export function RationaleText({ text, claims }: { text: string; claims?: Map<string, KernelClaim> }) {
  const parts = splitRationale(text, claims);
  return (
    <p className="text-sm text-foreground" data-testid="decision-rationale">
      {parts.map((part, i) =>
        part.kind === "text" ? (
          <span key={i}>{part.text}</span>
        ) : part.claim ? (
          <span
            key={i}
            data-testid="claim-filled"
            data-claim={part.id}
            title={`claim ${part.id} = ${part.claim.value} (${part.claim.path})`}
            className="font-mono font-semibold"
          >
            {renderClaim(part.claim)}
          </span>
        ) : (
          <code key={i} data-testid="claim-placeholder" title="a figure of the analysis; see the evidence below" className="rounded bg-muted px-1 font-mono text-[11px] text-muted-foreground">
            {part.id}
          </code>
        )
      )}
    </p>
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
            <Field label="Decidable">{verbatim(r.decidable)}</Field>
            {!r.decidable && <Field label="Undecidable reason">{verbatim(r.undecidable_reason)}</Field>}
            <Field label="Predicted benefit">{verbatim(r.predicted_benefit)}</Field>
            <Field label="Predicted interval">{verbatim(r.predicted_ci)}</Field>
            <Field label="Realized benefit" testId="followup-realized-benefit">{verbatim(r.realized_benefit)}</Field>
            <Field label="Realized interval">{verbatim(r.realized_ci)}</Field>
            <Field label="Point in predicted interval">{verbatim(r.point_in_predicted_ci)}</Field>
            <Field label="Intervals overlap">{verbatim(r.intervals_overlap)}</Field>
            <Field label="Cleared worthwhile">{verbatim(r.cleared_worthwhile)}</Field>
            <Field label="Realized harm">{verbatim(r.realized_harm)}</Field>
          </dl>
          {r.undecidable_detail && <p className="text-muted-foreground">{r.undecidable_detail}</p>}
        </div>
      )}
    </section>
  );
}

export function DecisionDetail({
  record,
  claims,
}: {
  record: DecisionRecordResponse;
  /** The claims of the outcome behind this record, when the caller has it; fills the rationale. */
  claims?: Map<string, KernelClaim>;
}) {
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

      <RationaleText text={d.rationale} claims={claims} />

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3">
        <Field label="Outcome">{d.request.outcome} ({d.request.direction})</Field>
        <Field label="Risk class">{d.request.risk_class}</Field>
        <Field label="Reversible">{verbatim(d.request.reversible)}</Field>
        <Field label="Minimum worthwhile effect">{verbatim(d.request.minimum_worthwhile_effect)}</Field>
        <Field label="Harm threshold">{verbatim(d.request.harm_threshold)}</Field>
        <Field label="Evidence kind">{ev.kind}</Field>
        {ev.kind === "result" ? (
          <>
            <Field label="Benefit" testId="decision-benefit">{verbatim(ev.benefit)}</Field>
            <Field label="Benefit interval" testId="decision-benefit-ci">{verbatim(ev.benefit_ci)}</Field>
            <Field label="Interval level">{verbatim(ev.ci_level)}</Field>
            <Field label="Estimator">{verbatim(ev.estimator)}</Field>
            <Field label="Estimand">{verbatim(ev.estimand)}</Field>
            <Field label="Identification">
              {verbatim(ev.identification_strategy)} (tier {verbatim(ev.identification_tier)})
            </Field>
            <Field label="E-value (interval)">{verbatim(ev.e_value_ci)}</Field>
            <Field label="Rows used">{verbatim(ev.n_used)}</Field>
          </>
        ) : (
          <Field label="Refusal reason" testId="decision-refusal-reason">{verbatim(ev.refusal_reason)}</Field>
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
            <tr className="text-left text-muted-foreground">
              <th scope="col" className="py-1 font-normal">Check</th>
              <th scope="col" className="py-1 font-normal">Passed</th>
              <th scope="col" className="py-1 font-normal">Allows</th>
              <th scope="col" className="py-1 font-normal">Detail</th>
            </tr>
          </thead>
          <tbody>
            {d.checks.map((c) => (
              <tr key={c.name} className="border-t border-border align-top">
                <th scope="row" className="py-1 text-left font-mono font-normal text-foreground">{c.name}</th>
                <td className="py-1 font-mono">{c.passed ? "yes" : "no"}</td>
                <td className="py-1 font-mono">{c.allows}</td>
                <td className="py-1 text-muted-foreground">{c.detail}</td>
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
            <Field label="Units per arm">{verbatim(d.experiment.units_per_arm)}</Field>
            <Field label="Estimated days">{verbatim(d.experiment.estimated_days)}</Field>
            <Field label="Minimum detectable effect">{verbatim(d.experiment.minimum_detectable_effect)}</Field>
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
