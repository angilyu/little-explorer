"""LiveKit wiring: run a LangGraph graph as a gated, context-aware voice agent.

* :class:`GraphAgent` runs the turn gate on every finished user turn: LISTEN keeps the words in
  history without replying, IGNORE drops them, RESPOND adds the page context as a system note.
* :func:`attach_room_context` keeps a :class:`ContextStore` in sync with the app's participant
  attributes and JSON data messages on the ``context`` topic.
* :func:`start_graph_session` connects, wires both, and starts the ``AgentSession``.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, TurnHandlingOptions, inference, llm
from livekit.agents.llm import StopResponse
from livekit.agents.voice import room_io

from .context import ContextStore
from .preset import VoicePreset
from .turns import TurnDecision, TurnGate, UserTurn, decide

log = logging.getLogger(__name__)

CONTEXT_TOPIC = "context"

ContextFormatter = Callable[[Mapping[str, Any]], str | None]
DecisionCallback = Callable[[UserTurn, TurnDecision], "None | Awaitable[None]"]


def _history(chat_ctx: llm.ChatContext, exclude_id: str | None = None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in chat_ctx.items:
        if getattr(item, "type", None) != "message" or getattr(item, "id", None) == exclude_id:
            continue
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if role in ("user", "assistant") and text:
            out.append((role, text))
    return out


class GraphAgent(Agent):
    """An ``Agent`` whose replies come from the session LLM (the LangGraph graph), behind a gate."""

    def __init__(
        self,
        *,
        instructions: str,
        gate: TurnGate | None,
        context: ContextStore,
        context_message: ContextFormatter | None = None,
        on_decision: DecisionCallback | None = None,
    ) -> None:
        super().__init__(instructions=instructions)
        self.gate = gate
        self.context = context
        self.context_message = context_message
        self.on_decision = on_decision

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        snapshot = self.context.snapshot()
        turn = UserTurn(
            text=new_message.text_content or "",
            context=snapshot,
            history=_history(turn_ctx, exclude_id=new_message.id),
        )
        decision = await decide(self.gate, turn)
        if self.on_decision is not None:
            try:
                result = self.on_decision(turn, decision)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("on_decision callback failed")
        if decision is TurnDecision.IGNORE:
            raise StopResponse()
        if decision is TurnDecision.LISTEN:
            # StopResponse skips adding the user message; keep it so later answers see it.
            ctx = self.chat_ctx.copy()
            ctx.items.append(new_message)
            await self.update_chat_ctx(ctx)
            raise StopResponse()
        if self.context_message is not None and (note := self.context_message(snapshot)):
            turn_ctx.add_message(role="system", content=note)


def attach_room_context(store: ContextStore, room: rtc.Room, *, topic: str = CONTEXT_TOPIC) -> Callable[[], None]:
    """Keep ``store`` in sync with remote participants. Returns a ``detach`` function.

    Sources: participant attributes except ``lk.*`` ones (an empty value deletes the key), and
    JSON objects sent as data packets on ``topic``.
    """

    def apply_attributes(values: Mapping[str, str]) -> None:
        update = {k: (v if v != "" else None) for k, v in values.items() if not k.startswith("lk.")}
        if update:
            store.update(update)

    for participant in room.remote_participants.values():
        apply_attributes(participant.attributes)

    def on_connected(participant: rtc.RemoteParticipant) -> None:
        apply_attributes(participant.attributes)

    def on_attributes(changed: dict[str, str], participant: rtc.Participant) -> None:
        if participant.identity != room.local_participant.identity:
            apply_attributes(changed)

    def on_data(packet: rtc.DataPacket) -> None:
        if packet.topic == topic:
            store.handle_data(packet.data)

    handlers: list[tuple[str, Callable[..., Any]]] = [
        ("participant_connected", on_connected),
        ("participant_attributes_changed", on_attributes),
        ("data_received", on_data),
    ]
    for event, handler in handlers:
        room.on(event, handler)

    def detach() -> None:
        for event, handler in handlers:
            room.off(event, handler)

    return detach


def _turn_handling(preset: VoicePreset) -> TurnHandlingOptions:
    opts = preset.turn_handling()
    if preset.turn_detection == "model":
        opts["turn_detection"] = inference.TurnDetector()
    return TurnHandlingOptions(**opts)


def _room_options(preset: VoicePreset) -> room_io.RoomOptions:
    """Room options with the preset's noise cancellation (LiveKit Cloud only; skipped if the plugin is missing)."""
    if preset.noise_cancellation == "none":
        return room_io.RoomOptions()
    try:
        from livekit.plugins import noise_cancellation
    except ImportError:
        log.warning("%s noise cancellation wanted but livekit-plugins-noise-cancellation is missing", preset.noise_cancellation)
        return room_io.RoomOptions()
    nc = noise_cancellation.BVC() if preset.noise_cancellation == "bvc" else noise_cancellation.NC()
    return room_io.RoomOptions(audio_input=room_io.AudioInputOptions(noise_cancellation=nc))


def _inference_tts(preset: VoicePreset) -> Any:
    if preset.tts_voice:
        return inference.TTS(model=preset.tts_model, voice=preset.tts_voice)
    return inference.TTS(model=preset.tts_model)


async def start_graph_session(
    ctx: JobContext,
    graph: Any,
    *,
    preset: VoicePreset,
    gate: TurnGate | None,
    instructions: str,
    context_message: ContextFormatter | None = None,
    on_decision: DecisionCallback | None = None,
    greeting: str | None = None,
    stt: Any = None,
    tts: Any = None,
) -> AgentSession:
    """Connect, follow the app's page context, and start an ``AgentSession`` driven by ``graph``.

    The graph receives ``{"messages": [...]}`` and streams the spoken reply with
    ``get_stream_writer()``. ``stt``/``tts`` replace the preset's LiveKit Inference models.
    """
    from livekit.plugins import langchain

    await ctx.connect()
    store = ContextStore()
    attach_room_context(store, ctx.room)
    session = AgentSession(
        stt=stt if stt is not None else inference.STT(model=preset.stt_model, language=preset.language),
        tts=tts if tts is not None else _inference_tts(preset),
        llm=langchain.LLMAdapter(graph=graph, stream_mode="custom"),
        turn_handling=_turn_handling(preset),
        user_away_timeout=preset.user_away_timeout,
    )
    agent = GraphAgent(
        instructions=instructions,
        gate=gate,
        context=store,
        context_message=context_message,
        on_decision=on_decision,
    )
    await session.start(agent=agent, room=ctx.room, room_options=_room_options(preset))
    if greeting:
        session.say(greeting)
    return session
