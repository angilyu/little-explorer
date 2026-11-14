"""The shelf the server lays out for app 1.2.6 and later (``GET``/``PUT /account/shelf``, see :mod:`.shelf`)."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent.accounts import RESEND_AFTER_S, Accounts
from little_explorer_agent.bookgen.config import BookgenConfig
from little_explorer_agent.bookgen.service import BookgenService
from little_explorer_agent.bookgen.storage import MemoryStorage
from little_explorer_agent.bookgen.store import BookRow
from little_explorer_agent.catalog import build_manifest, content_files, resources_dir
from little_explorer_agent.devices import DeviceStore
from little_explorer_agent.mail import Mailer
from little_explorer_agent.shelf import ShelfBook, layout, section_key
from little_explorer_agent.token_server import create_app

APP = {"Authorization": "Bearer app-key"}
APP_1_2_6 = {"X-App-Version": "1.2.6", "X-App-Build": "70", "X-App-Channel": "testflight"}
APP_1_2 = {"X-App-Version": "1.2", "X-App-Build": "60", "X-App-Channel": "testflight"}
APP_1_1 = {"X-App-Version": "1.1", "X-App-Build": "50", "X-App-Channel": "appstore"}
IPAD = "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B"

# Catalog order: Paris (3), London, Lake Tahoe (2), Travel.
CATALOG = [
    ShelfBook("paris", "Paris", "🇫🇷"), ShelfBook("paris-food", "Paris", "🇫🇷"), ShelfBook("paris-fun", "Paris", "🇫🇷"),
    ShelfBook("london", "London", "🇬🇧"),
    ShelfBook("tahoe", "Lake Tahoe", "🇺🇸"), ShelfBook("tahoe-heavenly", "Lake Tahoe", "🇺🇸"),
    ShelfBook("travel-flight", "Travel", "✈️"),
]
# The account from the bug report: made books in "Matt’s" (curly apostrophe), Paris and Hawaii.
MADE = [
    ShelfBook("dinosaur-2bb188", "Matt\u2019s"), ShelfBook("what-should-i-do-at-paris-d1a210", "Paris"),
    ShelfBook("honolulu-046d4f", "Hawaii"), ShelfBook("honolulu-6e37b6", "Hawaii"),
]


def names(shelf):
    return [s["name"] for s in shelf["sections"]]


def ids(shelf, name):
    return [b["id"] for s in shelf["sections"] if s["name"] == name for b in s["books"]]


def order(items, t=1):
    return {"order": items, "t": t}


# -- the layout ------------------------------------------------------------------------------------


def test_section_keys():
    assert section_key("Matt\u2019s") == section_key("matt's") == section_key(" Matt\u02bcs ") == "matt's"
    assert section_key("LAKE  tahoe") == "lake tahoe"
    assert section_key("\uff30aris") == "paris"  # full-width letters (NFKC)
    assert section_key("Matts") != section_key("Matt's")


def test_made_book_sections_come_after_the_catalog():
    shelf = layout(CATALOG, MADE, [], {})
    assert shelf["format"] == 1
    assert names(shelf) == ["Paris", "London", "Lake Tahoe", "Travel", "Matt\u2019s", "Hawaii"]
    assert ids(shelf, "Paris")[-1] == "what-should-i-do-at-paris-d1a210"
    assert ids(shelf, "Hawaii") == ["honolulu-046d4f", "honolulu-6e37b6"]
    matts = shelf["sections"][4]
    assert matts["key"] == "matt's" and matts["flag_book"] == "dinosaur-2bb188" and matts["flag"] == ""
    assert shelf["sections"][0]["flag"] == "🇫🇷" and shelf["sections"][0]["flag_book"] == "paris"
    assert all(b["hidden"] is False for s in shelf["sections"] for b in s["books"])


def test_a_made_book_named_like_a_catalog_city_joins_it():
    made = [ShelfBook("mine", " paris "), ShelfBook("lake", "LAKE  tahoe"), ShelfBook("dup", "Paris"),
            ShelfBook("paris", "Somewhere")]  # an id already on the shelf isn't listed twice
    shelf = layout(CATALOG, made, [], {})
    assert names(shelf) == ["Paris", "London", "Lake Tahoe", "Travel"]
    assert ids(shelf, "Paris") == ["paris", "paris-food", "paris-fun", "mine", "dup"]
    assert ids(shelf, "Lake Tahoe") == ["tahoe", "tahoe-heavenly", "lake"]


def test_curly_and_straight_apostrophes_are_one_section():
    made = [ShelfBook("dino", "Matt\u2019s"), ShelfBook("rex", "matt's")]
    shelf = layout(CATALOG, made, [], {
        "sections": order(["Matt's", "Paris"]),
        "books": {"MATT\u2019S": order(["rex", "dino"], 1), "Matt's": order(["dino", "rex"], 2)},
    })
    assert names(shelf)[:2] == ["Matt\u2019s", "Paris"]
    # Of two spellings' book orders, the later one counts.
    assert ids(shelf, "Matt\u2019s") == ["dino", "rex"]


def test_saved_orders_from_before_new_sections_keep_them_where_the_shelf_has_them():
    library = {"sections": order(["Lake Tahoe", "Atlantis", "Paris"]),
               "books": {"Paris": order(["paris-fun", "gone", "paris"]), "Atlantis": order(["x"])}}
    shelf = layout(CATALOG, MADE, [], library)
    assert names(shelf) == ["Lake Tahoe", "Paris", "London", "Travel", "Matt\u2019s", "Hawaii"]
    assert ids(shelf, "Paris") == ["paris-fun", "paris", "paris-food", "what-should-i-do-at-paris-d1a210"]
    # Reordering the new sections and their books.
    library["sections"] = order(["Matt\u2019s", "Hawaii", "Paris"], 2)
    library["books"]["Hawaii"] = order(["honolulu-6e37b6", "honolulu-046d4f"], 2)
    shelf = layout(CATALOG, MADE, [], library)
    assert names(shelf) == ["Matt\u2019s", "Hawaii", "Paris", "London", "Lake Tahoe", "Travel"]
    assert ids(shelf, "Hawaii") == ["honolulu-6e37b6", "honolulu-046d4f"]


def test_hidden_books_stay_listed_with_their_flag():
    library = {"hidden": {"dinosaur-2bb188": {"on": True, "t": 1}, "london": {"on": False, "t": 2},
                          "gone": {"on": True, "t": 1}}}
    shelf = layout(CATALOG, MADE, [], library)
    flags = {b["id"]: b["hidden"] for s in shelf["sections"] for b in s["books"]}
    assert flags["dinosaur-2bb188"] is True and flags["london"] is False and "gone" not in flags
    assert "Matt\u2019s" in names(shelf)


def test_books_being_made_go_in_their_section():
    making = [ShelfBook("making-j1", "hawaii", making=True), ShelfBook("making-j2", "Our Trips", making=True)]
    shelf = layout(CATALOG, MADE, making, {})
    assert ids(shelf, "Hawaii")[-1] == "making-j1"
    trips = shelf["sections"][-1]
    assert trips["name"] == "Our Trips" and trips["flag_book"] is None
    assert trips["books"] == [{"id": "making-j2", "hidden": False, "making": True}]


# -- the endpoint ----------------------------------------------------------------------------------


class Clock:
    def __init__(self, t=1_800_043_200.0):
        self.t = t

    def __call__(self):
        return self.t


def made_row(book_id, city, *, account="acct", openverse=False):
    photo = {"file": f"{book_id}-p01.jpg", **({"openverseId": "ov-1"} if openverse else {})}
    data = {"id": book_id, "city": city, "flag": "", "pages": [{"id": "p01", "text": "Hi.", "photo": photo}]}
    return BookRow(book_id, account, "job", "Title", city, 2, "published", data, {}, "t", "t", "t")


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "shelf.sqlite3")


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def accounts(store, clock):
    return Accounts(store, secret="test-secret", clock=clock)


@pytest.fixture
def service(store):
    root = resources_dir()
    return BookgenService(store, static_manifest=build_manifest(root), static_files=content_files(root),
                          config=BookgenConfig(), storage=MemoryStorage())


@pytest.fixture
async def client(store, accounts, service):
    app = create_app(access_key="app-key", store=store, admin_key="admin-secret", report_key="r",
                     accounts=accounts, mailer=Mailer(provider="console"), bookgen=service)
    async with TestClient(TestServer(app)) as c:
        yield c


def sign_in(accounts, clock, email="family@example.com"):
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(email)
    token, account, _ = accounts.verify(email, code, (IPAD, "iPad", "iPad13,1"))
    return token, account


def with_made(service, monkeypatch, rows):
    monkeypatch.setattr(service.books, "shelf_books", lambda account_id, book_id=None: [
        (r, {}) for r in rows if r.account_id == account_id and (book_id is None or r.id == book_id)])


async def test_shelf_endpoint_lays_out_the_accounts_books(client, accounts, clock, service, monkeypatch):
    token, account = sign_in(accounts, clock)
    rows = [made_row("dinosaur-2bb188", "Matt\u2019s", account=account.id),
            made_row("what-should-i-do-at-paris-d1a210", "paris", account=account.id),
            made_row("honolulu-046d4f", "Hawaii", account=account.id, openverse=True)]
    with_made(service, monkeypatch, rows)
    accounts.set_books(account.id, grant=[r.id for r in rows])
    headers = {**APP, **APP_1_2_6, "X-Pip-Session": token}

    assert (await client.get("/account/shelf", headers=APP)).status == 401
    resp = await client.get("/account/shelf", headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert body["library"]["v"] == 0
    shelf = body["shelf"]
    # A new account's default books, in catalog order, then its made books.
    assert names(shelf) == ["Paris", "London", "Barcelona", "Beijing", "Shanghai", "Matt\u2019s", "Hawaii"]
    assert ids(shelf, "Paris")[-1] == "what-should-i-do-at-paris-d1a210"
    assert shelf["sections"][0]["flag"] and shelf["sections"][0]["flag_book"] == "paris"

    # An arrangement change answers with the new layout (and the library, as PUT /account/library does).
    t = int(clock.t * 1000) - 1000
    change = {"sections": order(["Hawaii", "Matt\u2019s"], t), "hidden": {"dinosaur-2bb188": {"on": True, "t": t}}}
    resp = await client.put("/account/shelf", json=change, headers=headers)
    assert resp.status == 200
    body = await resp.json()
    assert body["library"]["v"] == 1 and body["library"]["sections"]["order"] == ["Hawaii", "Matt\u2019s"]
    assert names(body["shelf"])[:3] == ["Hawaii", "Matt\u2019s", "Paris"]
    assert body["shelf"]["sections"][1]["books"] == [{"id": "dinosaur-2bb188", "hidden": True}]
    # The same arrangement for older apps' endpoint.
    library = await (await client.get("/account/library", headers=headers)).json()
    assert library["library"] == body["library"]
    assert (await client.put("/account/shelf", json={"sections": "nope"}, headers=headers)).status == 400

    # Version gates, as in the account's content manifest: Openverse books from 1.2.5, made books from 1.2.
    old = await (await client.get("/account/shelf", headers={**headers, **APP_1_2})).json()
    assert "Hawaii" not in names(old["shelf"]) and "Matt\u2019s" in names(old["shelf"])
    older = await (await client.get("/account/shelf", headers={**headers, **APP_1_1})).json()
    assert names(older["shelf"]) == ["Paris", "London", "Barcelona", "Beijing", "Shanghai"]


async def test_books_being_made_show_in_the_layout(client, accounts, clock, service, monkeypatch):
    token, account = sign_in(accounts, clock)
    accounts.set_books(account.id, all_books=True)
    with_made(service, monkeypatch, [])
    job, _ = service.books.queue(account.id, {"title": "Hula", "section": "our trips", "level": 1, "about": ""})
    headers = {**APP, **APP_1_2_6, "X-Pip-Session": token}
    shelf = (await (await client.get("/account/shelf", headers=headers)).json())["shelf"]
    assert names(shelf)[-1] == "our trips"
    assert shelf["sections"][-1]["books"] == [{"id": f"making-{job.id}", "hidden": False, "making": True}]
    # Every catalog book of an all-books account.
    assert sum(len(s["books"]) for s in shelf["sections"]) == len(service.static["order"]) + 1
