"""Book content shared with the iOS app (LittleExplorer/Resources/Books/*.json).

The app only publishes what is on screen (book, page, page_text). The richer hidden "about" notes
for each page are read here, so the buddy can answer follow-up questions without the app sending
them on every page turn.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

# Participant-attribute keys published by the app (PageContext.attributes in Book.swift).
BOOK_KEY = "book"
PAGE_KEY = "page"
PAGE_NUMBER_KEY = "page_number"
CITY_KEY = "city"
PAGE_TEXT_KEY = "page_text"

_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "LittleExplorer" / "Resources" / "Books"


@dataclass(frozen=True)
class Page:
    id: str
    text: str
    about: str
    emoji: str = ""


@dataclass(frozen=True)
class Book:
    id: str
    city: str
    country: str
    pages: tuple[Page, ...]
    theme: str = ""

    def page(self, page_id: str) -> Page | None:
        return next((p for p in self.pages if p.id == page_id), None)


def content_dir() -> Path:
    raw = os.environ.get("CONTENT_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else _DEFAULT_DIR


def _parse_book(data: Mapping[str, Any]) -> Book:
    pages = tuple(
        Page(id=p["id"], text=p["text"], about=p.get("about", ""), emoji=p.get("emoji", ""))
        for p in data.get("pages", [])
    )
    return Book(
        id=data["id"],
        city=data["city"],
        country=data.get("country", ""),
        pages=pages,
        theme=data.get("theme") or "",
    )


@lru_cache(maxsize=4)
def load_books(directory: str | None = None) -> dict[str, Book]:
    root = Path(directory) if directory else content_dir()
    books: dict[str, Book] = {}
    for path in sorted(root.glob("*.json")):
        book = _parse_book(json.loads(path.read_text(encoding="utf-8")))
        books[book.id] = book
    return books


def lookup(context: Mapping[str, Any], books: Mapping[str, Book] | None = None) -> tuple[Book | None, Page | None]:
    """The book and page the child is looking at, from the app's context."""
    books = load_books() if books is None else books
    book = books.get(str(context.get(BOOK_KEY, "")))
    if book is None:
        return None, None
    return book, book.page(str(context.get(PAGE_KEY, "")))
