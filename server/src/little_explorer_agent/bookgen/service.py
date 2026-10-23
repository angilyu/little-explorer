"""The book-making service the web server wires in: config, store, R2, the runner, and each account's
manifest of made books.

The public catalog (``/content/manifest.json``) is the hand-made books only and never touches the database;
made books are listed per account (:meth:`BookgenService.account_manifest`), so another family's books and the
1.0 app never see them. The runner sleeps until a book is queued, so an idle service never touches the
database either (Neon's free plan scales to zero)."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from pathlib import Path
from typing import Any

from ..devices import DeviceStore
from ..levels import levels_json
from . import manifest as account_files
from .config import BookgenConfig
from .runner import Runner, cleanup_page, delete_keys
from .store import Asset, BookRow, BookStore, Job

log = logging.getLogger(__name__)


class BookgenService:
    def __init__(self, store: DeviceStore, *, static_manifest: dict[str, Any], static_files: dict[str, Path],
                 config: BookgenConfig | None = None, storage: Any = None, client: Any = None,
                 commons: Any = None) -> None:
        self.config = config or BookgenConfig.from_env()
        self.books = BookStore(store, self.config)
        if storage is None and self.config.r2.ready:
            from .storage import R2Storage

            storage = R2Storage(self.config.r2)
        self.storage = storage
        self.static = copy.deepcopy(static_manifest)
        self.static_files = static_files
        # The public manifest: the hand-made catalog, served as-is.
        self.manifest: dict[str, Any] = static_manifest
        self.runner: Runner | None = None
        if self.storage is not None and (client is not None or self.config.openai_key) and self.config.runner:
            if client is None:
                from .oai import OpenAIClient

                client = OpenAIClient(self.config.openai_key)
            self.runner = Runner(self.books, self.config, client=client, storage=self.storage, commons=commons)
        self._tasks: list[asyncio.Task[Any]] = []

    @property
    def ready(self) -> bool:
        """Books can be queued: there's storage and someone (this instance's runner) to make them."""
        return self.storage is not None and self.runner is not None

    # -- an account's made books -------------------------------------------------------------------

    async def account_manifest(self, account_id: str) -> tuple[dict[str, Any], dict[str, bytes | Asset]]:
        """The made books on this account's shelf: a manifest the app syncs like the catalog, and its files."""
        if self.storage is None:
            return {"format": 1, "version": "none", "order": [], "files": {}}, {}
        shelf = await asyncio.to_thread(self.books.shelf_books, account_id)
        return account_files.account_manifest(shelf, self.static, self.url_for)

    async def shelf_book(self, account_id: str, book_id: str) -> tuple[BookRow, dict[str, Asset]] | None:
        found = await asyncio.to_thread(self.books.shelf_books, account_id, book_id)
        return found[0] if found else None

    async def made_books(self, account_id: str) -> list[dict[str, Any]]:
        """The book JSON of every made book on the account's shelf (for Milo's page notes)."""
        if self.storage is None:
            return []
        shelf = await asyncio.to_thread(self.books.shelf_books, account_id)
        return [book.json for book, _ in shelf]

    def url_for(self, asset: Asset) -> str:
        return self.storage.presign(asset.r2_key, self.config.asset_url_ttl_s)

    # -- owner actions -----------------------------------------------------------------------------

    def queue(self, account_id: str, request: dict[str, Any]) -> Job:
        """Queue a book (blocking; call :meth:`wake` from the event loop afterwards)."""
        job, _ = self.books.queue(account_id, request)
        return job

    def wake(self) -> None:
        if self.runner:
            self.runner.wake()

    async def cancel(self, job_id: str) -> Job:
        job = await asyncio.to_thread(self.books.cancel, job_id)
        if job.kind == "page" and job.status == "failed" and self.storage is not None:
            await cleanup_page(self.books, self.storage, job)
        elif job.status in ("failed", "rejected"):
            book = await asyncio.to_thread(self.books.book, job.book_id)
            if book and book.status != "published":
                await delete_keys(self.storage, await asyncio.to_thread(self.books.asset_keys, book_id=job.book_id))
                await asyncio.to_thread(self.books.drop_assets, job.book_id)
        return job

    def queue_page(self, account_id: str, book_id: str, page_id: str) -> Job:
        """Queue "make this page again" (blocking; call :meth:`wake` afterwards)."""
        return self.books.queue_page(account_id, book_id, page_id)

    async def set_published(self, book_id: str, published: bool) -> dict[str, Any]:
        book = await asyncio.to_thread(self.books.set_published, book_id, published)
        return book.as_json()

    async def delete_book(self, book_id: str) -> int:
        keys = await asyncio.to_thread(self.books.delete_book, book_id)
        return await delete_keys(self.storage, keys) if self.storage is not None else 0

    async def forget_account(self, keys: list[str]) -> None:
        """After an account is erased: delete its books' files from R2."""
        if keys and self.storage is not None:
            await delete_keys(self.storage, keys)

    async def status(self) -> dict[str, Any]:
        spent = await asyncio.to_thread(self.books.spent_today)
        return {
            "ready": self.ready,
            "openai": bool(self.config.openai_key),
            "storage": self.storage is not None,
            "runner": dict(self.runner.state) if self.runner else None,
            "limits": self.config.limits_json(),
            "spent_today_usd": round(spent, 4),
            "levels": levels_json(),
            "made_books_on_shelf": len(await asyncio.to_thread(self.books.published_ids)),
        }

    # -- lifecycle ---------------------------------------------------------------------------------

    async def start(self) -> None:
        if self.runner is not None:
            self._tasks.append(asyncio.create_task(self.runner.run_forever()))
            log.info("book runner started (one book at a time, limits %s)", self.config.limits_json())

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        if self.runner is not None:
            await self.runner.close()
        close = getattr(self.storage, "close", None)
        if close:
            await close()
