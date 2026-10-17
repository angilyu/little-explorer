"""Speech-to-speech sessions on the OpenAI Realtime API.

One model hears the child and speaks back, so there is no separate transcribe -> think -> speak
chain: turn-taking (``semantic_vad``) and barge-in happen inside the model service. The app's page
context is folded into the model's instructions and refreshed on every page turn.

Topic discipline comes from the instructions. An optional ``guard`` (a :data:`TurnGate`) runs on
each finished transcript *after* the model has started answering; if it says LISTEN or IGNORE the
reply is cut off. It never delays a reply.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from livekit.agents import Agent, AgentSession, JobContext

from .context import ContextStore
from .preset import VoicePreset
from .session import DecisionCallback, _room_options, attach_room_context
from .turns import TurnDecision, TurnGate, UserTurn, decide

log = logging.getLogger("little_explorer.voice.realtime")

Eagerness = Literal["low", "medium", "high", "auto"]
InstructionsBuilder = Callable[[Mapping[str, Any]], str]

# A reply that starts this long after a guarded transcript still belongs to that turn.
GUARD_WINDOW_S = 4.0
MAX_GUARD_HISTORY = 8


@dataclass(frozen=True)
class RealtimeConfig:
    model: str = "gpt-realtime"
    voice: str = "coral"
    # ``low`` waits longest before deciding he has finished, so slow reading is not cut off.
    eagerness: Eagerness = "low"
    transcribe_model: str = "gpt-4o-mini-transcribe"
    language: str = "en"
    noise_reduction: Literal["near_field", "far_field"] | None = "near_field"


def realtime_model(config: RealtimeConfig) -> Any:
    from livekit.plugins import openai as lk_openai
    from openai.types import realtime

    kwargs: dict[str, Any] = {}
    if config.noise_reduction:
        kwargs["input_audio_noise_reduction"] = config.noise_reduction
    return lk_openai.realtime.RealtimeModel(
        model=config.model,
        voice=config.voice,
        modalities=["audio"],
        input_audio_transcription=realtime.AudioTranscription(
            model=config.transcribe_model, language=config.language
        ),
        turn_detection=realtime.realtime_audio_input_turn_detection.SemanticVad(
            type="semantic_vad",
            eagerness=config.eagerness,
            create_response=True,
            interrupt_response=True,
        ),
        **kwargs,
    )


class ReplyGuard:
    """Cuts off a reply to a turn the ``gate`` rejects. Pure bookkeeping, testable without LiveKit.

    ``check`` runs the gate on a final transcript; ``should_cut`` says whether a reply that is
    speaking (or starts) now belongs to a rejected turn.
    """

    def __init__(self, gate: TurnGate, *, window: float = GUARD_WINDOW_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.gate = gate
        self.window = window
        self.clock = clock
        self.history: list[tuple[str, str]] = []
        self._reject_until = 0.0

    async def check(self, text: str, context: Mapping[str, Any]) -> tuple[UserTurn, TurnDecision]:
        turn = UserTurn(text=text, context=dict(context), history=tuple(self.history))
        decision = await decide(self.gate, turn)
        if decision is TurnDecision.RESPOND:
            self._reject_until = 0.0
        else:
            self._reject_until = self.clock() + self.window
        if decision is not TurnDecision.IGNORE:
            self.remember("user", text)
        return turn, decision

    def remember(self, role: str, text: str) -> None:
        if text:
            self.history.append((role, text))
            del self.history[:-MAX_GUARD_HISTORY]

    def should_cut(self) -> bool:
        return self.clock() < self._reject_until

    def consume(self) -> None:
        self._reject_until = 0.0


async def start_realtime_session(
    ctx: JobContext,
    *,
    config: RealtimeConfig,
    preset: VoicePreset,
    instructions: InstructionsBuilder,
    tools: Sequence[Any] = (),
    guard: TurnGate | None = None,
    on_decision: DecisionCallback | None = None,
    greeting: str | None = None,
    context_store: ContextStore | None = None,
) -> AgentSession:
    """Connect, follow the app's page context, and start a realtime ``AgentSession``.

    ``instructions(context)`` builds the full instructions for the current page; they are pushed
    to the model again whenever the context changes. ``preset`` only supplies the room's noise
    cancellation; turn-taking belongs to the realtime model. Pass ``context_store`` to share the
    page context with the caller.
    """
    await ctx.connect()
    store = ContextStore() if context_store is None else context_store
    attach_room_context(store, ctx.room)
    agent = Agent(instructions=instructions(store.snapshot()), tools=list(tools))
    session = AgentSession(llm=realtime_model(config), user_away_timeout=preset.user_away_timeout)
    tasks: set[asyncio.Task[Any]] = set()

    def spawn(coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def on_context(_changed: dict[str, Any]) -> None:
        spawn(agent.update_instructions(instructions(store.snapshot())))

    store.subscribe(on_context)

    if guard is not None:
        reply_guard = ReplyGuard(guard)

        def cut_if_rejected() -> None:
            if reply_guard.should_cut() and session.agent_state in ("thinking", "speaking"):
                reply_guard.consume()
                log.info("guard: cutting off reply to a rejected turn")
                session.interrupt()

        async def run_guard(text: str) -> None:
            turn, decision = await reply_guard.check(text, store.snapshot())
            if on_decision is not None:
                try:
                    result = on_decision(turn, decision)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    log.exception("on_decision callback failed")
            cut_if_rejected()

        @session.on("user_input_transcribed")
        def _on_transcript(ev: Any) -> None:
            if ev.is_final and ev.transcript.strip():
                spawn(run_guard(ev.transcript))

        @session.on("agent_state_changed")
        def _on_state(ev: Any) -> None:
            if ev.new_state in ("thinking", "speaking"):
                cut_if_rejected()

        @session.on("conversation_item_added")
        def _on_item(ev: Any) -> None:
            item = ev.item
            if getattr(item, "role", None) == "assistant":
                reply_guard.remember("assistant", getattr(item, "text_content", "") or "")

    await session.start(agent=agent, room=ctx.room, room_options=_room_options(preset))
    if greeting:
        session.generate_reply(instructions=f'Greet him by saying exactly: "{greeting}"')
    return session
