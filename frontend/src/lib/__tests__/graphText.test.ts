/**
 * A kernel graph written back as the kernel's text syntax (src/lib/graphText.ts).
 *
 * Precondition: a graph as causal_decision.assumptions.canonical stores it.
 * Postcondition: the text parses back, in causal_core.graph.parse_graph, to the
 * same graph -- checked for GRAPH_MIXED, whose text was parsed by the kernel
 * when the fixture was made (src/test-fixtures/registry.ts).
 */

import { describe, expect, it } from "vitest";

import { graphToText } from "@/lib/graphText";
import { MODEL_V2 } from "@/test-fixtures/kernel";
import { GRAPH_MIXED, GRAPH_MIXED_TEXT } from "@/test-fixtures/registry";

describe("graphToText", () => {
  it("writes latent nodes, bidirected edges, non-default attributes and isolated nodes", () => {
    expect(graphToText(GRAPH_MIXED)).toBe(GRAPH_MIXED_TEXT);
  });

  it("writes a plain graph as plain edges", () => {
    expect(graphToText(MODEL_V2.graph)).toBe(
      "load -> minutes\nload -> playbook\nplaybook -> minutes\nteam -> minutes\nteam -> playbook"
    );
  });

  it("keeps a ruling an edge already has, and quotes a name the syntax would not read", () => {
    const g = {
      nodes: ["a b", "c"],
      edges: [{ source: "a b", target: "c", kind: "directed" as const, origin: "human" as const, status: "approved" as const, evidence: [] }],
      latent: [],
    };
    expect(graphToText(g)).toBe('"a b" -> c [status=approved]');
  });
});
