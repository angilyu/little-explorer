from little_explorer_agent.content import load_books, lookup


def test_loads_all_books_from_app_resources():
    books = load_books()
    assert {"paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "london", "london-big-ben", "barcelona"} <= set(books)
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
           "paris-versailles", "paris-seine")
    assert [books[i].theme for i in ids] == [
        "Famous Places", "Yummy Paris", "Fun & Art", "Notre-Dame", "Eiffel Tower", "Jardin d'Acclimatation",
        "Louvre Museum", "Palace of Versailles", "Seine River Cruise"]
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
    assert set(books) == {"paris", "paris-food", "paris-fun", "paris-notre-dame", "paris-eiffel", "paris-jardin", "paris-louvre", "paris-versailles", "paris-seine", "london", "london-big-ben", "barcelona"}
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
