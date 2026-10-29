"""How a family arranges its shelf: hidden books, the order of the sections and the books in each.

A grown-up changes these in the app's Grown-ups corner ("Arrange library"). They belong to the
account, so every device signed in to it shows the same shelf: ``GET /account/library`` returns the
account's settings and ``PUT /account/library`` merges a device's changes into them (the access key
and ``X-Pip-Session``, like stars and reading). One small JSON document per account is kept in
``account_library_prefs``; deleting the account deletes it.

The document (``{"library": ...}`` in both replies)::

    {"v": 3,
     "sections": {"order": ["Tahoe", "Paris"], "t": 1792000000000},
     "books": {"Paris": {"order": ["paris-food", "paris"], "t": 1792000000000}},
     "hidden": {"paris-french": {"on": true, "t": 1792000000000}},
     "updated_at": "2026-10-23T10:00:00-07:00"}

* A section is a shelf heading: a book's ``city`` (the Travel shelf is "Travel").
* ``t`` is when the device made the change, in milliseconds since 1970. The server merges each key on
  its own (the section order, each section's book order and each book's hidden flag) and keeps the
  later change, so edits made offline on two devices both survive unless they changed the same thing.
  A time ahead of the server's clock counts as now, so a wrong device clock can't pin a setting.
* An empty ``order`` means the default order and ``"on": false`` means shown; they're kept (rather
  than removed) so an older change from another device can't bring the old setting back.
* ``v`` goes up by one with every write that changes something.

Book ids and section names the app doesn't know are ignored by the app, so a book removed from the
catalog or no longer granted to the account just stops counting, and a new book shows in its default
place. Nothing about a book's grant changes: hiding only affects the shelf, not what Milo may read.
A request with more than :data:`MAX_SECTIONS` sections, :data:`MAX_BOOKS_PER_SECTION` books in one
order or :data:`MAX_HIDDEN` hidden flags is refused (``413 too_big``); a stored document that grows
past them through merges drops its oldest entries first.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from .accounts import valid_book_id
from .devices import DeviceStore

MAX_SECTIONS = 200
MAX_BOOKS_PER_SECTION = 500
MAX_HIDDEN = 1000
MAX_SECTION_NAME = 80
# Newer than this many milliseconds since 1970 is ~year 5138, so not a real clock.
MAX_T = 10**14


class LibraryError(Exception):
    """A change the server refuses. ``status`` is the HTTP status it answers with."""

    def __init__(self, code: str, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.extra = extra

    def as_json(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


def _bad(message: str) -> LibraryError:
    return LibraryError("bad_library", 400, message)


def _too_big(what: str, limit: int) -> LibraryError:
    return LibraryError("too_big", 413, f"too many {what}", max=limit)


def _stamp(value: object, now_ms: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_T:
        raise _bad("t must be milliseconds since 1970")
    return min(value, now_ms)


def _section(value: object) -> str:
    if not isinstance(value, str):
        raise _bad("section names must be text")
    name = value.strip()
    if not name or len(name) > MAX_SECTION_NAME or any(ord(c) < 32 for c in name):
        raise _bad(f"section names must be 1-{MAX_SECTION_NAME} characters")
    return name


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _order(raw: object, now_ms: int, item: Callable[[object], str], limit: int, what: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("order"), list):
        raise _bad(f'{what} must be {{"order": [...], "t": ...}}')
    if len(raw["order"]) > limit:
        raise _too_big(what, limit)
    return {"order": _unique([item(x) for x in raw["order"]]), "t": _stamp(raw.get("t"), now_ms)}


def _book(value: object) -> str:
    if not valid_book_id(value):
        raise _bad("not a book id")
    return value  # type: ignore[return-value]


def parse_changes(body: object, now_ms: int) -> dict[str, Any]:
    """The changes in a ``PUT /account/library`` body, checked and with times clamped to ``now_ms``.
    Each part is optional; ``v`` and ``updated_at`` (as the app got them) are ignored."""
    if not isinstance(body, Mapping):
        raise _bad("send a JSON object")
    changes: dict[str, Any] = {"sections": None, "books": {}, "hidden": {}}
    if body.get("sections") is not None:
        changes["sections"] = _order(body["sections"], now_ms, _section, MAX_SECTIONS, "sections")
    books = body.get("books") or {}
    if not isinstance(books, Mapping):
        raise _bad("books must map a section to its order")
    if len(books) > MAX_SECTIONS:
        raise _too_big("sections", MAX_SECTIONS)
    for name, raw in books.items():
        changes["books"][_section(name)] = _order(raw, now_ms, _book, MAX_BOOKS_PER_SECTION, "books")
    hidden = body.get("hidden") or {}
    if not isinstance(hidden, Mapping):
        raise _bad("hidden must map a book id to {on, t}")
    if len(hidden) > MAX_HIDDEN:
        raise _too_big("hidden books", MAX_HIDDEN)
    for book, raw in hidden.items():
        if not isinstance(raw, Mapping) or not isinstance(raw.get("on"), bool):
            raise _bad('hidden flags must be {"on": true | false, "t": ...}')
        changes["hidden"][_book(book)] = {"on": raw["on"], "t": _stamp(raw.get("t"), now_ms)}
    return changes


def empty() -> dict[str, Any]:
    return {"v": 0, "sections": None, "books": {}, "hidden": {}, "updated_at": None}


def _newer(incoming: Mapping[str, Any] | None, saved: Mapping[str, Any] | None) -> bool:
    """Whether ``incoming`` replaces ``saved``: it's at least as new (a tie goes to the later write)."""
    return incoming is not None and (saved is None or incoming["t"] >= saved["t"])


def _keep_newest(entries: dict[str, Any], limit: int, active: Callable[[Any], bool]) -> dict[str, Any]:
    """At most ``limit`` entries: defaults (empty orders, shown books) go first, then the oldest."""
    if len(entries) <= limit:
        return entries
    ranked = sorted(entries.items(), key=lambda kv: (active(kv[1]), kv[1]["t"]), reverse=True)
    keep = {k for k, _ in ranked[:limit]}
    return {k: v for k, v in entries.items() if k in keep}


def merge(saved: Mapping[str, Any], changes: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """``saved`` with ``changes`` merged in, key by key, and whether anything changed."""
    merged = {
        "sections": saved.get("sections"),
        "books": dict(saved.get("books") or {}),
        "hidden": dict(saved.get("hidden") or {}),
    }
    if _newer(changes.get("sections"), merged["sections"]):
        merged["sections"] = changes["sections"]
    for name, order in (changes.get("books") or {}).items():
        if _newer(order, merged["books"].get(name)):
            merged["books"][name] = order
    for book, flag in (changes.get("hidden") or {}).items():
        if _newer(flag, merged["hidden"].get(book)):
            merged["hidden"][book] = flag
    merged["books"] = _keep_newest(merged["books"], MAX_SECTIONS, lambda o: bool(o["order"]))
    merged["hidden"] = _keep_newest(merged["hidden"], MAX_HIDDEN, lambda f: f["on"])
    before = {"sections": saved.get("sections"), "books": saved.get("books") or {}, "hidden": saved.get("hidden") or {}}
    return merged, merged != before


class LibraryPrefs:
    """Keeps each account's library settings in ``account_library_prefs``."""

    def __init__(self, store: DeviceStore, *, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.clock = clock

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _load(self, cur: Any, account_id: str) -> dict[str, Any]:
        row = self.store._exec(
            cur, "SELECT prefs, version, updated_at FROM account_library_prefs WHERE account_id = ?", (account_id,)
        ).fetchone()
        if not row:
            return empty()
        try:
            saved = json.loads(row[0])
        except ValueError:
            saved = {}
        return {
            "v": int(row[1]),
            "sections": saved.get("sections"),
            "books": saved.get("books") or {},
            "hidden": saved.get("hidden") or {},
            "updated_at": row[2],
        }

    def get(self, account_id: str) -> dict[str, Any]:
        return self.store._run(lambda cur: self._load(cur, account_id))

    def put(self, account_id: str, body: object) -> dict[str, Any]:
        """Merges a device's changes into the account's settings and returns the result."""
        changes = parse_changes(body, self._now_ms())

        def run(cur: Any) -> dict[str, Any]:
            saved = self._load(cur, account_id)
            merged, changed = merge(saved, changes)
            if not changed:
                return saved
            version = saved["v"] + 1
            stamp = self.store.now().isoformat(timespec="seconds")
            self.store._exec(
                cur,
                """INSERT INTO account_library_prefs (account_id, prefs, version, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT (account_id) DO UPDATE SET prefs = excluded.prefs, version = excluded.version,
                   updated_at = excluded.updated_at""",
                (account_id, json.dumps(merged, separators=(",", ":")), version, stamp),
            )
            return {"v": version, **merged, "updated_at": stamp}

        return self.store.transaction(run)
