"""Which voice engine Milo runs on, switchable without rebuilding the app.

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


def job_metadata(
    mode: PipMode | None = None, device_id: str | None = None, remaining_s: float | None = None
) -> str | None:
    """Dispatch metadata from the token server: the engine, the device, and how many seconds it
    may still talk today (the agent ends the session then)."""
    data: dict[str, object] = {}
    if mode:
        data["pip_mode"] = mode
    if device_id:
        data["device_id"] = device_id
    if remaining_s is not None:
        data["remaining_s"] = round(remaining_s, 1)
    return json.dumps(data) if data else None


def parse_job_metadata(raw: str | None) -> dict[str, object]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_mode(job_metadata: str | None = None, env: Mapping[str, str] | None = None) -> PipMode:
    env = os.environ if env is None else env
    if mode := parse_mode(parse_job_metadata(job_metadata).get("pip_mode")):
        return mode
    return parse_mode(env.get("PIP_MODE")) or DEFAULT_MODE
