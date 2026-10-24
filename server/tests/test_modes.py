"""Milo's two voice engines: the mode switch and the pieces that make each one feel conversational."""

import asyncio
import base64
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import agent as agent_mod
from little_explorer_agent.buddy import (
    GREETING,
    PERSONA,
    REALTIME_RULES,
    ReadingTracker,
    make_parallel_topic_gate,
    realtime_instructions,
)
from little_explorer_agent.content import load_books
from little_explorer_agent.modes import (
    DEFAULT_MODE,
    app_plays_intro,
    job_metadata,
    mode_metadata,
    parse_intro,
    resolve_mode,
)
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


class FakeRoomInfo:
    name = "little-explorer-test"


class FakeJob:
    def __init__(self, metadata):
        self.metadata = metadata
        self.room = FakeRoomInfo()


class FakeSession:
    def __init__(self):
        self.handlers = {}

    def on(self, event, callback):
        self.handlers[event] = callback


class FakeCtx:
    def __init__(self, metadata="", joined=True):
        self.job = FakeJob(metadata)
        self.room = object()
        self.shutdown_callbacks = []
        self.joined = joined
        self.shutdown_reason = None

    def add_shutdown_callback(self, callback):
        self.shutdown_callbacks.append(callback)

    async def connect(self):
        pass

    async def wait_for_participant(self):
        if not self.joined:
            await asyncio.sleep(3600)
        return object()

    def shutdown(self, reason=""):
        self.shutdown_reason = reason


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

    async def fake_pipeline(ctx, game, timer, greeting, record=False):
        ran.append("pipeline")
        return FakeSession()

    async def fake_realtime(ctx, game, timer, greeting, record=False):
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
    assert len(ctx.shutdown_callbacks) == 4  # usage, pending star reports, timers, turn timing
    await ctx.shutdown_callbacks[0]("room disconnected")
    await ctx.shutdown_callbacks[1]()
    await ctx.shutdown_callbacks[2]()
    assert f",{expected}," in (tmp_path / "usage.csv").read_text()


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (job_metadata("realtime", "ipad-1", 600, intro="app"), None),
        (job_metadata("realtime", "ipad-1", 600), GREETING),
        ("", GREETING),
    ],
)
async def test_greeting_is_skipped_only_when_the_app_plays_the_intro(monkeypatch, tmp_path, metadata, expected):
    """New apps play the recorded intro on tap; older ones still get the spoken greeting."""
    greetings = []

    async def fake_realtime(ctx, game, timer, greeting, record=False):
        greetings.append(greeting)
        return FakeSession()

    monkeypatch.setattr(agent_mod, "attach_room_context", lambda store, room: None)
    monkeypatch.setenv("PIP_USAGE_FILE", str(tmp_path / "usage.csv"))
    monkeypatch.delenv("PIP_CONTENT_URL", raising=False)
    monkeypatch.setenv("PIP_MODE", "realtime")
    monkeypatch.setattr(agent_mod, "_realtime", fake_realtime)
    await agent_mod.entrypoint(FakeCtx(metadata))
    assert greetings == [expected]


@pytest.mark.parametrize(
    ("account", "listed", "expected"),
    [("acct-1", "acct-1,acct-2", True), ("acct-3", "acct-1,acct-2", False), (None, "acct-1", False), ("acct-1", None, False)],
)
async def test_entrypoint_records_only_listed_accounts(monkeypatch, tmp_path, account, listed, expected):
    recorded = []

    async def fake_realtime(ctx, game, timer, greeting, record=False):
        recorded.append(record)
        return FakeSession()

    monkeypatch.setattr(agent_mod, "attach_room_context", lambda store, room: None)
    monkeypatch.setenv("PIP_USAGE_FILE", str(tmp_path / "usage.csv"))
    monkeypatch.delenv("PIP_CONTENT_URL", raising=False)
    monkeypatch.setenv("PIP_MODE", "realtime")
    if listed is None:
        monkeypatch.delenv("PIP_RECORD_ACCOUNTS", raising=False)
    else:
        monkeypatch.setenv("PIP_RECORD_ACCOUNTS", listed)
    monkeypatch.setattr(agent_mod, "_realtime", fake_realtime)
    await agent_mod.entrypoint(FakeCtx(job_metadata("realtime", "ipad-1", 600, account_id=account)))
    assert recorded == [expected]


def test_intro_flag_parsing():
    assert parse_intro("app") == "app" and parse_intro(" APP ") == "app"
    assert parse_intro("milo") is None and parse_intro(None) is None and parse_intro(1) is None
    assert app_plays_intro({"intro": "app"}) and not app_plays_intro({}) and not app_plays_intro({"intro": True})


async def test_empty_room_ends_without_a_session(monkeypatch, tmp_path):
    """A tap cancelled during a cold start leaves a room nobody joins: no greeting, no session."""
    ran = []

    async def fake_realtime(ctx, game, timer, greeting, record=False):
        ran.append("realtime")
        return FakeSession()

    monkeypatch.setenv("PIP_USAGE_FILE", str(tmp_path / "usage.csv"))
    monkeypatch.delenv("PIP_CONTENT_URL", raising=False)
    monkeypatch.setenv("PIP_MODE", "realtime")
    monkeypatch.setattr(agent_mod, "_realtime", fake_realtime)
    monkeypatch.setattr(agent_mod.participant_joined, "__defaults__", (0.01,))
    ctx = FakeCtx(joined=False)
    await agent_mod.entrypoint(ctx)
    assert ran == []
    assert ctx.shutdown_reason == "nobody joined"
    await ctx.shutdown_callbacks[0]("room disconnected")
    assert ",realtime," in (tmp_path / "usage.csv").read_text(), "the empty room's usage is still logged"


def test_realtime_config_from_env():
    from little_explorer_agent.voice.realtime import RealtimeConfig

    config = agent_mod.realtime_config({})
    assert config == RealtimeConfig()
    assert (config.model, config.voice, config.turns, config.barge_in) == ("gpt-realtime", "coral", "server", "vad")
    config = agent_mod.realtime_config(
        {
            "PIP_REALTIME_MODEL": "gpt-realtime-mini",
            "PIP_REALTIME_EAGERNESS": "Low",
            "PIP_REALTIME_BARGE_IN": "model",
            "PIP_REALTIME_TURNS": "client",
            "PIP_REALTIME_END_OF_TURN": "model",
            "PIP_REALTIME_STT_MODEL": "none",
            "PIP_REALTIME_MIN_INTERRUPTION_DURATION": "0.8",
            "PIP_REALTIME_MIN_INTERRUPTION_WORDS": "2",
            "PIP_REALTIME_COMMIT_AFTER": "0",
            "PIP_REALTIME_READING_COMMIT_AFTER": "6",
        }
    )
    assert (config.model, config.eagerness, config.barge_in, config.turns) == ("gpt-realtime-mini", "low", "model", "client")
    assert (config.end_of_turn, config.stt_model) == ("model", None)
    assert (config.min_interruption_duration, config.min_interruption_words) == (0.8, 2)
    assert (config.commit_after_s, config.reading_commit_after_s) == (0.0, 6.0)
    assert (RealtimeConfig().commit_after_s, RealtimeConfig().reading_commit_after_s) == (2.5, 5.0)
    with pytest.raises(ValueError):
        agent_mod.realtime_config({"PIP_REALTIME_TURNS": "both"})


def test_realtime_model_server_turns_with_soft_barge_in(monkeypatch):
    from little_explorer_agent.voice.realtime import RealtimeConfig, realtime_model, session_kwargs

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = RealtimeConfig()
    model = realtime_model(config)
    opts = model._opts
    assert (opts.model, opts.voice) == ("gpt-realtime", "coral")
    assert opts.turn_detection.type == "semantic_vad"
    assert opts.turn_detection.eagerness == config.eagerness
    # OpenAI no longer cancels Milo on any sound; a local VAD stops him on sustained speech.
    assert opts.turn_detection.interrupt_response is False
    assert model.capabilities.supports_overlapping_speech is True
    kwargs = session_kwargs(config)
    assert set(kwargs) == {"vad", "turn_handling"}
    assert kwargs["turn_handling"]["turn_detection"] == "realtime_llm"


def test_realtime_model_model_barge_in_and_client_turns(monkeypatch):
    from dataclasses import replace

    from little_explorer_agent.voice.realtime import RealtimeConfig, realtime_model, session_kwargs

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    old = replace(RealtimeConfig(), barge_in="model", eagerness="low")
    model = realtime_model(old)
    assert model._opts.turn_detection.interrupt_response is True
    assert model.capabilities.supports_overlapping_speech is False
    assert session_kwargs(old) == {}

    client = replace(RealtimeConfig(), turns="client")
    model = realtime_model(client)
    assert model._opts.turn_detection is None
    kwargs = session_kwargs(client)
    assert {"vad", "turn_handling", "stt"} <= set(kwargs)
    assert kwargs["turn_handling"]["turn_detection"] == "vad"
    assert kwargs["turn_handling"]["interruption"]["min_words"] == client.min_interruption_words


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
    assert "pip_mode" not in json.loads(dispatch["metadata"])
    assert resolve_mode(dispatch["metadata"], env={"PIP_MODE": "realtime"}) == "realtime"


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


async def test_reply_guard_skips_repeated_transcripts():
    """The transcriber sometimes turns noise or Milo's echo into the last thing he said."""
    clock = Clock()
    guard = ReplyGuard(lambda t: TurnDecision.RESPOND, clock=clock)
    assert not guard.is_repeat("Give me some names.", "item-1")
    assert guard.is_repeat("Give me some names.", "item-1")  # the same item twice
    clock.now += 1.6
    assert guard.is_repeat("give me some names", "item-2")  # same words, a new item, moments later
    clock.now += 10
    assert not guard.is_repeat("Give me some names.", "item-3")  # asked again much later: real
    assert not guard.is_repeat("What about the boat?", "item-4")


async def test_reply_guard_lets_him_finish_after_a_barge_in():
    clock = Clock()
    guard = ReplyGuard(lambda t: TurnDecision.IGNORE, clock=clock)
    guard.cut_off()
    clock.now += 2
    _, decision = await guard.check("OK", CTX)  # "OK, go on" after he talked over Milo
    assert decision is TurnDecision.RESPOND and not guard.should_cut()
    _, decision = await guard.check("\u5443\uff0c\u5229\u7528\u3002", CTX)  # a noise is still a noise
    assert decision is TurnDecision.IGNORE
    _, decision = await guard.check("so the meeting moved to friday", CTX)  # adult talk too
    assert decision is TurnDecision.IGNORE
    clock.now += 10
    _, decision = await guard.check("OK", CTX)
    assert decision is TurnDecision.IGNORE


async def test_realtime_guard_ignores_noise_in_another_script():
    async def topic(t):
        return TurnDecision.RESPOND

    guard = agent_mod.make_realtime_guard(ReadingTracker(), topic)
    for noise in ("\u5443\uff0c\u5229\u7528\u3002", "...", ""):
        assert await guard(turn(noise)) is TurnDecision.IGNORE
    assert await guard(turn("why?")) is TurnDecision.RESPOND
    assert agent_mod.is_noise("\u5443") and not agent_mod.is_noise("ok")


async def test_realtime_guard_listens_to_reading_and_ignores_adults():
    calls = []

    async def topic(t):
        calls.append(t.text)
        return TurnDecision.LISTEN  # classifier unsure -> treated as not for Milo

    guard = agent_mod.make_realtime_guard(ReadingTracker(), topic)
    first_half = " ".join(PAGE.text.split()[:3])
    assert await guard(turn(first_half)) is TurnDecision.LISTEN
    assert await guard(turn("yes!")) is TurnDecision.RESPOND  # short answers are left to the model
    assert await guard(turn("did you email the deck to the team")) is TurnDecision.IGNORE
    assert calls == ["did you email the deck to the team"]


async def test_realtime_guard_never_cuts_a_question_to_the_buddy_by_name():
    async def topic(t):
        return TurnDecision.IGNORE

    guard = agent_mod.make_realtime_guard(ReadingTracker(), topic)
    assert await guard(turn("Milo, can you go up to the top of the tower?")) is TurnDecision.RESPOND
    gate = make_parallel_topic_gate(ReadingTracker(), topic)
    assert await gate(turn("milo can you go up to the top of the tower")) is TurnDecision.RESPOND
    assert await gate(turn("can you go up to the top of the tower")) is TurnDecision.IGNORE


async def test_realtime_guard_lets_him_answer_milos_question():
    async def topic(t):
        return TurnDecision.IGNORE

    guard = agent_mod.make_realtime_guard(ReadingTracker(), topic)
    asked = [("assistant", "It is very tall! What would you do at the top?")]
    told = [("assistant", "It is very tall and made of iron.")]
    answer = "I would look at all the little cars"
    assert await guard(UserTurn(text=answer, context=CTX, history=asked)) is TurnDecision.RESPOND
    assert await guard(UserTurn(text=answer, context=CTX, history=told)) is TurnDecision.IGNORE
    invited = [("assistant", "Ooh, the cars! Can you tell me more? You could say, I would look at the...")]
    assert await guard(UserTurn(text=answer, context=CTX, history=invited)) is TurnDecision.RESPOND
    long = "and then we went to the shop and bought milk and bread and some apples for later"
    assert await guard(UserTurn(text=long, context=CTX, history=asked)) is TurnDecision.IGNORE


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
