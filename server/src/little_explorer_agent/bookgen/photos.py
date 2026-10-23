"""Real, freely licensed photos from Wikimedia Commons for made books (a port of
``scripts/fetch_photos.py``; no AI images).

Each page has a couple of search queries. Candidates must have a free license that allows commercial
use and changes (CC0, public domain, CC BY, CC BY-SA), be big enough and roughly landscape, not be
in a category a child shouldn't see, and pass image moderation. The chosen photo is resized to
1400 px wide and saved as a JPEG; the author and license go into the page for the credits screen.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import re
from dataclasses import dataclass
from typing import Any

API = "https://commons.wikimedia.org/w/api.php"
UA = "LittleExplorer/1.0 (https://github.com/angilyu/little-explorer; kids picture-book app)"
WIDTH = 1400
MIN_WIDTH = 1000
MODERATION_WIDTH = 512
JPEG_QUALITY = 72
MAX_DOWNLOAD = 15 * 1024 * 1024
MIMES = ("image/jpeg", "image/png", "image/svg+xml", "image/webp", "image/tiff")

ALLOWED = re.compile(r"^(public domain|pd\b|cc0|cc[- ]by( |-)?(sa)?[- ]?\d|cc[- ]by(-sa)?$|cc by-sa|cc by \d)", re.I)
REJECT = re.compile(r"\b(nc|nd)\b|non-?commercial|no ?deriv|fair use|copyrighted", re.I)
UNSUITABLE = re.compile(
    r"nud(e|ity)|naked|erotic|\bsex|porn|lingerie|corpse|dead bod|blood|\bgore\b|violence|weapon|\bguns?\b|rifle|"
    r"execution|war crime|wounded|injur|slaughter|hunting|drug|cigar|alcohol|beer|wine", re.I
)


class PhotoError(RuntimeError):
    """Commons couldn't be reached (retry later)."""


@dataclass
class Photo:
    data: bytes
    title: str
    author: str
    license: str
    license_url: str | None
    source_url: str | None

    def credits(self, file: str, query: str) -> dict[str, Any]:
        return {
            "file": file,
            "commonsTitle": self.title,
            "query": query,
            "verified": True,
            "author": self.author,
            "license": self.license,
            "licenseUrl": self.license_url,
            "sourceUrl": self.source_url,
        }


def clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _meta(info: dict[str, Any], name: str) -> str:
    return clean((info.get("extmetadata") or {}).get(name, {}).get("value"))


def license_of(info: dict[str, Any]) -> tuple[bool, str, str, str]:
    """Returns (ok, short name, license url, reason)."""
    short, code, url = _meta(info, "LicenseShortName"), _meta(info, "License"), _meta(info, "LicenseUrl")
    restrictions, nonfree = _meta(info, "Restrictions").lower(), _meta(info, "NonFree")
    label = short or code
    if nonfree.lower() == "true":
        return False, label, url, "non-free"
    if not label:
        return False, label, url, "no license metadata"
    if REJECT.search(label) or REJECT.search(code):
        return False, label, url, "NC/ND or not free"
    if "trademark" in restrictions or "personality" in restrictions:
        return False, label, url, f"restricted: {restrictions}"
    if not (ALLOWED.search(label) or ALLOWED.search(code) or code.lower().startswith(("cc-by", "cc0", "pd"))):
        return False, label, url, f"license not on allow-list: {label!r} / {code!r}"
    if label.lower().startswith("public domain") or code.lower().startswith("pd"):
        label = "Public domain"
    return True, label, url, ""


def acceptable(page: dict[str, Any], used: set[str]) -> tuple[bool, str]:
    if page.get("title") in used:
        return False, "already used on another page"
    infos = page.get("imageinfo") or []
    if not infos:
        return False, "missing imageinfo"
    info = infos[0]
    ok, _, _, reason = license_of(info)
    if not ok:
        return False, reason
    if info.get("mime") not in MIMES:
        return False, f"mime {info.get('mime')}"
    is_svg = info.get("mime") == "image/svg+xml"
    width, height = info.get("width", 0), info.get("height", 1)
    if not is_svg and width < MIN_WIDTH:
        return False, f"too small ({width}px)"
    if not is_svg and not 0.6 <= width / max(height, 1) <= 2.2:
        return False, "awkward aspect ratio"
    if UNSUITABLE.search(f"{page.get('title', '')} {_meta(info, 'Categories')} {_meta(info, 'ImageDescription')}"):
        return False, "unsuitable subject"
    return True, ""


def author_of(info: dict[str, Any]) -> str:
    # Commons' explicit attribution (e.g. a museum) beats the Artist field, which can name the object's maker.
    author = _meta(info, "Attribution") or _meta(info, "Artist") or _meta(info, "Credit")
    if author.lower().startswith("unknown"):
        author = "Unknown author"
    return author[:80] or "Unknown author"


def to_jpeg(data: bytes, width: int = WIDTH, quality: int = JPEG_QUALITY) -> bytes:
    """Resize to at most ``width`` wide and save as an RGB JPEG (transparent parts become white)."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.draft("RGB", (width, width * 3))
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            image = background
        elif image.mode != "RGB":
            image = image.convert("RGB")
        if image.width > width:
            image = image.resize((width, max(1, round(image.height * width / image.width))), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        return out.getvalue()


class Commons:
    def __init__(self, *, timeout: float = 60.0, pause: float = 0.3) -> None:
        self._timeout = timeout
        self._pause = pause
        self._session: Any = None

    async def _http(self) -> Any:
        if self._session is None or self._session.closed:
            from aiohttp import ClientSession, ClientTimeout

            self._session = ClientSession(timeout=ClientTimeout(total=self._timeout), headers={"User-Agent": UA})
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _get(self, url: str, params: dict[str, str] | None = None, *, limit: int = MAX_DOWNLOAD) -> bytes:
        session = await self._http()
        for attempt in range(4):
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        body = await resp.content.read(limit + 1)
                        if len(body) > limit:
                            raise PhotoError("photo is too big")
                        return body
                    if resp.status not in (429, 500, 502, 503, 504):
                        raise PhotoError(f"Commons answered {resp.status}")
            except PhotoError:
                raise
            except Exception as exc:  # noqa: BLE001 - network hiccup, try again
                if attempt == 3:
                    raise PhotoError(f"Commons: {type(exc).__name__}") from None
            await asyncio.sleep(2 * (attempt + 1))
        raise PhotoError("Commons kept failing")

    async def search(self, query: str, quality: bool) -> list[dict[str, Any]]:
        q = f"{query} filetype:bitmap" + (" incategory:Quality_images" if quality else "")
        body = await self._get(API, {
            "action": "query", "format": "json", "formatversion": "2", "generator": "search", "gsrsearch": q,
            "gsrnamespace": "6", "gsrlimit": "20", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": str(WIDTH),
        })
        pages = json.loads(body).get("query", {}).get("pages", [])
        await asyncio.sleep(self._pause)
        return sorted(pages, key=lambda p: p.get("index", 999))

    async def candidates(self, queries: list[str], used: set[str]) -> list[dict[str, Any]]:
        """Acceptable pages for the queries, best first (quality images first, like the script)."""
        found, seen = [], set(used)
        for quality in (True, False):
            for query in queries:
                for page in await self.search(query, quality):
                    if acceptable(page, seen)[0]:
                        found.append(page)
                        seen.add(page["title"])
                if len(found) >= 3:
                    return found
        return found

    async def download(self, page: dict[str, Any]) -> Photo:
        info = page["imageinfo"][0]
        raw = await self._get(info.get("thumburl") or info["url"])
        data = await asyncio.to_thread(to_jpeg, raw)
        del raw
        _, label, url, _ = license_of(info)
        return Photo(data, page["title"], author_of(info), label, url or None, info.get("descriptionurl"))
