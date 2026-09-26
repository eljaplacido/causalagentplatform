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

And they can be scored: ``causal_decision.outcome`` records what would have to be
re-run to find out whether a verdict held, and compares the prediction with the
answer -- or records, with the kernel's own reason, that the later data can no
longer say. An action applied to everybody leaves nothing to compare against, and
that case is counted rather than quietly dropped. The report states each rate
with a Clopper-Pearson interval, and none at all from fewer than ten follow-ups.

The verdict is a pure function of (outcome, request, policy), so a recorded decision
can be replayed and disputed.
"""

from __future__ import annotations

from causal_decision._version import __version__
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
from causal_decision.outcome import (
    CalibrationReport,
    FollowUp,
    PairedComparison,
    RateBounds,
    RateEstimate,
    Realization,
    compare,
    plan,
    report,
)
from causal_decision.policy import decide
from causal_decision.render import explain

__all__ = [
    "Approval",
    "CalibrationReport",
    "ChainReport",
    "Check",
    "Decision",
    "DecisionPolicy",
    "DecisionRecord",
    "DecisionRequest",
    "Evidence",
    "EvidenceRef",
    "ExperimentSpec",
    "FollowUp",
    "PairedComparison",
    "RateBounds",
    "RateEstimate",
    "Realization",
    "RiskClass",
    "RiskRule",
    "Verdict",
    "__version__",
    "append",
    "compare",
    "decide",
    "explain",
    "plan",
    "report",
    "verify_chain",
]
