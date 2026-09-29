"use client";

/**
 * SecondBrainPanel — what the owner's second brain (agentcenter and its Oracle)
 * suggests: hypotheses to test, which open as prefilled analyses; next steps
 * from the Oracle's review queue; the learning radar's improvement targets.
 *
 * Everything shown is what GET /api/v1/brain/suggestions returned; when the
 * connector is off or a source is down, the panel says which and how to fix it.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, Brain, Database, FlaskConical, ListChecks, Sparkles } from "lucide-react";
import { brainApi, type Suggestion, type SuggestionsResponse } from "@/lib/analysis";
import { cn } from "@/lib/utils";

type Tab = "experiment" | "next_step" | "improvement";

const TABS: ReadonlyArray<{ key: Tab; label: string; icon: typeof FlaskConical }> = [
  { key: "experiment", label: "Hypotheses to test", icon: FlaskConical },
  { key: "next_step", label: "Next steps", icon: ListChecks },
  { key: "improvement", label: "Improvement targets", icon: Sparkles },
];

function ExperimentCard({ s }: { s: Suggestion }) {
  return (
    <li className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] text-muted-foreground">{s.id.replace(/^oracle:/, "")}</span>
        <span className="text-sm font-medium text-foreground">{s.title}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
        {s.project && <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">{s.project}</span>}
        {s.zone && <span className="rounded bg-violet-100 px-1.5 py-0.5 text-violet-800 dark:bg-violet-950 dark:text-violet-300">{s.zone}</span>}
        {s.rung && <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">evidence: {s.rung}</span>}
        {s.data && (
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded px-1.5 py-0.5",
              s.data.fetchable
                ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
                : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
            )}
          >
            <Database className="h-3 w-3" aria-hidden />
            {s.data.fetchable ? "data available" : s.data.builder === "none" ? "no data yet" : "bring a CSV"}
          </span>
        )}
      </div>
      {s.why && <p className="mt-1.5 text-xs text-muted-foreground">{s.why}</p>}
      {s.study && (
        <Link
          href={`/analyze?suggestion=${encodeURIComponent(s.id)}`}
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-foreground underline-offset-2 hover:underline"
        >
          Open as an analysis <ArrowRight className="h-3 w-3" aria-hidden />
        </Link>
      )}
    </li>
  );
}

function PlainCard({ s }: { s: Suggestion }) {
  return (
    <li className="rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-sm font-medium text-foreground">{s.title}</span>
        {s.project && <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{s.project}</span>}
        {s.time && <span className="text-[11px] text-muted-foreground">{s.time}</span>}
      </div>
      {s.why && <p className="mt-1 text-xs text-muted-foreground">{s.why}</p>}
      {s.kind === "next_step" && s.detail.length > 0 && (
        <ol className="mt-1.5 list-decimal space-y-0.5 pl-5 text-xs text-foreground">
          {s.detail.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ol>
      )}
    </li>
  );
}

export function SecondBrainPanel() {
  const [data, setData] = useState<SuggestionsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("experiment");
  const [project, setProject] = useState<string | null>(null);

  useEffect(() => {
    brainApi
      .suggestions()
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const projects = useMemo(
    () => [...new Set((data?.suggestions ?? []).map((s) => s.project).filter((p): p is string => !!p))].sort(),
    [data]
  );
  const shown = (data?.suggestions ?? []).filter(
    (s) => s.kind === tab && (project === null || s.project === project)
  );

  return (
    <section aria-labelledby="brain-h" className="rounded-xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Brain className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h2 id="brain-h" className="text-sm font-semibold text-foreground">
          From your second brain
        </h2>
        {data?.enabled && (
          <span className="ml-auto flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            {data.sources.map((src) => (
              <span key={src.name} title={src.error ?? src.url ?? ""} className="inline-flex items-center gap-1">
                <span className={cn("h-1.5 w-1.5 rounded-full", src.reachable ? "bg-green-500" : "bg-red-500")} aria-hidden />
                {src.name} {src.reachable ? `· ${src.count}` : "unreachable"}
              </span>
            ))}
            {data.emit && <span>· decisions reported back</span>}
          </span>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-red-700 dark:text-red-400">{error}</p>}
      {data === null && !error && <p className="mt-3 text-sm text-muted-foreground">Asking agentcenter…</p>}
      {data && !data.enabled && (
        <p className="mt-3 text-sm text-muted-foreground">
          Not connected. Set <code>AGENTCENTER_URL</code> (and optionally <code>ORACLE_URL</code>,{" "}
          <code>AGENTCENTER_EMIT=true</code>) in <code>backend/.env</code> to see the Oracle&apos;s
          hypotheses and next steps here, and to report decisions back.
        </p>
      )}
      {data?.enabled && (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-1" role="tablist" aria-label="Suggestion kind">
            {TABS.map(({ key, label, icon: Icon }) => {
              const n = data.suggestions.filter((s) => s.kind === key && (project === null || s.project === project)).length;
              return (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={tab === key}
                  onClick={() => setTab(key)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs",
                    tab === key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
                  )}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden /> {label} <span className="opacity-70">{n}</span>
                </button>
              );
            })}
            {projects.length > 1 && (
              <select
                aria-label="Project"
                className="ml-auto rounded-md border border-input bg-background px-2 py-1 text-xs"
                value={project ?? ""}
                onChange={(e) => setProject(e.target.value || null)}
              >
                <option value="">All projects</option>
                {projects.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            )}
          </div>
          {shown.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">Nothing here right now.</p>
          ) : (
            <ul className="mt-3 max-h-[28rem] space-y-2 overflow-y-auto pr-1">
              {shown.map((s) =>
                s.kind === "experiment" ? <ExperimentCard key={s.id} s={s} /> : <PlainCard key={s.id} s={s} />
              )}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
