"""Which devices may talk to Milo, and for how long each day.

Each app install sends a stable device id (kept in the Keychain) with its name and model. The token
server records it here and checks its minutes for today before handing out a token; the agent
reports every session's length and estimated cost when it ends (``POST /usage``).

A device is ``unknown`` until the owner approves it, ``approved``, or ``blocked``:

* unknown devices get ``PIP_LIMIT_UNKNOWN_MIN`` (10) minutes a day. Going over blocks them until
  the owner unblocks or approves them.
* approved devices get ``PIP_LIMIT_APPROVED_MIN`` (60) minutes a day, then rest until tomorrow.
* a per-device ``daily_limit_min`` overrides either.

Days run midnight to midnight in ``PIP_TIMEZONE`` (America/Los_Angeles).

Storage is Postgres when ``DATABASE_URL`` is set (the deployed token server uses a free Neon
database, because Render's free disk is wiped on every deploy and restart), otherwise SQLite at
``PIP_DEVICES_DB`` (``~/.config/little-explorer/devices.sqlite3``) for local runs and tests.
Star awards (see :mod:`.stars`) are kept here too, so a reinstall can restore them, and so are the
coin ledger and the owner's coin rules (see :mod:`.coins`).
Only ids, names, models, timings, book ids, costs and star and coin counts are stored; never transcripts or audio.
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

    @property
    def display_name(self) -> str:
        return self.label or self.name or self.id

    def limit_min(self, limits: Limits) -> float:
        if self.daily_limit_min is not None:
            return float(self.daily_limit_min)
        return limits.approved_min if self.status == "approved" else limits.unknown_min


@dataclass(frozen=True)
class Decision:
    """Whether a device may start a session now, and for how many seconds at most."""

    allowed: bool
    reason: str  # ok | blocked | limit
    remaining_s: float
    used_s: float = 0.0
    limit_s: float = 0.0
    device: Device | None = None


_DEVICE_COLUMNS = "id, name, model, label, status, daily_limit_min, unblocked_on, first_seen, last_seen"


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
                        self._ready = True
                    return fn(cur)
                except Exception as exc:
                    if attempt == 2 or not _is_connection_error(exc):
                        raise
                    log.info("database connection dropped; reconnecting")
                    self.close_quietly()
        raise AssertionError("unreachable")

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

    def check(self, device_id: str, name: str = "", model: str = "", now: datetime | None = None) -> Decision:
        """Record that ``device_id`` asked to talk, and decide whether it may (see module docs)."""
        now = now or self.now()
        day = self.day_of(now)

        def run(cur: Any) -> tuple[Device, float, float]:
            device = self._touch(cur, device_id, _text(name), _text(model), now)
            used, limit = self._over_limit(cur, device, day)
            if device.status != "blocked" and limit - used < self.limits.min_start_s:
                device = self._auto_block(cur, device, day, max(used, limit), limit)
            return device, used, limit

        device, used, limit = self._run(run)
        if device.status == "blocked":
            return Decision(False, "blocked", 0.0, used, limit, device)
        remaining = max(limit - used, 0.0)
        if remaining < self.limits.min_start_s:
            return Decision(False, "limit", remaining, used, limit, device)
        return Decision(True, "ok", remaining, used, limit, device)

    def record_session(self, report: Mapping[str, Any]) -> Device:
        """Store one finished session (idempotent per room) and block an unknown device that went over."""
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
            self._exec(
                cur,
                """INSERT INTO sessions (room, device_id, started_at, day, duration_s, mode, model, book,
                    pages, responses, est_usd, ended_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (room) DO UPDATE SET duration_s = excluded.duration_s, pages = excluded.pages,
                    responses = excluded.responses, est_usd = excluded.est_usd,
                    ended_reason = excluded.ended_reason""",
                values,
            )
            used, limit = self._over_limit(cur, device, day)
            return self._auto_block(cur, device, day, used, limit)

        return self._run(run)

    # -- stars -----------------------------------------------------------------------------------

    def record_star(self, award: Mapping[str, Any]) -> bool:
        """Store one star award from the agent; True if it was new (the id makes retries safe).

        Any award counts as one star, and a page that already has :data:`STARS_PER_PAGE` stars on
        this device that day (any session; the day is :func:`award_day`, the device's local date)
        gets no more, whatever build of the agent sent it."""
        star_id, device_id = award.get("id"), award.get("device_id") or ANONYMOUS
        if not isinstance(star_id, str) or not STAR_ID.match(star_id) or not valid_device_id(device_id):
            raise ValueError("a valid id and device_id are required")
        stars = award.get("stars")
        if isinstance(stars, bool) or not isinstance(stars, int) or stars < 1:
            raise ValueError("stars must be a positive whole number")
        stars = STARS_PER_AWARD
        kind = award.get("kind") if award.get("kind") in STAR_KINDS else "question"
        awarded = str(award.get("awarded_at") or self.now().isoformat(timespec="seconds"))[:40]
        values = (
            star_id,
            device_id,
            _text(award.get("room")),
            awarded,
            self.day_of(awarded),
            _text(award.get("book")),
            _text(award.get("page")),
            _text(award.get("city")),
            kind,
            int(bool(award.get("long"))),
            int(bool(award.get("why_how"))),
            int(bool(award.get("followup"))),
            stars,
            max(int(award.get("words") or 0), 0),
            _text(award.get("reason")),
        )

        book, page = values[5], values[6]

        def run(cur: Any) -> bool:
            if book and page:
                rows = self._exec(
                    cur,
                    "SELECT awarded_at, stars FROM stars WHERE device_id = ? AND book = ? AND page = ? AND id <> ?",
                    (device_id, book, page, star_id),
                ).fetchall()
                today = award_day(awarded)
                on_page = sum(int(n or 0) for at, n in rows if award_day(at) == today)
                if on_page >= STARS_PER_PAGE:
                    return False
            self._exec(
                cur,
                """INSERT INTO stars (id, device_id, room, awarded_at, day, book, page, city, kind, long, why_how,
                    followup, stars, words, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING""",
                values,
            )
            return cur.rowcount == 1

        return self._run(run)

    def stars_for(self, device_id: str) -> list[dict[str, Any]]:
        """Every award a device has earned, oldest first (for restoring after a reinstall)."""
        rows = self._run(
            lambda cur: self._exec(
                cur, f"SELECT {_STAR_COLUMNS} FROM stars WHERE device_id = ? ORDER BY awarded_at, id", (device_id,)
            ).fetchall()
        )
        keys = [k.strip() for k in _STAR_COLUMNS.split(",")]
        out = []
        for row in rows:
            item = dict(zip(keys, row))
            for flag in ("long", "why_how", "followup"):
                item[flag] = bool(item[flag])
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
            return devices, totals, stars, coins

        devices, totals, stars, coins = self._run(run)
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
                    COALESCE(SUM(s.duration_s), 0), COALESCE(SUM(s.est_usd), 0)
                FROM sessions s LEFT JOIN devices d ON d.id = s.device_id
                WHERE s.day >= ? GROUP BY s.day, s.device_id ORDER BY s.day DESC, s.device_id""",
                (since,),
            ).fetchall()

        rows = self._run(run)
        return [
            {
                "day": r[0],
                "device_id": r[1],
                "display_name": r[2] or r[3] or r[1],
                "sessions": int(r[4]),
                "minutes": round(float(r[5]) / 60, 1),
                "usd": round(float(r[6]), 4),
            }
            for r in rows
        ]


def _is_connection_error(exc: BaseException) -> bool:
    try:
        import psycopg

        return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))
    except ImportError:
        return False
