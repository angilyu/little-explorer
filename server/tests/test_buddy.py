import pytest

from little_explorer_agent.buddy import GREETING, PERSONA, ReadingTracker, make_context_note, make_gate
from little_explorer_agent.content import load_books
from little_explorer_agent.voice import TurnDecision, UserTurn

PARIS = load_books()["paris"]
PAGE = PARIS.pages[0]
CTX = {"book": "paris", "page": PAGE.id, "page_number": "1", "city": "Paris", "page_text": PAGE.text}


class FakeTopicGate:
    def __init__(self, decision=TurnDecision.RESPOND):
        self.decision = decision
        self.calls = []

    async def __call__(self, turn):
        self.calls.append(turn.text)
        return self.decision


def turn(text, context=CTX):
    return UserTurn(text=text, context=context, history=[])


@pytest.fixture
def setup():
    tracker = ReadingTracker()
    topic = FakeTopicGate()
    return tracker, topic, make_gate(tracker, topic)


async def test_noise_and_single_words_are_ignored(setup):
    _, topic, gate = setup
    assert await gate(turn("")) is TurnDecision.IGNORE
    assert await gate(turn("um")) is TurnDecision.IGNORE
    assert topic.calls == []


async def test_one_word_question_still_goes_to_topic_gate(setup):
    _, topic, gate = setup
    assert await gate(turn("Why?")) is TurnDecision.RESPOND
    assert topic.calls == ["Why?"]


async def test_slow_reading_in_pieces_is_listened_to_then_praised_once(setup):
    tracker, topic, gate = setup
    # "This is Paris. Paris is a big city in France." read slowly with long pauses.
    assert await gate(turn("this is Paris")) is TurnDecision.LISTEN
    assert await gate(turn("Paris is a big")) is TurnDecision.LISTEN
    assert await gate(turn("city in")) is TurnDecision.LISTEN  # nearly done, but not at the end yet
    assert await gate(turn("France")) is TurnDecision.RESPOND
    note = make_context_note(tracker)(CTX)
    assert "read this page aloud" in note
    # Praise only once per page, and the hint does not leak into later answers.
    assert "read this page aloud" not in make_context_note(tracker)(CTX)
    assert await gate(turn("this is Paris Paris is a big city in France")) is TurnDecision.LISTEN
    assert topic.calls == []


async def test_lone_word_before_reading_starts_is_noise(setup):
    _, topic, gate = setup
    assert await gate(turn("Paris")) is TurnDecision.IGNORE
    assert topic.calls == []


async def test_questions_about_the_page_are_not_mistaken_for_reading(setup):
    _, topic, gate = setup
    assert await gate(turn("how big is Paris")) is TurnDecision.RESPOND
    assert topic.calls == ["how big is Paris"]


async def test_question_echoing_page_words_without_question_mark_is_not_reading():
    bonjour = PARIS.pages[1]
    ctx = {**CTX, "page": bonjour.id, "page_number": "2", "page_text": bonjour.text}
    topic = FakeTopicGate()
    gate = make_gate(ReadingTracker(), topic)
    # Speech-to-text lost the "?" and split the question; most words are on the page.
    text = "Pip Why do people in Paris say Bonjour."
    assert await gate(turn(text, ctx)) is TurnDecision.RESPOND
    assert topic.calls == [text]


async def test_off_topic_talk_follows_topic_gate():
    tracker = ReadingTracker()
    gate = make_gate(tracker, FakeTopicGate(TurnDecision.IGNORE))
    assert await gate(turn("did you pick up the milk on the way home")) is TurnDecision.IGNORE


async def test_topic_gate_listen_is_dropped_not_kept():
    gate = make_gate(ReadingTracker(), FakeTopicGate(TurnDecision.LISTEN))
    assert await gate(turn("Not yet, I'll do it after dinner.")) is TurnDecision.IGNORE


async def test_reading_progress_is_per_page(setup):
    tracker, _, gate = setup
    other = PARIS.pages[1]
    ctx2 = {**CTX, "page": other.id, "page_number": "2", "page_text": other.text}
    assert await gate(turn("this is Paris Paris is a big city in France")) is TurnDecision.RESPOND
    assert tracker.take_praise(ctx2) is False


def test_context_note_includes_hidden_notes_for_current_page():
    note = make_context_note(ReadingTracker())(CTX)
    assert "Paris, France" in note
    assert PAGE.text in note
    assert PAGE.about in note


def test_context_note_names_the_themed_book():
    food = load_books()["paris-food"]
    page = food.page("crepes")
    ctx = {"book": food.id, "page": page.id, "page_number": "5", "city": "Paris", "page_text": page.text}
    note = make_context_note(ReadingTracker())(ctx)
    assert 'Paris, France (the "Yummy Paris" book)' in note
    assert page.about in note


def test_context_note_without_context_is_empty():
    assert make_context_note(ReadingTracker())({}) is None


def test_persona_guards():
    assert "personal" in PERSONA
    assert "question" in PERSONA
    assert "Pip" in GREETING
