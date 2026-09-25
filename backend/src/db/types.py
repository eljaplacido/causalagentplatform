"""
Bind types that mean the same thing on SQLite and on PostgreSQL.

The platform writes through ``text()`` statements, and an untyped bound value
reaches the driver as whatever Python object the caller passed. SQLite accepts
almost anything into almost any column; asyncpg does not. It refuses a string
into ``TIMESTAMPTZ`` ("expected a datetime.date or datetime.datetime instance"),
a JSON string into ``TEXT[]`` or ``UUID[]`` ("a sized iterable container
expected"), and an integer into ``BOOLEAN``. Every one of those shapes was in
the code, and the suite never saw it because it only ran on SQLite -- the
Guardian could not write ``audit_log`` on the production schema.

Each type here binds a Python value as the column's type on each dialect:

======================  ===========================  ==========================
type                    PostgreSQL column (bound as)  SQLite column (bound as)
======================  ===========================  ==========================
:data:`TIMESTAMP`       ``TIMESTAMPTZ`` (datetime)   ``TEXT`` (ISO-8601 string)
:data:`TEXT_ARRAY`      ``TEXT[]`` (list of str)     ``TEXT`` (JSON array text)
:data:`UUID_ARRAY`      ``UUID[]`` (list of str)     ``TEXT`` (JSON array text)
:data:`BOOLEAN`         ``BOOLEAN`` (bool)           ``INTEGER`` (0 / 1)
======================  ===========================  ==========================

On SQLite the stored text is exactly what the code stored before, so nothing
already written there reads differently.

Use :func:`typed_text` to declare the types of a statement's parameters::

    typed_text("INSERT INTO t (at, tags) VALUES (:at, :tags)",
               at=TIMESTAMP, tags=TEXT_ARRAY)

JSONB and UUID columns need nothing: an untyped string parameter is inferred by
the server from the column, and asyncpg sends JSON text and UUID strings as is.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Integer, Text, bindparam, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import TypeDecorator, TypeEngine

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect
    from sqlalchemy.sql.elements import TextClause


def _is_postgres(dialect: Dialect) -> bool:
    return dialect.name == "postgresql"


class Timestamp(TypeDecorator[Any]):
    """A point in time: ``TIMESTAMPTZ`` on PostgreSQL, ISO-8601 text on SQLite.

    Accepts a ``datetime`` or an ISO-8601 string. A naive datetime is taken to be
    UTC, which is what every writer in this codebase means by one.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if _is_postgres(dialect):
            return dialect.type_descriptor(DateTime(timezone=True))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if _is_postgres(dialect):
            moment = value
            if isinstance(moment, str):
                moment = datetime.fromisoformat(moment)
            elif isinstance(moment, date) and not isinstance(moment, datetime):
                moment = datetime(moment.year, moment.month, moment.day)
            if not isinstance(moment, datetime):
                raise TypeError(f"Timestamp cannot bind {type(value).__name__}")
            return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
        return value.isoformat() if isinstance(value, date) else str(value)


class StringArray(TypeDecorator[Any]):
    """A list of strings: ``TEXT[]`` / ``UUID[]`` on PostgreSQL, a JSON array on SQLite.

    Accepts any non-string iterable (each item bound as ``str``). A string is
    refused rather than guessed at: a JSON array's text bound as the array is the
    exact bug this type exists to end, and iterating it would bind its characters.
    """

    impl = Text
    cache_ok = True

    def __init__(self, element: str = "text") -> None:
        super().__init__()
        if element not in ("text", "uuid"):
            raise ValueError(f"StringArray element must be 'text' or 'uuid', not {element!r}")
        self.element = element

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if _is_postgres(dialect):
            item: TypeEngine[Any] = (
                postgresql.UUID(as_uuid=False) if self.element == "uuid" else Text()
            )
            return dialect.type_descriptor(postgresql.ARRAY(item))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise TypeError(
                f"StringArray binds a list of strings, not {type(value).__name__}: "
                "pass the list itself, not its JSON text"
            )
        items = [str(v) for v in value]
        return items if _is_postgres(dialect) else json.dumps(items)


class Flag(TypeDecorator[Any]):
    """A boolean: ``BOOLEAN`` on PostgreSQL, ``INTEGER`` 0/1 on SQLite."""

    impl = Integer
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if _is_postgres(dialect):
            return dialect.type_descriptor(Boolean())
        return dialect.type_descriptor(Integer())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return bool(value) if _is_postgres(dialect) else int(bool(value))


TIMESTAMP = Timestamp()
TEXT_ARRAY = StringArray("text")
UUID_ARRAY = StringArray("uuid")
BOOLEAN = Flag()


def json_value(value: Any, default: Any = None) -> Any:
    """A stored JSON value, decoded: JSONB arrives decoded from PostgreSQL, SQLite's
    TEXT column as the JSON text that was written.
    """
    if value is None:
        return default
    if isinstance(value, (str, bytes)):
        return json.loads(value) if value else default
    return value


def timestamp_text(value: Any) -> str | None:
    """A stored timestamp as the ISO-8601 text the application uses.

    PostgreSQL returns ``TIMESTAMPTZ`` as an aware ``datetime`` and SQLite returns
    the text that was written; both are rendered the same way, in UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def typed_text(statement: str, **types: TypeEngine[Any]) -> TextClause:
    """``text(statement)`` with the named parameters bound as ``types``."""
    return text(statement).bindparams(
        *(bindparam(name, type_=type_) for name, type_ in types.items())
    )
