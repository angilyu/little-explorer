"""Speech-to-text and text-to-speech for Milo.

``PIP_SPEECH=openai`` (default) uses streaming OpenAI transcription and a warm OpenAI voice through
the LiveKit OpenAI plugin, with the local Silero VAD and end-of-turn model. ``PIP_SPEECH=livekit`` uses the patient
preset's LiveKit Inference models and turn detector (LiveKit Cloud).
"""

from __future__ import annotations

import os
from typing import Any

from .voice import VoicePreset

VOICE_STYLE = (
    "Voice: a kind, cheerful grown-up friend chatting with a 6-year-old. Natural, relaxed and "
    "conversational, like a real person, with a smile in the voice. Tone: warm and curious, never "
    "sing-song, exaggerated or robotic. Pace: an easy, everyday pace with natural pauses between "
    "sentences. Questions rise naturally at the end."
)

# Synthesize at least this many characters at once: one-sentence chunks ("Wow!") each get their own
# intonation and a gap, so a reply sounded stitched together.
TTS_MIN_CHUNK_CHARS = 80


def speech_mode() -> str:
    mode = os.environ.get("PIP_SPEECH", "openai").strip().lower()
    if mode not in ("openai", "livekit"):
        raise ValueError(f"PIP_SPEECH must be 'openai' or 'livekit', not {mode!r}")
    return mode


# Streams words while he is still talking (interim results), so turn-taking and barge-in see them
# early. It has no server-side endpointing; the local VAD closes each segment.
DEFAULT_STT_MODEL = "gpt-realtime-whisper"


def speech_setup(preset: VoicePreset) -> tuple[VoicePreset, dict[str, Any]]:
    """The preset to use plus ``stt``/``tts``/``vad`` session overrides for the chosen provider."""
    if speech_mode() == "livekit":
        return preset, {}
    from livekit.agents import inference, tokenize
    from livekit.agents import tts as agents_tts
    from livekit.plugins import openai as lk_openai

    # Silero runs on this machine: it drives barge-in and closes the STT segments.
    vad = inference.VAD(model="silero")
    stt_model = os.environ.get("PIP_OPENAI_STT_MODEL", DEFAULT_STT_MODEL)
    if stt_model.startswith(("gpt-realtime-whisper", "gpt-live-transcribe")):
        stt = lk_openai.STT(model=stt_model, language="en", vad=vad)
    else:
        stt = lk_openai.STT(
            model=stt_model,
            language="en",
            turn_detection={"silence_duration_ms": 500, "prefix_padding_ms": 300},
            noise_reduction_type="near_field",
        )
    tts = agents_tts.StreamAdapter(
        tts=lk_openai.TTS(
            model=os.environ.get("PIP_OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=os.environ.get("PIP_OPENAI_VOICE", "coral"),
            instructions=VOICE_STYLE,
            # Raw PCM streams as it is generated; MP3 arrived in bursts and left gaps mid-reply.
            response_format="pcm",
        ),
        sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(
            retain_format=True, min_sentence_len=TTS_MIN_CHUNK_CHARS
        ),
    )
    # The preset's end-of-turn model runs locally (v1-mini), so it works with OpenAI speech too.
    return preset, {"stt": stt, "tts": tts, "vad": vad}
