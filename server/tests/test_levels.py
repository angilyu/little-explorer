"""Reading levels: descriptions, page checks, Milo's talk rules and the level on every book."""

import json

import pytest

from little_explorer_agent import buddy, content, speech
from little_explorer_agent.catalog import resources_dir
from little_explorer_agent.content import _parse_book
from little_explorer_agent.levels import (
    DEFAULT_LEVEL,
    LEVELS,
    check_page_text,
    level_of,
    levels_json,
    parse_level,
    talk_rules,
)


def test_level_descriptions():
    assert [(lv["level"], lv["ages"], lv["name"]) for lv in levels_json()] == [
        (1, "4–5", "Just starting"),
        (2, "5–7", "Early reader"),
        (3, "7–9", "Reading alone"),
    ]
    assert levels_json()[0]["example"] == "Look! A big tower."
    assert levels_json()[1]["example"] == "The Eiffel Tower sparkles every night."
    assert levels_json()[2]["example"] == "Workers paint the tower every seven years, so it never gets rusty."


def test_each_example_fits_its_own_level():
    for number, level in LEVELS.items():
        assert check_page_text(number, level.example) == [], number


def test_parse_level():
    assert parse_level(1) == 1 and parse_level("3") == 3 and parse_level(" 2 ") == 2
    assert parse_level(None) == DEFAULT_LEVEL and parse_level(7) == DEFAULT_LEVEL and parse_level(True) == DEFAULT_LEVEL
    assert parse_level("x", default=None) is None
    assert level_of(None).number == 2


@pytest.mark.parametrize(
    ("level", "text", "ok"),
    [
        (1, "Look! A big tower.", True),
        (1, "The tower is big. It is tall.", False),
        (1, "The very tall tower stands in the busy city.", False),
        (2, "The Eiffel Tower sparkles every night.", True),
        (2, "The tower is tall. It sparkles at night. It is in Paris.", False),
        (3, "Workers paint the tower. It never gets rusty.", True),
        (3, "Workers paint the tower every seven years.", True),
        (3, "The tower is tall. It is in Paris. It sparkles at night. People love it.", False),
        (3, "Workers climb up the tall iron tower with big brushes and many heavy buckets of brown paint every year.", False),
    ],
)
def test_check_page_text(level, text, ok):
    assert (check_page_text(level, text) == []) is ok


def test_talk_rules_differ_by_level():
    assert "either/or" in talk_rules(1)
    assert "why" in talk_rules(3) and "new interesting word" in talk_rules(3)
    assert talk_rules(None) == talk_rules(2)


def test_milo_uses_the_open_books_level(monkeypatch):
    book = _parse_book({"id": "tiny", "city": "Paris", "level": 1, "pages": [{"id": "p01", "text": "Look! A big tower."}]})
    monkeypatch.setattr(content, "_remote_books", {"tiny": book})
    note = "\n".join(buddy.page_note({"book": "tiny", "page": "p01"}))
    assert talk_rules(1) in note
    assert talk_rules(2) in buddy.realtime_instructions({})


def test_persona_is_neutral():
    for text in (buddy.PERSONA, speech.__dict__.get("INSTRUCTIONS", ""), buddy.realtime_instructions({})):
        lowered = text.lower()
        assert "6-year-old" not in lowered and "boy" not in lowered.split() and "six-year-old" not in lowered


def test_every_hand_made_book_is_level_2():
    books = sorted((resources_dir() / "Books").glob("*.json"))
    assert books
    for path in books:
        data = json.loads(path.read_text())
        assert data.get("level") == 2, path.name
        assert _parse_book(data).level == 2
