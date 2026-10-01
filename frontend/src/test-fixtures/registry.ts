/**
 * Fixtures for model authoring in the assumption registry, produced by
 * causal_decision, not written by hand.
 *
 *   MODEL_V3_REVIEWED  causal_decision.assumptions.review(MODEL_V2, [approve
 *                      "team -> playbook"], reviewer "governance-kim"), where
 *                      MODEL_V2 is the kernel.ts fixture (its digest recomputes).
 *   SELF_REVIEW_DETAIL the SelfReviewError the same review raises when the
 *                      reviewer is the edge's author ("analyst-ryo"); the API
 *                      returns it as the 403 detail.
 *   GRAPH_MIXED        causal_decision.assumptions.canonical(GRAPH_MIXED_TEXT):
 *                      a latent node, a bidirected edge, an isolated node, and
 *                      edges whose origin or status is not the default. The UI
 *                      writes GRAPH_MIXED back as GRAPH_MIXED_TEXT, which the
 *                      kernel parses to the same graph.
 */

import type { KernelGraph, ModelVersionResponse } from "@/lib/api";

export const MODEL_V3_REVIEWED: ModelVersionResponse = {
  "name": "incident-minutes",
  "version": 3,
  "graph": {
    "nodes": [
      "load",
      "minutes",
      "playbook",
      "team"
    ],
    "edges": [
      {
        "source": "load",
        "target": "minutes",
        "kind": "directed",
        "origin": "human",
        "status": "asserted",
        "evidence": []
      },
      {
        "source": "load",
        "target": "playbook",
        "kind": "directed",
        "origin": "human",
        "status": "asserted",
        "evidence": []
      },
      {
        "source": "playbook",
        "target": "minutes",
        "kind": "directed",
        "origin": "human",
        "status": "asserted",
        "evidence": []
      },
      {
        "source": "team",
        "target": "minutes",
        "kind": "directed",
        "origin": "human",
        "status": "asserted",
        "evidence": []
      },
      {
        "source": "team",
        "target": "playbook",
        "kind": "directed",
        "origin": "human",
        "status": "approved",
        "evidence": []
      }
    ],
    "latent": []
  },
  "assumptions": [
    {
      "kind": "no_interference",
      "statement": "One incident's playbook use does not change another's minutes",
      "about": [
        "playbook",
        "minutes"
      ]
    }
  ],
  "parent_digest": "sha256:d41879513b147cbb955fb29b6fdaaf2e8b6502de74df21c50e3bc78a0254d36e",
  "author_id": "governance-kim",
  "created_at": "2026-09-23T08:00:00+00:00",
  "why": "review: approved team -> playbook: team size is recorded before the playbook is chosen",
  "edge_authors": {
    "load -> minutes": "analyst-ryo",
    "load -> playbook": "analyst-ryo",
    "playbook -> minutes": "analyst-ryo",
    "team -> minutes": "analyst-ryo",
    "team -> playbook": "analyst-ryo"
  },
  "reviews": [
    {
      "edge": "team -> playbook",
      "status": "approved",
      "reason": "team size is recorded before the playbook is chosen"
    }
  ],
  "digest": "sha256:712533ee6a4b4334edc5fd8b7ff5bd2f50e731f3e9c1f326eec25d06bdfb9ac4"
};

export const SELF_REVIEW_DETAIL = "'analyst-ryo' introduced 'team -> playbook' and so cannot review it";

export const GRAPH_MIXED: KernelGraph = {
  "nodes": [
    "U",
    "a",
    "b",
    "q",
    "x",
    "y",
    "z"
  ],
  "edges": [
    {
      "source": "U",
      "target": "x",
      "kind": "directed",
      "origin": "human",
      "status": "asserted",
      "evidence": []
    },
    {
      "source": "a",
      "target": "b",
      "kind": "bidirected",
      "origin": "human",
      "status": "asserted",
      "evidence": []
    },
    {
      "source": "q",
      "target": "y",
      "kind": "directed",
      "origin": "human",
      "status": "proposed",
      "evidence": []
    },
    {
      "source": "x",
      "target": "y",
      "kind": "directed",
      "origin": "llm_prior",
      "status": "proposed",
      "evidence": []
    }
  ],
  "latent": [
    "U"
  ]
};

export const GRAPH_MIXED_TEXT = "latent U\nU -> x\na <-> b\nq -> y [status=proposed]\nx -> y [origin=llm_prior]\nz";
