"use client";

/**
 * NewDecisionForm — start a causal analysis and get a verdict, from the UI.
 *
 * Builds a decisions.py DecideRequest and posts it to POST /api/v1/decisions,
 * which analyses (causal_core.analyze), rules (causal_decision.decide) and
 * seals the result into the ledger in one call. The form sends only
 * DecideRequest's own fields:
 *
 *   data       a CSV parsed in the browser into columns, or pasted JSON columns
 *   graph      a registered model (GET /api/v1/models) with an optional version,
 *              sent as `model: {name, version?}` -- or an inline graph, `graph`
 *   treatment, outcome   chosen from the data's columns
 *   direction, minimum_worthwhile_effect, harm_threshold, risk_class, reversible
 *   action     the action as it would be executed
 *
 * Everything else takes the backend's defaults. The page checks only what it
 * can know before sending (a column chosen, a number >= 0); what the data and
 * graph mean is the kernel's to judge, and its answer -- a verdict, or a
 * refusal with what is missing -- is shown by DecideResult as returned. A
 * request the backend rejects (422, 404 for an unknown model) shows the
 * backend's message.
 */

import { useId, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Play } from "lucide-react";

import {
  decisionsApi,
  modelsApi,
  type DecideRequestBody,
  type DecideResponse,
  type ModelSummaryResponse,
} from "@/lib/api";
import { parseCsv, parseJsonColumns, type ParsedColumns } from "@/lib/csv";
import { cn } from "@/lib/utils";
import { DecideResult } from "./DecideResult";

type DataMode = "csv" | "json";
type GraphMode = "model" | "inline";

const inputClass =
  "w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50";

function Fieldset({ legend, children }: { legend: string; children: React.ReactNode }) {
  return (
    <fieldset className="space-y-3 rounded-lg border border-border bg-card p-4">
      <legend className="px-1 text-sm font-bold text-foreground">{legend}</legend>
      {children}
    </fieldset>
  );
}

function ModeToggle<T extends string>({
  name,
  value,
  options,
  onChange,
}: {
  name: string;
  value: T;
  options: ReadonlyArray<{ value: T; label: string }>;
  onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={name} className="flex flex-wrap gap-3 text-xs">
      {options.map((o) => (
        <label key={o.value} className="flex items-center gap-1.5">
          <input type="radio" name={name} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} />
          {o.label}
        </label>
      ))}
    </div>
  );
}

/** A file's text, through FileReader (Blob.text is not everywhere). */
function readText(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("The file could not be read."));
    reader.readAsText(file);
  });
}

/** A non-negative finite number from a text input, or why not. */
export function nonNegative(label: string, raw: string): number | string {
  const s = raw.trim();
  if (s === "") return `${label} is required (0 for none).`;
  const n = Number(s);
  if (!Number.isFinite(n)) return `${label} must be a number.`;
  if (n < 0) return `${label} must be 0 or more.`;
  return n;
}

export function NewDecisionForm() {
  const id = useId();
  const queryClient = useQueryClient();

  const [action, setAction] = useState("");
  const [dataMode, setDataMode] = useState<DataMode>("csv");
  const [fileName, setFileName] = useState<string | null>(null);
  const [jsonText, setJsonText] = useState("");
  const [parsed, setParsed] = useState<ParsedColumns | null>(null);
  const [dataError, setDataError] = useState<string | null>(null);

  const [graphMode, setGraphMode] = useState<GraphMode>("model");
  const [modelName, setModelName] = useState("");
  const [modelVersion, setModelVersion] = useState("");
  const [graphText, setGraphText] = useState("");

  const [treatment, setTreatment] = useState("");
  const [outcome, setOutcome] = useState("");
  const [direction, setDirection] = useState<"" | "increase" | "decrease">("");
  const [mwe, setMwe] = useState("0");
  const [harm, setHarm] = useState("0");
  const [riskClass, setRiskClass] = useState<"low" | "medium" | "high">("medium");
  const [reversible, setReversible] = useState(true);

  const [formErrors, setFormErrors] = useState<string[]>([]);

  const models = useQuery<{ models: ModelSummaryResponse[] }>({
    queryKey: ["models", "list"],
    queryFn: () => modelsApi.list(),
    retry: 1,
  });
  const modelList = models.data?.models ?? [];
  const selectedModel = modelList.find((m) => m.name === modelName) ?? null;

  const mutation = useMutation<DecideResponse, Error, DecideRequestBody>({
    mutationFn: (body) => decisionsApi.decide(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
    },
  });

  const columns = useMemo(() => parsed?.columns ?? [], [parsed]);

  function applyColumns(result: ParsedColumns | { error: string }) {
    if ("error" in result) {
      setParsed(null);
      setDataError(result.error);
    } else {
      setParsed(result);
      setDataError(null);
    }
    setTreatment("");
    setOutcome("");
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    try {
      applyColumns(parseCsv(await readText(file)));
    } catch (err) {
      applyColumns({ error: err instanceof Error ? err.message : "The file could not be read." });
    }
  }

  function buildBody(): DecideRequestBody | string[] {
    const errors: string[] = [];
    if (action.trim() === "") errors.push("Name the action, as it would be executed.");
    if (!parsed) errors.push("Give the data: upload a CSV or paste JSON columns.");
    if (graphMode === "model" && modelName === "") errors.push("Choose a registered model, or type an inline graph.");
    if (graphMode === "inline" && graphText.trim() === "") errors.push("Type the graph, e.g. Z -> X -> Y.");
    let version: number | undefined;
    if (graphMode === "model" && modelVersion.trim() !== "") {
      const v = Number(modelVersion);
      if (!Number.isInteger(v) || v < 1) errors.push("The model version is a whole number from 1.");
      else version = v;
    }
    if (treatment === "") errors.push("Choose the treatment column.");
    if (outcome === "") errors.push("Choose the outcome column.");
    if (treatment !== "" && treatment === outcome) errors.push("Treatment and outcome must be different columns.");
    if (direction === "") errors.push("Say which way the outcome should move.");
    const m = nonNegative("The minimum worthwhile effect", mwe);
    const h = nonNegative("The harm threshold", harm);
    if (typeof m === "string") errors.push(m);
    if (typeof h === "string") errors.push(h);
    if (errors.length > 0 || !parsed || direction === "" || typeof m === "string" || typeof h === "string") {
      return errors;
    }
    return {
      action: action.trim(),
      ...(graphMode === "model"
        ? { model: { name: modelName, ...(version !== undefined ? { version } : {}) } }
        : { graph: graphText.trim() }),
      treatment,
      outcome,
      direction,
      data: parsed.data,
      risk_class: riskClass,
      reversible,
      minimum_worthwhile_effect: m,
      harm_threshold: h,
    };
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const body = buildBody();
    if (Array.isArray(body)) {
      setFormErrors(body);
      return;
    }
    setFormErrors([]);
    mutation.mutate(body);
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <form onSubmit={submit} className="space-y-4" aria-label="New decision" noValidate>
        <Fieldset legend="1. The action">
          <label className="block space-y-1 text-xs">
            <span className="font-semibold text-foreground">Action (as it would be executed)</span>
            <input
              value={action}
              onChange={(e) => setAction(e.target.value)}
              placeholder="adopt the incident playbook"
              className={inputClass}
            />
          </label>
        </Fieldset>

        <Fieldset legend="2. Data">
          <ModeToggle
            name="Data source"
            value={dataMode}
            onChange={setDataMode}
            options={[
              { value: "csv", label: "Upload a CSV" },
              { value: "json", label: "Paste JSON columns" },
            ]}
          />
          {dataMode === "csv" ? (
            <label className="block space-y-1 text-xs">
              <span className="font-semibold text-foreground">CSV file (header row, then one row per unit)</span>
              <input type="file" accept=".csv,text/csv" onChange={(e) => void onFile(e)} className="block text-xs" />
              <span className="block text-muted-foreground">Parsed in your browser; the columns are sent with the request.</span>
            </label>
          ) : (
            <div className="space-y-1 text-xs">
              <label htmlFor={`${id}-json`} className="font-semibold text-foreground">
                Columns as JSON
              </label>
              <textarea
                id={`${id}-json`}
                value={jsonText}
                onChange={(e) => setJsonText(e.target.value)}
                rows={5}
                placeholder='{"load": [0.1, -1.2, ...], "playbook": [0, 1, ...], "minutes": [61.2, 48.9, ...]}'
                className={cn(inputClass, "font-mono text-xs")}
              />
              <button
                type="button"
                onClick={() => applyColumns(parseJsonColumns(jsonText))}
                className="rounded-md border border-border bg-background px-3 py-1 font-semibold hover:bg-muted"
              >
                Use these columns
              </button>
            </div>
          )}
          {dataError && (
            <p role="alert" data-testid="data-error" className="text-xs text-red-700 dark:text-red-300">
              {dataError}
            </p>
          )}
          {parsed && (
            <p className="text-xs text-muted-foreground" data-testid="data-summary">
              {fileName && dataMode === "csv" ? `${fileName}: ` : ""}
              {parsed.rows} rows · columns {parsed.columns.join(", ")}
            </p>
          )}
        </Fieldset>

        <Fieldset legend="3. Causal graph">
          <ModeToggle
            name="Graph source"
            value={graphMode}
            onChange={setGraphMode}
            options={[
              { value: "model", label: "A registered model" },
              { value: "inline", label: "Type a graph" },
            ]}
          />
          {graphMode === "model" ? (
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem] text-xs">
              <label className="space-y-1">
                <span className="font-semibold text-foreground">Model</span>
                <select value={modelName} onChange={(e) => setModelName(e.target.value)} className={inputClass}>
                  <option value="">— choose —</option>
                  {modelList.map((m) => (
                    <option key={m.name} value={m.name}>
                      {m.name} (head v{m.version})
                    </option>
                  ))}
                </select>
              </label>
              <label className="space-y-1">
                <span className="font-semibold text-foreground">Version (optional)</span>
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={modelVersion}
                  onChange={(e) => setModelVersion(e.target.value)}
                  placeholder={selectedModel ? `head: ${selectedModel.version}` : "head"}
                  className={inputClass}
                />
              </label>
              {models.isError && (
                <p role="alert" className="sm:col-span-2 text-red-700 dark:text-red-300">
                  Could not load the registered models: {models.error.message}
                </p>
              )}
              {models.data && modelList.length === 0 && (
                <p className="sm:col-span-2 text-muted-foreground" data-testid="no-models">
                  No model is registered yet. Register one in the assumption registry, or type a graph.
                </p>
              )}
              {selectedModel && selectedModel.unreviewed_edges.length > 0 && (
                <p className="sm:col-span-2 text-amber-700 dark:text-amber-400">
                  {selectedModel.unreviewed_edges.length} edge(s) of the head are unreviewed: a result
                  on it is provisional.
                </p>
              )}
            </div>
          ) : (
            <label className="block space-y-1 text-xs">
              <span className="font-semibold text-foreground">Graph (one statement per line or separated by ;)</span>
              <textarea
                value={graphText}
                onChange={(e) => setGraphText(e.target.value)}
                rows={3}
                placeholder={"load -> playbook -> minutes\nload -> minutes"}
                className={cn(inputClass, "font-mono text-xs")}
              />
              <span className="block text-muted-foreground">
                Syntax: <code>Z -&gt; X -&gt; Y</code>, <code>X &lt;-&gt; Y</code> for an unmeasured common cause,{" "}
                <code>latent U</code> for a variable never observed.
              </span>
            </label>
          )}
        </Fieldset>

        <Fieldset legend="4. The question">
          <div className="grid gap-3 text-xs sm:grid-cols-2">
            <label className="space-y-1">
              <span className="font-semibold text-foreground">Treatment</span>
              <select value={treatment} onChange={(e) => setTreatment(e.target.value)} disabled={columns.length === 0} className={inputClass}>
                <option value="">— a column —</option>
                {columns.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="font-semibold text-foreground">Outcome</span>
              <select value={outcome} onChange={(e) => setOutcome(e.target.value)} disabled={columns.length === 0} className={inputClass}>
                <option value="">— a column —</option>
                {columns.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="space-y-1 text-xs">
            <span className="font-semibold text-foreground">The outcome should</span>
            <ModeToggle
              name="Direction"
              value={direction}
              onChange={setDirection}
              options={[
                { value: "increase", label: "increase" },
                { value: "decrease", label: "decrease" },
              ]}
            />
          </div>
          <div className="grid gap-3 text-xs sm:grid-cols-2">
            <label className="space-y-1">
              <span className="font-semibold text-foreground">Minimum worthwhile effect</span>
              <input type="number" min={0} step="any" value={mwe} onChange={(e) => setMwe(e.target.value)} className={inputClass} />
              <span className="block text-muted-foreground">In the outcome&apos;s units, in the desired direction.</span>
            </label>
            <label className="space-y-1">
              <span className="font-semibold text-foreground">Harm threshold</span>
              <input type="number" min={0} step="any" value={harm} onChange={(e) => setHarm(e.target.value)} className={inputClass} />
              <span className="block text-muted-foreground">How far the wrong way counts as material harm.</span>
            </label>
            <label className="space-y-1">
              <span className="font-semibold text-foreground">Risk class</span>
              <select value={riskClass} onChange={(e) => setRiskClass(e.target.value as "low" | "medium" | "high")} className={inputClass}>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
              </select>
            </label>
            <label className="flex items-center gap-2 self-end pb-2">
              <input type="checkbox" checked={reversible} onChange={(e) => setReversible(e.target.checked)} />
              <span className="font-semibold text-foreground">Reversible</span>
            </label>
          </div>
        </Fieldset>

        {formErrors.length > 0 && (
          <ul role="alert" data-testid="form-errors" className="list-disc rounded-md border border-red-200 bg-red-50 p-3 pl-6 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
            {formErrors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        )}

        <button
          type="submit"
          disabled={mutation.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {mutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
          {mutation.isPending ? "Analysing…" : "Analyse and decide"}
        </button>
      </form>

      <div className="space-y-4">
        {mutation.isError && (
          <div role="alert" data-testid="decide-error" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>No decision was recorded: {mutation.error.message}</span>
          </div>
        )}
        {mutation.data ? (
          <DecideResult response={mutation.data} />
        ) : (
          !mutation.isError && (
            <div className="rounded-lg border border-dashed border-border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
              The verdict, the analysis behind it and the ledger entry it was sealed into appear here.
            </div>
          )
        )}
      </div>
    </div>
  );
}
