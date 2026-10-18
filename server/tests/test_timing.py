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
