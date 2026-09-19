"""
causal-core: a causal inference kernel that identifies before it estimates,
abstains when it cannot, and proves every number it returns.

    >>> import causal_core as cc
    >>> out = cc.analyze(df, "Z -> X -> Y; Z -> Y", treatment="X", outcome="Y")
    >>> out.kind                 # "result" | "refusal" | "apparatus_failure"
    >>> print(cc.report(out))    # a plain-language summary built from claims

See :mod:`causal_core.contract` for the typed outcome objects.
"""

from __future__ import annotations

from causal_core.claims import render, verify
from causal_core.contract import (
    CONTRACT_VERSION,
    Abstention,
    AbstentionReason,
    AnalysisPolicy,
    ApparatusFailure,
    CausalGraph,
    CausalQuery,
    CausalResult,
    Design,
    Edge,
    EdgeOrigin,
    EdgeStatus,
    IdentificationCertificate,
    ResultStatus,
    Rung,
    Strategy,
    Tier,
    json_schemas,
)
from causal_core.graph import Graph, parse_graph
from causal_core.identification import identify
from causal_core.pipeline import CORE_VERSION, analyze, as_frame
from causal_core.report import report
from causal_core.scm import LinearSCM

__version__ = CORE_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "Abstention",
    "AbstentionReason",
    "AnalysisPolicy",
    "ApparatusFailure",
    "CausalGraph",
    "CausalQuery",
    "CausalResult",
    "Design",
    "Edge",
    "EdgeOrigin",
    "EdgeStatus",
    "Graph",
    "IdentificationCertificate",
    "LinearSCM",
    "ResultStatus",
    "Rung",
    "Strategy",
    "Tier",
    "analyze",
    "as_frame",
    "identify",
    "json_schemas",
    "parse_graph",
    "render",
    "report",
    "verify",
]
