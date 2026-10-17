"""LiveKit worker for Milo. Run: ``uv run python -m little_explorer_agent.agent dev``.

``PIP_MODE=pipeline`` (default) or ``PIP_MODE=realtime`` picks the voice engine; see :mod:`.modes`.

Milo turns himself off after ``PIP_IDLE_TIMEOUT_S`` (20) seconds in which neither he nor the child
spoke (0 disables), and says goodbye when the device's minutes for today run out (the token server
passes ``remaining_s``). Either way he sets his ``milo.ended`` attribute to ``idle`` or ``limit`` so
the app can switch its toggle off with the right message, then leaves the room. Each session's
length and cost is reported to the token server for per-device usage (see :mod:`.usage`).

Curious questions earn stars (see :mod:`.stars`): the realtime model calls ``give_star`` with its
answer, :class:`StarKeeper` checks the claim against what was heard, and :class:`StarGame` sends
each award to the app (``milo.star`` data topic) and backs it up on the token server.
"""

from __future__ import annotations

import logging

from .config import load_env

load_env()

import asyncio  # noqa: E402
import inspect  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from collections.abc import Awaitable, Callable, Mapping  # noqa: E402
from typing import Any, Literal  # noqa: E402

from livekit.agents import AgentServer, AgentSession, JobContext, RunContext, cli, function_tool, llm  # noqa: E402

from . import AGENT_NAME  # noqa: E402
from .buddy import (  # noqa: E402
    GREETING,
    PERSONA,
    REPLY_MODEL,
    ReadingTracker,
    TopicGate,
    _words,
    addresses_buddy,
    build_graph,
    default_topic_gate,
    make_context_note,
    make_gate,
    make_parallel_topic_gate,
    realtime_instructions,
)
from .content import BOOK_KEY, PAGE_KEY, refresh_remote  # noqa: E402
from .modes import parse_job_metadata, resolve_mode  # noqa: E402
from .speech import speech_setup  # noqa: E402
from .stars import KINDS, STAR_TOPIC, StarAward, StarKeeper, StarRules, report_star, star_payload  # noqa: E402
from .usage import UsageMeter, report_usage  # noqa: E402
from .voice import ContextStore, TurnDecision, UserTurn, VoicePreset, preset_from_env  # noqa: E402
from .voice.realtime import RealtimeConfig, start_realtime_session  # noqa: E402
from .voice.session import attach_room_context, start_graph_session  # noqa: E402

log = logging.getLogger("little_explorer.agent")

ENDED_ATTRIBUTE = "milo.ended"
DEFAULT_IDLE_TIMEOUT_S = 20.0
LIMIT_GOODBYE = "I'm getting sleepy, so it's time for me to rest. Let's explore more tomorrow. Bye-bye!"


def idle_timeout(env: Mapping[str, str] | None = None) -> float | None:
    """Seconds of silence from both sides before Milo turns off; None when ``PIP_IDLE_TIMEOUT_S=0``."""
    env = os.environ if env is None else env
    raw = (env.get("PIP_IDLE_TIMEOUT_S") or "").strip()
    seconds = float(raw) if raw else DEFAULT_IDLE_TIMEOUT_S
    return seconds if seconds > 0 else None


def agent_preset(env: Mapping[str, str] | None = None) -> VoicePreset:
    return preset_from_env().with_overrides(user_away_timeout=idle_timeout(env))


class SessionEnder:
    """Ends a session once, for ``idle`` (no voice for a while) or ``limit`` (today's minutes are
    used up): optionally says goodbye, tells the app why via the ``milo.ended`` attribute, then
    shuts the job down (the shutdown callback still reports usage)."""

    def __init__(
        self,
        ctx: Any,
        speak: Callable[[str], Awaitable[None]] | None = None,
        *,
        on_end: Callable[[str], None] | None = None,
        goodbye_timeout: float = 12.0,
    ) -> None:
        self.ctx = ctx
        self.speak = speak
        self.on_end = on_end
        self.goodbye_timeout = goodbye_timeout
        self.reason: str | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def watch(self, session: Any, remaining_s: float | None = None) -> None:
        def on_user_state(ev: Any) -> None:
            if getattr(ev, "new_state", None) == "away":
                self._spawn("idle")

        session.on("user_state_changed", on_user_state)
        if remaining_s is not None:
            loop = asyncio.get_running_loop()
            self._timer = loop.call_later(max(remaining_s, 0.0), self._spawn, "limit", LIMIT_GOODBYE)

    def cancel(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _spawn(self, reason: str, goodbye: str | None = None) -> None:
        task = asyncio.ensure_future(self.end(reason, goodbye))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def end(self, reason: str, goodbye: str | None = None) -> None:
        if self.reason is not None:
            return
        self.reason = reason
        self.cancel()
        log.info("ending the session: %s", reason)
        if self.on_end:
            self.on_end(reason)
        if goodbye and self.speak:
            try:
                await asyncio.wait_for(self.speak(goodbye), self.goodbye_timeout)
            except Exception:  # noqa: BLE001 - leave anyway
                log.warning("goodbye did not finish", exc_info=True)
        try:
            await self.ctx.room.local_participant.set_attributes({ENDED_ATTRIBUTE: reason})
        except Exception:  # noqa: BLE001
            log.warning("could not tell the app why Milo left", exc_info=True)
        self.ctx.shutdown(reason=f"milo {reason}")


def speaker(session: Any, mode: str) -> Callable[[str], Awaitable[None]]:
    """Say ``text`` word for word in either engine and wait until it has played."""

    async def speak(text: str) -> None:
        if mode == "realtime":
            handle = session.generate_reply(instructions=f'Say exactly: "{text}"')
        else:
            handle = session.say(text, allow_interruptions=False)
        await handle.wait_for_playout()

    return speak

_graph = None


def _shared_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _log_decision(turn: UserTurn, decision: TurnDecision) -> None:
    # Transcripts only go to the local log for debugging; no audio is recorded or stored.
    log.info("turn %s page=%s: %r", decision.name, turn.context.get("page"), turn.text[:120])


@function_tool
async def stay_quiet() -> llm.ToolResult:
    """Say nothing this turn: he is still reading the page aloud or still thinking, or the speech
    was not meant for you (grown-ups talking, TV, noise)."""
    return llm.ToolResult("(Milo stayed quiet)", reply_required=False)


QUICK_CLAIM_S = 0.6


def _star_note(result: StarAward | str) -> str:
    if isinstance(result, StarAward):
        return f"(Star given: {result.stars}.)"
    return "(No star this time. Don't mention stars until his next question.)"


async def give_star_result(
    keeper: StarKeeper,
    kind: str,
    reason: str,
    on_award: Callable[[StarAward], None],
    spoke: Callable[[], Awaitable[bool]],
    *,
    spawn: Callable[[Any], asyncio.Future[Any]] = asyncio.ensure_future,
    quick_s: float = QUICK_CLAIM_S,
) -> llm.ToolResult:
    """What ``give_star`` does, without ever holding up Milo's voice.

    The claim runs as its own task (so a barge-in that cancels the tool can't lose a real award) and
    hands an award on the moment it is checked. If Milo spoke in the same response, nothing is
    waiting on the tool. If the model only made the call, he is waiting for an answer: give the
    check ``quick_s`` at most, then let the model answer (a late star still reaches the app).
    """

    async def settle() -> StarAward | str:
        result = await keeper.claim(kind if kind in KINDS else "question", reason)
        if isinstance(result, StarAward):
            on_award(result)
        else:
            log.info("no star: %s", result)
        return result

    started = time.monotonic()
    claim = spawn(settle())
    if await spoke():
        result = await asyncio.shield(claim)
        log.info("give_star after speaking, checked in %.2fs", time.monotonic() - started)
        return llm.ToolResult(_star_note(result), reply_required=False)
    done, _ = await asyncio.wait({claim}, timeout=quick_s)
    log.info("give_star before speaking, %s after %.2fs", "checked" if done else "still checking", time.monotonic() - started)
    if done:
        note = _star_note(claim.result())
    else:
        note = "(Don't mention stars this time.)"
    return llm.ToolResult(f"{note} Now answer his question out loud.", reply_required=True)


async def _spoke(context: RunContext) -> bool:
    """Whether this step's spoken answer was played (or cut off by him talking)."""
    try:
        await context.wait_for_playout()
    except Exception:  # noqa: BLE001 - be safe: never make Milo talk twice
        return True
    handle = context.speech_handle
    if handle.interrupted:
        return True
    return any(
        getattr(item, "role", None) == "assistant" and (getattr(item, "text_content", "") or "").strip()
        for item in handle.chat_items
    )


class StarGame:
    """Plugs a :class:`StarKeeper` into a session: it hears every guarded turn, gives the realtime
    model its ``give_star`` tool, and sends each award to the app and the token server."""

    def __init__(
        self,
        ctx: Any,
        keeper: StarKeeper,
        *,
        report: Callable[[StarAward], Awaitable[bool]] = report_star,
        enabled: bool = True,
    ) -> None:
        self.ctx = ctx
        self.keeper = keeper
        self.report = report
        self.refresh: Callable[[], None] | None = None
        self.enabled = enabled
        self._tasks: set[asyncio.Task[Any]] = set()

    def _spawn(self, coro: Any) -> asyncio.Future[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def on_decision(self, turn: UserTurn, decision: TurnDecision) -> None:
        _log_decision(turn, decision)
        self.keeper.heard(turn, decision)

    def pipeline_decision(self, turn: UserTurn, decision: TurnDecision) -> None:
        """The pipeline has no tool calls: a question it answers earns a star directly."""
        self.on_decision(turn, decision)
        if self.enabled and decision is TurnDecision.RESPOND:
            result = self.keeper.judge("question")
            if isinstance(result, StarAward):
                self.award(result)

    def watch(self, session: Any) -> None:
        def on_user_state(ev: Any) -> None:
            if getattr(ev, "new_state", None) == "speaking":
                self.keeper.speech_started()

        session.on("user_state_changed", on_user_state)

    def award(self, award: StarAward) -> None:
        log.info("star +%d on %s/%s (%s, %d words)", award.stars, award.book, award.page, award.kind, award.words)
        self._spawn(self._publish(award))
        self._spawn(self.report(award))
        if self.refresh is not None and self.keeper.stars_left({BOOK_KEY: award.book, PAGE_KEY: award.page}) == 0:
            self.refresh()

    async def _publish(self, award: StarAward) -> None:
        try:
            await self.ctx.room.local_participant.publish_data(
                star_payload(award, self.keeper.total()), reliable=True, topic=STAR_TOPIC
            )
        except Exception:  # noqa: BLE001
            log.warning("could not send the star to the app", exc_info=True)

    async def drain(self, timeout: float = 3.0) -> None:
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout)

    def tool(self) -> Any:
        game = self

        @function_tool
        async def give_star(
            context: RunContext, kind: Literal["question", "follow_up"] = "question", reason: str = ""
        ) -> llm.ToolResult:
            """Give him a star for a real, curious question about the book, the page, the city or
            travel. Start your spoken answer first and call this in the same response, never alone.

            Args:
                kind: "follow_up" when he asks more about the thing he just asked about, else "question".
                reason: a few words on what he asked about, like "why the tower is painted". No names.
            """
            return await give_star_result(
                game.keeper, kind, reason, game.award, lambda: _spoke(context), spawn=game._spawn
            )

        return give_star


def realtime_config(env: dict[str, str] | None = None) -> RealtimeConfig:
    env = dict(os.environ) if env is None else env
    return RealtimeConfig(
        model=env.get("PIP_REALTIME_MODEL", "gpt-realtime"),
        voice=env.get("PIP_OPENAI_VOICE", "coral"),
        eagerness=env.get("PIP_REALTIME_EAGERNESS", "low"),  # type: ignore[arg-type]
    )


def make_realtime_guard(tracker: ReadingTracker, topic_gate: TopicGate | None) -> TopicGate:
    """Post-hoc check of a realtime turn: cut the reply if he was reading or it was not for Milo.

    Very short turns ("yes", "the boat!") are left to the model; they are usually answers to Milo.
    """

    async def guard(turn: UserTurn) -> TurnDecision:
        reading = tracker.check(turn)
        if reading is not None:
            return reading
        if len(_words(turn.text)) < 3 or addresses_buddy(turn.text) or topic_gate is None:
            return TurnDecision.RESPOND
        result = topic_gate(turn)
        if inspect.isawaitable(result):
            result = await result
        return TurnDecision.IGNORE if result is TurnDecision.LISTEN else result

    return guard


async def _pipeline(ctx: JobContext, game: StarGame) -> AgentSession:
    tracker = ReadingTracker()
    preset, speech = speech_setup(agent_preset())
    return await start_graph_session(
        ctx,
        _shared_graph(),
        preset=preset,
        # Reading and noise are decided instantly; the LLM topic check runs alongside the reply.
        gate=make_gate(tracker, None),
        parallel_gate=make_parallel_topic_gate(tracker, default_topic_gate()),
        instructions=PERSONA,
        context_message=make_context_note(tracker),
        on_decision=game.pipeline_decision,
        greeting=GREETING,
        **speech,
    )


async def _realtime(ctx: JobContext, game: StarGame) -> AgentSession:
    store = ContextStore()

    def instructions(context: Mapping[str, Any]) -> str:
        stars = game.keeper.status_line(context) if game.enabled else None
        return realtime_instructions(context, stars=stars)

    session = await start_realtime_session(
        ctx,
        config=realtime_config(),
        preset=agent_preset(),
        instructions=instructions,
        tools=[stay_quiet, game.tool()] if game.enabled else [stay_quiet],
        guard=make_realtime_guard(ReadingTracker(), default_topic_gate()),
        on_decision=game.on_decision,
        greeting=GREETING,
        context_store=store,
    )

    def refresh() -> None:
        game._spawn(session.current_agent.update_instructions(instructions(store.snapshot())))

    game.refresh = refresh
    return session


def session_model(mode: str, env: dict[str, str] | None = None) -> str:
    """The main model a session bills for: the realtime model, or the pipeline's reply model."""
    if mode == "realtime":
        return realtime_config(env).model
    return REPLY_MODEL.split(":", 1)[-1].split("@", 1)[0]


def remaining_seconds(meta: Mapping[str, object]) -> float | None:
    value = meta.get("remaining_s")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


async def entrypoint(ctx: JobContext) -> None:
    mode = resolve_mode(ctx.job.metadata)
    meta = parse_job_metadata(ctx.job.metadata)
    device_id = str(meta.get("device_id") or "")
    remaining_s = remaining_seconds(meta)
    log.info("Milo mode: %s, device %s, %s s left today", mode, device_id or "?", remaining_s)
    meter = UsageMeter(mode=mode, model=session_model(mode), room=ctx.job.room.name, device_id=device_id)

    async def write_usage(reason: str = "") -> None:
        meter.finish()
        await report_usage(meter.report(reason))

    # Runs on every job end, including the child leaving abruptly or the worker shutting down.
    ctx.add_shutdown_callback(write_usage)
    await refresh_remote()
    game = StarGame(
        ctx,
        StarKeeper(device_id=device_id, room=ctx.job.room.name, rules=StarRules.from_env()),
        enabled=os.environ.get("PIP_STARS", "1").strip() != "0",
    )
    ctx.add_shutdown_callback(game.drain)
    session = await (_realtime if mode == "realtime" else _pipeline)(ctx, game)
    game.watch(session)
    meter.bind(session)

    def note_reason(reason: str) -> None:
        meter.ended_reason = reason

    ender = SessionEnder(ctx, speaker(session, mode), on_end=note_reason)
    ender.watch(session, remaining_s)
    ctx.add_shutdown_callback(lambda: _cancel(ender))
    pages = ContextStore()
    pages.subscribe(lambda _changed: meter.on_context(pages.snapshot()))
    attach_room_context(pages, ctx.room)


async def _cancel(ender: SessionEnder) -> None:
    ender.cancel()


def create_server() -> AgentServer:
    # Named agents are only dispatched explicitly (the app's token asks for this one). LiveKit takes
    # the name from LIVEKIT_AGENT_NAME; passing agent_name to rtc_session is deprecated.
    os.environ.setdefault("LIVEKIT_AGENT_NAME", AGENT_NAME)
    server = AgentServer()
    server.rtc_session(entrypoint)
    return server


def main() -> None:
    cli.run_app(create_server())


if __name__ == "__main__":
    main()
