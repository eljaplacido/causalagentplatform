"""
src/secret_source.py -- a secret comes from ``NAME_FILE`` or ``NAME``, never both.

Specification (from the module docstring and the register entry `secrets-in-env`):

Preconditions
  * ``read_secret(name)`` is called with the bare variable name.

Postconditions
  * ``NAME_FILE`` set: the value is the file's contents minus ONE trailing newline.
  * ``NAME`` set (non-blank): the value is ``NAME``.
  * neither: ``None``.
  * both, or a ``NAME_FILE`` that is missing / unreadable / empty:
    ``SecretConfigError`` whose message names the variable and the path and does
    not contain the value.
  * ``is_local_env()`` is true exactly when ``ENV`` is unset or ``local``
    (case and surrounding whitespace ignored) -- the rule that picks SQLite.

Undefined (not asserted): leading/trailing whitespace inside a file other than the
final newline; the exact wording of the messages beyond what they must name.

Values are assembled at runtime so the gate's secret scan, which reads this file,
finds nothing that looks like a credential.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from src.secret_source import (
    SecretConfigError,
    deployment_env,
    is_local_env,
    read_secret,
    read_secret_list,
)

if TYPE_CHECKING:
    from pathlib import Path

NAME = "CAUSAL_TEST_SECRET"


def _value(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NAME, raising=False)
    monkeypatch.delenv(f"{NAME}_FILE", raising=False)


def test_unset_is_none() -> None:
    assert read_secret(NAME) is None


def test_blank_env_value_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NAME, "   ")
    assert read_secret(NAME) is None


def test_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    value = _value("env")
    monkeypatch.setenv(NAME, value)
    assert read_secret(NAME) == value


def test_file_value_strips_exactly_one_newline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value = _value("file")
    secret_file = tmp_path / "secret"
    secret_file.write_text(value + "\n", encoding="utf-8")
    monkeypatch.setenv(f"{NAME}_FILE", str(secret_file))
    assert read_secret(NAME) == value

    # Only one: a second newline is part of the value as far as this is concerned.
    secret_file.write_text(value + "\n\n", encoding="utf-8")
    assert read_secret(NAME) == value + "\n"

    secret_file.write_text(value + "\r\n", encoding="utf-8")
    assert read_secret(NAME) == value


def test_both_set_is_an_error_and_names_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    value = _value("both")
    secret_file = tmp_path / "secret"
    secret_file.write_text(value, encoding="utf-8")
    monkeypatch.setenv(NAME, value)
    monkeypatch.setenv(f"{NAME}_FILE", str(secret_file))
    with pytest.raises(SecretConfigError) as err:
        read_secret(NAME)
    message = str(err.value)
    assert NAME in message
    assert f"{NAME}_FILE" in message
    assert value not in message


def test_missing_file_names_variable_and_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "not-mounted"
    monkeypatch.setenv(f"{NAME}_FILE", str(missing))
    with pytest.raises(SecretConfigError) as err:
        read_secret(NAME)
    assert f"{NAME}_FILE" in str(err.value)
    assert str(missing) in str(err.value)


def test_empty_file_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")
    monkeypatch.setenv(f"{NAME}_FILE", str(empty))
    with pytest.raises(SecretConfigError, match="empty"):
        read_secret(NAME)


def test_unreadable_file_does_not_print_its_contents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A directory cannot be read as a file: an OSError without depending on the
    # test runner's uid (chmod 000 does not stop root).
    value = _value("dir")
    directory = tmp_path / value
    directory.mkdir()
    monkeypatch.setenv(f"{NAME}_FILE", str(directory))
    with pytest.raises(SecretConfigError, match="cannot be read"):
        read_secret(NAME)

    # Undecodable bytes: the error still says nothing about what was in the file.
    binary = tmp_path / "binary"
    binary.write_bytes(b"\xff\xfe" + value.encode())
    monkeypatch.setenv(f"{NAME}_FILE", str(binary))
    with pytest.raises(SecretConfigError) as err:
        read_secret(NAME)
    assert value not in str(err.value).replace(str(tmp_path), "")


def test_list_splits_on_commas_and_newlines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b, c = _value("a"), _value("b"), _value("c")
    monkeypatch.setenv(NAME, f"{a}, {b}")
    assert read_secret_list(NAME) == [a, b]

    monkeypatch.delenv(NAME)
    listing = tmp_path / "list"
    listing.write_text(f"{a}\n{b}\n\n{c}\n", encoding="utf-8")
    monkeypatch.setenv(f"{NAME}_FILE", str(listing))
    assert read_secret_list(NAME) == [a, b, c]


def test_list_unset_is_empty() -> None:
    assert read_secret_list(NAME) == []


@pytest.mark.parametrize(
    ("env", "local"),
    [
        (None, True),
        ("local", True),
        ("  LOCAL ", True),
        ("staging", False),
        ("cloudrun", False),
        ("production", False),
        ("", False),  # set but empty is not "unset": factory.py picks PostgreSQL too
    ],
)
def test_local_rule(monkeypatch: pytest.MonkeyPatch, env: str | None, local: bool) -> None:
    if env is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", env)
    assert is_local_env() is local


def test_db_factory_uses_the_same_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.db.factory import _get_env

    for env in ("local", " Local", "staging", "CLOUDRUN"):
        monkeypatch.setenv("ENV", env)
        assert _get_env() == deployment_env()


def test_database_url_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.db.factory import _postgres_url

    url_file = tmp_path / "database_url"
    url_file.write_text("postgresql://svc@db.internal:5432/causal\n", encoding="utf-8")
    monkeypatch.delenv("CLOUD_SQL_INSTANCE_CONNECTION_NAME", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL_FILE", str(url_file))
    assert _postgres_url() == "postgresql+asyncpg://svc@db.internal:5432/causal"


def test_llm_provider_detected_from_key_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.agents.llm_factory import _detect_provider

    for var in ("OLLAMA_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(f"{var}_FILE", raising=False)
    key_file = tmp_path / "openai"
    key_file.write_text(_value("openai") + "\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY_FILE", str(key_file))
    assert _detect_provider() == "openai"
