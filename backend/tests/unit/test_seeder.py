"""
Lightweight tests for the simulated data seeder (scripts/seed_data.py).
Tests the helper functions without actually writing to the database.
"""

from __future__ import annotations

import uuid

from scripts.seed_data import (
    _SEEDER_USER,
    _make_lineage,
    _now_iso,
    _verdict_id,
)


def test_seeder_user() -> None:
    """Seeder user has valid identity."""
    assert _SEEDER_USER.id == "seeder-001"
    assert "@causalplatform.local" in (_SEEDER_USER.email or "")


def test_verdict_id_is_uuid() -> None:
    """Verdict ID is a valid UUID."""
    vid = _verdict_id()
    assert isinstance(vid, uuid.UUID)


def test_now_iso_format() -> None:
    """Now ISO returns valid timestamp string."""
    ts = _now_iso()
    assert isinstance(ts, str)
    assert "T" in ts  # ISO 8601 format


def test_make_lineage_seeded() -> None:
    """Seeded lineage has correct step type and actor."""
    lineage = _make_lineage("SEEDED")
    assert len(lineage) == 1
    assert lineage[0].step_type == "SEEDED"
    assert lineage[0].actor_id == _SEEDER_USER.id


def test_make_lineage_action() -> None:
    """Lineage supports custom action types."""
    lineage = _make_lineage("CLEARED")
    assert lineage[0].step_type == "CLEARED"
