"use client";

/**
 * /analyze — ask a causal question of your own data, in four steps.
 *
 *   1. Data: an example (simulated with a known effect) or your CSV.
 *   2. Question: treatment, outcome, how the data came about.
 *   3. Assumptions: what causes what, as a graph. Checked live against the
 *      question: is the effect identified from it, and by adjusting for what?
 *   4. Answer: the kernel's outcome (POST /api/v1/analysis/run, recorded
 *      nowhere); then, optionally, a verdict sealed into the decision ledger
 *      (POST /api/v1/decisions, which re-runs the analysis itself).
 *
 * `?example=<id>` opens an example; `?graph=&treatment=&outcome=&design=&
 * action=&direction=&mwe=` prefill the form (used by suggestions).
 */

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  ArrowRight,
  CheckCircle2,
  FileUp,
  FlaskConical,
  Loader2,
  RotateCcw,
  XCircle,
} from "lucide-react";
import {
  analysisApi,
  graphFromRoles,
  parseCsv,
  profileColumns,
  type CheckResponse,
  type Columns,
  type DecideResponse,
  type Design,
  type Direction,
  type Estimand,
  type ExampleData,
  type ExampleSummary,
  type RunResponse,
} from "@/lib/analysis";
import { CausalApiError } from "@/lib/api";
import { CausalGraphView } from "@/components/analysis/CausalGraphView";
import { ResultView } from "@/components/analysis/ResultView";
import { Term } from "@/components/analysis/Term";
import { VerdictBadge } from "@/components/decisions/VerdictBadge";
import { cn } from "@/lib/utils";

// ── Small pieces ─────────────────────────────────────────────────────────────

function Step({
  n,
  title,
  hint,
  done,
  disabled,
  children,
}: {
  n: number;
  title: string;
  hint?: React.ReactNode;
  done?: boolean;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-labelledby={`step-${n}`}
      inert={disabled || undefined}
      className={cn(
        "rounded-xl border border-border bg-card p-5 shadow-sm transition-opacity",
        disabled && "pointer-events-none opacity-45"
      )}
    >
      <header className="mb-4 flex items-start gap-3">
        <span
          className={cn(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold",
            done ? "bg-green-600 text-white" : "bg-primary text-primary-foreground"
          )}
          aria-hidden
        >
          {done ? <CheckCircle2 className="h-4 w-4" /> : n}
        </span>
        <div className="min-w-0">
          <h2 id={`step-${n}`} className="text-base font-semibold text-foreground">
            {title}
          </h2>
          {hint && <p className="mt-0.5 text-sm text-muted-foreground">{hint}</p>}
        </div>
      </header>
      {children}
    </section>
  );
}

const input =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const primaryBtn =
  "inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";
const quietBtn =
  "inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm text-foreground hover:bg-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

function errorText(err: unknown): string {
  if (err instanceof CausalApiError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

type Source =
  | { kind: "example"; example: ExampleData }
  | { kind: "file"; name: string }
  | { kind: "paste" };

// ── The page ─────────────────────────────────────────────────────────────────

function AnalyzePage() {
  const params = useSearchParams();

  // 1 · data
  const [examples, setExamples] = useState<ExampleSummary[] | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [columns, setColumns] = useState<Columns | null>(null);
  const [dataError, setDataError] = useState<string | null>(null);
  const [loadingData, setLoadingData] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [showPaste, setShowPaste] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // 2–3 · assumptions and question
  const [treatment, setTreatment] = useState("");
  const [outcome, setOutcome] = useState("");
  const [design, setDesign] = useState<Design>("observational");
  const [estimand, setEstimand] = useState<Estimand>("ate");
  const [graphMode, setGraphMode] = useState<"simple" | "text">("simple");
  const [confounders, setConfounders] = useState<string[]>([]);
  const [outcomeCauses, setOutcomeCauses] = useState<string[]>([]);
  const [graphText, setGraphText] = useState("");
  const [check, setCheck] = useState<CheckResponse | null>(null);
  const [checking, setChecking] = useState(false);

  // 4 · answer and decision
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<RunResponse | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [action, setAction] = useState("");
  const [direction, setDirection] = useState<Direction>("increase");
  const [mwe, setMwe] = useState("0");
  const [harm, setHarm] = useState("0");
  const [risk, setRisk] = useState<"low" | "medium" | "high">("medium");
  const [reversible, setReversible] = useState(true);
  const [deciding, setDeciding] = useState(false);
  const [decided, setDecided] = useState<DecideResponse | null>(null);
  const [decideError, setDecideError] = useState<string | null>(null);

  const names = useMemo(() => (columns ? Object.keys(columns) : []), [columns]);
  const profiles = useMemo(() => (columns ? profileColumns(columns) : []), [columns]);
  const nRows = columns && names[0] ? (columns[names[0]]?.length ?? 0) : 0;
  const example = source?.kind === "example" ? source.example : null;
  const outcomeBinary = profiles.find((p) => p.name === outcome)?.kind === "binary";

  // The graph the analysis will use: typed, or built from the role pickers.
  const graph = useMemo(() => {
    if (graphMode === "text") return graphText.trim();
    if (!treatment || !outcome || treatment === outcome) return "";
    return graphFromRoles(treatment, outcome, confounders, outcomeCauses);
  }, [graphMode, graphText, treatment, outcome, confounders, outcomeCauses]);

  const resetDownstream = useCallback(() => {
    setRun(null);
    setRunError(null);
    setDecided(null);
    setDecideError(null);
  }, []);

  // Examples list
  useEffect(() => {
    analysisApi
      .examples()
      .then(setExamples)
      .catch(() => setExamples([]));
  }, []);

  const applyPrefill = useCallback(
    (p: {
      graph?: string | null;
      treatment?: string | null;
      outcome?: string | null;
      design?: string | null;
      action?: string | null;
      direction?: string | null;
      mwe?: string | number | null;
    }) => {
      if (p.graph) {
        setGraphMode("text");
        setGraphText(p.graph.split(/;\s*/).filter(Boolean).join(";\n"));
      }
      if (p.treatment) setTreatment(p.treatment);
      if (p.outcome) setOutcome(p.outcome);
      if (p.design === "randomized" || p.design === "observational") setDesign(p.design);
      if (p.action) setAction(p.action);
      if (p.direction === "increase" || p.direction === "decrease") setDirection(p.direction);
      if (p.mwe !== undefined && p.mwe !== null && p.mwe !== "") setMwe(String(p.mwe));
    },
    []
  );

  const loadExample = useCallback(
    async (id: string) => {
      setLoadingData(true);
      setDataError(null);
      resetDownstream();
      try {
        const ex = await analysisApi.example(id);
        setSource({ kind: "example", example: ex });
        setColumns(ex.columns);
        setConfounders([]);
        setOutcomeCauses([]);
        applyPrefill({
          graph: ex.graph,
          treatment: ex.treatment,
          outcome: ex.outcome,
          design: ex.design,
          action: ex.action,
          direction: ex.direction,
          mwe: ex.minimum_worthwhile_effect,
        });
      } catch (err) {
        setDataError(errorText(err));
      } finally {
        setLoadingData(false);
      }
    },
    [applyPrefill, resetDownstream]
  );

  // URL prefill, once
  const prefilled = useRef(false);
  useEffect(() => {
    if (prefilled.current) return;
    prefilled.current = true;
    const ex = params.get("example");
    if (ex) {
      void loadExample(ex);
      return;
    }
    applyPrefill({
      graph: params.get("graph"),
      treatment: params.get("treatment"),
      outcome: params.get("outcome"),
      design: params.get("design"),
      action: params.get("action"),
      direction: params.get("direction"),
      mwe: params.get("mwe"),
    });
  }, [params, loadExample, applyPrefill]);

  function acceptTable(text: string, src: Source) {
    try {
      const t = parseCsv(text);
      setColumns(t.columns);
      setSource(src);
      setDataError(null);
      resetDownstream();
      // Keep a prefilled question when its columns exist; otherwise clear the pickers.
      if (!t.names.includes(treatment)) setTreatment("");
      if (!t.names.includes(outcome)) setOutcome("");
      setConfounders((c) => c.filter((x) => t.names.includes(x)));
      setOutcomeCauses((c) => c.filter((x) => t.names.includes(x)));
    } catch (err) {
      setDataError(errorText(err));
    }
  }

  async function onFile(file: File) {
    if (file.size > 25 * 1024 * 1024) {
      setDataError("That file is over 25 MB; sample it down first (the analysis runs in one request).");
      return;
    }
    acceptTable(await file.text(), { kind: "file", name: file.name });
  }

  // Live check of graph + question
  useEffect(() => {
    if (!graph) {
      setCheck(null);
      return;
    }
    let cancelled = false;
    setChecking(true);
    const handle = setTimeout(() => {
      analysisApi
        .check({
          graph,
          treatment: treatment || undefined,
          outcome: outcome || undefined,
          estimand,
          columns: names.length ? names : undefined,
          query: { design },
        })
        .then((r) => !cancelled && setCheck(r))
        .catch((err) => !cancelled && setCheck({ valid: false, error: errorText(err) } as CheckResponse))
        .finally(() => !cancelled && setChecking(false));
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [graph, treatment, outcome, estimand, design, names]);

  async function onRun() {
    if (!columns) return;
    setRunning(true);
    resetDownstream();
    try {
      setRun(
        await analysisApi.run({ graph, treatment, outcome, estimand, data: columns, query: { design } })
      );
      setTimeout(() => document.getElementById("step-4")?.scrollIntoView?.({ behavior: "smooth" }), 50);
    } catch (err) {
      setRunError(errorText(err));
    } finally {
      setRunning(false);
    }
  }

  async function onDecide() {
    if (!columns) return;
    setDeciding(true);
    setDecideError(null);
    try {
      setDecided(
        await analysisApi.decide({
          graph,
          treatment,
          outcome,
          estimand,
          data: columns,
          query: { design },
          action: action.trim(),
          direction,
          risk_class: risk,
          reversible,
          minimum_worthwhile_effect: Math.max(0, Number(mwe) || 0),
          harm_threshold: Math.max(0, Number(harm) || 0),
          can_randomize: true,
        })
      );
    } catch (err) {
      setDecideError(errorText(err));
    } finally {
      setDeciding(false);
    }
  }

  function startOver() {
    setSource(null);
    setColumns(null);
    setTreatment("");
    setOutcome("");
    setConfounders([]);
    setOutcomeCauses([]);
    setGraphText("");
    setGraphMode("simple");
    setDesign("observational");
    setEstimand("ate");
    setAction("");
    setMwe("0");
    setHarm("0");
    resetDownstream();
    window.history.replaceState(null, "", "/analyze");
  }

  const ident = check?.identification ?? null;
  const questionReady = Boolean(columns && treatment && outcome && treatment !== outcome && graph);
  const canRun = questionReady && check?.valid === true;
  const others = names.filter((n) => n !== treatment && n !== outcome);

  return (
    <main className="mx-auto w-full max-w-5xl space-y-5 px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Analyze</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Did something you did cause a change, and is it worth acting on? Bring data, say what
            you believe causes what, and the platform estimates the effect, or tells you why the
            data can&apos;t answer.
          </p>
        </div>
        {(columns || graph) && (
          <button type="button" onClick={startOver} className={quietBtn}>
            <RotateCcw className="h-3.5 w-3.5" aria-hidden /> Start over
          </button>
        )}
      </header>

      {/* 1 · Data ────────────────────────────────────────────────────── */}
      <Step
        n={1}
        title="Choose data"
        hint="One row per unit (customer, request, week...), one column per variable."
        done={Boolean(columns)}
      >
        {!columns && (
          <div className="space-y-4">
            <div
              className="flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border p-6 text-center"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const f = e.dataTransfer.files?.[0];
                if (f) void onFile(f);
              }}
            >
              <FileUp className="h-6 w-6 text-muted-foreground" aria-hidden />
              <p className="text-sm text-foreground">Drop a CSV here, or</p>
              <div className="flex flex-wrap justify-center gap-2">
                <button type="button" className={quietBtn} onClick={() => fileRef.current?.click()}>
                  Choose a file
                </button>
                <button type="button" className={quietBtn} onClick={() => setShowPaste((v) => !v)}>
                  Paste CSV text
                </button>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.tsv,.txt,text/csv"
                className="sr-only"
                aria-label="CSV file"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void onFile(f);
                  e.target.value = "";
                }}
              />
              <p className="text-xs text-muted-foreground">
                Your data stays in this browser until you run the analysis; nothing is stored unless
                you record a decision (then only digests of the data are kept).
              </p>
            </div>
            {showPaste && (
              <div className="space-y-2">
                <label htmlFor="paste" className="text-sm font-medium text-foreground">
                  CSV text, header row first
                </label>
                <textarea
                  id="paste"
                  rows={6}
                  className={cn(input, "font-mono text-xs")}
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                  placeholder={"treated,outcome,age\n1,3.2,41\n0,2.9,38"}
                />
                <button type="button" className={primaryBtn} onClick={() => acceptTable(pasteText, { kind: "paste" })}>
                  Use this data
                </button>
              </div>
            )}

            <div>
              <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground">
                <FlaskConical className="h-4 w-4 text-muted-foreground" aria-hidden />
                Or try an example. Each is simulated with a known true effect, so you can see
                whether the platform gets it right.
              </h3>
              {examples === null ? (
                <p className="text-sm text-muted-foreground">Loading examples…</p>
              ) : examples.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Examples are unavailable. Is the backend running?
                </p>
              ) : (
                <ul className="grid gap-3 sm:grid-cols-2">
                  {examples.map((ex) => (
                    <li key={ex.id}>
                      <button
                        type="button"
                        disabled={loadingData}
                        onClick={() => void loadExample(ex.id)}
                        className="h-full w-full rounded-lg border border-border p-4 text-left hover:border-foreground/30 hover:bg-muted/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <span className="block text-sm font-semibold text-foreground">{ex.title}</span>
                        <span className="mt-1 block text-xs text-muted-foreground">{ex.summary}</span>
                        <span className="mt-2 inline-block rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                          {ex.design} · {ex.n_rows.toLocaleString()} rows
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
        {dataError && (
          <p role="alert" className="mt-3 flex items-start gap-2 text-sm text-red-700 dark:text-red-400">
            <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> {dataError}
          </p>
        )}
        {columns && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-foreground">
                {example ? example.title : source?.kind === "file" ? source.name : "Pasted data"}
              </span>
              <span className="text-muted-foreground">
                · {nRows.toLocaleString()} rows · {names.length} columns
              </span>
              <button type="button" className={cn(quietBtn, "ml-auto py-1 text-xs")} onClick={startOver}>
                Change data
              </button>
            </div>
            {example && (
              <p className="rounded-md bg-muted/60 p-3 text-sm text-foreground">
                <b className="font-medium">What to look for:</b> {example.lesson}
              </p>
            )}
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full text-xs">
                <thead className="bg-muted/50">
                  <tr>
                    {profiles.map((p) => (
                      <th key={p.name} scope="col" className="whitespace-nowrap px-3 py-2 text-left font-medium text-foreground">
                        {p.name}
                        <span className="ml-1.5 font-normal text-muted-foreground">
                          {p.kind}
                          {p.missing > 0 && ` · ${p.missing} missing`}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: Math.min(5, nRows) }, (_, i) => (
                    <tr key={i} className="border-t border-border">
                      {names.map((n) => (
                        <td key={n} className="whitespace-nowrap px-3 py-1.5 font-mono tabular-nums text-foreground">
                          {String(columns[n]?.[i] ?? "—")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Step>

      {/* 2 · Question ────────────────────────────────────────────────── */}
      <Step
        n={2}
        title="Ask the question"
        hint="Which change are you asking about, and which result?"
        done={questionReady}
        disabled={!columns}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="treatment" className="text-sm font-medium text-foreground">
              <Term k="treatment">Treatment</Term>: what was done
            </label>
            <select
              id="treatment"
              className={cn(input, "mt-1")}
              value={treatment}
              onChange={(e) => {
                setTreatment(e.target.value);
                setConfounders((c) => c.filter((x) => x !== e.target.value));
                resetDownstream();
              }}
            >
              <option value="">Choose a column…</option>
              {names.map((n) => (
                <option key={n} value={n} disabled={n === outcome}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="outcome" className="text-sm font-medium text-foreground">
              <Term k="outcome">Outcome</Term>: what you measure
            </label>
            <select
              id="outcome"
              className={cn(input, "mt-1")}
              value={outcome}
              onChange={(e) => {
                setOutcome(e.target.value);
                setConfounders((c) => c.filter((x) => x !== e.target.value));
                resetDownstream();
              }}
            >
              <option value="">Choose a column…</option>
              {names.map((n) => (
                <option key={n} value={n} disabled={n === treatment}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <fieldset className="sm:col-span-2">
            <legend className="text-sm font-medium text-foreground">How was the treatment assigned?</legend>
            <div className="mt-1 grid gap-2 sm:grid-cols-2">
              {(
                [
                  ["randomized", "By chance: an A/B test or experiment"],
                  ["observational", "Some other way: people or systems chose"],
                ] as const
              ).map(([value, label]) => (
                <label
                  key={value}
                  className={cn(
                    "flex cursor-pointer items-start gap-2 rounded-md border p-3 text-sm",
                    design === value ? "border-foreground/40 bg-muted/60" : "border-border"
                  )}
                >
                  <input
                    type="radio"
                    name="design"
                    value={value}
                    checked={design === value}
                    onChange={() => {
                      setDesign(value);
                      resetDownstream();
                    }}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="font-medium text-foreground">
                      <Term k={value}>{value === "randomized" ? "Randomized" : "Observational"}</Term>
                    </span>
                    <span className="block text-xs text-muted-foreground">{label}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <details className="sm:col-span-2 text-sm">
            <summary className="cursor-pointer text-muted-foreground">Advanced: which average?</summary>
            <div className="mt-2 flex flex-wrap gap-4">
              {(["ate", "att"] as const).map((v) => (
                <label key={v} className="flex items-center gap-2">
                  <input type="radio" name="estimand" checked={estimand === v} onChange={() => { setEstimand(v); resetDownstream(); }} />
                  <Term k={v}>{v === "ate" ? "Everyone (ATE)" : "Those treated (ATT)"}</Term>
                </label>
              ))}
            </div>
          </details>
        </div>
      </Step>

      {/* 3 · Assumptions ─────────────────────────────────────────────── */}
      <Step
        n={3}
        title="Say what causes what"
        hint={
          <>
            Your <Term k="graph">causal graph</Term>. The analysis is only as good as it is, so
            name everything that influences both the treatment and the outcome.
          </>
        }
        done={ident?.identified === true}
        disabled={!columns || !treatment || !outcome}
      >
        <div className="mb-3 inline-flex rounded-md border border-border p-0.5 text-xs" role="tablist" aria-label="Graph editor">
          {(
            [
              ["simple", "Pick from columns"],
              ["text", "Write the graph"],
            ] as const
          ).map(([m, label]) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={graphMode === m}
              onClick={() => {
                if (m === "text" && graphMode === "simple" && graph) setGraphText(graph.replace(/; /g, ";\n"));
                setGraphMode(m);
                resetDownstream();
              }}
              className={cn("rounded px-3 py-1", graphMode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="grid gap-5 lg:grid-cols-2">
          <div className="min-w-0 space-y-4">
            {graphMode === "simple" ? (
              <>
                <fieldset>
                  <legend className="text-sm font-medium text-foreground">
                    What influences <em>both</em> {treatment || "the treatment"} and {outcome || "the outcome"}?{" "}
                    <span className="font-normal text-muted-foreground">(<Term k="confounder">confounders</Term>)</span>
                  </legend>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {others.length === 0 && <span className="text-xs text-muted-foreground">No other columns.</span>}
                    {others.map((n) => {
                      const on = confounders.includes(n);
                      return (
                        <button
                          key={n}
                          type="button"
                          aria-pressed={on}
                          onClick={() => {
                            setConfounders((c) => (on ? c.filter((x) => x !== n) : [...c, n]));
                            setOutcomeCauses((c) => c.filter((x) => x !== n));
                            resetDownstream();
                          }}
                          className={cn(
                            "rounded-full border px-3 py-1 text-xs",
                            on ? "border-amber-500 bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-200" : "border-border text-foreground hover:bg-muted"
                          )}
                        >
                          {n}
                        </button>
                      );
                    })}
                  </div>
                  {design === "randomized" && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      With randomized assignment nothing should influence the treatment; leave this empty
                      unless the randomization was conditional on something.
                    </p>
                  )}
                </fieldset>
                <fieldset>
                  <legend className="text-sm font-medium text-foreground">
                    What else affects {outcome || "the outcome"}? <span className="font-normal text-muted-foreground">(optional, sharpens the estimate)</span>
                  </legend>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {others
                      .filter((n) => !confounders.includes(n))
                      .map((n) => {
                        const on = outcomeCauses.includes(n);
                        return (
                          <button
                            key={n}
                            type="button"
                            aria-pressed={on}
                            onClick={() => {
                              setOutcomeCauses((c) => (on ? c.filter((x) => x !== n) : [...c, n]));
                              resetDownstream();
                            }}
                            className={cn(
                              "rounded-full border px-3 py-1 text-xs",
                              on ? "border-sky-500 bg-sky-50 text-sky-900 dark:bg-sky-950 dark:text-sky-200" : "border-border text-foreground hover:bg-muted"
                            )}
                          >
                            {n}
                          </button>
                        );
                      })}
                  </div>
                </fieldset>
                <p className="text-xs text-muted-foreground">
                  Need more (mediators, a variable you didn&apos;t measure, an instrument)? Switch to{" "}
                  <button type="button" className="underline" onClick={() => { setGraphText(graph.replace(/; /g, ";\n")); setGraphMode("text"); }}>
                    Write the graph
                  </button>
                  .
                </p>
              </>
            ) : (
              <div>
                <label htmlFor="graph" className="text-sm font-medium text-foreground">
                  One relationship per line or separated by <code>;</code>
                </label>
                <textarea
                  id="graph"
                  rows={8}
                  spellCheck={false}
                  className={cn(input, "mt-1 font-mono text-xs")}
                  value={graphText}
                  onChange={(e) => {
                    setGraphText(e.target.value);
                    resetDownstream();
                  }}
                  placeholder={"season -> promo\nseason -> sales\npromo -> sales"}
                />
                <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                  <li><code>A -&gt; B</code>: A influences B</li>
                  <li><code>A &lt;-&gt; B</code>: A and B share a cause you didn&apos;t measure</li>
                  <li><code>latent U</code>: U exists but has no column (so do nodes that aren&apos;t columns)</li>
                  <li><code>A -&gt; B [status=proposed]</code>: an edge nobody has reviewed yet</li>
                </ul>
              </div>
            )}

            {/* Identification verdict */}
            <div aria-live="polite" className="min-h-[3rem]">
              {checking && !check && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Checking…
                </p>
              )}
              {check && !check.valid && (
                <p className="flex items-start gap-2 rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/50 dark:text-red-300">
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                  <span className="break-words">The graph can&apos;t be read: {check.error}</span>
                </p>
              )}
              {check?.valid && ident && (
                ident.identified ? (
                  <div className="rounded-md bg-green-50 p-3 text-sm text-green-900 dark:bg-green-950/50 dark:text-green-200">
                    <p className="flex items-start gap-2 font-medium">
                      <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                      <span>
                        The effect is <Term k="identified">identified</Term> from this graph
                        {ident.certificate?.adjustment_set.length
                          ? `, adjusting for ${ident.certificate.adjustment_set.join(", ")}.`
                          : ident.certificate?.strategy === "randomized"
                            ? " by randomization."
                            : " with no adjustment needed."}
                      </span>
                    </p>
                  </div>
                ) : (
                  <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/50 dark:text-amber-200">
                    <p className="flex items-start gap-2 font-medium">
                      <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                      <span className="break-words">Not identified: {ident.refusal}</span>
                    </p>
                    {ident.missing_information.length > 0 && (
                      <ul className="mt-2 list-disc space-y-0.5 pl-9 text-xs">
                        {ident.missing_information.map((m) => (
                          <li key={m}>{m}</li>
                        ))}
                      </ul>
                    )}
                    <p className="mt-2 pl-6 text-xs">You can still run it: the platform will refuse, and say why.</p>
                  </div>
                )
              )}
              {check?.valid && check.unmeasured.length > 0 && (
                <p className="mt-2 text-xs text-muted-foreground">
                  No column for {check.unmeasured.join(", ")}: treated as unmeasured.
                </p>
              )}
              {check?.valid && check.unreviewed_edges.length > 0 && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Unreviewed edges ({check.unreviewed_edges.join(", ")}): the result will be at most
                  provisional.
                </p>
              )}
            </div>
          </div>

          <div className="min-w-0">
            <CausalGraphView
              className="rounded-lg border border-border bg-background p-2"
              nodes={check?.graph?.nodes ?? []}
              edges={check?.graph?.edges ?? []}
              treatment={treatment}
              outcome={outcome}
              adjustmentSet={ident?.certificate?.adjustment_set ?? []}
              unmeasured={[...(check?.unmeasured ?? []), ...(check?.latent ?? [])]}
            />
            {check?.valid && check.testable_implications.length > 0 && (
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer text-muted-foreground">
                  {check.testable_implications.length === 1
                    ? "1 pair of variables this graph claims are unrelated"
                    : `${check.testable_implications.length} pairs of variables this graph claims are unrelated`}
                </summary>
                <ul className="mt-1 space-y-0.5 pl-4 text-muted-foreground">
                  {check.testable_implications.slice(0, 12).map((i) => (
                    <li key={`${i.a}|${i.b}|${i.given.join(",")}`}>
                      {i.a} ⫫ {i.b}
                      {i.given.length > 0 && ` given ${i.given.join(", ")}`}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-border pt-4">
          <button type="button" className={primaryBtn} disabled={!canRun || running} onClick={() => void onRun()}>
            {running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <ArrowRight className="h-4 w-4" aria-hidden />}
            {running ? "Analyzing…" : "Estimate the effect"}
          </button>
          <span className="text-xs text-muted-foreground">
            Runs identification, several estimators, falsification tests and sensitivity analysis.
            Nothing is recorded.
          </span>
        </div>
        {runError && (
          <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-400">{runError}</p>
        )}
      </Step>

      {/* 4 · Answer ──────────────────────────────────────────────────── */}
      <Step n={4} title="The answer" done={Boolean(decided)} disabled={!run}>
        {run && (
          <div className="space-y-6">
            <ResultView
              outcome={run.outcome}
              report={run.report}
              direction={direction}
              minimumWorthwhileEffect={Number(mwe) || null}
              trueEffect={example?.true_effect ?? null}
              binaryOutcome={outcomeBinary}
            />

            <section aria-labelledby="decide-h" className="rounded-lg border border-border p-5">
              <h3 id="decide-h" className="text-base font-semibold text-foreground">
                Should you act on it?
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">
                The decision layer weighs the evidence against what the action is worth and how risky
                it is, and returns a <Term k="verdict">verdict</Term>. Recording seals it into the
                append-only decision ledger, where you can later record what actually happened.
              </p>
              {!decided ? (
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <div className="sm:col-span-2">
                    <label htmlFor="action" className="text-sm font-medium text-foreground">Action you&apos;d take</label>
                    <input id="action" className={cn(input, "mt-1")} value={action} onChange={(e) => setAction(e.target.value)} placeholder={`e.g. roll out ${treatment || "the treatment"} to everyone`} />
                  </div>
                  <fieldset>
                    <legend className="text-sm font-medium text-foreground">You want {outcome || "the outcome"} to…</legend>
                    <div className="mt-1 flex gap-4 text-sm">
                      {(["increase", "decrease"] as const).map((d) => (
                        <label key={d} className="flex items-center gap-1.5">
                          <input type="radio" name="direction" checked={direction === d} onChange={() => setDirection(d)} /> {d}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <div>
                    <label htmlFor="risk" className="text-sm font-medium text-foreground">Risk if it goes wrong</label>
                    <select id="risk" className={cn(input, "mt-1")} value={risk} onChange={(e) => setRisk(e.target.value as typeof risk)}>
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                    </select>
                  </div>
                  <div>
                    <label htmlFor="mwe" className="text-sm font-medium text-foreground">
                      <Term k="mwe">Smallest worthwhile effect</Term>
                    </label>
                    <input id="mwe" type="number" min={0} step="any" className={cn(input, "mt-1")} value={mwe} onChange={(e) => setMwe(e.target.value)} />
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      In {outcome || "outcome"} units{outcomeBinary ? " (0/1 outcome: 0.01 = 1 percentage point)" : ""}.
                    </p>
                  </div>
                  <div>
                    <label htmlFor="harm" className="text-sm font-medium text-foreground">Largest acceptable harm</label>
                    <input id="harm" type="number" min={0} step="any" className={cn(input, "mt-1")} value={harm} onChange={(e) => setHarm(e.target.value)} />
                    <p className="mt-1 text-[11px] text-muted-foreground">How far the wrong way you could tolerate.</p>
                  </div>
                  <label className="flex items-center gap-2 text-sm sm:col-span-2">
                    <input type="checkbox" checked={reversible} onChange={(e) => setReversible(e.target.checked)} />
                    The action can be undone
                  </label>
                  <div className="sm:col-span-2 flex flex-wrap items-center gap-3">
                    <button type="button" className={primaryBtn} disabled={!action.trim() || deciding} onClick={() => void onDecide()}>
                      {deciding && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
                      Decide and record
                    </button>
                    <span className="text-xs text-muted-foreground">Permanent: the ledger is append-only.</span>
                  </div>
                  {decideError && (
                    <p role="alert" className="sm:col-span-2 text-sm text-red-700 dark:text-red-400">{decideError}</p>
                  )}
                </div>
              ) : (
                <div className="mt-4 space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-muted-foreground">Verdict:</span>
                    <VerdictBadge verdict={decided.verdict} />
                    <span className="font-mono text-[11px] text-muted-foreground">
                      entry #{decided.record.sequence} · {decided.record.entry_digest.slice(0, 19)}…
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap text-sm text-foreground">{decided.explanation}</p>
                  {decided.decision.experiment && (
                    <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-950 dark:bg-amber-950/40 dark:text-amber-100">
                      <b className="font-medium">Suggested experiment:</b> {decided.decision.experiment.question}
                      {decided.decision.experiment.units_per_arm && ` · ${decided.decision.experiment.units_per_arm.toLocaleString()} units per arm`}
                      {decided.decision.experiment.estimated_days && ` · about ${Math.ceil(decided.decision.experiment.estimated_days)} days`}
                    </div>
                  )}
                  {decided.decision.what_would_change_it.length > 0 && (
                    <div>
                      <h4 className="text-sm font-medium text-foreground">What would change the verdict</h4>
                      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-foreground">
                        {decided.decision.what_would_change_it.map((w) => <li key={w}>{w}</li>)}
                      </ul>
                    </div>
                  )}
                  <Link href="/decisions" className={quietBtn}>
                    Open in the decision ledger <ArrowRight className="h-3.5 w-3.5" aria-hidden />
                  </Link>
                </div>
              )}
            </section>
          </div>
        )}
      </Step>
    </main>
  );
}

export default function AnalyzeRoute() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-muted-foreground">Loading…</main>}>
      <AnalyzePage />
    </Suspense>
  );
}
