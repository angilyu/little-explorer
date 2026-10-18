"""Per-turn timing logs for a voice session: how long Milo took to answer and what cut him off.

Only timings, counts and causes are logged, never what anyone said. One ``turn`` line per reply:

* ``wait``: the child stopped talking (as the turn detector saw it) -> Milo's turn began
  (``thinking``); with client-side turns this is the end-of-turn wait.
* ``answer``: ``thinking`` -> Milo's first audio.
* ``total``: the child stopped -> Milo's first audio.

An ``interrupted`` line says what stopped a reply (``barge_in`` = the soft barge-in heard him talk
over Milo, ``guard`` = the reply guard cut a rejected turn, ``audio_activity`` / ``user_turn`` =
LiveKit's own barge-in, ``programmatic`` = anything else, e.g. the session closing) and how long
he had been talking over Milo. The VAD only reports speech once it has heard ``lead_s`` of it, so
that much is counted from before the "speaking" event; a ``talked over Milo`` line gives the full
length once he stops. A false interruption (Milo paused for a noise) is logged with whether he
resumed. :meth:`TurnTimer.summary` adds medians and p90s for the session.
"""

from __future__ import annotations

import logging
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("little_explorer.voice.timing")

QUIET_TOOL = "stay_quiet"
# A programmatic interruption this soon after note_interrupt() is the noted cause.
CAUSE_WINDOW_S = 2.0


def p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))]


def _stats(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"median {statistics.median(values):.2f}s p90 {p90(values):.2f}s (n={len(values)})"


@dataclass
class TurnTimer:
    clock: Callable[[], float] = time.monotonic
    # Speech the VAD needed before it said "speaking" (its min_speech_duration).
    lead_s: float = 0.0
    waits: list[float] = field(default_factory=list)
    answers: list[float] = field(default_factory=list)
    totals: list[float] = field(default_factory=list)
    segments: int = 0
    replies: int = 0
    quiet_turns: int = 0
    interruptions: dict[str, int] = field(default_factory=dict)
    false_interruptions: int = 0
    resumed: int = 0
    overlaps: list[float] = field(default_factory=list)
    _user_started: float | None = None
    _user_stopped: float | None = None
    _thinking: float | None = None
    _agent_speaking: bool = False
    _overlap_started: float | None = None
    _last_overlap: float | None = None
    _cause: str | None = None
    _cause_at: float = 0.0

    def attach(self, session: Any) -> TurnTimer:
        session.on("user_state_changed", lambda ev: self.user_state(ev.new_state))
        session.on("agent_state_changed", lambda ev: self.agent_state(ev.new_state))
        session.on("speech_created", lambda ev: self.speech_created(ev.speech_handle))
        session.on("agent_false_interruption", lambda ev: self.false_interruption(bool(ev.resumed)))
        session.on("function_tools_executed", lambda ev: self.tools([c.name for c in ev.function_calls]))
        return self

    def user_state(self, state: str) -> None:
        now = self.clock()
        if state == "speaking":
            self._user_started = now
            if self._agent_speaking:
                self._overlap_started = now - self.lead_s
        elif state == "listening" and self._user_started is not None:
            self._user_stopped = now
            self.segments += 1
            if self._overlap_started is not None:
                self._last_overlap = now - self._overlap_started
                self._overlap_started = None
                self.overlaps.append(self._last_overlap)
                log.info("he talked over Milo for %.2fs", self._last_overlap)

    def agent_state(self, state: str) -> None:
        now = self.clock()
        if state == "thinking":
            self._thinking = now
        elif state == "speaking":
            self._agent_speaking = True
            stopped, thinking = self._user_stopped, self._thinking
            self._user_stopped = self._thinking = None
            if stopped is None:
                return  # greeting, a resumed reply, or Milo speaking on his own
            self.replies += 1
            total = now - stopped
            self.totals.append(total)
            parts = [f"total {total:.2f}s"]
            if thinking is not None and thinking >= stopped:
                self.waits.append(thinking - stopped)
                self.answers.append(now - thinking)
                parts = [f"wait {thinking - stopped:.2f}s", f"answer {now - thinking:.2f}s", *parts]
            log.info("turn %d: %s", self.replies, ", ".join(parts))
        else:
            self._agent_speaking = False

    def speech_created(self, handle: Any) -> None:
        started = self.clock()

        def done(h: Any) -> None:
            if not getattr(h, "interrupted", False):
                return
            now = self.clock()
            source = str(getattr(h, "_interrupt_source", None) or "unknown")
            if source == "programmatic" and self._cause is not None and now - self._cause_at <= CAUSE_WINDOW_S:
                source = self._cause
            self._cause = None
            self.interruptions[source] = self.interruptions.get(source, 0) + 1
            if self._overlap_started is not None:
                speech = f", user speech {now - self._overlap_started:.2f}s so far"
            elif self._last_overlap is not None:
                speech = f", user speech {self._last_overlap:.2f}s"
            else:
                speech = ""
            log.info("interrupted by %s after %.2fs%s", source, now - started, speech)
            self._last_overlap = None

        handle.add_done_callback(done)

    def note_interrupt(self, cause: str) -> None:
        """Say why the next programmatic interruption happens (``barge_in`` or ``guard``)."""
        self._cause, self._cause_at = cause, self.clock()

    def false_interruption(self, resumed: bool) -> None:
        self.false_interruptions += 1
        self.resumed += int(resumed)
        log.info("false interruption (a noise or short sound): %s", "resumed" if resumed else "not resumed")

    def tools(self, names: list[str]) -> None:
        if QUIET_TOOL in names:
            self.quiet_turns += 1
            log.info("stayed quiet")

    def summary(self) -> str:
        cut = ", ".join(f"{k} {v}" for k, v in sorted(self.interruptions.items())) or "none"
        return (
            f"user speech {self.segments}, replies {self.replies}, stayed quiet {self.quiet_turns}; "
            f"wait {_stats(self.waits)}; answer {_stats(self.answers)}; total {_stats(self.totals)}; "
            f"interrupted: {cut}; talked over Milo {_stats(self.overlaps)}; "
            f"false interruptions {self.false_interruptions} (resumed {self.resumed})"
        )

    async def log_summary(self, *_: Any) -> None:
        log.info("session timing: %s", self.summary())
