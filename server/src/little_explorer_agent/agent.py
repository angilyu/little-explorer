"""LiveKit worker for Pip. Run: ``uv run python -m little_explorer_agent.agent dev``.

``PIP_MODE=pipeline`` (default) or ``PIP_MODE=realtime`` picks the voice engine; see :mod:`.modes`.
"""

from __future__ import annotations

import logging

from .config import load_env

load_env()

import inspect  # noqa: E402
import os  # noqa: E402

from livekit.agents import AgentServer, AgentSession, JobContext, cli, function_tool, llm  # noqa: E402

from . import AGENT_NAME  # noqa: E402
from .buddy import (  # noqa: E402
    GREETING,
    PERSONA,
    REPLY_MODEL,
    ReadingTracker,
    TopicGate,
    _words,
    addresses_pip,
    build_graph,
    default_topic_gate,
    make_context_note,
    make_gate,
    make_parallel_topic_gate,
    realtime_instructions,
)
from .modes import resolve_mode  # noqa: E402
from .speech import speech_setup  # noqa: E402
from .usage import UsageMeter  # noqa: E402
from .voice import ContextStore, TurnDecision, UserTurn, preset_from_env  # noqa: E402
from .voice.realtime import RealtimeConfig, start_realtime_session  # noqa: E402
from .voice.session import attach_room_context, start_graph_session  # noqa: E402

log = logging.getLogger("little_explorer.agent")

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
    return llm.ToolResult("(Pip stayed quiet)", reply_required=False)


def realtime_config(env: dict[str, str] | None = None) -> RealtimeConfig:
    env = dict(os.environ) if env is None else env
    return RealtimeConfig(
        model=env.get("PIP_REALTIME_MODEL", "gpt-realtime"),
        voice=env.get("PIP_OPENAI_VOICE", "coral"),
        eagerness=env.get("PIP_REALTIME_EAGERNESS", "low"),  # type: ignore[arg-type]
    )


def make_realtime_guard(tracker: ReadingTracker, topic_gate: TopicGate | None) -> TopicGate:
    """Post-hoc check of a realtime turn: cut the reply if he was reading or it was not for Pip.

    Very short turns ("yes", "the boat!") are left to the model; they are usually answers to Pip.
    """

    async def guard(turn: UserTurn) -> TurnDecision:
        reading = tracker.check(turn)
        if reading is not None:
            return reading
        if len(_words(turn.text)) < 3 or addresses_pip(turn.text) or topic_gate is None:
            return TurnDecision.RESPOND
        result = topic_gate(turn)
        if inspect.isawaitable(result):
            result = await result
        return TurnDecision.IGNORE if result is TurnDecision.LISTEN else result

    return guard


async def _pipeline(ctx: JobContext) -> AgentSession:
    tracker = ReadingTracker()
    preset, speech = speech_setup(preset_from_env())
    return await start_graph_session(
        ctx,
        _shared_graph(),
        preset=preset,
        # Reading and noise are decided instantly; the LLM topic check runs alongside the reply.
        gate=make_gate(tracker, None),
        parallel_gate=make_parallel_topic_gate(tracker, default_topic_gate()),
        instructions=PERSONA,
        context_message=make_context_note(tracker),
        on_decision=_log_decision,
        greeting=GREETING,
        **speech,
    )


async def _realtime(ctx: JobContext) -> AgentSession:
    return await start_realtime_session(
        ctx,
        config=realtime_config(),
        preset=preset_from_env(),
        instructions=realtime_instructions,
        tools=[stay_quiet],
        guard=make_realtime_guard(ReadingTracker(), default_topic_gate()),
        on_decision=_log_decision,
        greeting=GREETING,
    )


def session_model(mode: str, env: dict[str, str] | None = None) -> str:
    """The main model a session bills for: the realtime model, or the pipeline's reply model."""
    if mode == "realtime":
        return realtime_config(env).model
    return REPLY_MODEL.split(":", 1)[-1].split("@", 1)[0]


async def entrypoint(ctx: JobContext) -> None:
    mode = resolve_mode(ctx.job.metadata)
    log.info("Pip mode: %s", mode)
    meter = UsageMeter(mode=mode, model=session_model(mode))

    async def write_usage(_reason: str = "") -> None:
        meter.finish()

    # Runs on every job end, including the child leaving abruptly or the worker shutting down.
    ctx.add_shutdown_callback(write_usage)
    session = await (_realtime if mode == "realtime" else _pipeline)(ctx)
    meter.bind(session)
    pages = ContextStore()
    pages.subscribe(lambda _changed: meter.on_context(pages.snapshot()))
    attach_room_context(pages, ctx.room)


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
