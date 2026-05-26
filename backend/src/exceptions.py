"""
Platform-wide custom exception types.

All platform-specific errors inherit from CausalPlatformError so callers can
catch the entire hierarchy with a single except clause when needed.
"""

from __future__ import annotations


class CausalPlatformError(Exception):
    """Base class for all Unified Enterprise Causal Platform errors."""


# ── Guardian / Governance ─────────────────────────────────────────────────────


class GuardianBlockedError(CausalPlatformError):
    """
    Raised when an operation is blocked because the associated Guardian verdict
    is not APPROVED.

    Every semantic-layer write and every intervention must carry a valid
    Guardian verdict reference.  This error is raised when:
      - The verdict is REJECTED
      - The verdict is REQUIRES_ESCALATION
      - The verdict_id does not exist in the audit_log table
      - No verdict was supplied at all

    Never catch and suppress this error without routing to human escalation.
    """

    def __init__(
        self,
        reason: str,
        verdict_status: str | None = None,
        verdict_id: str | None = None,
    ) -> None:
        self.reason = reason
        self.verdict_status = verdict_status
        self.verdict_id = verdict_id
        super().__init__(
            f"Guardian blocked operation: {reason}"
            + (f" [verdict={verdict_status}]" if verdict_status else "")
            + (f" [id={verdict_id}]" if verdict_id else "")
        )


# ── Circuit Breaker ───────────────────────────────────────────────────────────


class CircuitBreakerError(CausalPlatformError):
    """
    Raised when an agent's cycleCount reaches the maximum allowed threshold
    (5 cycles, per cynepic-guardian specification).

    When this error is raised the current EpistemicState must be escalated to
    a human reviewer.  No further automated action may be taken.
    """

    def __init__(self, cycle_count: int, session_id: str | None = None) -> None:
        self.cycle_count = cycle_count
        self.session_id = session_id
        super().__init__(
            f"Circuit breaker opened after {cycle_count} cycles"
            + (f" in session {session_id}" if session_id else "")
        )


# ── Semantic Layer ────────────────────────────────────────────────────────────


class SemanticLayerError(CausalPlatformError):
    """Base for semantic layer errors."""


class ObjectNotFoundError(SemanticLayerError):
    """Requested semantic object does not exist."""

    def __init__(self, object_id: str, object_type: str = "object") -> None:
        self.object_id = object_id
        self.object_type = object_type
        super().__init__(f"{object_type} '{object_id}' not found in semantic layer")


class VersionConflictError(SemanticLayerError):
    """Optimistic concurrency conflict on semantic object version."""


# ── Auth ──────────────────────────────────────────────────────────────────────


class AuthenticationError(CausalPlatformError):
    """Token is missing, invalid, or expired."""


class AuthorizationError(CausalPlatformError):
    """Caller lacks the required role or permission for this operation."""

    def __init__(self, required_role: str, caller_roles: list[str] | None = None) -> None:
        self.required_role = required_role
        self.caller_roles = caller_roles or []
        super().__init__(
            f"Requires role '{required_role}'; caller has: {self.caller_roles or ['none']}"
        )


# ── Audit ─────────────────────────────────────────────────────────────────────


class AuditWriteError(CausalPlatformError):
    """Failed to write to the append-only audit log."""
