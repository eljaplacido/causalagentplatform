/**
 * ModelDiffView — GET /api/v1/models/{name}/diff, as returned.
 *
 * A removed edge is a new assumption (that the effect it described does not
 * exist), so removals are listed as prominently as additions.
 */

import type { ModelAssumption, ModelDiffResponse } from "@/lib/api";
import { EdgeTable, edgeLabel } from "./ModelVersionView";

function isEmpty(d: ModelDiffResponse): boolean {
  return Object.values(d).every((v) => Array.isArray(v) && v.length === 0);
}

function NameList({ label, names }: { label: string; names: string[] }) {
  if (names.length === 0) return null;
  return (
    <p className="text-xs">
      <span className="font-semibold text-foreground">{label}: </span>
      <span className="font-mono text-muted-foreground">{names.join(", ")}</span>
    </p>
  );
}

function AssumptionList({ label, items }: { label: string; items: ModelAssumption[] }) {
  if (items.length === 0) return null;
  return (
    <div className="text-xs">
      <h4 className="font-semibold text-foreground">{label}</h4>
      <ul className="list-disc pl-5 text-muted-foreground">
        {items.map((a) => (
          <li key={`${a.kind}:${a.statement}`}>
            <span className="font-mono">{a.kind}</span> — {a.statement}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ModelDiffView({ diff, from, to }: { diff: ModelDiffResponse; from: number; to: number }) {
  return (
    <div data-testid="model-diff" className="space-y-2">
      <h3 className="text-xs font-bold text-foreground">
        v{from} → v{to}
      </h3>
      {isEmpty(diff) ? (
        <p className="text-xs text-muted-foreground" data-testid="model-diff-empty">
          The two versions have the same graph and assumptions.
        </p>
      ) : (
        <>
          <EdgeTable edges={diff.added_edges} caption="Added edges" />
          <EdgeTable edges={diff.removed_edges} caption="Removed edges (each a new assumption: no such effect)" />
          {diff.changed_edges.length > 0 && (
            <table className="w-full text-xs">
              <caption className="mb-1 text-left font-semibold text-foreground">Changed edges</caption>
              <thead>
                <tr className="text-left text-muted-foreground">
                  <th scope="col" className="py-1 font-normal">Edge</th>
                  <th scope="col" className="py-1 font-normal">Before</th>
                  <th scope="col" className="py-1 font-normal">After</th>
                </tr>
              </thead>
              <tbody>
                {diff.changed_edges.map((c) => (
                  <tr key={c.edge} className="border-t border-border">
                    <th scope="row" className="py-1 text-left font-mono font-normal">{c.edge || edgeLabel(c.after)}</th>
                    <td className="py-1 font-mono">{c.before.origin} / {c.before.status}</td>
                    <td className="py-1 font-mono">{c.after.origin} / {c.after.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <NameList label="Added nodes" names={diff.added_nodes} />
          <NameList label="Removed nodes" names={diff.removed_nodes} />
          <NameList label="Newly latent" names={diff.latent_added} />
          <NameList label="No longer latent" names={diff.latent_removed} />
          <AssumptionList label="Assumptions added" items={diff.assumptions_added} />
          <AssumptionList label="Assumptions removed" items={diff.assumptions_removed} />
        </>
      )}
    </div>
  );
}
