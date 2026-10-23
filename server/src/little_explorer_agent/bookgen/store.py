"""The database side of book making: jobs, books and their R2 assets (tables in :mod:`..devices`).

A job is queued by the owner (later by a parent), then run by the one in-process runner
(:mod:`.runner`). The runner *claims* a job with a lease (``lease_until``, epoch seconds) and renews
it at every step; every write it makes is fenced by the claim's ``attempts`` number, so a worker
that lost its lease (e.g. the old instance during a deploy) can't overwrite the new one's work.
A job whose lease ran out is claimed again and resumes from its last finished step.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..devices import DeviceStore
from .config import BookgenConfig

STEPS = ("text", "photos", "narration", "publish")
ACTIVE = ("queued", "running")
COUNTED = ("queued", "running", "done")  # failed and rejected jobs don't use up the quota
# Postgres advisory lock that makes claiming and queueing one at a time across instances.
LOCK_KEY = 7_270_421
_JOB_COLUMNS = ("id, account_id, book_id, status, step, input, error, attempts, lease_until, run_after, cost_usd, "
                "costs, day, month, created_at, updated_at, finished_at, kind")
REPORT_REASONS = ("wrong", "scary", "photo", "other")
NOTE_MAX = 300

# What a grown-up sees while a book is made (the app shows the same words under the placeholder cover).
STEP_WORDS = {"text": "Writing…", "photos": "Finding photos…", "narration": "Recording…", "publish": "Almost ready…"}
SOMETHING_WRONG = "Something went wrong while making this book. It didn't count toward your limit. Please try again."


def friendly_error(status: str, error: str) -> str | None:
    """The reason a grown-up sees for a book that wasn't made (the owner sees the raw ``error``)."""
    if status == "rejected":
        return (f"This book couldn't be made: {error} " if error else "This book couldn't be made. ") + \
            "It didn't count toward your limit. Try a different idea."
    if status != "failed":
        return None
    if error.startswith(("found free photos for only", "no photo for the new page")):
        return "We couldn't find enough free photos for this idea. It didn't count toward your limit. Try another topic."
    if "deleted" in error:
        return "This book was deleted."
    if "cancelled" in error:
        return "This book was stopped. It didn't count toward your limit."
    return SOMETHING_WRONG
_BOOK_COLUMNS = "id, account_id, job_id, title, section, level, status, json, narration, created_at, updated_at, published_at"


class LimitError(Exception):
    """A request the limits refuse; ``code`` is stable for the app, ``message`` is for a grown-up."""

    def __init__(self, code: str, message: str, status: int = 429) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def as_json(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


@dataclass
class Job:
    id: str
    account_id: str
    book_id: str
    status: str
    step: str
    input: dict[str, Any]
    error: str
    attempts: int
    lease_until: int
    run_after: int
    cost_usd: float
    costs: list[dict[str, Any]]
    day: str
    month: str
    created_at: str
    updated_at: str
    finished_at: str | None
    kind: str = "book"

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> Job:
        values = list(row)
        values[5] = json.loads(values[5] or "{}")
        values[11] = json.loads(values[11] or "[]")
        values[7], values[8], values[9] = int(values[7]), int(values[8]), int(values[9])
        values[10] = float(values[10] or 0)
        return cls(*values)

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "account_id": self.account_id, "book_id": self.book_id, "status": self.status,
            "step": self.step, "input": self.input, "error": self.error, "attempts": self.attempts,
            "cost_usd": round(self.cost_usd, 4), "costs": self.costs, "created_at": self.created_at,
            "updated_at": self.updated_at, "finished_at": self.finished_at, "kind": self.kind,
        }

    def parent_json(self) -> dict[str, Any]:
        """For the grown-up who asked: no costs or raw errors."""
        active = self.status in ACTIVE
        return {
            "id": self.id, "book_id": self.book_id, "kind": self.kind, "status": self.status,
            "step": self.step, "progress": STEP_WORDS.get(self.step, "Writing…") if active else None,
            "title": self.input.get("title", ""), "section": self.input.get("section", ""),
            "level": self.input.get("level"), "page": self.input.get("page"),
            "message": friendly_error(self.status, self.error), "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


@dataclass
class BookRow:
    id: str
    account_id: str
    job_id: str
    title: str
    section: str
    level: int
    status: str
    json: dict[str, Any]
    narration: dict[str, Any]
    created_at: str
    updated_at: str
    published_at: str | None

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> BookRow:
        values = list(row)
        values[5] = int(values[5])
        values[7] = json.loads(values[7] or "{}")
        values[8] = json.loads(values[8] or "{}")
        return cls(*values)

    def as_json(self, *, full: bool = False) -> dict[str, Any]:
        out = {
            "id": self.id, "account_id": self.account_id, "job_id": self.job_id, "title": self.title,
            "section": self.section, "level": self.level, "status": self.status,
            "pages": len(self.json.get("pages") or []), "created_at": self.created_at,
            "published_at": self.published_at,
        }
        if full:
            out["book"] = self.json
            out["narration"] = self.narration
        return out


@dataclass(frozen=True)
class Asset:
    book_id: str
    path: str
    r2_key: str
    sha256: str
    size: int


def slug(text: str, limit: int = 32) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:limit].strip("-")


def new_book_id(title: str) -> str:
    return f"{slug(title) or 'book'}-{secrets.token_hex(3)}"


class BookStore:
    def __init__(self, store: DeviceStore, config: BookgenConfig | None = None, clock: Any = time.time) -> None:
        self.store = store
        self.config = config or BookgenConfig()
        self.clock = clock

    # -- plumbing ----------------------------------------------------------------------------------

    def _ex(self, cur: Any, sql: str, params: Sequence[Any] = ()) -> Any:
        return self.store._exec(cur, sql, params)

    def _lock(self, cur: Any) -> None:
        if self.store.url:
            self._ex(cur, "SELECT pg_advisory_xact_lock(?)", (LOCK_KEY,))

    def _stamp(self) -> str:
        return self.store.now().isoformat(timespec="seconds")

    def _now_s(self) -> int:
        return int(self.clock())

    def _job(self, cur: Any, job_id: str) -> Job | None:
        row = self._ex(cur, f"SELECT {_JOB_COLUMNS} FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def _book(self, cur: Any, book_id: str) -> BookRow | None:
        row = self._ex(cur, f"SELECT {_BOOK_COLUMNS} FROM books WHERE id = ?", (book_id,)).fetchone()
        return BookRow.from_row(row) if row else None

    def _spent(self, cur: Any, day: str) -> float:
        row = self._ex(cur, "SELECT COALESCE(SUM(cost_usd), 0) FROM generation_jobs WHERE day = ?", (day,)).fetchone()
        return float(row[0] or 0)

    def _count(self, cur: Any, account_id: str, column: str, value: str, kind: str = "book",
               statuses: Sequence[str] = COUNTED) -> int:
        marks = ", ".join("?" for _ in statuses)
        row = self._ex(
            cur,
            f"""SELECT COUNT(*) FROM generation_jobs WHERE account_id = ? AND {column} = ? AND kind = ?
                AND status IN ({marks})""",
            (account_id, value, kind, *statuses),
        ).fetchone()
        return int(row[0])

    def _tries(self, cur: Any, account_id: str, day: str) -> int:
        """Books asked for today, including ones turned down or failed (they don't use the quota)."""
        return self._count(cur, account_id, "day", day, statuses=(*COUNTED, "failed", "rejected"))

    def _check_queue(self, cur: Any, account_id: str, day: str) -> None:
        """The limits every job shares: one at a time and the day's spending cap."""
        marks = ", ".join("?" for _ in ACTIVE)
        busy = self._ex(
            cur, f"SELECT 1 FROM generation_jobs WHERE account_id = ? AND status IN ({marks})", (account_id, *ACTIVE)
        ).fetchone()
        if busy:
            raise LimitError("busy", "A book is already being made. Please wait until it's ready.")
        if self._spent(cur, day) >= self.config.daily_usd_cap:
            raise LimitError("paused", "Book making is resting for today. Try again tomorrow.", 503)

    # -- queueing ----------------------------------------------------------------------------------

    def spent_today(self) -> float:
        day = self.store.day_of()
        return self.store._run(lambda cur: self._spent(cur, day))

    def usage(self, account_id: str) -> dict[str, Any]:
        """How much of its quota an account has used, for the owner and (later) the parent page."""
        day = self.store.day_of()

        def run(cur: Any) -> dict[str, Any]:
            marks = ", ".join("?" for _ in ACTIVE)
            active = self._ex(
                cur, f"SELECT COUNT(*) FROM generation_jobs WHERE account_id = ? AND status IN ({marks})",
                (account_id, *ACTIVE),
            ).fetchone()[0]
            cfg = self.config
            today, month = self._count(cur, account_id, "day", day), self._count(cur, account_id, "month", day[:7])
            redos = self._count(cur, account_id, "day", day, "page")
            return {"active": int(active), "today": today, "this_month": month,
                    "per_day": cfg.per_day, "per_month": cfg.per_month,
                    "left_today": max(0, min(cfg.per_day - today, cfg.per_month - month)),
                    "left_this_month": max(0, cfg.per_month - month),
                    "tries_today": self._tries(cur, account_id, day), "tries_per_day": cfg.tries_per_day,
                    "page_redos_today": redos, "page_redos_per_day": cfg.page_redos_per_day,
                    "page_redos_left": max(0, cfg.page_redos_per_day - redos)}

        return self.store._run(run)

    def queue(self, account_id: str, request: dict[str, Any]) -> tuple[Job, BookRow]:
        """Queue a book for ``account_id`` after checking its limits and today's spend.

        ``request`` is already validated (see :func:`.writer.validate_request`).
        """
        day = self.store.day_of()
        cfg = self.config

        def run(cur: Any) -> tuple[Job, BookRow]:
            self._lock(cur)
            self._check_queue(cur, account_id, day)
            if self._count(cur, account_id, "day", day) >= cfg.per_day:
                raise LimitError("day_limit", f"You can make {cfg.per_day} books a day. Try again tomorrow.")
            if self._count(cur, account_id, "month", day[:7]) >= cfg.per_month:
                raise LimitError("month_limit", f"You can make {cfg.per_month} books a month. Try again next month.")
            if self._tries(cur, account_id, day) >= cfg.tries_per_day:
                raise LimitError("tries_limit", "That's a lot of tries for one day. Try again tomorrow.")
            stamp = self._stamp()
            job_id, book_id = uuid.uuid4().hex, new_book_id(request["title"])
            self._ex(
                cur,
                """INSERT INTO generation_jobs (id, account_id, book_id, status, step, input, day, month, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', 'text', ?, ?, ?, ?, ?)""",
                (job_id, account_id, book_id, json.dumps(request, ensure_ascii=False), day, day[:7], stamp, stamp),
            )
            self._ex(
                cur,
                """INSERT INTO books (id, account_id, job_id, title, section, level, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (book_id, account_id, job_id, request["title"], request["section"], int(request["level"]), stamp, stamp),
            )
            job, book = self._job(cur, job_id), self._book(cur, book_id)
            assert job and book
            return job, book

        return self.store.transaction(run)

    def queue_page(self, account_id: str, book_id: str, page_id: str) -> Job:
        """Queue "make this page again" for a published book the account made. It doesn't use a book from the
        quota (it's a tenth of the work); it has its own daily limit and shares the one-at-a-time rule and the
        spending cap."""
        day = self.store.day_of()
        cfg = self.config

        def run(cur: Any) -> Job:
            self._lock(cur)
            book = self._book(cur, book_id)
            if not book or book.account_id != account_id:
                raise LookupError(book_id)
            if book.status != "published":
                raise ValueError("This book isn't on the shelf.")
            if not any(p.get("id") == page_id for p in book.json.get("pages") or []):
                raise LookupError(page_id)
            self._check_queue(cur, account_id, day)
            if self._count(cur, account_id, "day", day, "page") >= cfg.page_redos_per_day:
                raise LimitError("page_limit", f"You can remake {cfg.page_redos_per_day} pages a day. Try again tomorrow.")
            stamp = self._stamp()
            job_id = uuid.uuid4().hex
            request = {"title": book.title, "section": book.section, "level": book.level,
                       "about": str(self._input_of(cur, book.job_id).get("about") or ""), "page": page_id}
            self._ex(
                cur,
                """INSERT INTO generation_jobs (id, account_id, book_id, status, step, input, day, month, created_at,
                    updated_at, kind) VALUES (?, ?, ?, 'queued', 'text', ?, ?, ?, ?, ?, 'page')""",
                (job_id, account_id, book_id, json.dumps(request, ensure_ascii=False), day, day[:7], stamp, stamp),
            )
            job = self._job(cur, job_id)
            assert job
            return job

        return self.store.transaction(run)

    def _input_of(self, cur: Any, job_id: str) -> dict[str, Any]:
        job = self._job(cur, job_id)
        return job.input if job else {}

    # -- the runner's side -------------------------------------------------------------------------

    def claim(self) -> Job | None:
        """Resume a job whose lease ran out, else take the oldest queued one, unless another job holds
        a live lease (one book at a time). Returns it with ``attempts`` bumped (the fence for later writes)."""
        now_s = self._now_s()
        lease = now_s + int(self.config.lease_s)
        lock_rows = " FOR UPDATE SKIP LOCKED" if self.store.url else ""

        def run(cur: Any) -> Job | None:
            self._lock(cur)
            live = self._ex(
                cur, "SELECT 1 FROM generation_jobs WHERE status = 'running' AND lease_until >= ?", (now_s,)
            ).fetchone()
            if live:
                return None
            row = self._ex(
                cur,
                f"""SELECT id FROM generation_jobs
                WHERE (status = 'queued' AND run_after <= ?) OR (status = 'running' AND lease_until < ?)
                ORDER BY CASE WHEN status = 'running' THEN 0 ELSE 1 END, created_at, id LIMIT 1{lock_rows}""",
                (now_s, now_s),
            ).fetchone()
            if not row:
                return None
            self._ex(
                cur,
                """UPDATE generation_jobs SET status = 'running', attempts = attempts + 1, lease_until = ?,
                    updated_at = ? WHERE id = ?""",
                (lease, self._stamp(), row[0]),
            )
            return self._job(cur, row[0])

        return self.store.transaction(run)

    def next_due(self) -> float | None:
        """Seconds until :meth:`claim` may find work (a retry's backoff or another worker's lease running out),
        or None when no job is queued or running, so an idle runner can sleep without touching the database."""
        now_s = self._now_s()
        rows = self.store._run(lambda cur: self._ex(
            cur, "SELECT status, lease_until, run_after FROM generation_jobs WHERE status IN (?, ?)", ACTIVE
        ).fetchall())
        if not rows:
            return None
        live = [int(lease) for status, lease, _ in rows if status == "running" and int(lease) >= now_s]
        if live:
            return float(min(live) - now_s + 1)
        due = min(int(after) if status == "queued" else now_s for status, _, after in rows)
        return float(max(1, due - now_s))

    def renew(self, job: Job, *, step: str | None = None, costs: list[dict[str, Any]] | None = None) -> bool:
        """Extend the lease (and save the step and costs). False if another worker owns the job now."""
        lease = self._now_s() + int(self.config.lease_s)
        sets, params = ["lease_until = ?", "updated_at = ?"], [lease, self._stamp()]
        if step is not None:
            sets.append("step = ?")
            params.append(step)
        if costs is not None:
            sets += ["costs = ?", "cost_usd = ?"]
            params += [json.dumps(costs), round(sum(float(c.get("usd") or 0) for c in costs), 6)]

        def run(cur: Any) -> bool:
            done = self._ex(
                cur,
                f"UPDATE generation_jobs SET {', '.join(sets)} WHERE id = ? AND status = 'running' AND attempts = ?",
                (*params, job.id, job.attempts),
            ).rowcount
            return done == 1

        ok = self.store._run(run)
        if ok:
            job.lease_until = lease
            if step is not None:
                job.step = step
        return ok

    def _close(self, job: Job, status: str, *, error: str = "", costs: list[dict[str, Any]] | None = None,
               run_after: int = 0) -> bool:
        sets = ["status = ?", "error = ?", "updated_at = ?", "lease_until = 0", "run_after = ?"]
        params: list[Any] = [status, error[:500], self._stamp(), run_after]
        if status in ("done", "failed", "rejected"):
            sets.append("finished_at = ?")
            params.append(self._stamp())
        if costs is not None:
            sets += ["costs = ?", "cost_usd = ?"]
            params += [json.dumps(costs), round(sum(float(c.get("usd") or 0) for c in costs), 6)]

        def run(cur: Any) -> bool:
            done = self._ex(
                cur,
                f"UPDATE generation_jobs SET {', '.join(sets)} WHERE id = ? AND status = 'running' AND attempts = ?",
                (*params, job.id, job.attempts),
            ).rowcount
            if done == 1 and status in ("failed", "rejected"):
                self._ex(cur, "UPDATE books SET status = ?, updated_at = ? WHERE id = ? AND status = 'draft'",
                         (status, self._stamp(), job.book_id))
            return done == 1

        return self.store.transaction(run)

    def finish(self, job: Job, status: str, *, error: str = "", costs: list[dict[str, Any]] | None = None) -> bool:
        return self._close(job, status, error=error, costs=costs)

    def release(self, job: Job, *, delay_s: int, error: str = "", costs: list[dict[str, Any]] | None = None) -> bool:
        """Put a job back in the queue to try again after ``delay_s`` (e.g. OpenAI was down)."""
        return self._close(job, "queued", error=error, costs=costs, run_after=self._now_s() + delay_s)

    def save_book(self, job: Job, *, book: dict[str, Any] | None = None, narration: dict[str, Any] | None = None) -> bool:
        sets, params = ["updated_at = ?"], [self._stamp()]
        if book is not None:
            sets.append("json = ?")
            params.append(json.dumps(book, ensure_ascii=False))
        if narration is not None:
            sets.append("narration = ?")
            params.append(json.dumps(narration, ensure_ascii=False))

        def run(cur: Any) -> bool:
            if not self._fenced(cur, job):
                return False
            self._ex(cur, f"UPDATE books SET {', '.join(sets)} WHERE id = ?", (*params, job.book_id))
            return True

        return self.store.transaction(run)

    def _fenced(self, cur: Any, job: Job) -> bool:
        row = self._ex(cur, "SELECT status, attempts FROM generation_jobs WHERE id = ?", (job.id,)).fetchone()
        return bool(row) and row[0] == "running" and int(row[1]) == job.attempts

    def add_asset(self, job: Job, asset: Asset, *, narration: dict[str, Any] | None = None,
                  book: dict[str, Any] | None = None) -> bool:
        """Record an uploaded photo or clip (and the book or clips that now use it) in one go."""

        def run(cur: Any) -> bool:
            if not self._fenced(cur, job):
                return False
            self._ex(cur, "DELETE FROM book_assets WHERE book_id = ? AND path = ?", (asset.book_id, asset.path))
            self._ex(
                cur,
                "INSERT INTO book_assets (book_id, path, r2_key, sha256, size, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (asset.book_id, asset.path, asset.r2_key, asset.sha256, asset.size, self._stamp()),
            )
            if book is not None:
                self._ex(cur, "UPDATE books SET json = ? WHERE id = ?", (json.dumps(book, ensure_ascii=False), job.book_id))
            if narration is not None:
                self._ex(cur, "UPDATE books SET narration = ? WHERE id = ?",
                         (json.dumps(narration, ensure_ascii=False), job.book_id))
            return True

        return self.store.transaction(run)

    def publish(self, job: Job, costs: list[dict[str, Any]]) -> bool:
        """Put the book on the account's shelf and finish the job, all at once."""
        stamp = self._stamp()

        def run(cur: Any) -> bool:
            if not self._fenced(cur, job):
                return False
            self._ex(cur, "UPDATE books SET status = 'published', published_at = ?, updated_at = ? WHERE id = ?",
                     (stamp, stamp, job.book_id))
            self._ex(
                cur,
                "INSERT INTO account_books (account_id, book_id, granted_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (job.account_id, job.book_id, stamp),
            )
            self._ex(
                cur,
                """UPDATE generation_jobs SET status = 'done', step = 'publish', error = '', lease_until = 0,
                    finished_at = ?, updated_at = ?, costs = ?, cost_usd = ? WHERE id = ?""",
                (stamp, stamp, json.dumps(costs), round(sum(float(c.get("usd") or 0) for c in costs), 6), job.id),
            )
            return True

        return self.store.transaction(run)

    # -- reading -----------------------------------------------------------------------------------

    def job(self, job_id: str) -> Job | None:
        return self.store._run(lambda cur: self._job(cur, job_id))

    def book(self, book_id: str) -> BookRow | None:
        return self.store._run(lambda cur: self._book(cur, book_id))

    def jobs(self, limit: int = 50) -> list[Job]:
        rows = self.store._run(lambda cur: self._ex(
            cur, f"SELECT {_JOB_COLUMNS} FROM generation_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall())
        return [Job.from_row(r) for r in rows]

    def books(self, account_id: str | None = None) -> list[BookRow]:
        where, params = ("WHERE account_id = ?", (account_id,)) if account_id else ("", ())
        rows = self.store._run(lambda cur: self._ex(
            cur, f"SELECT {_BOOK_COLUMNS} FROM books {where} ORDER BY created_at", params
        ).fetchall())
        return [BookRow.from_row(r) for r in rows]

    def assets(self, book_id: str) -> dict[str, Asset]:
        rows = self.store._run(lambda cur: self._ex(
            cur, "SELECT book_id, path, r2_key, sha256, size FROM book_assets WHERE book_id = ?", (book_id,)
        ).fetchall())
        return {r[1]: Asset(r[0], r[1], r[2], r[3], int(r[4])) for r in rows}

    def published(self) -> list[tuple[BookRow, dict[str, Asset]]]:
        """Every published book with its assets, oldest first (for the manifest)."""

        def run(cur: Any) -> list[tuple[BookRow, dict[str, Asset]]]:
            books = [BookRow.from_row(r) for r in self._ex(
                cur, f"SELECT {_BOOK_COLUMNS} FROM books WHERE status = 'published' ORDER BY published_at, id"
            ).fetchall()]
            out = []
            for book in books:
                rows = self._ex(
                    cur, "SELECT book_id, path, r2_key, sha256, size FROM book_assets WHERE book_id = ?", (book.id,)
                ).fetchall()
                out.append((book, {r[1]: Asset(r[0], r[1], r[2], r[3], int(r[4])) for r in rows}))
            return out

        return self.store._run(run)

    def asset_keys(self, *, account_id: str | None = None, book_id: str | None = None) -> list[str]:
        if book_id:
            sql, params = "SELECT r2_key FROM book_assets WHERE book_id = ?", (book_id,)
        else:
            sql = "SELECT r2_key FROM book_assets WHERE book_id IN (SELECT id FROM books WHERE account_id = ?)"
            params = (account_id or "",)
        return [r[0] for r in self.store._run(lambda cur: self._ex(cur, sql, params).fetchall())]

    def drop_assets(self, book_id: str, paths: Sequence[str] | None = None) -> int:
        """Forget a failed book's assets, or just ``paths`` (the caller deletes them from R2)."""
        if paths is None:
            return self.store.transaction(
                lambda cur: self._ex(cur, "DELETE FROM book_assets WHERE book_id = ?", (book_id,)).rowcount
            )

        def run(cur: Any) -> int:
            return sum(self._ex(cur, "DELETE FROM book_assets WHERE book_id = ? AND path = ?", (book_id, p)).rowcount
                       for p in paths)

        return self.store.transaction(run)

    def replace_page(self, job: Job, page: dict[str, Any], clip: dict[str, Any], drop: Sequence[str],
                     costs: list[dict[str, Any]]) -> list[str] | None:
        """Swap a remade page (and its clip) into the published book and finish the job, all at once.
        Forgets the old page's files in ``drop`` and returns their R2 keys; None if the job isn't ours any more."""
        stamp = self._stamp()

        def run(cur: Any) -> list[str] | None:
            if not self._fenced(cur, job):
                return None
            book = self._book(cur, job.book_id)
            if not book or book.status != "published":
                return None
            data = book.json
            data["pages"] = [page if p.get("id") == page["id"] else p for p in data.get("pages") or []]
            clips = dict(book.narration or {})
            clips[f"{book.id}/{page['id']}"] = clip
            keys = []
            for path in drop:
                row = self._ex(cur, "SELECT r2_key FROM book_assets WHERE book_id = ? AND path = ?",
                               (book.id, path)).fetchone()
                if row:
                    keys.append(row[0])
                    self._ex(cur, "DELETE FROM book_assets WHERE book_id = ? AND path = ?", (book.id, path))
            self._ex(cur, "UPDATE books SET json = ?, narration = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(data, ensure_ascii=False), json.dumps(clips, ensure_ascii=False), stamp, book.id))
            self._ex(
                cur,
                """UPDATE generation_jobs SET status = 'done', step = 'publish', error = '', lease_until = 0,
                    finished_at = ?, updated_at = ?, costs = ?, cost_usd = ? WHERE id = ?""",
                (stamp, stamp, json.dumps(costs), round(sum(float(c.get("usd") or 0) for c in costs), 6), job.id),
            )
            return keys

        return self.store.transaction(run)

    # -- a grown-up's own books --------------------------------------------------------------------

    def _with_assets(self, cur: Any, books: list[BookRow]) -> list[tuple[BookRow, dict[str, Asset]]]:
        out = []
        for book in books:
            rows = self._ex(
                cur, "SELECT book_id, path, r2_key, sha256, size FROM book_assets WHERE book_id = ?", (book.id,)
            ).fetchall()
            out.append((book, {r[1]: Asset(r[0], r[1], r[2], r[3], int(r[4])) for r in rows}))
        return out

    def shelf_books(self, account_id: str, book_id: str | None = None) -> list[tuple[BookRow, dict[str, Asset]]]:
        """The published made books on an account's shelf (its own and any the owner granted it), oldest first,
        with their assets; or just ``book_id`` if it's one of them."""
        one, params = ("AND b.id = ?", (account_id, book_id)) if book_id else ("", (account_id,))
        columns = ", ".join(f"b.{c.strip()}" for c in _BOOK_COLUMNS.split(","))

        def run(cur: Any) -> list[tuple[BookRow, dict[str, Asset]]]:
            books = [BookRow.from_row(r) for r in self._ex(
                cur,
                f"""SELECT {columns} FROM books b JOIN account_books g ON g.book_id = b.id
                WHERE g.account_id = ? AND b.status = 'published' {one} ORDER BY b.published_at, b.id""",
                params,
            ).fetchall()]
            return self._with_assets(cur, books)

        return self.store._run(run)

    def account_jobs(self, account_id: str, limit: int = 10) -> list[Job]:
        rows = self.store._run(lambda cur: self._ex(
            cur, f"SELECT {_JOB_COLUMNS} FROM generation_jobs WHERE account_id = ? ORDER BY created_at DESC, id LIMIT ?",
            (account_id, limit),
        ).fetchall())
        return [Job.from_row(r) for r in rows]

    def published_ids(self) -> set[str]:
        rows = self.store._run(lambda cur: self._ex(cur, "SELECT id FROM books WHERE status = 'published'").fetchall())
        return {r[0] for r in rows}

    def add_report(self, account_id: str, book: BookRow, reason: str, note: str, page: str) -> str:
        report_id = uuid.uuid4().hex
        self.store.transaction(lambda cur: self._ex(
            cur,
            """INSERT INTO book_reports (id, account_id, book_id, book_owner, title, reason, note, page, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_id, account_id, book.id, book.account_id, book.title, reason, note, page, self._stamp()),
        ))
        return report_id

    def reports(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.store._run(lambda cur: self._ex(
            cur,
            """SELECT id, account_id, book_id, book_owner, title, reason, note, page, status, created_at
            FROM book_reports ORDER BY created_at DESC, id LIMIT ?""",
            (limit,),
        ).fetchall())
        keys = ("id", "account_id", "book_id", "book_owner", "title", "reason", "note", "page", "status", "created_at")
        return [dict(zip(keys, r)) for r in rows]

    def close_report(self, report_id: str) -> bool:
        return self.store.transaction(lambda cur: self._ex(
            cur, "UPDATE book_reports SET status = 'closed' WHERE id = ?", (report_id,)
        ).rowcount) == 1

    # -- owner -------------------------------------------------------------------------------------

    def set_published(self, book_id: str, published: bool) -> BookRow:
        """The owner's kill switch: take a published book off every shelf, or put it back."""
        stamp = self._stamp()

        def run(cur: Any) -> BookRow:
            book = self._book(cur, book_id)
            if not book:
                raise LookupError(book_id)
            if book.status not in ("published", "unpublished"):
                raise ValueError(f"book is {book.status}, not finished")
            self._ex(cur, "UPDATE books SET status = ?, updated_at = ? WHERE id = ?",
                     ("published" if published else "unpublished", stamp, book_id))
            fresh = self._book(cur, book_id)
            assert fresh
            return fresh

        return self.store.transaction(run)

    def cancel(self, job_id: str) -> Job:
        """Stop a queued or running job (a running one stops at its next step)."""
        stamp = self._stamp()

        def run(cur: Any) -> Job:
            job = self._job(cur, job_id)
            if not job:
                raise LookupError(job_id)
            if job.status in ACTIVE:
                self._ex(cur, """UPDATE generation_jobs SET status = 'failed', error = 'cancelled by the owner',
                    lease_until = 0, finished_at = ?, updated_at = ? WHERE id = ?""", (stamp, stamp, job_id))
                self._ex(cur, "UPDATE books SET status = 'failed', updated_at = ? WHERE id = ? AND status = 'draft'",
                         (stamp, job.book_id))
            fresh = self._job(cur, job_id)
            assert fresh
            return fresh

        return self.store.transaction(run)

    def delete_book(self, book_id: str) -> list[str]:
        """Remove a book everywhere (its job stays for the spend log). Returns its R2 keys to delete."""

        def run(cur: Any) -> list[str]:
            if not self._book(cur, book_id):
                raise LookupError(book_id)
            keys = [r[0] for r in self._ex(cur, "SELECT r2_key FROM book_assets WHERE book_id = ?", (book_id,)).fetchall()]
            self._ex(cur, "DELETE FROM book_assets WHERE book_id = ?", (book_id,))
            self._ex(cur, "DELETE FROM account_books WHERE book_id = ?", (book_id,))
            self._ex(cur, "DELETE FROM books WHERE id = ?", (book_id,))
            self._ex(cur, """UPDATE generation_jobs SET status = 'failed', error = 'book deleted by the owner',
                lease_until = 0, updated_at = ? WHERE book_id = ? AND status IN ('queued', 'running')""",
                     (self._stamp(), book_id))
            return keys

        return self.store.transaction(run)
