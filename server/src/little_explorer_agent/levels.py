"""Reading levels: who a book is for, how its pages are written and how Milo talks about it.

Every book has a ``level`` (1, 2 or 3; books without one are level 2, which is how all the
hand-written books read). The same three descriptions are shown to grown-ups when they make a book
(``LEVELS[n].as_json()``), drive the writing prompts and checks for generated books (see
:mod:`.bookgen`), set the narration pace, and pick Milo's talk rules (:func:`talk_rules`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_LEVEL = 2


@dataclass(frozen=True)
class Level:
    number: int
    ages: str
    name: str
    description: str
    example: str
    # Writing rules for generated pages (checked by :func:`check_page_text`).
    min_sentences: int
    max_sentences: int
    max_words_per_sentence: int
    max_words_per_page: int
    writing: str
    # How Milo talks while a book of this level is open.
    talk: str
    # Extra narration direction for generated books (level 2 keeps the hand-made books' voice).
    narration_pace: str

    @property
    def label(self) -> str:
        return f"Level {self.number}"

    def as_json(self) -> dict[str, Any]:
        """What the grown-up's "make a book" page shows for this level."""
        return {
            "level": self.number,
            "label": self.label,
            "ages": self.ages,
            "name": self.name,
            "description": self.description,
            "example": self.example,
        }


LEVELS: dict[int, Level] = {
    1: Level(
        number=1,
        ages="4–5",
        name="Just starting",
        description="One short sentence per page, easy words that repeat.",
        example="Look! A big tower.",
        min_sentences=1,
        max_sentences=1,
        max_words_per_sentence=6,
        max_words_per_page=10,
        writing=(
            "Level 1 (ages 4-5, just starting to read): one short sentence per page (a tiny opener like "
            '"Look!" may come first), at most 6 words per sentence. Use only very common, easy words '
            "a beginning reader can sound out, and repeat a few key words across pages. No commas, no "
            "hard names on the page (put names in the hidden notes)."
        ),
        talk=(
            "Talk for a beginning reader (level 1): one or two very short sentences per turn, about six "
            "words each, with easy everyday words. Say the page's key word again so it sticks. Ask easy "
            'either/or questions they can answer in a word or two ("Is it big or small?", "Would you eat '
            'the red one or the green one?").'
        ),
        narration_pace=(
            "Read slowly and clearly for a child just learning to read: a short pause between words "
            "and a longer pause at the end of the sentence."
        ),
    ),
    2: Level(
        number=2,
        ages="5–7",
        name="Early reader",
        description="One or two simple sentences, each with a fun place or food.",
        example="The Eiffel Tower sparkles every night.",
        min_sentences=1,
        max_sentences=2,
        max_words_per_sentence=12,
        max_words_per_page=22,
        writing=(
            "Level 2 (ages 5-7, early reader): one or two simple sentences per page, at most 12 words "
            "each, with everyday words and one fun place, food or thing to look at on every page."
        ),
        talk=(
            "Talk for an early reader (level 2): short, simple sentences a young child understands, two "
            "to four sentences per turn."
        ),
        narration_pace="",
    ),
    3: Level(
        number=3,
        ages="7–9",
        name="Reading alone",
        description='Two or three longer sentences, with one new "wow" word explained.',
        example="Workers paint the tower every seven years, so it never gets rusty.",
        # The example is one long sentence, so one is allowed; the prompt asks for two or three.
        min_sentences=1,
        max_sentences=3,
        max_words_per_sentence=16,
        max_words_per_page=40,
        writing=(
            "Level 3 (ages 7-9, reading alone): two or three sentences per page (or one long sentence "
            'joined with "so" or "because"), at most 16 words each. '
            'Each page may use one new "wow" word and explain it in the same sentence or the next one '
            '("rusty, which means..."). Give a surprising true fact on most pages.'
        ),
        talk=(
            "Talk for a confident reader (level 3): up to about twelve words per sentence, two to four "
            'sentences per turn. Ask "why" and "how" questions that make them think. You may use one '
            "new interesting word in a turn, and explain it right away in simple words."
        ),
        narration_pace="Read at a natural, lively storytelling pace for a confident young reader.",
    ),
}


def parse_level(value: object, default: int | None = DEFAULT_LEVEL) -> int | None:
    """1, 2 or 3 from a JSON value (``"2"`` too), else ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    return value if isinstance(value, int) and value in LEVELS else default


def level_of(value: object) -> Level:
    return LEVELS[parse_level(value) or DEFAULT_LEVEL]


def talk_rules(level: object) -> str:
    """Milo's talk rules for a book's level (level 2 when unknown or no book is open)."""
    return level_of(level).talk


def levels_json() -> list[dict[str, Any]]:
    return [LEVELS[n].as_json() for n in sorted(LEVELS)]


_SENTENCE = re.compile(r"[^.!?]+[.!?]*")
_WORD = re.compile(r"[A-Za-z0-9\u00C0-\u024F]+(?:['’-][A-Za-z0-9\u00C0-\u024F]+)*")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.findall(text or "") if _WORD.search(s)]


def words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def check_page_text(level: object, text: str) -> list[str]:
    """Deterministic problems with a generated page's text for its level (empty if fine).

    A one- or two-word exclamation ("Look!", "Wow!") is not counted as a sentence, so level 1 can say
    "Look! A big tower."
    """
    rules = level_of(level)
    found = [s for s in sentences(text) if len(words(s)) > 2 or len(sentences(text)) == 1]
    problems = []
    if len(found) < rules.min_sentences:
        problems.append(f"needs at least {rules.min_sentences} sentence(s)")
    if len(found) > rules.max_sentences:
        problems.append(f"has {len(found)} sentences; level {rules.number} allows {rules.max_sentences}")
    for sentence in found:
        n = len(words(sentence))
        if n > rules.max_words_per_sentence:
            problems.append(f'"{sentence}" has {n} words; level {rules.number} allows {rules.max_words_per_sentence}')
    total = len(words(text))
    if total > rules.max_words_per_page:
        problems.append(f"page has {total} words; level {rules.number} allows {rules.max_words_per_page}")
    return problems
