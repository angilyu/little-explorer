"""Each account's own content manifest: the made books on its shelf (see :mod:`..catalog` for the hand-made ones).

The public ``/content/manifest.json`` is the hand-made catalog only, the same for every install; the 1.0 app
downloads every file in it, so made books must never be listed there. A signed-in app also asks for
``/account/content/manifest.json``, which lists only the account's own made books and the ones the owner
granted it. Its book JSON and per-book narration (``Narration/<id>.narration.json``) are served from memory
with the account's session; photos and clips carry a short-lived signed R2 link (``url``), since the bucket
is private. File names stay unique across folders and against the catalog because the app caches them flat.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from typing import Any

from ..catalog import MANIFEST_FORMAT
from .store import Asset, BookRow

log = logging.getLogger(__name__)


def narration_path(book_id: str) -> str:
    return f"Narration/{book_id}.narration.json"


def _entry(data: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _version(files: dict[str, dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps({rel: e["sha256"] for rel, e in files.items()}, sort_keys=True).encode()).hexdigest()[:16]


def book_files(book: BookRow, assets: dict[str, Asset]) -> dict[str, bytes | Asset]:
    """A published book's files the app needs: its JSON, its clips' timings, and the photos and clips it uses
    (not leftovers, e.g. a remade page's new photo before the page is swapped in)."""
    data = book.json
    files: dict[str, bytes | Asset] = {f"Books/{book.id}.json": json.dumps(data, ensure_ascii=False, indent=2).encode()}
    for page in data.get("pages") or []:
        rel = f"Photos/{(page.get('photo') or {}).get('file')}"
        if rel in assets:
            files[rel] = assets[rel]
    clips = {}
    for key, clip in sorted((book.narration or {}).items()):
        rel = f"Narration/{clip.get('file')}.m4a"
        if rel in assets:
            clips[key] = clip
            files[rel] = assets[rel]
    files[narration_path(book.id)] = json.dumps({"clips": clips}, ensure_ascii=False, indent=2).encode()
    return files


def account_manifest(shelf: Iterable[tuple[BookRow, dict[str, Asset]]], static: dict[str, Any],
                     url_for: Callable[[Asset], str]) -> tuple[dict[str, Any], dict[str, bytes | Asset]]:
    """The manifest for one account and the files behind it (bytes to serve, or an :class:`Asset`)."""
    taken = {rel.rsplit("/", 1)[-1] for rel in static.get("files", {})}
    catalog = set(static.get("order", []))
    files: dict[str, dict[str, Any]] = {}
    served: dict[str, bytes | Asset] = {}
    order: list[str] = []
    for book, assets in shelf:
        made = book_files(book, assets)
        names = {rel.rsplit("/", 1)[-1] for rel in made}
        if book.id in catalog or book.id in order or not book.json.get("pages") or names & taken:
            log.warning("made book %s left out of the account manifest (clashes or is empty)", book.id)
            continue
        for rel, item in made.items():
            served[rel] = item
            if isinstance(item, Asset):
                files[rel] = {"sha256": item.sha256, "size": item.size, "url": url_for(item)}
            else:
                files[rel] = _entry(item)
        taken |= names
        order.append(book.id)
    return {"format": MANIFEST_FORMAT, "version": _version(files), "order": order, "files": files}, served
