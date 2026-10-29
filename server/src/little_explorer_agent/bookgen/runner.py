"""The free, in-process book maker: one asyncio task in the web service, one book at a time.

It claims a job with a lease (:meth:`.store.BookStore.claim`), renews the lease after every page,
and runs the steps text → photos → narration → publish. A job whose lease ran out (a deploy or a
crash) is claimed again and resumes from the last finished step: pages that already have their
photo or clip in R2 (``book_assets``) are skipped, so nothing paid for is made twice.

Safety fails closed: anything flagged rejects the book, and a check that can't run is retried
later, never skipped. Failed and rejected books have their R2 files deleted.

A grown-up can ask for one page of a published book again (a ``page`` job): the new page is written,
fact-checked and checked, gets its own photo and clip under new names (so the shelf keeps the old page
until the end), and is swapped in at once (:meth:`.store.BookStore.replace_page`). If it fails, only the
new files are deleted; the book stays as it was.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from datetime import timedelta
from typing import Any

from . import moderation, narration, writer
from .config import BookgenConfig
from .cost import CostLog
from .moderation import Rejected
from .photos import MODERATION_WIDTH, BadPhoto, Commons, to_jpeg
from .store import Asset, BookStore, Job
from .writer import BookError

log = logging.getLogger(__name__)

MIN_PHOTO_SHARE = 0.75


class LeaseLost(Exception):
    """Another worker owns the job now (or the owner cancelled it): stop without writing."""


def page_tag(job: Job) -> str:
    """Names a remade page's files apart from the page it replaces (and from other tries)."""
    return job.id[:8]


def page_paths(job: Job) -> tuple[str, str, str]:
    """A page job's photo file, clip name and the asset paths it may write."""
    tag = page_tag(job)
    page = str(job.input.get("page") or "")
    photo = f"{job.book_id}-{page}-{tag}.jpg"
    clip = f"{narration.clip_name(job.book_id, page)}-{tag}"
    return photo, clip, f"Photos/{photo}|Narration/{clip}.m4a"


def r2_key(book_id: str, file: str, attempt: int) -> str:
    # Each attempt writes its own objects, so a worker that lost its lease can't overwrite (or delete) the files
    # of the worker that took over; the manifest path is the same either way (see Asset.r2_key).
    return f"books/{book_id}/a{attempt}/{file}"


class Runner:
    def __init__(self, books: BookStore, config: BookgenConfig, *, client: Any, storage: Any,
                 commons: Commons | None = None) -> None:
        self.books = books
        self.config = config
        self.client = client
        self.storage = storage
        self.commons = commons or Commons()
        self._wake = asyncio.Event()
        # Set while the runner sleeps with no timer, until woken (no database access in the meantime).
        self.asleep = asyncio.Event()
        self.state: dict[str, Any] = {"running": False, "job": None, "step": None, "last_error": None, "paused": False,
                                      "next_check_s": None}

    def wake(self) -> None:
        self._wake.set()

    async def close(self) -> None:
        await self.commons.close()
        close = getattr(self.client, "close", None)
        if close:
            await close()

    # -- the loop ----------------------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Make books one at a time. Idle, it touches no database (Neon's free plan scales to zero): it checks
        for work at startup and when woken (a book was queued or an owner acted), and only arms a timer while a
        job is waiting (a retry's backoff, another worker's lease, or the daily spending cap)."""
        self.state["running"] = True
        errors = 0
        try:
            while True:
                self._wake.clear()
                try:
                    worked = await self.run_once()
                    errors = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the loop must keep going
                    log.exception("book runner")
                    self.state["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
                    worked = False
                    self.state["next_check_s"] = min(3600.0, self.config.poll_s * 2 ** errors)
                    errors += 1
                if worked:
                    continue
                delay = self.state.get("next_check_s")
                if delay is None:
                    self.asleep.set()
                    try:
                        await self._wake.wait()
                    finally:
                        self.asleep.clear()
                    continue
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.state["running"] = False

    async def run_once(self) -> bool:
        """Claim and run one job. False when there was nothing to do (or spending is paused); then
        ``state["next_check_s"]`` says when to look again (None: not until woken)."""
        self.state["next_check_s"] = None
        spent = await asyncio.to_thread(self.books.spent_today)
        self.state["paused"] = spent >= self.config.daily_usd_cap
        if self.state["paused"]:
            due = await asyncio.to_thread(self.books.next_due)
            if due is not None:
                self.state["next_check_s"] = max(due, self._until_tomorrow())
            return False
        job = await asyncio.to_thread(self.books.claim)
        if job is None:
            self.state["next_check_s"] = await asyncio.to_thread(self.books.next_due)
            return False
        self.state.update(job=job.id, step=job.step)
        try:
            await self.run_job(job)
        finally:
            self.state.update(job=None, step=None)
        return True

    def _until_tomorrow(self) -> float:
        now = self.books.store.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=1, microsecond=0)
        return max(1.0, (tomorrow - now).total_seconds())

    # -- one job -----------------------------------------------------------------------------------

    async def run_job(self, job: Job) -> None:
        costs = CostLog(list(job.costs))
        try:
            if job.attempts > self.config.max_attempts:
                raise BookError(f"gave up after {job.attempts - 1} tries: {job.error or 'unknown error'}")
            await self._steps(job, costs)
        except LeaseLost:
            log.warning("job %s: lease lost; stopping", job.id)
            current = await asyncio.to_thread(self.books.job, job.id)
            if current and current.status in ("failed", "rejected"):
                await self.cleanup_job(job)
        except Rejected as exc:
            log.info("job %s rejected: %s", job.id, exc.reason)
            await asyncio.to_thread(self.books.finish, job, "rejected", error=exc.reason, costs=costs.items)
            await self.cleanup_job(job)
        except BookError as exc:
            log.warning("job %s failed: %s", job.id, exc)
            await asyncio.to_thread(self.books.finish, job, "failed", error=str(exc), costs=costs.items)
            await self.cleanup_job(job)
        except asyncio.CancelledError:
            # Shutting down: the lease runs out and the next instance resumes the job.
            raise
        except Exception as exc:  # noqa: BLE001 - OpenAI, Commons, R2 or ffmpeg trouble: try again later
            error = f"{type(exc).__name__}: {exc}"[:300]
            self.state["last_error"] = error
            if job.attempts >= self.config.max_attempts:
                log.warning("job %s failed after %d tries: %s", job.id, job.attempts, error)
                await asyncio.to_thread(self.books.finish, job, "failed", error=error, costs=costs.items)
                await self.cleanup_job(job)
            else:
                delay = min(3600, 60 * 2 ** job.attempts)
                log.warning("job %s: %s; retrying in %ds", job.id, error, delay)
                await asyncio.to_thread(self.books.release, job, delay_s=delay, error=error, costs=costs.items)

    async def _renew(self, job: Job, costs: CostLog, step: str | None = None) -> None:
        if costs.total > self.config.job_usd_cap:
            raise BookError(f"cost ${costs.total:.2f} is over the per-book cap ${self.config.job_usd_cap:.2f}")
        if not await asyncio.to_thread(self.books.renew, job, step=step, costs=costs.items):
            raise LeaseLost(job.id)
        if step:
            self.state["step"] = step

    async def _steps(self, job: Job, costs: CostLog) -> None:
        if job.kind == "page":
            await self._page_steps(job, costs)
            return
        row = await asyncio.to_thread(self.books.book, job.book_id)
        if row is None:
            raise BookError("the book's row is missing")
        request = job.input
        level = int(request["level"])

        book = row.json
        if not book.get("pages"):
            await self._renew(job, costs, "text")
            await moderation.check_request(self.client, self.config, request, costs)
            draft = await writer.write_draft(self.client, self.config, request, costs)
            await self._renew(job, costs)
            draft = await writer.fact_check(self.client, self.config, request, draft, costs)
            book = writer.assemble(job.book_id, request, draft)
            await moderation.check_texts(self.client, self.config, writer.all_texts(book), "book")
            await self._renew(job, costs)
            if not await asyncio.to_thread(self.books.save_book, job, book=book):
                raise LeaseLost(job.id)

        await self._renew(job, costs, "photos")
        await self._photos(job, book, costs)

        await self._renew(job, costs, "narration")
        clips = dict(row.narration or {})
        assets = await asyncio.to_thread(self.books.assets, job.book_id)
        for page in book["pages"]:
            key = f"{book['id']}/{page['id']}"
            path = f"Narration/{narration.clip_name(book['id'], page['id'])}.m4a"
            if path in assets and (clips.get(key) or {}).get("text") == page["text"]:
                continue
            m4a, clip = await narration.narrate(self.client, self.config, book["id"], page["id"], page["text"], level, costs)
            clips[key] = clip
            await self._upload(job, path, m4a, "audio/mp4", narration=clips)
            del m4a
            await self._renew(job, costs)

        await self._renew(job, costs, "publish")
        if not await asyncio.to_thread(self.books.publish, job, costs.items):
            raise LeaseLost(job.id)
        log.info("job %s: published %s for $%.4f", job.id, book["id"], costs.total)

    async def _page_steps(self, job: Job, costs: CostLog) -> None:
        row = await asyncio.to_thread(self.books.book, job.book_id)
        if row is None or row.status != "published":
            raise BookError("the book isn't on the shelf any more")
        book = row.json
        page_id = str(job.input.get("page") or "")
        old = next((p for p in book.get("pages") or [] if p.get("id") == page_id), None)
        if old is None:
            raise BookError(f"the book has no page {page_id}")
        # A retry starts over: forget files an earlier try of this job left behind.
        await self.cleanup_job(job)
        photo_file, clip_name, _ = page_paths(job)

        await self._renew(job, costs, "text")
        page = await writer.rewrite_page(self.client, self.config, book, page_id, costs)
        await moderation.check_texts(self.client, self.config, [page["text"], page["about"], page["emoji"]], "page")

        await self._renew(job, costs, "photos")
        used = {p["photo"].get("commonsTitle") for p in book["pages"] if p["photo"].get("commonsTitle")}
        if not await self._find_photo(job, page, photo_file, used):
            raise BookError("no photo for the new page")

        await self._renew(job, costs, "narration")
        m4a, clip = await narration.narrate(self.client, self.config, book["id"], page_id, page["text"],
                                            int(book.get("level") or 1), costs)
        clip = {**clip, "file": clip_name}
        await self._upload(job, f"Narration/{clip_name}.m4a", m4a, "audio/mp4")
        del m4a

        await self._renew(job, costs, "publish")
        old_clip = (row.narration or {}).get(f"{book['id']}/{page_id}") or {}
        drop = [f"Photos/{(old.get('photo') or {}).get('file')}", f"Narration/{old_clip.get('file')}.m4a"]
        keys = await asyncio.to_thread(self.books.replace_page, job, page, clip, drop, costs.items)
        if keys is None:
            raise LeaseLost(job.id)
        await delete_keys(self.storage, keys)
        log.info("job %s: remade page %s of %s for $%.4f", job.id, page_id, book["id"], costs.total)

    async def _find_photo(self, job: Job, page: dict[str, Any], file: str, used: set[str],
                          book: dict[str, Any] | None = None) -> bool:
        """Find a safe, credited Commons photo for ``page``, upload it as ``file`` and set the page's photo."""
        queries = [page["photo"].get("query", "")] + list(page["photo"].get("alternates") or [])
        queries = [q for q in queries if q]
        for candidate in await self.commons.candidates(queries, used):
            try:
                photo = await self.commons.download(candidate)
            except BadPhoto as exc:
                log.info("job %s: %s; trying the next one", job.id, exc)
                continue
            small = await asyncio.to_thread(to_jpeg, photo.data, MODERATION_WIDTH, 80)
            if not await moderation.image_ok(self.client, self.config, small):
                log.info("job %s: photo %s flagged; trying the next one", job.id, candidate.get("title"))
                continue
            credits = photo.credits(file, queries[0] if queries else "")
            try:
                # The credit line is shown under the photo, and Commons authors write their own.
                await moderation.check_texts(self.client, self.config, writer.credit_texts(credits), "photo credit")
            except Rejected:
                log.info("job %s: photo %s credit flagged; trying the next one", job.id, candidate.get("title"))
                continue
            used.add(photo.title)
            page["photo"] = credits
            await self._upload(job, f"Photos/{file}", photo.data, "image/jpeg", book=book)
            return True
        return False

    async def _photos(self, job: Job, book: dict[str, Any], costs: CostLog) -> None:
        assets = await asyncio.to_thread(self.books.assets, job.book_id)
        used = {p["photo"].get("commonsTitle") for p in book["pages"] if p["photo"].get("commonsTitle")}
        for page in book["pages"]:
            file = f"{book['id']}-{page['id']}.jpg"
            if page["photo"].get("file") == file and f"Photos/{file}" in assets:
                continue
            await self._find_photo(job, page, file, used, book=book)
            await self._renew(job, costs)
        have = sum(1 for p in book["pages"] if p["photo"].get("file"))
        if have < math.ceil(MIN_PHOTO_SHARE * len(book["pages"])):
            raise BookError(f"found free photos for only {have} of {len(book['pages'])} pages")

    async def _upload(self, job: Job, path: str, data: bytes, content_type: str, *,
                      book: dict[str, Any] | None = None, narration: dict[str, Any] | None = None) -> None:
        key = r2_key(job.book_id, path.rsplit("/", 1)[-1], job.attempts)
        await self.storage.put(key, data, content_type)
        asset = Asset(job.book_id, path, key, hashlib.sha256(data).hexdigest(), len(data))
        if not await asyncio.to_thread(self.books.add_asset, job, asset, book=book, narration=narration):
            await self._delete(key)
            raise LeaseLost(job.id)

    async def _delete(self, key: str) -> None:
        try:
            await self.storage.delete(key)
        except Exception as exc:  # noqa: BLE001 - best effort
            log.warning("could not delete %s from R2: %s", key, exc)

    async def cleanup_job(self, job: Job) -> None:
        """After a job fails: a new book loses all its files; a remade page loses only its own new ones."""
        if job.kind == "page":
            await cleanup_page(self.books, self.storage, job)
        else:
            await self.cleanup(job.book_id)

    async def cleanup(self, book_id: str) -> None:
        """Delete a failed or removed book's files from R2 (best effort) and forget them."""
        await delete_keys(self.storage, await asyncio.to_thread(self.books.asset_keys, book_id=book_id))
        await asyncio.to_thread(self.books.drop_assets, book_id)


async def cleanup_page(books: BookStore, storage: Any, job: Job) -> None:
    """Delete a page job's own new files (never the published book's)."""
    paths = page_paths(job)[2].split("|")
    assets = await asyncio.to_thread(books.assets, job.book_id)
    keys = [assets[p].r2_key for p in paths if p in assets]
    if keys:
        await delete_keys(storage, keys)
        await asyncio.to_thread(books.drop_assets, job.book_id, paths)


async def delete_keys(storage: Any, keys: list[str]) -> int:
    deleted = 0
    for key in keys:
        try:
            await storage.delete(key)
            deleted += 1
        except Exception as exc:  # noqa: BLE001 - best effort
            log.warning("could not delete %s from R2: %s", key, exc)
    return deleted
