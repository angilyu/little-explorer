"""Stars for answering Milo and asking him questions: the validation rules, awarding turns Milo replied to,
storage and endpoints."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import agent as agent_mod
from little_explorer_agent.buddy import STAR_RULES, realtime_instructions
from little_explorer_agent.devices import DeviceStore, Limits, award_day
from little_explorer_agent.stars import (
    StarAward,
    StarKeeper,
    StarRules,
    asks_milo,
    asks_to_repeat,
    count_page_stars,
    device_clock,
    fetch_page_stars,
    is_small_talk,
    report_star,
    star_payload,
)
from little_explorer_agent.token_server import create_app
from little_explorer_agent.usage import report_target
from little_explorer_agent.voice import TurnDecision, UserTurn

PAGE = {"book": "paris", "page": "eiffel", "city": "Paris"}
OTHER_PAGE = {"book": "paris", "page": "louvre", "city": "Paris"}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


PACIFIC = timezone(timedelta(hours=-7))


class Wall:
    """The device's local time, settable (the keeper's ``wall``)."""

    def __init__(self, text: str = "2026-10-19T15:00:00-07:00") -> None:
        self.now = datetime.fromisoformat(text)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def keeper(clock):
    return StarKeeper(device_id="ipad-1", room="room-1", clock=clock)


MILO_ASKS = "The tower is very tall. If you went up there, what would you look at first?"
ANSWER = "I would look at all the tiny cars"


def hear(keeper, text, decision=TurnDecision.RESPOND, context=PAGE, clock=None, gap=12.0, milo=MILO_ASKS):
    """Milo said ``milo``, then he speaks and the guard decides; ``gap`` seconds pass first."""
    if clock is not None:
        clock.t += gap
    keeper.speech_started()
    if clock is not None:
        clock.t += 1.0
    history = (("user", "hi"), ("assistant", milo)) if milo else ()
    keeper.heard(UserTurn(text=text, context=dict(context), history=history), decision)


# --- the rules ---------------------------------------------------------------------------------


def test_an_answer_of_five_words_earns_one_star(keeper, clock):
    hear(keeper, "I would see the river", clock=clock)
    award = keeper.judge("looking at the river")
    assert isinstance(award, StarAward)
    assert (award.stars, award.kind, award.book, award.page, award.city) == (1, "answer", "paris", "eiffel", "Paris")
    assert (award.long, award.why_how, award.followup, award.words) == (False, False, False, 5)
    assert award.device_id == "ipad-1" and award.reason == "looking at the river"


def test_four_words_is_too_short(keeper, clock):
    hear(keeper, "I would see boats", clock=clock)
    assert keeper.judge() == "answer too short"
    assert keeper.total() == 0


@pytest.mark.parametrize(
    "text",
    [
        "Why is the tower so very tall?",
        "Milo, how do people get up to the top",
        "What is inside the tower at the very top",
        "Can you tell me about the tower lights please",
        "I wonder who built this big brown tower",
        "Hey, what's this tower?",
        "what's this tower",
        "Why did they make this a theme park on a mountain?",
    ],
)
def test_his_own_question_earns_a_star(keeper, clock, text):
    hear(keeper, text, clock=clock, milo="The Eiffel Tower is very tall.")  # Milo didn't ask anything
    award = keeper.judge("asked about the tower")
    assert isinstance(award, StarAward)
    assert (award.stars, award.kind, award.book, award.page) == (1, "question", "paris", "eiffel")
    assert keeper.total() == 1


def test_a_question_also_earns_a_star_after_milo_asked(keeper, clock):
    hear(keeper, "Why is the tower brown?", clock=clock)
    assert keeper.judge().kind == "question"


@pytest.mark.parametrize("text", ["Why?", "what?", "Milo, why?", "What's that?", "Hi Milo, how come?", "Is it?"])
def test_a_short_question_earns_nothing(keeper, clock, text):
    hear(keeper, text, clock=clock)
    assert keeper.judge() == "question too short"
    assert keeper.total() == 0


@pytest.mark.parametrize("text", ["What did you say?", "Milo, can you say that again?", "What do you mean?", "Sorry, what was that?"])
def test_asking_milo_to_repeat_earns_nothing(keeper, clock, text):
    hear(keeper, text, clock=clock)
    assert keeper.judge() == "asking Milo to say it again"
    assert keeper.total() == 0


@pytest.mark.parametrize(
    ("text", "decision", "reason"),
    [
        ("Mom, what are we having for dinner tonight?", TurnDecision.IGNORE, "that was reading or not meant for Milo"),
        ("Why is the TV so loud in here?", TurnDecision.IGNORE, "that was reading or not meant for Milo"),
        ("What is that tall tower over there?", TurnDecision.LISTEN, "that was reading or not meant for Milo"),
        ("Milo, are you there?", TurnDecision.RESPOND, "small talk"),
        ("Hi Milo, what's your name?", TurnDecision.RESPOND, "small talk"),
        ("How are you doing today?", TurnDecision.RESPOND, "small talk"),
    ],
)
def test_questions_to_grown_ups_noise_or_small_talk_earn_nothing(keeper, clock, text, decision, reason):
    hear(keeper, text, decision, clock=clock)
    assert keeper.judge() == reason
    assert keeper.total() == 0


def test_reading_a_question_on_the_page_aloud_earns_nothing(keeper, clock):
    page = {**PAGE, "page_text": "Can you see the tower from the river?"}
    hear(keeper, "Can you see the tower from the river?", context=page, clock=clock)
    assert keeper.judge() == "that was reading the page"


def test_questions_and_answers_share_the_page_limit(keeper, clock):
    for i in range(5):
        if i % 2:
            hear(keeper, ANSWER, clock=clock)
        else:
            hear(keeper, "Why is the tower so tall?", clock=clock)
        assert keeper.judge().stars == 1
    assert [a.kind for a in keeper.awards] == ["question", "answer", "question", "answer", "question"]
    hear(keeper, "How did they build the tower?", clock=clock)
    assert keeper.judge() == "this page's stars are all taken"
    hear(keeper, "Who paints the tower every year?", context=OTHER_PAGE, clock=clock)
    assert keeper.judge().kind == "question"  # a new page has its own stars


def test_a_question_respects_earlier_sessions_and_the_cooldown(clock):
    keeper = StarKeeper(clock=clock, wall=Wall(), earlier={("paris", "eiffel", "2026-10-19"): 5})
    hear(keeper, "Why is the tower so tall?", clock=clock)
    assert keeper.judge() == "this page's stars are all taken"
    keeper = StarKeeper(clock=clock, rules=StarRules(cooldown_s=8.0))
    hear(keeper, "Why is the tower so tall?", clock=clock)
    assert keeper.judge().kind == "question"
    hear(keeper, "How did they build the tower?", context=OTHER_PAGE, clock=clock, gap=2.0)
    assert keeper.judge() == "too soon after the last star"


def test_one_award_per_question(keeper, clock):
    hear(keeper, "Why is the tower so tall?", clock=clock)
    assert isinstance(keeper.judge(), StarAward)
    assert keeper.judge() == "no new answer heard"


def test_question_min_words_is_configurable(clock):
    keeper = StarKeeper(clock=clock, rules=StarRules(question_min_words=2))
    hear(keeper, "What's that?", clock=clock)
    assert keeper.judge().kind == "question"
    keeper = StarKeeper(clock=clock, rules=StarRules(question_min_words=5))
    hear(keeper, "Why is it brown?", clock=clock)
    assert keeper.judge() == "question too short"


def test_a_question_star_is_backed_up_as_a_question(store, keeper, clock):
    hear(keeper, "Why is the tower so tall?", clock=clock)
    award = keeper.judge()
    assert store.record_star(award.to_dict()) is True
    assert [s["kind"] for s in store.stars_for("ipad-1")] == ["question"]
    assert json.loads(star_payload(award, 1))["kind"] == "question"


def test_an_answer_still_needs_milos_question(keeper, clock):
    hear(keeper, ANSWER, clock=clock, milo="The tower is very tall. It was built long ago.")
    assert keeper.judge() == "Milo didn't ask a question"
    hear(keeper, ANSWER, clock=clock, milo=None)
    assert keeper.judge() == "Milo didn't ask a question"


def test_milos_question_can_be_anywhere_in_his_reply(keeper, clock):
    hear(keeper, ANSWER, clock=clock, milo="What would you see up there? I bet it's a lot!")
    assert isinstance(keeper.judge(), StarAward)


NUDGE = "Ooh, the boats! Can you tell me more? You could say, I would ride the boat because..."


def test_finishing_milos_sentence_start_earns_a_star(keeper, clock):
    hear(keeper, "boats", clock=clock)
    assert keeper.judge() == "answer too short"
    hear(keeper, "I would ride the boat because it goes fast", clock=clock, milo=NUDGE)
    award = keeper.judge()
    assert isinstance(award, StarAward) and award.why_how
    hear(keeper, "the big one", clock=clock, milo=NUDGE)
    assert keeper.judge() == "answer too short"


def test_reading_the_page_aloud_earns_nothing(keeper, clock):
    page = {**PAGE, "page_text": "The Eiffel Tower is tall and brown and stands next to the river."}
    hear(keeper, "The Eiffel Tower is tall and brown and stands next to the river", context=page, clock=clock)
    assert keeper.judge() == "that was reading the page"
    hear(keeper, "The Eiffel Tower is tall", TurnDecision.LISTEN, context=page, clock=clock)
    assert keeper.judge() == "that was reading or not meant for Milo"
    hear(keeper, "I would look at the river and the boats", context=page, clock=clock)
    assert isinstance(keeper.judge(), StarAward)  # a few of the page's words in a real answer are fine


@pytest.mark.parametrize(
    "text",
    [
        "I would look at the tiny cars and all the people walking on the bridge",
        "I want to see the boats because they are fast",
    ],
)
def test_long_and_because_answers_are_noted_but_still_one_star(keeper, clock, text):
    hear(keeper, text, clock=clock)
    award = keeper.judge()
    assert award.stars == 1 and (award.long or award.why_how)


def test_up_to_five_stars_per_page(keeper, clock):
    for _ in range(5):
        hear(keeper, "I would look at the tiny cars", clock=clock)
        assert keeper.judge().stars == 1
    hear(keeper, "I would wave at all the people", clock=clock)
    assert keeper.judge() == "this page's stars are all taken"
    assert keeper.page_stars("paris", "eiffel") == 5
    hear(keeper, "I would look at the big paintings", context=OTHER_PAGE, clock=clock)
    assert keeper.judge().stars == 1  # a new page has its own stars
    assert keeper.total() == 6


def test_stars_from_earlier_sessions_count_toward_the_page_limit(clock):
    earlier = {
        ("paris", "eiffel", "2026-10-19"): 4,
        ("paris", "louvre", "2026-10-19"): 5,
        ("paris", "eiffel", "2026-10-18"): 5,  # yesterday's stars don't count today
    }
    keeper = StarKeeper(clock=clock, wall=Wall(), earlier=earlier)
    assert keeper.stars_left(PAGE) == 1 and keeper.stars_left(OTHER_PAGE) == 0
    hear(keeper, ANSWER, clock=clock)
    assert keeper.judge().stars == 1
    hear(keeper, ANSWER, clock=clock)
    assert keeper.judge() == "this page's stars are all taken"
    hear(keeper, ANSWER, context=OTHER_PAGE, clock=clock)
    assert keeper.judge() == "this page's stars are all taken"
    assert keeper.total() == 1  # the session total counts this session's stars only


def test_count_page_stars_per_local_day():
    late, early = "2026-10-19T23:59:00-07:00", "2026-10-20T00:01:00-07:00"
    awards = [
        {"book": "paris", "page": "eiffel", "stars": 1, "awarded_at": late},
        {"book": "paris", "page": "eiffel", "stars": 3, "awarded_at": late},
        {"book": "paris", "page": "eiffel", "stars": 1, "awarded_at": early},
        {"book": "paris", "page": "louvre", "stars": 1, "awarded_at": late},
        {"book": "", "page": "x", "stars": 1, "awarded_at": late},
        {"book": "paris", "page": "eiffel", "stars": True, "awarded_at": late},
        {"book": "paris", "page": "eiffel", "stars": 0, "awarded_at": late},
        {"book": "paris", "page": "eiffel", "stars": 1, "awarded_at": "not a time"},
        {"book": "paris", "page": "eiffel", "stars": 1},
    ]
    assert count_page_stars(awards) == {
        ("paris", "eiffel", "2026-10-19"): 4,
        ("paris", "eiffel", "2026-10-20"): 1,
        ("paris", "louvre", "2026-10-19"): 1,
    }


def test_award_day_is_the_local_date_of_the_award():
    assert award_day("2026-10-19T23:59:00-07:00") == "2026-10-19"  # 06:59 UTC on the 20th
    assert award_day("2026-10-20T00:01:00-07:00") == "2026-10-20"
    assert award_day("2026-10-20T06:59:00Z") == "2026-10-20"
    assert award_day("") is None and award_day("soon") is None and award_day(None) is None


def test_the_page_limit_starts_over_at_local_midnight(clock):
    wall = Wall("2026-10-19T23:58:00-07:00")
    keeper = StarKeeper(device_id="ipad-1", clock=clock, wall=wall)
    for _ in range(5):
        hear(keeper, ANSWER, clock=clock)
        assert keeper.judge().awarded_at.startswith("2026-10-19T23:58")
    wall.now = datetime(2026, 10, 19, 23, 59, tzinfo=PACIFIC)
    hear(keeper, ANSWER, clock=clock)
    assert keeper.judge() == "this page's stars are all taken"
    wall.now = datetime(2026, 10, 20, 0, 1, tzinfo=PACIFIC)
    assert keeper.stars_left(PAGE) == 5  # a new day in his time zone
    hear(keeper, ANSWER, clock=clock)
    assert keeper.judge().awarded_at == "2026-10-20T00:01:00-07:00"
    assert keeper.total() == 6


def test_device_clock_uses_the_device_time_zone():
    assert device_clock("Asia/Tokyo", env={})().utcoffset() == timedelta(hours=9)
    paris = device_clock(None, env={"PIP_TIMEZONE": "Europe/Paris"})()
    assert str(paris.tzinfo) == "Europe/Paris"
    assert str(device_clock("Mars/Olympus", env={})().tzinfo) == "America/Los_Angeles"


def test_a_quick_next_star_is_not_refused_as_too_soon(keeper, clock):
    hear(keeper, ANSWER, clock=clock)
    assert isinstance(keeper.judge(), StarAward)
    hear(keeper, "I would look at the big paintings", context=OTHER_PAGE, clock=clock, gap=2.0)
    assert isinstance(keeper.judge(), StarAward)
    hear(keeper, "I would look at the big paintings", context=OTHER_PAGE, clock=clock, gap=2.0)
    assert isinstance(keeper.judge(), StarAward)  # the same page again


def test_cooldown_can_be_turned_on(clock):
    keeper = StarKeeper(clock=clock, rules=StarRules(cooldown_s=8.0))
    hear(keeper, ANSWER, clock=clock)
    assert isinstance(keeper.judge(), StarAward)
    hear(keeper, "I would look at the big paintings", context=OTHER_PAGE, clock=clock, gap=2.0)
    assert keeper.judge() == "too soon after the last star"


def test_one_award_per_answer(keeper, clock):
    hear(keeper, ANSWER, clock=clock)
    assert isinstance(keeper.judge(), StarAward)
    clock.t += 30
    assert keeper.judge() == "no new answer heard"


@pytest.mark.parametrize(
    ("text", "decision", "reason"),
    [
        ("The Eiffel Tower is in Paris.", TurnDecision.LISTEN, "that was reading or not meant for Milo"),
        ("I think we should call the plumber about the sink", TurnDecision.IGNORE, "that was reading or not meant for Milo"),
        ("Hi Milo", TurnDecision.RESPOND, "small talk"),
        ("Hello Milo, how are you?", TurnDecision.RESPOND, "small talk"),
        ("The boat", TurnDecision.RESPOND, "answer too short"),
        ("I don't know", TurnDecision.RESPOND, "answer too short"),
    ],
)
def test_noise_greetings_and_short_answers_earn_nothing(keeper, clock, text, decision, reason):
    hear(keeper, text, decision, clock=clock)
    assert keeper.judge() == reason
    assert keeper.total() == 0


def test_no_star_without_a_heard_answer(keeper):
    assert keeper.judge() == "no new answer heard"


def test_an_old_answer_cannot_earn_a_star(keeper, clock):
    hear(keeper, ANSWER, clock=clock)
    clock.t += 25
    assert keeper.judge() == "no new answer heard"


def test_no_star_before_a_book_is_open(keeper, clock):
    hear(keeper, ANSWER, context={}, clock=clock)
    assert keeper.judge() == "no book open"


def test_answers_that_start_unsure_still_count(keeper, clock):
    hear(keeper, "Um I don't know, maybe the river and the boats", clock=clock)
    assert keeper.judge().stars == 1


def test_small_talk():
    assert is_small_talk("Hey Milo, how are you doing?")
    assert is_small_talk("Milo, are you there?")
    assert not is_small_talk("How are you supposed to climb it?")


def test_asks_milo():
    assert asks_milo("Milo, why is it brown")
    assert asks_milo("it is brown?")
    assert not asks_milo("I would ride the elevator to the top")
    assert not asks_milo("Because the paint is brown")


def test_asks_to_repeat():
    assert asks_to_repeat("Milo, what did you say?") and asks_to_repeat("say it again")
    assert not asks_to_repeat("What did they build first?")


def test_reasons_are_short_and_single_line(keeper, clock):
    hear(keeper, ANSWER, clock=clock)
    award = keeper.judge("a\nvery " + "long " * 40)
    assert "\n" not in award.reason and len(award.reason) <= 80


def test_rules_from_env():
    rules = StarRules.from_env(
        {"PIP_STAR_COOLDOWN_S": "3", "PIP_STAR_LONG_WORDS": "10", "PIP_STAR_MIN_WORDS": "4", "PIP_STAR_QUESTION_MIN_WORDS": "2"}
    )
    assert (rules.cooldown_s, rules.long_words, rules.min_words, rules.question_min_words) == (3.0, 10, 4, 2)
    assert StarRules.from_env({}) == StarRules()
    assert StarRules().cooldown_s == 0 and StarRules().min_words == 5 and StarRules().question_min_words == 3


def test_min_words_is_configurable(clock):
    keeper = StarKeeper(clock=clock, rules=StarRules(min_words=4))
    hear(keeper, "I like the boats", clock=clock)
    assert isinstance(keeper.judge(), StarAward)
    hear(keeper, "I like boats", clock=clock)
    assert keeper.judge() == "answer too short"


def test_status_line_and_instructions(keeper, clock):
    line = keeper.status_line(PAGE)
    assert "stars waiting" in line and "at least 5 words" in line and "question of his own of at least 3 words" in line
    for _ in range(4):
        hear(keeper, ANSWER, clock=clock)
        keeper.judge()
    assert "stars waiting" in keeper.status_line(PAGE)  # one left
    hear(keeper, ANSWER, clock=clock)
    keeper.judge()
    assert "Don't mention stars" in keeper.status_line(PAGE)
    assert "stars waiting" in keeper.status_line(OTHER_PAGE)
    assert "Don't mention stars" in keeper.status_line({})
    text = realtime_instructions(PAGE, stars=keeper.status_line(PAGE))
    assert STAR_RULES in text and "Great answer, that's a star!" in text and "up to five stars" in text
    assert "Great question, that's a star!" in text and "what did you say?" in text
    assert "fewer than 5 words" in text and "at most once per question" in text and "never sound like a correction" in text
    full = realtime_instructions(OTHER_PAGE, stars="Stars right now: this page's stars are all taken for today.")
    assert "fewer than 5 words" in full  # he is still invited to say more once the stars are taken
    assert "fewer than 5 words" in realtime_instructions(PAGE)  # and with the star game off
    assert "fewer than 4 words" in realtime_instructions(PAGE, min_words=4)
    assert "that's a star" not in realtime_instructions(PAGE)


def test_payload_has_no_transcript(keeper, clock):
    hear(keeper, "It is brown because they paint it", clock=clock)
    award = keeper.judge("tower color")
    data = json.loads(star_payload(award, 2))
    assert data["session_total"] == 2 and data["stars"] == 1 and "room" not in data
    assert "brown" not in json.dumps(data)


class FakeParticipant:
    def __init__(self) -> None:
        self.sent = []

    async def publish_data(self, payload, *, reliable, topic):
        self.sent.append((json.loads(payload), reliable, topic))


class FakeCtx:
    def __init__(self) -> None:
        self.room = type("Room", (), {"local_participant": FakeParticipant()})()


async def test_star_game_sends_awards_to_the_app_and_the_server(clock):
    ctx, reported, refreshed = FakeCtx(), [], []

    async def report(award):
        reported.append(award)
        return True

    game = agent_mod.StarGame(ctx, StarKeeper(device_id="ipad-1", clock=clock), report=report)
    game.refresh = lambda: refreshed.append(True)
    hear(game.keeper, ANSWER, clock=clock)
    game.award(game.keeper.judge())
    await game.drain()
    payload, reliable, topic = ctx.room.local_participant.sent[0]
    assert topic == "milo.star" and reliable and payload["stars"] == 1 and payload["session_total"] == 1
    assert reported[0].stars == 1
    assert refreshed == []  # the page still has stars waiting
    for _ in range(4):
        hear(game.keeper, ANSWER, clock=clock)
        game.award(game.keeper.judge())
    await game.drain()
    assert refreshed == [True]  # the page's stars are all taken, so the model's instructions change
    assert ctx.room.local_participant.sent[-1][0]["session_total"] == 5


async def test_star_game_loads_earlier_stars(clock):
    asked, refreshed = [], []

    async def earlier(device_id, account_id=""):
        asked.append(device_id)
        return {("paris", "eiffel", "2026-10-19"): 5, ("paris", "louvre", "2026-10-18"): 5}

    keeper = StarKeeper(device_id="ipad-1", clock=clock, wall=Wall())
    game = agent_mod.StarGame(FakeCtx(), keeper, report=_report_ok, earlier=earlier)
    game.refresh = lambda: refreshed.append(True)
    game.load_earlier()
    await game.drain()
    assert asked == ["ipad-1"] and refreshed == [True]
    assert keeper.stars_left(PAGE) == 0 and keeper.stars_left(OTHER_PAGE) == 5


async def test_star_game_without_earlier_stars_keeps_going(clock):
    async def earlier(device_id, account_id=""):
        return None

    keeper = StarKeeper(device_id="ipad-1", clock=clock)
    game = agent_mod.StarGame(FakeCtx(), keeper, report=_report_ok, earlier=earlier)
    game.load_earlier()
    await game.drain()
    assert keeper.earlier == {} and keeper.stars_left(PAGE) == 5
    nobody = agent_mod.StarGame(FakeCtx(), StarKeeper(clock=clock), report=_report_ok, earlier=earlier)
    nobody.load_earlier()  # no device: nothing to read
    assert nobody._tasks == set()


def new_game(clock, **kw):
    ctx = FakeCtx()
    game = agent_mod.StarGame(ctx, StarKeeper(device_id="ipad-1", clock=clock), report=_report_ok, **kw)
    return ctx, game


async def _report_ok(award):
    return True


def turn(text, context=PAGE, milo=MILO_ASKS):
    return UserTurn(text=text, context=dict(context), history=(("assistant", milo),))


def ask(game, clock, text, decision=TurnDecision.RESPOND, context=PAGE):
    clock.t += 12.0
    game.keeper.speech_started()
    clock.t += 1.0
    game.realtime_decision(turn(text, context), decision)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_realtime_star_comes_once_milo_replies(clock):
    ctx, game = new_game(clock)
    ask(game, clock, ANSWER)
    await settle()
    assert game.keeper.awards == []  # Milo hasn't replied yet
    clock.t += 0.5
    game.milo_started_speaking()
    await game.drain()
    assert [a.stars for a in game.keeper.awards] == [1]
    assert ctx.room.local_participant.sent[0][0]["stars"] == 1


async def test_realtime_reply_already_under_way_counts(clock):
    ctx, game = new_game(clock)
    clock.t += 12.0
    game.keeper.speech_started()
    clock.t += 0.8
    game.milo_started_speaking()  # the realtime reply starts before the guard has decided
    clock.t += 0.4
    game.realtime_decision(turn(ANSWER), TurnDecision.RESPOND)
    await game.drain()
    assert len(game.keeper.awards) == 1


async def test_realtime_no_answer_no_star(clock):
    _, game = new_game(clock, answer_wait_s=0.05)
    clock.t += 12.0
    game.keeper.speech_started()
    game.milo_started_speaking()  # an older reply, before he answered
    clock.t += 1.0
    game.realtime_decision(turn(ANSWER), TurnDecision.RESPOND)
    task = asyncio.ensure_future(game.drain())
    for _ in range(20):
        clock.t += 0.01
        await asyncio.sleep(0.01)
    await task
    assert game.keeper.awards == []


async def test_realtime_ignored_turns_never_wait_or_earn(clock):
    _, game = new_game(clock)
    ask(game, clock, "I think we should call the plumber about the sink", decision=TurnDecision.IGNORE)
    ask(game, clock, "The Eiffel Tower is tall", decision=TurnDecision.LISTEN)
    assert game._tasks == set()
    game.milo_started_speaking()
    await game.drain()
    assert game.keeper.awards == []


async def test_realtime_newer_answer_wins(clock):
    _, game = new_game(clock)
    ask(game, clock, ANSWER)
    ask(game, clock, "I would look at the river and the boats")
    game.milo_started_speaking()
    await game.drain()
    assert len(game.keeper.awards) == 1 and game.keeper.awards[0].words == 9


async def test_realtime_his_question_earns_a_star(clock):
    _, game = new_game(clock)
    ask(game, clock, "Why do they paint the tower brown every time?")
    game.milo_started_speaking()
    await game.drain()
    assert [a.kind for a in game.keeper.awards] == ["question"]


async def test_realtime_short_question_earns_nothing(clock):
    _, game = new_game(clock)
    ask(game, clock, "Why?")
    game.milo_started_speaking()
    await game.drain()
    assert game.keeper.awards == []


async def test_stars_can_be_turned_off(clock):
    _, game = new_game(clock, enabled=False)
    ask(game, clock, ANSWER)
    game.pipeline_decision(turn("I would look at the river and the boats"), TurnDecision.RESPOND)
    game.milo_started_speaking()
    await game.drain()
    assert game.keeper.awards == []


async def test_pipeline_awards_answers_and_questions_directly(clock):
    ctx = FakeCtx()

    async def report(award):
        return True

    game = agent_mod.StarGame(ctx, StarKeeper(clock=clock), report=report)
    clock.t += 20
    game.pipeline_decision(turn(ANSWER), TurnDecision.RESPOND)
    game.pipeline_decision(turn("The tower is brown."), TurnDecision.LISTEN)
    game.pipeline_decision(turn("I would look at the river and the boats"), TurnDecision.RESPOND)
    game.pipeline_decision(turn("Why is the tower brown?"), TurnDecision.RESPOND)
    game.pipeline_decision(turn("Why?"), TurnDecision.RESPOND)
    await game.drain()
    assert game.keeper.total() == 3 and len(ctx.room.local_participant.sent) == 3
    assert [a.kind for a in game.keeper.awards] == ["answer", "answer", "question"]


async def test_watch_tracks_who_is_speaking(clock):
    game = agent_mod.StarGame(None, StarKeeper(clock=clock))
    handlers = {}
    session = type("S", (), {"on": lambda self, name, fn: handlers.__setitem__(name, fn)})()
    game.watch(session)
    clock.t += 1
    handlers["user_state_changed"](type("E", (), {"new_state": "speaking"})())
    assert game.keeper.speech_at == clock.t
    clock.t += 2
    handlers["agent_state_changed"](type("E", (), {"new_state": "speaking"})())
    assert game._answered_at == clock.t


async def test_report_star_goes_to_the_stars_endpoint(monkeypatch, keeper, clock):
    assert report_target({"PIP_SERVER_URL": "https://x.example/", "PIP_REPORT_KEY": "k"}, path="stars") == (
        "https://x.example/stars",
        "k",
    )
    monkeypatch.delenv("PIP_SERVER_URL", raising=False)
    hear(keeper, ANSWER, clock=clock)
    assert await report_star(keeper.judge()) is False  # not set up: skip quietly


# --- storage and endpoints -----------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits())


def award_dict(keeper, clock, text="It is brown because they paint it", context=PAGE):
    hear(keeper, text, clock=clock, context=context)
    return keeper.judge("tower color").to_dict()


def test_store_keeps_awards_once(store, keeper, clock):
    data = award_dict(keeper, clock)
    assert store.record_star(data) is True
    assert store.record_star(data) is False  # a retried report is not counted twice
    saved = store.stars_for("ipad-1")
    assert len(saved) == 1
    assert saved[0]["stars"] == 1 and saved[0]["why_how"] is True and saved[0]["book"] == "paris"
    assert "room" not in saved[0]
    assert store.stars_for("someone-else") == []


def test_store_rejects_bad_awards(store, keeper, clock):
    data = award_dict(keeper, clock)
    for bad in ({"id": "x"}, {"stars": 0}, {"stars": True}, {"stars": "2"}, {"device_id": "bad id!"}):
        with pytest.raises(ValueError):
            store.record_star({**data, **bad})


def test_summary_counts_stars(store, keeper, clock):
    store.check("ipad-1")
    store.record_star(award_dict(keeper, clock))
    store.record_star(award_dict(keeper, clock, "I would look at the big paintings", OTHER_PAGE))
    (row,) = store.summary()
    assert row["stars"] == 2


def test_store_allows_five_stars_per_page_over_all_sessions(store, keeper, clock):
    """Whatever build of the agent reports it, a page gets at most 5 stars per device, 1 per award."""
    first = award_dict(keeper, clock)
    assert store.record_star({**first, "stars": 3}) is True  # an old agent's bonus counts as 1
    for i, room in enumerate(["room-1", "room-1", "room-2", "room-3"]):
        assert store.record_star({**first, "id": f"{i:032d}", "room": room}) is True
    assert store.record_star({**first, "id": "f" * 32, "room": "room-4"}) is False  # a sixth, even in a new session
    assert store.record_star({**first, "id": "e" * 32, "device_id": "iphone-2"}) is True  # another device
    assert store.record_star({**first, "id": "d" * 32, "page": "louvre"}) is True  # another page
    assert store.record_star(first) is False  # a retry of a stored award is still not new
    assert [s["stars"] for s in store.stars_for("ipad-1")] == [1] * 6


def test_store_limit_matches_the_agent():
    from little_explorer_agent import devices, stars

    assert devices.STARS_PER_PAGE == stars.STARS_PER_PAGE == 5


def test_store_limit_is_per_local_day(store):
    """5 at 23:59 his time fill the page; at 00:01 his time it has stars again, though both are
    the same UTC day (and the same day where the server runs)."""
    base = {"device_id": "ipad-1", "room": "r", "book": "paris", "page": "eiffel", "stars": 1}
    for i in range(5):
        late = {**base, "id": f"{i:032d}", "awarded_at": "2026-10-19T23:59:00-07:00"}
        assert store.record_star(late) is True
    assert store.record_star({**base, "id": "a" * 32, "awarded_at": "2026-10-19T23:59:30-07:00"}) is False
    assert store.record_star({**base, "id": "b" * 32, "awarded_at": "2026-10-20T00:01:00-07:00"}) is True
    in_tokyo = {**base, "id": "c" * 32, "awarded_at": "2026-10-20T15:59:00+09:00"}  # 23:59 Pacific, a new Tokyo day
    assert store.record_star(in_tokyo) is True
    assert len(store.stars_for("ipad-1")) == 7


def test_stars_already_stored_are_kept(store):
    """Awards stored before the 1-per-page rule are restored as they were."""
    store.record_star({"id": "a" * 32, "device_id": "ipad-1", "room": "r", "book": "paris", "page": "eiffel", "stars": 1})
    store._run(lambda cur: store._exec(cur, "UPDATE stars SET stars = 3", ()))
    assert [s["stars"] for s in store.stars_for("ipad-1")] == [3]


@pytest.fixture
async def client(store):
    app = create_app(access_key="app-key", store=store, admin_key="admin-secret", report_key="report-secret")
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_star_endpoints(client, keeper, clock):
    data = award_dict(keeper, clock)
    assert (await client.post("/stars", json=data)).status == 401
    bad = await client.post("/stars", json=data, headers={"Authorization": "Bearer app-key"})
    assert bad.status == 401  # the app key cannot add stars
    ok = await client.post("/stars", json=data, headers={"Authorization": "Bearer report-secret"})
    assert ok.status == 200 and (await ok.json())["new"] is True
    again = await client.post("/stars", json=data, headers={"Authorization": "Bearer report-secret"})
    assert (await again.json())["new"] is False
    junk = await client.post("/stars", json={"id": "nope"}, headers={"Authorization": "Bearer report-secret"})
    assert junk.status == 400

    assert (await client.get("/stars?device=ipad-1")).status == 401
    milo = await client.get("/stars?device=ipad-1", headers={"Authorization": "Bearer report-secret"})
    assert milo.status == 200 and (await milo.json())["total"] == 1  # Milo reads the page counts too
    app = {"Authorization": "Bearer app-key"}
    assert (await client.get("/stars?device=bad id", headers=app)).status == 400
    resp = await client.get("/stars?device=ipad-1", headers=app)
    body = await resp.json()
    assert body["total"] == 1 and body["stars"][0]["id"] == data["id"]


async def test_fetch_page_stars_reads_the_server(client, keeper, clock):
    data = award_dict(keeper, clock)
    assert (await client.post("/stars", json=data, headers={"Authorization": "Bearer report-secret"})).status == 200
    url = str(client.make_url("/stars"))
    day = award_day(data["awarded_at"])
    assert await fetch_page_stars("ipad-1", (url, "report-secret")) == {("paris", "eiffel", day): 1}
    assert await fetch_page_stars("ipad-1", (url, "wrong-key")) is None
    assert await fetch_page_stars("", (url, "report-secret")) is None
