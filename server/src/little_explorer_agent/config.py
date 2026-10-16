"""Where Pip's secrets come from.

The first that applies wins:

1. ``LITTLE_EXPLORER_ENV=/path/to/file`` (an explicit override);
2. ``server/.env`` in this checkout, if it exists;
3. ``~/.config/little-explorer/.env``, a per-user copy that survives deleting the checkout.

Variables already set in the environment are never overwritten.

Admin-only keys that may sit in the same file (``OPENAI_ADMIN_KEY``, ``RENDER_API_KEY``) are never
loaded, and are removed from this process's environment, so the agent, its job processes and the
token server can't log or forward them. The file itself is left alone.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

ENV_OVERRIDE = "LITTLE_EXPLORER_ENV"
SERVER_ENV = Path(__file__).resolve().parents[2] / ".env"
USER_ENV = Path("~/.config/little-explorer/.env")
ADMIN_ONLY = frozenset({"OPENAI_ADMIN_KEY", "RENDER_API_KEY"})


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
        for key, value in dotenv_values(path).items():
            if key not in ADMIN_ONLY and value is not None:
                os.environ.setdefault(key, value)
    for key in ADMIN_ONLY:
        os.environ.pop(key, None)
    return path
