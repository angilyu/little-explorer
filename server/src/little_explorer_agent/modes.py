"""Which voice engine Milo runs on, switchable without rebuilding the app.

* ``pipeline``: streaming transcription -> topic gate + LangGraph reply -> OpenAI voice, with local
  VAD barge-in and a local end-of-turn model.
* ``realtime``: one OpenAI Realtime speech-to-speech model hears him and talks back.

The agent picks, in order: ``pip_mode`` in the dispatch metadata (the token server copies it from
the ``/token`` request, used by the e2e harness to compare modes), then ``PIP_MODE`` in the
environment, then ``pipeline``.

The metadata also says whether the app plays Milo's intro itself (``"intro": "app"``, copied from
the ``/token`` request). Then the agent doesn't speak the greeting; older apps don't send it and
still hear the greeting from the agent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Literal

PipMode = Literal["pipeline", "realtime"]
PIP_MODES: tuple[PipMode, ...] = ("pipeline", "realtime")
DEFAULT_MODE: PipMode = "pipeline"
# The app played the pre-recorded intro the moment he tapped, so the agent stays quiet at the start.
INTRO_APP = "app"


def parse_mode(value: object) -> PipMode | None:
    if isinstance(value, str) and (mode := value.strip().lower()) in PIP_MODES:
        return mode  # type: ignore[return-value]
    return None


def mode_metadata(mode: PipMode) -> str:
    """Dispatch metadata that asks the agent for ``mode``."""
    return json.dumps({"pip_mode": mode})


def parse_intro(value: object) -> str | None:
    return INTRO_APP if isinstance(value, str) and value.strip().lower() == INTRO_APP else None


def parse_tz(value: object) -> str | None:
    """The device's IANA time zone (e.g. ``America/Los_Angeles``), or None if missing or unknown."""
    if not isinstance(value, str) or not value or len(value) > 64 or not value.isascii():
        return None
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(value)
    except (ValueError, KeyError, OSError):
        return None
    return value


def job_metadata(
    mode: PipMode | None = None,
    device_id: str | None = None,
    remaining_s: float | None = None,
    intro: str | None = None,
    tz: str | None = None,
) -> str | None:
    """Dispatch metadata from the token server: the engine, the device, how many seconds it may
    still talk today (the agent ends the session then), whether the app plays the intro and the
    device's time zone (stars count per page per local day)."""
    data: dict[str, object] = {}
    if mode:
        data["pip_mode"] = mode
    if device_id:
        data["device_id"] = device_id
    if remaining_s is not None:
        data["remaining_s"] = round(remaining_s, 1)
    if intro:
        data["intro"] = intro
    if tz:
        data["tz"] = tz
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


def app_plays_intro(meta: Mapping[str, object]) -> bool:
    return parse_intro(meta.get("intro")) == INTRO_APP
