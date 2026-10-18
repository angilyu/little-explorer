"""Speech-to-speech sessions on the OpenAI Realtime API.

One model hears the child and speaks back, so there is no separate think -> speak chain. The app's
page context is folded into the model's instructions and refreshed on every page turn.

Turn-taking (``RealtimeConfig.turns``):

* ``server`` (default): OpenAI's ``semantic_vad`` decides when he has finished, so a slow reader
  who pauses between words isn't talked over. Barge-in (``barge_in``):

  * ``vad`` (default): Milo stops only once a local Silero VAD has heard
    ``min_interruption_duration`` of continuous speech, so a clap, a bump or a short sound can't
    cut him off, and a sound while he is about to answer can't cancel the answer.
  * ``model``: OpenAI cancels Milo the moment its VAD hears anything (the old behavior).

* ``client``: LiveKit decides turns with a local VAD (``end_of_turn="vad"``) or its end-of-turn
  model, plus a streaming transcriber so wordless sounds never start a turn. Measured slower than
  ``server`` (see the README), kept for experiments.

Topic discipline comes from the instructions. An optional ``guard`` (a :data:`TurnGate`) runs on
each finished transcript *after* the model has started answering; if it says LISTEN or IGNORE the
reply is cut off. It never delays a reply. A final transcript that repeats the last one (the same
item, or the same words within ``DUPLICATE_WINDOW_S``) is skipped: the transcriber sometimes
repeats the previous turn for a short noise or Milo's own echo, and that is not a new turn.
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
from .turns import TurnDecision, TurnGate, UserTurn, _words, decide, is_noise

log = logging.getLogger("little_explorer.voice.realtime")

Eagerness = Literal["low", "medium", "high", "auto"]
TurnTaking = Literal["client", "server"]
InterruptionMode = Literal["vad", "adaptive"]
EndOfTurn = Literal["model", "vad"]
BargeIn = Literal["vad", "model"]
InstructionsBuilder = Callable[[Mapping[str, Any]], str]

# A reply that starts this long after a guarded transcript still belongs to that turn.
GUARD_WINDOW_S = 4.0
MAX_GUARD_HISTORY = 8
# The same words again this soon are a transcription echo, not a new question.
DUPLICATE_WINDOW_S = 4.0
# After a barge-in, a short sound this soon is never cut: Milo may finish what he was saying.
RESUME_WINDOW_S = 6.0


@dataclass(frozen=True)
class RealtimeConfig:
    model: str = "gpt-realtime"
    voice: str = "coral"
    turns: TurnTaking = "server"
    language: str = "en"
    noise_reduction: Literal["near_field", "far_field"] | None = "near_field"
    # server turns: how long semantic_vad waits for more words (low waits up to ~8 s).
    eagerness: Eagerness = "low"
    # server turns: ``vad`` = Milo stops only after min_interruption_duration of continuous speech
    # (local Silero VAD), so claps and short sounds don't cut him off; ``model`` = OpenAI cancels
    # him on any sound its VAD hears.
    barge_in: BargeIn = "vad"
    # The model's own transcription (server turns): feeds the guard and the star check.
    transcribe_model: str = "gpt-4o-mini-transcribe"
    # client turns: streaming words for turn-taking, the guard and stars (None = the model's own
    # transcription, and then any sound the VAD hears counts as a turn).
    stt_model: str | None = "gpt-realtime-whisper"
    # client turns: ``vad`` ends the turn min_endpointing_delay after his last word; ``model`` lets
    # LiveKit's end-of-turn model wait up to max_endpointing_delay when he sounds mid-sentence.
    end_of_turn: EndOfTurn = "vad"
    min_endpointing_delay: float = 0.5
    max_endpointing_delay: float = 3.0
    # Barge-in: Milo stops once he has heard this much continuous speech. Client turns also need
    # this many words, and Milo resumes after false_interruption_timeout if it was nothing.
    interruption_mode: InterruptionMode = "vad"
    min_interruption_duration: float = 0.6
    min_interruption_words: int = 1
    false_interruption_timeout: float = 1.0


def realtime_model(config: RealtimeConfig) -> Any:
    from livekit.plugins import openai as lk_openai
    from openai.types import realtime

    kwargs: dict[str, Any] = {}
    if config.noise_reduction:
        kwargs["input_audio_noise_reduction"] = config.noise_reduction
    client = config.turns == "client"
    transcription = None
    if not (client and config.stt_model):
        transcription = realtime.AudioTranscription(model=config.transcribe_model, language=config.language)
    turn_detection = None
    soft_barge_in = not client and config.barge_in == "vad"
    if not client:
        turn_detection = realtime.realtime_audio_input_turn_detection.SemanticVad(
            type="semantic_vad",
            eagerness=config.eagerness,
            create_response=True,
            interrupt_response=not soft_barge_in,
        )
    model = lk_openai.realtime.RealtimeModel(
        model=config.model,
        voice=config.voice,
        modalities=["audio"],
        input_audio_transcription=transcription,
        turn_detection=turn_detection,
        **kwargs,
    )
    if soft_barge_in:
        # Otherwise LiveKit cancels Milo the moment OpenAI hears any sound (a clap, an "mm",
        # his own echo); start_realtime_session stops him on sustained speech instead.
        model.capabilities.supports_overlapping_speech = True
    return model


def client_turn_handling(config: RealtimeConfig) -> dict[str, Any]:
    """``TurnHandlingOptions``-shaped dict for client turns (the session adds the detector)."""
    return {
        "endpointing": {"min_delay": config.min_endpointing_delay, "max_delay": config.max_endpointing_delay},
        "interruption": {
            "enabled": True,
            "mode": config.interruption_mode,
            "min_duration": config.min_interruption_duration,
            "min_words": config.min_interruption_words if config.stt_model else 0,
            "resume_false_interruption": True,
            "false_interruption_timeout": config.false_interruption_timeout,
        },
        "preemptive_generation": {"enabled": False},
    }


def session_kwargs(config: RealtimeConfig) -> dict[str, Any]:
    """``AgentSession`` arguments besides the model. Server turns: a local VAD that only reports
    speech after min_interruption_duration, for the soft barge-in. Client turns: VAD, end-of-turn
    detection and (optionally) a streaming transcriber."""
    from livekit.agents import TurnHandlingOptions, inference

    if config.turns != "client":
        if config.barge_in != "vad":
            return {}
        return {
            "vad": inference.VAD(model="silero", min_speech_duration=config.min_interruption_duration),
            # The VAD only feeds the barge-in; OpenAI still decides when his turn ends.
            "turn_handling": TurnHandlingOptions(turn_detection="realtime_llm"),
        }

    vad = inference.VAD(model="silero")
    detector: Any = inference.TurnDetector(version="v1-mini") if config.end_of_turn == "model" else "vad"
    out: dict[str, Any] = {
        "vad": vad,
        "turn_handling": TurnHandlingOptions(**client_turn_handling(config), turn_detection=detector),
    }
    if config.stt_model:
        from livekit.plugins import openai as lk_openai

        out["stt"] = lk_openai.STT(model=config.stt_model, language=config.language, vad=vad)
    return out


class ReplyGuard:
    """Cuts off a reply to a turn the ``gate`` rejects. Pure bookkeeping, testable without LiveKit.

    ``check`` runs the gate on a final transcript; ``should_cut`` says whether a reply that is
    speaking (or starts) now belongs to a rejected turn. After :meth:`cut_off` (a barge-in), a
    short IGNORE turn is let through for ``RESUME_WINDOW_S`` so Milo can finish his answer.
    """

    def __init__(self, gate: TurnGate, *, window: float = GUARD_WINDOW_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.gate = gate
        self.window = window
        self.clock = clock
        self.history: list[tuple[str, str]] = []
        self._reject_until = 0.0
        self._seen_items: list[str] = []
        self._last_text = ""
        self._last_at = float("-inf")
        self._cut_off_at = float("-inf")

    def cut_off(self) -> None:
        self._cut_off_at = self.clock()

    def is_repeat(self, text: str, item_id: str | None = None) -> bool:
        """True for a final transcript already seen: the same item, or the same words as the last
        transcript within ``DUPLICATE_WINDOW_S``. Otherwise remembers it and returns False."""
        now = self.clock()
        words = " ".join(_words(text))
        if (item_id and item_id in self._seen_items) or (
            words and words == self._last_text and now - self._last_at <= DUPLICATE_WINDOW_S
        ):
            return True
        if item_id:
            self._seen_items.append(item_id)
            del self._seen_items[:-MAX_GUARD_HISTORY]
        self._last_text, self._last_at = words, now
        return False

    async def check(self, text: str, context: Mapping[str, Any]) -> tuple[UserTurn, TurnDecision]:
        turn = UserTurn(text=text, context=dict(context), history=tuple(self.history))
        decision = await decide(self.gate, turn)
        resuming = (
            self.clock() - self._cut_off_at <= RESUME_WINDOW_S and len(_words(text)) < 3 and not is_noise(text)
        )
        if decision is TurnDecision.IGNORE and resuming:
            decision = TurnDecision.RESPOND
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
    on_interrupt: Callable[[str], None] | None = None,
) -> AgentSession:
    """Connect, follow the app's page context, and start a realtime ``AgentSession``.

    ``instructions(context)`` builds the full instructions for the current page; they are pushed
    to the model again whenever the context changes. ``preset`` only supplies the room's noise
    cancellation; turn-taking follows ``config.turns``. Pass ``context_store`` to share the page
    context with the caller. ``on_interrupt(cause)`` is told just before Milo is cut off on purpose
    (``barge_in`` or ``guard``), for the timing log.
    """
    await ctx.connect()
    store = ContextStore() if context_store is None else context_store
    attach_room_context(store, ctx.room)
    agent = Agent(instructions=instructions(store.snapshot()), tools=list(tools))
    session = AgentSession(
        llm=realtime_model(config), user_away_timeout=preset.user_away_timeout, **session_kwargs(config)
    )
    tasks: set[asyncio.Task[Any]] = set()

    def spawn(coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def on_context(_changed: dict[str, Any]) -> None:
        spawn(agent.update_instructions(instructions(store.snapshot())))

    store.subscribe(on_context)

    reply_guard = ReplyGuard(guard) if guard is not None else None

    def interrupt(cause: str) -> None:
        if on_interrupt is not None:
            on_interrupt(cause)
        if cause == "barge_in" and reply_guard is not None:
            reply_guard.cut_off()
        session.interrupt()

    if config.turns == "server" and config.barge_in == "vad":

        @session.on("user_state_changed")
        def _on_user_state(ev: Any) -> None:
            # The local VAD only says "speaking" after min_interruption_duration of continuous speech.
            if ev.new_state == "speaking" and session.agent_state == "speaking":
                log.info("barge-in: he talked over Milo (at least %.2fs of speech)", config.min_interruption_duration)
                try:
                    interrupt("barge_in")
                except RuntimeError:
                    log.debug("barge-in: reply could not be interrupted")

    if reply_guard is not None:

        def cut_if_rejected() -> None:
            if reply_guard.should_cut() and session.agent_state in ("thinking", "speaking"):
                reply_guard.consume()
                log.info("guard: cutting off reply to a rejected turn")
                interrupt("guard")

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
            if not (ev.is_final and ev.transcript.strip()):
                return
            if reply_guard.is_repeat(ev.transcript, getattr(ev, "item_id", None)):
                log.info("guard: skipped a repeated transcript (echo or noise)")
                return
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
