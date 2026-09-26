"use client";

/**
 * FragmentDraftComposer — propose a graph fragment through the governed draft flow.
 *
 * The drafter composes a GraphFragment from edges the backend already holds
 * (picked from the fragments open in the workbench), names it, classifies the
 * situation, and submits it to POST /api/semantic/drafts. That one call is the
 * whole governed flow: the Guardian judges the draft, and only an APPROVED
 * verdict persists it -- as a DRAFT, which is what puts it in the governance
 * review queue for a Governance Officer to approve or reject.
 *
 * What the UI shows, and from where:
 *   - the Guardian's verdict, audit entry and the domain judged in: the 201
 *     response, or the structured detail of the 403 when the Guardian blocks;
 *   - "in the review queue": GET /api/governance/review-queue, read after the
 *     draft is saved -- the queue's word, not an assumption that it got there.
 * Nothing is shown as submitted before the backend says so, and a block is
 * shown with its reason and the rules cited (fail-closed, as on /governance).
 *
 * Classification is required, as ApprovalPanel requires it of a reviewer: a
 * declared Cynefin domain (DISORDER not offered; nothing pre-selected) or a
 * reasoning session whose own classification the Guardian reads. Without one
 * the situation is DISORDER, which rule PR-006 refuses.
 */

import { useId, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, FilePlus2, RefreshCw, X } from "lucide-react";

import { DAGCanvas, type DAGEdge, type DAGNode } from "@/components/graph/DAGCanvas";
import { APPROVAL_DOMAINS } from "@/components/governance/ApprovalPanel";
import { GuardianVerdictBadge } from "@/components/shared/GuardianVerdictBadge";
import {
  draftBlockOf,
  governanceApi,
  semanticApi,
  type CausalEdgeResponse,
  type DraftClassification,
  type DraftSubmissionResponse,
  type ReviewQueueItemResponse,
} from "@/lib/api";
import { useAuthStore } from "@/lib/store";
import type { CynefinDomain, GuardianVerdict } from "@/lib/types";

const KNOWN_VERDICTS: readonly GuardianVerdict[] = [
  "APPROVED",
  "REJECTED",
  "REQUIRES_ESCALATION",
  "PENDING",
];

function asKnownVerdict(v: string): GuardianVerdict | null {
  return (KNOWN_VERDICTS as readonly string[]).includes(v) ? (v as GuardianVerdict) : null;
}


/** DataSource.digest (backend/src/semantic_layer/models.py). */
const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;

/** A file's bytes: Blob.arrayBuffer where the platform has it, else FileReader. */
function readBytes(file: File): Promise<ArrayBuffer> {
  if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(file);
  });
}

export function edgeCaption(e: CausalEdgeResponse): string {
  return `${e.source.id} —${e.edgeType}→ ${e.target.id}`;
}

export interface FragmentDraftComposerProps {
  /** Edges of the fragment open in the workbench, as GET /api/semantic/edges/{id} returned them. */
  candidateEdges: CausalEdgeResponse[];
  /** Name of the fragment those edges come from, for the list's heading. */
  candidateSource: string | null;
  /** Semantic domain to pre-fill (the open fragment's). The drafter may change it. */
  defaultDomain?: string;
  /** When set, submission is disabled and this says why (e.g. mock data is shown). */
  disabledReason?: string | null;
}

type Mode = "declared" | "session";

export function FragmentDraftComposer({
  candidateEdges,
  candidateSource,
  defaultDomain,
  disabledReason,
}: FragmentDraftComposerProps) {
  const uid = useId();
  const userId = useAuthStore((s) => s.userId);

  const [draftEdges, setDraftEdges] = useState<Record<string, CausalEdgeResponse>>({});
  const [name, setName] = useState("");
  const [domain, setDomain] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [mode, setMode] = useState<Mode>("declared");
  const [declared, setDeclared] = useState<Exclude<CynefinDomain, "DISORDER"> | "">("");
  const [sessionId, setSessionId] = useState("");
  // The data the fragment was drafted from: approval requires it (septa-15).
  const [sourceRef, setSourceRef] = useState("");
  const [sourceDigest, setSourceDigest] = useState("");
  const [hashing, setHashing] = useState(false);

  const effectiveDomain = domain ?? defaultDomain ?? "";
  const edges = useMemo(() => Object.values(draftEdges), [draftEdges]);

  const classification: DraftClassification | null =
    mode === "declared"
      ? declared
        ? { cynefinDomain: declared }
        : null
      : sessionId.trim()
        ? { sessionId: sessionId.trim() }
        : null;

  const digestValid = SHA256_DIGEST.test(sourceDigest.trim());
  const sourceData =
    sourceRef.trim() !== "" && digestValid
      ? { kind: "dataset", ref: sourceRef.trim(), digest: sourceDigest.trim() }
      : null;

  async function digestFromFile(file: File | undefined) {
    if (!file) return;
    setHashing(true);
    try {
      // Hashed here, in the browser; the file itself is never uploaded.
      const bytes = await readBytes(file);
      const hash = await crypto.subtle.digest("SHA-256", bytes);
      const hex = Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, "0")).join("");
      setSourceDigest(`sha256:${hex}`);
      if (sourceRef.trim() === "") setSourceRef(file.name);
    } finally {
      setHashing(false);
    }
  }

  const submission = useMutation<DraftSubmissionResponse, Error, DraftClassification>({
    mutationFn: (cls) =>
      semanticApi.submitDraft(
        "GraphFragment",
        {
          name: name.trim(),
          domain: effectiveDomain.trim(),
          description: description.trim(),
          edges: edges.map((e) => e.id),
          tags: tags
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
          // Required by the GraphFragment model; the route replaces it with the
          // token's user before anything is stored (attribution from the token).
          createdBy: { id: userId ?? "unknown" },
          sourceData,
        },
        cls
      ),
  });

  const saved = submission.data;
  const queue = useQuery<ReviewQueueItemResponse[]>({
    queryKey: ["governance", "review-queue", "after-draft", saved?.object_id],
    queryFn: () => governanceApi.reviewQueue(),
    enabled: Boolean(saved),
    retry: 1,
  });
  const queued = saved ? queue.data?.find((i) => i.id === saved.object_id) : undefined;

  const block = submission.isError ? draftBlockOf(submission.error) : null;
  const locked = submission.isPending || Boolean(saved);
  const canSubmit =
    !disabledReason &&
    !locked &&
    name.trim() !== "" &&
    effectiveDomain.trim() !== "" &&
    edges.length > 0 &&
    sourceData !== null &&
    !hashing &&
    classification !== null;

  function toggle(edge: CausalEdgeResponse) {
    setDraftEdges((prev) => {
      const next = { ...prev };
      if (next[edge.id]) delete next[edge.id];
      else next[edge.id] = edge;
      return next;
    });
  }

  function reset() {
    submission.reset();
    setDraftEdges({});
    setName("");
    setDescription("");
    setTags("");
    setDeclared("");
    setSessionId("");
  }

  // Preview of the fragment being drafted: its nodes are the drafted edges' ends.
  const previewNodes: DAGNode[] = useMemo(() => {
    const seen = new Map<string, DAGNode>();
    for (const e of edges) {
      for (const ref of [e.source, e.target]) {
        if (!seen.has(ref.id)) {
          seen.set(ref.id, {
            id: ref.id,
            label: ref.id,
            nodeType: "CausalEntity",
            domain: e.domain,
            approvalStatus: "DRAFT",
          });
        }
      }
    }
    return Array.from(seen.values());
  }, [edges]);
  const previewEdges: DAGEdge[] = useMemo(
    () =>
      edges.map((e) => ({
        id: e.id,
        sourceId: e.source.id,
        targetId: e.target.id,
        edgeType: e.edgeType,
        confidence: e.confidence,
        approvalStatus: e.approvalStatus,
      })),
    [edges]
  );

  return (
    <section
      aria-labelledby={`${uid}-heading`}
      data-testid="fragment-draft-composer"
      className="space-y-4 rounded-lg border border-border bg-card p-4"
    >
      <header className="space-y-1">
        <h2 id={`${uid}-heading`} className="flex items-center gap-2 text-sm font-bold text-foreground">
          <FilePlus2 className="h-4 w-4 text-muted-foreground" aria-hidden />
          Propose a fragment
        </h2>
        <p className="text-xs text-muted-foreground">
          Compose a fragment from existing edges and submit it. The Guardian judges the draft
          first; an approved draft is saved as DRAFT and enters the governance review queue.
        </p>
      </header>

      {disabledReason && (
        <p role="note" className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {disabledReason}
        </p>
      )}

      {/* ── Edge picker ─────────────────────────────────────────────────── */}
      <fieldset className="space-y-1" disabled={locked}>
        <legend className="text-xs font-semibold text-foreground">
          {candidateSource ? `Edges in ${candidateSource}` : "Edges"}
        </legend>
        {candidateEdges.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Open a fragment above to pick edges from it. Edges picked from several fragments
            stay in the draft.
          </p>
        ) : (
          <ul className="space-y-0.5">
            {candidateEdges.map((e) => (
              <li key={e.id}>
                <label className="flex items-center gap-2 text-xs">
                  <input
                    type="checkbox"
                    checked={Boolean(draftEdges[e.id])}
                    onChange={() => toggle(e)}
                  />
                  <span className="font-mono">{edgeCaption(e)}</span>
                  <span className="text-muted-foreground">({e.approvalStatus})</span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>

      <div className="space-y-1">
        <h3 className="text-xs font-semibold text-foreground" id={`${uid}-draft-edges`}>
          In this draft ({edges.length})
        </h3>
        {edges.length === 0 ? (
          <p className="text-xs text-muted-foreground">No edge picked yet.</p>
        ) : (
          <>
            <ul aria-labelledby={`${uid}-draft-edges`} className="space-y-0.5">
              {edges.map((e) => (
                <li key={e.id} className="flex items-center gap-2 text-xs" data-testid="draft-edge">
                  <span className="font-mono">{edgeCaption(e)}</span>
                  <button
                    type="button"
                    onClick={() => toggle(e)}
                    disabled={locked}
                    aria-label={`Remove ${edgeCaption(e)} from the draft`}
                    className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                  >
                    <X className="h-3 w-3" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
            <DAGCanvas nodes={previewNodes} edges={previewEdges} width={560} height={260} />
          </>
        )}
      </div>

      {/* ── Draft fields ────────────────────────────────────────────────── */}
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit && classification) submission.mutate(classification);
        }}
      >
        <fieldset className="grid gap-3 sm:grid-cols-2" disabled={locked}>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-semibold text-foreground">Fragment name (required)</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={255}
              className="rounded-md border border-input bg-background px-2 py-1.5"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-semibold text-foreground">Domain (required)</span>
            <input
              value={effectiveDomain}
              onChange={(e) => setDomain(e.target.value)}
              className="rounded-md border border-input bg-background px-2 py-1.5 font-mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs sm:col-span-2">
            <span className="font-semibold text-foreground">Description</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="resize-none rounded-md border border-input bg-background px-2 py-1.5"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs sm:col-span-2">
            <span className="font-semibold text-foreground">Tags (comma-separated)</span>
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="rounded-md border border-input bg-background px-2 py-1.5"
            />
          </label>
        </fieldset>

        <fieldset
          className="grid gap-3 rounded-md border border-border p-3 sm:grid-cols-2"
          disabled={locked}
          data-testid="draft-source-data"
        >
          <legend className="px-1 text-xs font-semibold text-foreground">
            Source data (required for approval)
          </legend>
          <p className="text-xs text-muted-foreground sm:col-span-2">
            Where the data behind these edges is, and a SHA-256 digest that pins its contents. A
            reviewer cannot approve a fragment whose lineage does not say what it came from.
          </p>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-semibold text-foreground">Data reference</span>
            <input
              value={sourceRef}
              onChange={(e) => setSourceRef(e.target.value)}
              maxLength={2048}
              placeholder="s3://bucket/path.parquet"
              className="rounded-md border border-input bg-background px-2 py-1.5 font-mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-semibold text-foreground">SHA-256 digest</span>
            <input
              value={sourceDigest}
              onChange={(e) => setSourceDigest(e.target.value)}
              placeholder="sha256:…"
              aria-invalid={sourceDigest.trim() !== "" && !digestValid}
              className="rounded-md border border-input bg-background px-2 py-1.5 font-mono"
            />
            {sourceDigest.trim() !== "" && !digestValid && (
              <span className="text-destructive">
                Write it as sha256: followed by 64 hexadecimal digits.
              </span>
            )}
          </label>
          <label className="flex flex-col gap-1 text-xs sm:col-span-2">
            <span className="font-semibold text-foreground">Or compute the digest from a file</span>
            <input
              type="file"
              onChange={(e) => void digestFromFile(e.target.files?.[0])}
              className="text-xs"
            />
            <span className="text-muted-foreground">
              {hashing ? "Computing…" : "Hashed in your browser; the file is not uploaded."}
            </span>
          </label>
        </fieldset>

        <fieldset className="space-y-2 rounded-md border border-border p-3" disabled={locked}>
          <legend className="px-1 text-xs font-semibold text-foreground">
            Situation the draft is judged in (required)
          </legend>
          <p className="text-xs text-muted-foreground">
            The Guardian does not approve a draft in an unclassified situation (rule PR-006).
          </p>
          <label className="flex items-center gap-2 text-xs">
            <input
              type="radio"
              name={`${uid}-mode`}
              checked={mode === "declared"}
              onChange={() => setMode("declared")}
            />
            Declare the Cynefin domain (recorded with your name in the verdict&apos;s audit row)
          </label>
          {mode === "declared" && (
            <label className="ml-6 flex flex-col gap-1 text-xs">
              <span className="font-semibold text-foreground">Cynefin domain</span>
              <select
                value={declared}
                onChange={(e) => setDeclared(e.target.value as Exclude<CynefinDomain, "DISORDER"> | "")}
                className="w-48 rounded-md border border-input bg-background px-2 py-1.5"
              >
                <option value="">Choose…</option>
                {APPROVAL_DOMAINS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              {declared === "CHAOTIC" && (
                <span className="text-amber-700 dark:text-amber-400">
                  In a CHAOTIC situation the Guardian escalates to a human (PR-003) rather than approve.
                </span>
              )}
            </label>
          )}
          <label className="flex items-center gap-2 text-xs">
            <input
              type="radio"
              name={`${uid}-mode`}
              checked={mode === "session"}
              onChange={() => setMode("session")}
            />
            Use a reasoning session&apos;s own classification
          </label>
          {mode === "session" && (
            <label className="ml-6 flex flex-col gap-1 text-xs">
              <span className="font-semibold text-foreground">Session id</span>
              <input
                value={sessionId}
                onChange={(e) => setSessionId(e.target.value)}
                className="w-72 rounded-md border border-input bg-background px-2 py-1.5 font-mono"
              />
            </label>
          )}
        </fieldset>

        <div className="flex items-center gap-2">
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-md bg-foreground px-3 py-1.5 text-xs font-semibold text-background hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submission.isPending ? "Consulting Guardian…" : "Submit draft for review"}
          </button>
          {submission.isPending && (
            <RefreshCw className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
          )}
          {(saved || submission.isError) && (
            <button
              type="button"
              onClick={reset}
              className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted"
            >
              Start a new draft
            </button>
          )}
        </div>
      </form>

      {/* ── Outcome: only what the backend returned ─────────────────────── */}
      {block && (
        <div role="alert" data-testid="draft-blocked" className="space-y-1 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
          <p className="flex flex-wrap items-center gap-2 font-semibold">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
            The Guardian did not approve this draft; nothing was saved.
            {asKnownVerdict(block.verdict) ? (
              <GuardianVerdictBadge verdict={asKnownVerdict(block.verdict)!} policyRulesCited={block.policy_rules_cited} size="sm" />
            ) : null}
            <span className="font-mono">{block.verdict}</span>
          </p>
          <p data-testid="draft-blocked-reason">{block.reason}</p>
          {block.policy_rules_cited && block.policy_rules_cited.length > 0 && (
            <p>
              Rules cited: <span className="font-mono">{block.policy_rules_cited.join(", ")}</span>
            </p>
          )}
          {block.cynefin_domain && (
            <p>
              Judged in <span className="font-mono">{block.cynefin_domain}</span>
              {block.domain_source ? ` (${block.domain_source})` : ""}
            </p>
          )}
          <p>
            Audit entry <span className="font-mono">{block.audit_entry_id}</span>
          </p>
        </div>
      )}

      {submission.isError && !block && (
        <div role="alert" data-testid="draft-error" className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>The draft was not submitted: {submission.error.message}</span>
        </div>
      )}

      {saved && (
        <div role="status" data-testid="draft-saved" className="space-y-1 rounded-md border border-green-200 bg-green-50 p-3 text-xs text-green-900 dark:border-green-900 dark:bg-green-950/30 dark:text-green-200">
          <p className="flex flex-wrap items-center gap-2 font-semibold">
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
            {saved.message}
            {asKnownVerdict(saved.guardian_verdict) ? (
              <GuardianVerdictBadge verdict={asKnownVerdict(saved.guardian_verdict)!} size="sm" />
            ) : (
              <span className="font-mono">{saved.guardian_verdict}</span>
            )}
          </p>
          {saved.cynefin_domain && (
            <p>
              Judged in <span className="font-mono">{saved.cynefin_domain}</span>
              {saved.domain_source ? ` (${saved.domain_source})` : ""}
            </p>
          )}
          <p>
            Draft <span className="font-mono">{saved.object_id}</span> · audit entry{" "}
            <span className="font-mono">{saved.audit_entry_id}</span>
          </p>
          <div data-testid="draft-queue-status">
            {queue.isLoading && <p>Checking the review queue…</p>}
            {queue.isError && <p>Could not read the review queue: {queue.error.message}</p>}
            {queue.data && queued && (
              <p>
                In the governance review queue as <span className="font-mono">{queued.approval_status}</span>.{" "}
                <Link href="/governance" className="underline">
                  Review it on the governance page
                </Link>
                .
              </p>
            )}
            {queue.data && !queued && (
              <p>
                The review queue the backend returned does not list this draft (it lists only
                objects in your domain scope pending review).
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
