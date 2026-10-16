from little_explorer_agent.content import load_books, lookup


def test_loads_all_books_from_app_resources():
    books = load_books()
    assert {"paris", "paris-food", "paris-fun", "london", "barcelona"} <= set(books)
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
    assert [books[i].theme for i in ("paris", "paris-food", "paris-fun")] == ["Famous Places", "Yummy Paris", "Fun & Art"]
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
    assert set(books) == {"paris", "paris-food", "paris-fun", "london", "barcelona"}
    book, page = content.lookup({"book": "paris", "page": books["paris"].pages[1].id})
    assert page is books["paris"].pages[1]


async def test_refresh_remote_keeps_books_when_server_is_down(monkeypatch):
    from little_explorer_agent import content

    monkeypatch.setattr(content, "_remote_books", None)
    monkeypatch.setattr(content, "_remote_checked", 0.0)
    assert await content.refresh_remote("http://127.0.0.1:9/content", max_age=0, timeout=1) is False
    assert await content.refresh_remote("", max_age=0) is False
    assert content.current_books() == content.load_books()
