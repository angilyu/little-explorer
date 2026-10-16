"""Pip, the reading buddy: persona, turn gate, per-page context note and the reply graph."""

from __future__ import annotations

import inspect
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from voice_kit import (
    TurnDecision,
    UserTurn,
    init_chat_model_from_spec,
    is_question,
    llm_gate,
    text_overlap,
)

from .content import BOOK_KEY, CITY_KEY, PAGE_KEY, PAGE_NUMBER_KEY, PAGE_TEXT_KEY, lookup

log = logging.getLogger(__name__)

REPLY_MODEL = os.environ.get("PIP_LLM_MODEL", "openai:gpt-5.4-mini@none")
GATE_MODEL = os.environ.get("PIP_GATE_MODEL", "openai:gpt-5.4-nano@none")
MAX_HISTORY = 16

GREETING = "Hi, I'm Pip! You can read the page to me, or ask me anything you wonder about."

PERSONA = """\
You are Pip, a warm, playful travel buddy inside a picture book app called Little Explorer.
You talk with a 6-year-old boy who is learning about cities around the world so he gets excited
about travel. Everything you say is spoken aloud.

How to talk:
- Use short, simple sentences a 6-year-old understands. Two to four sentences per turn.
- Be warm, curious and encouraging. Be very patient; never rush him.
- End almost every answer with one open, curious question that invites him to say more
  ("What do you think...?", "If you went there, what would you do first?").
- Help him speak in longer sentences: when he says a few words, gently echo them back as a full
  sentence ("Oh, you think the tower is tall like a giraffe!") before you answer.
- Use the hidden page notes to answer. If you are not sure, say so and wonder together.
- Plain words only: no lists, no emoji, no markdown, no spelling out symbols.

Safety:
- Keep everything kind and age-appropriate. Steer away from scary or grown-up topics gently.
- Never ask for his name, age, address, school, photos or any personal details.
- If he asks about something unrelated to the book, cities, countries or travel, say kindly
  that you love talking about the book and bring him back with a fun question about the page.
"""

GATE_PURPOSE = """\
Pip is a voice buddy in a picture book app for a 6-year-old boy, about cities and travel.
RESPOND when the child talks to Pip: questions or comments about the book, the page, the photo,
the city, its country, language, food or famous places, about travel, or a short reply to Pip's
last question (even "yes", "I like the boat"), or when he asks Pip something directly.
LISTEN when he is reading the page sentence aloud or is clearly in the middle of a thought.
IGNORE adults talking to each other, TV or music, phone calls, speech that is not directed at
Pip, and noise or mumbles that are not words.
Signs it is grown-ups, not the child talking to Pip (IGNORE): work words (meeting, deck, launch,
deadline, project, team, email, planning), chores and errands, traffic, money, news, talking
about other people ("he said", "your mom"), long run-on speech, or anything a 6-year-old would
not say to a picture book. A word like "city" or "bridge" alone does not make it about the book.
When unsure whether a child's question is about the book, prefer RESPOND; when unsure whether it
is a child talking to Pip at all, prefer IGNORE."""


def _words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[\w']+", text or "")]


# Words that mark a question or a request to Pip rather than reading. Speech-to-text often drops
# the "?" and splits "Pip, why ..." into pieces, so look for them anywhere in the turn.
ASKING_WORDS = frozenset({"pip", "why", "what", "how", "where", "who", "when", "which", "tell", "wonder"})


class ReadingTracker:
    """Notices when the child reads the page sentence aloud.

    Reading is LISTENed to (kept in history, no reply), even when it comes in slow pieces split by
    long pauses. Once he has read most of the page, the turn is answered once with praise.
    """

    def __init__(self, *, threshold: float = 0.6, finish_coverage: float = 0.75, min_words: int = 2) -> None:
        self.threshold = threshold
        self.finish_coverage = finish_coverage
        self.min_words = min_words
        self._covered: dict[str, set[str]] = {}
        self._praised: set[str] = set()
        self._pending_praise: str | None = None

    @staticmethod
    def _page_key(context: Mapping[str, Any]) -> str:
        return f"{context.get(BOOK_KEY, '')}/{context.get(PAGE_KEY, '')}"

    def check(self, turn: UserTurn) -> TurnDecision | None:
        """LISTEN while reading, RESPOND when the page is finished, ``None`` if this is not reading."""
        reference = str(turn.context.get(PAGE_TEXT_KEY) or "")
        spoken = _words(turn.text)
        ref_list = _words(reference)
        if not ref_list or not spoken:
            return None
        key = self._page_key(turn.context)
        covered = self._covered.get(key, set())
        # A lone word only counts when he is already part-way through the page ("...bonjour").
        if len(spoken) < self.min_words and not covered:
            return None
        ref_words = set(ref_list)
        if is_question(turn.text) and spoken[0] not in ref_words:
            return None
        if any(word in ASKING_WORDS and word not in ref_words for word in spoken):
            return None
        if text_overlap(turn.text, reference) < self.threshold:
            return None
        covered = self._covered.setdefault(key, set())
        covered |= ref_words.intersection(spoken)
        coverage = len(covered) / len(ref_words)
        reached_end = ref_list[-1] in spoken
        if coverage >= self.finish_coverage and reached_end and key not in self._praised:
            self._praised.add(key)
            self._pending_praise = key
            return TurnDecision.RESPOND
        return TurnDecision.LISTEN

    def take_praise(self, context: Mapping[str, Any]) -> bool:
        """True once, for the reply right after he finished reading the current page."""
        key, self._pending_praise = self._pending_praise, None
        return key is not None and key == self._page_key(context)


TopicGate = Callable[[UserTurn], "TurnDecision | Awaitable[TurnDecision]"]


def make_gate(tracker: ReadingTracker, topic_gate: TopicGate | None) -> Callable[[UserTurn], Awaitable[TurnDecision]]:
    """Reading aloud -> LISTEN (praise when done), noise -> IGNORE, everything else -> topic gate.

    The topic gate may only RESPOND or IGNORE; its LISTEN is treated as IGNORE.
    """

    async def gate(turn: UserTurn) -> TurnDecision:
        reading = tracker.check(turn)
        if reading is not None:
            return reading
        words = _words(turn.text)
        if not words or (len(words) < 2 and not is_question(turn.text)):
            return TurnDecision.IGNORE
        if topic_gate is None:
            return TurnDecision.RESPOND
        result = topic_gate(turn)
        if inspect.isawaitable(result):
            result = await result
        # Only his own reading is worth keeping quietly; other talk the classifier is unsure about
        # (adults chatting, TV) is dropped so Pip never brings it up later.
        return TurnDecision.IGNORE if result is TurnDecision.LISTEN else result

    return gate


def default_topic_gate() -> TopicGate:
    return llm_gate(
        init_chat_model_from_spec(GATE_MODEL),
        GATE_PURPOSE,
        context_keys=[CITY_KEY, PAGE_NUMBER_KEY, PAGE_TEXT_KEY],
    )


def make_context_note(tracker: ReadingTracker) -> Callable[[Mapping[str, Any]], str | None]:
    """System note for each reply: what is on screen plus the hidden notes for this page."""

    def note(context: Mapping[str, Any]) -> str | None:
        book, page = lookup(context)
        city = book.city if book else context.get(CITY_KEY)
        page_text = page.text if page else context.get(PAGE_TEXT_KEY)
        if not city and not page_text:
            return None
        lines = ["What the child is looking at right now:"]
        if city:
            country = f", {book.country}" if book and book.country else ""
            theme = f' (the "{book.theme}" book)' if book and book.theme else ""
            lines.append(f"- Book: {city}{country}{theme}")
        if context.get(PAGE_NUMBER_KEY):
            lines.append(f"- Page {context[PAGE_NUMBER_KEY]}")
        if page_text:
            lines.append(f'- The sentence on the page: "{page_text}"')
        if page and page.about:
            lines.append(f"- Hidden notes for you (he cannot see these): {page.about}")
        if tracker.take_praise(context):
            lines.append(
                "He just read this page aloud all by himself. Praise his reading warmly and "
                "specifically in one short sentence, then ask one easy, curious question about the page."
            )
        return "\n".join(lines)

    return note


def _trim(messages: list[Any]) -> list[Any]:
    system = [m for m in messages if getattr(m, "type", None) == "system"]
    rest = [m for m in messages if getattr(m, "type", None) != "system"]
    return system + rest[-MAX_HISTORY:]


def build_graph(model: Any = None) -> Any:
    """A one-node LangGraph graph that streams Pip's spoken reply."""
    from langgraph.config import get_stream_writer
    from langgraph.graph import START, MessagesState, StateGraph
    from voice_kit import message_text

    chat = model if model is not None else init_chat_model_from_spec(REPLY_MODEL)

    async def reply(state: MessagesState) -> dict[str, Any]:
        writer = get_stream_writer()
        text = ""
        async for chunk in chat.astream(_trim(list(state["messages"]))):
            piece = message_text(chunk)
            if piece:
                writer(piece)
                text += piece
        return {"messages": [{"role": "assistant", "content": text}]}

    return StateGraph(MessagesState).add_node("reply", reply).add_edge(START, "reply").compile()
