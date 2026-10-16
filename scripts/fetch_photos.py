#!/usr/bin/env python3
"""Fetch, license-check and bundle the Wikimedia Commons photo for every book page.

For each page in LittleExplorer/Resources/Books/*.json:
  * use photo.commonsTitle if pinned, otherwise search Commons with photo.query
    (Quality images first, then any bitmap), skipping files already used elsewhere;
  * read the file's license from the Commons API (extmetadata) and accept only
    public domain, CC0, CC BY or CC BY-SA (never NC/ND or unknown);
  * download a resized rendition to LittleExplorer/Resources/Photos/<book>-<page>.<ext>
    and recompress it with `sips` when available;
  * write author, license, links and verified=true back into the JSON.

Stdlib only. Usage:
  python3 scripts/fetch_photos.py                # fetch missing photos
  python3 scripts/fetch_photos.py --only paris/eiffel --force
  python3 scripts/fetch_photos.py --check        # re-verify licenses of pinned files, no downloads
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS = ROOT / "LittleExplorer" / "Resources" / "Books"
PHOTOS = ROOT / "LittleExplorer" / "Resources" / "Photos"
API = "https://commons.wikimedia.org/w/api.php"
UA = "LittleExplorer/1.0 (https://github.com/angilyu/little-explorer; kids picture-book app)"
WIDTH = 1400
MIN_WIDTH = 1000

ALLOWED = re.compile(r"^(public domain|pd\b|cc0|cc[- ]by( |-)?(sa)?[- ]?\d|cc[- ]by(-sa)?$|cc by-sa|cc by \d)", re.I)
REJECT = re.compile(r"\b(nc|nd)\b|non-?commercial|no ?deriv|fair use|copyrighted", re.I)


def api(**params: str) -> dict:
    params = {"format": "json", "formatversion": "2", **params}
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as exc:  # network hiccup or 429
            if attempt == 3:
                raise
            print(f"   retrying after {exc}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def license_of(info: dict) -> tuple[bool, str, str, str]:
    """Returns (ok, short name, license url, reason)."""
    meta = info.get("extmetadata", {})
    short = clean(meta.get("LicenseShortName", {}).get("value"))
    code = clean(meta.get("License", {}).get("value"))
    url = clean(meta.get("LicenseUrl", {}).get("value"))
    restrictions = clean(meta.get("Restrictions", {}).get("value"))
    nonfree = clean(meta.get("NonFree", {}).get("value"))
    label = short or code
    if nonfree.lower() == "true":
        return False, label, url, "non-free"
    if not label:
        return False, label, url, "no license metadata"
    if REJECT.search(label) or REJECT.search(code):
        return False, label, url, "NC/ND or not free"
    if "trademark" in restrictions.lower():
        return False, label, url, f"restricted: {restrictions}"
    if not (ALLOWED.search(label) or ALLOWED.search(code) or code.lower().startswith(("cc-by", "cc0", "pd"))):
        return False, label, url, f"license not on allow-list: {label!r} / {code!r}"
    if label.lower().startswith("public domain") or code.lower().startswith("pd"):
        label = "Public domain"
    return True, label, url, ""


def imageinfo(titles: list[str]) -> list[dict]:
    data = api(
        action="query",
        titles="|".join(titles),
        prop="imageinfo",
        iiprop="url|size|mime|extmetadata",
        iiurlwidth=str(WIDTH),
    )
    return data.get("query", {}).get("pages", [])


def search(query: str, quality: bool) -> list[dict]:
    q = f"{query} filetype:bitmap" + (" incategory:Quality_images" if quality else "")
    data = api(
        action="query",
        generator="search",
        gsrsearch=q,
        gsrnamespace="6",
        gsrlimit="20",
        prop="imageinfo",
        iiprop="url|size|mime|extmetadata",
        iiurlwidth=str(WIDTH),
    )
    pages = data.get("query", {}).get("pages", [])
    return sorted(pages, key=lambda p: p.get("index", 999))


def acceptable(page: dict, used: set[str], pinned: bool = False) -> tuple[bool, str]:
    if page.get("title") in used:
        return False, "already used on another page"
    infos = page.get("imageinfo") or []
    if not infos:
        return False, "missing imageinfo"
    info = infos[0]
    ok, _, _, reason = license_of(info)
    if not ok:
        return False, reason
    if info.get("mime") not in ("image/jpeg", "image/png", "image/svg+xml", "image/webp", "image/tiff"):
        return False, f"mime {info.get('mime')}"
    is_svg = info.get("mime") == "image/svg+xml"
    width, height = info.get("width", 0), info.get("height", 1)
    if not is_svg and width < MIN_WIDTH:
        return False, f"too small ({width}px)"
    ratio = width / max(height, 1)
    if not is_svg and not pinned and not 0.6 <= ratio <= 2.2:
        return False, f"awkward aspect ratio {ratio:.2f}"
    return True, ""


def pick(photo: dict, used: set[str]) -> dict:
    title = photo.get("commonsTitle")
    if title:
        pages = imageinfo([title])
        if not pages or pages[0].get("missing"):
            raise SystemExit(f"   pinned file not found on Commons: {title}")
        ok, reason = acceptable(pages[0], used - {title}, pinned=True)
        if not ok:
            raise SystemExit(f"   pinned file rejected ({reason}): {title}")
        return pages[0]
    query = photo.get("query")
    if not query:
        raise SystemExit("   page has neither commonsTitle nor query")
    for quality in (True, False):
        for page in search(query, quality):
            ok, reason = acceptable(page, used)
            if ok:
                return page
            print(f"   skip {page.get('title')}: {reason}")
    raise SystemExit(f"   no acceptable photo for query {query!r}")


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)
    if dest.suffix == ".jpg" and shutil.which("sips"):
        subprocess.run(
            ["sips", "-Z", str(WIDTH), "-s", "formatOptions", "72", str(dest)],
            check=True,
            stdout=subprocess.DEVNULL,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", action="append", default=[], help="book or book/page to process (repeatable)")
    parser.add_argument("--force", action="store_true", help="re-download even if already verified")
    parser.add_argument("--check", action="store_true", help="only re-verify licenses of pinned files")
    args = parser.parse_args()

    PHOTOS.mkdir(parents=True, exist_ok=True)
    books = sorted(BOOKS.glob("*.json"))
    used = {
        p["photo"]["commonsTitle"]
        for path in books
        for p in json.loads(path.read_text())["pages"]
        if p["photo"].get("commonsTitle")
    }
    failures = 0

    for path in books:
        book = json.loads(path.read_text())
        changed = False
        for page in book["pages"]:
            key = f"{book['id']}/{page['id']}"
            if args.only and book["id"] not in args.only and key not in args.only:
                continue
            photo = page["photo"]

            if args.check:
                if not photo.get("commonsTitle"):
                    print(f"{key}: not pinned yet")
                    failures += 1
                    continue
                info = imageinfo([photo["commonsTitle"]])[0]["imageinfo"][0]
                ok, label, _, reason = license_of(info)
                print(f"{key}: {'OK' if ok else 'FAIL'} {label} {reason}")
                failures += 0 if ok else 1
                continue

            local = PHOTOS / (photo.get("file") or "")
            if photo.get("verified") and photo.get("file") and local.exists() and not args.force:
                continue

            print(f"{key}: {photo.get('commonsTitle') or photo.get('query')}")
            try:
                chosen = pick(photo, used)
            except SystemExit as exc:
                print(exc, file=sys.stderr)
                failures += 1
                continue

            info = chosen["imageinfo"][0]
            ok, label, license_url, _ = license_of(info)
            meta = info.get("extmetadata", {})
            author = clean(meta.get("Artist", {}).get("value")) or clean(meta.get("Credit", {}).get("value"))
            author = author[:80] or "Unknown author"
            ext = ".png" if info.get("mime") in ("image/svg+xml", "image/png") else ".jpg"
            filename = f"{book['id']}-{page['id']}{ext}"
            download(info.get("thumburl") or info["url"], PHOTOS / filename)

            used.add(chosen["title"])
            photo.update(
                {
                    "file": filename,
                    "commonsTitle": chosen["title"],
                    "author": author,
                    "license": label,
                    "licenseUrl": license_url or None,
                    "sourceUrl": info.get("descriptionurl"),
                    "verified": ok,
                }
            )
            changed = True
            print(f"   -> {chosen['title']} | {author} | {label}")
            time.sleep(0.3)

        if changed:
            path.write_text(json.dumps(book, ensure_ascii=False, indent=2) + "\n")

    if failures:
        print(f"\n{failures} page(s) need attention", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
