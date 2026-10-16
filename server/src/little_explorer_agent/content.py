"""Book content shared with the iOS app (LittleExplorer/Resources/Books/*.json).

The app only publishes what is on screen (book, page, page_text). The richer hidden "about" notes
for each page are read here, so the buddy can answer follow-up questions without the app sending
them on every page turn.

With ``PIP_CONTENT_URL`` set (the token server's ``/content``), :func:`refresh_remote` fetches the
current books before each session, so book edits reach Pip without redeploying the agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
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

log = logging.getLogger("little_explorer.content")

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


_remote_books: dict[str, Book] | None = None
_remote_version: str | None = None
_remote_checked = 0.0


def current_books() -> dict[str, Book]:
    """Books from the content server when fetched, else the ones on disk."""
    return _remote_books if _remote_books is not None else load_books()


async def refresh_remote(base_url: str | None = None, *, max_age: float = 60.0, timeout: float = 8.0) -> bool:
    """Fetch the books from the content server if they changed. Returns True when updated.

    Failures are logged and leave the current books in place.
    """
    global _remote_books, _remote_version, _remote_checked
    base = (base_url if base_url is not None else os.environ.get("PIP_CONTENT_URL", "")).strip().rstrip("/")
    if not base or time.monotonic() - _remote_checked < max_age:
        return False
    from aiohttp import ClientSession, ClientTimeout

    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as session:
            async with session.get(f"{base}/manifest.json") as resp:
                resp.raise_for_status()
                manifest = await resp.json()
            _remote_checked = time.monotonic()
            if manifest.get("version") == _remote_version:
                return False
            paths = [p for p in manifest.get("files", {}) if p.startswith("Books/") and p.endswith(".json")]

            async def fetch(path: str) -> Book:
                async with session.get(f"{base}/{path}") as r:
                    r.raise_for_status()
                    return _parse_book(json.loads(await r.read()))

            books = await asyncio.gather(*(fetch(p) for p in paths))
    except Exception as exc:  # noqa: BLE001 - keep the books we have
        log.warning("could not fetch books from %s: %s", base, exc)
        return False
    _remote_books = {b.id: b for b in books}
    _remote_version = manifest.get("version")
    log.info("books from %s, version %s: %s", base, _remote_version, sorted(_remote_books))
    return True


def lookup(context: Mapping[str, Any], books: Mapping[str, Book] | None = None) -> tuple[Book | None, Page | None]:
    """The book and page the child is looking at, from the app's context."""
    books = current_books() if books is None else books
    book = books.get(str(context.get(BOOK_KEY, "")))
    if book is None:
        return None, None
    return book, book.page(str(context.get(PAGE_KEY, "")))
