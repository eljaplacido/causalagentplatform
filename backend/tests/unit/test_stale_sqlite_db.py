"""A local SQLite file from before a migration fails with instructions, not a bare trace.

CREATE ... IF NOT EXISTS leaves an existing table as it is, so an old
local_dev.db stopped startup at the first index over a column it lacks, with
nothing saying the file was simply old (seen 2026-09-29: `no such column:
sensitivity_verdict_id`).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from src.db.factory import CloudSqlFactory

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_an_old_database_file_is_named_with_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    # An `assumptions` table as it was before the sensitivity_verdict_id column.
    con.execute("CREATE TABLE assumptions (id TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    monkeypatch.setenv("SQLITE_PATH", str(db))
    monkeypatch.setenv("ENV", "local")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    CloudSqlFactory.reset()
    try:
        with pytest.raises(RuntimeError, match="predates the current schema") as err:
            await CloudSqlFactory.init_db()
        assert "move or delete it" in str(err.value)
        assert str(db) in str(err.value)
    finally:
        CloudSqlFactory.reset()
