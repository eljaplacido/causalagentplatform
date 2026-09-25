"""
Where the backend's secrets come from: an environment variable, or a file it names.

Every secret the backend reads goes through :func:`read_secret`. For a secret
``NAME`` it accepts exactly one of:

  * ``NAME_FILE`` -- a path; the value is the file's contents, with one trailing
    newline removed. This is how Docker and Kubernetes secrets, and the file-mount
    (CSI) drivers of the cloud secret managers, deliver a secret: the value never
    enters the process environment, so it is not in ``/proc/<pid>/environ``, in a
    crash dump of the environment, or in a child process that inherits it.
  * ``NAME`` -- the value itself, in the environment.

Setting both is an error, because one of them would be silently ignored and the
operator would not know which. A ``NAME_FILE`` that is missing, unreadable or
empty is an error too: a mount that failed is not the same as "no secret".
Every error names the variable and the path, and never the value.

There is no secret-manager client here. A secret manager is supported the way
the platform's deployment targets expose one -- by mounting the secret as a file
(or injecting it as a variable) -- and rotation is the manager's job plus, for
the Guardian's signing key, a list of previous keys that still verify
(``src/semantic_layer/verdict.py``).

The same module holds the one rule for "is this local development?": ``ENV``
unset, or ``local`` (case and surrounding whitespace ignored). It is the rule
that already chooses SQLite over PostgreSQL in ``src/db/factory.py``, which now
calls it, so there is one switch rather than two.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The value ``ENV`` takes (or defaults to) in local development.
LOCAL_ENV = "local"

#: Suffix of the variable that names a file holding a secret.
FILE_SUFFIX = "_FILE"


class SecretConfigError(RuntimeError):
    """A secret is configured in a way the process cannot use.

    The message names variables and paths only. It must never carry a value.
    """


def deployment_env() -> str:
    """``ENV``, normalised: lower case, whitespace stripped; ``local`` when unset."""
    return os.getenv("ENV", LOCAL_ENV).strip().lower()


def is_local_env() -> bool:
    """Whether this process is local development (``ENV`` unset or ``local``)."""
    return deployment_env() == LOCAL_ENV


def _strip_one_newline(text: str) -> str:
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def read_secret(name: str) -> str | None:
    """The value of secret ``name``, from ``<name>_FILE`` or ``<name>``; None if neither.

    An empty or whitespace-only ``<name>`` counts as unset. Raises
    :class:`SecretConfigError` when both forms are set, or when the file named by
    ``<name>_FILE`` is missing, unreadable or empty.
    """
    file_var = f"{name}{FILE_SUFFIX}"
    path_text = os.getenv(file_var, "").strip()
    inline = os.getenv(name, "").strip()

    if path_text and inline:
        raise SecretConfigError(
            f"Both {name} and {file_var} are set. Set exactly one: {file_var} to read "
            f"the secret from a file, or {name} to pass it in the environment."
        )
    if not path_text:
        return inline or None

    path = Path(path_text)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SecretConfigError(
            f"{file_var} names {path}, which does not exist."
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        # The exception type and errno text only: never the file's contents.
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else type(exc).__name__
        raise SecretConfigError(
            f"{file_var} names {path}, which cannot be read ({reason})."
        ) from None

    value = _strip_one_newline(raw)
    if not value.strip():
        raise SecretConfigError(f"{file_var} names {path}, which is empty.")
    return value


def read_secret_list(name: str) -> list[str]:
    """Secret ``name`` as a list: comma- or newline-separated, blanks dropped."""
    value = read_secret(name)
    if value is None:
        return []
    items = value.replace("\r\n", "\n").replace(",", "\n").split("\n")
    return [item.strip() for item in items if item.strip()]
