"""The account's shelf, laid out by the server: which sections the app shows, in what order, and the books in each.

From app 1.2.6 the shelf and the Grown-ups corner's "Arrange library" draw whatever ``GET /account/shelf`` sends
(and ``PUT /account/shelf``, which saves an arrangement change first, like ``PUT /account/library``), so the
rules below can change without an app update. The reply::

    {"shelf": {"format": 1, "sections": [
        {"key": "matt's", "name": "Matt’s", "flag": "", "flag_book": "dinosaur-2bb188",
         "books": [{"id": "dinosaur-2bb188", "hidden": false}]},
        {"key": "paris", "name": "Paris", "flag": "🇫🇷", "flag_book": "paris",
         "books": [{"id": "paris", "hidden": false}, {"id": "what-should-i-do-at-paris-d1a210", "hidden": true}]},
        {"key": "hawaii", "name": "Hawaii", "flag": "", "flag_book": "honolulu-046d4f",
         "books": [{"id": "honolulu-046d4f", "hidden": false}, {"id": "making-<job>", "hidden": false, "making": true}]}]},
     "library": {...the arrangement, as GET /account/library...}}

* The books: the account's catalog books (in catalog order), then its published made books (oldest first, only
  for apps that show made books, and without Openverse photos for apps before 1.2.5, as in the account's content
  manifest), then a ``making`` entry (``making-<job id>``, the app's placeholder cover) for each book being made.
* A section is a book's ``city``. Names that differ only in Unicode form, the kind of apostrophe, spacing or case
  are one section (:func:`section_key`), named after its first book, so a made book in "paris" joins the
  catalog's "Paris" and "Matt’s" and "Matt's" are one.
* The arrangement (:mod:`.library`) applies by section key: sections in the saved order first, then the rest in
  shelf order; in each section the saved book order (the latest change among names with the same key), then
  the other books in shelf order. Names and ids it doesn't know are skipped.
* Hidden books stay in the layout with ``"hidden": true`` (Arrange library lists them); the shelf leaves them
  out, and a section with nothing left to show.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

FORMAT = 1
APOSTROPHES = "\u2019\u2018\u02bc\u2032\u00b4`"


def section_key(name: str) -> str:
    """Which section ``name`` means (see the module notes)."""
    text = unicodedata.normalize("NFKC", name)
    text = "".join("'" if c in APOSTROPHES else c for c in text)
    return " ".join(text.split()).lower()


@dataclass(frozen=True)
class ShelfBook:
    id: str
    section: str
    flag: str = ""
    making: bool = False


def _ordered(items: list[Any], saved: Iterable[str], key: Any) -> list[Any]:
    rank: dict[str, int] = {}
    for i, name in enumerate(saved):
        rank.setdefault(name, i)
    listed = sorted((x for x in items if key(x) in rank), key=lambda x: rank[key(x)])
    return listed + [x for x in items if key(x) not in rank]


def _book_orders(library: Mapping[str, Any]) -> dict[str, list[str]]:
    """Each section's saved book order by section key; the later change wins between names with one key."""
    latest: dict[str, Mapping[str, Any]] = {}
    for name, order in (library.get("books") or {}).items():
        key = section_key(name)
        if key in latest and latest[key]["t"] > order["t"]:
            continue
        latest[key] = order
    return {key: list(order["order"]) for key, order in latest.items()}


def layout(catalog: Iterable[ShelfBook], made: Iterable[ShelfBook], making: Iterable[ShelfBook],
           library: Mapping[str, Any]) -> dict[str, Any]:
    """The shelf for ``catalog`` + ``made`` + ``making`` books arranged by ``library`` (see the module notes)."""
    sections: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for book in [*catalog, *made, *making]:
        if book.id in seen:
            continue
        seen.add(book.id)
        key = section_key(book.section)
        if key not in by_key:
            by_key[key] = {"key": key, "name": book.section, "flag": "", "flag_book": None, "books": []}
            sections.append(by_key[key])
        section = by_key[key]
        if section["flag_book"] is None and not book.making:
            section["flag"], section["flag_book"] = book.flag, book.id
        section["books"].append(book)

    hidden = {book for book, flag in (library.get("hidden") or {}).items() if flag.get("on")}
    saved_sections = [section_key(name) for name in ((library.get("sections") or {}).get("order") or [])]
    orders = _book_orders(library)
    out = []
    for section in _ordered(sections, saved_sections, lambda s: s["key"]):
        books = _ordered(section["books"], orders.get(section["key"], []), lambda b: b.id)
        entries = []
        for book in books:
            entry: dict[str, Any] = {"id": book.id, "hidden": book.id in hidden}
            if book.making:
                entry["making"] = True
            entries.append(entry)
        out.append({**section, "books": entries})
    return {"format": FORMAT, "sections": out}
