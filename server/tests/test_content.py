from little_explorer_agent.content import load_books, lookup


def test_loads_all_books_from_app_resources():
    books = load_books()
    assert {"paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "paris-french", "london", "london-big-ben", "barcelona", "tahoe", "tahoe-heavenly", "tahoe-northstar", "hawaii", "hawaii-honolulu"} <= set(books)
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
    assert set(books) == {"paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "paris-french", "london", "london-big-ben", "barcelona", "tahoe", "tahoe-heavenly", "tahoe-northstar", "hawaii", "hawaii-honolulu"}
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
    for page in book.pages[1:]:
        assert re.search(r"\([^)]*[A-Z]{2,}[^)]*\)", page.text), f"{page.id} needs a sound-it-out hint"
    _, page = lookup({"book": "paris-french", "page": "croissant"})
    assert "Un croissant" in page.text and "Pretend I'm the baker" in page.about


def test_narration_reads_french_phrase_and_hint_as_separate_takes():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "generate_narration.py"
    spec = importlib.util.spec_from_file_location("generate_narration", path)
    narration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(narration)

    text = "Ask the baker: Un croissant (kwah-SAHN), s'il vous plaît!"
    assert [spoken for spoken, _ in narration.phrases(text)] == [
        "Ask the baker", "Un croissant", "kwah-SAHN", "s'il vous plaît!"]
    assert sum(len(words) for _, words in narration.phrases(text)) == 10
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
    assert "Honu (HO-noo)" in page.text and "touch" in page.about
    assert raw["hawaii"]["narration"]["phraseByPhrase"] is True
