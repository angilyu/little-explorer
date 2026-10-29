"""Who may talk to Milo, and for how long each day.

Milo's daily time belongs to the grown-up's *account* (see :mod:`.accounts`). The app sends its
session (``X-Pip-Session``) with ``/token``; an old build without one counts as the account its
device is linked to. The token server checks the account's minutes for today (every device signed
in to it together) before handing out a token, and the agent reports every session's length and
estimated cost when it ends (``POST /usage``), stamped with the account.

An account's Milo status is ``unknown`` until the owner approves it, ``approved``, or ``blocked``:

* unknown accounts get ``PIP_LIMIT_UNKNOWN_MIN`` (10) minutes a day, then Milo rests until tomorrow.
* approved accounts get ``PIP_LIMIT_APPROVED_MIN`` (60) minutes a day, then rest until tomorrow.
* blocked accounts get none.
* a per-account ``daily_limit_min`` overrides either.

Each app install also sends a stable device id (kept in the Keychain) with its name and model; it
is recorded as which device was used. Only an old build on a device that isn't linked to any
account still gets the per-device rules: the same statuses and limits on the device, except an
unknown device that goes over its limit is blocked until the owner unblocks or approves it.

Days run midnight to midnight in ``PIP_TIMEZONE`` (America/Los_Angeles).

Storage is Postgres when ``DATABASE_URL`` is set (the deployed token server uses a free Neon
database, because Render's free disk is wiped on every deploy and restart), otherwise SQLite at
``PIP_DEVICES_DB`` (``~/.config/little-explorer/devices.sqlite3``) for local runs and tests.
Star awards (see :mod:`.stars`) are kept here too, so a reinstall can restore them, and so are the
coin ledger and the owner's coin rules (see :mod:`.coins`).
So are a signed-in family's reading events (see :mod:`.reading`) and how it arranged its shelf
(see :mod:`.library`).
Only ids, names, models, timings, book ids, costs and star and coin counts are stored; never transcripts or audio.

Accounts (see :mod:`.accounts`) add a grown-up's email. A device signed in to an account is *linked*
(``devices.account_id``): its stars and coin conversions are stamped with the account, and its stars,
coins and per-page star limit are the account's, shared by every device signed in to it. Rows earned
before a device was linked are claimed by the first account it links to (``legacy_account_id``).
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

log = logging.getLogger("little_explorer.devices")
T = TypeVar("T")

STATUSES = ("unknown", "approved", "blocked")
ANONYMOUS = "anonymous"
DEVICE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
DEFAULT_DB = Path("~/.config/little-explorer/devices.sqlite3")
DEFAULT_TZ = "America/Los_Angeles"
MAX_TEXT = 80

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'unknown',
        daily_limit_min REAL,
        unblocked_on TEXT NOT NULL DEFAULT '',
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        room TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        started_at TEXT NOT NULL,
        day TEXT NOT NULL,
        duration_s REAL NOT NULL DEFAULT 0,
        mode TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        book TEXT NOT NULL DEFAULT '',
        pages INTEGER NOT NULL DEFAULT 0,
        responses INTEGER NOT NULL DEFAULT 0,
        est_usd REAL NOT NULL DEFAULT 0,
        ended_reason TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS sessions_device_day ON sessions (device_id, day)",
    """CREATE TABLE IF NOT EXISTS stars (
        id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        room TEXT NOT NULL DEFAULT '',
        awarded_at TEXT NOT NULL,
        day TEXT NOT NULL,
        book TEXT NOT NULL DEFAULT '',
        page TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'question',
        long INTEGER NOT NULL DEFAULT 0,
        why_how INTEGER NOT NULL DEFAULT 0,
        followup INTEGER NOT NULL DEFAULT 0,
        stars INTEGER NOT NULL,
        words INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS stars_device ON stars (device_id)",
    # Stars a grown-up turned into coins (see coins.py); the id is the app's, so retries are safe.
    """CREATE TABLE IF NOT EXISTS coin_conversions (
        id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        converted_at TEXT NOT NULL,
        stars_spent INTEGER NOT NULL,
        coins INTEGER NOT NULL,
        stars_per_coin INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS coin_conversions_device ON coin_conversions (device_id)",
    # Owner overrides, e.g. the coin rules, changed through /admin without a redeploy.
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # Accounts (see accounts.py): a grown-up's email, and the books on the account's shelf.
    """CREATE TABLE IF NOT EXISTS accounts (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        all_books INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS account_books (
        account_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        granted_at TEXT NOT NULL,
        PRIMARY KEY (account_id, book_id)
    )""",
    # Sign-in codes: only an HMAC of the code is kept, and an HMAC of the asker's IP for rate limits.
    """CREATE TABLE IF NOT EXISTS email_codes (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        purpose TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        ip_hash TEXT NOT NULL DEFAULT '',
        created_s INTEGER NOT NULL,
        expires_s INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        used INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS email_codes_email ON email_codes (email, purpose)",
    # Signed-in devices: only a SHA-256 of each session token is kept.
    """CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS auth_sessions_account ON auth_sessions (account_id)",
    """CREATE TABLE IF NOT EXISTS hearts (
        account_id TEXT NOT NULL,
        page_key TEXT NOT NULL,
        loved_at TEXT NOT NULL,
        PRIMARY KEY (account_id, page_key)
    )""",
    # What a signed-in family reads (see :mod:`.reading`): page views, read-aloud use and a daily
    # app open, so the owner can count active accounts and devices. Ids and timings only, no text.
    """CREATE TABLE IF NOT EXISTS reading_events (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        at TEXT NOT NULL,
        day TEXT NOT NULL,
        book TEXT,
        page TEXT,
        page_index INTEGER,
        seconds REAL,
        milo INTEGER,
        outcome TEXT,
        mode TEXT,
        received_s INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS reading_events_account ON reading_events (account_id, received_s)",
    "CREATE INDEX IF NOT EXISTS reading_events_day ON reading_events (day)",
    # How a family arranged its shelf (see :mod:`.library`): hidden books and the order of sections and books.
    """CREATE TABLE IF NOT EXISTS account_library_prefs (
        account_id TEXT PRIMARY KEY,
        prefs TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )""",
    # Books made in the cloud (see bookgen/): one job per book, run one at a time by the in-process
    # runner, which holds a lease (epoch seconds) it renews at every step.
    """CREATE TABLE IF NOT EXISTS generation_jobs (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        step TEXT NOT NULL DEFAULT 'text',
        input TEXT NOT NULL,
        error TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        lease_until BIGINT NOT NULL DEFAULT 0,
        run_after BIGINT NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0,
        costs TEXT NOT NULL DEFAULT '[]',
        day TEXT NOT NULL,
        month TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS generation_jobs_status ON generation_jobs (status, created_at)",
    "CREATE INDEX IF NOT EXISTS generation_jobs_account ON generation_jobs (account_id, day)",
    # The book itself: the same JSON as LittleExplorer/Resources/Books/<id>.json, plus its clips.
    """CREATE TABLE IF NOT EXISTS books (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        job_id TEXT NOT NULL,
        title TEXT NOT NULL,
        section TEXT NOT NULL,
        level INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        json TEXT NOT NULL DEFAULT '{}',
        narration TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        published_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS books_account ON books (account_id)",
    # Every photo and clip of a book, stored in R2 under r2_key; path is what the manifest lists.
    """CREATE TABLE IF NOT EXISTS book_assets (
        book_id TEXT NOT NULL,
        path TEXT NOT NULL,
        r2_key TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        size INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (book_id, path)
    )""",
    # A grown-up's report about a made book on their shelf, for the owner to look at (GET /admin/reports).
    """CREATE TABLE IF NOT EXISTS book_reports (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        book_id TEXT NOT NULL,
        book_owner TEXT NOT NULL,
        title TEXT NOT NULL,
        reason TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        page TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS book_reports_status ON book_reports (status, created_at)",
)

# Columns added after the first release, as (table, column, type). Added on startup if missing.
COLUMNS = (
    ("devices", "account_id", "TEXT"),
    ("devices", "legacy_account_id", "TEXT"),
    ("stars", "account_id", "TEXT"),
    ("coin_conversions", "account_id", "TEXT"),
    ("accounts", "milo_status", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("accounts", "daily_limit_min", "REAL"),
    # A grown-up's choice about Milo for the account: 'yes', 'no', or NULL if never asked (old builds).
    ("accounts", "milo_consent", "TEXT"),
    ("accounts", "milo_consent_at", "TEXT"),
    ("sessions", "account_id", "TEXT"),
    # May make books in the cloud (dogfood); the ``bookgen.everyone`` setting turns it on for all.
    ("accounts", "can_generate", "INTEGER NOT NULL DEFAULT 0"),
    # 'book' (a new book, counted against the quota) or 'page' (a grown-up asked for one page again).
    ("generation_jobs", "kind", "TEXT NOT NULL DEFAULT 'book'"),
    # The app build that last asked for a Milo token, e.g. "1.1 (20261101.0930, testflight)"; "1.0" for
    # builds that don't say (see app_versions.py). NULL until the device asks again.
    ("devices", "app_version", "TEXT"),
)
INDEXES = (
    "CREATE INDEX IF NOT EXISTS stars_account ON stars (account_id)",
    "CREATE INDEX IF NOT EXISTS coin_conversions_account ON coin_conversions (account_id)",
    "CREATE INDEX IF NOT EXISTS devices_account ON devices (account_id)",
    "CREATE INDEX IF NOT EXISTS sessions_account_day ON sessions (account_id, day)",
)
# One-time data moves, each recorded in ``settings`` once done (see ``DeviceStore._migrate``).
MILO_PER_ACCOUNT = "migration.milo_per_account"
BOOKGEN_DOGFOOD = "migration.bookgen_dogfood"
# The accounts that may make books before everyone can (see bookgen/config.py).
DOGFOOD_EMAILS = ("angilyu@gmail.com", "hfevers@gmail.com")
# Every table, for /admin/backup and restore (in an order that restores cleanly).
TABLES = (
    "devices", "sessions", "stars", "coin_conversions", "settings",
    "accounts", "account_books", "email_codes", "auth_sessions", "hearts", "reading_events",
    "generation_jobs", "books", "book_assets", "account_library_prefs", "book_reports",
)

STAR_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
STAR_KINDS = ("question", "follow_up", "answer")
# One star per award, up to 5 per page per device each day (same as stars.py); older awards keep
# what they were given.
STARS_PER_AWARD = 1
STARS_PER_PAGE = 5
_STAR_COLUMNS = "id, device_id, awarded_at, book, page, city, kind, long, why_how, followup, stars, words, reason"

REST_MESSAGE = "Milo needs a rest today. Let's talk again tomorrow!"


def _text(value: object) -> str:
    return value.strip()[:MAX_TEXT] if isinstance(value, str) else ""


def award_day(awarded_at: object) -> str | None:
    """The local day of an award: the date in its own ``awarded_at`` offset (the agent stamps it in
    the device's time zone, so this is the day the Passport calendar shows). None if unreadable."""
    if not isinstance(awarded_at, str) or not awarded_at:
        return None
    try:
        return datetime.fromisoformat(awarded_at.strip()).date().isoformat()
    except ValueError:
        return None


def valid_device_id(value: object) -> bool:
    return isinstance(value, str) and bool(DEVICE_ID.match(value))


@dataclass(frozen=True)
class Scope:
    """Whose stars and coins: an account's (every device signed in to it), or an unlinked device's."""

    account_id: str | None = None
    device_id: str | None = None

    def where(self) -> tuple[str, tuple[str, ...]]:
        if self.account_id:
            return "account_id = ?", (self.account_id,)
        return "device_id = ? AND account_id IS NULL", (self.device_id or "",)


@dataclass(frozen=True)
class Limits:
    """Daily minutes per status, and the least time worth starting a session with."""

    unknown_min: float = 10.0
    approved_min: float = 60.0
    min_start_s: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Limits:
        env = os.environ if env is None else env

        def num(name: str, default: float) -> float:
            raw = (env.get(name) or "").strip()
            return float(raw) if raw else default

        return cls(
            unknown_min=num("PIP_LIMIT_UNKNOWN_MIN", cls.unknown_min),
            approved_min=num("PIP_LIMIT_APPROVED_MIN", cls.approved_min),
            min_start_s=num("PIP_LIMIT_MIN_START_S", cls.min_start_s),
        )


@dataclass
class Device:
    id: str
    name: str = ""
    model: str = ""
    label: str = ""
    status: str = "unknown"
    daily_limit_min: float | None = None
    unblocked_on: str = ""
    first_seen: str = ""
    last_seen: str = ""
    app_version: str | None = None

    @property
    def display_name(self) -> str:
        return self.label or self.name or self.id

    def limit_min(self, limits: Limits) -> float:
        return limit_min(self.status, self.daily_limit_min, limits)


def limit_min(status: str, daily_limit_min: float | None, limits: Limits) -> float:
    """Minutes a day for a status (blocked is refused before this matters), or the custom limit."""
    if daily_limit_min is not None:
        return float(daily_limit_min)
    return limits.approved_min if status == "approved" else limits.unknown_min


@dataclass(frozen=True)
class MiloAccount:
    """An account's Milo status and custom limit (the rest of the account is in :mod:`.accounts`)."""

    id: str
    email: str
    status: str = "unknown"
    daily_limit_min: float | None = None

    def limit_min(self, limits: Limits) -> float:
        return limit_min(self.status, self.daily_limit_min, limits)


@dataclass(frozen=True)
class Decision:
    """Whether a device may start a session now, and for how many seconds at most. ``account_id``
    is whose minutes they are (None for an unlinked device on the per-device rules)."""

    allowed: bool
    reason: str  # ok | blocked | limit
    remaining_s: float
    used_s: float = 0.0
    limit_s: float = 0.0
    device: Device | None = None
    account_id: str | None = None


_DEVICE_COLUMNS = "id, name, model, label, status, daily_limit_min, unblocked_on, first_seen, last_seen, app_version"


class DeviceStore:
    """Devices and their sessions, in Postgres (``url``) or SQLite (``sqlite_path``).

    Methods are synchronous; the token server calls them with :func:`asyncio.to_thread`. One
    connection is kept open and re-opened once if the database dropped it (Neon closes idle ones).
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        sqlite_path: Path | str | None = None,
        limits: Limits | None = None,
        tz: str = DEFAULT_TZ,
    ) -> None:
        self.url = url or None
        self.sqlite_path = None if self.url else Path(sqlite_path or DEFAULT_DB).expanduser()
        self.limits = limits or Limits()
        self.tz = ZoneInfo(tz)
        self._conn: Any = None
        self._lock = threading.Lock()
        self._ready = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DeviceStore:
        env = os.environ if env is None else env
        return cls(
            url=(env.get("DATABASE_URL") or "").strip() or None,
            sqlite_path=(env.get("PIP_DEVICES_DB") or "").strip() or None,
            limits=Limits.from_env(env),
            tz=(env.get("PIP_TIMEZONE") or "").strip() or DEFAULT_TZ,
        )

    @property
    def kind(self) -> str:
        return "postgres" if self.url else "sqlite"

    # -- connection plumbing -------------------------------------------------------------------

    def _open(self) -> Any:
        if self.url:
            import psycopg

            return psycopg.connect(self.url, autocommit=True, connect_timeout=10)
        assert self.sqlite_path is not None
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.sqlite_path, isolation_level=None, check_same_thread=False)
        try:
            os.chmod(self.sqlite_path, 0o600)
        except OSError:
            pass
        return conn

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.url else sql

    def _run(self, fn: Callable[[Any], T]) -> T:
        """Run ``fn(cursor)``, re-opening the connection and retrying once if it was dropped."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        self._conn = self._open()
                    cur = self._conn.cursor()
                    if not self._ready:
                        for statement in SCHEMA:
                            cur.execute(statement)
                        self._migrate(cur)
                        self._ready = True
                    return fn(cur)
                except Exception as exc:
                    if attempt == 2 or not _is_connection_error(exc):
                        raise
                    log.info("database connection dropped; reconnecting")
                    self.close_quietly()
        raise AssertionError("unreachable")

    def _columns(self, cur: Any, table: str) -> set[str]:
        if self.url:
            rows = self._exec(
                cur, "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?", (table,)
            ).fetchall()
            return {r[0] for r in rows}
        return {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}

    def _migrate(self, cur: Any) -> None:
        """Add columns newer than the tables (only ever adds; old builds ignore them), then run any
        one-time data move not done yet."""
        for table, column, kind in COLUMNS:
            if column not in self._columns(cur, table):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
                log.info("added column %s.%s", table, column)
        for statement in INDEXES:
            cur.execute(statement)
        if not self._exec(cur, "SELECT 1 FROM settings WHERE key = ?", (MILO_PER_ACCOUNT,)).fetchone():
            cur.execute("BEGIN")
            try:
                moved = self._milo_per_account(cur)
                self._exec(
                    cur,
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (MILO_PER_ACCOUNT, "done", self.now().isoformat(timespec="seconds")),
                )
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            cur.execute("COMMIT")
            log.info("moved Milo's minutes to accounts: %s", moved)
        if not self._exec(cur, "SELECT 1 FROM settings WHERE key = ?", (BOOKGEN_DOGFOOD,)).fetchone():
            marks = ", ".join("?" for _ in DOGFOOD_EMAILS)
            cur.execute("BEGIN")
            try:
                turned_on = self._exec(
                    cur, f"UPDATE accounts SET can_generate = 1 WHERE email IN ({marks})", DOGFOOD_EMAILS
                ).rowcount
                self._exec(
                    cur,
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (BOOKGEN_DOGFOOD, "done", self.now().isoformat(timespec="seconds")),
                )
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            cur.execute("COMMIT")
            log.info("book making turned on for %d dogfood account(s)", max(turned_on, 0))

    def _milo_per_account(self, cur: Any) -> dict[str, int]:
        """Milo's minutes moved from devices to accounts: every session of a linked device counts
        for its account (so today's minutes carry over), and an account is approved, or keeps a
        custom limit, if one of its linked devices had."""
        sessions = self._exec(
            cur,
            """UPDATE sessions SET account_id = (SELECT d.account_id FROM devices d WHERE d.id = sessions.device_id)
            WHERE account_id IS NULL
                AND device_id IN (SELECT id FROM devices WHERE account_id IS NOT NULL)""",
        ).rowcount
        approved = self._exec(
            cur,
            """UPDATE accounts SET milo_status = 'approved' WHERE milo_status = 'unknown' AND EXISTS
                (SELECT 1 FROM devices d WHERE d.account_id = accounts.id AND d.status = 'approved')""",
        ).rowcount
        limits = self._exec(
            cur,
            """UPDATE accounts SET daily_limit_min =
                (SELECT MAX(d.daily_limit_min) FROM devices d WHERE d.account_id = accounts.id)
            WHERE daily_limit_min IS NULL AND EXISTS
                (SELECT 1 FROM devices d WHERE d.account_id = accounts.id AND d.daily_limit_min IS NOT NULL)""",
        ).rowcount
        return {"sessions": max(sessions, 0), "approved": max(approved, 0), "limits": max(limits, 0)}

    def transaction(self, fn: Callable[[Any], T]) -> T:
        """Run ``fn(cursor)`` all-or-nothing."""

        def run(cur: Any) -> T:
            cur.execute("BEGIN")
            try:
                result = fn(cur)
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            cur.execute("COMMIT")
            return result

        return self._run(run)

    def close_quietly(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None

    def _exec(self, cur: Any, sql: str, params: Sequence[Any] = ()) -> Any:
        cur.execute(self._sql(sql), tuple(params))
        return cur

    # -- time ------------------------------------------------------------------------------------

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def day_of(self, moment: datetime | str | None = None) -> str:
        if moment is None:
            moment = self.now()
        if isinstance(moment, str):
            moment = datetime.fromisoformat(moment)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.tz)
        return moment.astimezone(self.tz).date().isoformat()

    # -- devices ---------------------------------------------------------------------------------

    def _get(self, cur: Any, device_id: str) -> Device | None:
        row = self._exec(cur, f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE id = ?", (device_id,)).fetchone()
        return Device(*row) if row else None

    def get(self, device_id: str) -> Device | None:
        return self._run(lambda cur: self._get(cur, device_id))

    def _account_of(self, cur: Any, device_id: str) -> str | None:
        row = self._exec(cur, "SELECT account_id FROM devices WHERE id = ?", (device_id,)).fetchone()
        return row[0] if row and row[0] else None

    def _scope(self, cur: Any, device_id: str) -> Scope:
        """A linked device's stars and coins are its account's."""
        account = self._account_of(cur, device_id)
        return Scope(account_id=account) if account else Scope(device_id=device_id)

    def scope(self, device_id: str) -> Scope:
        return self._run(lambda cur: self._scope(cur, device_id))

    def _milo_account(self, cur: Any, account_id: object) -> MiloAccount | None:
        if not valid_device_id(account_id):
            return None
        row = self._exec(
            cur, "SELECT id, email, milo_status, daily_limit_min FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not row:
            return None
        return MiloAccount(row[0], row[1], row[2] if row[2] in STATUSES else "unknown", row[3])

    def _whose(self, cur: Any, account_id: object, device_id: str) -> MiloAccount | None:
        """The account a session or star counts for: the one the agent named, else the device's link."""
        return self._milo_account(cur, account_id) or self._milo_account(cur, self._account_of(cur, device_id))

    def _account_used_s(self, cur: Any, account_id: str, day: str) -> float:
        row = self._exec(
            cur, "SELECT COALESCE(SUM(duration_s), 0) FROM sessions WHERE account_id = ? AND day = ?", (account_id, day)
        ).fetchone()
        return float(row[0] or 0)

    def _touch(self, cur: Any, device_id: str, name: str, model: str, now: datetime) -> Device:
        stamp = now.isoformat(timespec="seconds")
        self._exec(
            cur,
            """INSERT INTO devices (id, name, model, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                name = CASE WHEN excluded.name = '' THEN devices.name ELSE excluded.name END,
                model = CASE WHEN excluded.model = '' THEN devices.model ELSE excluded.model END,
                last_seen = excluded.last_seen""",
            (device_id, name, model, stamp, stamp),
        )
        device = self._get(cur, device_id)
        assert device is not None
        return device

    def _used_s(self, cur: Any, device_id: str, day: str) -> float:
        row = self._exec(
            cur, "SELECT COALESCE(SUM(duration_s), 0) FROM sessions WHERE device_id = ? AND day = ?", (device_id, day)
        ).fetchone()
        return float(row[0] or 0)

    def _set_status(self, cur: Any, device_id: str, status: str, unblocked_on: str | None = None) -> None:
        if unblocked_on is None:
            self._exec(cur, "UPDATE devices SET status = ? WHERE id = ?", (status, device_id))
        else:
            self._exec(
                cur, "UPDATE devices SET status = ?, unblocked_on = ? WHERE id = ?", (status, unblocked_on, device_id)
            )

    def _over_limit(self, cur: Any, device: Device, day: str) -> tuple[float, float]:
        return self._used_s(cur, device.id, day), device.limit_min(self.limits) * 60

    def _auto_block(self, cur: Any, device: Device, day: str, used_s: float, limit_s: float) -> Device:
        """Block an unknown device that has used up its day, unless the owner unblocked it today."""
        if device.status == "unknown" and used_s >= limit_s and device.unblocked_on != day:
            self._set_status(cur, device.id, "blocked")
            log.warning("blocked unknown device %s (%s): %.0f of %.0f s today", device.id, device.display_name, used_s, limit_s)
            device.status = "blocked"
        return device

    def check(
        self,
        device_id: str,
        name: str = "",
        model: str = "",
        now: datetime | None = None,
        account_id: str | None = None,
        record: bool = True,
        app_version: str | None = None,
    ) -> Decision:
        """Record that ``device_id`` asked to talk, and decide whether it may: on ``account_id``'s
        minutes (the signed-in account), else its linked account's, else its own (see module docs).
        ``record=False`` only reads (a Milo warm-up): no device row, no auto-block. ``app_version``
        (when recording) is noted on the device so the owner can see who still runs an old build."""
        now = now or self.now()
        day = self.day_of(now)

        def run(cur: Any) -> tuple[Device, str | None, str, float, float]:
            if record:
                device = self._touch(cur, device_id, _text(name), _text(model), now)
                if app_version:
                    device.app_version = _text(app_version)
                    self._exec(cur, "UPDATE devices SET app_version = ? WHERE id = ?", (device.app_version, device_id))
            else:
                device = self._get(cur, device_id) or Device(device_id)
            account = self._whose(cur, account_id, device_id)
            if account:
                used = self._account_used_s(cur, account.id, day)
                return device, account.id, account.status, used, account.limit_min(self.limits) * 60
            used, limit = self._over_limit(cur, device, day)
            if record and device.status != "blocked" and limit - used < self.limits.min_start_s:
                device = self._auto_block(cur, device, day, max(used, limit), limit)
            return device, None, device.status, used, limit

        device, account, status, used, limit = self._run(run)
        if status == "blocked":
            return Decision(False, "blocked", 0.0, used, limit, device, account)
        remaining = max(limit - used, 0.0)
        if remaining < self.limits.min_start_s:
            return Decision(False, "limit", remaining, used, limit, device, account)
        return Decision(True, "ok", remaining, used, limit, device, account)

    def record_session(self, report: Mapping[str, Any]) -> Device:
        """Store one finished session (idempotent per room) for its account (the report's
        ``account_id``, else the device's link), or block an unlinked unknown device that went over."""
        room = _text(report.get("room")) or None
        device_id = report.get("device_id") or ANONYMOUS
        if not room or not valid_device_id(device_id):
            raise ValueError("room and a valid device_id are required")
        started = str(report.get("started_at") or self.now().isoformat())
        day = self.day_of(started)
        values = (
            room,
            device_id,
            started[:40],
            day,
            max(float(report.get("duration_s") or 0), 0.0),
            _text(report.get("mode")),
            _text(report.get("model")),
            _text(report.get("book")),
            int(report.get("pages") or 0),
            int(report.get("responses") or 0),
            max(float(report.get("est_usd") or 0), 0.0),
            _text(report.get("ended_reason")),
        )

        def run(cur: Any) -> Device:
            device = self._touch(cur, device_id, "", "", self.now())
            account = self._whose(cur, report.get("account_id"), device_id)
            self._exec(
                cur,
                """INSERT INTO sessions (room, device_id, started_at, day, duration_s, mode, model, book,
                    pages, responses, est_usd, ended_reason, account_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (room) DO UPDATE SET duration_s = excluded.duration_s, pages = excluded.pages,
                    responses = excluded.responses, est_usd = excluded.est_usd,
                    ended_reason = excluded.ended_reason,
                    account_id = COALESCE(sessions.account_id, excluded.account_id)""",
                (*values, account.id if account else None),
            )
            if account:
                return device
            used, limit = self._over_limit(cur, device, day)
            return self._auto_block(cur, device, day, used, limit)

        return self._run(run)

    # -- stars -----------------------------------------------------------------------------------

    def record_star(self, award: Mapping[str, Any]) -> bool:
        """Store one star award from the agent; True if it was new (the id makes retries safe).

        Any award counts as one star, and a page that already has :data:`STARS_PER_PAGE` stars on
        this device (or its account) that day (any session; the day is :func:`award_day`, the
        device's local date) gets no more, whatever build of the agent sent it. The star is the
        award's ``account_id``'s (the signed-in account the agent was told about), else the device's
        linked account's, else the device's."""
        device_id = award.get("device_id") or ANONYMOUS
        if not valid_device_id(device_id):
            raise ValueError("a valid id and device_id are required")
        if not isinstance(award.get("id"), str) or not STAR_ID.match(award["id"]):
            raise ValueError("a valid id and device_id are required")
        values = self.star_values(award, device_id, self.day_of, self.now().isoformat(timespec="seconds"))

        def run(cur: Any) -> bool:
            account = self._whose(cur, award.get("account_id"), device_id)
            scope = Scope(account_id=account.id) if account else Scope(device_id=device_id)
            return self._insert_star(cur, scope, values)

        return self._run(run)

    def _insert_star(self, cur: Any, scope: Scope, values: tuple[Any, ...], *, capped: bool = True) -> bool:
        star_id, awarded, book, page = values[0], values[3], values[5], values[6]
        if capped and book and page:
            where, params = scope.where()
            rows = self._exec(
                cur,
                f"SELECT awarded_at, stars FROM stars WHERE {where} AND book = ? AND page = ? AND id <> ?",
                (*params, book, page, star_id),
            ).fetchall()
            today = award_day(awarded)
            on_page = sum(int(n or 0) for at, n in rows if award_day(at) == today)
            if on_page >= STARS_PER_PAGE:
                return False
        self._exec(
            cur,
            """INSERT INTO stars (id, device_id, room, awarded_at, day, book, page, city, kind, long, why_how,
                followup, stars, words, reason, account_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING""",
            (*values, scope.account_id),
        )
        return cur.rowcount == 1

    @staticmethod
    def star_values(award: Mapping[str, Any], device_id: str, day_of: Callable[[str], str], now: str) -> tuple[Any, ...]:
        """The ``stars`` row for an award (raises ``ValueError`` if it isn't one)."""
        star_id = award.get("id")
        if not isinstance(star_id, str) or not STAR_ID.match(star_id):
            raise ValueError("a valid id is required")
        stars = award.get("stars")
        if isinstance(stars, bool) or not isinstance(stars, int) or stars < 1:
            raise ValueError("stars must be a positive whole number")
        kind = award.get("kind") if award.get("kind") in STAR_KINDS else "question"
        awarded = str(award.get("awarded_at") or now)[:40]
        return (
            star_id,
            device_id,
            _text(award.get("room")),
            awarded,
            day_of(awarded),
            _text(award.get("book")),
            _text(award.get("page")),
            _text(award.get("city")),
            kind,
            int(bool(award.get("long"))),
            int(bool(award.get("why_how"))),
            int(bool(award.get("followup"))),
            STARS_PER_AWARD,
            max(int(award.get("words") or 0), 0),
            _text(award.get("reason")),
        )

    def stars_for(self, device_id: str) -> list[dict[str, Any]]:
        """Every award a device (or, if it's linked, its account) has earned, oldest first (for
        restoring after a reinstall, and for the agent's per-page counts)."""
        return self._run(lambda cur: self._stars_in(cur, self._scope(cur, device_id)))

    def stars_of(self, scope: Scope) -> list[dict[str, Any]]:
        return self._run(lambda cur: self._stars_in(cur, scope))

    def _stars_in(self, cur: Any, scope: Scope) -> list[dict[str, Any]]:
        where, params = scope.where()
        rows = self._exec(cur, f"SELECT {_STAR_COLUMNS} FROM stars WHERE {where} ORDER BY awarded_at, id", params).fetchall()
        keys = [k.strip() for k in _STAR_COLUMNS.split(",")]
        out = []
        for row in rows:
            item = dict(zip(keys, row))
            for flag in ("long", "why_how", "followup"):
                item[flag] = bool(item[flag])
            item["stars"] = int(item["stars"])
            out.append(item)
        return out

    # -- owner tools -----------------------------------------------------------------------------

    def find(self, ref: str) -> list[Device]:
        """Devices whose id is ``ref``, or whose label or name is ``ref`` (ignoring case)."""

        def run(cur: Any) -> list[Device]:
            exact = self._get(cur, ref)
            if exact:
                return [exact]
            rows = self._exec(
                cur,
                f"SELECT {_DEVICE_COLUMNS} FROM devices WHERE LOWER(label) = ? OR LOWER(name) = ? ORDER BY last_seen DESC",
                (ref.lower(), ref.lower()),
            ).fetchall()
            return [Device(*r) for r in rows]

        return self._run(run)

    def update(
        self,
        ref: str,
        *,
        status: str | None = None,
        label: str | None = None,
        daily_limit_min: float | None = None,
        clear_limit: bool = False,
        now: datetime | None = None,
    ) -> Device:
        """Change a device found by id or name. Unblocking sets it back to ``unknown`` for good
        behaviour: it keeps the unknown limit but is not blocked again until tomorrow."""
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if daily_limit_min is not None and daily_limit_min < 0:
            raise ValueError("daily_limit_min must be >= 0")
        matches = self.find(ref)
        if not matches:
            raise LookupError(f"no device matches {ref!r}")
        if len(matches) > 1:
            raise LookupError(f"{len(matches)} devices match {ref!r}; use the id: " + ", ".join(d.id for d in matches))
        device = matches[0]

        def run(cur: Any) -> Device | None:
            if status is not None:
                unblocked = self.day_of(now) if (device.status == "blocked" and status == "unknown") else None
                self._set_status(cur, device.id, status, unblocked)
            if label is not None:
                self._exec(cur, "UPDATE devices SET label = ? WHERE id = ?", (_text(label), device.id))
            if clear_limit:
                self._exec(cur, "UPDATE devices SET daily_limit_min = NULL WHERE id = ?", (device.id,))
            elif daily_limit_min is not None:
                self._exec(cur, "UPDATE devices SET daily_limit_min = ? WHERE id = ?", (daily_limit_min, device.id))
            return self._get(cur, device.id)

        updated = self._run(run)
        assert updated is not None
        log.info("device %s (%s) -> %s", updated.id, updated.display_name, updated.status)
        return updated

    def summary(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """One entry per device: status, limit, and minutes and dollars today and in total."""
        day = self.day_of(now)

        def run(cur: Any) -> tuple[list[Device], dict[str, Any], dict[str, Any], dict[str, Any]]:
            devices = [Device(*r) for r in self._exec(cur, f"SELECT {_DEVICE_COLUMNS} FROM devices").fetchall()]
            totals = {
                r[0]: r[1:]
                for r in self._exec(
                    cur,
                    """SELECT device_id, COUNT(*), COALESCE(SUM(duration_s), 0), COALESCE(SUM(est_usd), 0),
                        COALESCE(SUM(CASE WHEN day = ? THEN duration_s ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN day = ? THEN est_usd ELSE 0 END), 0)
                    FROM sessions GROUP BY device_id""",
                    (day, day),
                ).fetchall()
            }
            stars = dict(
                self._exec(cur, "SELECT device_id, COALESCE(SUM(stars), 0) FROM stars GROUP BY device_id").fetchall()
            )
            coins = {
                r[0]: r[1:]
                for r in self._exec(
                    cur, "SELECT device_id, SUM(stars_spent), SUM(coins) FROM coin_conversions GROUP BY device_id"
                ).fetchall()
            }
            emails = dict(
                self._exec(
                    cur, "SELECT d.id, a.email FROM devices d JOIN accounts a ON a.id = d.account_id"
                ).fetchall()
            )
            return devices, totals, stars, coins, emails

        devices, totals, stars, coins, emails = self._run(run)
        out = []
        for d in devices:
            sessions, total_s, total_usd, today_s, today_usd = totals.get(d.id, (0, 0, 0, 0, 0))
            out.append(
                {
                    **asdict(d),
                    "display_name": d.display_name,
                    "limit_min": d.limit_min(self.limits),
                    "today_min": round(float(today_s) / 60, 1),
                    "today_usd": round(float(today_usd), 4),
                    "total_min": round(float(total_s) / 60, 1),
                    "total_usd": round(float(total_usd), 4),
                    "sessions": int(sessions),
                    "stars": int(stars.get(d.id, 0)),
                    "stars_spent": int(coins.get(d.id, (0, 0))[0] or 0),
                    "coins": int(coins.get(d.id, (0, 0))[1] or 0),
                    "account": emails.get(d.id),
                }
            )
        return sorted(out, key=lambda r: r["last_seen"], reverse=True)

    def usage_by_day(self, days: int = 7, now: datetime | None = None) -> list[dict[str, Any]]:
        """Minutes, sessions and dollars per day and device for the last ``days`` days."""
        today = date.fromisoformat(self.day_of(now))
        since = (today - timedelta(days=max(days, 1) - 1)).isoformat()

        def run(cur: Any) -> list[Any]:
            return self._exec(
                cur,
                """SELECT s.day, s.device_id, COALESCE(MAX(d.label), ''), COALESCE(MAX(d.name), ''), COUNT(*),
                    COALESCE(SUM(s.duration_s), 0), COALESCE(SUM(s.est_usd), 0), COALESCE(a.email, '')
                FROM sessions s LEFT JOIN devices d ON d.id = s.device_id LEFT JOIN accounts a ON a.id = s.account_id
                WHERE s.day >= ? GROUP BY s.day, s.device_id, a.email ORDER BY s.day DESC, a.email, s.device_id""",
                (since,),
            ).fetchall()

        rows = self._run(run)
        return [
            {
                "day": r[0],
                "device_id": r[1],
                "display_name": r[2] or r[3] or r[1],
                "account": r[7] or None,
                "sessions": int(r[4]),
                "minutes": round(float(r[5]) / 60, 1),
                "usd": round(float(r[6]), 4),
            }
            for r in rows
        ]

    # -- accounts' Milo time ---------------------------------------------------------------------

    def milo_of(self, cur: Any, account_id: str, day: str) -> dict[str, Any]:
        """An account's Milo status, limit and minutes on ``day`` (for the owner's views)."""
        account = self._milo_account(cur, account_id)
        if not account:
            return {}
        return {
            "status": account.status,
            "daily_limit_min": account.daily_limit_min,
            "limit_min": 0.0 if account.status == "blocked" else account.limit_min(self.limits),
            "today_min": round(self._account_used_s(cur, account.id, day) / 60, 1),
        }

    def set_milo(
        self,
        cur: Any,
        account_id: str,
        *,
        status: str | None = None,
        daily_limit_min: float | None = None,
        clear_limit: bool = False,
    ) -> None:
        """Change an account's Milo status or custom limit (unblocking is just ``unknown``: accounts
        are never blocked automatically)."""
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if daily_limit_min is not None and daily_limit_min < 0:
            raise ValueError("daily_limit_min must be >= 0")
        if status is not None:
            self._exec(cur, "UPDATE accounts SET milo_status = ? WHERE id = ?", (status, account_id))
        if clear_limit:
            self._exec(cur, "UPDATE accounts SET daily_limit_min = NULL WHERE id = ?", (account_id,))
        elif daily_limit_min is not None:
            self._exec(cur, "UPDATE accounts SET daily_limit_min = ? WHERE id = ?", (daily_limit_min, account_id))

    # -- backup ----------------------------------------------------------------------------------

    def backup(self) -> dict[str, Any]:
        """Every row of every table, as ``{"tables": {name: {"columns": [...], "rows": [[...]]}}}``."""

        def run(cur: Any) -> dict[str, Any]:
            tables = {}
            for table in TABLES:
                cur.execute(f"SELECT * FROM {table}")
                columns = [c[0] for c in cur.description]
                tables[table] = {"columns": columns, "rows": [list(r) for r in cur.fetchall()]}
            return {"kind": self.kind, "taken_at": self.now().isoformat(timespec="seconds"), "tables": tables}

        return self._run(run)

    def restore(self, dump: Mapping[str, Any]) -> dict[str, int]:
        """Replace every table in ``dump`` with its rows, all-or-nothing. Columns the dump lacks keep
        their defaults (e.g. an older backup has no ``account_id``)."""
        tables = dump.get("tables") or {}
        unknown = set(tables) - set(TABLES)
        if unknown:
            raise ValueError(f"unknown table(s) in backup: {', '.join(sorted(unknown))}")

        def run(cur: Any) -> dict[str, int]:
            counts = {}
            for table in TABLES:
                if table not in tables:
                    continue
                columns, rows = tables[table]["columns"], tables[table]["rows"]
                known = self._columns(cur, table)
                if not set(columns) <= known:
                    raise ValueError(f"backup has columns {table} doesn't: {set(columns) - known}")
                cur.execute(f"DELETE FROM {table}")
                marks = ", ".join("?" for _ in columns)
                for row in rows:
                    self._exec(cur, f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", row)
                counts[table] = len(rows)
            return counts

        return self.transaction(run)


def _is_connection_error(exc: BaseException) -> bool:
    try:
        import psycopg

        return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))
    except ImportError:
        return False
