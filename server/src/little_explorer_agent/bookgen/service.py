"""The book-making service the web server wires in: config, store, R2, the runner and the
manifest with made books."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import math
import time
from pathlib import Path
from typing import Any

from ..devices import DeviceStore
from ..levels import levels_json
from . import manifest as manifest_merge
from .config import BookgenConfig
from .runner import Runner, delete_keys
from .store import Asset, BookStore, Job

log = logging.getLogger(__name__)

# Made books are reloaded at startup and whenever this instance changes them. Another instance (the other side
# of a deploy) may publish too, so a manifest request older than this reloads them in the background. There is no
# timer: an idle service never touches the database (Neon's free plan scales to zero).
REFRESH_SECONDS = 600.0


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
        # Served as-is and updated in place, so the web app can hold on to the same objects.
        self.manifest: dict[str, Any] = static_manifest
        self.extra: dict[str, bytes | Asset] = {}
        self.runner: Runner | None = None
        if self.storage is not None and (client is not None or self.config.openai_key) and self.config.runner:
            if client is None:
                from .oai import OpenAIClient

                client = OpenAIClient(self.config.openai_key)
            self.runner = Runner(self.books, self.config, client=client, storage=self.storage, commons=commons,
                                 on_publish=self.refresh)
        self._tasks: list[asyncio.Task[Any]] = []
        self.clock = time.monotonic
        self._refreshed_at = -math.inf
        self._refreshing: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        """Books can be queued: there's storage and someone (this instance's runner) to make them."""
        return self.storage is not None and self.runner is not None

    # -- the manifest ------------------------------------------------------------------------------

    async def refresh(self) -> None:
        self._refreshed_at = self.clock()
        published = await asyncio.to_thread(self.books.published) if self.storage is not None else []
        merged, extra = manifest_merge.merge(self.static, self.static_files, published)
        if merged.get("version") != self.manifest.get("version"):
            log.info("content version %s (%d made books)", merged["version"], len(merged["order"]) - len(self.static["order"]))
        fresh = copy.deepcopy(merged) if merged is self.static else merged
        self.manifest.clear()
        self.manifest.update(fresh)
        self.extra.clear()
        self.extra.update(extra)

    def maybe_refresh(self) -> None:
        """On a manifest request: reload made books in the background if the last load is stale."""
        if self.storage is None or self.clock() - self._refreshed_at < REFRESH_SECONDS:
            return
        if self._refreshing is not None and not self._refreshing.done():
            return
        self._refreshing = asyncio.create_task(self._refresh_quietly())

    async def _refresh_quietly(self) -> None:
        try:
            await self.refresh()
        except Exception as exc:  # noqa: BLE001 - keep serving what we have
            log.warning("could not refresh made books: %s", exc)

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
        if job.status in ("failed", "rejected"):
            book = await asyncio.to_thread(self.books.book, job.book_id)
            if book and book.status != "published":
                await delete_keys(self.storage, await asyncio.to_thread(self.books.asset_keys, book_id=job.book_id))
                await asyncio.to_thread(self.books.drop_assets, job.book_id)
        return job

    async def set_published(self, book_id: str, published: bool) -> dict[str, Any]:
        book = await asyncio.to_thread(self.books.set_published, book_id, published)
        await self.refresh()
        return book.as_json()

    async def delete_book(self, book_id: str) -> int:
        keys = await asyncio.to_thread(self.books.delete_book, book_id)
        deleted = await delete_keys(self.storage, keys) if self.storage is not None else 0
        await self.refresh()
        return deleted

    async def forget_account(self, keys: list[str]) -> None:
        """After an account is erased: delete its books' files from R2 and drop them from the manifest."""
        if keys and self.storage is not None:
            await delete_keys(self.storage, keys)
        await self.refresh()

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
            "made_books_on_shelf": len(self.manifest.get("order", [])) - len(self.static.get("order", [])),
        }

    # -- lifecycle ---------------------------------------------------------------------------------

    async def start(self) -> None:
        try:
            await self.refresh()
        except Exception as exc:  # noqa: BLE001 - serve the hand-made books anyway
            log.warning("could not load made books: %s", exc)
        if self.runner is not None:
            self._tasks.append(asyncio.create_task(self.runner.run_forever()))
            log.info("book runner started (one book at a time, limits %s)", self.config.limits_json())

    async def stop(self) -> None:
        if self._refreshing is not None:
            self._tasks.append(self._refreshing)
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
