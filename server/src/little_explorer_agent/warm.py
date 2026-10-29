"""Waking Milo's agent before the child taps "Talk to Milo".

On LiveKit Cloud's free Build plan the deployed agent scales to zero once its sessions end, and the
next session waits 10 to 20 s while a replica cold-starts. When a book opens on a device that may use
Milo, the app calls ``POST /milo/warm`` and the token server dispatches the agent into a throwaway
room with ``{"warm": true}`` metadata. LiveKit starts a replica for it; the agent sees the warm-up
and shuts the job down without joining the room, so no OpenAI connection is made, no minutes are
counted or reported and LiveKit meters no agent session time ("If an agent receives a job but never
connects to the room, no metering occurs", https://docs.livekit.io/deploy/admin/billing/).

Everything here is in memory: a warm-up never writes to the database. A device warms at most once
per ``PIP_WARM_DEVICE_GAP_S`` (600 s), and nobody warms while the agent was busy (a warm-up, a token
or a finished session) in the last ``PIP_WARM_FRESH_S`` (300 s): one replica serves every device, so
it is probably still up. ``PIP_WARM=0`` turns warm-ups off.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

log = logging.getLogger("little_explorer.warm")

WARM_KEY = "warm"
WARM_ROOM_PREFIX = "milo-warm-"
# Long enough for a cold replica to pick the job up; the room is empty and closes by itself.
WARM_ROOM_EMPTY_TIMEOUT_S = 60


def warm_metadata() -> str:
    return json.dumps({WARM_KEY: True})


def is_warm(meta: Mapping[str, object]) -> bool:
    return meta.get(WARM_KEY) is True


@dataclass(frozen=True)
class WarmLimits:
    enabled: bool = True
    device_gap_s: float = 600.0
    fresh_s: float = 300.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WarmLimits:
        env = os.environ if env is None else env

        def num(name: str, default: float) -> float:
            raw = (env.get(name) or "").strip()
            return float(raw) if raw else default

        return cls(
            enabled=(env.get("PIP_WARM") or "1").strip() != "0",
            device_gap_s=num("PIP_WARM_DEVICE_GAP_S", cls.device_gap_s),
            fresh_s=num("PIP_WARM_FRESH_S", cls.fresh_s),
        )


Dispatch = Callable[[str, str], Awaitable[None]]


async def dispatch_agent(room: str, metadata: str) -> None:
    """Create an empty room and dispatch the agent into it (LiveKit server API)."""
    from aiohttp import ClientTimeout
    from livekit import api

    from . import agent_name

    async with api.LiveKitAPI(timeout=ClientTimeout(total=10)) as lk:
        await lk.room.create_room(api.CreateRoomRequest(name=room, empty_timeout=WARM_ROOM_EMPTY_TIMEOUT_S))
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(agent_name=agent_name(), room=room, metadata=metadata)
        )


class Warmer:
    """The token server's in-memory warm-up limits and the dispatch."""

    def __init__(
        self,
        limits: WarmLimits | None = None,
        *,
        dispatch: Dispatch | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or WarmLimits.from_env()
        self._dispatch = dispatch or dispatch_agent
        self._clock = clock
        self._devices: dict[str, float] = {}
        self._busy_at: float | None = None

    def agent_busy(self) -> None:
        """A token was handed out or a session ended: the agent is (or was just) running."""
        self._busy_at = self._clock()

    def skip_reason(self, device_id: str) -> str | None:
        if not self.limits.enabled:
            return "off"
        now = self._clock()
        last = self._devices.get(device_id)
        if last is not None and now - last < self.limits.device_gap_s:
            return "too_soon"
        if self._busy_at is not None and now - self._busy_at < self.limits.fresh_s:
            return "fresh"
        return None

    def claim(self, device_id: str) -> None:
        """Count a warm-up attempt for the device (before the account checks, so they run at most
        once per gap too)."""
        now = self._clock()
        gap = self.limits.device_gap_s
        self._devices = {d: t for d, t in self._devices.items() if now - t < gap}
        self._devices[device_id] = now

    async def warm(self) -> str:
        room = f"{WARM_ROOM_PREFIX}{uuid.uuid4().hex[:10]}"
        self._busy_at = self._clock()
        await self._dispatch(room, warm_metadata())
        return room
