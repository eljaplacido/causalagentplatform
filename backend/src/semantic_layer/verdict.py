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
import secrets
from hashlib import sha256
from typing import TYPE_CHECKING, Any

import structlog
from causal_core.canonical import canonical_json

from src.secret_source import SecretConfigError, is_local_env, read_secret, read_secret_list

if TYPE_CHECKING:
    from uuid import UUID

log = structlog.get_logger(__name__)

#: Secret holding the issuer's key: the variable itself, or ``GUARDIAN_SIGNING_KEY_FILE``
#: naming a file that holds it (``src/secret_source.py``). Set it in any deployment that
#: runs more than one process, or verdicts issued by one worker will be refused by
#: another. Outside local development it is required.
KEY_ENV = "GUARDIAN_SIGNING_KEY"

#: Keys that no longer sign but still verify, so that rotating the key does not void
#: verdicts issued and not yet spent. Comma- or newline-separated; ``_FILE`` form too.
PREVIOUS_KEYS_ENV = "GUARDIAN_SIGNING_KEYS_PREVIOUS"

#: Shortest key accepted outside local development: HMAC-SHA256's block of security.
MIN_KEY_BYTES = 32

SIGNATURE_VERSION = 1

_EPHEMERAL_KEY: bytes | None = None


class SigningKeyError(SecretConfigError):
    """The Guardian has no usable signing key in an environment that requires one."""


def _checked(key: str, variable: str) -> bytes:
    material = key.encode("utf-8")
    if len(material) < MIN_KEY_BYTES:
        detail = (
            f"{variable} is {len(material)} bytes; at least {MIN_KEY_BYTES} are required "
            "(for example the output of `python -c \"import secrets; "
            "print(secrets.token_hex(32))\"`)."
        )
        if not is_local_env():
            raise SigningKeyError(detail)
        log.warning("guardian.signing_key.short", detail=detail)
    return material


def signing_key() -> bytes:
    """The issuer's current key: the one every new verdict is signed with.

    Outside local development (``ENV`` set to anything but ``local``) a missing key,
    or one shorter than :data:`MIN_KEY_BYTES`, raises :class:`SigningKeyError`; the
    application calls :func:`require_signing_key` at start-up so that it refuses to
    start rather than failing on the first verdict.

    In local development a random key is generated for this process instead and the
    fact is logged at CRITICAL. That is deliberately inconvenient: verdicts then do
    not survive a restart and are not accepted by a sibling worker, which is noisy
    and safe, rather than quiet and forgeable. Falling back to a fixed default would
    mean shipping a key that everyone knows, which is the same as no key.
    """
    configured = read_secret(KEY_ENV)
    if configured:
        return _checked(configured, KEY_ENV)

    if not is_local_env():
        raise SigningKeyError(
            f"{KEY_ENV} (or {KEY_ENV}_FILE) is not set, and ENV is not 'local'. "
            "Outside local development verdicts must be signed with a configured key: "
            f"set {KEY_ENV}_FILE to a mounted secret file, or {KEY_ENV}."
        )

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


def previous_keys() -> list[bytes]:
    """Keys retired by a rotation that still verify (never sign)."""
    return [_checked(k, PREVIOUS_KEYS_ENV) for k in read_secret_list(PREVIOUS_KEYS_ENV)]


def verification_keys() -> list[bytes]:
    """Every key a signature may have been made with: the current one first."""
    return [signing_key(), *previous_keys()]


def require_signing_key() -> None:
    """Fail now, not at the first verdict, if the keys are not usable here.

    Called from the application's start-up. Raises :class:`SigningKeyError` (or
    :class:`~src.secret_source.SecretConfigError` for a malformed ``_FILE``) outside
    local development without a key of at least :data:`MIN_KEY_BYTES`. Logs how
    many previous keys verify -- a count, never a key.
    """
    keys = verification_keys()
    log.info("guardian.signing_key.ready", previous_keys=len(keys) - 1)


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

    With ``key``, only that key is tried. Without it, the current key and every
    previous key (:data:`PREVIOUS_KEYS_ENV`) are, so a rotation does not void a
    verdict issued before it; drop a key from the previous list to void them.

    A missing signature is not valid. Rows written straight into `audit_log` by
    something other than the Guardian have none, which is the point: they can still
    be read as an audit trail, they just cannot authorise a write.
    """
    if not signature:
        return False
    keys = [key] if key is not None else verification_keys()
    # Every key is compared, match or not, so the time taken does not say which matched.
    matched = False
    for candidate in keys:
        matched |= hmac.compare_digest(sign(statement, candidate), signature)
    return matched
