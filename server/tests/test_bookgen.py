"""Book making: store limits and leases, the runner (mocked OpenAI, Commons and R2), the merged
manifest and the owner endpoints. No network, no OpenAI spend."""

import asyncio
import io
import json
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent.accounts import Accounts
from little_explorer_agent.bookgen import moderation, narration, writer
from little_explorer_agent.bookgen.config import BookgenConfig, R2Config
from little_explorer_agent.bookgen.cost import CostLog
from little_explorer_agent.bookgen.manifest import merge
from little_explorer_agent.bookgen.oai import Moderation, OpenAIError, TextResult
from little_explorer_agent.bookgen.photos import Photo, acceptable, license_of, to_jpeg
from little_explorer_agent.bookgen.runner import LeaseLost, Runner, r2_key
from little_explorer_agent.bookgen.service import BookgenService
from little_explorer_agent.bookgen.storage import MemoryStorage, R2Storage
from little_explorer_agent.bookgen.store import BookStore, LimitError, new_book_id
from little_explorer_agent.catalog import build_manifest, content_files, resources_dir
from little_explorer_agent.devices import BOOKGEN_DOGFOOD, DeviceStore, Limits
from little_explorer_agent.mail import Mailer
from little_explorer_agent.token_server import create_app

APP = {"Authorization": "Bearer app-key"}
ADMIN = {"Authorization": "Bearer admin-secret"}
REQUEST = {"title": "The Tall Tower", "section": "Paris", "level": 2, "about": "The Eiffel Tower"}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def page_text(level, i):
    return {
        1: f"Look! A big tower {i}.",
        2: f"The tall tower sparkles at night {i}.",
        3: f"Workers paint the tower every seven years {i}. The paint keeps it from getting rusty.",
    }[level]


def draft(level, pages=None):
    count = pages or writer.PAGES[level]
    return {
        "tagline": "The city of lights",
        "country": "France",
        "flag": "🇫🇷",
        "pages": [
            {"text": page_text(level, i), "about": "A true fact for Milo.", "emoji": "🗼",
             "photo_queries": [f"Eiffel Tower view {i}", f"Paris tower {i}"]}
            for i in range(1, count + 1)
        ],
    }


class FakeOpenAI:
    def __init__(self, level=2, *, request_ok=True, flag_word=None, flag_images=0, safe=True, fixes=None,
                 drafts=None, moderation_down=False):
        self.level = level
        self.request_ok = request_ok
        self.flag_word = flag_word
        self.flag_images = flag_images
        self.safe = safe
        self.fixes = fixes or []
        self.drafts = list(drafts or [])
        self.moderation_down = moderation_down
        self.calls = []

    async def structured(self, *, model, instructions, input, schema_name, schema, web_search=False,
                         max_output_tokens=None, reasoning=None):
        self.calls.append(schema_name)
        if schema_name == "request_check":
            data = {"ok": self.request_ok, "problems": [] if self.request_ok else ["brand"],
                    "reason": "" if self.request_ok else "Books can't be about toy brands."}
        elif schema_name == "picture_book":
            data = self.drafts.pop(0) if self.drafts else draft(self.level)
        else:
            data = {"safe": self.safe, "reason": "" if self.safe else "Too scary.", "fixes": self.fixes}
        return TextResult(data, 2000, 3000, 2 if web_search else 0)

    async def moderate(self, items, *, model="omni-moderation-latest"):
        self.calls.append("moderate")
        if self.moderation_down:
            raise OpenAIError("moderation is down")
        out = []
        for item in items:
            flagged = False
            if item["type"] == "text" and self.flag_word and self.flag_word in item["text"]:
                flagged = True
            if item["type"] == "image_url" and self.flag_images > 0:
                self.flag_images -= 1
                flagged = True
            out.append(Moderation(flagged, ["violence"] if flagged else []))
        return out

    async def close(self):
        pass


def tiny_jpeg(color=(200, 120, 40)):
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(out, "JPEG")
    return out.getvalue()


class FakeCommons:
    def __init__(self, missing=0, authors=None):
        self.missing = missing
        self.downloads = 0
        self.authors = authors or {}

    async def candidates(self, queries, used):
        if self.missing > 0:
            self.missing -= 1
            return []
        return [{"title": f"File:{queries[0]} {n}.jpg"} for n in range(2) if f"File:{queries[0]} {n}.jpg" not in used]

    async def download(self, page):
        self.downloads += 1
        return Photo(tiny_jpeg(), page["title"], self.authors.get(page["title"], "A. Photographer"), "CC BY-SA 4.0",
                     "https://creativecommons.org/licenses/by-sa/4.0", "https://commons.wikimedia.org/wiki/" + page["title"])

    async def close(self):
        pass


@pytest.fixture
def fake_narrate(monkeypatch):
    calls = []

    async def narrate(client, config, book_id, page_id, text, level_no, costs):
        calls.append(page_id)
        costs.audio("narration", config.tts_model, 3.0, speech=True)
        costs.audio("narration", config.stt_model, 3.0, speech=False)
        clip = {"file": narration.clip_name(book_id, page_id), "text": text, "duration": 3.0, "match": 1.0,
                "heard": text, "words": [], "lufs": -18.0, "style": f"bookgen-l{level_no}"}
        return b"m4a:" + text.encode(), clip

    monkeypatch.setattr(narration, "narrate", narrate)
    return calls


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits())


@pytest.fixture
def clock():
    return Clock()


CONFIG = BookgenConfig(openai_key="sk-test", poll_s=0.05)


def make_books(store, clock, **overrides):
    config = BookgenConfig(**{**CONFIG.__dict__, **overrides})
    return BookStore(store, config, clock=clock)


def make_runner(books, client=None, storage=None, commons=None, on_publish=None):
    return Runner(books, books.config, client=client or FakeOpenAI(), storage=storage or MemoryStorage(),
                  commons=commons or FakeCommons(), on_publish=on_publish)


# -- requests and levels ---------------------------------------------------------------------------


def test_validate_request():
    ok = writer.validate_request({"title": "  The\tTall  Tower ", "section": "Paris", "level": "3"})
    assert ok == {"title": "The Tall Tower", "section": "Paris", "level": 3, "about": ""}
    assert writer.validate_request({"title": "Ｔower", "section": "Paris", "level": 1})["title"] == "Tower"  # NFKC
    for bad in (
        {"title": "", "section": "Paris", "level": 2},
        {"title": "x" * 41, "section": "Paris", "level": 2},
        {"title": "Tower", "section": "y" * 31, "level": 2},
        {"title": "Tower", "section": "Paris", "level": 4},
        {"title": "Tower", "section": "Paris", "level": True},
        {"title": "Tower", "section": "Paris", "level": 2, "about": "z" * 301},
        {"title": "!!!", "section": "Paris", "level": 2},
        {"title": 5, "section": "Paris", "level": 2},
    ):
        with pytest.raises(ValueError):
            writer.validate_request(bad)


def test_page_problems_follow_the_level():
    assert writer.page_problems(1, draft(1)) == []
    assert writer.page_problems(2, draft(2)) == []
    assert writer.page_problems(3, draft(3)) == []
    # A level 3 page is too long for level 1, and the page count is checked too.
    problems = writer.page_problems(1, draft(3))
    assert any("pages" in p for p in problems) and any("allows" in p for p in problems)


def test_writing_instructions_carry_the_level():
    assert "Look! A big tower." in writer.writing_instructions(1)
    assert "exactly 8 pages" in writer.writing_instructions(1)
    assert "seven years" in writer.writing_instructions(3)
    assert "<request>" in writer.writing_instructions(2)


def test_assemble_matches_the_app_book_shape():
    book = writer.assemble("tall-tower-abc123", {**REQUEST, "level": 1}, draft(1))
    assert book["id"] == "tall-tower-abc123" and book["city"] == "Paris" and book["theme"] == "The Tall Tower"
    assert book["level"] == 1 and book["generated"] is True and book["flag"] == "🇫🇷"
    assert [p["id"] for p in book["pages"]][:2] == ["p01", "p02"]
    assert book["pages"][0]["photo"] == {"query": "Eiffel Tower view 1", "alternates": ["Paris tower 1"]}
    assert book["coverColor"].startswith("#")
    assert writer.assemble("x-1", REQUEST, {**draft(2), "flag": "FR"})["flag"] == "🌍"


async def test_write_draft_rewrites_until_the_level_fits():
    client = FakeOpenAI(2, drafts=[draft(2, pages=7), draft(2)])
    result = await writer.write_draft(client, CONFIG, REQUEST, CostLog([]))
    assert writer.page_problems(2, result) == []
    assert client.calls.count("picture_book") == 2


async def test_write_draft_gives_up():
    client = FakeOpenAI(2, drafts=[draft(2, pages=7)] * 3)
    with pytest.raises(writer.BookError):
        await writer.write_draft(client, CONFIG, REQUEST, CostLog([]))


async def test_fact_check_applies_fixes_and_can_reject():
    client = FakeOpenAI(2, fixes=[{"page": 2, "text": "The tower glows gold at night.", "about": "Fixed fact."}])
    costs = CostLog([])
    fixed = await writer.fact_check(client, CONFIG, REQUEST, draft(2), costs)
    assert fixed["pages"][1]["text"] == "The tower glows gold at night."
    assert costs.items[-1]["web_searches"] == 2
    with pytest.raises(moderation.Rejected):
        await writer.fact_check(FakeOpenAI(2, safe=False), CONFIG, REQUEST, draft(2), CostLog([]))


# -- moderation ------------------------------------------------------------------------------------


async def test_moderation_rejects_and_fails_closed():
    await moderation.check_request(FakeOpenAI(), CONFIG, REQUEST, CostLog([]))
    with pytest.raises(moderation.Rejected):
        await moderation.check_request(FakeOpenAI(flag_word="Tower"), CONFIG, REQUEST, CostLog([]))
    with pytest.raises(moderation.Rejected) as exc:
        await moderation.check_request(FakeOpenAI(request_ok=False), CONFIG, REQUEST, CostLog([]))
    assert "brand" in exc.value.reason
    with pytest.raises(OpenAIError):
        await moderation.check_request(FakeOpenAI(moderation_down=True), CONFIG, REQUEST, CostLog([]))

    class Short(FakeOpenAI):
        async def moderate(self, items, *, model=""):
            return []

    with pytest.raises(OpenAIError):
        await moderation.check_texts(Short(), CONFIG, ["one", "two"], "book")


# -- photos and narration --------------------------------------------------------------------------


def info(license_short="CC BY-SA 4.0", code="cc-by-sa-4.0", **extra):
    meta = {"LicenseShortName": {"value": license_short}, "License": {"value": code},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}
    meta.update({k: {"value": v} for k, v in extra.items()})
    return {"extmetadata": meta, "mime": "image/jpeg", "width": 2000, "height": 1500}


def test_photo_licenses():
    assert license_of(info())[0]
    assert license_of(info("Public domain", "pd"))[1] == "Public domain"
    assert not license_of(info("CC BY-NC-SA 4.0", "cc-by-nc-sa-4.0"))[0]
    assert not license_of(info("CC BY-SA 4.0", "cc-by-sa-4.0", Restrictions="trademarked"))[0]
    assert not license_of(info("CC BY-SA 4.0", "cc-by-sa-4.0", NonFree="true"))[0]
    assert not license_of(info("All rights reserved", "arr"))[0]
    page = {"title": "File:Tower.jpg", "imageinfo": [info()]}
    assert acceptable(page, set())[0]
    assert not acceptable(page, {"File:Tower.jpg"})[0]
    assert not acceptable({"title": "File:Hunting rifle.jpg", "imageinfo": [info()]}, set())[0]
    small = {"title": "File:S.jpg", "imageinfo": [{**info(), "width": 400, "height": 300}]}
    assert not acceptable(small, set())[0]


def test_to_jpeg_flattens_and_shrinks():
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (3000, 2000), (0, 0, 255, 0)).save(out, "PNG")
    jpeg = to_jpeg(out.getvalue(), 1400)
    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.format == "JPEG" and image.mode == "RGB" and image.size == (1400, 933)
        assert image.getpixel((5, 5)) == (255, 255, 255)


def test_align_maps_heard_words_to_the_page():
    text = "Look! A big tower."
    heard = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, w in enumerate(["look", "a", "big", "tower"])]
    words, score = narration.align(text, heard, 2.0)
    assert score == 1.0
    assert [text[w["location"]:w["location"] + w["length"]] for w in words] == ["Look", "A", "big", "tower"]
    assert [w["start"] for w in words] == [0.0, 0.4, 0.8, 1.2]
    _, partial = narration.align(text, heard[:2], 2.0)
    assert partial < 1.0


def test_narration_instructions_follow_the_level():
    assert narration.instructions(2) == narration.INSTRUCTIONS
    assert narration.instructions(1) != narration.instructions(3)
    assert "six-year-old" not in narration.INSTRUCTIONS.lower()
    assert narration.clip_name("tall-tower-abc123", "p01") == "read-tall-tower-abc123-p01"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
async def test_narrate_end_to_end_with_ffmpeg(tmp_path):
    mp3 = tmp_path / "tone.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=1.2", "-af",
                    "apad=pad_dur=0.3", "-c:a", "libmp3lame", str(mp3)], check=True)
    text = "Look! A big tower."

    class Voice(FakeOpenAI):
        async def speak(self, text, *, model, voice, instructions):
            self.calls.append(("speak", instructions))
            return mp3.read_bytes()

        async def transcribe(self, audio, prompt, *, model="whisper-1"):
            words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(["Look", "A", "big", "tower"])]
            return {"text": text, "duration": 1.5, "words": words}

    client, costs = Voice(), CostLog([])
    m4a, clip = await narration.narrate(client, CONFIG, "tall-tower-abc123", "p01", text, 1, costs)
    assert m4a[4:8] == b"ftyp"
    assert clip["file"] == "read-tall-tower-abc123-p01" and clip["text"] == text and clip["style"] == "bookgen-l1"
    assert len(clip["words"]) == 4 and clip["match"] == 1.0
    assert client.calls[0][1] == narration.instructions(1)
    assert 0 < costs.total < 0.01


# -- storage ---------------------------------------------------------------------------------------


def test_r2_presign_is_a_sigv4_link():
    import datetime as dt

    storage = R2Storage(R2Config("https://acct.r2.cloudflarestorage.com", "books-bucket", "AKID", "secret"))
    when = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)
    url = storage.presign("books/x-1/x-1-p01.jpg", 900, when=when)
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "acct.r2.cloudflarestorage.com" and parts.path == "/books-bucket/books/x-1/x-1-p01.jpg"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"] and query["X-Amz-Expires"] == ["900"]
    assert query["X-Amz-Credential"] == ["AKID/20260102/auto/s3/aws4_request"]
    assert query["X-Amz-Date"] == ["20260102T030405Z"] and len(query["X-Amz-Signature"][0]) == 64
    assert storage.presign("books/x-1/x-1-p01.jpg", 900, when=when) == url
    assert "secret" not in url


# -- the store -------------------------------------------------------------------------------------


def test_queue_limits(store, clock):
    books = make_books(store, clock, per_day=2, per_month=3)
    job, row = books.queue("acct", REQUEST)
    assert job.status == "queued" and row.status == "draft" and row.level == 2 and job.book_id == row.id
    with pytest.raises(LimitError) as exc:
        books.queue("acct", REQUEST)
    assert exc.value.code == "busy"
    claimed = books.claim()
    assert books.publish(claimed, [{"usd": 0.1}])
    books.queue("acct", REQUEST)
    claimed = books.claim()
    books.finish(claimed, "done", costs=[])
    with pytest.raises(LimitError) as exc:
        books.queue("acct", REQUEST)
    assert exc.value.code == "day_limit"
    assert books.usage("acct") == {"active": 0, "today": 2, "this_month": 2, "per_day": 2, "per_month": 3}
    # Failed and rejected books don't use up the quota.
    other = make_books(store, clock, per_day=1)
    job, _ = other.queue("other", REQUEST)
    other.finish(other.claim(), "rejected", error="no")
    assert other.queue("other", REQUEST)[0].status == "queued"


def test_month_limit_and_spend_cap(store, clock):
    books = make_books(store, clock, per_day=10, per_month=1, daily_usd_cap=0.5)
    books.publish(_queued_claim(books, "a"), [{"usd": 0.2}])
    with pytest.raises(LimitError) as exc:
        books.queue("a", REQUEST)
    assert exc.value.code == "month_limit"
    books.publish(_queued_claim(books, "b"), [{"usd": 0.4}])
    assert books.spent_today() == pytest.approx(0.6)
    with pytest.raises(LimitError) as exc:
        books.queue("c", REQUEST)
    assert exc.value.code == "paused" and exc.value.status == 503


def _queued_claim(books, account):
    books.queue(account, REQUEST)
    return books.claim()


def test_claim_one_at_a_time_and_lease_fence(store, clock):
    books = make_books(store, clock, lease_s=300)
    books.queue("a", REQUEST)
    books.queue("b", REQUEST)
    first = books.claim()
    assert first.attempts == 1 and first.status == "running"
    assert books.claim() is None  # one book at a time
    assert books.renew(first, step="photos")
    clock.t += 301  # the worker died; its lease ran out
    again = books.claim()
    assert again.id == first.id and again.attempts == 2 and again.step == "photos"
    # The stale worker can't write any more.
    assert not books.renew(first)
    assert not books.save_book(first, book={"pages": []})
    assert not books.publish(first, [])
    assert books.publish(again, [])
    nxt = books.claim()
    assert nxt.id != first.id and nxt.attempts == 1


def test_release_backs_off(store, clock):
    books = make_books(store, clock)
    books.queue("a", REQUEST)
    job = books.claim()
    assert books.release(job, delay_s=120, error="OpenAI down")
    assert books.job(job.id).status == "queued" and books.claim() is None
    clock.t += 121
    assert books.claim().attempts == 2


def test_cancel_and_delete(store, clock):
    books = make_books(store, clock)
    job, row = books.queue("a", REQUEST)
    assert books.cancel(job.id).status == "failed" and books.book(row.id).status == "failed"
    with pytest.raises(LookupError):
        books.cancel("nope")
    with pytest.raises(ValueError):
        books.set_published(row.id, True)
    assert books.delete_book(row.id) == [] and books.book(row.id) is None and books.job(job.id) is not None


def test_new_book_id():
    book_id = new_book_id("Les Champs-Élysées!")
    assert book_id.startswith("les-champs-elysees-") and len(book_id.rsplit("-", 1)[1]) == 6
    assert new_book_id("🗼🗼").startswith("book-")


# -- the runner ------------------------------------------------------------------------------------


async def test_runner_makes_and_publishes_a_book(store, clock, fake_narrate):
    books = make_books(store, clock)
    storage, published = MemoryStorage(), []

    async def on_publish():
        published.append(True)

    runner = make_runner(books, storage=storage, on_publish=on_publish)
    job, row = books.queue("acct", REQUEST)
    assert await runner.run_once() is True
    done = books.job(job.id)
    assert done.status == "done" and done.step == "publish" and done.error == ""
    book = books.book(row.id)
    assert book.status == "published" and published == [True]
    pages = book.json["pages"]
    assert len(pages) == 10 and all(p["photo"]["file"] == f"{row.id}-{p['id']}.jpg" for p in pages)
    assert pages[0]["photo"]["verified"] is True and pages[0]["photo"]["license"] == "CC BY-SA 4.0"
    assert set(book.narration) == {f"{row.id}/{p['id']}" for p in pages}
    assets = books.assets(row.id)
    assert len(assets) == 20 and set(storage.objects) == {a.r2_key for a in assets.values()}
    assert r2_key(row.id, f"{row.id}-p01.jpg", 1) in storage.objects
    # The cost log: text, fact check (with web search) and narration, under the per-book cap.
    steps = {c["step"] for c in done.costs}
    assert steps == {"text", "narration"} and 0 < done.cost_usd < CONFIG.job_usd_cap
    # The account owns the book now.
    owned = store._run(lambda cur: store._exec(cur, "SELECT book_id FROM account_books WHERE account_id = ?", ("acct",)).fetchall())
    assert (row.id,) in [tuple(r) for r in owned]
    assert await runner.run_once() is False


async def test_runner_resumes_without_paying_twice(store, clock, fake_narrate):
    books = make_books(store, clock)
    storage, commons = MemoryStorage(), FakeCommons()
    client = FakeOpenAI()

    class Crash(Exception):
        pass

    calls = {"n": 0}
    real = narration.narrate

    async def crash_on_third(*args):
        calls["n"] += 1
        if calls["n"] == 3:
            raise asyncio.CancelledError  # a deploy stops the worker mid-book
        return await real(*args)

    narration.narrate = crash_on_third
    try:
        job, row = books.queue("acct", REQUEST)
        runner = make_runner(books, client=client, storage=storage, commons=commons)
        with pytest.raises(asyncio.CancelledError):
            await runner.run_once()
    finally:
        narration.narrate = real
    assert books.job(job.id).status == "running" and books.job(job.id).step == "narration"
    texts_written, photos_downloaded, clips_made = client.calls.count("picture_book"), commons.downloads, len(fake_narrate)
    assert photos_downloaded == 10 and clips_made == 2

    clock.t += CONFIG.lease_s + 1  # the lease runs out; the next instance picks the job up
    runner = make_runner(books, client=client, storage=storage, commons=commons)
    assert await runner.run_once() is True
    assert books.job(job.id).status == "done" and books.job(job.id).attempts == 2
    assert client.calls.count("picture_book") == texts_written  # no new text
    assert commons.downloads == photos_downloaded  # no new photos
    assert len(fake_narrate) == 10  # only the 8 missing clips
    assert len(books.assets(row.id)) == 20


async def test_runner_rejects_flagged_books_and_cleans_up(store, clock, fake_narrate):
    books = make_books(store, clock)
    storage = MemoryStorage()
    job, row = books.queue("acct", REQUEST)
    runner = make_runner(books, client=FakeOpenAI(flag_word="sparkles"), storage=storage)
    await runner.run_once()
    assert books.job(job.id).status == "rejected" and "safety" in books.job(job.id).error
    assert books.book(row.id).status == "rejected" and storage.objects == {} and books.published() == []


async def test_runner_skips_flagged_photos(store, clock, fake_narrate):
    books = make_books(store, clock)
    commons = FakeCommons()
    books.queue("acct", REQUEST)
    await make_runner(books, client=FakeOpenAI(flag_images=1), commons=commons).run_once()
    book = books.published()[0][0]
    assert book.json["pages"][0]["photo"]["commonsTitle"].endswith(" 1.jpg")  # the second candidate
    assert commons.downloads == 11


async def test_runner_skips_photos_with_flagged_credits(store, clock, fake_narrate):
    books = make_books(store, clock)
    first = f"File:{draft(2)['pages'][0]['photo_queries'][0]} 0.jpg"
    commons = FakeCommons(authors={first: "Rude McRudeface"})
    books.queue("acct", REQUEST)
    await make_runner(books, client=FakeOpenAI(flag_word="Rude"), commons=commons).run_once()
    page = books.published()[0][0].json["pages"][0]
    assert page["photo"]["commonsTitle"].endswith(" 1.jpg") and page["photo"]["author"] == "A. Photographer"


async def test_a_worker_that_lost_its_lease_cannot_touch_the_new_owners_files(store, clock):
    books = make_books(store, clock, lease_s=300)
    storage = MemoryStorage()
    runner = make_runner(books, storage=storage)
    books.queue("acct", REQUEST)
    stale = books.claim()
    clock.t += 301
    fresh = books.claim()
    assert fresh.attempts == stale.attempts + 1
    path = f"Photos/{fresh.book_id}-p01.jpg"
    await runner._upload(fresh, path, b"new", "image/jpeg")
    with pytest.raises(LeaseLost):
        await runner._upload(stale, path, b"old", "image/jpeg")
    assert storage.objects == {r2_key(fresh.book_id, f"{fresh.book_id}-p01.jpg", fresh.attempts): (b"new", "image/jpeg")}
    assert books.assets(fresh.book_id)[path].r2_key in storage.objects


async def test_runner_fails_without_enough_photos(store, clock, fake_narrate):
    books = make_books(store, clock)
    storage = MemoryStorage()
    job, _ = books.queue("acct", REQUEST)
    await make_runner(books, storage=storage, commons=FakeCommons(missing=3)).run_once()
    assert books.job(job.id).status == "failed" and "photos" in books.job(job.id).error
    assert storage.objects == {}


async def test_runner_retries_when_moderation_is_down_and_never_publishes(store, clock, fake_narrate):
    books = make_books(store, clock, max_attempts=2)
    storage = MemoryStorage()
    job, row = books.queue("acct", REQUEST)
    runner = make_runner(books, client=FakeOpenAI(moderation_down=True), storage=storage)
    await runner.run_once()
    assert books.job(job.id).status == "queued" and "moderation is down" in books.job(job.id).error
    clock.t += 3600
    await runner.run_once()
    assert books.job(job.id).status == "failed" and books.book(row.id).status == "failed"
    assert books.published() == []


async def test_runner_stops_at_the_cost_cap(store, clock, fake_narrate):
    books = make_books(store, clock, job_usd_cap=0.02)
    job, _ = books.queue("acct", REQUEST)
    await make_runner(books).run_once()
    assert books.job(job.id).status == "failed" and "cap" in books.job(job.id).error


async def test_runner_pauses_at_the_daily_cap(store, clock, fake_narrate):
    books = make_books(store, clock, daily_usd_cap=0.1)
    books.publish(_queued_claim(books, "a"), [{"usd": 0.2}])
    books.config = BookgenConfig(**{**books.config.__dict__, "daily_usd_cap": 10.0})
    books.queue("b", REQUEST)
    books.config = BookgenConfig(**{**books.config.__dict__, "daily_usd_cap": 0.1})
    runner = make_runner(books)
    assert await runner.run_once() is False and runner.state["paused"] is True


async def test_runner_looks_again_only_while_a_job_waits(store, clock, fake_narrate):
    books = make_books(store, clock)
    runner = make_runner(books)
    assert await runner.run_once() is False and runner.state["next_check_s"] is None  # idle: sleep until woken
    job, _ = books.queue("acct", REQUEST)
    claimed = books.claim()  # another instance (the other side of a deploy) is making it
    assert await runner.run_once() is False
    assert runner.state["next_check_s"] == books.config.lease_s + 1  # when its lease would run out
    books.release(claimed, delay_s=120, error="try later")
    assert await runner.run_once() is False and runner.state["next_check_s"] == 120  # the retry's backoff
    clock.t += 120
    assert await runner.run_once() is True and books.job(job.id).status == "done"
    assert await runner.run_once() is False and runner.state["next_check_s"] is None


async def test_paused_runner_waits_for_tomorrow_only_with_work(store, clock, fake_narrate):
    books = make_books(store, clock, daily_usd_cap=0.1)
    books.publish(_queued_claim(books, "a"), [{"usd": 0.2}])
    runner = make_runner(books)
    assert await runner.run_once() is False and runner.state["paused"] and runner.state["next_check_s"] is None
    books.config = BookgenConfig(**{**books.config.__dict__, "daily_usd_cap": 10.0})
    books.queue("b", REQUEST)
    books.config = BookgenConfig(**{**books.config.__dict__, "daily_usd_cap": 0.1})
    assert await runner.run_once() is False
    assert 1 <= runner.state["next_check_s"] <= 86_401


async def test_idle_service_never_touches_the_database(server, store, monkeypatch):
    # Neon's free plan scales to zero when idle; a poll every few seconds would keep it awake all month.
    c, service = server
    # Startup: the made books were loaded before serving; wait for the runner's first check to finish too.
    await asyncio.wait_for(service.runner.asleep.wait(), timeout=10)
    assert service.runner.state["next_check_s"] is None and service._refreshing is None
    calls: list[str] = []
    run = store._run
    monkeypatch.setattr(store, "_run", lambda fn: (calls.append("db"), run(fn))[1])
    now = [service.clock()]
    service.clock = lambda: now[0]

    for _ in range(3):  # a week of idle time, with the keep-awake pings
        now[0] += 2 * 86_400
        assert (await c.get("/healthz")).status == 200
        await asyncio.sleep(0.1)  # any timer (poll_s is 0.05 in these tests) would fire
    assert calls == [] and service.runner.asleep.is_set()

    # A manifest request (user traffic) reloads made books in the background when they're 10+ minutes old.
    assert (await c.get("/content/manifest.json", headers=APP)).status == 200
    await service._refreshing
    assert calls
    calls.clear()
    now[0] += 60
    assert (await c.get("/content/manifest.json", headers=APP)).status == 200
    for _ in range(20):
        await asyncio.sleep(0)
    assert calls == []


async def test_cancelled_job_stops_at_the_next_step(store, clock, fake_narrate):
    books = make_books(store, clock)
    storage = MemoryStorage()
    job, row = books.queue("acct", REQUEST)

    class CancelDuringPhotos(FakeCommons):
        async def download(self, page):
            if self.downloads == 2:
                books.cancel(job.id)
            return await super().download(page)

    await make_runner(books, storage=storage, commons=CancelDuringPhotos()).run_once()
    assert books.job(job.id).status == "failed" and books.book(row.id).status == "failed"
    assert storage.objects == {} and books.assets(row.id) == {}


# -- the manifest ----------------------------------------------------------------------------------


@pytest.fixture
def static():
    root = resources_dir()
    return build_manifest(root), content_files(root)


async def published_service(store, clock, static, fake_narrate):
    manifest, files = static
    storage = MemoryStorage()
    service = BookgenService(store, static_manifest=manifest, static_files=files, config=CONFIG, storage=storage,
                             client=FakeOpenAI(), commons=FakeCommons())
    service.books.clock = clock
    service.runner.books.clock = clock
    job = service.queue("acct", REQUEST)
    await service.runner.run_once()
    return service, job


async def test_manifest_adds_published_books(store, clock, static, fake_narrate):
    manifest, files = static
    before = json.loads(json.dumps(manifest))
    assert merge(manifest, files, [])[0] is manifest
    service, job = await published_service(store, clock, static, fake_narrate)
    served = service.manifest
    assert served is manifest  # updated in place
    assert served["order"] == before["order"] + [job.book_id] and served["version"] != before["version"]
    for rel, entry in before["files"].items():
        if rel not in ("library.json", "Narration/narration.json"):
            assert served["files"][rel] == entry
    library = json.loads(service.extra["library.json"])
    assert library["order"] == served["order"]
    clips = json.loads(service.extra["Narration/narration.json"])["clips"]
    assert f"{job.book_id}/p01" in clips and len(clips) == len(json.loads((files["Narration/narration.json"]).read_bytes())["clips"]) + 10
    body = service.extra[f"Books/{job.book_id}.json"]
    assert served["files"][f"Books/{job.book_id}.json"]["size"] == len(body)
    assert json.loads(body)["level"] == 2
    photo = service.extra[f"Photos/{job.book_id}-p01.jpg"]
    assert served["files"][f"Photos/{job.book_id}-p01.jpg"]["sha256"] == photo.sha256
    # Unpublishing (the kill switch) restores the static manifest exactly.
    await service.set_published(job.book_id, False)
    assert service.manifest == before and service.extra == {}
    await service.set_published(job.book_id, True)
    assert job.book_id in service.manifest["order"]
    assert await service.delete_book(job.book_id) == 20
    assert service.manifest == before and service.storage.objects == {}


# -- the web server --------------------------------------------------------------------------------


@pytest.fixture
def accounts(store, clock):
    return Accounts(store, secret="test-secret", clock=clock)


async def make_client(store, accounts, service):
    app = create_app(access_key="app-key", store=store, admin_key="admin-secret", report_key="r",
                     accounts=accounts, mailer=Mailer(provider="console"), bookgen=service)
    return TestClient(TestServer(app))


@pytest.fixture
async def server(store, clock, accounts, static, fake_narrate):
    manifest, files = static
    service = BookgenService(store, static_manifest=manifest, static_files=files,
                             config=BookgenConfig(**{**CONFIG.__dict__, "runner": True}),
                             storage=MemoryStorage(), client=FakeOpenAI(), commons=FakeCommons())
    service.books.clock = clock
    async with await make_client(store, accounts, service) as c:
        yield c, service


async def test_without_bookgen_config_nothing_changes(store, accounts, static):
    manifest, files = static
    before = json.loads(json.dumps(manifest))
    service = BookgenService(store, static_manifest=manifest, static_files=files, config=BookgenConfig())
    assert not service.ready and service.runner is None
    async with await make_client(store, accounts, service) as c:
        resp = await c.get("/content/manifest.json")
        assert await resp.json() == before
        resp = await c.post("/admin/books/generate", json={**REQUEST, "account": "x"}, headers=ADMIN)
        assert resp.status == 503
        assert (await c.get("/content/library.json")).status == 200


async def test_levels_endpoint(server):
    c, _ = server
    levels = (await (await c.get("/levels")).json())["levels"]
    assert [lv["level"] for lv in levels] == [1, 2, 3]
    assert levels[0]["ages"] == "4–5" and levels[0]["example"] == "Look! A big tower."


async def test_generate_endpoint(server, accounts):
    c, service = server
    assert (await c.post("/admin/books/generate", json=REQUEST)).status == 401
    resp = await c.post("/admin/books/generate", json={**REQUEST, "account": "nobody@example.com"}, headers=ADMIN)
    assert resp.status == 404
    accounts.create("kid@example.com")
    resp = await c.post("/admin/books/generate", json={**REQUEST, "account": "kid@example.com"}, headers=ADMIN)
    assert resp.status == 403
    resp = await c.post("/admin/accounts/kid@example.com/bookgen", json={"can_generate": True}, headers=ADMIN)
    assert resp.status == 200
    resp = await c.post("/admin/books/generate", json={**REQUEST, "level": 9, "account": "kid@example.com"}, headers=ADMIN)
    assert resp.status == 400
    resp = await c.post("/admin/books/generate", json={**REQUEST, "account": "kid@example.com"}, headers=ADMIN)
    assert resp.status == 202, await resp.text()
    body = await resp.json()
    job_id, book_id = body["job"]["id"], body["job"]["book_id"]
    assert body["usage"]["active"] == 1
    resp = await c.post("/admin/books/generate", json={**REQUEST, "account": "kid@example.com"}, headers=ADMIN)
    assert resp.status == 429 and (await resp.json())["error"] == "busy"

    for _ in range(200):  # the runner started with the app; wait for it
        job = await (await c.get(f"/admin/jobs/{job_id}", headers=ADMIN)).json()
        order = (await (await c.get("/content/manifest.json")).json())["order"]
        if job["status"] == "done" and job["book_id"] in order:  # on_publish refreshes just after
            break
        await asyncio.sleep(0.02)
    assert job["status"] == "done", job
    jobs = (await (await c.get("/admin/jobs", headers=ADMIN)).json())["jobs"]
    assert jobs[0]["id"] == job_id
    listed = (await (await c.get("/admin/books?account=kid@example.com", headers=ADMIN)).json())["books"]
    assert [b["id"] for b in listed] == [book_id] and listed[0]["status"] == "published"
    full = await (await c.get(f"/admin/books/{book_id}", headers=ADMIN)).json()
    assert full["book"]["level"] == 2 and len(full["narration"]) == 10

    manifest = await (await c.get("/content/manifest.json")).json()
    assert manifest["order"][-1] == book_id
    library = await (await c.get("/content/library.json")).json()
    assert library["order"] == manifest["order"]
    resp = await c.get(f"/content/Books/{book_id}.json")
    assert resp.status == 200 and (await resp.json())["id"] == book_id
    resp = await c.get(f"/content/Photos/{book_id}-p01.jpg", allow_redirects=False)
    assert resp.status == 302 and resp.headers["Location"].startswith(f"https://r2.invalid/books/{book_id}/")
    resp = await c.get(f"/content/Narration/read-{book_id}-p01.m4a", allow_redirects=False)
    assert resp.status == 302
    assert (await c.get("/content/Photos/paris-p01.jpg")).status in (200, 404)
    health = await (await c.get("/healthz")).json()
    assert health["content"] == manifest["version"] and health["bundled"] == build_manifest()["version"]
    assert health["bundled"] != health["content"]

    status = await (await c.get("/admin/bookgen", headers=ADMIN)).json()
    assert status["ready"] and status["everyone"] is False and status["made_books_on_shelf"] == 1
    assert status["spent_today_usd"] > 0 and status["limits"]["per_day"] == 3

    resp = await c.post(f"/admin/books/{book_id}/unpublish", headers=ADMIN)
    assert resp.status == 200 and (await resp.json())["status"] == "unpublished"
    assert book_id not in (await (await c.get("/content/manifest.json")).json())["order"]
    assert (await c.get(f"/content/Books/{book_id}.json")).status == 404
    assert (await c.post(f"/admin/books/{book_id}/frobnicate", headers=ADMIN)).status == 404
    resp = await c.post(f"/admin/books/{book_id}/delete", headers=ADMIN)
    assert (await resp.json())["deleted_files"] == 20 and service.storage.objects == {}
    assert (await c.get(f"/admin/books/{book_id}", headers=ADMIN)).status == 404
    assert (await c.post("/admin/jobs/nope/cancel", headers=ADMIN)).status == 404


async def test_everyone_switch(server, accounts):
    c, _ = server
    account, _ = accounts.create("parent@example.com")
    assert not accounts.get(account.id).can_generate
    resp = await c.post("/admin/bookgen", json={"everyone": "yes"}, headers=ADMIN)
    assert resp.status == 400
    resp = await c.post("/admin/bookgen", json={"everyone": True}, headers=ADMIN)
    assert (await resp.json())["everyone"] is True
    assert accounts.get(account.id).can_generate
    await c.post("/admin/bookgen", json={"everyone": False}, headers=ADMIN)
    assert not accounts.get(account.id).can_generate
    assert Accounts(accounts.store, secret="s", bookgen_everyone=True).get(account.id).can_generate


def test_dogfood_accounts_can_generate(store, accounts, tmp_path):
    mom, _ = accounts.create("angilyu@gmail.com")
    other, _ = accounts.create("someone@example.com")
    assert accounts.get(mom.id).can_generate and not accounts.get(other.id).can_generate
    # An account made before the column existed gets the flag from the one-time migration.
    store.transaction(lambda cur: store._exec(cur, "UPDATE accounts SET can_generate = 0", ()))
    store.transaction(lambda cur: store._exec(cur, "DELETE FROM settings WHERE key = ?", (BOOKGEN_DOGFOOD,)))
    again = DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits())
    fresh = Accounts(again, secret="s")
    assert fresh.get(mom.id).can_generate and not fresh.get(other.id).can_generate
    assert fresh.summary()[0]["can_generate"] in (True, False)
    # The owner's toggle wins afterwards (the migration runs once).
    fresh.set_can_generate(mom.id, False)
    assert not Accounts(DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits()), secret="s").get(mom.id).can_generate


async def _make_book_for(c, service, email, title):
    resp = await c.post("/admin/books/generate", json={**REQUEST, "title": title, "account": email}, headers=ADMIN)
    assert resp.status == 202, await resp.text()
    job_id = (await resp.json())["job"]["id"]
    for _ in range(200):
        job = service.books.job(job_id)
        if job.status == "done" and job.book_id in service.manifest["order"]:
            return job.book_id
        await asyncio.sleep(0.02)
    raise AssertionError(service.books.job(job_id))


async def _app_account(c, accounts, email, device):
    code, _ = accounts.start(email)
    token, _, _ = accounts.verify(email, code, (device, "iPad", "iPad13,1"))
    return (await (await c.get("/account", headers={**APP, "X-Pip-Session": token})).json())["account"]


async def test_all_books_accounts_only_see_their_own_made_books(server, accounts, clock):
    # The 1.0 app reads all_books: true as "every book in the manifest", which would show one family's made
    # book to every all_books account (App Review's too); the app always gets an explicit shelf instead.
    c, service = server
    a, _ = accounts.create("angilyu@gmail.com", all_books=True)
    b, _ = accounts.create("hfevers@gmail.com", all_books=True)
    theirs = await _make_book_for(c, service, b.email, "Owls at Night")
    mine = await _make_book_for(c, service, a.email, "Tall Towers")
    catalog = list(service.static["order"])
    assert {theirs, mine} <= set(service.manifest["order"]) and not {theirs, mine} & set(catalog)

    shelf = await _app_account(c, accounts, a.email, "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B")
    assert shelf["all_books"] is False
    assert shelf["books"] == catalog + [mine]  # every hand-made book, its own made book, not B's
    assert theirs not in shelf["books"]
    assert accounts.get(a.email).may_read(mine) and not accounts.get(a.email).may_read(theirs)
    assert accounts.get(a.email).may_read("chengdu")  # a hidden hand-made book
    assert accounts.get(b.email).may_read(theirs) and not accounts.get(b.email).may_read(mine)

    # A hand-made book added later (a deploy with a new bundled book) is on every all_books shelf.
    service.static["order"].append("brand-new-city")
    clock.t += 120  # the next sign-in code
    assert "brand-new-city" in (await _app_account(c, accounts, a.email, "C0FFEE00-6EBF-47BC-AA79-5FE554F0502B"))["books"]
    assert accounts.get(a.email).may_read("brand-new-city")

    # The owner still sees the real flag and the account's own grants.
    detail = await (await c.get(f"/admin/accounts/{a.email}", headers=ADMIN)).json()
    assert detail["account"]["all_books"] is True and mine in detail["account"]["books"]
    assert theirs not in detail["account"]["books"]


async def test_accounts_without_all_books_see_only_their_grants(server, accounts):
    c, service = server
    a, _ = accounts.create("angilyu@gmail.com")
    mine = await _make_book_for(c, service, a.email, "Tall Towers")
    shelf = await _app_account(c, accounts, a.email, "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B")
    assert shelf["all_books"] is False and shelf["books"] == sorted([*accounts.default_books, mine])


async def test_account_delete_removes_made_books(server, accounts, clock):
    c, service = server
    account, _ = accounts.create("hfevers@gmail.com")
    job = service.queue(account.id, REQUEST)
    service.wake()
    for _ in range(200):
        if service.books.job(job.id).status == "done" and job.book_id in service.manifest["order"]:
            break
        await asyncio.sleep(0.02)
    assert service.storage.objects and job.book_id in service.manifest["order"]
    code, _ = accounts.start("hfevers@gmail.com")
    token, _, _ = accounts.verify("hfevers@gmail.com", code, ("BDFF5D95-6EBF-47BC-AA79-5FE554F0502B", "iPad", "iPad13,1"))
    clock.t += 120
    code, _ = accounts.start("hfevers@gmail.com", "delete")
    resp = await c.post("/account/delete", json={"code": code}, headers={**APP, "X-Pip-Session": token})
    assert resp.status == 200, await resp.text()
    assert service.storage.objects == {} and job.book_id not in service.manifest["order"]
    assert service.books.books(account.id) == []
    kept = service.books.job(job.id)  # nothing about the account, but today's spend still counts
    assert kept.account_id == "" and kept.input == {} and kept.cost_usd > 0
    assert service.books.spent_today() == pytest.approx(kept.cost_usd)


def test_backup_covers_bookgen_tables(store, clock):
    books = make_books(store, clock)
    books.queue("a", REQUEST)
    dump = store.backup()["tables"]
    assert {"generation_jobs", "books", "book_assets"} <= set(dump)
    assert dump["generation_jobs"]["rows"] and dump["books"]["rows"]
    store.transaction(lambda cur: store._exec(cur, "DELETE FROM books", ()))
    store.restore({"tables": dump})
    assert len(books.books("a")) == 1
