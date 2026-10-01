/**
 * Fixtures for the "New decision" flow (/decisions/new), produced by the
 * kernel, not written by hand: each response is the JSON POST /api/v1/decisions
 * returned (causal_core.analyze, then causal_decision.decide and explain) on data
 * simulated from a structural model whose effect is set --
 *
 *   load ~ N(0,1); playbook = 1[0.9 load + logistic > 0];
 *   minutes = 60 + 8 load - 9 playbook + N(0, 6^2);  n = 400, numpy seed 0
 *
 * DECIDE_CSV is that sample written to 3 decimals, and the analyses ran on
 * exactly those values (as the page parses and posts them). The question:
 * action "adopt the incident playbook", treatment playbook, outcome minutes,
 * decrease, minimum worthwhile effect 2, harm threshold 0.5, medium risk,
 * reversible.
 *
 *   DECIDE_ACT      graph "load -> playbook; load -> minutes; playbook -> minutes":
 *                   a result near the set effect (benefit ~9).
 *   DECIDE_REFUSED  the same graph plus an unmeasured confounder U of playbook
 *                   and minutes: the kernel refuses (NON_IDENTIFIABLE).
 *   DECIDE_MODEL    the first graph registered as model "incident-playbook" v1
 *                   (its edges unreviewed) and named by the request instead.
 *   MODELS_LIST     GET /api/v1/models after that registration.
 *
 * The views must show these numbers exactly as they are here.
 */

import type { DecideResponse, ModelSummaryResponse } from "@/lib/api";

export const DECIDE_CSV = "load,playbook,minutes\n0.126,0,56.864\n-0.132,1,46.096\n0.640,1,60.371\n0.105,1,57.962\n-0.536,1,40.384\n0.362,1,55.328\n1.304,1,66.161\n0.947,1,52.086\n-0.704,1,42.355\n-1.265,1,34.648\n-0.623,0,47.268\n0.041,0,60.942\n-2.325,0,36.983\n-0.219,0,62.036\n-1.246,1,40.856\n-0.732,0,56.618\n-0.544,0,53.892\n-0.316,0,53.660\n0.412,1,53.749\n1.043,1,59.310\n-0.129,0,54.737\n1.366,1,64.491\n-0.665,0,59.158\n0.352,1,54.761\n0.903,1,68.509\n0.094,1,47.958\n-0.743,1,48.176\n-0.922,0,50.174\n-0.458,0,57.746\n0.220,0,56.782\n-1.010,0,58.614\n-0.209,1,50.383\n-0.159,1,56.880\n0.541,0,56.730\n0.215,1,49.756\n0.355,1,48.488\n-0.654,0,51.578\n-0.130,1,45.807\n0.784,1,56.549\n1.493,1,62.844\n-1.259,0,49.682\n1.514,0,68.778\n1.346,1,62.892\n0.781,1,62.504\n0.264,1,47.702\n-0.314,0,57.494\n1.458,1,62.220\n1.960,1,69.492\n1.802,1,65.030\n1.315,1,61.427\n0.357,0,56.912\n-1.208,1,41.464\n-0.004,1,45.433\n0.656,0,68.346\n-1.288,0,49.074\n0.395,1,54.400\n0.430,1,49.092\n0.696,1,61.391\n-1.184,0,54.681\n-0.662,0,59.876\n-0.436,0,69.873\n-1.170,1,41.326\n1.739,1,72.135\n-0.496,0,55.290\n0.329,1,55.996\n-0.259,0,60.123\n1.583,0,74.255\n1.320,1,65.512\n0.633,1,54.243\n-2.204,0,42.618\n0.052,0,63.628\n0.684,0,75.957\n1.004,1,53.978\n-0.618,0,44.220\n1.822,1,62.672\n-1.320,0,49.976\n-0.662,0,55.758\n0.935,1,59.034\n0.049,1,58.489\n2.002,1,72.875\n0.189,1,52.213\n-0.633,0,52.117\n-0.378,0,54.903\n-1.091,0,49.133\n-1.278,0,48.425\n0.630,1,46.607\n0.581,1,52.883\n1.295,1,58.807\n-0.755,0,52.830\n1.689,1,62.987\n-0.287,0,61.733\n1.574,1,60.404\n-0.433,0,53.906\n-0.735,1,48.368\n0.250,1,51.586\n1.031,0,69.550\n0.161,0,65.304\n-0.586,0,57.848\n-1.341,0,50.771\n-1.402,1,38.609\n0.503,1,59.113\n0.990,1,59.971\n-0.164,1,46.657\n-1.074,0,50.500\n0.873,1,50.676\n-1.280,0,43.988\n-0.713,0,42.998\n0.621,1,51.889\n-2.250,0,50.012\n0.386,1,50.752\n-0.582,0,60.072\n0.109,0,60.854\n-0.076,0,55.191\n0.202,1,60.647\n0.694,0,69.047\n-0.758,0,43.422\n1.421,1,68.616\n0.726,1,50.360\n0.844,1,56.682\n1.165,1,64.328\n0.788,1,55.502\n0.844,1,64.466\n0.076,1,56.172\n-1.427,0,39.125\n-0.135,1,47.085\n-0.770,0,55.536\n-1.423,0,45.168\n0.258,0,60.770\n-0.569,0,60.250\n-1.030,0,53.662\n-1.043,0,46.132\n0.268,1,54.195\n0.359,1,50.196\n1.322,1,54.289\n-0.014,1,44.108\n1.042,1,61.056\n1.402,1,62.049\n1.150,0,69.234\n-2.365,0,34.200\n1.229,1,59.726\n0.340,1,47.494\n0.424,1,48.832\n0.371,1,52.979\n0.383,0,54.762\n0.319,0,66.571\n-0.359,0,72.050\n-1.902,0,47.540\n-0.109,0,52.871\n-0.804,1,42.943\n1.080,0,59.309\n-0.289,1,46.424\n0.083,1,54.712\n-0.850,0,56.739\n-0.511,1,40.725\n-0.012,0,61.703\n-1.485,0,55.055\n0.301,0,72.936\n-0.106,0,54.947\n-1.186,0,45.329\n-2.398,0,41.141\n0.513,1,37.536\n-0.298,0,54.433\n-0.530,0,54.134\n-0.236,0,55.355\n1.816,1,56.029\n-0.050,0,58.128\n0.087,0,56.033\n-1.487,0,52.648\n1.647,1,58.668\n0.917,1,56.646\n1.067,1,58.138\n0.048,1,54.787\n0.917,1,43.058\n0.371,1,51.924\n0.613,0,69.463\n-0.152,1,47.612\n-1.474,0,39.047\n1.029,1,61.190\n-1.935,0,46.540\n-0.240,0,56.458\n-0.205,0,51.405\n-1.043,0,47.213\n0.613,1,54.022\n-0.200,0,53.144\n-0.437,1,35.984\n0.520,1,50.543\n-0.477,1,46.817\n1.389,1,59.069\n0.351,1,53.358\n-0.474,0,56.707\n-1.944,1,40.825\n-1.308,0,62.761\n1.087,0,73.085\n-0.051,1,42.169\n-0.283,1,32.793\n1.643,1,63.579\n-1.283,0,50.167\n-0.586,0,48.343\n-0.473,0,57.851\n0.586,1,51.089\n-0.664,0,57.108\n-0.613,0,53.140\n-1.605,0,49.544\n0.729,1,46.376\n0.806,0,63.818\n-0.476,0,55.297\n0.163,1,43.757\n-1.293,0,60.953\n-0.472,1,43.981\n1.378,1,70.363\n0.136,0,57.100\n2.310,1,68.103\n-0.787,0,60.806\n0.580,1,57.465\n-0.196,0,59.588\n0.566,1,57.122\n-0.007,1,42.745\n-0.561,0,53.173\n-0.868,1,38.320\n3.066,1,76.713\n-0.077,1,47.117\n-2.017,0,43.602\n-0.649,0,54.347\n0.678,1,56.206\n-0.500,0,55.791\n1.360,0,66.969\n1.002,0,61.695\n-0.152,1,45.795\n-0.472,0,62.652\n-1.005,0,54.207\n-0.700,0,57.922\n-1.473,0,56.495\n1.204,0,62.559\n1.591,1,66.785\n-1.256,1,34.500\n-1.182,0,48.541\n-1.769,1,39.757\n-0.964,0,61.975\n-3.106,0,30.456\n-1.142,0,50.293\n1.297,1,68.313\n-0.346,0,48.296\n0.855,1,60.009\n-0.489,0,54.239\n1.761,1,59.795\n0.199,0,62.474\n-0.382,1,51.514\n2.552,1,65.947\n-0.324,0,59.684\n-1.221,0,51.270\n0.202,0,54.164\n-0.039,0,69.010\n1.066,1,66.070\n-0.922,0,47.467\n0.805,1,53.918\n0.853,1,62.447\n-0.668,0,51.686\n0.163,0,50.268\n-0.831,0,59.647\n2.346,1,69.819\n-0.704,0,65.809\n-0.453,0,58.520\n-1.066,1,43.619\n-0.346,0,74.478\n-0.006,1,49.922\n0.768,0,60.431\n-0.610,0,56.491\n-0.186,0,65.328\n-1.416,0,41.677\n-0.827,0,47.931\n2.756,1,75.745\n1.041,1,40.146\n-0.781,0,47.193\n-1.337,0,54.074\n-0.976,1,39.675\n-0.022,0,50.068\n0.035,0,71.831\n-0.744,0,45.582\n-1.287,0,46.567\n1.422,1,60.143\n0.452,1,55.112\n-0.375,0,54.786\n-0.221,0,57.749\n-0.530,0,56.109\n-2.936,0,35.992\n0.116,1,52.485\n-1.071,0,37.162\n-1.003,0,54.625\n-0.640,1,37.451\n0.732,1,43.859\n-1.171,1,49.924\n-1.434,0,40.813\n0.640,1,57.198\n0.754,0,61.400\n-0.959,1,39.258\n0.562,1,58.401\n-0.292,0,51.378\n0.301,0,64.646\n-1.261,1,43.196\n0.833,0,73.650\n1.203,1,58.609\n0.637,1,62.376\n0.558,1,65.791\n-3.772,0,39.342\n0.261,1,56.601\n-0.025,1,53.491\n-0.147,1,66.920\n-0.631,1,59.348\n0.055,1,46.843\n0.412,1,59.839\n-0.264,1,52.501\n-0.463,0,56.727\n1.230,0,70.758\n-1.105,0,54.084\n1.030,0,73.865\n0.177,0,62.727\n-0.804,0,55.604\n-0.290,1,57.033\n-0.920,1,45.546\n0.675,0,68.671\n0.348,1,59.725\n-0.557,0,65.345\n-1.102,1,49.544\n0.302,1,55.677\n0.957,1,59.906\n-0.114,1,42.748\n0.418,1,56.099\n-0.376,0,50.769\n0.068,1,45.396\n-0.291,0,61.573\n0.294,0,61.749\n-1.510,0,50.755\n0.644,0,61.392\n-0.230,1,56.368\n0.359,1,54.731\n-0.340,0,64.404\n0.320,0,66.604\n-1.073,1,43.411\n1.189,1,57.642\n-1.704,0,46.561\n-1.039,1,47.654\n0.236,0,66.071\n1.463,1,55.529\n0.278,1,59.379\n-0.248,1,47.733\n-1.425,1,44.491\n-0.191,0,54.285\n-0.020,1,54.668\n1.691,1,59.745\n0.622,0,65.753\n-1.529,0,45.979\n2.027,1,65.501\n-0.395,0,53.440\n-0.879,0,52.043\n1.475,1,52.355\n-0.050,1,55.862\n-0.367,0,62.831\n0.219,1,50.094\n0.845,0,58.481\n0.993,1,55.066\n-1.375,0,54.684\n1.998,1,70.741\n0.947,0,65.773\n-0.379,1,53.350\n-0.819,0,47.202\n-0.969,1,39.572\n0.123,1,54.835\n-0.648,0,54.240\n-0.765,0,50.346\n0.811,0,51.417\n0.365,0,66.944\n-0.395,0,58.786\n0.734,1,46.416\n1.367,0,74.541\n-1.094,1,42.158\n-0.603,0,56.847\n0.943,0,73.212\n0.719,1,52.312\n0.227,1,57.095\n1.162,1,64.402\n-1.088,0,55.863\n-1.479,0,57.983\n-0.867,1,48.020\n0.123,0,57.611\n-0.796,0,64.422\n-0.487,1,40.458\n-0.975,1,40.424\n-0.620,0,49.250\n-1.005,0,51.345\n0.367,0,69.433\n0.795,1,65.146\n-0.480,1,49.938\n-0.207,1,45.686\n-0.581,0,52.030\n0.531,0,60.631\n0.089,1,57.429\n1.594,1,58.314\n-1.095,0,56.423\n0.363,0,62.704\n0.444,0,64.584\n";

export const DECIDE_ACT: DecideResponse = {
  "verdict": "act",
  "explanation": "ACT: Act: adopt the incident playbook. The estimate is -9.009 on minutes (95% CI -10.2 to -7.819, n = 400, estimator aipw), read as a benefit of 7.819 to 10.2 in the requested direction (decrease).\n\nProceed. Record the outcome and compare it with this estimate.\n\nChecks:\n  [ok ] the whole interval clears the minimum worthwhile effect -- interval [7.819, 10.2] against 2\n  [ok ] the interval excludes material harm -- lower bound 7.819 against a harm threshold of -0.5\n  [ok ] the result is supported -- status supported\n  [ok ] identification is nonparametric -- tier A\n  [ok ] falsification tests passed -- all passed\n  [ok ] estimators with different assumptions agree -- agree\n  [ok ] robust to unmeasured confounding -- E-value for the interval bound 3.89 against a bar of 1.5\n  [ok ] the estimate is precise enough to act on -- interval width is 0.26x the effect (limit 1.00x)",
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
      "benefit": 9.00921617223674,
      "benefit_ci": [
        7.819297328790351,
        10.19913501568313
      ],
      "ci_level": 0.95,
      "e_value_ci": 3.8947750766545033,
      "refuters_failed": [],
      "triangulation_agree": true,
      "n_used": 400,
      "outcome_sd": 8.851441844195666,
      "result_digest": "sha256:ae35145643e02c2220b89371ffb92a9fa1bd13cec2395812cffe8691342f760b",
      "segments": [],
      "heterogeneity_differs": null
    },
    "checks": [
      {
        "name": "the whole interval clears the minimum worthwhile effect",
        "passed": true,
        "allows": "act",
        "detail": "interval [7.819, 10.2] against 2"
      },
      {
        "name": "the interval excludes material harm",
        "passed": true,
        "allows": "act",
        "detail": "lower bound 7.819 against a harm threshold of -0.5"
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
        "detail": "E-value for the interval bound 3.89 against a bar of 1.5"
      },
      {
        "name": "the estimate is precise enough to act on",
        "passed": true,
        "allows": "act",
        "detail": "interval width is 0.26x the effect (limit 1.00x)"
      }
    ],
    "rationale": "Act: adopt the incident playbook. The estimate is ⟦claim:effect⟧ on minutes (⟦claim:ci.level⟧ CI ⟦claim:effect.ci.lo⟧ to ⟦claim:effect.ci.hi⟧, n = ⟦claim:n⟧, estimator aipw), read as a benefit of 7.819 to 10.2 in the requested direction (decrease).",
    "experiment": null,
    "what_would_change_it": [],
    "policy_digest": "sha256:e9a67b273e75d454e30b3a380415e765c2573cb1d07650d4359c3147c675e8eb",
    "request_digest": "sha256:2e83a809abefebb8ee74a0811603f6282664ecb3cf2b761f3e73212ee513e3b8",
    "decision_digest": "sha256:eb8ea15dcee57d3688d3ef2ae24d9a819620ee77382709626520e4bffb677960"
  },
  "outcome": {
    "kind": "result",
    "status": "supported",
    "query": {
      "rung": "intervention",
      "treatment": "playbook",
      "outcome": "minutes",
      "estimand": "ate",
      "design": "observational",
      "treatment_levels": null,
      "adjust_for": null,
      "instruments": null,
      "treatment_proxies": [],
      "outcome_proxies": [],
      "negative_control_outcomes": [],
      "expected_treated_share": null,
      "segment_by": [],
      "cluster": null,
      "washout": null,
      "sequence": null,
      "unit": null,
      "time": null,
      "covariates": [],
      "control_group": "never_treated",
      "population": null,
      "horizon": null
    },
    "certificate": {
      "status": "identified",
      "strategy": "backdoor",
      "tier": "A",
      "estimand": "Σ_{load} E[minutes | playbook=t, load] P(load)",
      "adjustment_set": [
        "load"
      ],
      "instruments": [],
      "mediators": [],
      "treatment_proxies": [],
      "outcome_proxies": [],
      "conditions": [
        {
          "name": "adjustment set is observed",
          "holds": true,
          "detail": ""
        },
        {
          "name": "no adjustment variable is a descendant of a causal node",
          "holds": true,
          "detail": ""
        },
        {
          "name": "all non-causal paths are blocked",
          "holds": true,
          "detail": ""
        }
      ],
      "assumptions": [
        "causal sufficiency relative to the graph: every absent edge and absent <-> is a claim",
        "positivity: every adjustment stratum has a non-zero chance of each treatment level",
        "consistency and no interference between units (SUTVA)"
      ],
      "general_id": "Σ_{load} [P(load) P(minutes | load, playbook)]",
      "unreviewed_edges": [],
      "engine": "causal_core.identification/0.2",
      "graph_digest": "sha256:6f4ff00f87a192dfbe3c1f756e1dc008062e9c0e5d8c9697dde019c2614f0518"
    },
    "primary": {
      "estimator": "aipw",
      "family": "doubly_robust",
      "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
      "point": -9.00921617223674,
      "se": 0.6071126065745684,
      "ci": {
        "level": 0.95,
        "lo": -10.19913501568313,
        "hi": -7.819297328790351,
        "method": "wald/influence-function (cross-fitted)"
      },
      "ci_null_reason": null,
      "n_used": 400,
      "assumptions": [
        "either the propensity model or the outcome model is correctly specified",
        "nuisance learners: linear"
      ],
      "details": {
        "share_clipped": 0.0,
        "folds": 5.0
      }
    },
    "estimates": [
      {
        "estimator": "aipw",
        "family": "doubly_robust",
        "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
        "point": -9.00921617223674,
        "se": 0.6071126065745684,
        "ci": {
          "level": 0.95,
          "lo": -10.19913501568313,
          "hi": -7.819297328790351,
          "method": "wald/influence-function (cross-fitted)"
        },
        "ci_null_reason": null,
        "n_used": 400,
        "assumptions": [
          "either the propensity model or the outcome model is correctly specified",
          "nuisance learners: linear"
        ],
        "details": {
          "share_clipped": 0.0,
          "folds": 5.0
        }
      },
      {
        "estimator": "regression_adjustment",
        "family": "outcome_model",
        "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
        "point": -9.205128616315733,
        "se": 0.6123970174145613,
        "ci": {
          "level": 0.95,
          "lo": -10.405404714688022,
          "hi": -8.004852517943444,
          "method": "wald/hc2+covariate-mean"
        },
        "ci_null_reason": null,
        "n_used": 400,
        "assumptions": [
          "outcome linear in the covariates within each arm"
        ],
        "details": {}
      },
      {
        "estimator": "ipw_hajek",
        "family": "weighting",
        "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
        "point": -8.700702439723102,
        "se": 0.6209188247266281,
        "ci": {
          "level": 0.95,
          "lo": -9.917680973510231,
          "hi": -7.483723905935973,
          "method": "wald/m-estimation"
        },
        "ci_null_reason": null,
        "n_used": 400,
        "assumptions": [
          "propensity logistic-linear in the covariates"
        ],
        "details": {
          "share_clipped": 0.0
        }
      }
    ],
    "diagnostics": {
      "n_rows": 400,
      "n_used": 400,
      "n_dropped_missing": 0,
      "treatment_kind": "binary",
      "n_treated": 197,
      "n_control": 203,
      "outcome_sd": 8.851441844195666,
      "propensity": {
        "min": 0.014946262795080417,
        "max": 0.96814779698316,
        "share_outside_bounds": 0.0,
        "ess_treated": 145.28889466360104,
        "ess_control": 160.67028029361268
      },
      "balance": [
        {
          "covariate": "load",
          "smd_raw": 0.9783473312302402,
          "smd_weighted": 0.048753800155049744
        }
      ],
      "srm_p_value": null,
      "n_clusters": null,
      "icc": null,
      "first_stage_f": null,
      "checks": [
        {
          "name": "sample size",
          "passed": true,
          "detail": "n=400"
        },
        {
          "name": "units per arm",
          "passed": true,
          "detail": "treated=197, control=203"
        },
        {
          "name": "overlap (positivity)",
          "passed": true,
          "detail": "0.0% have propensity outside [0.01, 0.99]"
        }
      ]
    },
    "refutations": [
      {
        "name": "placebo_treatment",
        "passed": true,
        "statistic": -0.40899384932683347,
        "p_value": 0.5586057122145056,
        "threshold": 0.01,
        "detail": "effect of a randomly permuted treatment; should be indistinguishable from 0"
      }
    ],
    "sensitivity": {
      "e_value": 4.487195344812698,
      "e_value_ci": 3.8947750766545033,
      "e_value_method": "approximate: RR = exp(0.91 × effect / sd(outcome)) (VanderWeele & Ding 2017)",
      "partial_r2_treatment": 0.35898379323783075,
      "robustness_value": 0.5190062849594973,
      "robustness_value_alpha": 0.4719921436174327,
      "parallel_trends": null
    },
    "triangulation": {
      "estimators": [
        "aipw",
        "regression_adjustment",
        "ipw_hajek"
      ],
      "agree": true,
      "pairs": [
        {
          "a": "aipw",
          "b": "regression_adjustment",
          "difference": 0.1959124440789921,
          "se_difference": 0.17075014145498332,
          "z": 1.14736329006581
        },
        {
          "a": "aipw",
          "b": "ipw_hajek",
          "difference": -0.30851373251363867,
          "se_difference": 0.2972511428210291,
          "z": -1.0378891384091016
        }
      ],
      "detail": "estimators with different modelling assumptions agree within sampling noise"
    },
    "segments": [],
    "heterogeneity": null,
    "sequential": null,
    "event_study": [],
    "claims": [
      {
        "id": "effect",
        "value": -9.00921617223674,
        "text": "-9.009",
        "unit": null,
        "path": "/primary/point"
      },
      {
        "id": "effect.se",
        "value": 0.6071126065745684,
        "text": "0.6071",
        "unit": null,
        "path": "/primary/se"
      },
      {
        "id": "effect.ci.lo",
        "value": -10.19913501568313,
        "text": "-10.2",
        "unit": null,
        "path": "/primary/ci/lo"
      },
      {
        "id": "effect.ci.hi",
        "value": -7.819297328790351,
        "text": "-7.819",
        "unit": null,
        "path": "/primary/ci/hi"
      },
      {
        "id": "ci.level",
        "value": 95.0,
        "text": "95",
        "unit": "%",
        "path": "/primary/ci/level"
      },
      {
        "id": "n",
        "value": 400.0,
        "text": "400",
        "unit": null,
        "path": "/diagnostics/n_used"
      },
      {
        "id": "e_value",
        "value": 4.487195344812698,
        "text": "4.487",
        "unit": null,
        "path": "/sensitivity/e_value"
      },
      {
        "id": "e_value.ci",
        "value": 3.8947750766545033,
        "text": "3.895",
        "unit": null,
        "path": "/sensitivity/e_value_ci"
      },
      {
        "id": "robustness_value",
        "value": 0.5190062849594973,
        "text": "0.519",
        "unit": null,
        "path": "/sensitivity/robustness_value"
      }
    ],
    "warnings": [],
    "provenance": {
      "contract_version": "0.11.0",
      "core_version": "0.11.0",
      "graph_digest": "sha256:6f4ff00f87a192dfbe3c1f756e1dc008062e9c0e5d8c9697dde019c2614f0518",
      "query_digest": "sha256:c09c955d6f701f61eb6b8886c6f480e790e9c7e39430074aa4787a1ee59b61eb",
      "data_digest": "sha256:482c2b5a557e03992b2dc996abae7237b669a938231a70da1701040ade3ce817",
      "policy_digest": "sha256:f4ca0544665bc58f09bbf690d2adaf1f38043920853d139291ee3cce0cccda8d",
      "seed": 0
    },
    "result_digest": "sha256:ae35145643e02c2220b89371ffb92a9fa1bd13cec2395812cffe8691342f760b"
  },
  "record": {
    "sequence": 0,
    "recorded_at": "2026-09-26T12:00:37.775735+00:00",
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
        "benefit": 9.00921617223674,
        "benefit_ci": [
          7.819297328790351,
          10.19913501568313
        ],
        "ci_level": 0.95,
        "e_value_ci": 3.8947750766545033,
        "refuters_failed": [],
        "triangulation_agree": true,
        "n_used": 400,
        "outcome_sd": 8.851441844195666,
        "result_digest": "sha256:ae35145643e02c2220b89371ffb92a9fa1bd13cec2395812cffe8691342f760b",
        "segments": [],
        "heterogeneity_differs": null
      },
      "checks": [
        {
          "name": "the whole interval clears the minimum worthwhile effect",
          "passed": true,
          "allows": "act",
          "detail": "interval [7.819, 10.2] against 2"
        },
        {
          "name": "the interval excludes material harm",
          "passed": true,
          "allows": "act",
          "detail": "lower bound 7.819 against a harm threshold of -0.5"
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
          "detail": "E-value for the interval bound 3.89 against a bar of 1.5"
        },
        {
          "name": "the estimate is precise enough to act on",
          "passed": true,
          "allows": "act",
          "detail": "interval width is 0.26x the effect (limit 1.00x)"
        }
      ],
      "rationale": "Act: adopt the incident playbook. The estimate is ⟦claim:effect⟧ on minutes (⟦claim:ci.level⟧ CI ⟦claim:effect.ci.lo⟧ to ⟦claim:effect.ci.hi⟧, n = ⟦claim:n⟧, estimator aipw), read as a benefit of 7.819 to 10.2 in the requested direction (decrease).",
      "experiment": null,
      "what_would_change_it": [],
      "policy_digest": "sha256:e9a67b273e75d454e30b3a380415e765c2573cb1d07650d4359c3147c675e8eb",
      "request_digest": "sha256:2e83a809abefebb8ee74a0811603f6282664ecb3cf2b761f3e73212ee513e3b8",
      "decision_digest": "sha256:eb8ea15dcee57d3688d3ef2ae24d9a819620ee77382709626520e4bffb677960"
    },
    "evidence": {
      "outcome_kind": "result",
      "result_digest": "sha256:ae35145643e02c2220b89371ffb92a9fa1bd13cec2395812cffe8691342f760b",
      "graph_digest": "sha256:6f4ff00f87a192dfbe3c1f756e1dc008062e9c0e5d8c9697dde019c2614f0518",
      "data_digest": "sha256:482c2b5a557e03992b2dc996abae7237b669a938231a70da1701040ade3ce817",
      "query_digest": "sha256:c09c955d6f701f61eb6b8886c6f480e790e9c7e39430074aa4787a1ee59b61eb",
      "contract_version": "0.11.0",
      "core_version": "0.11.0",
      "model": null
    },
    "approval": null,
    "executed": false,
    "prev_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    "entry_digest": "sha256:5080db0de366428ba128620db6c24ed6fdf66b0007ced82e92e7c5ff058fed9b"
  }
};

export const DECIDE_REFUSED: DecideResponse = {
  "verdict": "experiment",
  "explanation": "EXPERIMENT: Run an experiment before acting: adopt the incident playbook. No effect could be estimated: NON_IDENTIFIABLE.\n\nRun the experiment below, then ask again.\n\nChecks:\n  [no ] the effect could be estimated from the data -- NON_IDENTIFIABLE: the effect of playbook on minutes is not identified from the measured variables under this graph (FAIL: hedge F=['minutes', 'playbook'] F'=['minutes'])\n\nExperiment that would settle it:\n  Does 'adopt the incident playbook' change minutes by at least 2 (decrease)?\n  Randomize units, control vs treated, 308 units per arm.\n  Guardrail: stop if minutes moves against the desired direction by more than 0.5\n  Guardrail: randomize the assignment; do not let the agent choose who is treated\n  Guardrail: fix the analysis before the data arrive: this is the estimand above\n  Sized for 80% power at alpha 0.05, two-sided, equal allocation.",
  "decision": {
    "verdict": "experiment",
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
      "outcome_sd": 8.851441844195666,
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
    "rationale": "Run an experiment before acting: adopt the incident playbook. No effect could be estimated: NON_IDENTIFIABLE.",
    "experiment": {
      "question": "Does 'adopt the incident playbook' change minutes by at least 2 (decrease)?",
      "design": "randomized_units",
      "unit": "unit",
      "arms": [
        "control",
        "treated"
      ],
      "allocation": 0.5,
      "minimum_detectable_effect": 2.0,
      "outcome_sd": 8.851441844195666,
      "units_per_arm": 308,
      "estimated_days": null,
      "guardrails": [
        "stop if minutes moves against the desired direction by more than 0.5",
        "randomize the assignment; do not let the agent choose who is treated",
        "fix the analysis before the data arrive: this is the estimand above"
      ],
      "note": "Sized for 80% power at alpha 0.05, two-sided, equal allocation."
    },
    "what_would_change_it": [
      "the effect could be estimated from the data: NON_IDENTIFIABLE: the effect of playbook on minutes is not identified from the measured variables under this graph (FAIL: hedge F=['minutes', 'playbook'] F'=['minutes'])"
    ],
    "policy_digest": "sha256:e9a67b273e75d454e30b3a380415e765c2573cb1d07650d4359c3147c675e8eb",
    "request_digest": "sha256:2e83a809abefebb8ee74a0811603f6282664ecb3cf2b761f3e73212ee513e3b8",
    "decision_digest": "sha256:9dfcea18066d1f6fe6a56557a5122312e7862ad154a719d657a265a18800448a"
  },
  "outcome": {
    "kind": "refusal",
    "reason": "NON_IDENTIFIABLE",
    "detail": "the effect of playbook on minutes is not identified from the measured variables under this graph (FAIL: hedge F=['minutes', 'playbook'] F'=['minutes'])",
    "missing_information": [
      "measure the unobserved common cause(s) behind: minutes<->playbook",
      "declare an instrument: a variable that moves playbook, affects minutes only through playbook, and shares no unmeasured cause with minutes",
      "declare treatment and outcome proxies of the unmeasured confounder (proximal inference)",
      "randomize playbook (an experiment identifies the effect by design)"
    ],
    "query": {
      "rung": "intervention",
      "treatment": "playbook",
      "outcome": "minutes",
      "estimand": "ate",
      "design": "observational",
      "treatment_levels": null,
      "adjust_for": null,
      "instruments": null,
      "treatment_proxies": [],
      "outcome_proxies": [],
      "negative_control_outcomes": [],
      "expected_treated_share": null,
      "segment_by": [],
      "cluster": null,
      "washout": null,
      "sequence": null,
      "unit": null,
      "time": null,
      "covariates": [],
      "control_group": "never_treated",
      "population": null,
      "horizon": null
    },
    "certificate": {
      "status": "not_identified",
      "strategy": null,
      "tier": "D",
      "estimand": "not identified",
      "adjustment_set": [],
      "instruments": [],
      "mediators": [],
      "treatment_proxies": [],
      "outcome_proxies": [],
      "conditions": [],
      "assumptions": [],
      "general_id": "FAIL: hedge F=['minutes', 'playbook'] F'=['minutes']",
      "unreviewed_edges": [],
      "engine": "causal_core.identification/0.2",
      "graph_digest": "sha256:1bd3d74dd69db4e05d327c31e936509f39e34283ba07c3d01d9ca4c65de311c2"
    },
    "diagnostics": {
      "n_rows": 400,
      "n_used": 0,
      "n_dropped_missing": 0,
      "treatment_kind": "binary",
      "n_treated": null,
      "n_control": null,
      "outcome_sd": 8.851441844195666,
      "propensity": null,
      "balance": [],
      "srm_p_value": null,
      "n_clusters": null,
      "icc": null,
      "first_stage_f": null,
      "checks": []
    },
    "provenance": {
      "contract_version": "0.11.0",
      "core_version": "0.11.0",
      "graph_digest": "sha256:1bd3d74dd69db4e05d327c31e936509f39e34283ba07c3d01d9ca4c65de311c2",
      "query_digest": "sha256:c09c955d6f701f61eb6b8886c6f480e790e9c7e39430074aa4787a1ee59b61eb",
      "data_digest": null,
      "policy_digest": "sha256:f4ca0544665bc58f09bbf690d2adaf1f38043920853d139291ee3cce0cccda8d",
      "seed": 0
    }
  },
  "record": {
    "sequence": 1,
    "recorded_at": "2026-09-26T12:00:37.802533+00:00",
    "decision": {
      "verdict": "experiment",
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
        "outcome_sd": 8.851441844195666,
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
      "rationale": "Run an experiment before acting: adopt the incident playbook. No effect could be estimated: NON_IDENTIFIABLE.",
      "experiment": {
        "question": "Does 'adopt the incident playbook' change minutes by at least 2 (decrease)?",
        "design": "randomized_units",
        "unit": "unit",
        "arms": [
          "control",
          "treated"
        ],
        "allocation": 0.5,
        "minimum_detectable_effect": 2.0,
        "outcome_sd": 8.851441844195666,
        "units_per_arm": 308,
        "estimated_days": null,
        "guardrails": [
          "stop if minutes moves against the desired direction by more than 0.5",
          "randomize the assignment; do not let the agent choose who is treated",
          "fix the analysis before the data arrive: this is the estimand above"
        ],
        "note": "Sized for 80% power at alpha 0.05, two-sided, equal allocation."
      },
      "what_would_change_it": [
        "the effect could be estimated from the data: NON_IDENTIFIABLE: the effect of playbook on minutes is not identified from the measured variables under this graph (FAIL: hedge F=['minutes', 'playbook'] F'=['minutes'])"
      ],
      "policy_digest": "sha256:e9a67b273e75d454e30b3a380415e765c2573cb1d07650d4359c3147c675e8eb",
      "request_digest": "sha256:2e83a809abefebb8ee74a0811603f6282664ecb3cf2b761f3e73212ee513e3b8",
      "decision_digest": "sha256:9dfcea18066d1f6fe6a56557a5122312e7862ad154a719d657a265a18800448a"
    },
    "evidence": {
      "outcome_kind": "refusal",
      "result_digest": null,
      "graph_digest": "sha256:1bd3d74dd69db4e05d327c31e936509f39e34283ba07c3d01d9ca4c65de311c2",
      "data_digest": null,
      "query_digest": "sha256:c09c955d6f701f61eb6b8886c6f480e790e9c7e39430074aa4787a1ee59b61eb",
      "contract_version": "0.11.0",
      "core_version": "0.11.0",
      "model": null
    },
    "approval": null,
    "executed": false,
    "prev_digest": "sha256:5080db0de366428ba128620db6c24ed6fdf66b0007ced82e92e7c5ff058fed9b",
    "entry_digest": "sha256:dc36022e516ba8d0859c98f48e3507e396d8d471c7ce7bac2998fb3c5d1fd65d"
  }
};

export const DECIDE_MODEL: DecideResponse = {
  "verdict": "act",
  "explanation": "ACT: Act: adopt the incident playbook. The estimate is -9.009 on minutes (95% CI -10.2 to -7.819, n = 400, estimator aipw), read as a benefit of 7.819 to 10.2 in the requested direction (decrease).\n\nProceed. Record the outcome and compare it with this estimate.\n\nChecks:\n  [ok ] the whole interval clears the minimum worthwhile effect -- interval [7.819, 10.2] against 2\n  [ok ] the interval excludes material harm -- lower bound 7.819 against a harm threshold of -0.5\n  [ok ] the result is supported -- status supported\n  [ok ] identification is nonparametric -- tier A\n  [ok ] falsification tests passed -- all passed\n  [ok ] estimators with different assumptions agree -- agree\n  [ok ] robust to unmeasured confounding -- E-value for the interval bound 3.89 against a bar of 1.5\n  [ok ] the estimate is precise enough to act on -- interval width is 0.26x the effect (limit 1.00x)",
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
      "benefit": 9.00921617223674,
      "benefit_ci": [
        7.819297328790351,
        10.19913501568313
      ],
      "ci_level": 0.95,
      "e_value_ci": 3.8947750766545033,
      "refuters_failed": [],
      "triangulation_agree": true,
      "n_used": 400,
      "outcome_sd": 8.851441844195666,
      "result_digest": "sha256:661c647910abae10a31e8ff9bed0c17c00adc533c53414401ecafc5628159ffb",
      "segments": [],
      "heterogeneity_differs": null
    },
    "checks": [
      {
        "name": "the whole interval clears the minimum worthwhile effect",
        "passed": true,
        "allows": "act",
        "detail": "interval [7.819, 10.2] against 2"
      },
      {
        "name": "the interval excludes material harm",
        "passed": true,
        "allows": "act",
        "detail": "lower bound 7.819 against a harm threshold of -0.5"
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
        "detail": "E-value for the interval bound 3.89 against a bar of 1.5"
      },
      {
        "name": "the estimate is precise enough to act on",
        "passed": true,
        "allows": "act",
        "detail": "interval width is 0.26x the effect (limit 1.00x)"
      }
    ],
    "rationale": "Act: adopt the incident playbook. The estimate is ⟦claim:effect⟧ on minutes (⟦claim:ci.level⟧ CI ⟦claim:effect.ci.lo⟧ to ⟦claim:effect.ci.hi⟧, n = ⟦claim:n⟧, estimator aipw), read as a benefit of 7.819 to 10.2 in the requested direction (decrease).",
    "experiment": null,
    "what_would_change_it": [],
    "policy_digest": "sha256:e9a67b273e75d454e30b3a380415e765c2573cb1d07650d4359c3147c675e8eb",
    "request_digest": "sha256:2e83a809abefebb8ee74a0811603f6282664ecb3cf2b761f3e73212ee513e3b8",
    "decision_digest": "sha256:9a41857348f24b4dcfb6dd2199d98ed4d070c68373de551747655eead5ca919a"
  },
  "outcome": {
    "kind": "result",
    "status": "supported",
    "query": {
      "rung": "intervention",
      "treatment": "playbook",
      "outcome": "minutes",
      "estimand": "ate",
      "design": "observational",
      "treatment_levels": null,
      "adjust_for": null,
      "instruments": null,
      "treatment_proxies": [],
      "outcome_proxies": [],
      "negative_control_outcomes": [],
      "expected_treated_share": null,
      "segment_by": [],
      "cluster": null,
      "washout": null,
      "sequence": null,
      "unit": null,
      "time": null,
      "covariates": [],
      "control_group": "never_treated",
      "population": null,
      "horizon": null
    },
    "certificate": {
      "status": "identified",
      "strategy": "backdoor",
      "tier": "A",
      "estimand": "Σ_{load} E[minutes | playbook=t, load] P(load)",
      "adjustment_set": [
        "load"
      ],
      "instruments": [],
      "mediators": [],
      "treatment_proxies": [],
      "outcome_proxies": [],
      "conditions": [
        {
          "name": "adjustment set is observed",
          "holds": true,
          "detail": ""
        },
        {
          "name": "no adjustment variable is a descendant of a causal node",
          "holds": true,
          "detail": ""
        },
        {
          "name": "all non-causal paths are blocked",
          "holds": true,
          "detail": ""
        }
      ],
      "assumptions": [
        "causal sufficiency relative to the graph: every absent edge and absent <-> is a claim",
        "positivity: every adjustment stratum has a non-zero chance of each treatment level",
        "consistency and no interference between units (SUTVA)"
      ],
      "general_id": "Σ_{load} [P(load) P(minutes | load, playbook)]",
      "unreviewed_edges": [],
      "engine": "causal_core.identification/0.2",
      "graph_digest": "sha256:467bea1642f6a852f6485006c0547b15181774b32560fe842037a56e3c2547ea"
    },
    "primary": {
      "estimator": "aipw",
      "family": "doubly_robust",
      "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
      "point": -9.00921617223674,
      "se": 0.6071126065745684,
      "ci": {
        "level": 0.95,
        "lo": -10.19913501568313,
        "hi": -7.819297328790351,
        "method": "wald/influence-function (cross-fitted)"
      },
      "ci_null_reason": null,
      "n_used": 400,
      "assumptions": [
        "either the propensity model or the outcome model is correctly specified",
        "nuisance learners: linear"
      ],
      "details": {
        "share_clipped": 0.0,
        "folds": 5.0
      }
    },
    "estimates": [
      {
        "estimator": "aipw",
        "family": "doubly_robust",
        "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
        "point": -9.00921617223674,
        "se": 0.6071126065745684,
        "ci": {
          "level": 0.95,
          "lo": -10.19913501568313,
          "hi": -7.819297328790351,
          "method": "wald/influence-function (cross-fitted)"
        },
        "ci_null_reason": null,
        "n_used": 400,
        "assumptions": [
          "either the propensity model or the outcome model is correctly specified",
          "nuisance learners: linear"
        ],
        "details": {
          "share_clipped": 0.0,
          "folds": 5.0
        }
      },
      {
        "estimator": "regression_adjustment",
        "family": "outcome_model",
        "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
        "point": -9.205128616315733,
        "se": 0.6123970174145613,
        "ci": {
          "level": 0.95,
          "lo": -10.405404714688022,
          "hi": -8.004852517943444,
          "method": "wald/hc2+covariate-mean"
        },
        "ci_null_reason": null,
        "n_used": 400,
        "assumptions": [
          "outcome linear in the covariates within each arm"
        ],
        "details": {}
      },
      {
        "estimator": "ipw_hajek",
        "family": "weighting",
        "estimand": "ATE: E[Y(T=1) - Y(T=0)]",
        "point": -8.700702439723102,
        "se": 0.6209188247266281,
        "ci": {
          "level": 0.95,
          "lo": -9.917680973510231,
          "hi": -7.483723905935973,
          "method": "wald/m-estimation"
        },
        "ci_null_reason": null,
        "n_used": 400,
        "assumptions": [
          "propensity logistic-linear in the covariates"
        ],
        "details": {
          "share_clipped": 0.0
        }
      }
    ],
    "diagnostics": {
      "n_rows": 400,
      "n_used": 400,
      "n_dropped_missing": 0,
      "treatment_kind": "binary",
      "n_treated": 197,
      "n_control": 203,
      "outcome_sd": 8.851441844195666,
      "propensity": {
        "min": 0.014946262795080417,
        "max": 0.96814779698316,
        "share_outside_bounds": 0.0,
        "ess_treated": 145.28889466360104,
        "ess_control": 160.67028029361268
      },
      "balance": [
        {
          "covariate": "load",
          "smd_raw": 0.9783473312302402,
          "smd_weighted": 0.048753800155049744
        }
      ],
      "srm_p_value": null,
      "n_clusters": null,
      "icc": null,
      "first_stage_f": null,
      "checks": [
        {
          "name": "sample size",
          "passed": true,
          "detail": "n=400"
        },
        {
          "name": "units per arm",
          "passed": true,
          "detail": "treated=197, control=203"
        },
        {
          "name": "overlap (positivity)",
          "passed": true,
          "detail": "0.0% have propensity outside [0.01, 0.99]"
        }
      ]
    },
    "refutations": [
      {
        "name": "placebo_treatment",
        "passed": true,
        "statistic": -0.40899384932683347,
        "p_value": 0.5586057122145056,
        "threshold": 0.01,
        "detail": "effect of a randomly permuted treatment; should be indistinguishable from 0"
      }
    ],
    "sensitivity": {
      "e_value": 4.487195344812698,
      "e_value_ci": 3.8947750766545033,
      "e_value_method": "approximate: RR = exp(0.91 × effect / sd(outcome)) (VanderWeele & Ding 2017)",
      "partial_r2_treatment": 0.35898379323783075,
      "robustness_value": 0.5190062849594973,
      "robustness_value_alpha": 0.4719921436174327,
      "parallel_trends": null
    },
    "triangulation": {
      "estimators": [
        "aipw",
        "regression_adjustment",
        "ipw_hajek"
      ],
      "agree": true,
      "pairs": [
        {
          "a": "aipw",
          "b": "regression_adjustment",
          "difference": 0.1959124440789921,
          "se_difference": 0.17075014145498332,
          "z": 1.14736329006581
        },
        {
          "a": "aipw",
          "b": "ipw_hajek",
          "difference": -0.30851373251363867,
          "se_difference": 0.2972511428210291,
          "z": -1.0378891384091016
        }
      ],
      "detail": "estimators with different modelling assumptions agree within sampling noise"
    },
    "segments": [],
    "heterogeneity": null,
    "sequential": null,
    "event_study": [],
    "claims": [
      {
        "id": "effect",
        "value": -9.00921617223674,
        "text": "-9.009",
        "unit": null,
        "path": "/primary/point"
      },
      {
        "id": "effect.se",
        "value": 0.6071126065745684,
        "text": "0.6071",
        "unit": null,
        "path": "/primary/se"
      },
      {
        "id": "effect.ci.lo",
        "value": -10.19913501568313,
        "text": "-10.2",
        "unit": null,
        "path": "/primary/ci/lo"
      },
      {
        "id": "effect.ci.hi",
        "value": -7.819297328790351,
        "text": "-7.819",
        "unit": null,
        "path": "/primary/ci/hi"
      },
      {
        "id": "ci.level",
        "value": 95.0,
        "text": "95",
        "unit": "%",
        "path": "/primary/ci/level"
      },
      {
        "id": "n",
        "value": 400.0,
        "text": "400",
        "unit": null,
        "path": "/diagnostics/n_used"
      },
      {
        "id": "e_value",
        "value": 4.487195344812698,
        "text": "4.487",
        "unit": null,
        "path": "/sensitivity/e_value"
      },
      {
        "id": "e_value.ci",
        "value": 3.8947750766545033,
        "text": "3.895",
        "unit": null,
        "path": "/sensitivity/e_value_ci"
      },
      {
        "id": "robustness_value",
        "value": 0.5190062849594973,
        "text": "0.519",
        "unit": null,
        "path": "/sensitivity/robustness_value"
      }
    ],
    "warnings": [],
    "provenance": {
      "contract_version": "0.11.0",
      "core_version": "0.11.0",
      "graph_digest": "sha256:467bea1642f6a852f6485006c0547b15181774b32560fe842037a56e3c2547ea",
      "query_digest": "sha256:c09c955d6f701f61eb6b8886c6f480e790e9c7e39430074aa4787a1ee59b61eb",
      "data_digest": "sha256:482c2b5a557e03992b2dc996abae7237b669a938231a70da1701040ade3ce817",
      "policy_digest": "sha256:f4ca0544665bc58f09bbf690d2adaf1f38043920853d139291ee3cce0cccda8d",
      "seed": 0
    },
    "result_digest": "sha256:661c647910abae10a31e8ff9bed0c17c00adc533c53414401ecafc5628159ffb"
  },
  "record": {
    "sequence": 2,
    "recorded_at": "2026-09-26T12:00:37.854101+00:00",
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
        "benefit": 9.00921617223674,
        "benefit_ci": [
          7.819297328790351,
          10.19913501568313
        ],
        "ci_level": 0.95,
        "e_value_ci": 3.8947750766545033,
        "refuters_failed": [],
        "triangulation_agree": true,
        "n_used": 400,
        "outcome_sd": 8.851441844195666,
        "result_digest": "sha256:661c647910abae10a31e8ff9bed0c17c00adc533c53414401ecafc5628159ffb",
        "segments": [],
        "heterogeneity_differs": null
      },
      "checks": [
        {
          "name": "the whole interval clears the minimum worthwhile effect",
          "passed": true,
          "allows": "act",
          "detail": "interval [7.819, 10.2] against 2"
        },
        {
          "name": "the interval excludes material harm",
          "passed": true,
          "allows": "act",
          "detail": "lower bound 7.819 against a harm threshold of -0.5"
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
          "detail": "E-value for the interval bound 3.89 against a bar of 1.5"
        },
        {
          "name": "the estimate is precise enough to act on",
          "passed": true,
          "allows": "act",
          "detail": "interval width is 0.26x the effect (limit 1.00x)"
        }
      ],
      "rationale": "Act: adopt the incident playbook. The estimate is ⟦claim:effect⟧ on minutes (⟦claim:ci.level⟧ CI ⟦claim:effect.ci.lo⟧ to ⟦claim:effect.ci.hi⟧, n = ⟦claim:n⟧, estimator aipw), read as a benefit of 7.819 to 10.2 in the requested direction (decrease).",
      "experiment": null,
      "what_would_change_it": [],
      "policy_digest": "sha256:e9a67b273e75d454e30b3a380415e765c2573cb1d07650d4359c3147c675e8eb",
      "request_digest": "sha256:2e83a809abefebb8ee74a0811603f6282664ecb3cf2b761f3e73212ee513e3b8",
      "decision_digest": "sha256:9a41857348f24b4dcfb6dd2199d98ed4d070c68373de551747655eead5ca919a"
    },
    "evidence": {
      "outcome_kind": "result",
      "result_digest": "sha256:661c647910abae10a31e8ff9bed0c17c00adc533c53414401ecafc5628159ffb",
      "graph_digest": "sha256:467bea1642f6a852f6485006c0547b15181774b32560fe842037a56e3c2547ea",
      "data_digest": "sha256:482c2b5a557e03992b2dc996abae7237b669a938231a70da1701040ade3ce817",
      "query_digest": "sha256:c09c955d6f701f61eb6b8886c6f480e790e9c7e39430074aa4787a1ee59b61eb",
      "contract_version": "0.11.0",
      "core_version": "0.11.0",
      "model": {
        "name": "incident-playbook",
        "version": 1,
        "digest": "sha256:31d2b6b9d84a51ccaa5dcbd12dc656971a250c2aaf1cb0f453690e68bb6407f0"
      }
    },
    "approval": null,
    "executed": false,
    "prev_digest": "sha256:dc36022e516ba8d0859c98f48e3507e396d8d471c7ce7bac2998fb3c5d1fd65d",
    "entry_digest": "sha256:67abfb9164b8a3b9e19a86fac87b89af6e5bb0f0c50ce3628e1e92d6f6dd1a17"
  }
};

export const MODELS_LIST: { models: ModelSummaryResponse[] } = {
  "models": [
    {
      "name": "incident-playbook",
      "version": 1,
      "digest": "sha256:31d2b6b9d84a51ccaa5dcbd12dc656971a250c2aaf1cb0f453690e68bb6407f0",
      "author_id": "dev-user-001",
      "created_at": "2026-09-26T12:00:37.822222+00:00",
      "unreviewed_edges": []
    }
  ]
};
