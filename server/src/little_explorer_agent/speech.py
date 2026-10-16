"""Speech-to-text and text-to-speech for Pip.

``PIP_SPEECH=openai`` (default) uses OpenAI transcription and a warm OpenAI voice through the
LiveKit OpenAI plugin, so it works on any LiveKit server. ``PIP_SPEECH=livekit`` uses the voice-kit
preset's LiveKit Inference models and turn detector (LiveKit Cloud).
"""

from __future__ import annotations

import os
from typing import Any

from voice_kit import VoicePreset

VOICE_STYLE = (
    "You are Pip, a friendly, gentle buddy talking with a 6-year-old. Speak warmly and a little "
    "slowly, with a smile in your voice and clear, simple words."
)


def speech_mode() -> str:
    mode = os.environ.get("PIP_SPEECH", "openai").strip().lower()
    if mode not in ("openai", "livekit"):
        raise ValueError(f"PIP_SPEECH must be 'openai' or 'livekit', not {mode!r}")
    return mode


def speech_setup(preset: VoicePreset) -> tuple[VoicePreset, dict[str, Any]]:
    """The preset to use plus ``stt``/``tts`` session overrides for the chosen speech provider."""
    if speech_mode() == "livekit":
        return preset, {}
    from livekit.plugins import openai as lk_openai

    stt = lk_openai.STT(
        model=os.environ.get("PIP_OPENAI_STT_MODEL", "gpt-4o-transcribe"),
        language="en",
        # Children read slowly; let a pause run before OpenAI closes a segment. The session's
        # patient endpointing still waits on top of this before Pip replies.
        turn_detection={"silence_duration_ms": 900, "prefix_padding_ms": 400},
        noise_reduction_type="near_field",
    )
    tts = lk_openai.TTS(
        model=os.environ.get("PIP_OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        voice=os.environ.get("PIP_OPENAI_VOICE", "coral"),
        instructions=VOICE_STYLE,
    )
    # The model turn detector is a LiveKit Inference service too; segment on the STT's end of speech.
    return preset.with_overrides(turn_detection="stt"), {"stt": stt, "tts": tts}
