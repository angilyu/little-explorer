"""Pip's speech and turn-taking settings.

The patient preset is for slow, quiet or hesitant speakers such as a young child reading aloud: a
long end-of-turn delay, interruption only on deliberate speech, background-voice noise
cancellation and no preemptive generation (nothing is generated until he has really finished).
It is plain data; :mod:`.session` turns it into ``AgentSession`` arguments.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from typing import Any, Literal

NoiseCancellation = Literal["none", "nc", "bvc"]
"""``nc`` = noise cancellation, ``bvc`` = background voice + noise cancellation (removes other
people talking in the room). Both need LiveKit Cloud."""

TurnDetection = Literal["model", "vad", "stt", "manual"]
InterruptionMode = Literal["adaptive", "vad"]
"""``vad`` = barge-in needs ``min_interruption_duration`` of speech (and the word count); ``adaptive`` =
LiveKit's ML detector, which can fire on ~50 ms of audio and so cuts Pip off on echo and noise."""
"""``model`` = LiveKit's end-of-turn model (LiveKit Inference); ``stt`` = the STT's end of speech."""


@dataclass(frozen=True)
class VoicePreset:
    # LiveKit Inference models, used when PIP_SPEECH=livekit (otherwise speech.py passes OpenAI STT/TTS).
    stt_model: str = "assemblyai/universal-3-5-pro"
    tts_model: str = "fishaudio/s2.1-pro"
    tts_voice: str | None = "fa4c9eb3dccc4806b382b40d61c6b10a"
    language: str = "en"
    turn_detection: TurnDetection = "model"
    # Children pause mid-sentence while sounding out words; wait well past a normal pause.
    min_endpointing_delay: float = 1.6
    max_endpointing_delay: float = 6.0
    # Coughs, "um", page turns, short background words and speaker echo should not cut Pip off.
    interruption_mode: InterruptionMode = "vad"
    min_interruption_duration: float = 0.8
    min_interruption_words: int = 2
    false_interruption_timeout: float = 1.5
    noise_cancellation: NoiseCancellation = "bvc"
    # Seconds of mutual silence before the child is marked "away".
    user_away_timeout: float | None = 60.0

    def with_overrides(self, **changes: Any) -> VoicePreset:
        return dataclasses.replace(self, **changes)

    def turn_handling(self) -> dict[str, Any]:
        """``TurnHandlingOptions``-shaped dict; :mod:`.session` adds the model detector for ``"model"``."""
        out: dict[str, Any] = {
            "endpointing": {"min_delay": self.min_endpointing_delay, "max_delay": self.max_endpointing_delay},
            "interruption": {
                "enabled": True,
                "mode": self.interruption_mode,
                "min_duration": self.min_interruption_duration,
                "min_words": self.min_interruption_words,
                "resume_false_interruption": True,
                "false_interruption_timeout": self.false_interruption_timeout,
            },
            # Don't start answering while he may still be mid-thought.
            "preemptive_generation": {"enabled": False},
        }
        if self.turn_detection != "model":
            out["turn_detection"] = self.turn_detection
        return out


PATIENT = VoicePreset()


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def preset_from_env(prefix: str = "PIP_") -> VoicePreset:
    """The patient preset with optional overrides: ``{prefix}STT_MODEL``, ``STT_LANGUAGE``,
    ``TTS_MODEL``, ``TTS_VOICE``, ``MIN_ENDPOINTING_DELAY``, ``MAX_ENDPOINTING_DELAY`` and
    ``NOISE_CANCELLATION`` (none, nc or bvc)."""
    changes: dict[str, Any] = {}
    for env_name, field_name in (
        ("STT_MODEL", "stt_model"),
        ("STT_LANGUAGE", "language"),
        ("TTS_MODEL", "tts_model"),
        ("TTS_VOICE", "tts_voice"),
    ):
        if value := _env(prefix + env_name):
            changes[field_name] = value
    for env_name, field_name in (
        ("MIN_ENDPOINTING_DELAY", "min_endpointing_delay"),
        ("MAX_ENDPOINTING_DELAY", "max_endpointing_delay"),
    ):
        if value := _env(prefix + env_name):
            changes[field_name] = float(value)
    if nc := _env(prefix + "NOISE_CANCELLATION").lower():
        if nc not in ("none", "nc", "bvc"):
            raise ValueError(f"Unknown {prefix}NOISE_CANCELLATION {nc!r}; use none, nc or bvc")
        changes["noise_cancellation"] = nc
    return PATIENT.with_overrides(**changes)
