"""Real, freely licensed photos for made books (no AI images): from Wikimedia Commons (a port of
``scripts/fetch_photos.py``) and, for apps that can credit it (1.2.5 and later), from Openverse
(https://openverse.org), the open search over Flickr and other Creative Commons sites.

Each page has a couple of search queries. Both sources are searched with them, and every candidate must have a
license that allows changes (CC0, public domain, CC BY, CC BY-SA, and the non-commercial CC BY-NC and
CC BY-NC-SA: the app is free with no ads), be big enough and roughly landscape, not be about something a child
shouldn't see, and pass image moderation. No-derivatives (ND) licenses are never used, because every photo is
cropped and resized; neither are fair-use, copyrighted, trademark/personality-restricted, sampling or unknown
licenses. The candidates of both sources are scored together (how much of the search they match, their shape,
their place in the search results) and the best one wins; the same photo is never used twice in a book, even
when Commons and Openverse both have it (Flickr photos are often copied to Commons). The chosen photo is resized
to 1400 px wide and saved as a JPEG; its title, author, license (and link), source page and where it came from
go into the page for the app's photo credits.

Openverse is a bonus: when it's down, slow or out of requests, books use Commons alone. It is sent only the
search words (the writer's photo queries), never the grown-up's own words or anything about the account.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
UA = "LittleExplorer/1.0 (https://github.com/angilyu/little-explorer; kids picture-book app)"
WIDTH = 1400
MIN_WIDTH = 1000
MODERATION_WIDTH = 512
JPEG_QUALITY = 72
MAX_DOWNLOAD = 15 * 1024 * 1024
MIMES = ("image/jpeg", "image/png", "image/svg+xml", "image/webp", "image/tiff")

# Made books are free with no ads, so non-commercial (NC) licenses are fine. ND is not: photos are cropped.
ALLOWED = re.compile(
    r"^(public domain|pd\b|cc0|cc[- ]by([- ]nc)?([- ]sa)?([- ]?\d|$)|cc[- ]by[- ]nc[- ]sa|cc by-sa|cc by \d)", re.I
)
REJECT = re.compile(r"\bnd\b|no ?deriv|fair use|copyrighted", re.I)
UNSUITABLE = re.compile(
    r"nud(e|ity)|naked|erotic|\bsex|porn|lingerie|corpse|dead bod|blood|\bgore\b|violence|weapon|\bguns?\b|rifle|"
    r"execution|war crime|wounded|injur|slaughter|hunting|drug|cigar|alcohol|beer|wine", re.I
)
# Openverse finds a lot of personal Flickr snapshots: keep to places, animals and things, like the writer asks.
PERSONAL = re.compile(
    r"selfie|portrait|\bme\b|\bmy (son|daughter|kids?|wife|husband|family|baby|mom|dad|mum|friends?)\b|family|"
    r"wedding|bride|groom|birthday|\bparty\b|\bkids?\b|\bchild|\bboys?\b|\bgirls?\b|\bbaby\b|toddler|people|crowd", re.I
)
GENERIC = {"the", "and", "with", "for", "photo", "picture", "image", "close", "view", "big", "small", "little"}


COMMONS_NAME = "Wikimedia Commons"
OPENVERSE_API = "https://api.openverse.org/v1"
# Openverse license codes we take, and their names in the credits. Everything else (by-nd, by-nc-nd, sampling+,
# nc-sampling+ and anything new) is turned down.
OPENVERSE_LICENSES = {"cc0": "CC0", "pdm": "Public domain", "by": "CC BY", "by-sa": "CC BY-SA", "by-nc": "CC BY-NC",
                      "by-nc-sa": "CC BY-NC-SA"}
# Commons is searched directly; the others are 3D models, silhouettes and icons, not photos.
OPENVERSE_SKIP = ("wikimedia", "svgsilh", "sketchfab", "thingiverse", "phylopic")
OPENVERSE_EXTENSIONS = ("jpg", "jpeg", "png", "webp")
PROVIDERS = {"flickr": "Flickr", "inaturalist": "iNaturalist", "europeana": "Europeana", "rawpixel": "Rawpixel",
             "geographorguk": "Geograph", "met": "The Met", "nasa": "NASA", "stocksnap": "StockSnap",
             "wordpress": "WordPress Photo Directory", "nappy": "Nappy", "animaldiversity": "Animal Diversity Web"}
FLICKR_URL = re.compile(r"flickr\.com/photos/[^/\s\"'<>]+/(\d{5,})", re.I)
# Flickr photos copied to Commons are usually named "Waikiki Beach (51872593291).jpg".
FLICKR_TITLE = re.compile(r"\((\d{8,12})\)\.\w{3,4}$")


class PhotoError(RuntimeError):
    """Commons couldn't be reached (retry later)."""


class BadPhoto(ValueError):
    """This one file couldn't be opened as an image (try the next candidate)."""


@dataclass
class Photo:
    data: bytes
    title: str
    author: str
    license: str
    license_url: str | None
    source_url: str | None
    source_name: str = COMMONS_NAME
    author_url: str | None = None
    provider: str | None = None
    openverse_id: str | None = None
    flickr_id: str | None = None

    def credits(self, file: str, query: str) -> dict[str, Any]:
        """What the page keeps: everything the license asks to credit, plus where the photo came from."""
        credits: dict[str, Any] = {"file": file}
        # The app links a "commonsTitle" to Commons, so only Commons files have one.
        credits["title" if self.openverse_id else "commonsTitle"] = self.title
        credits.update({
            "query": query,
            "verified": True,
            "author": self.author,
            "license": self.license,
            "licenseUrl": self.license_url,
            "sourceUrl": self.source_url,
            "sourceName": self.source_name,
        })
        optional = {"authorUrl": self.author_url, "provider": self.provider, "openverseId": self.openverse_id,
                    "flickrId": self.flickr_id}
        credits.update({k: v for k, v in optional.items() if v})
        return credits


@dataclass
class Candidate:
    """A photo that passed the checks, waiting to be downloaded (best ``score`` first)."""

    source: str  # "commons" or "openverse"
    title: str
    url: str
    keys: frozenset[str]
    score: float
    query: str
    item: dict[str, Any] = field(repr=False)


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
        return False, label, url, "no derivatives (ND), fair use or not free"
    if "trademark" in restrictions or "personality" in restrictions:
        return False, label, url, f"restricted: {restrictions}"
    if not (ALLOWED.search(label) or ALLOWED.search(code) or code.lower().startswith(("cc-by", "cc0", "pd"))):
        return False, label, url, f"license not on allow-list: {label!r} / {code!r}"
    if label.lower().startswith("public domain") or code.lower().startswith("pd"):
        label = "Public domain"
    return True, label, url, ""


def _words(query: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z]+", query.lower()) if len(w) >= 3 and w not in GENERIC]
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words]


def match_share(text: str, query: str) -> float:
    """How much of the search (0 to 1) is in a photo's own words."""
    words = _words(query)
    if not words:
        return 1.0
    text = text.lower()
    return sum(1 for w in words if w in text) / len(words)


def _commons_text(page: dict[str, Any]) -> str:
    info = (page.get("imageinfo") or [{}])[0]
    return " ".join([page.get("title", ""), _meta(info, "Categories"), _meta(info, "ImageDescription"),
                     _meta(info, "ObjectName")])


def relevant(page: dict[str, Any], query: str) -> bool:
    """Commons' search also matches text we can't see, so a photo of an elephant can come back for
    "stegosaurus fossil". Keep a candidate only if a word of the query is in its title, categories or description."""
    return match_share(_commons_text(page), query) > 0


def acceptable(page: dict[str, Any], used: set[str], query: str = "") -> tuple[bool, str]:
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
    if query and not relevant(page, query):
        return False, "nothing in it matches the search"
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




# -- the same photo, wherever it comes from ----------------------------------------------------------


def _url_key(url: str | None) -> str:
    url = (url or "").strip().lower().split("#")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", url)


def flickr_id(*texts: str | None) -> str:
    """The Flickr photo id in a Flickr link or a Commons file name copied from Flickr ("" if none)."""
    for text in texts:
        if not text:
            continue
        found = FLICKR_URL.search(text) or FLICKR_TITLE.search(text)
        if found:
            return found.group(1)
    return ""


def _keys(*, title: str = "", urls: Iterable[str | None] = (), flickr: str = "", openverse: str = "") -> frozenset[str]:
    keys = {f"url:{_url_key(u)}" for u in urls if u}
    if title:
        keys.add(title)
    if flickr:
        keys.add(f"flickr:{flickr}")
    if openverse:
        keys.add(f"openverse:{openverse}")
    return frozenset(keys)


def credit_keys(photo: dict[str, Any]) -> frozenset[str]:
    """What identifies a page's photo (its stored credits), to keep it off the book's other pages."""
    return _keys(title=str(photo.get("commonsTitle") or ""), urls=[photo.get("sourceUrl")],
                 flickr=str(photo.get("flickrId") or "") or flickr_id(photo.get("sourceUrl"), photo.get("commonsTitle")),
                 openverse=str(photo.get("openverseId") or ""))


def source_of(photo: dict[str, Any]) -> str:
    return "openverse" if photo.get("openverseId") else "commons"


def used_keys(pages: Iterable[dict[str, Any]]) -> set[str]:
    used: set[str] = set()
    for page in pages:
        photo = page.get("photo") or {}
        if photo.get("file"):
            used |= credit_keys(photo)
    return used


def _raw_meta(info: dict[str, Any], name: str) -> str:
    return str((info.get("extmetadata") or {}).get(name, {}).get("value") or "")


def commons_keys(page: dict[str, Any]) -> frozenset[str]:
    info = (page.get("imageinfo") or [{}])[0]
    fid = flickr_id(page.get("title"), _raw_meta(info, "Credit"), _raw_meta(info, "ImageDescription"),
                    _raw_meta(info, "Artist"))
    return _keys(title=page.get("title", ""), urls=[info.get("descriptionurl")], flickr=fid)


# -- Openverse's results ---------------------------------------------------------------------------


def _provider(item: dict[str, Any]) -> str:
    return str(item.get("provider") or item.get("source") or "").strip().lower()


def provider_name(provider: str) -> str:
    if provider.startswith("smithsonian"):
        return "Smithsonian"
    return PROVIDERS.get(provider) or provider.replace("_", " ").title() or "Openverse"


def openverse_license(item: dict[str, Any]) -> tuple[bool, str, str, str]:
    """Returns (ok, the license's name for the credits, its link, reason)."""
    code = str(item.get("license") or "").strip().lower()
    version = str(item.get("license_version") or "").strip()
    url = str(item.get("license_url") or "").strip()
    if code not in OPENVERSE_LICENSES or REJECT.search(code):
        return False, code, url, f"license not on allow-list: {code!r}"
    if REJECT.search(url) or "sampling" in url.lower():
        return False, code, url, f"license link isn't the license: {url!r}"
    if not url.startswith("http"):
        return False, code, url, "no license link"
    name = OPENVERSE_LICENSES[code]
    label = name if code == "pdm" or not version or version == "N/A" else f"{name} {version}"
    return True, label, url, ""


def openverse_keys(item: dict[str, Any]) -> frozenset[str]:
    landing = str(item.get("foreign_landing_url") or "")
    fid = flickr_id(landing, str(item.get("url") or "")) if _provider(item) == "flickr" or "flickr" in landing else ""
    return _keys(urls=[landing, item.get("url")], flickr=fid, openverse=str(item.get("id") or ""))


def _openverse_text(item: dict[str, Any]) -> str:
    tags = " ".join(str(t.get("name") or "") for t in item.get("tags") or [] if isinstance(t, dict))
    return f"{clean(item.get('title'))} {tags}"


def openverse_acceptable(item: dict[str, Any], used: set[str], query: str = "") -> tuple[bool, str]:
    if openverse_keys(item) & used:
        return False, "already used on another page"
    if _provider(item) in OPENVERSE_SKIP or str(item.get("source") or "").lower() in OPENVERSE_SKIP:
        return False, f"from {_provider(item)} (Commons is searched directly)"
    ok, _, _, reason = openverse_license(item)
    if not ok:
        return False, reason
    if item.get("mature") or item.get("unstable__sensitivity"):
        return False, "marked sensitive"
    if not str(item.get("url") or "").startswith("http") or not str(item.get("foreign_landing_url") or "").startswith("http"):
        return False, "no file or source link"
    filetype = str(item.get("filetype") or "").lower()
    if filetype and filetype not in OPENVERSE_EXTENSIONS:
        return False, f"file type {filetype}"
    if (item.get("filesize") or 0) > MAX_DOWNLOAD:
        return False, "too big to download"
    width, height = item.get("width") or 0, item.get("height") or 0
    if width < MIN_WIDTH or not height:
        return False, f"too small ({width}px)"
    if not 0.6 <= width / height <= 2.2:
        return False, "awkward aspect ratio"
    text = _openverse_text(item)
    if UNSUITABLE.search(text):
        return False, "unsuitable subject"
    if PERSONAL.search(text):
        return False, "a personal photo of people"
    if query and match_share(text, query) == 0:
        return False, "nothing in it matches the search"
    return True, ""


def creator_of(item: dict[str, Any]) -> str:
    author = clean(item.get("creator"))
    if not author or author.lower().startswith("unknown"):
        author = "Unknown author"
    return author[:80]


# -- scoring -----------------------------------------------------------------------------------------


def score(share: float, width: int, height: int, rank: int, query_index: int, quality: bool = False) -> float:
    """Higher is better: how much of the search the photo matches comes first, then a comfortable landscape shape,
    its place in its own search results, and (a little) Commons' "quality image" mark. Alternate queries count a
    bit less. See :meth:`Photos.candidates` for the nudge toward the source the book has used less."""
    ratio = width / max(height, 1)
    value = share + (0.15 if 1.2 <= ratio <= 1.9 else 0.0) + 0.25 * (1 - min(rank, 20) / 20) + (0.05 if quality else 0.0)
    return round(value * (1.0 if query_index == 0 else 0.85), 4)


# -- downloads ---------------------------------------------------------------------------------------


async def fetch(session: Any, url: str, params: dict[str, str] | None = None, *, limit: int = MAX_DOWNLOAD,
                name: str = "Commons", attempts: int = 4, headers: dict[str, str] | None = None) -> bytes:
    for attempt in range(attempts):
        try:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    # content.read(n) returns only what has arrived so far, so read to the end.
                    body = bytearray()
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        body += chunk
                        if len(body) > limit:
                            raise PhotoError("photo is too big")
                    return bytes(body)
                if resp.status not in (429, 500, 502, 503, 504):
                    raise PhotoError(f"{name} answered {resp.status}")
        except PhotoError:
            raise
        except Exception as exc:  # noqa: BLE001 - network hiccup, try again
            if attempt == attempts - 1:
                raise PhotoError(f"{name}: {type(exc).__name__}") from None
        if attempt < attempts - 1:
            await asyncio.sleep(2 * (attempt + 1))
    raise PhotoError(f"{name} kept failing")


async def _jpeg(raw: bytes, title: str) -> bytes:
    try:
        return await asyncio.to_thread(to_jpeg, raw)
    except Exception as exc:  # noqa: BLE001 - Pillow raises many kinds; this file just can't be used
        raise BadPhoto(f"{title}: {type(exc).__name__}: {exc}") from None


class _Http:
    def __init__(self, timeout: float) -> None:
        self._timeout = timeout
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


class Commons(_Http):
    def __init__(self, *, timeout: float = 60.0, pause: float = 0.3, api: str = API) -> None:
        super().__init__(timeout)
        self._pause = pause
        self._api = api

    async def _get(self, url: str, params: dict[str, str] | None = None, *, limit: int = MAX_DOWNLOAD) -> bytes:
        return await fetch(await self._http(), url, params, limit=limit)

    async def search(self, query: str, quality: bool) -> list[dict[str, Any]]:
        q = f"{query} filetype:bitmap" + (" incategory:Quality_images" if quality else "")
        body = await self._get(self._api, {
            "action": "query", "format": "json", "formatversion": "2", "generator": "search", "gsrsearch": q,
            "gsrnamespace": "6", "gsrlimit": "20", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": str(WIDTH),
        })
        pages = json.loads(body).get("query", {}).get("pages", [])
        await asyncio.sleep(self._pause)
        return sorted(pages, key=lambda p: p.get("index", 999))

    async def candidates(self, query: str, query_index: int, used: set[str]) -> list[Candidate]:
        """Acceptable files for one query: quality images first, like the script."""
        found = []
        for quality in (True, False):
            for rank, page in enumerate(await self.search(query, quality)):
                if not acceptable(page, used, query)[0] or commons_keys(page) & used:
                    continue
                info = page["imageinfo"][0]
                found.append(Candidate(
                    "commons", page["title"], info.get("thumburl") or info["url"], commons_keys(page),
                    score(match_share(_commons_text(page), query), info.get("width", 0), info.get("height", 1), rank,
                          query_index, quality),
                    query, page,
                ))
        return found

    async def download(self, page: dict[str, Any]) -> Photo:
        info = page["imageinfo"][0]
        data = await _jpeg(await self._get(info.get("thumburl") or info["url"]), page.get("title", ""))
        _, label, url, _ = license_of(info)
        return Photo(data, page["title"], author_of(info), label, url or None, info.get("descriptionurl"),
                     flickr_id=next(iter(k[7:] for k in commons_keys(page) if k.startswith("flickr:")), None))


class Openverse(_Http):
    """Openverse's image search (https://api.openverse.org/v1/). Anonymous use allows 20 searches a minute and 200
    a day; a registered app (``OPENVERSE_CLIENT_ID``/``OPENVERSE_CLIENT_SECRET``) gets more. It never raises: when
    it fails or runs out of requests it rests for a while and finds nothing, so books use Commons alone."""

    def __init__(self, *, client_id: str = "", client_secret: str = "", api: str = OPENVERSE_API,
                 timeout: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        super().__init__(timeout)
        self._api = api.rstrip("/")
        self._client = (client_id.strip(), client_secret.strip())
        self._clock = clock
        self._sleep = sleep
        self._token = ""
        self._token_until = 0.0
        self._auth_retry_at = 0.0
        self._last = -1e9
        self.resting_until = 0.0
        self.last_error: str | None = None
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._clock() >= self.resting_until

    def _rest(self, seconds: float, why: str) -> None:
        self.resting_until = max(self.resting_until, self._clock() + seconds)
        self.last_error = why
        log.warning("Openverse: %s; using Commons alone for %ds", why, int(seconds))

    async def _auth(self) -> dict[str, str]:
        client_id, secret = self._client
        if not (client_id and secret) or self._clock() < self._auth_retry_at:
            return {}
        if self._token and self._clock() < self._token_until:
            return {"Authorization": f"Bearer {self._token}"}
        try:
            session = await self._http()
            async with session.post(f"{self._api}/auth_tokens/token/", data={
                "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
            }) as resp:
                if resp.status != 200:
                    raise PhotoError(f"token answered {resp.status}")
                body = await resp.json(content_type=None)
            self._token = str(body["access_token"])
            self._token_until = self._clock() + max(60.0, float(body.get("expires_in") or 3600) - 60)
            return {"Authorization": f"Bearer {self._token}"}
        except Exception as exc:  # noqa: BLE001 - search anonymously instead
            log.warning("Openverse sign-in failed (%s); searching anonymously for 10 minutes", exc)
            self._token, self._auth_retry_at = "", self._clock() + 600
            return {}

    def _note_limits(self, headers: Any) -> None:
        """Rest before the server says no: e.g. ``x-ratelimit-available-anon_burst: 0``."""
        for name, value in headers.items():
            name = name.lower()
            if name.startswith("x-ratelimit-available-") and str(value).strip() == "0":
                self._rest(60 if "burst" in name else 3600, f"no requests left ({name[22:]})")

    async def _pace(self) -> None:
        gap = (0.6 if self._token else 3.1) - (self._clock() - self._last)
        if gap > 0:
            await self._sleep(gap)
        self._last = self._clock()

    async def search(self, query: str) -> list[dict[str, Any]]:
        if not self.available:
            return []
        params = {
            "q": query[:200], "license": ",".join(OPENVERSE_LICENSES), "mature": "false",
            "excluded_source": ",".join(OPENVERSE_SKIP), "size": "medium,large", "page_size": "20",
        }
        error = "kept failing"
        async with self._lock:
            for attempt in range(2):
                if not self.available:
                    return []
                await self._pace()
                headers = await self._auth()
                try:
                    session = await self._http()
                    async with session.get(f"{self._api}/images/", params=params, headers=headers) as resp:
                        self._note_limits(resp.headers)
                        if resp.status == 200:
                            body = await resp.json(content_type=None)
                            results = [r for r in body.get("results") or [] if isinstance(r, dict)]
                            if results or attempt:
                                return results
                            # Openverse's license filter sometimes finds nothing for a common search ("Waikiki
                            # beach"); ask again without it. Every result's license is still checked here.
                            params.pop("license", None)
                            continue
                        if resp.status == 401 and headers:
                            self._token, self._auth_retry_at = "", self._clock() + 600
                            continue
                        if resp.status == 429:
                            retry = resp.headers.get("Retry-After", "")
                            self._rest(float(retry) if retry.isdigit() else 600, "too many requests (429)")
                            return []
                        if resp.status < 500:
                            log.warning("Openverse answered %s for %r", resp.status, query)
                            return []
                        error = f"answered {resp.status}"
                except Exception as exc:  # noqa: BLE001 - down or slow: Commons alone will do
                    error = f"{type(exc).__name__}: {exc}"[:200]
                if attempt == 0:
                    await self._sleep(2)
            self._rest(300, error)
            return []

    async def candidates(self, query: str, query_index: int, used: set[str]) -> list[Candidate]:
        found = []
        for rank, item in enumerate(await self.search(query)):
            ok, reason = openverse_acceptable(item, used, query)
            if not ok:
                log.debug("Openverse %s: %s", item.get("id"), reason)
                continue
            found.append(Candidate(
                "openverse", clean(item.get("title")) or "Untitled", str(item["url"]), openverse_keys(item),
                score(match_share(_openverse_text(item), query), item["width"], item["height"], rank, query_index),
                query, item,
            ))
        return found

    async def download(self, item: dict[str, Any]) -> Photo:
        """The photo from where it lives (Flickr and the like). Any failure there (some sites refuse downloads
        from other apps, links go dead) only rules out this photo."""
        title = clean(item.get("title")) or "Untitled"
        try:
            raw = await fetch(await self._http(), str(item["url"]), name=provider_name(_provider(item)), attempts=2)
        except PhotoError as exc:
            raise BadPhoto(f"{title}: {exc}") from None
        data = await _jpeg(raw, title)
        del raw
        _, label, license_url, _ = openverse_license(item)
        provider = _provider(item)
        fid = next(iter(k[7:] for k in openverse_keys(item) if k.startswith("flickr:")), None)
        return Photo(data, title, creator_of(item), label, license_url, str(item["foreign_landing_url"]),
                     source_name=f"{provider_name(provider)} (via Openverse)",
                     author_url=str(item.get("creator_url") or "") or None, provider=provider or None,
                     openverse_id=str(item.get("id") or "") or None, flickr_id=fid)


class Photos:
    """Both sources together: the candidates for a page, best first, and their download."""

    def __init__(self, commons: Commons | None = None, openverse: Openverse | None = None) -> None:
        self.commons = commons or Commons()
        self.openverse = openverse

    async def close(self) -> None:
        await self.commons.close()
        if self.openverse is not None:
            await self.openverse.close()

    async def candidates(self, queries: list[str], used: set[str], *, openverse: bool = False,
                         book_sources: dict[str, int] | None = None, enough: int = 6) -> list[Candidate]:
        """Search Commons (and Openverse when ``openverse`` and it's set up) with each query in turn until there
        are ``enough`` candidates, then return them best first. A photo both sources have is kept once.

        ``book_sources`` counts the book's photos so far by source; the source with fewer gets a small nudge, so
        when both have a good match the book alternates between them (more varied, everyday photos)."""
        found: list[Candidate] = []
        seen = set(used)
        sources: list[Any] = [self.commons]
        if openverse and self.openverse is not None:
            sources.append(self.openverse)
        for index, query in enumerate(queries):
            results = await asyncio.gather(*(s.candidates(query, index, seen) for s in sources), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result  # Commons couldn't be reached: try the book again later (Openverse never raises)
            for candidate in sorted((c for r in results for c in r), key=lambda c: -c.score):
                if candidate.keys & seen:
                    continue
                found.append(candidate)
                seen |= candidate.keys
            if len(found) >= enough:
                break
        if len(sources) > 1 and book_sources:
            commons, other = book_sources.get("commons", 0), book_sources.get("openverse", 0)
            behind = "openverse" if other < commons else "commons" if commons < other else None
            for candidate in found:
                if candidate.source == behind:
                    candidate.score = round(candidate.score + 0.15, 4)
        return sorted(found, key=lambda c: -c.score)

    async def download(self, candidate: Candidate) -> Photo:
        if candidate.source == "openverse":
            assert self.openverse is not None
            return await self.openverse.download(candidate.item)
        return await self.commons.download(candidate.item)
