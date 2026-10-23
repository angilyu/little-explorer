"""What signed-in families read: page views, read-aloud use and a daily app open.

The app queues these events on the device and sends them in batches with ``POST /account/reading``
(the access key and ``X-Pip-Session``, like stars and hearts), so the owner can see how the books
are used: daily active accounts and devices, books opened, pages viewed, reading minutes and how
often read-aloud is started and finished. The session decides the account and device; the app
never sends either. Nothing is recorded for signed-out devices or demo launches, and events hold
ids and timings only: never audio, the page's text or anything the child said.

Each event is ``{"id", "kind", "at", ...}``:

* ``id``: a client-made id (a UUID), so a retried batch is stored once.
* ``at``: when it happened, ISO 8601 with the device's UTC offset; its date is the event's ``day``.
* ``kind`` ``app_open``: the app came to the foreground (at most once a day per device).
* ``kind`` ``page_view``: ``book``, ``page`` (the page id), ``page_index``, ``seconds`` on the page
  (capped at :data:`MAX_PAGE_SECONDS`) and ``milo`` (whether Milo, the voice buddy, was on).
* ``kind`` ``read_aloud``: ``book``, ``page``, ``page_index``, ``outcome`` (``started``,
  ``finished`` or ``stopped`` early), ``mode`` (``clip``, the recorded narration, or ``system``,
  the device's voice) and, for ``finished``/``stopped``, the ``seconds`` it played.

A batch holds at most :data:`MAX_BATCH` events. Events that don't fit the shape (an unknown book,
a bad id, a time more than :data:`MAX_AGE_DAYS` days old) are skipped and counted as ``rejected``;
a batch that would take an account over :data:`PER_ACCOUNT_PER_HOUR` or :data:`PER_ACCOUNT_PER_DAY`
stored events is refused with ``429 too_many`` and ``retry_after_s``. Deleting the account deletes
its events.

``GET /account/reading`` gives the account's reading progress back for the Passport badges (see
:meth:`ReadingLog.progress`), so every device signed in to it shows the same badges.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from .accounts import valid_book_id
from .devices import DeviceStore

KINDS = ("app_open", "page_view", "read_aloud")
OUTCOMES = ("started", "finished", "stopped")
MODES = ("clip", "system")
EVENT_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
PAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_BATCH = 100
MAX_PAGE_INDEX = 999
MAX_PAGE_SECONDS = 600.0
MAX_READ_ALOUD_SECONDS = 600.0
MAX_AT_LENGTH = 40
MAX_AGE_DAYS = 30
MAX_AHEAD = timedelta(days=1)
PER_ACCOUNT_PER_HOUR = 1000
PER_ACCOUNT_PER_DAY = 5000

_COLUMNS = "id, account_id, device_id, kind, at, day, book, page, page_index, seconds, milo, outcome, mode, received_s"


class ReadingError(Exception):
    """A batch the server refuses. ``status`` is the HTTP status it answers with."""

    def __init__(self, code: str, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.extra = extra

    def as_json(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


def _seconds(value: object, cap: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return None
    return round(min(float(value), cap), 1)


def _page_index(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_PAGE_INDEX:
        return None
    return value


class ReadingLog:
    """Stores the app's reading events and makes the owner's reports from them."""

    def __init__(self, store: DeviceStore, *, clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.clock = clock

    # -- recording -------------------------------------------------------------------------------

    def _when(self, value: object, now: datetime) -> tuple[str, str] | None:
        """``(at, day)`` for a valid timestamp: within the last ``MAX_AGE_DAYS`` days, not ahead."""
        if not isinstance(value, str) or not 0 < len(value) <= MAX_AT_LENGTH:
            return None
        try:
            moment = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.store.tz)
        if not now - timedelta(days=MAX_AGE_DAYS) <= moment <= now + MAX_AHEAD:
            return None
        return value.strip(), moment.date().isoformat()

    def event_row(
        self, raw: object, account_id: str, device_id: str, known_books: Collection[str], now: float
    ) -> tuple[Any, ...] | None:
        """The row to store for one event from the app, or None if it doesn't fit the shape."""
        if not isinstance(raw, Mapping):
            return None
        event_id, kind = raw.get("id"), raw.get("kind")
        if not isinstance(event_id, str) or not EVENT_ID.match(event_id) or kind not in KINDS:
            return None
        when = self._when(raw.get("at"), datetime.fromtimestamp(now, self.store.tz))
        if when is None:
            return None
        at, day = when
        book = page = outcome = mode = None
        page_index = seconds = milo = None
        if kind != "app_open":
            book, page = raw.get("book"), raw.get("page")
            if not valid_book_id(book) or book not in known_books:
                return None
            if not isinstance(page, str) or not PAGE_ID.match(page):
                return None
            page_index = _page_index(raw.get("page_index"))
        if kind == "page_view":
            seconds = _seconds(raw.get("seconds"), MAX_PAGE_SECONDS)
            if seconds is None:
                return None
            milo = 1 if raw.get("milo") is True else 0
        elif kind == "read_aloud":
            outcome, mode = raw.get("outcome"), raw.get("mode")
            if outcome not in OUTCOMES or mode not in MODES:
                return None
            seconds = _seconds(raw.get("seconds"), MAX_READ_ALOUD_SECONDS) if outcome != "started" else None
        return (
            event_id, account_id, device_id, kind, at, day, book, page, page_index, seconds, milo, outcome, mode,
            int(now),
        )

    def record(
        self, account_id: str, device_id: str, events: object, known_books: Collection[str]
    ) -> dict[str, int]:
        """Store a batch for the session's account and device. Returns how many events were
        ``stored``, already stored before (``duplicates``) and ``rejected``."""
        if not isinstance(events, list):
            raise ReadingError("bad_events", 400, "events must be a list")
        if len(events) > MAX_BATCH:
            raise ReadingError("too_many_events", 413, f"send at most {MAX_BATCH} events at a time", max=MAX_BATCH)
        now = self.clock()
        rows = [r for r in (self.event_row(e, account_id, device_id, known_books, now) for e in events) if r]
        rejected = len(events) - len(rows)

        def run(cur: Any) -> dict[str, int]:
            if rows:
                self._check_rate(cur, account_id, len(rows), int(now))
            stored = 0
            marks = ", ".join("?" for _ in _COLUMNS.split(", "))
            for row in rows:
                stored += max(
                    self.store._exec(
                        cur, f"INSERT INTO reading_events ({_COLUMNS}) VALUES ({marks}) ON CONFLICT (id) DO NOTHING", row
                    ).rowcount,
                    0,
                )
            return {"stored": stored, "duplicates": len(rows) - stored, "rejected": rejected}

        return self.store.transaction(run)

    def _check_rate(self, cur: Any, account_id: str, adding: int, now: int) -> None:
        for window, cap in ((3600, PER_ACCOUNT_PER_HOUR), (86400, PER_ACCOUNT_PER_DAY)):
            count, oldest = self.store._exec(
                cur,
                "SELECT COUNT(*), MIN(received_s) FROM reading_events WHERE account_id = ? AND received_s > ?",
                (account_id, now - window),
            ).fetchone()
            if int(count) + adding > cap:
                retry = max(int(oldest or now) + window - now, 60)
                raise ReadingError("too_many", 429, "too many reading events; try again later", retry_after_s=retry)

    # -- the owner's reports -----------------------------------------------------------------------

    def report(self, days: int = 7, now: datetime | None = None) -> dict[str, Any]:
        """Activity for the last ``days`` days (by each event's own local date): per day, per
        account and per book. ``minutes`` is the time on pages; ``books`` counts each account's
        different books (a book read on two days counts on both days)."""
        days = max(days, 1)
        today = date.fromisoformat(self.store.day_of(now or datetime.fromtimestamp(self.clock(), self.store.tz)))
        since = (today - timedelta(days=days - 1)).isoformat()
        sums = """COUNT(DISTINCT CASE WHEN r.kind = 'page_view' THEN r.account_id || '/' || r.book END),
            SUM(CASE WHEN r.kind = 'page_view' THEN 1 ELSE 0 END),
            COALESCE(SUM(CASE WHEN r.kind = 'page_view' THEN r.seconds END), 0),
            SUM(CASE WHEN r.kind = 'read_aloud' AND r.outcome = 'started' THEN 1 ELSE 0 END),
            SUM(CASE WHEN r.kind = 'read_aloud' AND r.outcome = 'finished' THEN 1 ELSE 0 END),
            SUM(CASE WHEN r.kind = 'read_aloud' AND r.outcome = 'stopped' THEN 1 ELSE 0 END)"""

        def run(cur: Any) -> tuple[list[Any], list[Any], list[Any], list[Any]]:
            ex = self.store._exec
            by_day = ex(
                cur,
                f"""SELECT r.day, COUNT(DISTINCT r.account_id), COUNT(DISTINCT r.device_id),
                    SUM(CASE WHEN r.kind = 'app_open' THEN 1 ELSE 0 END), {sums}
                FROM reading_events r WHERE r.day >= ? GROUP BY r.day ORDER BY r.day DESC""",
                (since,),
            ).fetchall()
            by_account = ex(
                cur,
                f"""SELECT r.account_id, COALESCE(MAX(a.email), ''), COUNT(DISTINCT r.device_id),
                    COUNT(DISTINCT r.day), {sums}
                FROM reading_events r LEFT JOIN accounts a ON a.id = r.account_id
                WHERE r.day >= ? GROUP BY r.account_id""",
                (since,),
            ).fetchall()
            by_book = ex(
                cur,
                f"""SELECT r.book, COUNT(DISTINCT r.account_id), COUNT(DISTINCT r.device_id), {sums}
                FROM reading_events r WHERE r.day >= ? AND r.book IS NOT NULL GROUP BY r.book""",
                (since,),
            ).fetchall()
            totals = ex(
                cur,
                f"""SELECT COUNT(DISTINCT r.account_id), COUNT(DISTINCT r.device_id),
                    SUM(CASE WHEN r.kind = 'app_open' THEN 1 ELSE 0 END), {sums}
                FROM reading_events r WHERE r.day >= ?""",
                (since,),
            ).fetchall()
            return by_day, by_account, by_book, totals

        by_day, by_account, by_book, totals = self.store._run(run)
        return {
            "since": since,
            "days": [{"day": r[0], "accounts": int(r[1]), "devices": int(r[2]), "opens": int(r[3] or 0), **_sums(r[4:])} for r in by_day],
            "accounts": sorted(
                (
                    {"account_id": r[0], "account": r[1] or None, "devices": int(r[2]), "active_days": int(r[3]), **_sums(r[4:])}
                    for r in by_account
                ),
                key=lambda a: (-a["minutes"], -a["pages"], a["account"] or a["account_id"]),
            ),
            "books": sorted(
                ({"book": r[0], "accounts": int(r[1]), "devices": int(r[2]), **_sums(r[3:])} for r in by_book),
                key=lambda b: (-b["minutes"], -b["pages"], b["book"]),
            ),
            "total": {"accounts": int(totals[0][0]), "devices": int(totals[0][1]), "opens": int(totals[0][2] or 0), **_sums(totals[0][3:])},
        }

    def progress(self, account_id: str) -> dict[str, list[str]]:
        """What the account has read, from all of its devices, for the app's badges: the different
        pages read (``"book/page"``), pages read with Milo on, pages read aloud to the end, and the days
        with a page read. Sets, so the app can merge them with what it hasn't sent yet."""

        def run(cur: Any) -> dict[str, list[str]]:
            def keys(where: str) -> list[str]:
                rows = self.store._exec(
                    cur,
                    f"SELECT DISTINCT book, page FROM reading_events WHERE account_id = ? AND {where}",
                    (account_id,),
                ).fetchall()
                return sorted(f"{book}/{page}" for book, page in rows if book and page)

            days = self.store._exec(
                cur,
                "SELECT DISTINCT day FROM reading_events WHERE account_id = ? AND kind = 'page_view'",
                (account_id,),
            ).fetchall()
            return {
                "pages": keys("kind = 'page_view'"),
                "milo_pages": keys("kind = 'page_view' AND milo = 1"),
                "listened": keys("kind = 'read_aloud' AND outcome = 'finished'"),
                "days": sorted(str(d) for (d,) in days if d),
            }

        return self.store._run(run)

    def events_of(self, account_id: str) -> list[dict[str, Any]]:
        names = _COLUMNS.split(", ")
        rows = self.store._run(
            lambda cur: self.store._exec(
                cur, f"SELECT {_COLUMNS} FROM reading_events WHERE account_id = ? ORDER BY at, id", (account_id,)
            ).fetchall()
        )
        return [dict(zip(names, r, strict=True)) for r in rows]


def _sums(r: Iterable[Any]) -> dict[str, Any]:
    books, pages, seconds, started, finished, stopped = list(r)
    return {
        "books": int(books or 0),
        "pages": int(pages or 0),
        "minutes": round(float(seconds or 0) / 60, 1),
        "read_aloud_started": int(started or 0),
        "read_aloud_finished": int(finished or 0),
        "read_aloud_stopped": int(stopped or 0),
    }
