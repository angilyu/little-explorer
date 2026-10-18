"""Little Explorer voice buddy (Milo) and token server."""

import os

AGENT_NAME = "little-explorer"


def agent_name() -> str:
    """The worker's dispatch name. ``LIVEKIT_AGENT_NAME`` overrides it for both the worker and the
    token server, so a local test worker (e.g. ``little-explorer-dev``) never gets real devices."""
    return (os.environ.get("LIVEKIT_AGENT_NAME") or "").strip() or AGENT_NAME
