"""Pip's voice stack: turn gates, the patient voice preset, client context and LiveKit wiring.

Adapted from the MuseVoiceAgent voice stack and trimmed to what Little Explorer uses. The
modules here are framework-free except :mod:`.session` (LiveKit Agents) and :mod:`.tokens`
(LiveKit server API).
"""

from .context import ContextStore
from .models import init_chat_model_from_spec, message_text
from .preset import PATIENT, VoicePreset, preset_from_env
from .turns import TurnDecision, TurnGate, UserTurn, decide, is_question, llm_gate, text_overlap

__all__ = [
    "PATIENT",
    "ContextStore",
    "TurnDecision",
    "TurnGate",
    "UserTurn",
    "VoicePreset",
    "decide",
    "init_chat_model_from_spec",
    "is_question",
    "llm_gate",
    "message_text",
    "preset_from_env",
    "text_overlap",
]
