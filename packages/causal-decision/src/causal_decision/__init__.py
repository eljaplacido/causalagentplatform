"""
causal-decision: is an agent's proposed action causally justified enough to act on?

    >>> from causal_decision import decide, DecisionRequest
    >>> out = causal_core.analyze(df, graph, treatment="playbook", outcome="minutes_to_resolve")
    >>> d = decide(out, DecisionRequest(action="apply playbook X", outcome="minutes_to_resolve",
    ...                                 direction="decrease", minimum_worthwhile_effect=5.0))
    >>> d.verdict          # act | act_with_approval | experiment | refuse
    >>> d.rationale        # plain language, numbers bound to the analysis
    >>> d.experiment       # when the evidence cannot settle it: the trial that would

Decisions can be kept: ``causal_decision.ledger`` seals each one into a hash chain
with the digests of the evidence it rested on and the approval that let it happen,
so a past decision can be replayed and an edited one is detectable.

The verdict is a pure function of (outcome, request, policy), so a recorded decision
can be replayed and disputed.
"""

from __future__ import annotations

from causal_decision.contract import (
    Check,
    Decision,
    DecisionPolicy,
    DecisionRequest,
    Evidence,
    ExperimentSpec,
    RiskClass,
    RiskRule,
    Verdict,
)
from causal_decision.ledger import (
    Approval,
    ChainReport,
    DecisionRecord,
    EvidenceRef,
    append,
    verify_chain,
)
from causal_decision.policy import decide
from causal_decision.render import explain

__all__ = [
    "Approval",
    "ChainReport",
    "Check",
    "Decision",
    "DecisionPolicy",
    "DecisionRecord",
    "DecisionRequest",
    "Evidence",
    "EvidenceRef",
    "ExperimentSpec",
    "RiskClass",
    "RiskRule",
    "Verdict",
    "append",
    "decide",
    "explain",
    "verify_chain",
]
