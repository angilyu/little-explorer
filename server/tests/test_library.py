"""Library settings: how a grown-up arranged the shelf (hidden books, the order of sections and books),
merged from every device, checked and limited, kept in backups and deleted with the account."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import library as library_module
from little_explorer_agent.accounts import RESEND_AFTER_S, Accounts
from little_explorer_agent.devices import DeviceStore, Limits
from little_explorer_agent.library import (
    MAX_BOOKS_PER_SECTION,
    MAX_HIDDEN,
    MAX_SECTIONS,
    LibraryError,
    LibraryPrefs,
)
from little_explorer_agent.mail import Mailer
from little_explorer_agent.token_server import create_app

IPAD = "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B"
IPHONE = "4EC4F17C-EEEF-4CED-8E46-D637337F48C6"
EMAIL = "family@example.com"
APP = {"Authorization": "Bearer app-key"}


class Clock:
    def __init__(self, t=1_800_043_200.0):
        self.t = t

    def __call__(self):
        return self.t

    @property
    def ms(self):
        return int(self.t * 1000)


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits())


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def accounts(store, clock):
    return Accounts(store, secret="test-secret", clock=clock)


@pytest.fixture
def prefs(store, clock):
    return LibraryPrefs(store, clock=clock)


def sign_in(accounts, clock, email=EMAIL, device=IPAD):
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(email)
    return accounts.verify(email, code, (device, "iPad", "iPad13,1"))


def test_an_account_starts_with_the_default_shelf(prefs):
    assert prefs.get("acct-1") == {"v": 0, "sections": None, "books": {}, "hidden": {}, "updated_at": None}


def test_saves_each_part_and_counts_versions(prefs, clock):
    t = clock.ms - 5000
    saved = prefs.put(
        "acct-1",
        {
            "sections": {"order": ["Tahoe", "Paris", "Tahoe"], "t": t},
            "books": {"Paris": {"order": ["paris-food", "paris"], "t": t}},
            "hidden": {"paris-french": {"on": True, "t": t}},
            "v": 99,
            "something_new": 1,
        },
    )
    assert saved["v"] == 1 and saved["updated_at"]
    assert saved["sections"] == {"order": ["Tahoe", "Paris"], "t": t}
    assert saved["books"] == {"Paris": {"order": ["paris-food", "paris"], "t": t}}
    assert saved["hidden"] == {"paris-french": {"on": True, "t": t}}
    assert prefs.get("acct-1") == saved
    assert prefs.get("acct-2")["v"] == 0

    # Sending the same thing again changes nothing, so the version stays.
    assert prefs.put("acct-1", {"hidden": {"paris-french": {"on": True, "t": t - 1}}})["v"] == 1
    assert prefs.put("acct-1", {})["v"] == 1


def test_merges_each_key_and_keeps_the_later_change(prefs, clock):
    t = clock.ms - 60_000
    prefs.put("acct-1", {"hidden": {"paris": {"on": True, "t": t}, "tokyo": {"on": True, "t": t}}})
    # Another device, offline for a while, sends older changes and one newer one.
    merged = prefs.put(
        "acct-1",
        {
            "sections": {"order": ["Tokyo"], "t": t - 10},
            "hidden": {"paris": {"on": False, "t": t - 10}, "tokyo": {"on": False, "t": t + 10}, "london": {"on": True, "t": t}},
        },
    )
    assert merged["hidden"] == {
        "paris": {"on": True, "t": t},
        "tokyo": {"on": False, "t": t + 10},
        "london": {"on": True, "t": t},
    }
    # Nothing else had set the section order, so the older one still counts.
    assert merged["sections"] == {"order": ["Tokyo"], "t": t - 10}
    # A tie goes to the later write.
    assert prefs.put("acct-1", {"sections": {"order": ["Paris"], "t": t - 10}})["sections"]["order"] == ["Paris"]


def test_back_to_default_is_kept_so_older_changes_cant_return(prefs, clock):
    t = clock.ms - 60_000
    prefs.put("acct-1", {"books": {"Paris": {"order": ["paris-food", "paris"], "t": t}}})
    reset = prefs.put("acct-1", {"books": {"Paris": {"order": [], "t": t + 1}}})
    assert reset["books"]["Paris"] == {"order": [], "t": t + 1}
    stale = prefs.put("acct-1", {"books": {"Paris": {"order": ["paris-food", "paris"], "t": t}}})
    assert stale["books"]["Paris"]["order"] == [] and stale["v"] == reset["v"]


def test_a_device_clock_ahead_counts_as_now(prefs, clock):
    future = clock.ms + 365 * 86_400_000
    saved = prefs.put("acct-1", {"hidden": {"paris": {"on": True, "t": future}}})
    assert saved["hidden"]["paris"]["t"] == clock.ms
    # So a later real change still wins.
    clock.t += 1
    assert prefs.put("acct-1", {"hidden": {"paris": {"on": False, "t": clock.ms}}})["hidden"]["paris"]["on"] is False


@pytest.mark.parametrize(
    "body",
    [
        [],
        "nope",
        {"sections": ["Paris"]},
        {"sections": {"order": "Paris", "t": 1}},
        {"sections": {"order": ["Paris"]}},
        {"sections": {"order": ["Paris"], "t": -1}},
        {"sections": {"order": ["Paris"], "t": True}},
        {"sections": {"order": ["Paris"], "t": 1.5}},
        {"sections": {"order": [""], "t": 1}},
        {"sections": {"order": ["x" * 81], "t": 1}},
        {"sections": {"order": ["Par\nis"], "t": 1}},
        {"sections": {"order": [3], "t": 1}},
        {"books": ["Paris"]},
        {"books": {"Paris": {"order": ["../etc/passwd"], "t": 1}}},
        {"books": {"Paris": {"order": ["Paris Book"], "t": 1}}},
        {"books": {"": {"order": [], "t": 1}}},
        {"hidden": ["paris"]},
        {"hidden": {"paris": True}},
        {"hidden": {"paris": {"on": "yes", "t": 1}}},
        {"hidden": {"PARIS!": {"on": True, "t": 1}}},
    ],
)
def test_rejects_bad_shapes(prefs, body):
    with pytest.raises(LibraryError) as exc:
        prefs.put("acct-1", body)
    assert exc.value.code == "bad_library" and exc.value.status == 400
    assert prefs.get("acct-1")["v"] == 0


def test_size_limits(prefs, clock):
    t = clock.ms
    for body, limit in [
        ({"sections": {"order": [f"S{i}" for i in range(MAX_SECTIONS + 1)], "t": t}}, MAX_SECTIONS),
        ({"books": {f"S{i}": {"order": [], "t": t} for i in range(MAX_SECTIONS + 1)}}, MAX_SECTIONS),
        ({"books": {"Paris": {"order": [f"b{i}" for i in range(MAX_BOOKS_PER_SECTION + 1)], "t": t}}}, MAX_BOOKS_PER_SECTION),
        ({"hidden": {f"b{i}": {"on": True, "t": t} for i in range(MAX_HIDDEN + 1)}}, MAX_HIDDEN),
    ]:
        with pytest.raises(LibraryError) as exc:
            prefs.put("acct-1", body)
        assert exc.value.status == 413 and exc.value.extra == {"max": limit}
    # Exactly at the limit is fine.
    assert len(prefs.put("acct-1", {"hidden": {f"b{i}": {"on": True, "t": t} for i in range(MAX_HIDDEN)}})["hidden"]) == MAX_HIDDEN


def test_merges_past_the_limit_drop_defaults_then_the_oldest(prefs, clock, monkeypatch):
    monkeypatch.setattr(library_module, "MAX_HIDDEN", 3)
    t = clock.ms - 60_000
    prefs.put("acct-1", {"hidden": {"a": {"on": True, "t": t}, "b": {"on": False, "t": t + 5}}})
    prefs.put("acct-1", {"hidden": {"c": {"on": True, "t": t + 1}}})
    merged = prefs.put("acct-1", {"hidden": {"d": {"on": True, "t": t + 2}}})
    # The shown book goes first even though it's the newest.
    assert set(merged["hidden"]) == {"a", "c", "d"}
    merged = prefs.put("acct-1", {"hidden": {"e": {"on": True, "t": t + 3}}})
    assert set(merged["hidden"]) == {"c", "d", "e"}


# --- deleting, backups -----------------------------------------------------------------------------


def test_deleting_the_account_deletes_its_library(store, accounts, clock, prefs):
    token, account, _ = sign_in(accounts, clock)
    prefs.put(account.id, {"hidden": {"paris": {"on": True, "t": clock.ms}}})
    prefs.put("someone-else", {"hidden": {"paris": {"on": True, "t": clock.ms}}})
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(EMAIL, "delete")
    counts = accounts.delete(account.id, EMAIL, code)
    assert counts["account_library_prefs"] == 1
    assert prefs.get(account.id)["v"] == 0 and prefs.get("someone-else")["v"] == 1


def test_backup_and_restore_keep_the_library(store, prefs, clock, tmp_path):
    saved = prefs.put("acct-1", {"sections": {"order": ["Tahoe"], "t": clock.ms}})
    dump = store.backup()
    assert len(dump["tables"]["account_library_prefs"]["rows"]) == 1
    copy = DeviceStore(sqlite_path=tmp_path / "copy.sqlite3", limits=Limits())
    assert copy.restore(dump)["account_library_prefs"] == 1
    assert LibraryPrefs(copy, clock=clock).get("acct-1") == saved
    # A backup from before library settings still restores.
    del dump["tables"]["account_library_prefs"]
    assert "account_library_prefs" not in copy.restore(dump)


# --- endpoint --------------------------------------------------------------------------------------


@pytest.fixture
async def client(store, accounts, prefs):
    app = create_app(
        access_key="app-key", store=store, admin_key="admin-secret", report_key="report-secret",
        accounts=accounts, mailer=Mailer(provider="console"), library=prefs,
    )
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_library_endpoint(client, accounts, clock):
    assert (await client.get("/account/library")).status == 401
    resp = await client.get("/account/library", headers=APP)
    assert resp.status == 401 and (await resp.json())["error"] == "signed_out"
    assert (await client.put("/account/library", json={}, headers=APP)).status == 401

    token, account, _ = sign_in(accounts, clock)
    ipad = {**APP, "X-Pip-Session": token}
    resp = await client.get("/account/library", headers=ipad)
    assert resp.status == 200 and (await resp.json())["library"]["v"] == 0

    body = {"hidden": {"paris": {"on": True, "t": clock.ms}}, "sections": {"order": ["Tahoe", "Paris"], "t": clock.ms}}
    resp = await client.put("/account/library", json=body, headers=ipad)
    library = (await resp.json())["library"]
    assert resp.status == 200 and library["v"] == 1 and library["hidden"]["paris"]["on"] is True

    # Another device signed in to the same account sees it.
    token2, _, _ = sign_in(accounts, clock, device=IPHONE)
    iphone = {**APP, "X-Pip-Session": token2}
    assert (await (await client.get("/account/library", headers=iphone)).json())["library"] == library

    resp = await client.put("/account/library", data="nope", headers=ipad)
    assert resp.status == 400
    resp = await client.put("/account/library", json={"hidden": "nope"}, headers=ipad)
    assert resp.status == 400 and (await resp.json())["error"] == "bad_library"
    hidden = {f"b{i}": {"on": True, "t": clock.ms} for i in range(MAX_HIDDEN + 1)}
    resp = await client.put("/account/library", json={"hidden": hidden}, headers=ipad)
    assert resp.status == 413 and (await resp.json()) == {"error": "too_big", "message": "too many hidden books", "max": MAX_HIDDEN}
    assert (await client.post("/account/library", json={}, headers=ipad)).status == 405

    assert (await client.post("/auth/signout", headers=ipad)).status == 200
    assert (await client.get("/account/library", headers=ipad)).status == 401
