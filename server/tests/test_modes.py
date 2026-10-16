"""Pip's two voice engines: the mode switch and the pieces that make each one feel conversational."""

import asyncio
import base64
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import agent as agent_mod
from little_explorer_agent.buddy import (
    PERSONA,
    REALTIME_RULES,
    ReadingTracker,
    make_parallel_topic_gate,
    realtime_instructions,
)
from little_explorer_agent.content import load_books
from little_explorer_agent.modes import DEFAULT_MODE, mode_metadata, resolve_mode
from little_explorer_agent.token_server import create_app
from little_explorer_agent.voice import ContextStore, TurnDecision, UserTurn
from little_explorer_agent.voice.realtime import ReplyGuard

PARIS = load_books()["paris"]
PAGE = PARIS.pages[0]
CTX = {"book": "paris", "page": PAGE.id, "page_number": "1", "city": "Paris", "page_text": PAGE.text}


def turn(text, context=CTX):
    return UserTurn(text=text, context=context, history=[])


# --- mode switch ---------------------------------------------------------------------------


def test_mode_defaults_to_pipeline():
    assert DEFAULT_MODE == "pipeline"
    assert resolve_mode(None, env={}) == "pipeline"


def test_env_picks_mode():
    assert resolve_mode(None, env={"PIP_MODE": "realtime"}) == "realtime"
    assert resolve_mode(None, env={"PIP_MODE": " Realtime "}) == "realtime"
    assert resolve_mode(None, env={"PIP_MODE": "warp-speed"}) == "pipeline"


def test_dispatch_metadata_beats_env():
    assert resolve_mode(mode_metadata("realtime"), env={"PIP_MODE": "pipeline"}) == "realtime"
    assert resolve_mode(mode_metadata("pipeline"), env={"PIP_MODE": "realtime"}) == "pipeline"
    # Junk metadata falls back to the environment.
    assert resolve_mode("not json", env={"PIP_MODE": "realtime"}) == "realtime"
    assert resolve_mode('{"pip_mode": "loud"}', env={"PIP_MODE": "realtime"}) == "realtime"
    assert resolve_mode("[1, 2]", env={}) == "pipeline"


class FakeJob:
    def __init__(self, metadata):
        self.metadata = metadata


class FakeSession:
    def __init__(self):
        self.handlers = {}

    def on(self, event, callback):
        self.handlers[event] = callback


class FakeCtx:
    def __init__(self, metadata=""):
        self.job = FakeJob(metadata)
        self.room = object()
        self.shutdown_callbacks = []

    def add_shutdown_callback(self, callback):
        self.shutdown_callbacks.append(callback)


@pytest.mark.parametrize(
    ("metadata", "env", "expected"),
    [
        ("", None, "pipeline"),
        ("", "realtime", "realtime"),
        (mode_metadata("realtime"), None, "realtime"),
        (mode_metadata("pipeline"), "realtime", "pipeline"),
    ],
)
async def test_entrypoint_runs_the_chosen_engine(monkeypatch, tmp_path, metadata, env, expected):
    ran = []

    async def fake_pipeline(ctx):
        ran.append("pipeline")
        return FakeSession()

    async def fake_realtime(ctx):
        ran.append("realtime")
        return FakeSession()

    monkeypatch.setattr(agent_mod, "attach_room_context", lambda store, room: None)
    monkeypatch.setenv("PIP_USAGE_FILE", str(tmp_path / "usage.csv"))

    monkeypatch.delenv("PIP_CONTENT_URL", raising=False)
    monkeypatch.setattr(agent_mod, "_pipeline", fake_pipeline)
    monkeypatch.setattr(agent_mod, "_realtime", fake_realtime)
    if env:
        monkeypatch.setenv("PIP_MODE", env)
    else:
        monkeypatch.delenv("PIP_MODE", raising=False)
    ctx = FakeCtx(metadata)
    await agent_mod.entrypoint(ctx)
    assert ran == [expected]
    # Every session logs its usage when the job ends.
    assert len(ctx.shutdown_callbacks) == 1
    await ctx.shutdown_callbacks[0]("room disconnected")
    assert f",{expected}," in (tmp_path / "usage.csv").read_text()


def test_realtime_config_from_env():
    config = agent_mod.realtime_config({})
    assert (config.model, config.voice, config.eagerness) == ("gpt-realtime", "coral", "low")
    config = agent_mod.realtime_config({"PIP_REALTIME_MODEL": "gpt-realtime-mini", "PIP_REALTIME_EAGERNESS": "medium"})
    assert (config.model, config.eagerness) == ("gpt-realtime-mini", "medium")


def test_realtime_model_uses_semantic_vad(monkeypatch):
    from little_explorer_agent.voice.realtime import RealtimeConfig, realtime_model

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    model = realtime_model(RealtimeConfig())
    opts = model._opts
    assert opts.model == "gpt-realtime"
    assert opts.voice == "coral"
    assert opts.turn_detection.type == "semantic_vad"
    assert opts.turn_detection.eagerness == "low"
    assert opts.turn_detection.interrupt_response is True


# --- token server ----------------------------------------------------------------------------


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-that-is-long-enough-for-hs256")
    async with TestClient(TestServer(create_app(access_key=""))) as c:
        yield c


async def test_token_can_ask_for_a_mode(client):
    resp = await client.post("/token", json={"pip_mode": "realtime"})
    dispatch = claims((await resp.json())["participantToken"])["roomConfig"]["agents"][0]
    assert resolve_mode(dispatch["metadata"], env={}) == "realtime"


async def test_token_without_mode_leaves_it_to_the_agent(client):
    resp = await client.post("/token", json={})
    dispatch = claims((await resp.json())["participantToken"])["roomConfig"]["agents"][0]
    assert not dispatch.get("metadata")


async def test_token_rejects_unknown_mode(client):
    resp = await client.post("/token", json={"pip_mode": "turbo"})
    assert resp.status == 400


# --- context ---------------------------------------------------------------------------------


def test_context_store_notifies_on_change():
    store = ContextStore()
    seen = []
    unsubscribe = store.subscribe(seen.append)
    store.update({"page": "a"})
    store.update({"page": "a"})  # no change, no call
    store.handle_data(b'{"page": "b"}')
    unsubscribe()
    store.update({"page": "c"})
    assert seen == [{"page": "a"}, {"page": "b"}]


def test_realtime_instructions_follow_the_page():
    text = realtime_instructions(CTX)
    assert PERSONA in text and REALTIME_RULES in text
    assert PAGE.text in text
    assert "stay_quiet" in text
    other = PARIS.pages[1]
    assert other.text in realtime_instructions({**CTX, "page": other.id, "page_text": other.text})
    assert "not open on a page yet" in realtime_instructions({})


# --- realtime post-hoc guard -----------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


async def test_reply_guard_cuts_only_soon_after_a_rejected_turn():
    decisions = iter([TurnDecision.IGNORE, TurnDecision.RESPOND])
    clock = Clock()
    guard = ReplyGuard(lambda t: next(decisions), window=4.0, clock=clock)
    await guard.check("so the meeting moved to friday", CTX)
    assert guard.should_cut()
    clock.now += 5
    assert not guard.should_cut()
    await guard.check("why is the tower so tall", CTX)
    assert not guard.should_cut()
    # Ignored talk is not kept as conversation history; his question is.
    assert guard.history == [("user", "why is the tower so tall")]


async def test_reply_guard_cuts_once():
    clock = Clock()
    guard = ReplyGuard(lambda t: TurnDecision.LISTEN, clock=clock)
    await guard.check(PAGE.text, CTX)
    assert guard.should_cut()
    guard.consume()
    assert not guard.should_cut()


async def test_realtime_guard_listens_to_reading_and_ignores_adults():
    calls = []

    async def topic(t):
        calls.append(t.text)
        return TurnDecision.LISTEN  # classifier unsure -> treated as not for Pip

    guard = agent_mod.make_realtime_guard(ReadingTracker(), topic)
    first_half = " ".join(PAGE.text.split()[:3])
    assert await guard(turn(first_half)) is TurnDecision.LISTEN
    assert await guard(turn("yes!")) is TurnDecision.RESPOND  # short answers are left to the model
    assert await guard(turn("did you email the deck to the team")) is TurnDecision.IGNORE
    assert calls == ["did you email the deck to the team"]


async def test_realtime_guard_never_cuts_a_question_to_pip_by_name():
    async def topic(t):
        return TurnDecision.IGNORE

    guard = agent_mod.make_realtime_guard(ReadingTracker(), topic)
    assert await guard(turn("Pip, can you go up to the top of the tower?")) is TurnDecision.RESPOND
    gate = make_parallel_topic_gate(ReadingTracker(), topic)
    assert await gate(turn("pip can you go up to the top of the tower")) is TurnDecision.RESPOND
    assert await gate(turn("can you go up to the top of the tower")) is TurnDecision.IGNORE


async def test_realtime_guard_without_topic_gate_trusts_the_model():
    guard = agent_mod.make_realtime_guard(ReadingTracker(), None)
    assert await guard(turn("why do they paint it brown")) is TurnDecision.RESPOND


# --- pipeline parallel gate ------------------------------------------------------------------


async def test_parallel_topic_gate_always_answers_reading_praise():
    tracker = ReadingTracker()
    asked = []

    def topic(t):
        asked.append(t.text)
        return TurnDecision.IGNORE

    gate = make_parallel_topic_gate(tracker, topic)
    assert tracker.check(turn(PAGE.text)) is TurnDecision.RESPOND
    assert await gate(turn(PAGE.text)) is TurnDecision.RESPOND
    assert tracker.praise_pending(CTX)  # still there for the context note
    assert asked == []
    assert tracker.take_praise(CTX)
    assert await gate(turn("my uncle has a red car")) is TurnDecision.IGNORE


def _graph_agent(parallel_gate):
    from little_explorer_agent.voice.session import GraphAgent

    return GraphAgent(
        instructions="x", gate=None, parallel_gate=parallel_gate, context=ContextStore(CTX)
    )


@pytest.fixture
def fake_llm(monkeypatch):
    from livekit.agents import Agent

    state = {"chunks": ["Hello ", "there!"], "started": asyncio.Event()}

    async def llm_node(self, chat_ctx, tools, model_settings):
        state["started"].set()
        for chunk in state["chunks"]:
            await asyncio.sleep(0)
            yield chunk

    monkeypatch.setattr(Agent.default, "llm_node", llm_node)
    return state


async def _collect(agent):
    return [c async for c in agent.llm_node(None, [], None)]


async def test_reply_generates_while_the_gate_thinks(fake_llm):
    release = asyncio.Event()

    async def slow_gate(t):
        await release.wait()
        return TurnDecision.RESPOND

    agent = _graph_agent(slow_gate)
    agent._pending_gate = asyncio.ensure_future(agent._parallel_decision(turn("why is it tall")))
    collecting = asyncio.ensure_future(_collect(agent))
    await asyncio.wait_for(fake_llm["started"].wait(), 1)  # generation began before the gate answered
    assert not collecting.done()
    release.set()
    assert await asyncio.wait_for(collecting, 1) == ["Hello ", "there!"]


async def test_reply_is_dropped_when_the_gate_ignores(fake_llm):
    async def no(t):
        return TurnDecision.IGNORE

    agent = _graph_agent(no)
    agent._pending_gate = asyncio.ensure_future(agent._parallel_decision(turn("so the meeting is at nine")))
    agent._pending_message_id = "msg-1"
    assert await _collect(agent) == []
    assert "msg-1" in agent._ignored_ids


async def test_reply_without_parallel_gate_streams_straight_through(fake_llm):
    agent = _graph_agent(None)
    assert await _collect(agent) == ["Hello ", "there!"]
