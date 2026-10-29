"""Book making: store limits and leases, the runner (mocked OpenAI, Commons and R2), each account's
manifest of made books, and the grown-up and owner endpoints. No network, no OpenAI spend."""

import asyncio
import hashlib
import io
import json
import shutil
import subprocess
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent.accounts import Accounts
from little_explorer_agent.bookgen import moderation, narration, writer
from little_explorer_agent.bookgen.config import BookgenConfig, R2Config
from little_explorer_agent.bookgen.cost import CostLog
from little_explorer_agent.bookgen.oai import Moderation, OpenAIError, TextResult
from little_explorer_agent.bookgen.photos import BadPhoto, Commons, Photo, PhotoError, acceptable, license_of, to_jpeg
from little_explorer_agent.bookgen.runner import LeaseLost, Runner, page_paths, r2_key
from little_explorer_agent.bookgen.service import BookgenService
from little_explorer_agent.bookgen.storage import MemoryStorage, R2Storage
from little_explorer_agent.bookgen.store import BookStore, LimitError, friendly_error, new_book_id
from little_explorer_agent.catalog import build_manifest, content_files, resources_dir
from little_explorer_agent.devices import BOOKGEN_DOGFOOD, DeviceStore, Limits
from little_explorer_agent.mail import Mailer
from little_explorer_agent.token_server import create_app

APP = {"Authorization": "Bearer app-key"}
ADMIN = {"Authorization": "Bearer admin-secret"}
# The first app that shows made books (app_versions.MADE_BOOKS_VERSION); 1.0 sends no headers.
APP_1_2 = {"X-App-Version": "1.2", "X-App-Build": "60", "X-App-Channel": "testflight"}
APP_1_1 = {"X-App-Version": "1.1", "X-App-Build": "50", "X-App-Channel": "appstore"}
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
                 drafts=None, moderation_down=False, pages=None):
        self.level = level
        self.request_ok = request_ok
        self.flag_word = flag_word
        self.flag_images = flag_images
        self.safe = safe
        self.fixes = fixes or []
        self.drafts = list(drafts or [])
        self.moderation_down = moderation_down
        self.pages = list(pages or [])
        self.calls = []

    async def structured(self, *, model, instructions, input, schema_name, schema, web_search=False,
                         max_output_tokens=None, reasoning=None):
        self.calls.append(schema_name)
        if schema_name == "request_check":
            data = {"ok": self.request_ok, "problems": [] if self.request_ok else ["brand"],
                    "reason": "" if self.request_ok else "Books can't be about toy brands."}
        elif schema_name == "picture_book":
            data = self.drafts.pop(0) if self.drafts else draft(self.level)
        elif schema_name == "picture_page":
            data = self.pages.pop(0) if self.pages else {
                "text": page_text(self.level, "again"), "about": "A new true fact for Milo.", "emoji": "🌉",
                "photo_queries": ["Tower at sunset", "Tower from the river"]}
        else:
            data = {"safe": self.safe, "reason": "" if self.safe else "Too scary.", "fixes": self.fixes}
        return TextResult(data, 2000, 3000, 2 if web_search else 0)

    async def moderate(self, input, *, model="omni-moderation-latest"):
        # Like the real API: a list of strings gets one result per string, but a list of multimodal items
        # ({"type": "text"} / {"type": "image_url"}) is one input and gets ONE combined result.
        self.calls.append("moderate")
        if self.moderation_down:
            raise OpenAIError("moderation is down")
        if all(isinstance(item, str) for item in input):
            return [self._flag(bool(self.flag_word and self.flag_word in text)) for text in input]
        flagged = False
        for item in input:
            if item["type"] == "text" and self.flag_word and self.flag_word in item["text"]:
                flagged = True
            if item["type"] == "image_url" and self.flag_images > 0:
                self.flag_images -= 1
                flagged = True
        return [self._flag(flagged)]

    @staticmethod
    def _flag(flagged):
        return Moderation(flagged, ["violence"] if flagged else [])

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


def make_runner(books, client=None, storage=None, commons=None):
    return Runner(books, books.config, client=client or FakeOpenAI(), storage=storage or MemoryStorage(),
                  commons=commons or FakeCommons())


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


async def test_check_texts_sends_plain_strings_for_one_result_each():
    """The real API gives a multimodal array ONE combined result, so texts must go as plain strings
    (sending them as {"type": "text"} items failed every made book with "wrong number of results")."""
    sent = []

    class Spy(FakeOpenAI):
        async def moderate(self, input, *, model=""):
            sent.append(input)
            return await super().moderate(input, model=model)

    texts = [f"page {i}" for i in range(40)] + ["", "  "]
    await moderation.check_texts(Spy(), CONFIG, texts, "book")
    assert [len(batch) for batch in sent] == [32, 8]
    assert all(isinstance(t, str) for batch in sent for t in batch)
    with pytest.raises(moderation.Rejected):
        await moderation.check_texts(FakeOpenAI(flag_word="page 39"), CONFIG, texts, "book")
    # The fake answers like the API: a multimodal array is one input.
    combined = await FakeOpenAI().moderate([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    assert len(combined) == 1


async def test_image_moderation_sends_one_image_item():
    sent = []

    class Spy(FakeOpenAI):
        async def moderate(self, input, *, model=""):
            sent.append(input)
            return await super().moderate(input, model=model)

    assert await moderation.image_ok(Spy(), CONFIG, tiny_jpeg())
    assert len(sent[0]) == 1 and sent[0][0]["type"] == "image_url"
    assert sent[0][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert not await moderation.image_ok(FakeOpenAI(flag_images=1), CONFIG, tiny_jpeg())


async def test_a_childs_name_on_the_shelf_is_fine():
    request = {"title": "Dinosaur", "section": "For Matt", "level": 1,
               "about": "Little dinosaur meeting friends, each friend is a different type of dinosaur"}
    client = FakeOpenAI(1)
    await moderation.check_request(client, CONFIG, request, CostLog([]))
    assert client.calls == ["moderate", "request_check"]
    assert "For Matt" in moderation._CLASSIFY_INSTRUCTIONS and "Matt is 5" in moderation._CLASSIFY_INSTRUCTIONS
    assert "For Matt" in writer.writing_instructions(1)


# -- the OpenAI client against the real response shapes ----------------------------------------------


class Canned:
    """Stands in for OpenAIClient._post with bodies shaped like the real API's."""

    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.sent = []

    async def __call__(self, path, *, json_body=None, data=None, content_type="application/json"):
        self.sent.append((path, json_body))
        return json.dumps(self.bodies.pop(0)).encode()


def client_with(*bodies):
    from little_explorer_agent.bookgen.oai import OpenAIClient

    client = OpenAIClient("sk-test")
    client._post = Canned(*bodies)
    return client


async def test_client_parses_a_responses_api_answer():
    # Raw REST has no "output_text" (that's an SDK helper): reasoning, web searches, then the message.
    body = {
        "status": "completed",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {"type": "web_search_call", "id": "ws_1", "status": "completed", "action": {"type": "search", "query": "x"}},
            {"type": "web_search_call", "id": "ws_2", "status": "completed", "action": {"type": "search", "query": "y"}},
            {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": "{\"safe\": true, ", "annotations": []},
                         {"type": "output_text", "text": "\"reason\": \"\", \"fixes\": []}", "annotations": []}]},
        ],
        "usage": {"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540},
    }
    client = client_with(body)
    result = await client.structured(model="gpt-5.4-mini", instructions="i", input="x", schema_name="fact_check",
                                     schema=writer.CHECK_SCHEMA, web_search=True, reasoning="low")
    assert result.data == {"safe": True, "reason": "", "fixes": []}
    assert (result.input_tokens, result.output_tokens, result.web_searches) == (1200, 340, 2)
    sent = client._post.sent[0][1]
    assert sent["tools"] == [{"type": "web_search"}] and sent["text"]["format"]["strict"] is True

    refused = {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}
    with pytest.raises(OpenAIError):
        await client_with(refused).structured(model="m", instructions="i", input="x", schema_name="s", schema={})
    with pytest.raises(OpenAIError):
        await client_with({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []}) \
            .structured(model="m", instructions="i", input="x", schema_name="s", schema={})


async def test_client_parses_moderation_answers():
    def result(flagged, *cats):
        return {"flagged": flagged, "categories": {"violence": "violence" in cats, "sexual": "sexual" in cats},
                "category_scores": {"violence": 0.9 if flagged else 0.0, "sexual": 0.0}}

    client = client_with({"id": "modr-1", "model": "omni-moderation-latest",
                          "results": [result(False), result(True, "violence"), result(False)]})
    out = await client.moderate(["a", "b", "c"])
    assert [m.flagged for m in out] == [False, True, False] and out[1].categories == ["violence"]
    assert client._post.sent[0][1]["input"] == ["a", "b", "c"]
    with pytest.raises(OpenAIError):
        await client_with({"results": []}).moderate(["a"])


async def test_client_returns_whisper_word_timestamps():
    body = {"task": "transcribe", "language": "english", "duration": 2.1, "text": "Look! A big tower.",
            "words": [{"word": "Look", "start": 0.0, "end": 0.4}, {"word": "A", "start": 0.6, "end": 0.7}]}
    result = await client_with(body).transcribe(b"mp3", "Look! A big tower.")
    words, _ = narration.align("Look! A big tower.", result["words"], float(result["duration"]))
    assert len(words) == 4 and words[0]["start"] == 0.0


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


def test_photos_must_match_the_search():
    # Real Commons results for "stegosaurus fossil" included this courtyard with an elephant statue.
    hotel = {"title": "File:London Apex Temple Court Hotel-20130714-RM-211919.jpg",
             "imageinfo": [info(Categories="Sculptures of elephants in the United Kingdom|Quality images",
                                ImageDescription="Courtyard of the Apex Temple Court Hotel, London")]}
    assert acceptable(hotel, set())[0]
    assert acceptable(hotel, set(), "stegosaurus fossil") == (False, "nothing in it matches the search")
    trex = {"title": "File:Esqueleto T-Rex, Expo Dinosaurios 2023.jpg", "imageinfo": [info()]}
    assert acceptable(trex, set(), "tyrannosaurus rex skeleton")[0]
    bees = {"title": "File:P1.jpg", "imageinfo": [info(Categories="Apis mellifera|Bees on flowers")]}
    assert acceptable(bees, set(), "bees on a flower")[0]
    assert acceptable(bees, set(), "big photo")[0]


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
    usage = books.usage("acct")
    assert {k: usage[k] for k in ("active", "today", "this_month", "per_day", "per_month", "left_today")} == \
        {"active": 0, "today": 2, "this_month": 2, "per_day": 2, "per_month": 3, "left_today": 0}
    assert usage["left_this_month"] == 1 and usage["page_redos_today"] == 0
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
    storage = MemoryStorage()
    runner = make_runner(books, storage=storage)
    job, row = books.queue("acct", REQUEST)
    assert await runner.run_once() is True
    done = books.job(job.id)
    assert done.status == "done" and done.step == "publish" and done.error == ""
    book = books.book(row.id)
    assert book.status == "published"
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
    await asyncio.wait_for(service.runner.asleep.wait(), timeout=10)
    assert service.runner.state["next_check_s"] is None
    calls: list[str] = []
    run = store._run
    monkeypatch.setattr(store, "_run", lambda fn: (calls.append("db"), run(fn))[1])

    for _ in range(3):  # idle time with the keep-awake pings, and every install checking the catalog
        assert (await c.get("/healthz")).status == 200
        assert (await c.get("/content/manifest.json", headers=APP)).status == 200
        assert (await c.get("/content/library.json", headers=APP)).status == 200
        await asyncio.sleep(0.1)  # any timer (poll_s is 0.05 in these tests) would fire
    assert calls == [] and service.runner.asleep.is_set()


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


async def test_commons_reads_the_whole_photo_even_when_it_arrives_in_pieces():
    # Real downloads arrive in chunks; content.read(n) would hand back only the first piece.
    from aiohttp import web
    from PIL import Image

    out = io.BytesIO()
    Image.effect_noise((1400, 900), 64).convert("RGB").save(out, "JPEG", quality=95)
    photo = out.getvalue()

    async def slow(request):
        resp = web.StreamResponse()
        resp.content_length = len(photo)
        await resp.prepare(request)
        for start in range(0, len(photo), 16 * 1024):
            await resp.write(photo[start:start + 16 * 1024])
            await asyncio.sleep(0.001)
        return resp

    app = web.Application()
    app.router.add_get("/photo.jpg", slow)
    async with TestServer(app) as server:
        commons = Commons(pause=0)
        try:
            assert await commons._get(str(server.make_url("/photo.jpg"))) == photo
            with pytest.raises(PhotoError, match="too big"):
                await commons._get(str(server.make_url("/photo.jpg")), limit=len(photo) // 2)
        finally:
            await commons.close()
    assert len(to_jpeg(photo)) > 0


async def test_a_photo_that_wont_open_is_skipped(store, clock, fake_narrate):
    books = make_books(store, clock)
    job, row = books.queue("acct", REQUEST)

    class OneBroken(FakeCommons):
        async def download(self, page):
            if self.downloads == 0:
                self.downloads += 1
                raise BadPhoto(f"{page['title']}: OSError: image file is truncated")
            return await super().download(page)

    await make_runner(books, storage=MemoryStorage(), commons=OneBroken()).run_once()
    assert books.job(job.id).status == "done"
    first = books.book(row.id).json["pages"][0]["photo"]["commonsTitle"]
    assert first.endswith(" 1.jpg")


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


async def test_account_manifest_lists_only_the_shelf(store, clock, static, fake_narrate):
    manifest, _ = static
    before = json.loads(json.dumps(manifest))
    service, job = await published_service(store, clock, static, fake_narrate)
    assert service.manifest == before  # the public catalog never changes

    made, served = await service.account_manifest("acct")
    book_id = job.book_id
    assert made["order"] == [book_id] and made["format"] == 1
    body = served[f"Books/{book_id}.json"]
    assert made["files"][f"Books/{book_id}.json"] == {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    assert json.loads(body)["generated"] is True and json.loads(body)["level"] == 2
    clips = json.loads(served[f"Narration/{book_id}.narration.json"])["clips"]
    assert len(clips) == 10 and clips[f"{book_id}/p01"]["file"] == f"read-{book_id}-p01"
    photo = made["files"][f"Photos/{book_id}-p01.jpg"]
    assert photo["url"].startswith(f"https://r2.invalid/books/{book_id}/") and photo["size"] > 0
    assert f"Narration/read-{book_id}-p01.m4a" in made["files"]
    assert len(made["files"]) == 22  # the book, its clips' timings, 10 photos, 10 clips
    # File names never clash with the catalog's (the app caches files flat).
    names = [rel.rsplit("/", 1)[-1] for rel in made["files"]]
    assert not set(names) & {rel.rsplit("/", 1)[-1] for rel in manifest["files"]}

    assert (await service.account_manifest("someone-else"))[0]["order"] == []
    await service.set_published(book_id, False)  # the kill switch
    assert (await service.account_manifest("acct"))[0]["order"] == []
    await service.set_published(book_id, True)
    assert await service.delete_book(book_id) == 20
    assert (await service.account_manifest("acct"))[0]["order"] == [] and service.storage.objects == {}


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
        if job["status"] == "done":
            break
        await asyncio.sleep(0.02)
    assert job["status"] == "done", job
    jobs = (await (await c.get("/admin/jobs", headers=ADMIN)).json())["jobs"]
    assert jobs[0]["id"] == job_id
    listed = (await (await c.get("/admin/books?account=kid@example.com", headers=ADMIN)).json())["books"]
    assert [b["id"] for b in listed] == [book_id] and listed[0]["status"] == "published"
    full = await (await c.get(f"/admin/books/{book_id}", headers=ADMIN)).json()
    assert full["book"]["level"] == 2 and len(full["narration"]) == 10

    # The public catalog (and the 1.0 app, which downloads all of it) never sees a made book.
    manifest = await (await c.get("/content/manifest.json")).json()
    assert book_id not in manifest["order"] and manifest == build_manifest()
    assert not [rel for rel in manifest["files"] if book_id in rel]
    assert (await c.get(f"/content/Books/{book_id}.json")).status == 404
    assert (await c.get(f"/content/Photos/{book_id}-p01.jpg", allow_redirects=False)).status == 404
    health = await (await c.get("/healthz")).json()
    assert health["content"] == health["bundled"] == build_manifest()["version"]
    # The account that owns it gets it in its own manifest.
    kid = await _signed_in(accounts, "kid@example.com", "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B")
    made = await (await c.get("/account/content/manifest.json", headers=kid)).json()
    assert made["order"] == [book_id]
    # The 1.0 and 1.1 apps can't label made books (and never ask; this is in case).
    unversioned = {k: v for k, v in kid.items() if not k.startswith("X-App-")}
    for old in (unversioned, {**unversioned, **APP_1_1}):
        assert (await (await c.get("/account/content/manifest.json", headers=old)).json())["order"] == []

    status = await (await c.get("/admin/bookgen", headers=ADMIN)).json()
    assert status["ready"] and status["everyone"] is False and status["made_books_on_shelf"] == 1
    assert status["spent_today_usd"] > 0 and status["limits"]["per_day"] == 3

    resp = await c.post(f"/admin/books/{book_id}/unpublish", headers=ADMIN)
    assert resp.status == 200 and (await resp.json())["status"] == "unpublished"
    assert (await (await c.get("/account/content/manifest.json", headers=kid)).json())["order"] == []
    assert (await c.get(f"/account/content/Books/{book_id}.json", headers=kid)).status == 404
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
    return (await _wait_done(service, job_id)).book_id


async def _wait_done(service, job_id, statuses=("done",)):
    for _ in range(300):
        job = service.books.job(job_id)
        if job.status in statuses or job.status in ("failed", "rejected"):
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(service.books.job(job_id))


async def _signed_in(accounts, email, device):
    """App headers with a fresh session for the account."""
    accounts.clock.t += 120  # past the resend wait for a new code
    code, _ = accounts.start(email)
    token, _, _ = accounts.verify(email, code, (device, "iPad", "iPad13,1"))
    return {**APP, **APP_1_2, "X-Pip-Session": token}


async def _app_account(c, accounts, email, device, client=APP_1_2):
    code, _ = accounts.start(email)
    token, _, _ = accounts.verify(email, code, (device, "iPad", "iPad13,1"))
    return (await (await c.get("/account", headers={**APP, **client, "X-Pip-Session": token})).json())["account"]


async def test_all_books_accounts_only_see_their_own_made_books(server, accounts, clock):
    # The 1.0 app reads all_books: true as "every book in the manifest", which would show one family's made
    # book to every all_books account (App Review's too); the app always gets an explicit shelf instead.
    c, service = server
    a, _ = accounts.create("angilyu@gmail.com", all_books=True)
    b, _ = accounts.create("hfevers@gmail.com", all_books=True)
    theirs = await _make_book_for(c, service, b.email, "Owls at Night")
    mine = await _make_book_for(c, service, a.email, "Tall Towers")
    catalog = list(service.static["order"])
    assert {theirs, mine} <= service.books.published_ids() and not {theirs, mine} & set(catalog)

    shelf = await _app_account(c, accounts, a.email, "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B")
    assert shelf["all_books"] is False
    assert shelf["books"] == catalog + [mine]  # every hand-made book, its own made book, not B's
    assert theirs not in shelf["books"]
    # The 1.0 and 1.1 apps can't label made books, so their shelf is the hand-made catalog only.
    for i, old in enumerate(({}, APP_1_1)):
        clock.t += 120  # the next sign-in code
        device = f"BDFF5D95-6EBF-47BC-AA79-5FE554F0502{i}"
        assert (await _app_account(c, accounts, a.email, device, old))["books"] == catalog
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


async def test_accounts_without_all_books_see_only_their_grants(server, accounts, clock):
    c, service = server
    a, _ = accounts.create("angilyu@gmail.com")
    mine = await _make_book_for(c, service, a.email, "Tall Towers")
    shelf = await _app_account(c, accounts, a.email, "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B")
    assert shelf["all_books"] is False and shelf["books"] == sorted([*accounts.default_books, mine])
    clock.t += 120  # the next sign-in code
    old = await _app_account(c, accounts, a.email, "BDFF5D95-6EBF-47BC-AA79-5FE554F05021", APP_1_1)
    assert old["books"] == sorted(accounts.default_books)


async def test_account_delete_removes_made_books(server, accounts, clock):
    c, service = server
    account, _ = accounts.create("hfevers@gmail.com")
    job = service.queue(account.id, REQUEST)
    service.wake()
    assert (await _wait_done(service, job.id)).status == "done"
    assert service.storage.objects and job.book_id in service.books.published_ids()
    code, _ = accounts.start("hfevers@gmail.com")
    token, _, _ = accounts.verify("hfevers@gmail.com", code, ("BDFF5D95-6EBF-47BC-AA79-5FE554F0502B", "iPad", "iPad13,1"))
    clock.t += 120
    code, _ = accounts.start("hfevers@gmail.com", "delete")
    resp = await c.post("/account/delete", json={"code": code}, headers={**APP, "X-Pip-Session": token})
    assert resp.status == 200, await resp.text()
    assert service.storage.objects == {} and job.book_id not in service.books.published_ids()
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


# -- grown-ups making books in the app -------------------------------------------------------------

MOM_DEVICE = "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B"
DAD_DEVICE = "C0FFEE00-6EBF-47BC-AA79-5FE554F0502B"


def test_tries_cap_and_failed_attempts_dont_count(store, clock):
    books = make_books(store, clock, per_day=3, tries_per_day=3)
    for status in ("rejected", "failed"):
        books.queue("a", REQUEST)
        books.finish(books.claim(), status, error="no")
    usage = books.usage("a")
    assert usage["today"] == 0 and usage["left_today"] == 3 and usage["tries_today"] == 2
    books.queue("a", REQUEST)
    books.finish(books.claim(), "rejected", error="no")
    with pytest.raises(LimitError) as exc:
        books.queue("a", REQUEST)
    assert exc.value.code == "tries_limit" and exc.value.status == 429


def test_friendly_errors():
    assert "didn't count" in friendly_error("rejected", "Books can't be about toy brands.")
    assert friendly_error("rejected", "Too scary.").startswith("This book couldn't be made: Too scary.")
    assert "free photos" in friendly_error("failed", "found free photos for only 3 of 10 pages")
    assert friendly_error("failed", "RuntimeError: boom").startswith("Something went wrong")
    assert "boom" not in friendly_error("failed", "RuntimeError: boom")
    assert friendly_error("failed", "cancelled by the owner").startswith("This book was stopped")
    assert friendly_error("done", "") is None and friendly_error("running", "") is None


async def _published_book(store, clock, fake_narrate, client=None, commons=None, account="acct"):
    books = make_books(store, clock)
    storage = MemoryStorage()
    runner = make_runner(books, client=client or FakeOpenAI(), storage=storage, commons=commons or FakeCommons())
    _, row = books.queue(account, REQUEST)
    assert await runner.run_once()
    assert books.book(row.id).status == "published"
    return books, runner, storage, row.id


async def test_page_regenerate_swaps_one_page(store, clock, fake_narrate):
    books, runner, storage, book_id = await _published_book(store, clock, fake_narrate)
    before = books.book(book_id)
    old_keys = {p: a.r2_key for p, a in books.assets(book_id).items()}
    usage = books.usage("acct")

    with pytest.raises(LookupError):
        books.queue_page("someone-else", book_id, "p03")
    with pytest.raises(LookupError):
        books.queue_page("acct", book_id, "p99")
    job = books.queue_page("acct", book_id, "p03")
    assert job.kind == "page" and job.input["page"] == "p03" and job.input["about"] == "The Eiffel Tower"
    with pytest.raises(LimitError) as exc:
        books.queue_page("acct", book_id, "p04")
    assert exc.value.code == "busy"
    assert await runner.run_once()

    done = books.job(job.id)
    assert done.status == "done" and done.cost_usd > 0
    after = books.book(book_id)
    photo_file, clip_name, _ = page_paths(done)
    page = after.json["pages"][2]
    assert page["text"] == page_text(2, "again") and page["photo"]["file"] == photo_file
    assert after.json["pages"][3] == before.json["pages"][3]  # the rest of the book is untouched
    assert after.narration[f"{book_id}/p03"]["file"] == clip_name
    assets = books.assets(book_id)
    assert f"Photos/{photo_file}" in assets and f"Narration/{clip_name}.m4a" in assets
    assert f"Photos/{book_id}-p03.jpg" not in assets and f"Narration/read-{book_id}-p03.m4a" not in assets
    assert len(assets) == 20
    assert old_keys[f"Photos/{book_id}-p03.jpg"] not in storage.objects  # the old page's files are gone
    assert set(storage.objects) == {a.r2_key for a in assets.values()}
    # A page redo doesn't use a book from the quota; it has its own daily count.
    again = books.usage("acct")
    assert again["today"] == usage["today"] and again["page_redos_today"] == 1 and again["page_redos_left"] == 4
    # The account manifest shows the new page only.
    made, _ = account_manifest_of(books, storage)
    assert f"Photos/{photo_file}" in made["files"] and f"Photos/{book_id}-p03.jpg" not in made["files"]


def account_manifest_of(books, storage):
    from little_explorer_agent.bookgen.manifest import account_manifest

    return account_manifest(books.shelf_books("acct"), {"files": {}, "order": []},
                            lambda a: storage.presign(a.r2_key, 60))


async def test_page_redo_limit(store, clock, fake_narrate):
    books, runner, _, book_id = await _published_book(store, clock, fake_narrate)
    books.config = BookgenConfig(**{**books.config.__dict__, "page_redos_per_day": 1})
    books.queue_page("acct", book_id, "p02")
    assert await runner.run_once()
    with pytest.raises(LimitError) as exc:
        books.queue_page("acct", book_id, "p02")
    assert exc.value.code == "page_limit"


async def test_failed_page_redo_keeps_the_book(store, clock, fake_narrate):
    books, runner, storage, book_id = await _published_book(store, clock, fake_narrate)
    before, keys = books.book(book_id), set(storage.objects)
    runner.commons.missing = 1  # no photo for the new page
    job = books.queue_page("acct", book_id, "p05")
    assert await runner.run_once()
    failed = books.job(job.id)
    assert failed.status == "failed" and failed.parent_json()["message"].startswith("We couldn't find enough free photos")
    assert books.book(book_id).json == before.json and books.book(book_id).status == "published"
    assert set(storage.objects) == keys and len(books.assets(book_id)) == 20
    assert books.usage("acct")["page_redos_today"] == 0  # a failed redo doesn't count


async def test_rejected_page_redo_keeps_the_book(store, clock, fake_narrate):
    client = FakeOpenAI()
    books, runner, storage, book_id = await _published_book(store, clock, fake_narrate, client=client)
    before, keys = books.book(book_id), set(storage.objects)
    client.safe = False
    job = books.queue_page("acct", book_id, "p02")
    assert await runner.run_once()
    rejected = books.job(job.id)
    assert rejected.status == "rejected" and "Too scary." in rejected.parent_json()["message"]
    assert books.book(book_id).json == before.json and set(storage.objects) == keys


async def test_parent_gating(server, accounts):
    c, _ = server
    accounts.create("someone@example.com")
    headers = await _signed_in(accounts, "someone@example.com", MOM_DEVICE)
    assert (await c.get("/account/bookgen")).status == 401
    assert await (await c.get("/account/bookgen", headers=headers)).json() == {"can_generate": False}
    me = (await (await c.get("/account", headers=headers)).json())["account"]
    assert me["can_generate"] is False
    resp = await c.post("/account/books", json=REQUEST, headers=headers)
    assert resp.status == 403 and (await resp.json())["error"] == "not_allowed"
    await c.post("/admin/bookgen", json={"everyone": True}, headers=ADMIN)
    assert (await (await c.get("/account", headers=headers)).json())["account"]["can_generate"] is True
    status = await (await c.get("/account/bookgen", headers=headers)).json()
    assert status["can_generate"] and status["ready"] and [lv["level"] for lv in status["levels"]] == [1, 2, 3]
    assert status["usage"]["left_today"] == 3 and status["fields"]["about_max"] == 300 and status["active"] is None


async def test_parent_endpoints_need_a_ready_server(store, accounts, static):
    manifest, files = static
    service = BookgenService(store, static_manifest=manifest, static_files=files, config=BookgenConfig())
    accounts.create("angilyu@gmail.com")
    async with await make_client(store, accounts, service) as c:
        headers = await _signed_in(accounts, "angilyu@gmail.com", MOM_DEVICE)
        resp = await c.post("/account/books", json=REQUEST, headers=headers)
        assert resp.status == 503 and (await resp.json())["error"] == "not_ready"
        made = await (await c.get("/account/content/manifest.json", headers=headers)).json()
        assert made["order"] == [] and made["files"] == {}


async def test_parent_makes_a_book(server, accounts):
    c, service = server
    mom, _ = accounts.create("angilyu@gmail.com")
    dad, _ = accounts.create("hfevers@gmail.com")
    headers = await _signed_in(accounts, mom.email, MOM_DEVICE)
    other = await _signed_in(accounts, dad.email, DAD_DEVICE)

    resp = await c.post("/account/books", json={**REQUEST, "title": ""}, headers=headers)
    assert resp.status == 400 and await resp.json() == {"error": "bad_request", "message": "The book name is required."}
    resp = await c.post("/account/books", json={**REQUEST, "about": "x" * 301}, headers=headers)
    assert (await resp.json())["message"] == "What it's about must be at most 300 characters."
    resp = await c.post("/account/books", json={**REQUEST, "level": 7}, headers=headers)
    assert (await resp.json())["message"] == "Please choose a level."

    resp = await c.post("/account/books", json=REQUEST, headers=headers)
    assert resp.status == 202, await resp.text()
    body = await resp.json()
    job = body["job"]
    assert job["status"] == "queued" and job["progress"] == "Writing…" and job["title"] == "The Tall Tower"
    assert "cost_usd" not in job and "costs" not in job and "error" not in job
    assert body["usage"]["active"] == 1 and body["usage"]["today"] == 1
    resp = await c.post("/account/books", json=REQUEST, headers=headers)
    assert resp.status == 429 and (await resp.json())["error"] == "busy"
    assert (await c.get(f"/account/jobs/{job['id']}", headers=other)).status == 404

    await _wait_done(service, job["id"])
    done = (await (await c.get(f"/account/jobs/{job['id']}", headers=headers)).json())["job"]
    assert done["status"] == "done" and done["progress"] is None and done["message"] is None
    book_id = done["book_id"]
    mine = (await (await c.get("/account/books", headers=headers)).json())["books"]
    assert [b["id"] for b in mine] == [book_id] and mine[0]["status"] == "published" and "account_id" not in mine[0]
    assert (await (await c.get("/account/books", headers=other)).json())["books"] == []
    status = await (await c.get("/account/bookgen", headers=headers)).json()
    assert status["usage"]["left_today"] == 2 and status["jobs"][0]["id"] == job["id"]

    # The account's own manifest has it; another family's doesn't, and can't fetch its files.
    made = await (await c.get("/account/content/manifest.json", headers=headers)).json()
    assert made["order"] == [book_id]
    resp = await c.get(f"/account/content/Books/{book_id}.json", headers=headers)
    assert resp.status == 200 and (await resp.json())["generated"] is True
    resp = await c.get(f"/account/content/Narration/{book_id}.narration.json", headers=headers)
    assert len((await resp.json())["clips"]) == 10
    assert (await c.get(f"/account/content/Books/{book_id}.json", headers=other)).status == 404
    assert (await c.get(f"/account/content/Books/{book_id}.json", headers=APP)).status == 401
    assert (await c.get(f"/account/content/Photos/{book_id}-p01.jpg", headers=headers)).status == 404
    assert (await c.get("/account/content/Books/..%2F..%2Fsecret.json", headers=headers)).status == 404
    assert (await (await c.get("/account/content/manifest.json", headers=other)).json())["order"] == []
    # The public catalog still never lists it.
    assert book_id not in (await (await c.get("/content/manifest.json")).json())["order"]

    # Reading a made book counts for the badges; reading another family's doesn't.
    now = datetime.now(UTC).isoformat(timespec="seconds")

    def view(event_id):
        return {"id": event_id, "kind": "page_view", "at": now, "book": book_id, "page": "p01",
                "page_index": 0, "seconds": 5, "milo": False}

    resp = await c.post("/account/reading", json={"events": [view("evt-mom-0001")]}, headers=headers)
    assert resp.status == 200 and (await resp.json())["stored"] == 1, await resp.text()
    progress = (await (await c.get("/account/reading", headers=headers)).json())["progress"]
    assert progress["pages"] == [f"{book_id}/p01"]
    resp = await c.post("/account/reading", json={"events": [view("evt-dad-0001")]}, headers=other)
    assert (await resp.json())["stored"] == 0

    # Milo's agent reads the made books on the account's shelf (report key only).
    assert (await c.get(f"/made-books?account={mom.id}")).status == 401
    resp = await c.get(f"/made-books?account={mom.id}", headers={"Authorization": "Bearer r"})
    made_books = (await resp.json())["books"]
    assert [b["id"] for b in made_books] == [book_id] and made_books[0]["pages"][0]["about"]
    resp = await c.get(f"/made-books?account={dad.id}", headers={"Authorization": "Bearer r"})
    assert (await resp.json())["books"] == []


async def test_parent_redo_report_and_delete(server, accounts):
    c, service = server
    mom, _ = accounts.create("angilyu@gmail.com")
    dad, _ = accounts.create("hfevers@gmail.com")
    headers = await _signed_in(accounts, mom.email, MOM_DEVICE)
    other = await _signed_in(accounts, dad.email, DAD_DEVICE)
    resp = await c.post("/account/books", json=REQUEST, headers=headers)
    book_id = (await _wait_done(service, (await resp.json())["job"]["id"])).book_id
    first = await (await c.get("/account/content/manifest.json", headers=headers)).json()

    # Make a page again.
    assert (await c.post(f"/account/books/{book_id}/pages/p02/regenerate", headers=other)).status == 404
    assert (await c.post(f"/account/books/{book_id}/pages/p77/regenerate", headers=headers)).status == 404
    resp = await c.post(f"/account/books/{book_id}/pages/p02/regenerate", headers=headers)
    assert resp.status == 202, await resp.text()
    body = await resp.json()
    assert body["job"]["kind"] == "page" and body["job"]["page"] == "p02"
    assert (await _wait_done(service, body["job"]["id"])).status == "done"
    again = await (await c.get("/account/content/manifest.json", headers=headers)).json()
    assert again["version"] != first["version"] and again["order"] == [book_id]
    status = await (await c.get("/account/bookgen", headers=headers)).json()
    assert status["usage"]["today"] == 1 and status["usage"]["page_redos_today"] == 1

    # Report it (anyone whose shelf it's on); the owner sees it.
    assert (await c.post(f"/account/books/{book_id}/report", json={"reason": "meh"}, headers=headers)).status == 400
    assert (await c.post(f"/account/books/{book_id}/report", json={"reason": "wrong"}, headers=other)).status == 404
    resp = await c.post(f"/account/books/{book_id}/report",
                        json={"reason": "wrong", "note": "The tower isn't blue. " * 30, "page": "p02"}, headers=headers)
    assert resp.status == 201
    reports = (await (await c.get("/admin/reports", headers=ADMIN)).json())["reports"]
    assert len(reports) == 1 and reports[0]["book_id"] == book_id and reports[0]["reason"] == "wrong"
    assert len(reports[0]["note"]) == 300 and reports[0]["status"] == "open" and reports[0]["page"] == "p02"
    assert (await c.get("/admin/reports")).status == 401
    assert (await c.post(f"/admin/reports/{reports[0]['id']}/close", headers=ADMIN)).status == 200
    assert (await (await c.get("/admin/reports", headers=ADMIN)).json())["reports"][0]["status"] == "closed"

    # Delete it: only its maker can; it still counts toward the month.
    assert (await c.delete(f"/account/books/{book_id}", headers=other)).status == 404
    resp = await c.delete(f"/account/books/{book_id}", headers=headers)
    assert resp.status == 200 and service.storage.objects == {}
    assert (await (await c.get("/account/content/manifest.json", headers=headers)).json())["order"] == []
    assert (await (await c.get("/account/books", headers=headers)).json())["books"] == []
    assert (await (await c.get("/account/bookgen", headers=headers)).json())["usage"]["this_month"] == 1
    assert (await c.delete(f"/account/books/{book_id}", headers=headers)).status == 404


async def test_granted_made_book_is_on_another_shelf(server, accounts):
    c, service = server
    mom, _ = accounts.create("angilyu@gmail.com")
    dad, _ = accounts.create("hfevers@gmail.com")
    headers = await _signed_in(accounts, mom.email, MOM_DEVICE)
    other = await _signed_in(accounts, dad.email, DAD_DEVICE)
    resp = await c.post("/account/books", json=REQUEST, headers=headers)
    book_id = (await _wait_done(service, (await resp.json())["job"]["id"])).book_id
    resp = await c.post(f"/admin/accounts/{dad.email}/books", json={"grant": [book_id]}, headers=ADMIN)
    assert resp.status == 200, await resp.text()
    made = await (await c.get("/account/content/manifest.json", headers=other)).json()
    assert made["order"] == [book_id]
    resp = await c.post(f"/account/books/{book_id}/report", json={"reason": "photo"}, headers=other)
    assert resp.status == 201
    # Only the maker can remake or delete it.
    assert (await c.post(f"/account/books/{book_id}/pages/p02/regenerate", headers=other)).status == 404
    assert (await c.delete(f"/account/books/{book_id}", headers=other)).status == 404


async def test_account_delete_removes_reports(server, accounts, clock):
    c, service = server
    mom, _ = accounts.create("angilyu@gmail.com")
    headers = await _signed_in(accounts, mom.email, MOM_DEVICE)
    resp = await c.post("/account/books", json=REQUEST, headers=headers)
    book_id = (await _wait_done(service, (await resp.json())["job"]["id"])).book_id
    await c.post(f"/account/books/{book_id}/report", json={"reason": "scary"}, headers=headers)
    clock.t += 120
    code, _ = accounts.start(mom.email, "delete")
    resp = await c.post("/account/delete", json={"code": code}, headers=headers)
    assert resp.status == 200, await resp.text()
    assert (await (await c.get("/admin/reports", headers=ADMIN)).json())["reports"] == []
