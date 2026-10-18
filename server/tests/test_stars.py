"""Stars for answering Milo: the validation rules, awarding answers Milo replied to, storage and endpoints."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import agent as agent_mod
from little_explorer_agent.buddy import STAR_RULES, realtime_instructions
from little_explorer_agent.devices import DeviceStore, Limits
from little_explorer_agent.stars import (
    StarAward,
    StarKeeper,
    StarRules,
    asks_milo,
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


def test_an_answer_of_six_words_earns_one_star(keeper, clock):
    hear(keeper, "I would look at the river", clock=clock)
    award = keeper.judge("looking at the river")
    assert isinstance(award, StarAward)
    assert (award.stars, award.kind, award.book, award.page, award.city) == (1, "answer", "paris", "eiffel", "Paris")
    assert (award.long, award.why_how, award.followup, award.words) == (False, False, False, 6)
    assert award.device_id == "ipad-1" and award.reason == "looking at the river"


def test_five_words_is_too_short(keeper, clock):
    hear(keeper, "I would look at boats", clock=clock)
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
    ],
)
def test_his_own_questions_earn_nothing(keeper, clock, text):
    hear(keeper, text, clock=clock)
    assert keeper.judge() == "a question, not an answer"
    assert keeper.total() == 0


def test_no_star_unless_milo_asked_a_question(keeper, clock):
    hear(keeper, ANSWER, clock=clock, milo="The tower is very tall. It was built long ago.")
    assert keeper.judge() == "Milo didn't ask a question"
    hear(keeper, ANSWER, clock=clock, milo=None)
    assert keeper.judge() == "Milo didn't ask a question"


def test_milos_question_can_be_anywhere_in_his_reply(keeper, clock):
    hear(keeper, ANSWER, clock=clock, milo="What would you see up there? I bet it's a lot!")
    assert isinstance(keeper.judge(), StarAward)


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


def test_one_star_per_page(keeper, clock):
    hear(keeper, "I would look at the tiny cars", clock=clock)
    assert keeper.judge().stars == 1
    hear(keeper, "I would wave at all the people", clock=clock)
    assert keeper.judge() == "this page's star is taken"
    assert keeper.page_stars("paris", "eiffel") == 1
    hear(keeper, "I would look at the big paintings", context=OTHER_PAGE, clock=clock)
    assert keeper.judge().stars == 1  # a new page has its own star
    assert keeper.total() == 2


def test_a_page_turn_star_is_not_refused_as_too_soon(keeper, clock):
    hear(keeper, ANSWER, clock=clock)
    assert isinstance(keeper.judge(), StarAward)
    hear(keeper, "I would look at the big paintings", context=OTHER_PAGE, clock=clock, gap=2.0)
    assert isinstance(keeper.judge(), StarAward)


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


def test_reasons_are_short_and_single_line(keeper, clock):
    hear(keeper, ANSWER, clock=clock)
    award = keeper.judge("a\nvery " + "long " * 40)
    assert "\n" not in award.reason and len(award.reason) <= 80


def test_rules_from_env():
    rules = StarRules.from_env({"PIP_STAR_COOLDOWN_S": "3", "PIP_STAR_LONG_WORDS": "10", "PIP_STAR_MIN_WORDS": "4"})
    assert (rules.cooldown_s, rules.long_words, rules.min_words) == (3.0, 10, 4)
    assert StarRules.from_env({}) == StarRules()
    assert StarRules().cooldown_s == 0 and StarRules().min_words == 6


def test_min_words_is_configurable(clock):
    keeper = StarKeeper(clock=clock, rules=StarRules(min_words=4))
    hear(keeper, "I like the boats", clock=clock)
    assert isinstance(keeper.judge(), StarAward)


def test_status_line_and_instructions(keeper, clock):
    assert "still waiting" in keeper.status_line(PAGE) and "at least 6 words" in keeper.status_line(PAGE)
    hear(keeper, ANSWER, clock=clock)
    keeper.judge()
    assert "Don't mention stars" in keeper.status_line(PAGE)
    assert "still waiting" in keeper.status_line(OTHER_PAGE)
    assert "Don't mention stars" in keeper.status_line({})
    text = realtime_instructions(PAGE, stars=keeper.status_line(PAGE))
    assert STAR_RULES in text and "Great answer, that's a star!" in text
    assert "Great question" not in text
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
    assert refreshed == [True]  # the page's one star is taken, so the model's instructions change


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


async def test_realtime_his_question_earns_nothing(clock):
    _, game = new_game(clock)
    ask(game, clock, "Why do they paint the tower brown every time?")
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


async def test_pipeline_awards_answers_directly(clock):
    ctx = FakeCtx()

    async def report(award):
        return True

    game = agent_mod.StarGame(ctx, StarKeeper(clock=clock), report=report)
    clock.t += 20
    game.pipeline_decision(turn(ANSWER), TurnDecision.RESPOND)
    game.pipeline_decision(turn("The tower is brown."), TurnDecision.LISTEN)
    game.pipeline_decision(turn("I would look at the river and the boats"), TurnDecision.RESPOND)
    await game.drain()
    assert game.keeper.total() == 1 and len(ctx.room.local_participant.sent) == 1


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


def test_store_allows_one_star_per_page_per_session(store, keeper, clock):
    """Whatever build of the agent reports it, a page gets one star per session, and one star."""
    first = award_dict(keeper, clock)
    assert store.record_star({**first, "stars": 3}) is True  # an old agent's bonus counts as 1
    second = {**first, "id": "b" * 32, "stars": 2}
    assert store.record_star(second) is False  # the page already has its star in this room
    assert store.record_star({**second, "id": "c" * 32, "room": "room-2"}) is True  # a new session
    assert [s["stars"] for s in store.stars_for("ipad-1")] == [1, 1]


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
    app = {"Authorization": "Bearer app-key"}
    assert (await client.get("/stars?device=bad id", headers=app)).status == 400
    resp = await client.get("/stars?device=ipad-1", headers=app)
    body = await resp.json()
    assert body["total"] == 1 and body["stars"][0]["id"] == data["id"]
