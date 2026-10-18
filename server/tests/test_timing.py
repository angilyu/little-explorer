import logging

from little_explorer_agent.voice.timing import TurnTimer, p90


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Handle:
    def __init__(self) -> None:
        self.interrupted = False
        self._interrupt_source = None
        self.callbacks = []

    def add_done_callback(self, fn) -> None:
        self.callbacks.append(fn)

    def finish(self, source: str | None = None) -> None:
        if source:
            self.interrupted, self._interrupt_source = True, source
        for fn in self.callbacks:
            fn(self)


def test_p90():
    assert p90([]) is None
    assert p90([3.0, 1.0, 2.0]) == 3.0
    assert p90([float(i) for i in range(11)]) == 9.0


def test_turn_timer_splits_wait_and_answer(caplog):
    clock = Clock()
    timer = TurnTimer(clock=clock)
    caplog.set_level(logging.INFO, logger="little_explorer.voice.timing")
    timer.agent_state("speaking")  # the greeting: no user turn yet
    timer.agent_state("listening")
    clock.t = 1.0
    timer.user_state("speaking")
    clock.t = 3.0
    timer.user_state("listening")
    clock.t = 3.6
    timer.agent_state("thinking")
    clock.t = 4.0
    timer.agent_state("speaking")
    assert timer.replies == 1
    assert [round(x, 2) for x in (timer.waits[0], timer.answers[0], timer.totals[0])] == [0.6, 0.4, 1.0]
    assert "turn 1: wait 0.60s, answer 0.40s, total 1.00s" in caplog.text
    assert "median 1.00s p90 1.00s (n=1)" in timer.summary()


def test_turn_timer_counts_interruptions_and_quiet_turns(caplog):
    clock = Clock()
    timer = TurnTimer(clock=clock)
    caplog.set_level(logging.INFO, logger="little_explorer.voice.timing")
    handle = Handle()
    timer.speech_created(handle)
    timer.agent_state("speaking")
    clock.t = 2.0
    timer.user_state("speaking")
    clock.t = 2.5
    handle.finish("audio_activity")
    timer.false_interruption(resumed=True)
    timer.tools(["stay_quiet"])
    Handle().finish()  # not interrupted: nothing logged
    assert timer.interruptions == {"audio_activity": 1}
    assert "interrupted by audio_activity after 2.50s, user speech 0.50s so far" in caplog.text
    assert (timer.false_interruptions, timer.resumed, timer.quiet_turns) == (1, 1, 1)
    summary = timer.summary()
    assert "stayed quiet 1" in summary and "audio_activity 1" in summary and "(resumed 1)" in summary


def test_barge_in_counts_the_speech_the_vad_needed_first(caplog):
    """The VAD says "speaking" only after lead_s of speech, and the soft barge-in fires right then."""
    clock = Clock()
    timer = TurnTimer(clock=clock, lead_s=0.6)
    caplog.set_level(logging.INFO, logger="little_explorer.voice.timing")
    handle = Handle()
    timer.speech_created(handle)
    timer.agent_state("speaking")
    clock.t = 3.0
    timer.user_state("speaking")
    timer.note_interrupt("barge_in")
    handle.finish("programmatic")
    assert "interrupted by barge_in after 3.00s, user speech 0.60s so far" in caplog.text
    timer.agent_state("listening")
    clock.t = 4.5
    timer.user_state("listening")
    assert "he talked over Milo for 2.10s" in caplog.text
    assert timer.interruptions == {"barge_in": 1}
    assert "talked over Milo median 2.10s" in timer.summary()


def test_programmatic_without_a_recent_cause_stays_programmatic():
    clock = Clock()
    timer = TurnTimer(clock=clock)
    timer.note_interrupt("guard")
    clock.t = 5.0  # long after: e.g. the session closing
    handle = Handle()
    timer.speech_created(handle)
    handle.finish("programmatic")
    assert timer.interruptions == {"programmatic": 1}


def test_realtime_commit_splits_wait_and_answer_and_counts_forced(caplog):
    clock = Clock()
    timer = TurnTimer(clock=clock)
    caplog.set_level(logging.INFO, logger="little_explorer.voice.timing")
    timer.user_state("speaking")
    clock.t = 2.0
    timer.user_state("listening")
    clock.t = 4.5
    timer.committed(forced=True)
    clock.t = 5.2
    timer.agent_state("speaking")
    assert "turn 1: wait 2.50s, answer 0.70s, total 3.20s, forced commit" in caplog.text
    timer.agent_state("listening")
    clock.t = 6.0
    timer.user_state("speaking")
    clock.t = 7.0
    timer.user_state("listening")
    clock.t = 7.4
    timer.committed()
    clock.t = 8.0
    timer.agent_state("speaking")
    assert "turn 2: wait 0.40s, answer 0.60s, total 1.00s\n" in caplog.text + "\n"
    assert "forced commits 1" in timer.summary()
