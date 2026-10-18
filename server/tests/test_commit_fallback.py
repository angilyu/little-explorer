"""The realtime commit fallback: a finished question never waits for semantic_vad's ~8 s cap."""

import logging

from little_explorer_agent.voice.realtime import READING_WINDOW_S, STALE_COMMIT_WINDOW_S, CommitFallback

SPEECH_STARTED = {"type": "input_audio_buffer.speech_started"}
COMMITTED = {"type": "input_audio_buffer.committed"}


def server_response(rid="resp_auto"):
    return {"type": "response.created", "response": {"id": rid, "metadata": None}}


def our_response(rid="resp_ours"):
    return {"type": "response.created", "response": {"id": rid, "metadata": {"client_event_id": "response_create_x"}}}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Timer:
    def __init__(self, delay, callback):
        self.delay, self.callback, self.cancelled = delay, callback, False

    def cancel(self):
        self.cancelled = True


class Harness:
    def __init__(self, delay=2.5, reading_delay=5.0):
        self.clock = Clock()
        self.timers: list[Timer] = []
        self.busy = False
        self.forced = 0
        self.commits: list[bool] = []
        self.fallback = CommitFallback(
            delay=delay,
            reading_delay=reading_delay,
            can_force=lambda: not self.busy,
            force=self._force,
            on_commit=self.commits.append,
            schedule=self._schedule,
            clock=self.clock,
        )

    def _force(self):
        self.forced += 1

    def _schedule(self, delay, callback):
        timer = Timer(delay, callback)
        self.timers.append(timer)
        return timer

    @property
    def pending(self):
        return [t for t in self.timers if not t.cancelled and t.callback is not None]

    def run_timers(self):
        """Let every pending timer expire (advancing the clock to it)."""
        for timer in self.pending:
            self.clock.t += timer.delay
            callback, timer.callback = timer.callback, None
            callback()

    def question(self):
        """He speaks (OpenAI and the local VAD both hear it) and stops."""
        self.fallback.openai_event(SPEECH_STARTED)
        self.fallback.user_state("speaking")
        self.clock.t += 1.5
        self.fallback.user_state("listening")


def test_forces_the_turn_when_openai_is_still_waiting(caplog):
    caplog.set_level(logging.INFO, logger="little_explorer.voice.realtime")
    h = Harness()
    h.question()
    assert [t.delay for t in h.pending] == [2.5]
    h.run_timers()
    assert h.forced == 1 and h.commits == [True]
    assert "forced commit after 2.50s of silence" in caplog.text


def test_the_8s_reply_from_room_12c1de47e7():
    """'OK, any other food?': local VAD stopped, OpenAI's commit came ~7.7 s later. Now ~2.5 s."""
    h = Harness()
    h.question()
    h.run_timers()
    assert h.forced == 1
    # OpenAI's own commit of the leftover silence, ~5 s later: its auto-reply is cancelled once
    # ours has started.
    h.fallback.openai_event(our_response())
    h.clock.t += 5.2
    assert h.fallback.openai_event(COMMITTED) is None
    assert h.fallback.openai_event(server_response("resp_stale")) == "resp_stale"
    assert h.commits == [True], "the stale commit is not a new turn for the timing log"
    assert h.fallback.stale_cancelled == 1


def test_no_force_once_openai_has_committed():
    h = Harness()
    h.question()
    h.clock.t += 0.6
    h.fallback.openai_event(COMMITTED)
    assert h.commits == [False]
    assert h.pending == []
    assert h.fallback.openai_event(server_response()) is None, "OpenAI's normal reply is never cancelled"


def test_no_force_when_he_starts_talking_again():
    h = Harness()
    h.question()
    first = h.pending[0]
    h.clock.t += 1.0
    h.fallback.user_state("speaking")
    assert first.cancelled
    h.clock.t += 1.0
    h.fallback.user_state("listening")
    assert len(h.pending) == 1, "the silence is timed again from his last words"


def test_no_force_while_milo_is_busy():
    h = Harness()
    h.question()
    h.busy = True  # a reply is already active (or Milo is speaking)
    h.run_timers()
    assert h.forced == 0 and h.commits == []
    h.question()
    h.fallback.agent_state("speaking")
    assert h.pending == []


def test_no_force_without_an_open_openai_turn():
    h = Harness()
    # The local VAD heard something OpenAI never took for speech.
    h.fallback.user_state("speaking")
    h.fallback.user_state("listening")
    h.run_timers()
    assert h.forced == 0


def test_listening_without_speech_starts_nothing():
    h = Harness()
    h.fallback.openai_event(SPEECH_STARTED)
    h.fallback.user_state("listening")
    assert h.pending == []


def test_waits_longer_while_he_is_reading():
    h = Harness()
    h.fallback.reading()
    h.clock.t += READING_WINDOW_S - 5
    h.question()
    assert [t.delay for t in h.pending] == [5.0]
    h.run_timers()
    h.clock.t += READING_WINDOW_S
    h.question()
    assert [t.delay for t in h.pending] == [2.5], "reading long ago: the normal wait again"


def test_disabled_at_zero():
    h = Harness(delay=0)
    h.question()
    assert h.pending == []
    h.fallback.openai_event(COMMITTED)
    assert h.commits == [False], "the commit is still timed"


def test_new_speech_after_a_forced_commit_is_a_real_turn():
    h = Harness()
    h.question()
    h.run_timers()
    h.fallback.openai_event(our_response())
    h.clock.t += 3
    h.question()  # a follow-up question: OpenAI hears new speech
    h.fallback.openai_event(COMMITTED)
    assert h.fallback.openai_event(server_response()) is None
    assert h.commits == [True, False]


def test_local_speech_alone_also_ends_the_stale_window():
    h = Harness()
    h.question()
    h.run_timers()
    h.fallback.openai_event(our_response())
    # He speaks again; OpenAI's VAD may fold it into its still-open turn without a speech_started.
    h.fallback.user_state("speaking")
    h.fallback.openai_event(COMMITTED)
    assert h.fallback.openai_event(server_response()) is None


def test_openai_committing_first_keeps_its_reply():
    """Race: OpenAI committed just before our commit; ours is refused, so its reply must stay."""
    h = Harness()
    h.question()
    h.run_timers()
    h.fallback.openai_event(COMMITTED)
    assert h.fallback.openai_event(server_response("resp_auto")) is None
    assert h.fallback.stale_cancelled == 0


def test_stale_window_expires():
    h = Harness()
    h.question()
    h.run_timers()
    h.fallback.openai_event(our_response())
    h.clock.t += STALE_COMMIT_WINDOW_S + 1
    h.fallback.openai_event(COMMITTED)
    assert h.fallback.openai_event(server_response()) is None


async def test_start_realtime_session_wires_the_fallback(monkeypatch):
    """The real wiring with fake LiveKit objects: no network, no OpenAI."""
    import asyncio
    from types import SimpleNamespace

    from livekit.rtc import EventEmitter

    from little_explorer_agent.voice import realtime as rt_mod
    from little_explorer_agent.voice.preset import PATIENT

    class FakeRealtime(EventEmitter):
        has_active_generation = False

        def __init__(self):
            super().__init__()
            self.calls = []

        def commit_audio(self):
            self.calls.append("commit")

        def send_event(self, event):
            self.calls.append(event)

    fake_rt = FakeRealtime()

    class FakeAgent:
        def __init__(self, instructions, tools):
            self.realtime_llm_session = fake_rt

    class FakeSession(EventEmitter):
        def __init__(self, **kwargs):
            super().__init__()
            self.agent_state = "listening"
            self.replies = 0

        async def start(self, **kwargs):
            pass

        def generate_reply(self, **kwargs):
            self.replies += 1

    class Ctx:
        room = object()

        async def connect(self):
            pass

    monkeypatch.setattr(rt_mod, "Agent", FakeAgent)
    monkeypatch.setattr(rt_mod, "AgentSession", FakeSession)
    monkeypatch.setattr(rt_mod, "realtime_model", lambda config: None)
    monkeypatch.setattr(rt_mod, "session_kwargs", lambda config: {})
    monkeypatch.setattr(rt_mod, "attach_room_context", lambda store, room: None)
    monkeypatch.setattr(rt_mod, "_room_options", lambda preset: None)

    commits = []
    session = await rt_mod.start_realtime_session(
        Ctx(),
        config=rt_mod.RealtimeConfig(commit_after_s=0.05),
        preset=PATIENT,
        instructions=lambda ctx: "",
        on_commit=commits.append,
    )
    fake_rt.emit("openai_server_event_received", SPEECH_STARTED)
    session.emit("user_state_changed", SimpleNamespace(new_state="speaking"))
    session.emit("user_state_changed", SimpleNamespace(new_state="listening"))
    await asyncio.sleep(0.15)
    assert fake_rt.calls == ["commit"] and session.replies == 1 and commits == [True]

    fake_rt.emit("openai_server_event_received", our_response())
    fake_rt.emit("openai_server_event_received", COMMITTED)
    fake_rt.emit("openai_server_event_received", server_response("resp_stale"))
    assert fake_rt.calls[-1] == {"type": "response.cancel", "response_id": "resp_stale"}
    session.emit("close", None)
