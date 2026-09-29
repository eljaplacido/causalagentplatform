"use client";

/**
 * Home: start an analysis, try an example, see what was decided lately, and
 * find the rest of the platform.
 *
 * This page used to be a role picker that redirected every later visit to the
 * last role's page, so home was unreachable once a role had been picked.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  BookOpen,
  FlaskConical,
  GanttChartSquare,
  GitBranch,
  LineChart,
  Shield,
  Siren,
  Wrench,
} from "lucide-react";
import { analysisApi, type ExampleSummary } from "@/lib/analysis";
import { decisionsApi, type DecisionRecordResponse } from "@/lib/api";
import { VerdictBadge } from "@/components/decisions/VerdictBadge";

const PLACES = [
  { href: "/decisions", icon: BookOpen, title: "Decision ledger", text: "Every verdict recorded, tamper-evident; record what actually happened and see whether the platform was right." },
  { href: "/registry", icon: GitBranch, title: "Assumption registry", text: "Causal models as reviewed versions, and which past decisions a revision would overturn." },
  { href: "/workbench", icon: LineChart, title: "DAG Workbench", text: "The library of reviewed graph fragments; propose new ones for review." },
  { href: "/incidents", icon: Siren, title: "Incidents", text: "Operational incidents with causal hypotheses and interventions to approve." },
  { href: "/governance", icon: Shield, title: "Governance", text: "The review queue, approvals and the audit trail." },
  { href: "/engineer", icon: Wrench, title: "Platform Health", text: "Connectors, drift alerts and fragment health." },
  { href: "/executive", icon: GanttChartSquare, title: "Manager Dashboard", text: "What the platform did, in aggregate." },
] as const;

export default function HomePage() {
  const [examples, setExamples] = useState<ExampleSummary[] | null>(null);
  const [recent, setRecent] = useState<DecisionRecordResponse[] | null>(null);

  useEffect(() => {
    analysisApi.examples().then(setExamples).catch(() => setExamples([]));
    decisionsApi
      .list({ limit: 5 })
      .then((r) => setRecent(r.records))
      .catch(() => setRecent([]));
  }, []);

  return (
    <main className="mx-auto w-full max-w-5xl space-y-8 px-4 py-8 sm:px-6">
      <section className="rounded-2xl border border-border bg-card p-6 shadow-sm sm:p-8">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
          Did it work, and is it worth doing?
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground sm:text-base">
          Estimate the effect of a change from your data, with the assumptions written down and
          checked, an honest interval, and a refusal when the data can&apos;t answer. Then record
          the decision and, later, whether it held up.
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <Link
            href="/analyze"
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Analyze your data <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
          <Link
            href="/analyze?example=email-campaign"
            className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-muted"
          >
            <FlaskConical className="h-4 w-4" aria-hidden /> Try an example
          </Link>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section aria-labelledby="examples-h">
          <h2 id="examples-h" className="mb-3 text-sm font-semibold text-foreground">
            Examples with a known answer
          </h2>
          {examples === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : examples.length === 0 ? (
            <p className="text-sm text-muted-foreground">The backend isn&apos;t reachable.</p>
          ) : (
            <ul className="space-y-2">
              {examples.map((ex) => (
                <li key={ex.id}>
                  <Link
                    href={`/analyze?example=${ex.id}`}
                    className="block rounded-lg border border-border p-3 hover:bg-muted/50"
                  >
                    <span className="block text-sm font-medium text-foreground">{ex.title}</span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">{ex.summary}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="recent-h">
          <div className="mb-3 flex items-center justify-between">
            <h2 id="recent-h" className="text-sm font-semibold text-foreground">
              Recent decisions
            </h2>
            <Link href="/decisions" className="text-xs text-muted-foreground hover:text-foreground">
              All decisions →
            </Link>
          </div>
          {recent === null ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : recent.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
              Nothing decided yet. Analyze something and choose <i>Decide and record</i>; it will show
              up here.
            </p>
          ) : (
            <ul className="divide-y divide-border rounded-lg border border-border">
              {recent.map((r) => (
                <li key={r.entry_digest} className="flex items-center gap-3 p-3">
                  <VerdictBadge verdict={r.decision.verdict} />
                  <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                    {r.decision.request.action}
                  </span>
                  <time className="shrink-0 text-xs text-muted-foreground" dateTime={r.recorded_at}>
                    {new Date(r.recorded_at).toLocaleDateString()}
                  </time>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section aria-labelledby="places-h">
        <h2 id="places-h" className="mb-3 text-sm font-semibold text-foreground">
          Everything else
        </h2>
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {PLACES.map(({ href, icon: Icon, title, text }) => (
            <li key={href}>
              <Link href={href} className="flex h-full gap-3 rounded-lg border border-border p-3 hover:bg-muted/50">
                <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <span>
                  <span className="block text-sm font-medium text-foreground">{title}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{text}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
