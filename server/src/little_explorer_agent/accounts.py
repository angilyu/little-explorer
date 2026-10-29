"""Grown-ups' accounts: sign in with an emailed code, and the books on each account's shelf.

A grown-up types an email, gets a 6-digit code (:mod:`.mail`) and types it in the app. Signing up
and signing in are the same: the first good code for a new email makes the account. Only the email
is collected, never the child's name. The code is kept as an HMAC (``PIP_AUTH_SECRET``), works for
10 minutes and 5 tries, and asking is rate limited per email, per IP and per day.

A good code returns a session token (only its SHA-256 is stored) that the app keeps in the Keychain
and sends as ``X-Pip-Session``. Signing in also *links* the device to the account (see
:mod:`.devices`): its stars and coins become the account's, shared by every device signed in to it.
The first account a device links to also claims what the device earned before accounts existed.
Milo's daily minutes are the account's too: a new account gets ``PIP_LIMIT_UNKNOWN_MIN`` (10) a
day until the owner approves it (``PIP_LIMIT_APPROVED_MIN``, 60), blocks it or sets its own limit.

Milo needs a grown-up's permission: until the account says yes, the app shows what Milo shares (the
child's voice and the open page go to OpenAI through LiveKit) after every sign-in and behind the grown-up
check when "Talk to Milo" is tapped. "Allow Milo" records ``yes`` for the whole account with
:meth:`Accounts.set_milo_consent`; "Not now" records nothing; the Grown-ups corner's switch records ``yes``
or ``no`` (``milo_consent``: ``yes``, ``no``, or NULL). The app gates Milo on ``yes``; ``/token`` refuses
only ``no``, so builds older than the consent screen keep working.

A new account's shelf is ``PIP_DEFAULT_BOOKS`` (comma-separated ids); the owner can grant more, or
every book (``all_books``), with ``tools/devices.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .devices import DOGFOOD_EMAILS, STAR_ID, STARS_PER_AWARD, DeviceStore, Scope, limit_min, valid_device_id

log = logging.getLogger("little_explorer.accounts")

# Settings key: book making is on for every account ("on"/"off"), see set_bookgen_everyone.
BOOKGEN_EVERYONE = "bookgen.everyone"

DEFAULT_BOOKS = (
    "paris",
    "paris-food",
    "paris-eiffel",
    "paris-louvre",
    "london",
    "london-big-ben",
    "barcelona",
    "barcelona-sagrada",
    "beijing",
    "beijing-great-wall",
    "beijing-forbidden-city",
    "beijing-temple-of-heaven",
    "shanghai",
)
PURPOSES = ("sign_in", "delete")
CODE_DIGITS = 6
CODE_TTL_S = 10 * 60
CODE_TRIES = 5
RESEND_AFTER_S = 60
PER_EMAIL_PER_HOUR = 5
PER_IP_PER_HOUR = 20
DAILY_CAP = 100
MAX_IMPORT = 5000
# legacy_account_id of a device whose earlier stars and coins the owner left out of every account.
UNCLAIMED = "-"
EMAIL = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)+$")
BOOK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
PAGE_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}/[A-Za-z0-9_-]{1,64}$")


def normalize_email(value: object) -> str | None:
    """The address in lower case, or None if it isn't one."""
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    return email if len(email) <= 254 and EMAIL.match(email) else None


def valid_book_id(value: object) -> bool:
    return isinstance(value, str) and bool(BOOK_ID.match(value))


class AuthError(Exception):
    """A refusal the app shows to the grown-up: ``code`` is one of the app's known errors."""

    def __init__(self, code: str, status: int, message: str = "", **extra: Any) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.message = message or code
        self.extra = extra
        self.count_try = False

    def as_json(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    all_books: bool
    books: tuple[str, ...]
    created_at: str
    # Whether a grown-up allowed Milo for the account (True), turned it off (False) or was never asked (None).
    milo_consent: bool | None = None
    # May make books in the cloud: the account's own flag, or book making is on for everyone.
    can_generate: bool = False
    # The hand-made books ``all_books`` stands for (the shelf order of the bundled library). Made books are
    # never part of it: an account only reads the made books it was granted (its own are granted when published).
    catalog: tuple[str, ...] = ()

    @property
    def shelf(self) -> tuple[str, ...]:
        """Every book the account may read: its grants, plus every hand-made book if it has ``all_books``."""
        if not self.all_books:
            return self.books
        catalog = set(self.catalog)
        return self.catalog + tuple(b for b in self.books if b not in catalog)

    def as_json(self) -> dict[str, Any]:
        """For the owner: the real ``all_books`` flag and the account's own grants."""
        return {
            "id": self.id,
            "email": self.email,
            "all_books": self.all_books,
            "books": list(self.books),
            "milo_consent": self.milo_consent,
            "can_generate": self.can_generate,
        }

    def app_json(self) -> dict[str, Any]:
        """For the app: always an explicit shelf. The 1.0 app reads ``all_books: true`` as "every book in the
        manifest", which would include other families' made books, so it is never sent to the app."""
        return {**self.as_json(), "all_books": False, "books": list(self.shelf)}

    def may_read(self, book_id: str) -> bool:
        return book_id in self.shelf


def bundled_catalog() -> tuple[str, ...]:
    """The bundled library's book ids in shelf order (``library.json``)."""
    from .catalog import LIBRARY_FILE, resources_dir

    try:
        order = json.loads((resources_dir() / LIBRARY_FILE).read_text(encoding="utf-8")).get("order") or []
    except (OSError, ValueError) as exc:
        log.warning("could not read the bundled library: %s", exc)
        return ()
    return tuple(b for b in order if isinstance(b, str))


def _cached(load: Callable[[], tuple[str, ...]]) -> Callable[[], tuple[str, ...]]:
    value: list[tuple[str, ...]] = []

    def get() -> tuple[str, ...]:
        if not value:
            value.append(load())
        return value[0]

    return get


@dataclass(frozen=True)
class Session:
    account: Account
    device_id: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Accounts:
    """Accounts, codes and sessions, in the same database as :class:`DeviceStore`."""

    def __init__(
        self,
        store: DeviceStore,
        *,
        secret: str = "",
        default_books: Sequence[str] = DEFAULT_BOOKS,
        review_email: str = "",
        review_code: str = "",
        daily_cap: int = DAILY_CAP,
        clock: Callable[[], float] = time.time,
        bookgen_everyone: bool = False,
    ) -> None:
        self.store = store
        if not secret:
            log.warning("PIP_AUTH_SECRET is not set; sign-in codes won't survive a restart")
            secret = secrets.token_hex(32)
        self._secret = secret.encode()
        self.default_books = tuple(b for b in default_books if valid_book_id(b))
        self.review_email = normalize_email(review_email) or ""
        self.review_code = review_code.strip() if re.fullmatch(r"\d{6}", review_code.strip()) else ""
        self.daily_cap = daily_cap
        self.clock = clock
        self.bookgen_everyone = bookgen_everyone
        # Where all_books accounts get their hand-made books; the web app points it at the served catalog.
        self.catalog: Callable[[], Iterable[str]] = _cached(bundled_catalog)

    @classmethod
    def from_env(cls, store: DeviceStore, env: Mapping[str, str] | None = None) -> Accounts:
        env = os.environ if env is None else env
        books = env.get("PIP_DEFAULT_BOOKS", "").strip()
        return cls(
            store,
            secret=env.get("PIP_AUTH_SECRET", "").strip(),
            default_books=[b.strip() for b in books.split(",") if b.strip()] if books else DEFAULT_BOOKS,
            review_email=env.get("PIP_REVIEW_EMAIL", ""),
            review_code=env.get("PIP_REVIEW_CODE", ""),
            daily_cap=int(env.get("PIP_EMAIL_DAILY_CAP", "").strip() or DAILY_CAP),
            bookgen_everyone=env.get("BOOKGEN_EVERYONE", "").strip().lower() in ("1", "true", "yes", "on"),
        )

    def _hmac(self, *parts: str) -> str:
        return hmac.new(self._secret, "\x1f".join(parts).encode(), hashlib.sha256).hexdigest()

    def _stamp(self) -> str:
        return self.store.now().isoformat(timespec="seconds")

    # -- accounts --------------------------------------------------------------------------------

    def _account(self, cur: Any, column: str, value: str) -> Account | None:
        row = self.store._exec(
            cur,
            f"SELECT id, email, all_books, created_at, milo_consent, can_generate FROM accounts WHERE {column} = ?",
            (value,),
        ).fetchone()
        if not row:
            return None
        books = self.store._exec(
            cur, "SELECT book_id FROM account_books WHERE account_id = ? ORDER BY book_id", (row[0],)
        ).fetchall()
        consent = {"yes": True, "no": False}.get(row[4])
        can_generate = bool(row[5]) or self._bookgen_everyone(cur)
        catalog = tuple(self.catalog()) if row[2] else ()
        return Account(row[0], row[1], bool(row[2]), tuple(b[0] for b in books), row[3], consent, can_generate, catalog)

    def _bookgen_everyone(self, cur: Any) -> bool:
        if self.bookgen_everyone:
            return True
        row = self.store._exec(cur, "SELECT value FROM settings WHERE key = ?", (BOOKGEN_EVERYONE,)).fetchone()
        return bool(row) and row[0] == "on"

    def bookgen_everyone_on(self) -> bool:
        """Book making is on for every account (``BOOKGEN_EVERYONE`` or the owner's setting)."""
        return self.store._run(self._bookgen_everyone)

    def set_bookgen_everyone(self, on: bool) -> bool:
        def run(cur: Any) -> None:
            self.store._exec(cur, "DELETE FROM settings WHERE key = ?", (BOOKGEN_EVERYONE,))
            self.store._exec(
                cur, "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (BOOKGEN_EVERYONE, "on" if on else "off", self._stamp()),
            )

        self.store.transaction(run)
        log.warning("owner turned book making %s for everyone", "on" if on else "off")
        return self.bookgen_everyone_on()

    def set_can_generate(self, ref: str, allow: bool) -> Account:
        """Let one account (by email or id) make books, or stop it."""

        def run(cur: Any) -> Account:
            account = self._by_ref(cur, ref)
            if not account:
                raise LookupError(ref)
            self.store._exec(cur, "UPDATE accounts SET can_generate = ? WHERE id = ?", (int(allow), account.id))
            fresh = self._account(cur, "id", account.id)
            assert fresh is not None
            return fresh

        account = self.store.transaction(run)
        log.info("account %s may make books: %s", account.id, allow)
        return account

    def _by_ref(self, cur: Any, ref: str) -> Account | None:
        email = normalize_email(ref)
        return self._account(cur, "email", email) if email else self._account(cur, "id", ref)

    def get(self, ref: str) -> Account | None:
        """An account by email or id."""
        return self.store._run(lambda cur: self._by_ref(cur, ref))

    def _create(self, cur: Any, email: str, all_books: bool = False) -> Account:
        stamp = self._stamp()
        account_id = uuid.uuid4().hex
        self.store._exec(
            cur,
            "INSERT INTO accounts (id, email, all_books, created_at, last_seen, can_generate) VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, email, int(all_books), stamp, stamp, int(email in DOGFOOD_EMAILS)),
        )
        for book in self.default_books:
            self.store._exec(
                cur,
                "INSERT INTO account_books (account_id, book_id, granted_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (account_id, book, stamp),
            )
        log.info("new account %s", account_id)
        account = self._account(cur, "id", account_id)
        assert account is not None
        return account

    def create(self, email: str, *, all_books: bool = False) -> tuple[Account, bool]:
        """The account for ``email`` (made with the default books if new). Returns ``(account, created)``."""
        address = normalize_email(email)
        if not address:
            raise ValueError("not an email address")

        def run(cur: Any) -> tuple[Account, bool]:
            existing = self._account(cur, "email", address)
            if existing:
                if all_books and not existing.all_books:
                    self.store._exec(cur, "UPDATE accounts SET all_books = 1 WHERE id = ?", (existing.id,))
                    existing = self._account(cur, "id", existing.id)
                return existing, False
            return self._create(cur, address, all_books), True

        return self.store.transaction(run)

    def set_books(
        self, ref: str, *, grant: Iterable[str] = (), revoke: Iterable[str] = (), all_books: bool | None = None
    ) -> Account:
        grant, revoke = list(grant), list(revoke)
        bad = [b for b in grant + revoke if not valid_book_id(b)]
        if bad:
            raise ValueError(f"not book ids: {', '.join(map(str, bad))}")

        def run(cur: Any) -> Account:
            account = self._by_ref(cur, ref)
            if not account:
                raise LookupError(ref)
            stamp = self._stamp()
            for book in grant:
                self.store._exec(
                    cur,
                    "INSERT INTO account_books (account_id, book_id, granted_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    (account.id, book, stamp),
                )
            for book in revoke:
                self.store._exec(cur, "DELETE FROM account_books WHERE account_id = ? AND book_id = ?", (account.id, book))
            if all_books is not None:
                self.store._exec(cur, "UPDATE accounts SET all_books = ? WHERE id = ?", (int(all_books), account.id))
            fresh = self._account(cur, "id", account.id)
            assert fresh is not None
            return fresh

        return self.store.transaction(run)

    # -- devices ---------------------------------------------------------------------------------

    def _link(
        self, cur: Any, account: Account, device_id: str, name: str = "", model: str = "", claim: bool = True
    ) -> bool:
        """Sign ``device_id`` in to ``account``. True if the account owns what the device earned
        before it was ever linked (the app then uploads the hearts and stars it kept locally).
        ``claim=False`` (owner only) leaves a never-linked device's earlier stars and coins out of
        every account: they stay on the server under the device but count for no one."""
        self.store._touch(cur, device_id, name, model, self.store.now())
        row = self.store._exec(cur, "SELECT legacy_account_id FROM devices WHERE id = ?", (device_id,)).fetchone()
        legacy = row[0] if row and row[0] else None
        if legacy is None:
            legacy = account.id if claim else UNCLAIMED
            self.store._exec(cur, "UPDATE devices SET legacy_account_id = ? WHERE id = ?", (legacy, device_id))
            if claim:
                log.info("account %s claims device %s's earlier stars and coins", account.id, device_id)
            else:
                log.info("device %s's earlier stars and coins are left out of account %s", device_id, account.id)
        self.store._exec(cur, "UPDATE devices SET account_id = ? WHERE id = ?", (account.id, device_id))
        if legacy == account.id:
            # Anything the device earned while not linked (before accounts, or while signed out).
            for table in ("stars", "coin_conversions"):
                self.store._exec(
                    cur,
                    f"UPDATE {table} SET account_id = ? WHERE device_id = ? AND account_id IS NULL",
                    (account.id, device_id),
                )
        return legacy == account.id

    def link(self, ref: str, device_id: str, claim: bool = True) -> tuple[Account, bool]:
        """Owner tool: link a device to an account as if it had signed in there. ``claim=False``
        leaves out what the device earned before (only if it was never linked)."""
        if not valid_device_id(device_id):
            raise ValueError("bad device id")

        def run(cur: Any) -> tuple[Account, bool]:
            account = self._by_ref(cur, ref)
            if not account:
                raise LookupError(ref)
            return account, self._link(cur, account, device_id, claim=claim)

        return self.store.transaction(run)

    def unlink(self, device_id: str) -> bool:
        """Owner tool: the device goes back to its own stars (what it earned while linked stays with
        the account). Its sessions end. True if it was linked."""

        def run(cur: Any) -> bool:
            linked = self.store._account_of(cur, device_id) is not None
            self.store._exec(cur, "UPDATE devices SET account_id = NULL WHERE id = ?", (device_id,))
            self.store._exec(cur, "DELETE FROM auth_sessions WHERE device_id = ?", (device_id,))
            return linked

        return self.store.transaction(run)

    def devices_of(self, account_id: str) -> list[dict[str, Any]]:
        def run(cur: Any) -> list[dict[str, Any]]:
            rows = self.store._exec(
                cur,
                """SELECT id, name, model, label, account_id, legacy_account_id, last_seen FROM devices
                WHERE account_id = ? OR legacy_account_id = ? ORDER BY last_seen DESC""",
                (account_id, account_id),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "name": r[3] or r[1] or r[2] or r[0],
                    "linked": r[4] == account_id,
                    "claimed": r[5] == account_id,
                    "last_seen": r[6],
                }
                for r in rows
            ]

        return self.store._run(run)

    # -- codes -----------------------------------------------------------------------------------

    def start(self, email: str, purpose: str = "sign_in", ip: str = "") -> tuple[str | None, int]:
        """Make a code for ``email``. Returns ``(code, resend_after_s)``; the code is None for the
        review address (it uses ``PIP_REVIEW_CODE``). Raises :class:`AuthError` when rate limited."""
        if purpose not in PURPOSES:
            raise AuthError("bad_purpose", 400)
        now = int(self.clock())
        ip_hash = self._hmac("ip", ip) if ip else ""
        if self.review_code and email == self.review_email:
            return None, RESEND_AFTER_S
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"

        def run(cur: Any) -> None:
            ex = self.store._exec
            latest = ex(
                cur, "SELECT MAX(created_s) FROM email_codes WHERE email = ? AND purpose = ?", (email, purpose)
            ).fetchone()[0]
            if latest is not None and now - int(latest) < RESEND_AFTER_S:
                raise AuthError("too_many", 429, "wait a minute", retry_after_s=RESEND_AFTER_S - (now - int(latest)))
            hour = now - 3600
            for column, value, limit in (("email", email, PER_EMAIL_PER_HOUR), ("ip_hash", ip_hash, PER_IP_PER_HOUR)):
                if not value:
                    continue
                rows = ex(
                    cur,
                    f"SELECT created_s FROM email_codes WHERE {column} = ? AND created_s > ? ORDER BY created_s",
                    (value, hour),
                ).fetchall()
                if len(rows) >= limit:
                    raise AuthError("too_many", 429, "too many codes", retry_after_s=int(rows[0][0]) + 3600 - now)
            sent_today = ex(cur, "SELECT COUNT(*) FROM email_codes WHERE created_s > ?", (now - 86400,)).fetchone()[0]
            if sent_today >= self.daily_cap:
                log.error("daily email cap (%d) reached", self.daily_cap)
                raise AuthError("too_many", 429, "too many codes today", retry_after_s=3600)
            # A new code replaces any earlier one; old rows past a day are dropped.
            ex(cur, "UPDATE email_codes SET used = 1 WHERE email = ? AND purpose = ?", (email, purpose))
            ex(cur, "DELETE FROM email_codes WHERE created_s < ?", (now - 2 * 86400,))
            ex(
                cur,
                """INSERT INTO email_codes (id, email, purpose, code_hash, ip_hash, created_s, expires_s)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, email, purpose, self._hmac(email, purpose, code), ip_hash, now, now + CODE_TTL_S),
            )

        self.store.transaction(run)
        return code, RESEND_AFTER_S

    def forget_code(self, email: str, purpose: str) -> None:
        """The email didn't go out: let the grown-up ask again straight away."""
        self.store._run(
            lambda cur: self.store._exec(
                cur, "DELETE FROM email_codes WHERE email = ? AND purpose = ? AND used = 0", (email, purpose)
            )
        )

    def _check_code(self, cur: Any, email: str, purpose: str, code: object) -> None:
        if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code.strip()):
            raise AuthError("bad_code", 400, "the code is 6 digits", tries_left=CODE_TRIES)
        code = code.strip()
        if self.review_code and email == self.review_email and hmac.compare_digest(code, self.review_code):
            return
        ex = self.store._exec
        row = ex(
            cur,
            """SELECT id, code_hash, expires_s, attempts FROM email_codes
            WHERE email = ? AND purpose = ? AND used = 0 ORDER BY created_s DESC LIMIT 1""",
            (email, purpose),
        ).fetchone()
        if not row or int(row[2]) <= int(self.clock()) or int(row[3]) >= CODE_TRIES:
            raise AuthError("expired", 400, "ask for a new code")
        if not hmac.compare_digest(row[1], self._hmac(email, purpose, code)):
            # The caller counts the try after rolling back (see _count_try).
            wrong = AuthError("bad_code", 400, "wrong code", tries_left=CODE_TRIES - int(row[3]) - 1)
            wrong.count_try = True
            raise wrong
        ex(cur, "UPDATE email_codes SET used = 1 WHERE id = ?", (row[0],))

    # -- sessions --------------------------------------------------------------------------------

    def verify(self, email: str, code: object, device: tuple[str, str, str]) -> tuple[str, Account, bool]:
        """Check a sign-in code, make the account if it's new, link the device and start a session.
        Returns ``(token, account, claimed_device)``."""
        device_id, name, model = device

        def run(cur: Any) -> tuple[str, Account, bool]:
            self._check_code(cur, email, "sign_in", code)
            account = self._account(cur, "email", email) or self._create(cur, email)
            claimed = self._link(cur, account, device_id, name, model)
            token = secrets.token_urlsafe(32)
            stamp = self._stamp()
            self.store._exec(
                cur,
                "INSERT INTO auth_sessions (token_hash, account_id, device_id, created_at, last_seen) VALUES (?, ?, ?, ?, ?)",
                (_sha256(token), account.id, device_id, stamp, stamp),
            )
            self.store._exec(cur, "UPDATE accounts SET last_seen = ? WHERE id = ?", (stamp, account.id))
            return token, account, claimed

        try:
            return self.store.transaction(run)
        except AuthError as exc:
            if exc.count_try:
                # The failed try must count even though the transaction rolled back.
                self.store.transaction(lambda cur: self._count_try(cur, email, "sign_in"))
            raise

    def _count_try(self, cur: Any, email: str, purpose: str) -> None:
        row = self.store._exec(
            cur,
            """SELECT id, attempts FROM email_codes WHERE email = ? AND purpose = ? AND used = 0
            ORDER BY created_s DESC LIMIT 1""",
            (email, purpose),
        ).fetchone()
        if row:
            attempts = int(row[1]) + 1
            self.store._exec(
                cur,
                "UPDATE email_codes SET attempts = ?, used = ? WHERE id = ?",
                (attempts, int(attempts >= CODE_TRIES), row[0]),
            )

    def session(self, token: str, *, touch: bool = True) -> Session | None:
        """The signed-in session; ``touch=False`` only reads (no ``last_seen`` stamp)."""
        if not token or len(token) > 200:
            return None

        def run(cur: Any) -> Session | None:
            row = self.store._exec(
                cur, "SELECT account_id, device_id, last_seen FROM auth_sessions WHERE token_hash = ?", (_sha256(token),)
            ).fetchone()
            if not row:
                return None
            account = self._account(cur, "id", row[0])
            if not account:
                return None
            stamp = self._stamp()
            if touch and row[2][:13] != stamp[:13]:  # at most once an hour
                self.store._exec(
                    cur, "UPDATE auth_sessions SET last_seen = ? WHERE token_hash = ?", (stamp, _sha256(token))
                )
                self.store._exec(cur, "UPDATE accounts SET last_seen = ? WHERE id = ?", (stamp, account.id))
            return Session(account, row[1])

        return self.store._run(run)

    def sign_out(self, token: str) -> None:
        """End this session. The device stops sharing the account's stars once no session of the
        account is left on it."""

        def run(cur: Any) -> None:
            row = self.store._exec(
                cur, "SELECT account_id, device_id FROM auth_sessions WHERE token_hash = ?", (_sha256(token),)
            ).fetchone()
            if not row:
                return
            self.store._exec(cur, "DELETE FROM auth_sessions WHERE token_hash = ?", (_sha256(token),))
            left = self.store._exec(
                cur, "SELECT COUNT(*) FROM auth_sessions WHERE account_id = ? AND device_id = ?", row
            ).fetchone()[0]
            if not left:
                self.store._exec(
                    cur, "UPDATE devices SET account_id = NULL WHERE id = ? AND account_id = ?", (row[1], row[0])
                )

        self.store.transaction(run)

    def delete(self, account_id: str, email: str, code: object) -> dict[str, int]:
        """Delete the account and everything it owns, after a ``delete`` code sent to its email."""

        def run(cur: Any) -> dict[str, int]:
            self._check_code(cur, email, "delete", code)
            return self._erase(cur, account_id, email)

        try:
            return self.store.transaction(run)
        except AuthError as exc:
            if exc.count_try:
                self.store.transaction(lambda cur: self._count_try(cur, email, "delete"))
            raise

    def _erase(self, cur: Any, account_id: str, email: str) -> dict[str, int]:
        ex = self.store._exec
        counts = {}
        for table, column in (
            ("stars", "account_id"),
            ("coin_conversions", "account_id"),
            ("hearts", "account_id"),
            ("account_books", "account_id"),
            ("auth_sessions", "account_id"),
            ("reading_events", "account_id"),
        ):
            counts[table] = ex(cur, f"DELETE FROM {table} WHERE {column} = ?", (account_id,)).rowcount
        # Books the account made (their R2 objects are removed by the caller, see bookgen.BookStore).
        counts["book_assets"] = ex(
            cur, "DELETE FROM book_assets WHERE book_id IN (SELECT id FROM books WHERE account_id = ?)", (account_id,)
        ).rowcount
        counts["books"] = ex(cur, "DELETE FROM books WHERE account_id = ?", (account_id,)).rowcount
        # Its jobs lose everything about the account but keep their day and cost, so the global daily spend
        # cap still counts money already spent; a job still going stops at its next step.
        counts["generation_jobs"] = ex(
            cur,
            """UPDATE generation_jobs SET account_id = '', input = '{}', error = '',
                status = CASE WHEN status IN ('queued', 'running') THEN 'failed' ELSE status END, lease_until = 0
               WHERE account_id = ?""",
            (account_id,),
        ).rowcount
        ex(cur, "DELETE FROM email_codes WHERE email = ?", (email,))
        # Session minutes stay as the device's (they're how much Milo was used, not the account's).
        ex(cur, "UPDATE sessions SET account_id = NULL WHERE account_id = ?", (account_id,))
        ex(cur, "UPDATE devices SET account_id = NULL WHERE account_id = ?", (account_id,))
        ex(cur, "UPDATE devices SET legacy_account_id = NULL WHERE legacy_account_id = ?", (account_id,))
        ex(cur, "DELETE FROM accounts WHERE id = ?", (account_id,))
        log.warning("deleted account %s (%s)", account_id, counts)
        return counts

    # -- the account's things --------------------------------------------------------------------

    def hearts(self, account_id: str) -> list[str]:
        rows = self.store._run(
            lambda cur: self.store._exec(
                cur, "SELECT page_key FROM hearts WHERE account_id = ? ORDER BY loved_at, page_key", (account_id,)
            ).fetchall()
        )
        return [r[0] for r in rows]

    def _love(self, cur: Any, account_id: str, keys: Iterable[str], stamp: str) -> None:
        for key in keys:
            self.store._exec(
                cur,
                "INSERT INTO hearts (account_id, page_key, loved_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (account_id, key, stamp),
            )

    def set_hearts(self, account_id: str, loved: Iterable[object] = (), unloved: Iterable[object] = ()) -> list[str]:
        loved_keys = [k for k in loved if isinstance(k, str) and PAGE_KEY.match(k)][:MAX_IMPORT]
        unloved_keys = [k for k in unloved if isinstance(k, str) and PAGE_KEY.match(k)][:MAX_IMPORT]

        def run(cur: Any) -> None:
            self._love(cur, account_id, loved_keys, self._stamp())
            for key in unloved_keys:
                self.store._exec(cur, "DELETE FROM hearts WHERE account_id = ? AND page_key = ?", (account_id, key))

        self.store.transaction(run)
        return self.hearts(account_id)

    def import_device(
        self, session: Session, stars: Sequence[object] = (), hearts: Sequence[object] = ()
    ) -> dict[str, int]:
        """What the app kept locally before accounts (hearts were only ever on the device; stars may
        be missing on the server if a backup failed). Only for a device whose earlier things the
        account claimed. Safe to repeat: stars are kept by id, hearts are a set."""

        def run(cur: Any) -> dict[str, int]:
            row = self.store._exec(
                cur, "SELECT legacy_account_id FROM devices WHERE id = ?", (session.device_id,)
            ).fetchone()
            if not row or row[0] != session.account.id:
                raise AuthError("not_claimed", 403, "this device's earlier stars belong to another account")
            stamp = self._stamp()
            added = 0
            for award in list(stars)[:MAX_IMPORT]:
                if not isinstance(award, Mapping) or not STAR_ID.match(str(award.get("id") or "")):
                    continue
                try:
                    values = list(self.store.star_values(award, session.device_id, self.store.day_of, stamp))
                except (ValueError, TypeError):
                    continue
                # Keep the count the app shows (older awards could be worth more than one star).
                values[12] = max(1, min(int(award.get("stars") or STARS_PER_AWARD), 5))
                added += self.store._insert_star(cur, Scope(account_id=session.account.id), tuple(values), capped=False)
            keys = [k for k in list(hearts)[:MAX_IMPORT] if isinstance(k, str) and PAGE_KEY.match(k)]
            before = self.store._exec(
                cur, "SELECT COUNT(*) FROM hearts WHERE account_id = ?", (session.account.id,)
            ).fetchone()[0]
            self._love(cur, session.account.id, keys, stamp)
            after = self.store._exec(
                cur, "SELECT COUNT(*) FROM hearts WHERE account_id = ?", (session.account.id,)
            ).fetchone()[0]
            return {"stars_added": added, "hearts_added": int(after) - int(before)}

        return self.store.transaction(run)

    def set_milo_consent(self, account_id: str, allow: bool) -> Account:
        """A grown-up allowed Milo for the account, or turned Milo off (see :mod:`.token_server`)."""

        def run(cur: Any) -> Account:
            self.store._exec(
                cur,
                "UPDATE accounts SET milo_consent = ?, milo_consent_at = ? WHERE id = ?",
                ("yes" if allow else "no", self._stamp(), account_id),
            )
            account = self._account(cur, "id", account_id)
            if not account:
                raise LookupError(account_id)
            return account

        account = self.store.transaction(run)
        log.info("account %s Milo consent -> %s", account_id, "yes" if allow else "no")
        return account

    # -- owner -----------------------------------------------------------------------------------

    def milo(self, account_id: str) -> dict[str, Any]:
        """The account's Milo status, limit and minutes today."""
        day = self.store.day_of()
        return self.store._run(lambda cur: self.store.milo_of(cur, account_id, day))

    def set_milo(
        self, ref: str, *, status: str | None = None, daily_limit_min: float | None = None, clear_limit: bool = False
    ) -> Account:
        """Approve, block or unblock (``unknown``) an account found by email or id, or set its limit."""

        def run(cur: Any) -> Account:
            account = self._by_ref(cur, ref)
            if not account:
                raise LookupError(ref)
            self.store.set_milo(
                cur, account.id, status=status, daily_limit_min=daily_limit_min, clear_limit=clear_limit
            )
            return account

        account = self.store.transaction(run)
        log.info("account %s Milo -> status %s, limit %s", account.id, status, None if clear_limit else daily_limit_min)
        return account

    def summary(self) -> list[dict[str, Any]]:
        """Every account with its devices, totals and Milo time, for ``GET /admin/accounts``."""
        day = self.store.day_of()
        limits = self.store.limits

        def run(cur: Any) -> list[tuple[Any, ...]]:
            return self.store._exec(
                cur,
                """SELECT a.id, a.email, a.all_books, a.created_at, a.last_seen,
                    (SELECT COUNT(*) FROM devices d WHERE d.account_id = a.id),
                    (SELECT COUNT(*) FROM account_books b WHERE b.account_id = a.id),
                    (SELECT COALESCE(SUM(s.stars), 0) FROM stars s WHERE s.account_id = a.id),
                    (SELECT COALESCE(SUM(c.stars_spent), 0) FROM coin_conversions c WHERE c.account_id = a.id),
                    (SELECT COALESCE(SUM(c.coins), 0) FROM coin_conversions c WHERE c.account_id = a.id),
                    (SELECT COUNT(*) FROM hearts h WHERE h.account_id = a.id),
                    a.milo_status, a.daily_limit_min,
                    (SELECT COALESCE(SUM(m.duration_s), 0) FROM sessions m WHERE m.account_id = a.id AND m.day = ?),
                    a.milo_consent, a.can_generate
                FROM accounts a ORDER BY a.created_at""",
                (day,),
            ).fetchall()

        return [
            {
                "id": r[0],
                "email": r[1],
                "all_books": bool(r[2]),
                "created_at": r[3],
                "last_seen": r[4],
                "devices": int(r[5]),
                "books": int(r[6]),
                "stars": int(r[7]),
                "stars_spent": int(r[8]),
                "coins": int(r[9]),
                "hearts": int(r[10]),
                "milo_status": r[11],
                "daily_limit_min": r[12],
                "limit_min": 0.0 if r[11] == "blocked" else limit_min(r[11], r[12], limits),
                "today_min": round(float(r[13] or 0) / 60, 1),
                "milo_consent": {"yes": True, "no": False}.get(r[14]),
                "can_generate": bool(r[15]),
            }
            for r in self.store._run(run)
        ]
