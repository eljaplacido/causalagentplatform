"use client";

/**
 * ResultView — one analysis outcome, read top-down: the answer, how sure, how
 * it was worked out, what tried to break it, and how much hidden bias it takes.
 *
 * Every number shown is a field of the kernel's typed outcome; the page adds
 * words around them, never numbers of its own. A refusal is an answer too, and
 * is shown as one: why, and what would make the question answerable.
 */

import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  HelpCircle,
  XCircle,
} from "lucide-react";
import type {
  Abstention,
  CausalOutcome,
  CausalResult,
  Direction,
} from "@/lib/analysis";
import { fmt } from "@/lib/analysis";
import { cn } from "@/lib/utils";
import { Term } from "./Term";

// ── Refusals ─────────────────────────────────────────────────────────────────

const REASON_TEXT: Record<string, string> = {
  NON_IDENTIFIABLE:
    "Under the graph you gave, no amount of this data can separate the effect from confounding.",
  POSITIVITY:
    "Some kinds of units were (almost) never treated, or never untreated, so there's nothing to compare them with.",
  LOW_N: "There are too few rows to estimate this reliably.",
  UNAPPROVED_ASSUMPTION: "The graph contains edges nobody has reviewed yet.",
  CI_TOO_WIDE: "The estimate is too imprecise to be useful.",
  DESIGN_CHECK_FAILED: "A check of the study design failed.",
  RUNG_MISMATCH: "The question asks for more than this kind of data can answer.",
  INVALID_ADJUSTMENT_SET: "The variables chosen for adjustment don't block every confounding path.",
  WEAK_INSTRUMENT: "The instrument barely moves the treatment.",
  NO_ESTIMATOR: "No estimator fits this combination of design and data.",
  INVALID_INPUT: "The input couldn't be used as given.",
};

function RefusalView({ outcome }: { outcome: Abstention }) {
  return (
    <section
      aria-label="Analysis refused"
      className="rounded-lg border border-amber-300 bg-amber-50 p-5 dark:border-amber-800 dark:bg-amber-950/40"
    >
      <div className="flex items-start gap-3">
        <CircleSlash className="mt-0.5 h-5 w-5 shrink-0 text-amber-700 dark:text-amber-400" aria-hidden />
        <div className="min-w-0 space-y-2">
          <h3 className="font-semibold text-foreground">
            No effect estimate: the platform declined to answer
            <span className="ml-2 rounded bg-amber-200/60 px-1.5 py-0.5 font-mono text-[11px] text-amber-900 dark:bg-amber-900 dark:text-amber-200">
              {outcome.reason}
            </span>
          </h3>
          <p className="text-sm text-foreground">
            {REASON_TEXT[outcome.reason] ?? "The analysis could not produce a trustworthy estimate."}
          </p>
          <p className="text-xs text-muted-foreground break-words">{outcome.detail}</p>
          <p className="text-xs text-muted-foreground">
            This is a correct result, not an error: a number here would look precise and be wrong.
          </p>
          {outcome.missing_information.length > 0 && (
            <div className="pt-1">
              <h4 className="text-sm font-medium text-foreground">What would make it answerable</h4>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-foreground">
                {outcome.missing_information.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// ── Interval plot ────────────────────────────────────────────────────────────

function IntervalPlot({
  point,
  lo,
  hi,
  mwe,
  direction,
  truth,
  scale,
}: {
  point: number;
  lo: number;
  hi: number;
  mwe: number | null;
  direction: Direction;
  truth: number | null;
  scale: (x: number) => string;
}) {
  const mweAt = mwe ? (direction === "increase" ? mwe : -mwe) : null;
  const marks = [lo, hi, 0, point, ...(mweAt !== null ? [mweAt] : []), ...(truth !== null ? [truth] : [])];
  let min = Math.min(...marks);
  let max = Math.max(...marks);
  const pad = (max - min) * 0.12 || Math.abs(point) * 0.5 || 1;
  min -= pad;
  max += pad;
  const W = 600;
  const x = (v: number) => ((v - min) / (max - min)) * W;
  return (
    <svg viewBox={`0 0 ${W} 74`} className="h-auto w-full" role="img"
      aria-label={`Estimate ${scale(point)}, 95% interval ${scale(lo)} to ${scale(hi)}`}>
      <line x1={0} x2={W} y1={34} y2={34} className="stroke-border" strokeWidth={1} />
      <line x1={x(0)} x2={x(0)} y1={12} y2={56} className="stroke-zinc-500" strokeWidth={1.2} />
      <text x={x(0)} y={70} textAnchor="middle" className="fill-muted-foreground text-[11px]">no effect</text>
      {mweAt !== null && (
        <g>
          <line x1={x(mweAt)} x2={x(mweAt)} y1={12} y2={56} className="stroke-violet-500" strokeWidth={1.2} strokeDasharray="4 3" />
          <text x={x(mweAt)} y={9} textAnchor="middle" className="fill-violet-600 text-[11px] dark:fill-violet-400">worthwhile</text>
        </g>
      )}
      <rect x={x(lo)} y={27} width={Math.max(2, x(hi) - x(lo))} height={14} rx={7} className="fill-teal-500/25 stroke-teal-600" />
      <circle cx={x(point)} cy={34} r={6} className="fill-teal-600" />
      {truth !== null && (
        <g>
          <path d={`M ${x(truth)} 44 l -6 10 h 12 z`} className="fill-rose-500" />
          <text x={x(truth)} y={70} textAnchor="middle" className="fill-rose-600 text-[11px] dark:fill-rose-400">true effect</text>
        </g>
      )}
    </svg>
  );
}

// ── Result ───────────────────────────────────────────────────────────────────

const STATUS: Record<CausalResult["status"], { label: string; cls: string; icon: typeof CheckCircle2 }> = {
  supported: { label: "supported", cls: "border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-300", icon: CheckCircle2 },
  provisional: { label: "provisional", cls: "border-sky-300 bg-sky-50 text-sky-800 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-300", icon: HelpCircle },
  contested: { label: "contested", cls: "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300", icon: AlertTriangle },
};

function Check({ passed, name, detail }: { passed: boolean; name: string; detail: string }) {
  const Icon = passed ? CheckCircle2 : XCircle;
  return (
    <li className="flex items-start gap-2 text-sm">
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", passed ? "text-green-600" : "text-red-600")} aria-hidden />
      <span className="min-w-0">
        <span className="font-medium text-foreground">{name.replace(/_/g, " ")}</span>
        <span className="sr-only">{passed ? " passed" : " failed"}</span>
        {detail && <span className="block text-xs text-muted-foreground break-words">{detail}</span>}
      </span>
    </li>
  );
}

function Card({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <h4 className="mb-2 text-sm font-semibold text-foreground">{title}</h4>
      {children}
    </section>
  );
}

export interface ResultViewProps {
  outcome: CausalOutcome;
  report: string;
  direction: Direction;
  minimumWorthwhileEffect: number | null;
  /** The effect the data was simulated with (examples only). */
  trueEffect?: number | null;
  /** The outcome is 0/1: show effects as percentage points. */
  binaryOutcome?: boolean;
}

export function ResultView({
  outcome,
  report,
  direction,
  minimumWorthwhileEffect,
  trueEffect = null,
  binaryOutcome = false,
}: ResultViewProps) {
  if (outcome.kind === "refusal") return <RefusalView outcome={outcome} />;
  if (outcome.kind === "apparatus_failure") {
    return (
      <section className="rounded-lg border border-red-300 bg-red-50 p-5 text-sm dark:border-red-800 dark:bg-red-950/40">
        <h3 className="font-semibold text-foreground">The analysis failed at {outcome.stage}</h3>
        <p className="mt-1 font-mono text-xs text-muted-foreground break-words">
          {outcome.error_type}: {outcome.detail}
        </p>
      </section>
    );
  }

  const r = outcome;
  const p = r.primary;
  const ci = p.ci;
  const unit = binaryOutcome ? " pp" : "";
  const scale = (v: number) => (binaryOutcome ? fmt(v * 100) : fmt(v)) + unit;
  const signed = (v: number) => (v > 0 ? "+" : "") + scale(v);
  const benefit = direction === "increase" ? p.point : -p.point;
  const excludesZero = ci ? ci.lo > 0 || ci.hi < 0 : false;
  const st = STATUS[r.status];
  const StatusIcon = st.icon;
  const t = r.query.treatment;
  const y = r.query.outcome;
  const cert = r.certificate;
  const checks = [
    ...r.refutations.map((x) => ({ name: x.name, passed: x.passed, detail: x.detail })),
    ...r.diagnostics.checks.map((x) => ({ name: x.name, passed: x.passed, detail: x.detail })),
  ];
  const sens = r.sensitivity;

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-border bg-card p-5" aria-label="Answer">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold", st.cls)}>
            <StatusIcon className="h-3.5 w-3.5" aria-hidden />
            <Term k="status">{st.label}</Term>
          </span>
          <span className="text-xs text-muted-foreground">
            {p.estimand.toUpperCase()} · {p.estimator.replace(/_/g, " ")} · n = {p.n_used.toLocaleString()}
          </span>
        </div>
        <p className="mt-3 text-lg leading-snug text-foreground">
          {r.status === "contested" ? "Estimated difference" : "Estimated effect"} of{" "}
          <b className="font-semibold">{t}</b> on <b className="font-semibold">{y}</b>:{" "}
          <span className="font-mono text-2xl font-semibold tabular-nums">{signed(p.point)}</span>
          {binaryOutcome && <span className="text-sm text-muted-foreground"> (percentage points)</span>}
        </p>
        {ci && (
          <p className="mt-1 text-sm text-muted-foreground">
            <Term k="interval">95% interval</Term>: {signed(ci.lo)} to {signed(ci.hi)}.{" "}
            {excludesZero
              ? `The data rule out no effect; the effect ${benefit > 0 ? "goes the way you want" : "goes against what you want"}.`
              : "The data can't rule out no effect."}
            {minimumWorthwhileEffect ? (
              <>
                {" "}
                {(() => {
                  const lo = direction === "increase" ? ci.lo : -ci.hi;
                  const hi = direction === "increase" ? ci.hi : -ci.lo;
                  if (lo >= minimumWorthwhileEffect) return "Even the cautious end clears the worthwhile threshold.";
                  if (hi < minimumWorthwhileEffect) return "Even the optimistic end falls short of the worthwhile threshold.";
                  return "The worthwhile threshold lies inside the interval, so it's not settled whether it's worth it.";
                })()}
              </>
            ) : null}
          </p>
        )}
        {ci && (
          <div className="mt-3">
            <IntervalPlot
              point={p.point}
              lo={ci.lo}
              hi={ci.hi}
              mwe={minimumWorthwhileEffect}
              direction={direction}
              truth={trueEffect}
              scale={scale}
            />
          </div>
        )}
        {trueEffect !== null && ci && (
          <p className={cn("mt-1 text-xs", ci.lo <= trueEffect && trueEffect <= ci.hi ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400")}>
            This example was simulated with a true effect of {signed(trueEffect)};{" "}
            {ci.lo <= trueEffect && trueEffect <= ci.hi ? "the interval contains it." : "the interval misses it."}
          </p>
        )}
        {r.warnings.length > 0 && (
          <ul className="mt-3 space-y-1">
            {r.warnings.map((w) => (
              <li key={w} className="flex items-start gap-2 text-xs text-amber-800 dark:text-amber-300">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                <span className="break-words">{w}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="How it was worked out">
          <dl className="space-y-1.5 text-sm">
            <div>
              <dt className="inline text-muted-foreground">Identification: </dt>
              <dd className="inline text-foreground">
                {(cert.strategy ?? "—").replace(/_/g, " ")} (tier {cert.tier})
              </dd>
            </div>
            <div>
              <dt className="inline text-muted-foreground"><Term k="adjustment">Adjusted for</Term>: </dt>
              <dd className="inline text-foreground">
                {cert.adjustment_set.length ? cert.adjustment_set.join(", ") : "nothing needed"}
              </dd>
            </div>
            <div>
              <dt className="inline text-muted-foreground">Rows used: </dt>
              <dd className="inline text-foreground">
                {r.diagnostics.n_used.toLocaleString()} of {r.diagnostics.n_rows.toLocaleString()}
                {r.diagnostics.n_treated !== null && ` (${r.diagnostics.n_treated.toLocaleString()} treated, ${r.diagnostics.n_control?.toLocaleString()} not)`}
              </dd>
            </div>
            {r.triangulation && (
              <div>
                <dt className="inline text-muted-foreground">Cross-check: </dt>
                <dd className="inline text-foreground">
                  {r.triangulation.agree ? "independent estimators agree" : "estimators disagree"} (
                  {r.estimates.map((e) => `${e.estimator.replace(/_/g, " ")} ${signed(e.point)}`).join(", ")})
                </dd>
              </div>
            )}
          </dl>
        </Card>

        <Card title={<Term k="refutation">Checks that tried to break it</Term>}>
          {checks.length ? (
            <ul className="space-y-2">
              {checks.map((c) => (
                <Check key={c.name} {...c} />
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No falsification test applies to this design.</p>
          )}
        </Card>

        {sens && (sens.e_value !== null || sens.rosenbaum) && (
          <Card title="How much hidden confounding would it take?">
            {sens.e_value !== null && (
              <p className="text-sm text-foreground">
                <Term k="evalue">E-value</Term> {fmt(sens.e_value)}
                {sens.e_value_ci !== null && <> ({fmt(sens.e_value_ci)} for the interval&apos;s near end)</>}: an unmeasured
                confounder would need a risk-ratio association of about {fmt(sens.e_value)} with both{" "}
                {t} and {y} to explain the estimate away.
              </p>
            )}
            {sens.rosenbaum?.gamma_star != null && (
              <p className="mt-2 text-sm text-foreground">
                Matched test holds up to hidden bias Γ = {fmt(sens.rosenbaum.gamma_star)} ({sens.rosenbaum.n_pairs} pairs).
              </p>
            )}
          </Card>
        )}

        {r.diagnostics.balance.length > 0 && (
          <Card title="Balance: are the groups comparable after adjustment?">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th className="py-1 font-medium">Variable</th>
                  <th className="py-1 font-medium text-right">Before</th>
                  <th className="py-1 font-medium text-right">After</th>
                </tr>
              </thead>
              <tbody>
                {r.diagnostics.balance.map((b) => (
                  <tr key={b.covariate} className="border-t border-border">
                    <td className="py-1 text-foreground">{b.covariate}</td>
                    <td className="py-1 text-right font-mono tabular-nums">{fmt(b.smd_raw, 2)}</td>
                    <td className={cn("py-1 text-right font-mono tabular-nums", b.smd_weighted !== null && Math.abs(b.smd_weighted) > 0.1 ? "text-red-600" : "")}>
                      {fmt(b.smd_weighted, 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-[11px] text-muted-foreground">
              Standardized mean differences; within ±0.1 after adjustment is the usual bar.
            </p>
          </Card>
        )}
      </div>

      <details className="rounded-lg border border-border bg-card p-4 text-sm">
        <summary className="cursor-pointer font-medium text-foreground">The platform&apos;s full report</summary>
        <pre className="mt-3 whitespace-pre-wrap break-words font-sans text-sm text-foreground">{report}</pre>
        {r.result_digest && (
          <p className="mt-2 font-mono text-[11px] text-muted-foreground break-all">result {r.result_digest}</p>
        )}
      </details>
    </div>
  );
}
