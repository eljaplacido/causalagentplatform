/**
 * Fixtures for the decision-ledger and assumption-registry view tests, produced
 * by the kernel, not written by hand: each value is the JSON the backend's
 * response models serialise, dumped from causal_core / causal_decision run on
 * data simulated from a structural model whose effect is set --
 *
 *   load ~ N(0,1); playbook = 1[0.9 load + logistic > 0];
 *   minutes = 60 + 8 load - 9 playbook + N(0, 6^2);  n = 2000, seed 0
 *
 * analysed under "load -> playbook; load -> minutes; playbook -> minutes" and
 * ruled on (action "adopt the incident playbook", decrease, minimum worthwhile
 * effect 2): verdict act, benefit ~9 (the set effect). The second record is
 * the same question under a graph with an unmeasured confounder U (refused,
 * NON_IDENTIFIABLE, so the policy asks for an experiment). The follow-up is the
 * sealed plan re-run on a later window (seed 7); the registry versions add a
 * confounder `team`, which invalidates the v1 adjustment set {load}.
 *
 * The views must show these numbers exactly as they are here.
 */

import type {
  CalibrationReportResponse,
  ChainReportResponse,
  DecisionRecordResponse,
  ImpactResponse,
  ModelDiffResponse,
  ModelVersionResponse,
  RealizationResponse,
} from "@/lib/api";

export const RECORD_ACT: DecisionRecordResponse = {
  "sequence": 0,
  "recorded_at": "2026-09-20T09:00:00+00:00",
  "decision": {
    "verdict": "act",
    "request": {
      "action": "adopt the incident playbook",
      "outcome": "minutes",
      "direction": "decrease",
      "risk_class": "medium",
      "reversible": true,
      "minimum_worthwhile_effect": 2.0,
      "harm_threshold": 0.5,
      "population": null,
      "horizon": null,
      "can_randomize": true,
      "randomization_unit": "unit",
      "units_per_day": null
    },
    "evidence": {
      "kind": "result",
      "status": "supported",
      "refusal_reason": null,
      "estimator": "aipw",
      "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
      "identification_strategy": "backdoor",
      "identification_tier": "A",
      "benefit": 9.09615087806273,
      "benefit_ci": [
        8.5148727454821,
        9.67742901064336
      ],
      "ci_level": 0.95,
      "e_value_ci": 3.8199538302429303,
      "refuters_failed": [],
      "triangulation_agree": true,
      "n_used": 2000,
      "outcome_sd": 9.84092537544467,
      "result_digest": "sha256:4a52518f18b3e641db7cfba675290cc512ecdc42a96fe7128e7b2192543cc717",
      "segments": [],
      "heterogeneity_differs": null
    },
    "checks": [
      {
        "name": "the whole interval clears the minimum worthwhile effect",
        "passed": true,
        "allows": "act",
        "detail": "interval [8.515, 9.677] against 2"
      },
      {
        "name": "the interval excludes material harm",
        "passed": true,
        "allows": "act",
        "detail": "lower bound 8.515 against a harm threshold of -0.5"
      },
      {
        "name": "the result is supported",
        "passed": true,
        "allows": "act",
        "detail": "status supported"
      },
      {
        "name": "identification is nonparametric",
        "passed": true,
        "allows": "act",
        "detail": "tier A"
      },
      {
        "name": "falsification tests passed",
        "passed": true,
        "allows": "act",
        "detail": "all passed"
      },
      {
        "name": "estimators with different assumptions agree",
        "passed": true,
        "allows": "act",
        "detail": "agree"
      },
      {
        "name": "robust to unmeasured confounding",
        "passed": true,
        "allows": "act",
        "detail": "E-value for the interval bound 3.82 against a bar of 1.5"
      },
      {
        "name": "the estimate is precise enough to act on",
        "passed": true,
        "allows": "act",
        "detail": "interval width is 0.13x the effect (limit 1.00x)"
      }
    ],
    "rationale": "Act: adopt the incident playbook. The estimate is ⟦claim:effect⟧ on minutes (⟦claim:ci.level⟧ CI ⟦claim:effect.ci.lo⟧ to ⟦claim:effect.ci.hi⟧, n = ⟦claim:n⟧, estimator aipw), read as a benefit of 8.515 to 9.677 in the requested direction (decrease).",
    "experiment": null,
    "what_would_change_it": [],
    "policy_digest": "sha256:e9a67b273e75d454e30b3a380415e765c2573cb1d07650d4359c3147c675e8eb",
    "request_digest": "sha256:2e83a809abefebb8ee74a0811603f6282664ecb3cf2b761f3e73212ee513e3b8",
    "decision_digest": "sha256:79bd2523e3295f93edca758e6f9d3b3af782f854403e800f68ec0334322f502d"
  },
  "evidence": {
    "outcome_kind": "result",
    "result_digest": "sha256:4a52518f18b3e641db7cfba675290cc512ecdc42a96fe7128e7b2192543cc717",
    "graph_digest": "sha256:6f4ff00f87a192dfbe3c1f756e1dc008062e9c0e5d8c9697dde019c2614f0518",
    "data_digest": "sha256:f8fb7cdc52c3dc50757cd2ec22383805e4f87a39fd93beb7b0a28fd57cb3c129",
    "query_digest": "sha256:c09c955d6f701f61eb6b8886c6f480e790e9c7e39430074aa4787a1ee59b61eb",
    "contract_version": "0.11.0",
    "core_version": "0.11.0",
    "model": null
  },
  "approval": null,
  "executed": false,
  "prev_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "entry_digest": "sha256:f2b225218417ba69a3b2aa5d77bef1105b42826f1ba4ee59d25abb440680d2b6"
};

export const RECORD_REFUSED: DecisionRecordResponse = {
  "sequence": 1,
  "recorded_at": "2026-09-21T09:00:00+00:00",
  "decision": {
    "verdict": "experiment",
    "request": {
      "action": "page the on-call lead first",
      "outcome": "minutes",
      "direction": "decrease",
      "risk_class": "medium",
      "reversible": true,
      "minimum_worthwhile_effect": 2.0,
      "harm_threshold": 0.0,
      "population": null,
      "horizon": null,
      "can_randomize": true,
      "randomization_unit": "unit",
      "units_per_day": null
    },
    "evidence": {
      "kind": "refusal",
      "status": null,
      "refusal_reason": "NON_IDENTIFIABLE",
      "estimator": null,
      "estimand": null,
      "identification_strategy": null,
      "identification_tier": null,
      "benefit": null,
      "benefit_ci": null,
      "ci_level": null,
      "e_value_ci": null,
      "refuters_failed": [],
      "triangulation_agree": null,
      "n_used": 0,
      "outcome_sd": 9.527858406010216,
      "result_digest": null,
      "segments": [],
      "heterogeneity_differs": null
    },
    "checks": [
      {
        "name": "the effect could be estimated from the data",
        "passed": false,
        "allows": "experiment",
        "detail": "NON_IDENTIFIABLE: the effect of playbook on minutes is not identified from the measured variables under this graph (FAIL: hedge F=['minutes', 'playbook'] F'=['minutes'])"
      }
    ],
    "rationale": "Run an experiment before acting: page the on-call lead first. No effect could be estimated: NON_IDENTIFIABLE.",
    "experiment": {
      "question": "Does 'page the on-call lead first' change minutes by at least 2 (decrease)?",
      "design": "randomized_units",
      "unit": "unit",
      "arms": [
        "control",
        "treated"
      ],
      "allocation": 0.5,
      "minimum_detectable_effect": 2.0,
      "outcome_sd": 9.527858406010216,
      "units_per_arm": 357,
      "estimated_days": null,
      "guardrails": [
        "stop if minutes moves against the desired direction by more than 0",
        "randomize the assignment; do not let the agent choose who is treated",
        "fix the analysis before the data arrive: this is the estimand above"
      ],
      "note": "Sized for 80% power at alpha 0.05, two-sided, equal allocation."
    },
    "what_would_change_it": [
      "the effect could be estimated from the data: NON_IDENTIFIABLE: the effect of playbook on minutes is not identified from the measured variables under this graph (FAIL: hedge F=['minutes', 'playbook'] F'=['minutes'])"
    ],
    "policy_digest": "sha256:e9a67b273e75d454e30b3a380415e765c2573cb1d07650d4359c3147c675e8eb",
    "request_digest": "sha256:30ee5a196874200f6962b71b2818e786b1645e735c5ab13f53ed1b05544a4559",
    "decision_digest": "sha256:f8cd6e59a60b93c73cebf3f341dbedf42c14bbefc8eda37a2d5aabfdc532e599"
  },
  "evidence": {
    "outcome_kind": "refusal",
    "result_digest": null,
    "graph_digest": "sha256:5bc33e60150771152e012e31bdfc70617169c077bc59b13b115187afb55ef6a7",
    "data_digest": null,
    "query_digest": "sha256:c09c955d6f701f61eb6b8886c6f480e790e9c7e39430074aa4787a1ee59b61eb",
    "contract_version": "0.11.0",
    "core_version": "0.11.0",
    "model": null
  },
  "approval": null,
  "executed": false,
  "prev_digest": "sha256:f2b225218417ba69a3b2aa5d77bef1105b42826f1ba4ee59d25abb440680d2b6",
  "entry_digest": "sha256:5f919d406b1c19312ace88a1f7924f0650734a802e093fa82fa469e79853e744"
};

export const CHAIN_OK: ChainReportResponse = {
  "ok": true,
  "length": 2,
  "head_digest": "sha256:5f919d406b1c19312ace88a1f7924f0650734a802e093fa82fa469e79853e744",
  "broken_at": null,
  "reason": ""
};

export const REALIZATION: RealizationResponse = {
  "decision_digest": "sha256:79bd2523e3295f93edca758e6f9d3b3af782f854403e800f68ec0334322f502d",
  "verdict": "act",
  "predicted_benefit": 9.09615087806273,
  "predicted_ci": [
    8.5148727454821,
    9.67742901064336
  ],
  "realized_benefit": 8.579432204469548,
  "realized_ci": [
    7.993660035249286,
    9.165204373689809
  ],
  "decidable": true,
  "undecidable_reason": null,
  "undecidable_detail": "",
  "point_in_predicted_ci": true,
  "intervals_overlap": true,
  "cleared_worthwhile": true,
  "realized_harm": false
};

/** decisions.py record_followup's explanation for REALIZATION. */
export const REALIZATION_EXPLANATION = "predicted 9.096, measured 8.579 in the same direction";

export const CALIBRATION_EMPTY: CalibrationReportResponse = {
  "n_decisions": 0,
  "n_acted": 0,
  "n_decidable": 0,
  "n_undecidable": 0,
  "undecidable_by_reason": {},
  "point_in_interval_rate": null,
  "interval_overlap_rate": null,
  "acted_and_worthwhile_rate": null,
  "acted_and_harmful": 0,
  "not_acted_but_worthwhile": 0,
  "by_verdict": {},
  "note": "Rates are over decidable realizations only, and the undecidable ones are counted beside them because they are not missing at random: a decision that was acted on everywhere is exactly the one whose effect can no longer be measured. `point_in_interval_rate` is not interval coverage of the truth -- the realized figure is itself an estimate, with error this comparison ignores; `interval_overlap_rate` is the weaker statement that accounts for both."
};

export const CALIBRATION_ONE: CalibrationReportResponse = {
  "n_decisions": 1,
  "n_acted": 1,
  "n_decidable": 1,
  "n_undecidable": 0,
  "undecidable_by_reason": {},
  "point_in_interval_rate": 1.0,
  "interval_overlap_rate": 1.0,
  "acted_and_worthwhile_rate": 1.0,
  "acted_and_harmful": 0,
  "not_acted_but_worthwhile": 0,
  "by_verdict": {
    "act": 1
  },
  "note": "Rates are over decidable realizations only, and the undecidable ones are counted beside them because they are not missing at random: a decision that was acted on everywhere is exactly the one whose effect can no longer be measured. `point_in_interval_rate` is not interval coverage of the truth -- the realized figure is itself an estimate, with error this comparison ignores; `interval_overlap_rate` is the weaker statement that accounts for both."
};

export const MODEL_V1: ModelVersionResponse = {
  "name": "incident-minutes",
  "version": 1,
  "graph": {
    "nodes": [
      "load",
      "minutes",
      "playbook"
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
      }
    ],
    "latent": []
  },
  "assumptions": [],
  "parent_digest": null,
  "author_id": "analyst-ryo",
  "created_at": "2026-09-19T08:00:00+00:00",
  "why": "Initial model of how the playbook moves resolution minutes",
  "edge_authors": {
    "load -> minutes": "analyst-ryo",
    "load -> playbook": "analyst-ryo",
    "playbook -> minutes": "analyst-ryo"
  },
  "reviews": [],
  "digest": "sha256:6cadd1414250b0af369ec9fd51b47b66f915de94bcb8f2c349766f0ec568f07b"
};

export const MODEL_V2: ModelVersionResponse = {
  "name": "incident-minutes",
  "version": 2,
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
        "status": "asserted",
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
  "parent_digest": "sha256:6cadd1414250b0af369ec9fd51b47b66f915de94bcb8f2c349766f0ec568f07b",
  "author_id": "analyst-ryo",
  "created_at": "2026-09-22T08:00:00+00:00",
  "why": "Team size confounds playbook adoption and minutes",
  "edge_authors": {
    "load -> minutes": "analyst-ryo",
    "load -> playbook": "analyst-ryo",
    "playbook -> minutes": "analyst-ryo",
    "team -> minutes": "analyst-ryo",
    "team -> playbook": "analyst-ryo"
  },
  "reviews": [],
  "digest": "sha256:d41879513b147cbb955fb29b6fdaaf2e8b6502de74df21c50e3bc78a0254d36e"
};

export const DIFF_V1_V2: ModelDiffResponse = {
  "added_edges": [
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
      "status": "asserted",
      "evidence": []
    }
  ],
  "removed_edges": [],
  "changed_edges": [],
  "added_nodes": [
    "team"
  ],
  "removed_nodes": [],
  "latent_added": [],
  "latent_removed": [],
  "assumptions_added": [
    {
      "kind": "no_interference",
      "statement": "One incident's playbook use does not change another's minutes",
      "about": [
        "playbook",
        "minutes"
      ]
    }
  ],
  "assumptions_removed": []
};

/** impact(v1, v2) for RECORD_ACT's query, adjust_for pinned to {load}. */
export const IMPACT_V1_TO_V2: ImpactResponse = {
  "status": "invalidated",
  "detail": "the adjustment set the estimate used, {load}, is not valid under the new graph: non-causal path left open: playbook <- team -> minutes",
  "old_strategy": "backdoor",
  "new_strategy": "backdoor",
  "old_adjustment_set": [
    "load"
  ],
  "new_adjustment_set": [
    "load",
    "team"
  ],
  "unreviewed_before": [],
  "unreviewed_after": [],
  "digest": "sha256:5ba7e8ca56a9e4496640d82cd098e547f7c7ba0622ecb23cf7bf9bb71e0834aa"
};
