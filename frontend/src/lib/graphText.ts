/**
 * A kernel graph written back in the kernel's text syntax (causal_core.graph).
 *
 * Used to start a revision from the head version's graph: the text parses to
 * the same graph, so an unedited revision changes nothing (and the registry
 * refuses it for that reason). Written as:
 *
 *   latent U, V                  the latent nodes, if any
 *   a -> b                       each edge, in the graph's order
 *   a <-> b                      a bidirected edge
 *   x -> y [origin=llm_prior]    an attribute only where it is not the default
 *   z                            each node no edge mentions
 *
 * The defaults are the parser's: origin human, and status proposed for an
 * llm_prior edge, asserted for any other. An edge kept at approved or refuted
 * is written with that status -- a revision may keep a ruling, never make one.
 */

import type { KernelEdge, KernelGraph } from "@/lib/api";

const PLAIN_NAME = /^[A-Za-z_][\w.:-]*$/;

function name(n: string): string {
  return PLAIN_NAME.test(n) ? n : `"${n}"`;
}

function edgeText(e: KernelEdge): string {
  const arrow = e.kind === "bidirected" ? "<->" : "->";
  const attrs: string[] = [];
  if (e.origin !== "human") attrs.push(`origin=${e.origin}`);
  const defaultStatus = e.origin === "llm_prior" ? "proposed" : "asserted";
  if (e.status !== defaultStatus) attrs.push(`status=${e.status}`);
  return `${name(e.source)} ${arrow} ${name(e.target)}${attrs.length ? ` [${attrs.join(", ")}]` : ""}`;
}

export function graphToText(g: KernelGraph): string {
  const lines: string[] = [];
  if (g.latent.length > 0) lines.push(`latent ${g.latent.map(name).join(", ")}`);
  for (const e of g.edges) lines.push(edgeText(e));
  const mentioned = new Set([...g.latent, ...g.edges.flatMap((e) => [e.source, e.target])]);
  for (const n of g.nodes) if (!mentioned.has(n)) lines.push(name(n));
  return lines.join("\n");
}
