"""Turn gates: decide whether Pip should answer a finished user turn at all.

* ``RESPOND``: run the reply graph and speak.
* ``LISTEN``: keep the utterance in the conversation history but stay silent (the child reading
  the page aloud).
* ``IGNORE``: drop the utterance entirely (background chatter, TV, off-topic talk).

A gate may be sync or async and return a :class:`TurnDecision` or a bool (``True`` = respond).
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

log = logging.getLogger(__name__)


class TurnDecision(StrEnum):
    RESPOND = "respond"
    LISTEN = "listen"
    IGNORE = "ignore"


@dataclass(frozen=True)
class UserTurn:
    text: str
    """Final transcript of the user's turn."""
    context: Mapping[str, Any] = field(default_factory=dict)
    """Context pushed by the app (book, page, page text)."""
    history: Sequence[tuple[str, str]] = ()
    """Prior conversation as ``(role, text)`` pairs, oldest first; role is ``user`` or ``assistant``."""


TurnGate = Callable[[UserTurn], "bool | TurnDecision | Awaitable[bool | TurnDecision]"]


def _coerce(result: Any) -> TurnDecision:
    if isinstance(result, TurnDecision):
        return result
    if isinstance(result, str):
        return TurnDecision(result.strip().lower())
    return TurnDecision.RESPOND if result else TurnDecision.IGNORE


async def decide(gate: TurnGate | None, turn: UserTurn) -> TurnDecision:
    """Run a gate (sync or async). ``None`` always responds."""
    if gate is None:
        return TurnDecision.RESPOND
    result = gate(turn)
    if inspect.isawaitable(result):
        result = await result
    return _coerce(result)


_WORD = re.compile(r"[\w']+")


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


_QUESTION_START = re.compile(
    r"^\s*(?:(?:um+|uh+|hey|so|and|but|ok(?:ay)?|wait)[,\s]+)*"
    r"(?:what|what's|whats|why|how|who|who's|where|where's|when|which|whose|"
    r"is|are|am|was|were|do|does|did|can|could|will|would|should|shall|have|has|may|might|"
    r"tell me|explain|i wonder|i don't (?:know|understand|get)|what does)\b",
    re.I,
)


def is_question(text: str) -> bool:
    """Ends with ``?`` or starts like a question. Speech-to-text often drops the question mark."""
    value = (text or "").strip()
    return bool(value) and (value.endswith("?") or bool(_QUESTION_START.match(value)))


def text_overlap(spoken: str, reference: str) -> float:
    """Fraction of the spoken words that appear in ``reference`` (0.0 to 1.0)."""
    spoken_words = _words(spoken)
    if not spoken_words:
        return 0.0
    ref = set(_words(reference))
    return sum(1 for w in spoken_words if w in ref) / len(spoken_words)


_LLM_GATE_DEFAULT = """\
You decide whether a voice assistant should answer what its microphone just heard.
Reply with exactly one word:
RESPOND - the user is talking to the assistant and expects an answer.
LISTEN - the user is talking but no answer is wanted yet (for example reading aloud or thinking).
IGNORE - background noise, other people talking, or speech unrelated to the assistant's purpose."""


def llm_gate(
    model: Any,
    instructions: str = "",
    *,
    on_error: TurnDecision = TurnDecision.RESPOND,
    max_history: int = 6,
    context_keys: Sequence[str] | None = None,
) -> Callable[[UserTurn], Awaitable[TurnDecision]]:
    """Ask a small, fast LangChain chat model to classify the turn as RESPOND, LISTEN or IGNORE.

    ``instructions`` describe the assistant's purpose. Any other answer, or an error, yields
    ``on_error``. ``context_keys`` limits which context entries the model sees (default all).
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    system = _LLM_GATE_DEFAULT + (f"\n\nAssistant purpose:\n{instructions.strip()}" if instructions else "")

    async def gate(turn: UserTurn) -> TurnDecision:
        lines: list[str] = []
        ctx = {k: v for k, v in turn.context.items() if context_keys is None or k in context_keys}
        if ctx:
            lines.append("Context:")
            lines.extend(f"- {k}: {v}" for k, v in ctx.items())
        recent = list(turn.history)[-max_history:] if max_history else []
        if recent:
            lines.append("Recent conversation:")
            lines.extend(f"{role}: {text}" for role, text in recent)
        lines.append(f"Just heard: {turn.text}")
        try:
            reply = await model.ainvoke([SystemMessage(system), HumanMessage("\n".join(lines))])
        except Exception:
            log.exception("llm_gate failed; using %s", on_error)
            return on_error
        match = re.search(r"\b(RESPOND|LISTEN|IGNORE)\b", _content_text(getattr(reply, "content", reply)), re.I)
        return TurnDecision(match.group(1).lower()) if match else on_error

    return gate


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)
