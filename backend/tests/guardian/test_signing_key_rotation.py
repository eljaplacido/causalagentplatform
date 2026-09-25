"""
The Guardian's signing key: required outside local development, and rotatable.

This is the test the register's `secrets-in-env` entry asks for ("a test pins that
the process refuses to start in a non-local ENV without it"), and the rotation
tests that make a key change survivable.

Specification (src/semantic_layer/verdict.py, src/secret_source.py):

Preconditions
  * ``ENV`` unset or ``local`` is local development; anything else is not.

Postconditions
  * Non-local ENV, no ``GUARDIAN_SIGNING_KEY`` / ``_FILE``, or a key shorter than
    32 bytes: application start-up (the FastAPI lifespan uvicorn runs) raises
    ``SigningKeyError`` before serving anything.
  * Local ENV without a key: start-up proceeds on a per-process key.
  * New verdicts are signed with the current key only.
  * A verdict signed with a key listed in ``GUARDIAN_SIGNING_KEYS_PREVIOUS``
    verifies after rotation; once that key is dropped from the list, it does not.
  * A signature made with any other key never verifies.
  * The signature format is unchanged: ``v1:`` + hex HMAC-SHA256 over the claim's
    canonical JSON, keyed by the key's UTF-8 bytes -- so a verdict signed before
    this change still verifies with the same key.

Undefined (not asserted): which key matched; the log lines' wording.

Keys are derived at runtime, so the gate's secret scan finds nothing here.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from causal_core.canonical import canonical_json

from src.exceptions import GuardianBlockedError
from src.semantic_layer import verdict as verdict_module
from src.semantic_layer.verdict import (
    KEY_ENV,
    PREVIOUS_KEYS_ENV,
    SigningKeyError,
    claim,
    require_signing_key,
    sign,
    signing_key,
    verify,
)
from tests.conftest import VerdictFactory, make_causal_entity

if TYPE_CHECKING:
    from pathlib import Path

    from src.semantic_layer.registry import SemanticLayerRegistry

pytestmark = [pytest.mark.guardian]


def _key(seed: str) -> str:
    """A 64-character key, the shape `secrets.token_hex(32)` produces."""
    return hashlib.sha256(f"rotation-test-{seed}".encode()).hexdigest()


OLD, NEW, OTHER = _key("old"), _key("new"), _key("attacker")


@pytest.fixture(autouse=True)
def _no_configured_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (KEY_ENV, PREVIOUS_KEYS_ENV):
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(f"{var}_FILE", raising=False)
    monkeypatch.setenv("ENV", "local")


def _statement() -> dict[str, Any]:
    return claim(
        verdict_id=uuid4(),
        subject_id=uuid4(),
        payload_hash="ab" * 32,
        verdict="APPROVED",
        action_type="PROPOSE_DRAFT",
        agent_id="governance-agent",
        created_at="2026-09-25T00:00:00+00:00",
    )


# ── Start-up refusal outside local development ───────────────────────────────


class _Dialect:
    # Neither SQLite nor PostgreSQL: the start-up's RLS check (src/db/scope.py)
    # has nothing to inspect on a stub.
    name = "stub"


class _Engine:
    dialect = _Dialect()

    async def dispose(self) -> None:
        return None


async def _start_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the application's start-up exactly as uvicorn does: enter the lifespan.

    Only the engine disposed at shutdown is stubbed: outside ENV=local it would be
    a PostgreSQL engine, and nothing here is about the database.
    """
    from src.api import main
    from src.db.factory import CloudSqlFactory

    monkeypatch.setattr(CloudSqlFactory, "get_engine", classmethod(lambda cls: _Engine()))
    async with main.lifespan(main.app):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("env", ["staging", "production", "cloudrun"])
async def test_non_local_start_up_refuses_without_a_key(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("ENV", env)
    with pytest.raises(SigningKeyError, match=KEY_ENV):
        await _start_app(monkeypatch)


@pytest.mark.asyncio
async def test_non_local_start_up_refuses_a_short_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv(KEY_ENV, NEW[:31])
    with pytest.raises(SigningKeyError, match="at least 32"):
        await _start_app(monkeypatch)


@pytest.mark.asyncio
async def test_non_local_start_up_refuses_a_short_previous_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv(KEY_ENV, NEW)
    monkeypatch.setenv(PREVIOUS_KEYS_ENV, OLD[:16])
    with pytest.raises(SigningKeyError, match=PREVIOUS_KEYS_ENV):
        await _start_app(monkeypatch)


@pytest.mark.asyncio
async def test_non_local_start_up_proceeds_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv(KEY_ENV, NEW)
    await _start_app(monkeypatch)


@pytest.mark.asyncio
async def test_non_local_start_up_proceeds_with_a_key_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key_file = tmp_path / "guardian_signing_key"
    key_file.write_text(NEW + "\n", encoding="utf-8")
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv(f"{KEY_ENV}_FILE", str(key_file))
    await _start_app(monkeypatch)
    assert signing_key() == NEW.encode()


def test_local_without_a_key_uses_an_ephemeral_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verdict_module, "_EPHEMERAL_KEY", None)
    require_signing_key()  # does not raise
    key = signing_key()
    assert len(key) == 32
    assert signing_key() == key  # stable within the process


# ── Format compatibility ─────────────────────────────────────────────────────


def test_signature_format_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Known answer, computed from the definition rather than from `sign`."""
    monkeypatch.setenv(KEY_ENV, OLD)
    statement = _statement()
    expected = "v1:" + hmac.new(
        OLD.encode("utf-8"), canonical_json(statement).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert sign(statement) == expected
    assert verify(statement, expected)


# ── Rotation ─────────────────────────────────────────────────────────────────


def test_new_verdicts_are_signed_with_the_current_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, NEW)
    monkeypatch.setenv(PREVIOUS_KEYS_ENV, OLD)
    statement = _statement()
    assert sign(statement) == sign(statement, key=NEW.encode())
    assert sign(statement) != sign(statement, key=OLD.encode())


def test_old_signature_verifies_only_while_its_key_is_listed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    statement = _statement()
    monkeypatch.setenv(KEY_ENV, OLD)
    issued_before_rotation = sign(statement)

    monkeypatch.setenv(KEY_ENV, NEW)
    assert not verify(statement, issued_before_rotation)

    monkeypatch.setenv(PREVIOUS_KEYS_ENV, f"{_key('older')},{OLD}")
    assert verify(statement, issued_before_rotation)

    # The file form, newline-separated, as a mounted secret would hold it.
    monkeypatch.delenv(PREVIOUS_KEYS_ENV)
    listing = tmp_path / "previous"
    listing.write_text(f"{OLD}\n", encoding="utf-8")
    monkeypatch.setenv(f"{PREVIOUS_KEYS_ENV}_FILE", str(listing))
    assert verify(statement, issued_before_rotation)


def test_a_forged_signature_fails_whatever_keys_are_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement = _statement()
    forged = sign(statement, key=OTHER.encode())
    monkeypatch.setenv(KEY_ENV, NEW)
    monkeypatch.setenv(PREVIOUS_KEYS_ENV, OLD)
    assert not verify(statement, forged)
    # ...and a real signature over a different claim does not transfer.
    assert not verify(_statement(), sign(statement))


@pytest.mark.asyncio
async def test_rotation_end_to_end_through_the_registry(
    monkeypatch: pytest.MonkeyPatch,
    db_engine: object,
    registry: SemanticLayerRegistry,
    verdict_for: VerdictFactory,
) -> None:
    """Issued before a rotation, spent after it: accepted iff the old key is listed."""
    monkeypatch.setenv(KEY_ENV, OLD)
    kept, dropped = make_causal_entity(), make_causal_entity()
    kept_verdict = await verdict_for(kept.id)
    dropped_verdict = await verdict_for(dropped.id)

    monkeypatch.setenv(KEY_ENV, NEW)
    monkeypatch.setenv(PREVIOUS_KEYS_ENV, OLD)
    assert await registry.propose_draft(kept, kept_verdict) == kept.id

    monkeypatch.delenv(PREVIOUS_KEYS_ENV)
    with pytest.raises(GuardianBlockedError, match="not signed by the issuer"):
        await registry.propose_draft(dropped, dropped_verdict)
