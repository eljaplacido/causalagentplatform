"use client";

/**
 * Model authoring in the assumption registry: register, revise, review.
 *
 *   RegisterModelForm  POST /api/v1/models                  (name, graph text, why)
 *   ReviseModelForm    POST /api/v1/models/{name}/versions  (graph text, why;
 *                      parent_digest = the head's digest)
 *   ReviewEdgesForm    POST /api/v1/models/{name}/reviews   (approve or refute
 *                      edges of the head, each with a reason; parent_digest =
 *                      the head's digest)
 *
 * The rules are the registry's, not the page's: a name's syntax, an acyclic
 * graph, a revision that changes something, a revision built on the head (a
 * stale parent is refused with 409), reviewing only with a governance role and
 * never one's own edges (403). The page sends what the user wrote and shows the
 * backend's answer -- the new version, or its refusal worded as the backend
 * worded it. Proposing and revising need a drafting role; the backend decides.
 */

import { useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2 } from "lucide-react";

import { modelsApi, type EdgeReviewResponse, type ModelVersionResponse } from "@/lib/api";
import { graphToText } from "@/lib/graphText";
import { cn } from "@/lib/utils";
import { edgeLabel } from "./ModelVersionView";

const inputClass =
  "w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring";
const buttonClass =
  "rounded-md border border-border bg-background px-3 py-1.5 text-xs font-semibold hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50";

function Refusal({ what, error }: { what: string; error: Error }) {
  return (
    <div role="alert" data-testid="authoring-error" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      <span>
        {what}: {error.message}
      </span>
    </div>
  );
}

function Created({ version, verb }: { version: ModelVersionResponse; verb: string }) {
  return (
    <p role="status" data-testid="authoring-result" className="flex items-center gap-1.5 text-xs text-foreground">
      <CheckCircle2 className="h-3.5 w-3.5 text-green-600" aria-hidden />
      {verb} {version.name} v{version.version}{" "}
      <span className="break-all font-mono text-muted-foreground">({version.digest})</span>
    </p>
  );
}

function useAuthoring<TBody>(send: (body: TBody) => Promise<ModelVersionResponse>, onDone?: (v: ModelVersionResponse) => void) {
  const queryClient = useQueryClient();
  return useMutation<ModelVersionResponse, Error, TBody>({
    mutationFn: send,
    onSuccess: (v) => {
      void queryClient.invalidateQueries({ queryKey: ["models"] });
      onDone?.(v);
    },
  });
}

/** Version 1 of a new model. */
export function RegisterModelForm({ onRegistered }: { onRegistered?: (name: string) => void }) {
  const id = useId();
  const [name, setName] = useState("");
  const [graph, setGraph] = useState("");
  const [why, setWhy] = useState("");
  const mutation = useAuthoring(
    (body: { name: string; graph: string; why: string }) => modelsApi.create(body),
    (v) => onRegistered?.(v.name)
  );
  const ready = name.trim() !== "" && graph.trim() !== "" && why.trim() !== "";

  return (
    <form
      aria-labelledby={`${id}-h`}
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) mutation.mutate({ name: name.trim(), graph: graph.trim(), why: why.trim() });
      }}
    >
      <h3 id={`${id}-h`} className="text-sm font-bold text-foreground">
        Register a model
      </h3>
      <label className="block space-y-1 text-xs">
        <span className="font-semibold text-foreground">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="incident-minutes" className={cn(inputClass, "font-mono")} />
        <span className="block text-muted-foreground">Lowercase letters, digits, &apos;.&apos;, &apos;_&apos; or &apos;-&apos;.</span>
      </label>
      <label className="block space-y-1 text-xs">
        <span className="font-semibold text-foreground">Graph</span>
        <textarea
          value={graph}
          onChange={(e) => setGraph(e.target.value)}
          rows={4}
          placeholder={"load -> playbook -> minutes\nload -> minutes"}
          className={cn(inputClass, "font-mono text-xs")}
        />
      </label>
      <label className="block space-y-1 text-xs">
        <span className="font-semibold text-foreground">Why (what this model claims, and on what grounds)</span>
        <textarea value={why} onChange={(e) => setWhy(e.target.value)} rows={2} className={inputClass} />
      </label>
      <button type="submit" disabled={!ready || mutation.isPending} className={buttonClass}>
        {mutation.isPending ? "Registering…" : "Register"}
      </button>
      {mutation.isError && <Refusal what="Not registered" error={mutation.error} />}
      {mutation.data && <Created version={mutation.data} verb="Registered" />}
    </form>
  );
}

/** A new version built on the head: its graph, edited. */
export function ReviseModelForm({ head }: { head: ModelVersionResponse }) {
  const id = useId();
  const [graph, setGraph] = useState(() => graphToText(head.graph));
  const [why, setWhy] = useState("");
  const mutation = useAuthoring((body: { parent_digest: string; graph: string; why: string }) =>
    modelsApi.revise(head.name, body)
  );
  const ready = graph.trim() !== "" && why.trim() !== "";

  return (
    <form
      aria-labelledby={`${id}-h`}
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) mutation.mutate({ parent_digest: head.digest, graph: graph.trim(), why: why.trim() });
      }}
    >
      <h3 id={`${id}-h`} className="text-sm font-bold text-foreground">
        Revise {head.name} (building on v{head.version})
      </h3>
      <p className="break-all text-[11px] text-muted-foreground">parent digest {head.digest}</p>
      <label className="block space-y-1 text-xs">
        <span className="font-semibold text-foreground">Graph (starts as v{head.version}&apos;s)</span>
        <textarea value={graph} onChange={(e) => setGraph(e.target.value)} rows={6} className={cn(inputClass, "font-mono text-xs")} />
        <span className="block text-muted-foreground">
          A revision cannot approve or refute an edge; that is a review, by someone else.
        </span>
      </label>
      <label className="block space-y-1 text-xs">
        <span className="font-semibold text-foreground">Why this revision</span>
        <textarea value={why} onChange={(e) => setWhy(e.target.value)} rows={2} className={inputClass} />
      </label>
      <button type="submit" disabled={!ready || mutation.isPending} className={buttonClass}>
        {mutation.isPending ? "Revising…" : "Save as a new version"}
      </button>
      {mutation.isError && <Refusal what="Not revised" error={mutation.error} />}
      {mutation.data && <Created version={mutation.data} verb="Saved" />}
    </form>
  );
}

type Ruling = "" | EdgeReviewResponse["status"];

/** Approve or refute edges of the head, each with a reason. */
export function ReviewEdgesForm({ head }: { head: ModelVersionResponse }) {
  const id = useId();
  const [rulings, setRulings] = useState<Record<string, { status: Ruling; reason: string }>>({});
  const mutation = useAuthoring((body: { parent_digest: string; reviews: EdgeReviewResponse[] }) =>
    modelsApi.review(head.name, body)
  );

  const reviews: EdgeReviewResponse[] = Object.entries(rulings).flatMap(([edge, r]) =>
    r.status ? [{ edge, status: r.status, reason: r.reason.trim() }] : []
  );
  const missingReason = reviews.some((r) => r.reason.length < 5);
  const ready = reviews.length > 0 && !missingReason;

  function set(edge: string, patch: Partial<{ status: Ruling; reason: string }>) {
    setRulings((prev) => ({ ...prev, [edge]: { status: "", reason: "", ...prev[edge], ...patch } }));
  }

  return (
    <form
      aria-labelledby={`${id}-h`}
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) mutation.mutate({ parent_digest: head.digest, reviews });
      }}
    >
      <h3 id={`${id}-h`} className="text-sm font-bold text-foreground">
        Review edges of {head.name} v{head.version}
      </h3>
      <p className="text-xs text-muted-foreground">
        A governance role, and never an edge you introduced (the registry refuses both). Each ruling
        needs a reason of at least 5 characters.
      </p>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th scope="col" className="py-1 font-normal">Edge</th>
            <th scope="col" className="py-1 font-normal">Now</th>
            <th scope="col" className="py-1 font-normal">Introduced by</th>
            <th scope="col" className="py-1 font-normal">Ruling</th>
            <th scope="col" className="py-1 font-normal">Reason</th>
          </tr>
        </thead>
        <tbody>
          {head.graph.edges.map((e) => {
            const key = edgeLabel(e);
            const r = rulings[key] ?? { status: "", reason: "" };
            return (
              <tr key={key} className="border-t border-border align-top" data-testid="review-row">
                <th scope="row" className="py-1 text-left font-mono font-normal">{key}</th>
                <td className="py-1 font-mono">{e.status}</td>
                <td className="py-1 font-mono text-muted-foreground">{head.edge_authors[key] ?? "—"}</td>
                <td className="py-1">
                  <select
                    aria-label={`Ruling on ${key}`}
                    value={r.status}
                    onChange={(ev) => set(key, { status: ev.target.value as Ruling })}
                    className="rounded-md border border-input bg-background px-1 py-0.5"
                  >
                    <option value="">no ruling</option>
                    <option value="approved">approve</option>
                    <option value="refuted">refute</option>
                  </select>
                </td>
                <td className="py-1">
                  <input
                    aria-label={`Reason for ${key}`}
                    value={r.reason}
                    onChange={(ev) => set(key, { reason: ev.target.value })}
                    disabled={!r.status}
                    className="w-full rounded-md border border-input bg-background px-1 py-0.5 disabled:opacity-50"
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <button type="submit" disabled={!ready || mutation.isPending} className={buttonClass}>
        {mutation.isPending ? "Submitting…" : `Submit ${reviews.length} ruling${reviews.length === 1 ? "" : "s"}`}
      </button>
      {mutation.isError && <Refusal what="Review not recorded" error={mutation.error} />}
      {mutation.data && <Created version={mutation.data} verb="Reviewed into" />}
    </form>
  );
}
