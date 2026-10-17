"""LiveKit wiring: run a LangGraph graph as a gated, context-aware voice agent.

* :class:`GraphAgent` runs the turn gate on every finished user turn: LISTEN keeps the words in
  history without replying, IGNORE drops them, RESPOND adds the page context as a system note.
  An optional ``parallel_gate`` (the slower LLM topic check) runs alongside reply generation and
  holds the reply back until it agrees, so it adds no latency when it says RESPOND.
* :func:`attach_room_context` keeps a :class:`ContextStore` in sync with the app's participant
  attributes and JSON data messages on the ``context`` topic.
* :func:`start_graph_session` connects, wires both, and starts the ``AgentSession``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping
from typing import Any

from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, ModelSettings, TurnHandlingOptions, inference, llm
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
        parallel_gate: TurnGate | None = None,
    ) -> None:
        super().__init__(instructions=instructions)
        self.gate = gate
        self.parallel_gate = parallel_gate
        self.context = context
        self.context_message = context_message
        self.on_decision = on_decision
        self._pending_gate: asyncio.Task[TurnDecision] | None = None
        self._pending_message_id: str | None = None
        self._ignored_ids: set[str] = set()

    async def _report(self, turn: UserTurn, decision: TurnDecision) -> None:
        if self.on_decision is None:
            return
        try:
            result = self.on_decision(turn, decision)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("on_decision callback failed")

    async def _parallel_decision(self, turn: UserTurn) -> TurnDecision:
        decision = await decide(self.parallel_gate, turn)
        await self._report(turn, decision)
        return decision

    async def _forget_ignored(self) -> None:
        """Drop turns the parallel gate IGNOREd (they reach history before the gate answers)."""
        if not self._ignored_ids:
            return
        ctx = self.chat_ctx.copy()
        kept = [item for item in ctx.items if getattr(item, "id", None) not in self._ignored_ids]
        if len(kept) != len(ctx.items):
            ctx.items[:] = kept
            await self.update_chat_ctx(ctx)
            self._ignored_ids.clear()

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        snapshot = self.context.snapshot()
        turn = UserTurn(
            text=new_message.text_content or "",
            context=snapshot,
            history=_history(turn_ctx, exclude_id=new_message.id),
        )
        await self._forget_ignored()
        decision = await decide(self.gate, turn)
        if decision is not TurnDecision.RESPOND or self.parallel_gate is None:
            await self._report(turn, decision)
        if decision is TurnDecision.IGNORE:
            raise StopResponse()
        if decision is TurnDecision.LISTEN:
            # StopResponse skips adding the user message; keep it so later answers see it.
            ctx = self.chat_ctx.copy()
            ctx.items.append(new_message)
            await self.update_chat_ctx(ctx)
            raise StopResponse()
        if self.parallel_gate is not None:
            self._pending_gate = asyncio.create_task(self._parallel_decision(turn))
            self._pending_message_id = new_message.id
        if self.context_message is not None and (note := self.context_message(snapshot)):
            turn_ctx.add_message(role="system", content=note)

    async def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterable[Any]:
        gate, self._pending_gate = self._pending_gate, None
        message_id, self._pending_message_id = self._pending_message_id, None
        stream = Agent.default.llm_node(self, chat_ctx, tools, model_settings)
        if gate is None:
            async for chunk in stream:
                yield chunk
            return
        # Generate while the gate thinks; speak nothing until it says RESPOND.
        queue: asyncio.Queue[Any] = asyncio.Queue()
        done = object()

        async def pump() -> None:
            try:
                async for chunk in stream:
                    queue.put_nowait(chunk)
            finally:
                queue.put_nowait(done)

        pump_task = asyncio.create_task(pump())
        try:
            try:
                decision = await gate
            except Exception:
                log.exception("parallel gate failed; answering anyway")
                decision = TurnDecision.RESPOND
            if decision is not TurnDecision.RESPOND:
                if message_id:
                    self._ignored_ids.add(message_id)
                    # The turn joins history once its (now empty) reply is scheduled; drop it then.
                    asyncio.get_running_loop().call_later(1.0, lambda: asyncio.ensure_future(self._forget_ignored()))
                return
            while (chunk := await queue.get()) is not done:
                yield chunk
        finally:
            if not pump_task.done():
                pump_task.cancel()


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
        opts["turn_detection"] = inference.TurnDetector(version=preset.turn_detector_version)
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
    vad: Any = None,
    parallel_gate: TurnGate | None = None,
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
        **({"vad": vad} if vad is not None else {}),
    )
    agent = GraphAgent(
        instructions=instructions,
        gate=gate,
        parallel_gate=parallel_gate,
        context=store,
        context_message=context_message,
        on_decision=on_decision,
    )
    await session.start(agent=agent, room=ctx.room, room_options=_room_options(preset))
    if greeting:
        session.say(greeting)
    return session
