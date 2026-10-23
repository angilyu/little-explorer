"""Milo talks with children: no session may be recorded by LiveKit Cloud's Agent observability.

``AgentSession.start(record=False)`` turns off the audio, transcript, trace and log uploads for the
session even when the project's observability setting is on. Fake LiveKit objects: no network, no OpenAI.
"""

import inspect

from livekit.agents import AgentSession
from livekit.rtc import EventEmitter

from little_explorer_agent.voice import realtime as rt_mod
from little_explorer_agent.voice import session as session_mod
from little_explorer_agent.voice.preset import PATIENT


class FakeSession(EventEmitter):
    started: list[dict] = []

    def __init__(self, **kwargs):
        super().__init__()

    async def start(self, **kwargs):
        FakeSession.started.append(kwargs)

    def say(self, text):
        pass


class Ctx:
    room = object()

    async def connect(self):
        pass


def test_the_sdk_still_takes_record():
    assert "record" in inspect.signature(AgentSession.start).parameters


async def test_pipeline_sessions_are_not_recorded(monkeypatch):
    from livekit.plugins import langchain

    FakeSession.started = []
    monkeypatch.setattr(session_mod, "AgentSession", FakeSession)
    monkeypatch.setattr(session_mod, "GraphAgent", lambda **kwargs: object())
    monkeypatch.setattr(session_mod, "attach_room_context", lambda store, room: None)
    monkeypatch.setattr(session_mod, "_turn_handling", lambda preset: None)
    monkeypatch.setattr(session_mod, "_room_options", lambda preset: None)
    monkeypatch.setattr(langchain, "LLMAdapter", lambda **kwargs: None)

    await session_mod.start_graph_session(
        Ctx(), object(), preset=PATIENT, gate=None, instructions="", stt=object(), tts=object()
    )
    assert [s.get("record") for s in FakeSession.started] == [False]


async def test_realtime_sessions_are_not_recorded(monkeypatch):
    FakeSession.started = []
    monkeypatch.setattr(rt_mod, "AgentSession", FakeSession)
    monkeypatch.setattr(rt_mod, "Agent", lambda instructions, tools: object())
    monkeypatch.setattr(rt_mod, "realtime_model", lambda config: None)
    monkeypatch.setattr(rt_mod, "session_kwargs", lambda config: {})
    monkeypatch.setattr(rt_mod, "attach_room_context", lambda store, room: None)
    monkeypatch.setattr(rt_mod, "_room_options", lambda preset: None)

    await rt_mod.start_realtime_session(
        Ctx(), config=rt_mod.RealtimeConfig(turns="client"), preset=PATIENT, instructions=lambda ctx: ""
    )
    assert [s.get("record") for s in FakeSession.started] == [False]
