"""The content manifest with made books added (see :mod:`..catalog` for the hand-made ones).

With nothing published, the static manifest is served exactly as before. Otherwise the made books
are appended to ``library.json``'s order and the manifest's ``order``; their book JSON, the merged
``library.json`` and ``narration.json`` are served from memory, and their photos and clips redirect
(302) to short-lived signed R2 links, since the bucket is private. File names stay unique across
folders because the app caches them flat.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ..catalog import LIBRARY_FILE
from .store import Asset, BookRow

log = logging.getLogger(__name__)

NARRATION_FILE = "Narration/narration.json"


def _entry(data: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _version(files: dict[str, dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps({rel: e["sha256"] for rel, e in files.items()}, sort_keys=True).encode()).hexdigest()[:16]


def merge(static: dict[str, Any], static_files: dict[str, Path],
          published: list[tuple[BookRow, dict[str, Asset]]]) -> tuple[dict[str, Any], dict[str, bytes | Asset]]:
    """The manifest to serve, and the made files: bytes to serve, or an :class:`Asset` to redirect to."""
    if not published:
        return static, {}
    files = dict(static["files"])
    taken = {rel.rsplit("/", 1)[-1] for rel in files}
    order = list(static["order"])
    extra: dict[str, bytes | Asset] = {}
    clips: dict[str, Any] = {}
    for book, assets in published:
        data = book.json
        pages = data.get("pages") or []
        names = {f"Books/{book.id}.json"} | set(assets)
        if book.id in order or not pages or any(rel.rsplit("/", 1)[-1] in taken for rel in names):
            log.warning("made book %s left out of the manifest (clashes or is empty)", book.id)
            continue
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        extra[f"Books/{book.id}.json"] = body
        files[f"Books/{book.id}.json"] = _entry(body)
        for rel, asset in sorted(assets.items()):
            extra[rel] = asset
            files[rel] = {"sha256": asset.sha256, "size": asset.size}
        for key, clip in (book.narration or {}).items():
            if f"Narration/{clip.get('file')}.m4a" in assets:
                clips[key] = clip
        taken |= {rel.rsplit("/", 1)[-1] for rel in names}
        order.append(book.id)
    if len(order) == len(static["order"]):
        return static, {}

    library = json.loads(static_files[LIBRARY_FILE].read_bytes()) if LIBRARY_FILE in static_files else {}
    library["order"] = order
    body = json.dumps(library, ensure_ascii=False, indent=2).encode()
    extra[LIBRARY_FILE] = body
    files[LIBRARY_FILE] = _entry(body)
    if clips:
        narration = json.loads(static_files[NARRATION_FILE].read_bytes()) if NARRATION_FILE in static_files else {}
        narration.setdefault("clips", {}).update(clips)
        body = json.dumps(narration, ensure_ascii=False, indent=2).encode()
        extra[NARRATION_FILE] = body
        files[NARRATION_FILE] = _entry(body)
    manifest = {**static, "version": _version(files), "order": order, "files": files}
    return manifest, extra
