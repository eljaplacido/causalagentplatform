/**
 * ModelVersionView — one immutable version of a registered causal model.
 *
 * Shows the kernel graph (each edge with its origin and review status), the
 * assumptions a graph cannot state, why the version exists, and the reviews
 * that produced it -- all fields of the ModelVersion the backend returned.
 * An unreviewed (`proposed`) edge is labelled in words, not by colour alone:
 * it makes every result on the model provisional.
 */

import type { KernelEdge, ModelVersionResponse } from "@/lib/api";
import { formatTimestamp } from "@/lib/utils";

/**
 * An edge's identity as causal_decision.assumptions.edge_key spells it (the key
 * of `edge_authors` and of a review): "a -> b", or "a <-> b" with the ends
 * sorted, since a bidirected edge has no direction.
 */
export function edgeLabel(e: Pick<KernelEdge, "source" | "target" | "kind">): string {
  if (e.kind === "bidirected") {
    const [a, b] = [e.source, e.target].sort();
    return `${a} <-> ${b}`;
  }
  return `${e.source} -> ${e.target}`;
}

export function EdgeTable({ edges, caption, authors }: { edges: KernelEdge[]; caption: string; authors?: Record<string, string> }) {
  if (edges.length === 0) return null;
  return (
    <table className="w-full text-xs">
      <caption className="mb-1 text-left font-semibold text-foreground">{caption}</caption>
      <thead>
        <tr className="text-left text-muted-foreground">
          <th scope="col" className="py-1 font-normal">Edge</th>
          <th scope="col" className="py-1 font-normal">Origin</th>
          <th scope="col" className="py-1 font-normal">Status</th>
          {authors && <th scope="col" className="py-1 font-normal">Introduced by</th>}
        </tr>
      </thead>
      <tbody>
        {edges.map((e) => {
          const key = edgeLabel(e);
          return (
            <tr key={key} className="border-t border-border" data-testid="model-edge">
              <th scope="row" className="py-1 text-left font-mono font-normal text-foreground">{key}</th>
              <td className="py-1 font-mono">{e.origin}</td>
              <td className="py-1 font-mono">
                {e.status}
                {e.status === "proposed" && <span className="ml-1 font-sans text-amber-700 dark:text-amber-400">(unreviewed)</span>}
              </td>
              {authors && <td className="py-1 font-mono text-muted-foreground">{authors[key] ?? "—"}</td>}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function ModelVersionView({ version }: { version: ModelVersionResponse }) {
  const unreviewed = version.graph.edges.filter((e) => e.status === "proposed");
  return (
    <article aria-label={`${version.name} version ${version.version}`} data-testid="model-version" className="space-y-3 rounded-lg border border-border bg-card p-4">
      <header className="space-y-1">
        <h3 className="text-sm font-bold text-foreground">
          {version.name} <span className="font-mono">v{version.version}</span>
        </h3>
        <p className="text-xs text-muted-foreground">
          by <span className="font-mono">{version.author_id}</span> · {formatTimestamp(version.created_at)}
        </p>
        <p className="text-sm text-foreground">{version.why}</p>
      </header>

      {unreviewed.length > 0 && (
        <p className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200" data-testid="unreviewed-warning">
          {unreviewed.length} edge{unreviewed.length === 1 ? " is" : "s are"} proposed and unreviewed: every
          result on this version is provisional, and an analysis that requires an approved graph refuses it.
        </p>
      )}

      <div className="text-xs">
        <span className="font-semibold text-foreground">Nodes: </span>
        <span className="font-mono text-muted-foreground">{version.graph.nodes.join(", ") || "—"}</span>
        {version.graph.latent.length > 0 && (
          <>
            <br />
            <span className="font-semibold text-foreground">Latent (unmeasured): </span>
            <span className="font-mono text-muted-foreground">{version.graph.latent.join(", ")}</span>
          </>
        )}
      </div>

      <EdgeTable edges={version.graph.edges} caption="Edges" authors={version.edge_authors} />

      {version.assumptions.length > 0 && (
        <div className="text-xs">
          <h4 className="font-semibold text-foreground">Assumptions the graph cannot state</h4>
          <ul className="mt-1 space-y-1">
            {version.assumptions.map((a) => (
              <li key={`${a.kind}:${a.statement}`}>
                <span className="font-mono text-muted-foreground">{a.kind}</span> — {a.statement}
                {a.about.length > 0 && <span className="text-muted-foreground"> (about {a.about.join(", ")})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {version.reviews.length > 0 && (
        <div className="text-xs">
          <h4 className="font-semibold text-foreground">Reviews that produced this version</h4>
          <ul className="mt-1 space-y-1">
            {version.reviews.map((r) => (
              <li key={r.edge}>
                <span className="font-mono">{r.edge}</span>: <span className="font-mono">{r.status}</span> — {r.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="break-all font-mono text-[11px] text-muted-foreground">digest {version.digest}</p>
    </article>
  );
}
