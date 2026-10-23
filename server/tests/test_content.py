from little_explorer_agent.content import load_books, lookup


def test_loads_all_books_from_app_resources():
    books = load_books()
    assert {"paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "paris-french", "london", "london-big-ben", "barcelona", "barcelona-park-guell", "barcelona-sagrada", "barcelona-casa-batllo", "barcelona-tibidabo", "barcelona-montjuic", "tahoe", "tahoe-heavenly", "tahoe-northstar", "hawaii", "hawaii-honolulu", "chengdu", "longyan", "beijing", "beijing-great-wall", "beijing-forbidden-city", "beijing-summer-palace", "beijing-temple-of-heaven", "shanghai", "israel-tel-aviv", "israel-dead-sea", "israel-hebrew", "travel-flight", "travel-packing", "travel-passport", "travel-hotel", "travel-jetlag"} <= set(books)
    for book in books.values():
        assert 10 <= len(book.pages) <= 14
        for page in book.pages:
            assert page.text and page.about


def test_lookup_finds_page_from_app_context():
    book, page = lookup({"book": "london", "page": load_books()["london"].pages[2].id})
    assert book.city == "London"
    assert page is book.pages[2]


def test_lookup_handles_unknown_context():
    assert lookup({}) == (None, None)
    book, page = lookup({"book": "paris", "page": "nope"})
    assert book.id == "paris" and page is None


def test_themed_paris_books_carry_their_theme():
    books = load_books()
    ids = ("paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre",
           "paris-versailles", "paris-seine", "paris-french")
    assert [books[i].theme for i in ids] == [
        "Famous Places", "Yummy Paris", "Fun & Art", "Notre-Dame", "Eiffel Tower", "Jardin d'Acclimatation",
        "Louvre Museum", "Palace of Versailles", "Seine River Cruise", "Speak French"]
    assert books["london"].theme == ""


async def test_refresh_remote_swaps_in_server_books(monkeypatch):
    from aiohttp.test_utils import TestServer

    from little_explorer_agent import content
    from little_explorer_agent.token_server import create_app

    monkeypatch.setattr(content, "_remote_books", None)
    monkeypatch.setattr(content, "_remote_version", None)
    monkeypatch.setattr(content, "_remote_checked", 0.0)
    async with TestServer(create_app(access_key="")) as server:
        base = str(server.make_url("/content"))
        assert await content.refresh_remote(base, max_age=0) is True
        assert await content.refresh_remote(base, max_age=0) is False  # same version
        assert await content.refresh_remote(base) is False  # checked moments ago
    books = content.current_books()
    assert set(books) == {"paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "paris-french", "london", "london-big-ben", "barcelona", "barcelona-park-guell", "barcelona-sagrada", "barcelona-casa-batllo", "barcelona-tibidabo", "barcelona-montjuic", "tahoe", "tahoe-heavenly", "tahoe-northstar", "hawaii", "hawaii-honolulu", "chengdu", "longyan", "beijing", "beijing-great-wall", "beijing-forbidden-city", "beijing-summer-palace", "beijing-temple-of-heaven", "shanghai", "israel-tel-aviv", "israel-dead-sea", "israel-hebrew", "travel-flight", "travel-packing", "travel-passport", "travel-hotel", "travel-jetlag"}
    book, page = content.lookup({"book": "paris", "page": books["paris"].pages[1].id})
    assert page is books["paris"].pages[1]


async def test_refresh_remote_keeps_books_when_server_is_down(monkeypatch):
    from little_explorer_agent import content

    monkeypatch.setattr(content, "_remote_books", None)
    monkeypatch.setattr(content, "_remote_checked", 0.0)
    assert await content.refresh_remote("http://127.0.0.1:9/content", max_age=0, timeout=1) is False
    assert await content.refresh_remote("", max_age=0) is False
    assert content.current_books() == content.load_books()


def test_notre_dame_pages_give_milo_context():
    books = load_books()
    book, page = lookup({"book": "paris-notre-dame", "page": "fire"})
    assert book.theme == "Notre-Dame" and book.city == "Paris"
    assert "firefighters" in page.text and "safe and open again" in page.about
    assert [p.id for p in books["paris-notre-dame"].pages][-1] == "goodbye"


def test_big_ben_book_sits_on_the_london_shelf():
    books = load_books()
    book, page = lookup({"book": "london-big-ben", "page": "penny"})
    assert book.theme == "Big Ben" and book.city == books["london"].city
    assert "pennies" in page.about and "pendulum" in page.text


def test_new_paris_books_give_milo_context_with_fresh_photos():
    import json

    from little_explorer_agent.content import content_dir

    books = load_books()
    new = ("paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine")
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in content_dir().glob("*.json")}

    def photos(bid):
        return {(pg["photo"]["commonsTitle"], pg["photo"]["file"]) for pg in raw[bid]["pages"]}

    old_titles = {t for bid in raw if bid not in new for t, _ in photos(bid)}
    old_files = {f for bid in raw if bid not in new for _, f in photos(bid)}
    for bid in new:
        book = books[bid]
        assert book.city == "Paris" and 10 <= len(book.pages) <= 12
        assert book.pages[-1].id == "goodbye"
        assert not {t for t, _ in photos(bid)} & old_titles, f"{bid} reuses a photo"
        assert not {f for _, f in photos(bid)} & old_files, f"{bid} overwrites a photo file"
    book, page = lookup({"book": "paris-eiffel", "page": "apartment"})
    assert "Edison" in page.about and "apartment" in page.text
    book, page = lookup({"book": "paris-louvre", "page": "mona"})
    assert "small" in page.text and "Leonardo" in page.about
    book, page = lookup({"book": "paris-seine", "page": "bouquin"})
    assert "green boxes" in page.text.lower() and "bouquinistes" in page.about


def test_barcelona_landmark_books_give_milo_context_with_fresh_photos():
    import json

    from little_explorer_agent.content import content_dir

    books = load_books()
    new = ("barcelona-park-guell", "barcelona-sagrada", "barcelona-casa-batllo", "barcelona-tibidabo", "barcelona-montjuic")
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in content_dir().glob("*.json")}

    def photos(bid):
        return {(pg["photo"]["commonsTitle"], pg["photo"]["file"]) for pg in raw[bid]["pages"]}

    old_titles = {t for bid in raw if bid not in new for t, _ in photos(bid)}
    old_files = {f for bid in raw if bid not in new for _, f in photos(bid)}
    for bid in new:
        book = books[bid]
        assert book.city == "Barcelona" and 12 <= len(book.pages) <= 14
        assert book.pages[-1].id == "goodbye"
        assert all("Ask Francis" in pg.about for pg in book.pages), f"{bid} needs a question for Francis on every page"
        assert not {t for t, _ in photos(bid)} & old_titles, f"{bid} reuses a photo"
        assert not {f for _, f in photos(bid)} & old_files, f"{bid} overwrites a photo file"
    book, page = lookup({"book": "barcelona-park-guell", "page": "dragon"})
    assert "dragon" in page.text and "salamander" in page.about
    book, page = lookup({"book": "barcelona-sagrada", "page": "turtles"})
    assert "turtle" in page.text and "tortoise" in page.about
    book, page = lookup({"book": "barcelona-tibidabo", "page": "plane"})
    assert "1928" in page.text and "Madrid" in page.about
    book, page = lookup({"book": "barcelona-montjuic", "page": "fountain"})
    assert "Magic Fountain" in page.text and "1929" in page.about


def test_french_book_teaches_a_phrase_per_page_with_fresh_photos():
    import json
    import re

    from little_explorer_agent.content import content_dir

    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in content_dir().glob("*.json")}
    french = raw.pop("paris-french")
    titles = {pg["photo"]["commonsTitle"] for pg in french["pages"]}
    files = {pg["photo"]["file"] for pg in french["pages"]}
    assert not titles & {pg["photo"]["commonsTitle"] for b in raw.values() for pg in b["pages"]}
    assert not files & {pg["photo"]["file"] for b in raw.values() for pg in b["pages"]}
    assert french["narration"]["phraseByPhrase"] is True

    book = load_books()["paris-french"]
    assert book.city == "Paris" and len(book.pages) == 12 and book.pages[-1].id == "goodbye"
    assert book.pages[2].text == "Merci means thank you. Say it a lot!"
    _, page = lookup({"book": "paris-french", "page": "croissant"})
    assert "Un croissant" in page.text and "Pretend I'm the baker" in page.about


def test_visible_text_has_no_pronunciation_hints():
    # The read-aloud voice says each word, so pages don't spell out sounds like "Honu (HO-noo)".
    import json
    import re

    from little_explorer_agent.content import content_dir

    for path in content_dir().glob("*.json"):
        book = json.loads(path.read_text(encoding="utf-8"))
        for label in (book["city"], book.get("theme", ""), book.get("tagline", "")):
            assert not re.search(r"[()]", label), f"{book['id']}: {label!r}"
        for page in book["pages"]:
            assert not re.search(r"[()]", page["text"]), f"{book['id']}/{page['id']}: {page['text']!r}"


def test_narration_reads_french_book_phrase_by_phrase():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "generate_narration.py"
    spec = importlib.util.spec_from_file_location("generate_narration", path)
    narration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(narration)

    text = "Ask the baker: Un croissant, s'il vous plaît!"
    assert [spoken for spoken, _ in narration.phrases(text)] == ["Ask the baker", "Un croissant", "s'il vous plaît!"]
    assert sum(len(words) for _, words in narration.phrases(text)) == 8
    # A Hebrew word on its own line is its own phrase, and its letters are words (spoken and highlighted).
    assert [spoken for spoken, _ in narration.phrases("שלום\nShalom means hello!")] == ["שלום", "Shalom means hello!"]
    # Books without their own narration note keep the shared style, so their clips aren't regenerated.
    assert narration.style_hash() == narration.style_hash("", False) != narration.style_hash("note", True)


def test_tahoe_and_hawaii_are_new_destinations_with_fresh_photos():
    import json

    from little_explorer_agent.content import content_dir

    books = load_books()
    new = {"tahoe": "Lake Tahoe", "tahoe-heavenly": "Lake Tahoe", "tahoe-northstar": "Lake Tahoe",
           "hawaii": "Hawaii", "hawaii-honolulu": "Hawaii"}
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in content_dir().glob("*.json")}
    old_titles = {pg["photo"]["commonsTitle"] for bid, b in raw.items() if bid not in new for pg in b["pages"]}
    for bid, city in new.items():
        book = books[bid]
        assert book.city == city and book.pages[-1].id == "goodbye"
        assert not {pg["photo"]["commonsTitle"] for pg in raw[bid]["pages"]} & old_titles, f"{bid} reuses a photo"
        assert all(pg["photo"]["verified"] for pg in raw[bid]["pages"])
    assert [books[b].theme for b in new] == ["", "Heavenly", "Northstar", "", "Honolulu"]
    _, page = lookup({"book": "tahoe-heavenly", "page": "two-states"})
    assert "California into Nevada" in page.text
    _, page = lookup({"book": "hawaii", "page": "honu"})
    assert page.text.startswith("Honu means sea turtle") and "touch" in page.about
    assert raw["hawaii"]["narration"]["phraseByPhrase"] is True


def test_chengdu_and_longyan_are_new_china_destinations_with_fresh_photos():
    import json

    from little_explorer_agent.buddy import page_note
    from little_explorer_agent.content import content_dir

    books = load_books()
    new = {"chengdu": ("Chengdu", "Sichuan, China"), "longyan": ("Longyan", "Fujian, China")}
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in content_dir().glob("*.json")}
    old_titles = {pg["photo"]["commonsTitle"] for bid, b in raw.items() if bid not in new for pg in b["pages"]}
    for bid, (city, country) in new.items():
        book = books[bid]
        assert (book.city, book.country, book.theme) == (city, country, "")
        assert book.pages[0].id == "welcome" and book.pages[-1].id == "goodbye"
        assert book.pages[0].text.startswith("Ni hao!") and book.pages[-1].text.startswith(f"Zaijian, {city}!")
        assert not {pg["photo"]["commonsTitle"] for pg in raw[bid]["pages"]} & old_titles, f"{bid} reuses a photo"
        assert all(pg["photo"]["verified"] for pg in raw[bid]["pages"])
        assert raw[bid]["flag"] == "🇨🇳" and raw[bid]["flagImage"] == "chengdu-flag.png"
        # Pronunciation help lives in the hidden notes and the narration note, never in the page text.
        assert city in raw[bid]["narration"]["instructions"]
        for page in book.pages:
            assert not any("\u4e00" <= ch <= "\u9fff" for ch in page.text), f"{bid}/{page.id} has Chinese characters"
        note = page_note({"book": bid, "page": book.pages[1].id, "city": city})
        assert f"{city}, {country}" in note[1]
    _, page = lookup({"book": "chengdu", "page": "peppers"})
    assert "tingle" in page.text and "not chili peppers" in page.about
    _, page = lookup({"book": "longyan", "page": "chengqi"})
    assert "four rings" in page.text and "1709" in page.about


def test_beijing_and_shanghai_shelves_are_new_china_books_with_fresh_photos():
    import json

    from little_explorer_agent.buddy import page_note
    from little_explorer_agent.content import content_dir

    books = load_books()
    new = {
        "beijing": ("Beijing", ""),
        "beijing-great-wall": ("Beijing", "Great Wall"),
        "beijing-forbidden-city": ("Beijing", "Forbidden City"),
        "beijing-summer-palace": ("Beijing", "Summer Palace"),
        "beijing-temple-of-heaven": ("Beijing", "Temple of Heaven"),
        "shanghai": ("Shanghai", ""),
    }
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in content_dir().glob("*.json")}
    old_titles = {pg["photo"]["commonsTitle"] for bid, b in raw.items() if bid not in new for pg in b["pages"]}
    seen: set[str] = set()
    for bid, (city, theme) in new.items():
        book = books[bid]
        assert (book.city, book.country, book.theme) == (city, "China", theme)
        assert book.pages[0].id == "welcome" and book.pages[-1].id == "goodbye"
        assert book.pages[0].text.startswith("Ni hao!") and book.pages[-1].text.startswith(f"Zaijian, {city}!")
        titles = {pg["photo"]["commonsTitle"] for pg in raw[bid]["pages"]}
        assert not titles & old_titles and not titles & seen, f"{bid} reuses a photo"
        seen |= titles
        assert all(pg["photo"]["verified"] for pg in raw[bid]["pages"])
        assert raw[bid]["flag"] == "🇨🇳" and raw[bid]["flagImage"] == "chengdu-flag.png"
        assert city in raw[bid]["narration"]["instructions"]
        for page in book.pages:
            assert not any("\u4e00" <= ch <= "\u9fff" for ch in page.text), f"{bid}/{page.id} has Chinese characters"
            assert "Ask Francis" in page.about
        note = page_note({"book": bid, "page": book.pages[1].id, "city": city})
        assert f"{city}, China" in note[1]
    _, page = lookup({"book": "beijing-great-wall", "page": "space"})
    assert "not with just eyes" in page.text and "Yang Liwei" in page.about
    _, page = lookup({"book": "beijing-great-wall", "page": "dynasties"})
    assert "Gansu" in page.about
    _, page = lookup({"book": "shanghai", "page": "xiaolongbao"})
    assert "soup" in page.text and "bite a tiny hole" in page.about


def test_israel_shelf_has_tel_aviv_dead_sea_and_hebrew_words_with_fresh_photos():
    import json

    from little_explorer_agent.buddy import page_note
    from little_explorer_agent.content import content_dir

    books = load_books()
    new = {"israel-tel-aviv": "Tel Aviv", "israel-dead-sea": "Dead Sea", "israel-hebrew": "Hebrew Words"}
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in content_dir().glob("*.json")}
    old_titles = {pg["photo"]["commonsTitle"] for bid, b in raw.items() if bid not in new for pg in b["pages"]}
    seen: set[str] = set()
    for bid, theme in new.items():
        book = books[bid]
        assert (book.city, book.country, book.theme, book.kind) == ("Israel", "Israel", theme, "")
        assert book.pages[0].id == "welcome" and book.pages[-1].id == "goodbye"
        titles = {pg["photo"]["commonsTitle"] for pg in raw[bid]["pages"]}
        assert not titles & old_titles and not titles & seen, f"{bid} reuses a photo"
        seen |= titles
        assert all(pg["photo"]["verified"] for pg in raw[bid]["pages"])
        assert raw[bid]["flag"] == "🇮🇱" and raw[bid]["flagImage"] == "israel-flag.png"
        assert (content_dir().parent / "Photos" / "israel-flag.png").exists()
        for page in book.pages:
            assert "Ask Francis" in page.about
        note = page_note({"book": bid, "page": book.pages[1].id})
        assert "Israel" in note[1] and theme in note[1]

    # Hebrew Words: each page shows the Hebrew word on its own line above an English sentence he can read,
    # and is read phrase by phrase so the Hebrew line is spoken (in Hebrew) on its own.
    hebrew = books["israel-hebrew"]
    assert raw["israel-hebrew"]["narration"]["phraseByPhrase"] is True
    for page in hebrew.pages:
        script, english = page.text.split("\n")
        assert script and all("\u05d0" <= ch <= "\u05ea" or ch == " " for ch in script), page.id
        assert not any("\u0591" <= ch <= "\u05c7" for ch in page.text), f"{page.id} has nikud"
        assert english.isascii() and len(english.split()) >= 5, page.id
    _, page = lookup({"book": "israel-hebrew", "page": "shalom"})
    assert page.text.startswith("שלום\nShalom means hello") and "shah-LOHM" in page.about
    _, page = lookup({"book": "israel-dead-sea", "page": "float"})
    assert "float" in page.text and "face out of the water" in page.about


def test_travel_books_are_a_general_shelf_not_a_city():
    import json

    from little_explorer_agent.buddy import page_note
    from little_explorer_agent.content import content_dir

    books = load_books()
    travel = ["travel-flight", "travel-packing", "travel-passport", "travel-hotel", "travel-jetlag"]
    raw = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in content_dir().glob("*.json")}
    old_titles = {pg["photo"]["commonsTitle"] for bid, b in raw.items() if bid not in travel for pg in b["pages"]}
    assert [b for b, book in books.items() if book.is_travel] == sorted(travel)
    assert [books[b].theme for b in travel] == ["Take a Flight", "Pack Your Bag", "Passport", "Hotel Stay", "Jet Lag"]
    for bid in travel:
        book = books[bid]
        assert book.city == "Travel" and book.kind == "travel" and book.pages[-1].id == "goodbye"
        assert not {pg["photo"]["commonsTitle"] for pg in raw[bid]["pages"]} & old_titles, f"{bid} reuses a photo"
        assert all(pg["photo"]["verified"] for pg in raw[bid]["pages"])
        assert raw[bid]["flagImage"] == "travel-flag.png"
    jetlag = " ".join(p.text + " " + p.about for p in books["travel-jetlag"].pages).lower()
    assert "melatonin" not in jetlag and "medicine" not in jetlag.replace("no medicine", "")
    note = page_note({"book": "travel-flight", "page": books["travel-flight"].pages[1].id, "city": "Travel"})
    assert note[1] == '- Book: "Take a Flight", a general travel book for kids (not about one city)'
    assert not books["paris"].is_travel
    assert page_note({"book": "paris", "page": "welcome"})[1].startswith("- Book: Paris, France")


async def test_refresh_made_adds_the_familys_made_books(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from little_explorer_agent import content

    made = {"id": "made-abc123", "city": "Paris", "country": "France", "level": 1, "generated": True,
            "pages": [{"id": "p01", "text": "Look! A big tower.", "about": "The Eiffel Tower is 330 m tall."}]}
    seen = []

    async def made_books(request):
        seen.append((request.query.get("account"), request.headers.get("Authorization")))
        return web.json_response({"books": [made]})

    app = web.Application()
    app.router.add_get("/made-books", made_books)
    monkeypatch.setattr(content, "_made_books", {})
    async with TestServer(app) as server:
        target = (str(server.make_url("/made-books")), "r")
        assert await content.refresh_made("", target) == 0
        assert await content.refresh_made("acct-1", None) == 0
        assert await content.refresh_made("acct-1", target) == 1
    assert seen == [("acct-1", "Bearer r")]
    book, page = content.lookup({"book": "made-abc123", "page": "p01"})
    assert book.level == 1 and page.about == "The Eiffel Tower is 330 m tall."
    assert content.lookup({"book": "paris"})[0].city == "Paris"  # the catalog is still there
    # A server that's down leaves Milo with what it had.
    assert await content.refresh_made("acct-1", ("http://127.0.0.1:9/made-books", "r"), timeout=1) == 0
    assert "made-abc123" in content.current_books()
