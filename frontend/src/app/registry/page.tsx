"use client";

/**
 * Assumption registry — causal models as versions, and what a revision breaks.
 *
 * Personas: Causal Analyst (versions, diff: US-20, US-22), Governance Officer
 * (unreviewed edges, history integrity), Engineering Manager (US-18). UIX §2.
 *
 * Data (all from the backend, shown verbatim):
 *   - GET /api/v1/models                         heads of every model
 *   - GET /api/v1/models/{name}                  head + version list
 *   - GET /api/v1/models/{name}/versions/{v}     one version
 *   - GET /api/v1/models/{name}/diff?from&to     what changed
 *   - GET /api/v1/models/{name}/impact?to        which recorded decisions a version invalidates
 *   - GET /api/v1/models/{name}/verify           recompute the version history
 *
 * Authoring (components/registry/ModelAuthoring.tsx), each a role-gated POST
 * whose refusal is shown as the backend words it:
 *   - POST /api/v1/models                        register a model (name, graph, why)
 *   - POST /api/v1/models/{name}/versions        revise the head (parent = head digest)
 *   - POST /api/v1/models/{name}/reviews         approve / refute edges of the head
 *
 * Fail-closed: every failed request shows its error; there is no mock registry.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Library, RefreshCw, ShieldCheck, ShieldX } from "lucide-react";

import { ImpactReportView } from "@/components/registry/ImpactReportView";
import {
  RegisterModelForm,
  ReviewEdgesForm,
  ReviseModelForm,
} from "@/components/registry/ModelAuthoring";
import { ModelDiffView } from "@/components/registry/ModelDiffView";
import { ModelVersionView } from "@/components/registry/ModelVersionView";
import {
  modelsApi,
  type HistoryReportResponse,
  type ImpactReportResponse,
  type ModelDiffResponse,
  type ModelHistoryResponse,
  type ModelSummaryResponse,
  type ModelVersionResponse,
} from "@/lib/api";
import { cn, formatTimestamp } from "@/lib/utils";

function ErrorBox({ what, error }: { what: string; error: Error }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>
        Could not load {what}: {error.message}
      </span>
    </div>
  );
}

function Loading({ what }: { what: string }) {
  return (
    <p role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
      <RefreshCw className="h-3.5 w-3.5 animate-spin" aria-hidden />
      Loading {what}…
    </p>
  );
}

function ModelPanel({ name }: { name: string }) {
  const history = useQuery<ModelHistoryResponse>({
    queryKey: ["models", "history", name],
    queryFn: () => modelsApi.get(name),
    retry: 1,
  });
  const head = history.data?.head;
  const versions = history.data?.versions ?? [];

  const [shownVersion, setShownVersion] = useState<number | null>(null);
  const [diffFrom, setDiffFrom] = useState<number | null>(null);
  const [diffTo, setDiffTo] = useState<number | null>(null);
  const [diffRequest, setDiffRequest] = useState<{ from: number; to: number } | null>(null);
  const [impactTo, setImpactTo] = useState<number | null>(null);
  const [impactRequest, setImpactRequest] = useState<number | null>(null);
  const [verifyRequested, setVerifyRequested] = useState(false);

  // Defaults follow the history once it arrives: compare the head with its parent.
  useEffect(() => {
    if (!head) return;
    setShownVersion(head.version);
    setDiffTo(head.version);
    setDiffFrom(Math.max(1, head.version - 1));
    setImpactTo(head.version);
  }, [head]);

  const version = useQuery<ModelVersionResponse>({
    queryKey: ["models", "version", name, shownVersion],
    queryFn: () => modelsApi.version(name, shownVersion!),
    enabled: shownVersion !== null && head !== undefined && shownVersion !== head.version,
    retry: 1,
  });
  const shown: ModelVersionResponse | undefined =
    head && shownVersion === head.version ? head : version.data;

  const diff = useQuery<ModelDiffResponse>({
    queryKey: ["models", "diff", name, diffRequest?.from, diffRequest?.to],
    queryFn: () => modelsApi.diff(name, diffRequest!.from, diffRequest!.to),
    enabled: diffRequest !== null,
    retry: false,
  });

  const impact = useQuery<ImpactReportResponse>({
    queryKey: ["models", "impact", name, impactRequest],
    queryFn: () => modelsApi.impact(name, impactRequest!),
    enabled: impactRequest !== null,
    retry: false,
  });

  const verify = useQuery<HistoryReportResponse>({
    queryKey: ["models", "verify", name],
    queryFn: () => modelsApi.verify(name),
    enabled: verifyRequested,
    retry: false,
    staleTime: 0,
  });

  if (history.isLoading) return <Loading what={`model ${name}`} />;
  if (history.isError) return <ErrorBox what={`model ${name}`} error={history.error} />;
  if (!head) return null;

  const versionOptions = versions.map((v) => (
    <option key={v.version} value={v.version}>
      v{v.version}
    </option>
  ));

  return (
    <div className="space-y-4">
      <section aria-labelledby="versions-heading" className="space-y-2 rounded-lg border border-border bg-card p-4">
        <h2 id="versions-heading" className="text-sm font-bold text-foreground">
          {name}: version history
        </h2>
        <ol className="flex flex-wrap gap-1" aria-label="Versions">
          {versions.map((v) => (
            <li key={v.version}>
              <button
                type="button"
                onClick={() => setShownVersion(v.version)}
                aria-current={v.version === shownVersion ? "true" : undefined}
                title={v.digest}
                className={cn(
                  "rounded-md border px-2 py-1 font-mono text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  v.version === shownVersion ? "border-foreground/40 bg-muted font-semibold" : "border-border hover:bg-muted"
                )}
              >
                v{v.version}
                {v.version === head.version && " (head)"}
              </button>
            </li>
          ))}
        </ol>
        {version.isLoading && <Loading what={`v${shownVersion}`} />}
        {version.isError && <ErrorBox what={`v${shownVersion}`} error={version.error} />}
      </section>

      {shown && <ModelVersionView version={shown} />}

      <section aria-label="Revise the model" className="rounded-lg border border-border bg-card p-4">
        <ReviseModelForm key={head.digest} head={head} />
      </section>

      <section aria-label="Review edges" className="rounded-lg border border-border bg-card p-4">
        <ReviewEdgesForm key={head.digest} head={head} />
      </section>

      <section aria-labelledby="diff-heading" className="space-y-2 rounded-lg border border-border bg-card p-4">
        <h2 id="diff-heading" className="text-sm font-bold text-foreground">
          Compare versions
        </h2>
        <form
          className="flex flex-wrap items-end gap-2 text-xs"
          onSubmit={(e) => {
            e.preventDefault();
            if (diffFrom !== null && diffTo !== null) setDiffRequest({ from: diffFrom, to: diffTo });
          }}
        >
          <label className="flex flex-col gap-1">
            <span className="font-semibold">From</span>
            <select value={diffFrom ?? ""} onChange={(e) => setDiffFrom(Number(e.target.value))} className="rounded-md border border-input bg-background px-2 py-1">
              {versionOptions}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-semibold">To</span>
            <select value={diffTo ?? ""} onChange={(e) => setDiffTo(Number(e.target.value))} className="rounded-md border border-input bg-background px-2 py-1">
              {versionOptions}
            </select>
          </label>
          <button type="submit" className="rounded-md border border-border bg-background px-3 py-1.5 font-semibold hover:bg-muted">
            Compare
          </button>
        </form>
        {diff.isFetching && <Loading what="the diff" />}
        {diff.isError && <ErrorBox what="the diff" error={diff.error} />}
        {diff.data && diffRequest && <ModelDiffView diff={diff.data} from={diffRequest.from} to={diffRequest.to} />}
      </section>

      <section aria-labelledby="impact-heading" className="space-y-2 rounded-lg border border-border bg-card p-4">
        <h2 id="impact-heading" className="text-sm font-bold text-foreground">
          What a version does to recorded decisions
        </h2>
        <form
          className="flex flex-wrap items-end gap-2 text-xs"
          onSubmit={(e) => {
            e.preventDefault();
            if (impactTo !== null) setImpactRequest(impactTo);
          }}
        >
          <label className="flex flex-col gap-1">
            <span className="font-semibold">If this version is true</span>
            <select value={impactTo ?? ""} onChange={(e) => setImpactTo(Number(e.target.value))} className="rounded-md border border-input bg-background px-2 py-1">
              {versionOptions}
            </select>
          </label>
          <button type="submit" className="rounded-md border border-border bg-background px-3 py-1.5 font-semibold hover:bg-muted">
            Check impact
          </button>
        </form>
        {impact.isFetching && <Loading what="the impact report" />}
        {impact.isError && <ErrorBox what="the impact report" error={impact.error} />}
        {impact.data && <ImpactReportView report={impact.data} />}
      </section>

      <section aria-labelledby="history-heading" className="space-y-2 rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2">
          <h2 id="history-heading" className="text-sm font-bold text-foreground">
            History integrity
          </h2>
          <button
            type="button"
            onClick={() => (verifyRequested ? void verify.refetch() : setVerifyRequested(true))}
            disabled={verify.isFetching}
            className="ml-auto rounded-md border border-border bg-background px-3 py-1 text-xs font-semibold hover:bg-muted disabled:opacity-50"
          >
            {verify.isFetching ? "Verifying…" : "Verify the history"}
          </button>
        </div>
        {verify.isError && <ErrorBox what="the history report" error={verify.error} />}
        {verify.data && (
          <p role="status" data-testid="history-report" className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
            {verify.data.ok ? <ShieldCheck className="h-4 w-4 text-green-600" aria-hidden /> : <ShieldX className="h-4 w-4 text-red-600" aria-hidden />}
            {verify.data.ok
              ? `Consistent: ${verify.data.length} versions recompute, each linked to its parent.`
              : `Broken at version index ${verify.data.broken_at ?? "?"} of ${verify.data.length}: ${verify.data.reason}`}
          </p>
        )}
      </section>
    </div>
  );
}

export default function RegistryPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const models = useQuery<{ models: ModelSummaryResponse[] }>({
    queryKey: ["models", "list"],
    queryFn: () => modelsApi.list(),
    retry: 1,
  });
  const list = models.data?.models ?? [];

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-border bg-background/95 px-4 py-3 backdrop-blur-sm">
        <h1 className="flex items-center gap-2 text-sm font-bold text-foreground">
          <Library className="h-4 w-4 text-muted-foreground" aria-hidden />
          Assumption registry
        </h1>
      </header>

      <div className="flex flex-1 flex-col lg:flex-row">
        <aside aria-labelledby="models-heading" className="space-y-3 border-b border-border bg-muted/30 p-4 lg:w-80 lg:border-b-0 lg:border-r">
          <div className="flex items-center gap-2">
            <h2 id="models-heading" className="text-sm font-bold text-foreground">
              Models
            </h2>
            <button
              type="button"
              onClick={() => void models.refetch()}
              className="ml-auto flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              <RefreshCw className="h-3 w-3" aria-hidden />
              Refresh
            </button>
          </div>
          {models.isLoading && <Loading what="models" />}
          {models.isError && <ErrorBox what="the registry" error={models.error} />}
          {models.data && list.length === 0 && (
            <p className="text-xs text-muted-foreground" data-testid="models-empty">
              No model is registered yet. Register one below.
            </p>
          )}
          <ul className="space-y-1" aria-label="Registered models">
            {list.map((m) => (
              <li key={m.name}>
                <button
                  type="button"
                  onClick={() => setSelected(m.name)}
                  aria-current={m.name === selected ? "true" : undefined}
                  data-testid="model-row"
                  className={cn(
                    "w-full rounded-md border px-2.5 py-1.5 text-left text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    m.name === selected ? "border-foreground/40 bg-background font-medium" : "border-transparent hover:border-border hover:bg-background"
                  )}
                >
                  <span className="block font-mono text-foreground">{m.name}</span>
                  <span className="block text-[11px] text-muted-foreground">
                    v{m.version} · {m.author_id} · {formatTimestamp(m.created_at)}
                  </span>
                  {m.unreviewed_edges.length > 0 && (
                    <span className="block text-[11px] text-amber-700 dark:text-amber-400">
                      {m.unreviewed_edges.length} unreviewed edge{m.unreviewed_edges.length === 1 ? "" : "s"}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
          <div className="rounded-lg border border-border bg-background p-3">
            <RegisterModelForm onRegistered={setSelected} />
          </div>
        </aside>

        <main className="flex-1 p-4 lg:p-6">
          {selected ? (
            <ModelPanel key={selected} name={selected} />
          ) : (
            <div className="rounded-lg border border-dashed border-border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
              Select a model to see its versions, what changed between them, and which
              recorded decisions a revision would invalidate.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
