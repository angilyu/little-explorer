"""LiveKit worker for Pip. Run: ``uv run python -m little_explorer_agent.agent dev``."""

from __future__ import annotations

import logging

from dotenv import load_dotenv

load_dotenv()

import os  # noqa: E402

from livekit.agents import AgentServer, JobContext, cli  # noqa: E402

from . import AGENT_NAME  # noqa: E402
from .speech import speech_setup  # noqa: E402
from .buddy import GREETING, PERSONA, ReadingTracker, build_graph, default_topic_gate, make_context_note, make_gate  # noqa: E402
from .voice import TurnDecision, UserTurn, preset_from_env  # noqa: E402
from .voice.session import start_graph_session  # noqa: E402

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


async def entrypoint(ctx: JobContext) -> None:
    tracker = ReadingTracker()
    preset, speech = speech_setup(preset_from_env())
    await start_graph_session(
        ctx,
        _shared_graph(),
        preset=preset,
        gate=make_gate(tracker, default_topic_gate()),
        instructions=PERSONA,
        context_message=make_context_note(tracker),
        on_decision=_log_decision,
        greeting=GREETING,
        **speech,
    )


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
