"""
The bind types in src/db/types.py hand each driver what its column accepts.

Preconditions: none -- these bind against dialect objects, with no database.

Postconditions (from the module's table and each type's docstring):
  - TIMESTAMP binds an aware datetime on PostgreSQL (from a datetime, an ISO
    string, or a naive datetime taken as UTC) and ISO-8601 text on SQLite;
  - TEXT_ARRAY / UUID_ARRAY bind a list of str on PostgreSQL and its JSON text on
    SQLite, and refuse a string -- a JSON array's text is the bug they end;
  - BOOLEAN binds a bool on PostgreSQL and 0/1 on SQLite;
  - timestamp_text renders a TIMESTAMPTZ and the ISO text SQLite stored the same
    way, so a signature over that text verifies on either store;
  - json_value decodes JSON text and passes decoded JSONB through.

Undefined, and not asserted: how any of them treat types no caller passes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from src.db.types import (
    BOOLEAN,
    TEXT_ARRAY,
    TIMESTAMP,
    UUID_ARRAY,
    StringArray,
    json_value,
    timestamp_text,
)

PG = postgresql.asyncpg.dialect()
LITE = sqlite.aiosqlite.dialect()

MOMENT = datetime(2026, 9, 25, 10, 30, 0, 123456, tzinfo=UTC)


def test_timestamp_binds_a_datetime_on_postgres_and_iso_text_on_sqlite() -> None:
    assert TIMESTAMP.process_bind_param(MOMENT, PG) == MOMENT
    assert TIMESTAMP.process_bind_param(MOMENT.isoformat(), PG) == MOMENT
    naive = TIMESTAMP.process_bind_param(datetime(2026, 1, 1), PG)
    assert naive == datetime(2026, 1, 1, tzinfo=UTC)
    assert TIMESTAMP.process_bind_param(MOMENT, LITE) == "2026-09-25T10:30:00.123456+00:00"
    assert TIMESTAMP.process_bind_param("2026-01-01T00:00:00", LITE) == "2026-01-01T00:00:00"
    assert TIMESTAMP.process_bind_param(None, PG) is None


@pytest.mark.parametrize("array", [TEXT_ARRAY, UUID_ARRAY])
def test_arrays_bind_lists_and_refuse_their_json_text(array: StringArray) -> None:
    values = ["a1b2c3d4-0000-0000-0000-000000000001"]
    assert array.process_bind_param(values, PG) == values
    assert array.process_bind_param(tuple(values), LITE) == (
        '["a1b2c3d4-0000-0000-0000-000000000001"]'
    )
    for dialect in (PG, LITE):
        with pytest.raises(TypeError, match="not its JSON text"):
            array.process_bind_param('["PR-001"]', dialect)


def test_boolean_is_a_bool_on_postgres_and_an_integer_on_sqlite() -> None:
    assert BOOLEAN.process_bind_param(True, PG) is True
    assert BOOLEAN.process_bind_param(0, PG) is False
    assert BOOLEAN.process_bind_param(True, LITE) == 1
    assert BOOLEAN.process_bind_param(None, LITE) is None


def test_timestamp_text_renders_both_stores_alike() -> None:
    signed = MOMENT.isoformat()
    assert timestamp_text(MOMENT) == signed  # what asyncpg returns for TIMESTAMPTZ
    assert timestamp_text(signed) == signed  # what SQLite returns: the text written
    plus_two = MOMENT.astimezone(timezone(timedelta(hours=2)))
    assert timestamp_text(plus_two) == signed, "rendered in UTC whatever the zone"
    assert timestamp_text(None) is None


def test_json_value_decodes_text_and_passes_jsonb_through() -> None:
    assert json_value('["a", 1]') == ["a", 1]
    assert json_value(["a", 1]) == ["a", 1]
    assert json_value({"k": 1}) == {"k": 1}
    assert json_value(None, []) == []
    assert json_value("", []) == []
