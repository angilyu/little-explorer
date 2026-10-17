"""Stars for curious questions: the validation rules, the give_star tool, storage and endpoints."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import agent as agent_mod
from little_explorer_agent.buddy import STAR_RULES, realtime_instructions
from little_explorer_agent.devices import DeviceStore, Limits
from little_explorer_agent.stars import StarAward, StarKeeper, StarRules, is_small_talk, report_star, star_payload
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


def hear(keeper, text, decision=TurnDecision.RESPOND, context=PAGE, clock=None, gap=12.0):
    """He speaks, the guard decides; ``gap`` seconds pass first so the cooldown is over."""
    if clock is not None:
        clock.t += gap
    keeper.speech_started()
    if clock is not None:
        clock.t += 1.0
    keeper.heard(UserTurn(text=text, context=dict(context)), decision)


# --- the rules ---------------------------------------------------------------------------------


def test_a_real_question_earns_one_star(keeper, clock):
    hear(keeper, "What is on top of the tower?", clock=clock)
    award = keeper.judge("question", "what is at the top")
    assert isinstance(award, StarAward)
    assert (award.stars, award.kind, award.book, award.page, award.city) == (1, "question", "paris", "eiffel", "Paris")
    assert (award.long, award.why_how, award.followup, award.words) == (False, False, False, 7)
    assert award.device_id == "ipad-1" and award.reason == "what is at the top"


def test_why_and_how_questions_earn_a_bonus(keeper, clock):
    hear(keeper, "Why is it brown?", clock=clock)
    assert keeper.judge("question").stars == 2


def test_long_questions_earn_a_bonus(keeper, clock):
    hear(keeper, "What do the people at the very top of the tower see?", clock=clock)
    award = keeper.judge("question")
    assert award.long and not award.why_how and award.stars == 2


def test_long_why_question_gets_one_bonus_not_two(keeper, clock):
    hear(keeper, "Why do they paint the whole big tower again every seven years?", clock=clock)
    assert keeper.judge("question").stars == 2


def test_follow_up_bonus_needs_an_earlier_star_on_the_page(keeper, clock):
    hear(keeper, "What is at the top?", clock=clock)
    first = keeper.judge("follow_up")
    assert first.stars == 1 and not first.followup  # nothing to follow up yet
    hear(keeper, "Who lives up there?", clock=clock)
    second = keeper.judge("follow_up")
    assert second.followup and second.kind == "follow_up" and second.stars == 2


def test_at_most_three_stars_per_page(keeper, clock):
    hear(keeper, "Why is the tower so tall?", clock=clock)
    assert keeper.judge("question").stars == 2
    hear(keeper, "How do people go up there?", clock=clock)
    assert keeper.judge("follow_up").stars == 1  # 3 earned, trimmed to the one left
    hear(keeper, "What is the tower made of?", clock=clock)
    assert keeper.judge("question") == "this page has all its stars"
    assert keeper.page_stars("paris", "eiffel") == 3
    hear(keeper, "What is inside the museum?", context=OTHER_PAGE, clock=clock)
    assert isinstance(keeper.judge("question"), StarAward)  # a new page has new stars


def test_cooldown_between_stars(keeper, clock):
    hear(keeper, "What is at the top?", clock=clock)
    assert isinstance(keeper.judge("question"), StarAward)
    hear(keeper, "What color is it at night?", clock=clock, gap=2.0)
    assert keeper.judge("question") == "too soon after the last star"
    clock.t += 10
    hear(keeper, "What color is it in the day?", clock=clock)
    assert isinstance(keeper.judge("question"), StarAward)


def test_one_award_per_question(keeper, clock):
    hear(keeper, "What is at the top?", clock=clock)
    assert isinstance(keeper.judge("question"), StarAward)
    clock.t += 30
    assert keeper.judge("question") == "no new question heard"


@pytest.mark.parametrize(
    ("text", "decision", "reason"),
    [
        ("The Eiffel Tower is in Paris.", TurnDecision.LISTEN, "that was reading or not meant for Milo"),
        ("Did you call the plumber about the sink?", TurnDecision.IGNORE, "that was reading or not meant for Milo"),
        ("Hi Milo", TurnDecision.RESPOND, "too short to be a question"),
        ("Hello Milo, how are you?", TurnDecision.RESPOND, "small talk"),
        ("I like the big tower a lot", TurnDecision.RESPOND, "not a question"),
    ],
)
def test_reading_noise_greetings_and_statements_earn_nothing(keeper, clock, text, decision, reason):
    hear(keeper, text, decision, clock=clock)
    assert keeper.judge("question") == reason
    assert keeper.total() == 0


def test_no_star_without_a_heard_question(keeper):
    assert keeper.judge("question") == "no new question heard"


def test_an_old_question_cannot_earn_a_star(keeper, clock):
    hear(keeper, "What is at the top?", clock=clock)
    clock.t += 25
    assert keeper.judge("question") == "no new question heard"


def test_no_star_before_a_book_is_open(keeper, clock):
    hear(keeper, "What is at the top of it?", context={}, clock=clock)
    assert keeper.judge("question") == "no book open"


def test_question_words_without_a_question_mark_count(keeper, clock):
    hear(keeper, "I wonder why it is brown", clock=clock)
    assert keeper.judge("question").stars == 2


def test_small_talk():
    assert is_small_talk("Hey Milo, how are you doing?")
    assert is_small_talk("Milo, are you there?")
    assert not is_small_talk("How are you supposed to climb it?")


def test_reasons_are_short_and_single_line(keeper, clock):
    hear(keeper, "What is at the top?", clock=clock)
    award = keeper.judge("question", "a\nvery " + "long " * 40)
    assert "\n" not in award.reason and len(award.reason) <= 80


def test_rules_from_env():
    rules = StarRules.from_env({"PIP_STAR_PAGE_CAP": "5", "PIP_STAR_COOLDOWN_S": "3", "PIP_STAR_LONG_WORDS": "10"})
    assert (rules.page_cap, rules.cooldown_s, rules.long_words) == (5, 3.0, 10)
    assert StarRules.from_env({}) == StarRules()


def test_status_line_and_instructions(keeper, clock):
    assert "still has stars" in keeper.status_line(PAGE)
    for text in ("Why is it so tall?", "How do you get up?"):
        hear(keeper, text, clock=clock)
        keeper.judge("question")
    assert "Do not call give_star" in keeper.status_line(PAGE)
    assert "still has stars" in keeper.status_line(OTHER_PAGE)
    assert "Do not call give_star" in keeper.status_line({})
    text = realtime_instructions(PAGE, stars=keeper.status_line(PAGE))
    assert STAR_RULES in text and "Great question, that's a star!" in text
    assert "give_star" not in realtime_instructions(PAGE)


def test_payload_has_no_transcript(keeper, clock):
    hear(keeper, "Why is the tower brown?", clock=clock)
    award = keeper.judge("question", "tower color")
    data = json.loads(star_payload(award, 2))
    assert data["session_total"] == 2 and data["stars"] == 2 and "room" not in data
    assert "brown" not in json.dumps(data)


# --- waiting for the transcript ------------------------------------------------------------------


async def test_claim_waits_for_the_turns_transcript():
    keeper = StarKeeper(rules=StarRules(wait_s=2.0))
    keeper.speech_started()  # he is talking; the transcript is not in yet

    async def later():
        await asyncio.sleep(0.05)
        keeper.heard(UserTurn(text="Why is the tower brown?", context=PAGE), TurnDecision.RESPOND)

    task = asyncio.ensure_future(later())
    award = await keeper.claim("question")
    await task
    assert isinstance(award, StarAward) and award.stars == 2


async def test_claim_gives_up_when_no_transcript_comes():
    keeper = StarKeeper(rules=StarRules(wait_s=0.05))
    keeper.speech_started()
    assert await keeper.claim("question") == "no new question heard"


async def test_claim_judges_the_new_turn_not_the_old_one():
    keeper = StarKeeper(rules=StarRules(wait_s=1.0, cooldown_s=0))
    keeper.heard(UserTurn(text="What is at the top?", context=PAGE), TurnDecision.RESPOND)
    keeper.speech_started()

    async def later():
        await asyncio.sleep(0.05)
        keeper.heard(UserTurn(text="Eiffel Tower is tall", context=PAGE), TurnDecision.LISTEN)

    task = asyncio.ensure_future(later())
    assert await keeper.claim("question") == "that was reading or not meant for Milo"
    await task


# --- the tool ----------------------------------------------------------------------------------


async def _yes():
    return True


async def _no():
    return False


async def test_give_star_hands_the_award_on_and_stays_quiet(keeper, clock):
    hear(keeper, "Why is it brown?", clock=clock)
    got = []
    result = await agent_mod.give_star_result(keeper, "question", "color", got.append, _yes)
    assert len(got) == 1 and got[0].stars == 2
    assert result.reply_required is False and "Star given" in result.output


async def test_give_star_rejected_tells_the_model_not_to_mention_it(keeper, clock):
    hear(keeper, "Hi Milo", clock=clock)
    got = []
    result = await agent_mod.give_star_result(keeper, "question", "", got.append, _yes)
    assert got == [] and result.reply_required is False and "No star" in result.output


async def test_give_star_alone_still_gets_him_an_answer(keeper, clock):
    hear(keeper, "Why is it brown?", clock=clock)
    result = await agent_mod.give_star_result(keeper, "question", "", lambda a: None, _no)
    assert result.reply_required is True and "answer" in result.output


async def test_give_star_alone_never_waits_long_for_the_check(keeper):
    keeper.rules = StarRules(wait_s=5.0)
    keeper.speech_started()  # the transcript hasn't arrived yet
    got = []
    started = asyncio.get_running_loop().time()
    result = await agent_mod.give_star_result(keeper, "question", "", got.append, _no, quick_s=0.05)
    assert asyncio.get_running_loop().time() - started < 1.0
    assert result.reply_required is True and "Don't mention stars" in result.output
    keeper.heard(UserTurn(text="Why is the tower brown?", context=PAGE), TurnDecision.RESPOND)
    for _ in range(50):
        if got:
            break
        await asyncio.sleep(0.01)
    assert len(got) == 1, "a late but real star still reaches the app"


async def test_give_star_award_survives_the_tool_being_cancelled(keeper):
    keeper.rules = StarRules(wait_s=5.0)
    keeper.speech_started()
    got = []

    async def slow_playout():
        await asyncio.sleep(10)
        return True

    tool = asyncio.ensure_future(agent_mod.give_star_result(keeper, "question", "", got.append, slow_playout))
    await asyncio.sleep(0.01)
    tool.cancel()  # he barged in
    keeper.heard(UserTurn(text="Why is the tower brown?", context=PAGE), TurnDecision.RESPOND)
    for _ in range(50):
        if got:
            break
        await asyncio.sleep(0.01)
    assert len(got) == 1


async def test_give_star_treats_unknown_kinds_as_questions(keeper, clock):
    hear(keeper, "Why is it brown?", clock=clock)
    got = []
    await agent_mod.give_star_result(keeper, None, None, got.append, _yes)
    assert got[0].kind == "question" and got[0].reason == ""


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
    hear(game.keeper, "Why is it brown?", clock=clock)
    game.award(game.keeper.judge("question"))
    await game.drain()
    payload, reliable, topic = ctx.room.local_participant.sent[0]
    assert topic == "milo.star" and reliable and payload["stars"] == 2 and payload["session_total"] == 2
    assert reported[0].stars == 2 and refreshed == []
    hear(game.keeper, "How do you climb it?", clock=clock)
    game.award(game.keeper.judge("question"))
    await game.drain()
    assert refreshed == [True]  # the page is full now, so the model's instructions change


async def test_pipeline_awards_answered_questions_directly(clock):
    ctx = FakeCtx()

    async def report(award):
        return True

    game = agent_mod.StarGame(ctx, StarKeeper(clock=clock), report=report)
    clock.t += 20
    game.pipeline_decision(UserTurn(text="Why is it brown?", context=PAGE), TurnDecision.RESPOND)
    game.pipeline_decision(UserTurn(text="The tower is brown.", context=PAGE), TurnDecision.LISTEN)
    await game.drain()
    assert game.keeper.total() == 2 and len(ctx.room.local_participant.sent) == 1


def test_speaking_marks_a_turn_pending(clock):
    game = agent_mod.StarGame(None, StarKeeper(clock=clock))
    handlers = {}
    session = type("S", (), {"on": lambda self, name, fn: handlers.__setitem__(name, fn)})()
    game.watch(session)
    game.keeper.heard(UserTurn(text="hello there you", context=PAGE), TurnDecision.RESPOND)
    clock.t += 1
    assert not game.keeper.pending()
    handlers["user_state_changed"](type("E", (), {"new_state": "speaking"})())
    assert game.keeper.pending()


async def test_report_star_goes_to_the_stars_endpoint(monkeypatch, keeper, clock):
    assert report_target({"PIP_SERVER_URL": "https://x.example/", "PIP_REPORT_KEY": "k"}, path="stars") == (
        "https://x.example/stars",
        "k",
    )
    monkeypatch.delenv("PIP_SERVER_URL", raising=False)
    hear(keeper, "Why is it brown?", clock=clock)
    assert await report_star(keeper.judge("question")) is False  # not set up: skip quietly


# --- storage and endpoints -----------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits())


def award_dict(keeper, clock, text="Why is it brown?", context=PAGE):
    hear(keeper, text, clock=clock, context=context)
    return keeper.judge("question", "tower color").to_dict()


def test_store_keeps_awards_once(store, keeper, clock):
    data = award_dict(keeper, clock)
    assert store.record_star(data) is True
    assert store.record_star(data) is False  # a retried report is not counted twice
    saved = store.stars_for("ipad-1")
    assert len(saved) == 1
    assert saved[0]["stars"] == 2 and saved[0]["why_how"] is True and saved[0]["book"] == "paris"
    assert "room" not in saved[0]
    assert store.stars_for("someone-else") == []


def test_store_rejects_bad_awards(store, keeper, clock):
    data = award_dict(keeper, clock)
    for bad in ({"id": "x"}, {"stars": 9}, {"stars": True}, {"device_id": "bad id!"}):
        with pytest.raises(ValueError):
            store.record_star({**data, **bad})


def test_summary_counts_stars(store, keeper, clock):
    store.check("ipad-1")
    store.record_star(award_dict(keeper, clock))
    store.record_star(award_dict(keeper, clock, "What is in it?", OTHER_PAGE))
    (row,) = store.summary()
    assert row["stars"] == 3


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
    assert body["total"] == 2 and body["stars"][0]["id"] == data["id"]
