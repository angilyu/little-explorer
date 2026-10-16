"""Where Pip's secrets come from.

The first that applies wins:

1. ``LITTLE_EXPLORER_ENV=/path/to/file`` (an explicit override);
2. ``server/.env`` in this checkout, if it exists;
3. ``~/.config/little-explorer/.env``, a per-user copy that survives deleting the checkout.

Variables already set in the environment are never overwritten.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_OVERRIDE = "LITTLE_EXPLORER_ENV"
SERVER_ENV = Path(__file__).resolve().parents[2] / ".env"
USER_ENV = Path("~/.config/little-explorer/.env")


def env_file(environ: dict[str, str] | None = None) -> Path | None:
    """The env file to load, or None if there is none."""
    environ = os.environ if environ is None else environ
    if override := environ.get(ENV_OVERRIDE, "").strip():
        return Path(override).expanduser()
    if SERVER_ENV.is_file():
        return SERVER_ENV
    user = USER_ENV.expanduser()
    return user if user.is_file() else None


def load_env() -> Path | None:
    """Load the env file (see module docs) and return its path."""
    path = env_file()
    if path is not None:
        if not path.is_file():
            raise FileNotFoundError(f"{ENV_OVERRIDE} points to {path}, which does not exist")
        load_dotenv(path, override=False)
    return path
