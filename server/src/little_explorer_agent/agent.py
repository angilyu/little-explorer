"""LiveKit worker for Milo. Run: ``uv run python -m little_explorer_agent.agent dev``.

``PIP_MODE=pipeline`` (default) or ``PIP_MODE=realtime`` picks the voice engine; see :mod:`.modes`.

Milo turns himself off after ``PIP_IDLE_TIMEOUT_S`` (20) seconds in which neither he nor the child
spoke (0 disables), and says goodbye when the device's minutes for today run out (the token server
passes ``remaining_s``). Either way he sets his ``milo.ended`` attribute to ``idle`` or ``limit`` so
the app can switch its toggle off with the right message, then leaves the room. Each session's
length and cost is reported to the token server for per-device usage (see :mod:`.usage`).

Answering Milo earns stars (see :mod:`.stars`): when the guard lets the child's answer to Milo's
question through and Milo replies, :class:`StarKeeper` checks it against what was heard, and
:class:`StarGame` sends each award to the app (``milo.star`` data topic) and backs it up on the token
server.
"""

from __future__ import annotations

import logging

from .config import load_env

load_env()

import asyncio  # noqa: E402
import inspect  # noqa: E402
import os  # noqa: E402
from collections.abc import Awaitable, Callable, Mapping  # noqa: E402
from dataclasses import replace  # noqa: E402
from typing import Any  # noqa: E402

from livekit.agents import AgentServer, AgentSession, JobContext, cli, function_tool, llm  # noqa: E402

from . import agent_name  # noqa: E402
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
from .modes import app_plays_intro, parse_job_metadata, parse_tz, resolve_mode  # noqa: E402
from .speech import speech_setup  # noqa: E402
from .stars import (  # noqa: E402
    STAR_TOPIC,
    StarAward,
    StarKeeper,
    StarRules,
    device_clock,
    fetch_page_stars,
    report_star,
    star_payload,
)
from .usage import UsageMeter, report_usage  # noqa: E402
from .voice import ContextStore, TurnDecision, UserTurn, VoicePreset, preset_from_env  # noqa: E402
from .voice.turns import is_noise  # noqa: E402
from .voice.realtime import RealtimeConfig, start_realtime_session  # noqa: E402
from .voice.session import attach_room_context, start_graph_session  # noqa: E402
from .voice.timing import TurnTimer  # noqa: E402

log = logging.getLogger("little_explorer.agent")

ENDED_ATTRIBUTE = "milo.ended"
DEFAULT_IDLE_TIMEOUT_S = 20.0
# How long a job waits for the app to join before it gives up (a cold start takes ~5 s).
PARTICIPANT_WAIT_S = 20.0
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


class StarGame:
    """Plugs a :class:`StarKeeper` into a session: it hears every guarded turn, awards a star when a
    real answer to Milo's question gets a reply from Milo, and sends each award to the app and the
    token server.

    There is no model tool: on gpt-realtime a tool call makes the model answer in a second response,
    which cost every star turn about half a second. Milo's judgement is the relevance guard
    (the same check that decides whether he answers at all), and a star counts only once he starts
    replying to that turn, so a turn he chose to stay quiet on earns nothing."""

    def __init__(
        self,
        ctx: Any,
        keeper: StarKeeper,
        *,
        report: Callable[[StarAward], Awaitable[bool]] = report_star,
        earlier: Callable[..., Awaitable[dict[tuple[str, str, str], int] | None]] = fetch_page_stars,
        enabled: bool = True,
        answer_wait_s: float = 6.0,
    ) -> None:
        self.ctx = ctx
        self.keeper = keeper
        self.report = report
        self.earlier = earlier
        self.refresh: Callable[[], None] | None = None
        self.enabled = enabled
        self.answer_wait_s = answer_wait_s
        self._answered_at = -1.0
        self._answer = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()

    def _spawn(self, coro: Any) -> asyncio.Future[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def load_earlier(self) -> None:
        """Read the stars he already has on each page (off the voice path), so the 5-per-page daily
        limit counts earlier sessions that day too. Until it arrives (or if it can't be read) only this session's
        stars count here; the token server still refuses a page's sixth star."""
        if self.enabled and (self.keeper.device_id or self.keeper.account_id):
            self._spawn(self._load_earlier())

    async def _load_earlier(self) -> None:
        counts = await self.earlier(self.keeper.device_id, account_id=self.keeper.account_id)
        if counts is None:
            return
        self.keeper.earlier = counts
        log.info("earlier stars: %d on %d pages", sum(counts.values()), len(counts))
        if self.refresh is not None:
            self.refresh()

    def on_decision(self, turn: UserTurn, decision: TurnDecision) -> None:
        _log_decision(turn, decision)
        self.keeper.heard(turn, decision)

    def pipeline_decision(self, turn: UserTurn, decision: TurnDecision) -> None:
        """The pipeline answers every turn its gate lets through, so the star is judged at once."""
        self.on_decision(turn, decision)
        if self.enabled and decision is TurnDecision.RESPOND:
            self._settle(self.keeper.judge())

    def realtime_decision(self, turn: UserTurn, decision: TurnDecision) -> None:
        """The realtime model answers on its own, so wait (off the voice path) until it does."""
        self.on_decision(turn, decision)
        if self.enabled and decision is TurnDecision.RESPOND:
            self._spawn(self._award_when_answered(self.keeper.speech_at))

    def milo_started_speaking(self) -> None:
        self._answered_at = self.keeper.clock()
        self._answer.set()
        self._answer = asyncio.Event()

    async def _award_when_answered(self, asked_at: float) -> None:
        heard = self.keeper.latest()
        deadline = self.keeper.clock() + self.answer_wait_s
        while self._answered_at <= asked_at:
            left = deadline - self.keeper.clock()
            if left <= 0:
                log.info("no star: Milo didn't answer")
                return
            try:
                await asyncio.wait_for(self._answer.wait(), left)
            except asyncio.TimeoutError:
                continue
        if self.keeper.latest() is not heard:
            return  # he spoke again before Milo replied; the newer turn is the one to judge
        self._settle(self.keeper.judge())

    def _settle(self, result: StarAward | str) -> None:
        if isinstance(result, StarAward):
            self.award(result)
        else:
            log.info("no star: %s", result)

    def watch(self, session: Any) -> None:
        def on_user_state(ev: Any) -> None:
            if getattr(ev, "new_state", None) == "speaking":
                self.keeper.speech_started()

        def on_agent_state(ev: Any) -> None:
            if getattr(ev, "new_state", None) == "speaking":
                self.milo_started_speaking()

        session.on("user_state_changed", on_user_state)
        session.on("agent_state_changed", on_agent_state)

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


def realtime_config(env: Mapping[str, str] | None = None) -> RealtimeConfig:
    """Realtime settings, overridable with ``PIP_REALTIME_*`` (see the README)."""
    env = os.environ if env is None else env
    base = RealtimeConfig()

    def get(name: str) -> str:
        return (env.get(name) or "").strip()

    changes: dict[str, Any] = {}
    for name, field_name in (
        ("PIP_REALTIME_MODEL", "model"),
        ("PIP_OPENAI_VOICE", "voice"),
        ("PIP_REALTIME_TURNS", "turns"),
        ("PIP_REALTIME_EAGERNESS", "eagerness"),
        ("PIP_REALTIME_INTERRUPTION_MODE", "interruption_mode"),
        ("PIP_REALTIME_END_OF_TURN", "end_of_turn"),
        ("PIP_REALTIME_BARGE_IN", "barge_in"),
    ):
        if value := get(name):
            changes[field_name] = value.lower() if field_name not in ("model", "voice") else value
    if stt := get("PIP_REALTIME_STT_MODEL"):
        changes["stt_model"] = None if stt.lower() == "none" else stt
    for name, field_name, cast in (
        ("PIP_REALTIME_MIN_ENDPOINTING_DELAY", "min_endpointing_delay", float),
        ("PIP_REALTIME_MAX_ENDPOINTING_DELAY", "max_endpointing_delay", float),
        ("PIP_REALTIME_MIN_INTERRUPTION_DURATION", "min_interruption_duration", float),
        ("PIP_REALTIME_MIN_INTERRUPTION_WORDS", "min_interruption_words", int),
        ("PIP_REALTIME_COMMIT_AFTER", "commit_after_s", float),
        ("PIP_REALTIME_READING_COMMIT_AFTER", "reading_commit_after_s", float),
    ):
        if value := get(name):
            changes[field_name] = cast(value)
    if changes.get("turns", base.turns) not in ("client", "server"):
        raise ValueError(f"PIP_REALTIME_TURNS must be 'client' or 'server', not {changes['turns']!r}")
    return replace(base, **changes)


ANSWER_MAX_WORDS = 12


def _milo_just_asked(turn: UserTurn) -> bool:
    last = turn.history[-1] if turn.history else None
    # Anywhere in the reply: an invitation to say more ends with a sentence start, not the "?".
    return last is not None and last[0] == "assistant" and "?" in last[1]


def make_realtime_guard(tracker: ReadingTracker, topic_gate: TopicGate | None) -> TopicGate:
    """Post-hoc check of a realtime turn: cut the reply if he was reading or it was not for Milo.

    Very short turns ("yes", "the boat!") are left to the model; they are usually answers to Milo.
    So is a short reply right after Milo asked something: the topic check often mistook his answers
    to Milo's own question for chatter and cut Milo off. A transcript with no English letters is a
    noise and is ignored.
    """

    async def guard(turn: UserTurn) -> TurnDecision:
        if is_noise(turn.text):
            return TurnDecision.IGNORE
        reading = tracker.check(turn)
        if reading is not None:
            return reading
        words = len(_words(turn.text))
        if words < 3 or addresses_buddy(turn.text) or topic_gate is None:
            return TurnDecision.RESPOND
        if words <= ANSWER_MAX_WORDS and _milo_just_asked(turn):
            return TurnDecision.RESPOND
        result = topic_gate(turn)
        if inspect.isawaitable(result):
            result = await result
        return TurnDecision.IGNORE if result is TurnDecision.LISTEN else result

    return guard


async def _pipeline(ctx: JobContext, game: StarGame, timer: TurnTimer, greeting: str | None) -> AgentSession:
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
        greeting=greeting,
        **speech,
    )


async def _realtime(ctx: JobContext, game: StarGame, timer: TurnTimer, greeting: str | None) -> AgentSession:
    store = ContextStore()
    config = realtime_config()
    if config.turns == "server" and config.barge_in == "vad":
        timer.lead_s = config.min_interruption_duration

    def instructions(context: Mapping[str, Any]) -> str:
        stars = game.keeper.status_line(context) if game.enabled else None
        return realtime_instructions(context, stars=stars, min_words=game.keeper.rules.min_words)

    session = await start_realtime_session(
        ctx,
        config=config,
        preset=agent_preset(),
        instructions=instructions,
        tools=[stay_quiet],
        guard=make_realtime_guard(ReadingTracker(), default_topic_gate()),
        on_decision=game.realtime_decision,
        greeting=greeting,
        context_store=store,
        on_interrupt=timer.note_interrupt,
        on_commit=timer.committed,
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


async def participant_joined(ctx: Any, timeout: float = PARTICIPANT_WAIT_S) -> bool:
    """Wait for the child's app to be in the room. A tap that was cancelled while the agent was
    still starting leaves an empty room; greeting it would only burn the device's minutes."""
    await ctx.connect()
    try:
        await asyncio.wait_for(ctx.wait_for_participant(), timeout)
    except asyncio.TimeoutError:
        log.info("nobody joined within %.0fs; ending without a greeting", timeout)
        return False
    return True


async def entrypoint(ctx: JobContext) -> None:
    mode = resolve_mode(ctx.job.metadata)
    meta = parse_job_metadata(ctx.job.metadata)
    device_id = str(meta.get("device_id") or "")
    # The signed-in account whose minutes and stars these are (old apps: none; the server falls back
    # to the device's link).
    account_id = str(meta.get("account_id") or "")
    remaining_s = remaining_seconds(meta)
    log.info("Milo mode: %s, device %s, account %s, %s s left today", mode, device_id or "?", account_id or "?", remaining_s)
    meter = UsageMeter(
        mode=mode, model=session_model(mode), room=ctx.job.room.name, device_id=device_id, account_id=account_id
    )

    async def write_usage(reason: str = "") -> None:
        meter.finish()
        await report_usage(meter.report(reason))

    # Runs on every job end, including the child leaving abruptly or the worker shutting down.
    ctx.add_shutdown_callback(write_usage)
    await refresh_remote()
    game = StarGame(
        ctx,
        StarKeeper(
            device_id=device_id,
            account_id=account_id,
            room=ctx.job.room.name,
            rules=StarRules.from_env(),
            wall=device_clock(parse_tz(meta.get("tz"))),
        ),
        enabled=os.environ.get("PIP_STARS", "1").strip() != "0",
    )
    ctx.add_shutdown_callback(game.drain)
    game.load_earlier()
    if not await participant_joined(ctx):
        meter.ended_reason = "nobody joined"
        ctx.shutdown(reason="nobody joined")
        return
    timer = TurnTimer()
    # New apps play the recorded intro on tap; only older ones still need the spoken greeting.
    greeting = None if app_plays_intro(meta) else GREETING
    log.info("intro: %s", "played by the app" if greeting is None else "spoken by Milo")
    session = await (_realtime if mode == "realtime" else _pipeline)(ctx, game, timer, greeting)
    game.watch(session)
    meter.bind(session)
    timer.attach(session)
    ctx.add_shutdown_callback(timer.log_summary)

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


# Imported lazily by a session; loading them in the job's event loop stalled audio for ~0.5 s.
PREWARM_MODULES = (
    "difflib",
    "langchain_core.messages",
    "langchain_core.outputs",
    "langchain_core.runnables",
    "langchain_openai",
    "langsmith.schemas",
    "openai.types.realtime",
    "livekit.plugins.openai.realtime",
)


def prewarm(_proc: Any = None) -> None:
    """Import the heavy, lazily loaded modules while the idle process waits for a job."""
    import importlib

    for name in PREWARM_MODULES:
        try:
            importlib.import_module(name)
        except Exception:  # an optional extra may be missing; the session imports what it needs
            log.debug("prewarm: could not import %s", name)


def create_server() -> AgentServer:
    # Named agents are only dispatched explicitly (the app's token asks for this one). LiveKit takes
    # the name from LIVEKIT_AGENT_NAME; passing agent_name to rtc_session is deprecated.
    os.environ["LIVEKIT_AGENT_NAME"] = agent_name()
    server = AgentServer(setup_fnc=prewarm)
    server.rtc_session(entrypoint)
    return server


def main() -> None:
    cli.run_app(create_server())


if __name__ == "__main__":
    main()
