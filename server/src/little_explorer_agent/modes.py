"""Which voice engine Pip runs on, switchable without rebuilding the app.

* ``pipeline``: streaming transcription -> topic gate + LangGraph reply -> OpenAI voice, with local
  VAD barge-in and a local end-of-turn model.
* ``realtime``: one OpenAI Realtime speech-to-speech model hears him and talks back.

The agent picks, in order: ``pip_mode`` in the dispatch metadata (the token server copies it from
the ``/token`` request, used by the e2e harness to compare modes), then ``PIP_MODE`` in the
environment, then ``pipeline``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Literal

PipMode = Literal["pipeline", "realtime"]
PIP_MODES: tuple[PipMode, ...] = ("pipeline", "realtime")
DEFAULT_MODE: PipMode = "pipeline"


def parse_mode(value: object) -> PipMode | None:
    if isinstance(value, str) and (mode := value.strip().lower()) in PIP_MODES:
        return mode  # type: ignore[return-value]
    return None


def mode_metadata(mode: PipMode) -> str:
    """Dispatch metadata that asks the agent for ``mode``."""
    return json.dumps({"pip_mode": mode})


def resolve_mode(job_metadata: str | None = None, env: Mapping[str, str] | None = None) -> PipMode:
    env = os.environ if env is None else env
    if job_metadata:
        try:
            data = json.loads(job_metadata)
        except ValueError:
            data = None
        if isinstance(data, dict) and (mode := parse_mode(data.get("pip_mode"))):
            return mode
    return parse_mode(env.get("PIP_MODE")) or DEFAULT_MODE
