"""
Guardian verdicts that authorise exactly one write.

A verdict used to be nothing but a row id: `_assert_verdict_approved` looked the id
up in `audit_log` and checked the string said APPROVED. Three things follow from
that, and all three were real:

  * **Replay.** One APPROVED row authorised any number of unrelated writes.
  * **Forgery.** Anything that could insert into `audit_log` could mint one -- and
    two code paths do exactly that, including a human clicking "approve" on an
    intervention, under no role requirement at all.
  * **No binding.** Nothing tied the verdict to the object it was issued for.

This module fixes the first two halves of that: a verdict now carries an HMAC over
the claim it makes, keyed by a secret the Guardian holds, and the claim names the
subject. The third -- single use -- cannot live here, because `audit_log` is
append-only by construction (SELECT and INSERT are the only grants, and BEFORE
UPDATE / BEFORE DELETE triggers raise). Consumption is therefore recorded by
inserting into `verdict_consumption`, whose primary key *is* the no-replay rule:
the second attempt violates it.

The signature is over canonical JSON (RFC 8785, via causal-core), so the bytes
signed here are the bytes any other part of the platform would produce for the
same claim.
"""

from __future__ import annotations

import hmac
import os
import secrets
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import structlog
from causal_core.canonical import canonical_json

if TYPE_CHECKING:
    from uuid import UUID

log = structlog.get_logger(__name__)

#: Environment variable holding the issuer's key. Set it in any deployment that
#: runs more than one process, or verdicts issued by one worker will be refused
#: by another.
KEY_ENV = "GUARDIAN_SIGNING_KEY"

SIGNATURE_VERSION = 1

_EPHEMERAL_KEY: bytes | None = None


def signing_key() -> bytes:
    """The issuer's key.

    Without ``GUARDIAN_SIGNING_KEY`` a random key is generated for this process and
    the fact is logged at CRITICAL. That is deliberately inconvenient: verdicts then
    do not survive a restart and are not accepted by a sibling worker, which is
    noisy and safe, rather than quiet and forgeable. Falling back to a fixed default
    would mean shipping a key that everyone knows, which is the same as no key.
    """
    configured = os.getenv(KEY_ENV, "").strip()
    if configured:
        return configured.encode("utf-8")

    global _EPHEMERAL_KEY
    if _EPHEMERAL_KEY is None:
        _EPHEMERAL_KEY = secrets.token_bytes(32)
        log.critical(
            "guardian.signing_key.ephemeral",
            detail=(
                f"{KEY_ENV} is not set: verdicts are signed with a key generated for this "
                "process. They will be refused after a restart and by any other worker. "
                "Set it before running more than one process."
            ),
        )
    return _EPHEMERAL_KEY


def claim(
    *,
    verdict_id: UUID | str,
    subject_id: UUID | str | None,
    payload_hash: str,
    verdict: str,
    action_type: str,
    agent_id: str,
    created_at: str,
) -> dict[str, Any]:
    """The statement a signature covers.

    Every field is one a forger would have to get right *and* be able to sign. The
    subject is what binds the verdict to a single object; ``payload_hash`` binds it
    to what was actually evaluated.
    """
    return {
        "v": SIGNATURE_VERSION,
        "action_type": action_type,
        "agent_id": agent_id,
        "created_at": created_at,
        "payload_hash": payload_hash,
        "subject_id": str(subject_id) if subject_id is not None else None,
        "verdict": str(verdict),
        "verdict_id": str(verdict_id),
    }


def sign(statement: dict[str, Any], key: bytes | None = None) -> str:
    """HMAC-SHA256 over the canonical form of ``statement``."""
    material = canonical_json(statement).encode("utf-8")
    mac = hmac.new(key if key is not None else signing_key(), material, sha256)
    return f"v{SIGNATURE_VERSION}:{mac.hexdigest()}"


def verify(statement: dict[str, Any], signature: str | None, key: bytes | None = None) -> bool:
    """Whether ``signature`` is this issuer's signature over ``statement``.

    A missing signature is not valid. Rows written straight into `audit_log` by
    something other than the Guardian have none, which is the point: they can still
    be read as an audit trail, they just cannot authorise a write.
    """
    if not signature:
        return False
    return hmac.compare_digest(sign(statement, key), signature)
