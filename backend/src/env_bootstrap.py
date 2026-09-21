"""
Load `.env` before anything reads the environment.

This ran as a loop in the middle of `src/api/main.py`'s imports, which is why every
import below it was flagged E402 -- nineteen findings describing one deliberate
decision. The decision is real: `DEV_AUTH_BYPASS` and `ENV` have to be set before
the auth middleware is imported, so the loading cannot simply move down.

Importing this module first does the same thing without putting statements between
imports. Import order is execution order, so `import src.env_bootstrap` above the
rest is the ordering constraint, stated as an import rather than as a comment.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | None = None) -> int:
    """Set variables from `.env` that are not already in the environment.

    Deliberately minimal -- no extra dependency, no interpolation, no export
    syntax. Returns how many variables it set, so a caller can log it rather than
    guess whether the file was found.
    """
    env_path = path or Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return 0
    set_count = 0
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value
            set_count += 1
    return set_count


#: Run on import. The environment has to be in place before the modules that read
#: it are imported, and an import is the only thing that reliably happens first.
LOADED = load_dotenv()
