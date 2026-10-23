"""The book content the app can download without an app update.

Everything under ``LittleExplorer/Resources`` (books, photos, narration clips, ``library.json``,
the shelf order, and ``badges.json``, the Passport badges; see :mod:`.badges`) is listed in a manifest of SHA-256 hashes. The app compares the manifest with what
it has and downloads only changed files; the agent fetches the book JSON from the same place.
File names must be unique across folders, because the app bundle (and the app's cache) is flat.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import badges

RESOURCES_DIR = Path(__file__).resolve().parents[3] / "LittleExplorer" / "Resources"
LIBRARY_FILE = "library.json"
FOLDERS = ("Books", "Photos", "Narration")
SUFFIXES = frozenset({".json", ".jpg", ".jpeg", ".png", ".m4a"})
MANIFEST_FORMAT = 1


class CatalogError(ValueError):
    pass


def resources_dir() -> Path:
    raw = os.environ.get("CONTENT_ROOT", "").strip()
    return Path(raw).expanduser().resolve() if raw else RESOURCES_DIR


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def content_files(root: Path) -> dict[str, Path]:
    """Relative path -> file, for every file the app may download."""
    files: dict[str, Path] = {}
    library = root / LIBRARY_FILE
    if library.is_file():
        files[LIBRARY_FILE] = library
    badge_file = root / badges.FILE
    if badge_file.is_file():
        files[badges.FILE] = badge_file
    for folder in FOLDERS:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.is_file() and path.suffix.lower() in SUFFIXES and not path.name.startswith("."):
                files[f"{folder}/{path.name}"] = path
    return files


def build_manifest(root: Path | None = None) -> dict[str, Any]:
    """The manifest the app downloads. Fails loudly on content the app could not use."""
    root = root or resources_dir()
    files = content_files(root)
    names = [rel.rsplit("/", 1)[-1] for rel in files]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise CatalogError(f"file names must be unique across folders: {duplicates}")
    if LIBRARY_FILE not in files:
        raise CatalogError(f"{root / LIBRARY_FILE} is missing")
    order = json.loads(files[LIBRARY_FILE].read_text(encoding="utf-8")).get("order", [])
    if not order or not all(isinstance(b, str) for b in order):
        raise CatalogError(f"{LIBRARY_FILE} needs a non-empty 'order' list of book ids")
    missing = [b for b in order if f"Books/{b}.json" not in files]
    if missing:
        raise CatalogError(f"books in {LIBRARY_FILE} without Books/<id>.json: {missing}")
    if badges.FILE in files:
        try:
            badges.validate(json.loads(files[badges.FILE].read_text(encoding="utf-8")))
        except (ValueError, badges.BadgeError) as exc:
            raise CatalogError(f"{badges.FILE}: {exc}") from exc
    entries = {rel: {"sha256": _sha256(path), "size": path.stat().st_size} for rel, path in files.items()}
    version = hashlib.sha256(
        json.dumps({rel: e["sha256"] for rel, e in entries.items()}, sort_keys=True).encode()
    ).hexdigest()[:16]
    return {"format": MANIFEST_FORMAT, "version": version, "order": order, "files": entries}
