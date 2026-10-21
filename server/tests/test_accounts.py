"""Accounts: emailed sign-in codes, sessions, linking devices (and claiming their stars), shelves,
hearts, deleting an account, and the owner's tools and backups."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent.accounts import (
    CODE_TRIES,
    DEFAULT_BOOKS,
    PER_EMAIL_PER_HOUR,
    RESEND_AFTER_S,
    Accounts,
    AuthError,
    normalize_email,
)
from little_explorer_agent.coins import CoinLedger
from little_explorer_agent.devices import DeviceStore, Limits, Scope
from little_explorer_agent.mail import Mailer, compose
from little_explorer_agent.token_server import create_app

IPAD = "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B"
IPHONE = "4EC4F17C-EEEF-4CED-8E46-D637337F48C6"
OTHER = "0000AAAA-1111-4222-8333-444455556666"
EMAIL = "family@example.com"
APP = {"Authorization": "Bearer app-key"}
ADMIN = {"Authorization": "Bearer admin-secret"}
REPORT = {"Authorization": "Bearer report-secret"}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits())


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def accounts(store, clock):
    return Accounts(store, secret="test-secret", clock=clock)


def give_stars(store, device, count, tag="a", book="paris"):
    for i in range(count):
        assert store.record_star(
            {"id": f"star-{tag}-{device[:8]}-{i:04d}", "device_id": device, "book": book, "page": f"p{i}", "stars": 1}
        )


def sign_in(accounts, clock, email=EMAIL, device=IPAD):
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(email)
    return accounts.verify(email, code, (device, "iPad", "iPad13,1"))


# --- emails and codes ------------------------------------------------------------------------------


def test_normalize_email():
    assert normalize_email("  Wen@Example.COM ") == "wen@example.com"
    for bad in ("", "nope", "a@b", "a b@c.com", "@c.com", "a@.com", None, 7, "a@b..com", "x" * 250 + "@a.com"):
        assert normalize_email(bad) is None


def test_compose_has_the_code():
    subject, body = compose("sign_in", "123456", 10)
    assert "123456" in subject and "123456" in body and "10 minutes" in body
    assert "delete" in compose("delete", "654321", 10)[0].lower()


def test_mailer_from_env():
    assert not Mailer.from_env({}).ready
    smtp = Mailer.from_env(
        {"PIP_EMAIL_PROVIDER": "smtp", "SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "me@gmail.com",
         "SMTP_PASSWORD": "abcd efgh ijkl mnop"}
    )
    assert smtp.ready and smtp.smtp_port == 587 and smtp.sender == "me@gmail.com"
    assert smtp.smtp_password == "abcdefghijklmnop"
    assert "abcd" not in repr(smtp) and "abcd" not in smtp.describe()
    assert not Mailer.from_env({"PIP_EMAIL_PROVIDER": "smtp", "SMTP_HOST": "h"}).ready
    assert Mailer.from_env({"PIP_EMAIL_PROVIDER": "resend", "RESEND_API_KEY": "k", "PIP_EMAIL_FROM": "a@b.co"}).ready
    assert not Mailer.from_env({"PIP_EMAIL_PROVIDER": "pigeon"}).ready


def test_gmail_api_sends_with_a_refreshed_token(monkeypatch):
    import base64
    import email
    import io
    import json as jsonlib
    import urllib.request

    from little_explorer_agent import mail

    env = {
        "PIP_EMAIL_PROVIDER": "gmail",
        "PIP_EMAIL_FROM": "Little Explorer <family@gmail.com>",
        "GMAIL_CLIENT_ID": "cid",
        "GMAIL_CLIENT_SECRET": "csecret",
        "GMAIL_REFRESH_TOKEN": "rtoken",
    }
    assert not Mailer.from_env({**env, "GMAIL_REFRESH_TOKEN": ""}).ready
    gmail = Mailer.from_env(env)
    assert gmail.ready and "rtoken" not in repr(gmail) and "csecret" not in repr(gmail)
    calls = []

    class Reply(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        calls.append(request)
        if request.full_url == mail.GOOGLE_TOKEN_URL:
            return Reply(jsonlib.dumps({"access_token": "at-1", "expires_in": 3599}).encode())
        return Reply(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    gmail.send("mom@example.com", "Your code: 123456", "Here it is: 123456")
    gmail.send("mom@example.com", "Your code: 654321", "Here it is: 654321")
    assert [c.full_url for c in calls] == [mail.GOOGLE_TOKEN_URL, mail.GMAIL_SEND_URL, mail.GMAIL_SEND_URL]
    assert b"grant_type=refresh_token" in calls[0].data and b"refresh_token=rtoken" in calls[0].data
    assert calls[1].get_header("Authorization").endswith(" at-1")
    raw = jsonlib.loads(calls[2].data)["raw"]
    sent = email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    assert sent["To"] == "mom@example.com" and sent["From"] == "Little Explorer <family@gmail.com>"
    assert "654321" in sent.get_payload()


def test_a_code_signs_in_once_and_makes_the_account(accounts, store):
    code, wait = accounts.start(EMAIL)
    assert len(code) == 6 and code.isdigit() and wait == RESEND_AFTER_S
    token, account, claimed = accounts.verify(EMAIL, code, (IPAD, "iPad", "iPad13,1"))
    assert account.email == EMAIL and not account.all_books and account.books == tuple(sorted(DEFAULT_BOOKS))
    assert claimed is True
    assert accounts.session(token).account.id == account.id
    with pytest.raises(AuthError) as exc:
        accounts.verify(EMAIL, code, (IPAD, "", ""))
    assert exc.value.code == "expired"
    # Only hashes are stored.
    dump = store.backup()["tables"]
    assert code not in str(dump["email_codes"]["rows"]) and token not in str(dump["auth_sessions"]["rows"])


def test_signing_in_again_finds_the_same_account(accounts, clock):
    _, first, _ = sign_in(accounts, clock)
    _, again, _ = sign_in(accounts, clock, email="FAMILY@example.com ".strip().lower(), device=IPHONE)
    assert again.id == first.id


def test_wrong_codes_count_down_then_the_code_dies(accounts):
    code, _ = accounts.start(EMAIL)
    wrong = "000000" if code != "000000" else "111111"
    for left in range(CODE_TRIES - 1, -1, -1):
        with pytest.raises(AuthError) as exc:
            accounts.verify(EMAIL, wrong, (IPAD, "", ""))
        assert exc.value.code == "bad_code" and exc.value.extra["tries_left"] == left
    with pytest.raises(AuthError) as exc:
        accounts.verify(EMAIL, code, (IPAD, "", ""))
    assert exc.value.code == "expired"


def test_codes_expire_after_ten_minutes(accounts, clock):
    code, _ = accounts.start(EMAIL)
    clock.t += 10 * 60 + 1
    with pytest.raises(AuthError) as exc:
        accounts.verify(EMAIL, code, (IPAD, "", ""))
    assert exc.value.code == "expired"


def test_a_new_code_replaces_the_old_one(accounts, clock):
    old, _ = accounts.start(EMAIL)
    clock.t += RESEND_AFTER_S
    new, _ = accounts.start(EMAIL)
    if old != new:
        with pytest.raises(AuthError):
            accounts.verify(EMAIL, old, (IPAD, "", ""))
    assert accounts.verify(EMAIL, new, (IPAD, "", ""))[1].email == EMAIL


def test_asking_is_rate_limited(store, clock):
    accounts = Accounts(store, secret="s", clock=clock, daily_cap=7)
    accounts.start(EMAIL, ip="1.2.3.4")
    with pytest.raises(AuthError) as exc:
        accounts.start(EMAIL, ip="1.2.3.4")
    assert exc.value.code == "too_many" and 0 < exc.value.extra["retry_after_s"] <= RESEND_AFTER_S
    for _ in range(PER_EMAIL_PER_HOUR - 1):
        clock.t += RESEND_AFTER_S
        accounts.start(EMAIL)
    clock.t += RESEND_AFTER_S
    with pytest.raises(AuthError) as exc:
        accounts.start(EMAIL)
    assert exc.value.code == "too_many" and exc.value.extra["retry_after_s"] > RESEND_AFTER_S
    # Another address still works, until the day's cap.
    accounts.start("b@example.com")
    accounts.start("c@example.com")
    with pytest.raises(AuthError):
        accounts.start("d@example.com")
    clock.t += 86400
    accounts.start("d@example.com")


def test_per_ip_limit(store, clock):
    accounts = Accounts(store, secret="s", clock=clock)
    for i in range(20):
        accounts.start(f"kid{i}@example.com", ip="9.9.9.9")
    with pytest.raises(AuthError):
        accounts.start("kid99@example.com", ip="9.9.9.9")
    accounts.start("kid99@example.com", ip="8.8.8.8")


def test_review_login(store):
    accounts = Accounts(store, secret="s", review_email="Review@Example.com", review_code="246810")
    assert accounts.start("review@example.com") == (None, RESEND_AFTER_S)
    assert accounts.verify("review@example.com", "246810", (OTHER, "", ""))[1].email == "review@example.com"


# --- linking and claiming ----------------------------------------------------------------------------


def test_the_family_account_claims_both_devices(store, accounts):
    ledger = CoinLedger(store)
    give_stars(store, IPAD, 23, "i")
    give_stars(store, IPHONE, 9, "p")
    ledger.convert(IPAD, "conv-ipad-0001", 2, 10)
    before = (ledger.balance(IPAD), ledger.balance(IPHONE))
    assert before[0].stars_earned == 23 and before[0].coins == 2 and before[1].stars_earned == 9

    account, created = accounts.create(EMAIL, all_books=True)
    assert created and account.all_books
    assert accounts.link(EMAIL, IPAD) == (account, True)
    assert accounts.link(EMAIL, IPHONE)[1] is True

    total = ledger.balance_of(Scope(account_id=account.id))
    assert (total.stars_earned, total.stars_spent, total.coins) == (32, 20, 2)
    # Old builds still ask by device; a linked device answers for the whole account.
    assert ledger.balance(IPHONE) == total
    assert len(store.stars_for(IPHONE)) == 32
    assert [d["id"] for d in accounts.devices_of(account.id)].count(IPAD) == 1

    # Linking again changes nothing.
    accounts.link(EMAIL, IPAD)
    assert ledger.balance_of(Scope(account_id=account.id)) == total


def test_the_owner_can_leave_a_devices_earlier_stars_out(store, accounts, clock):
    ledger = CoinLedger(store)
    give_stars(store, IPAD, 23, "i")
    give_stars(store, IPHONE, 12, "p")
    ledger.convert(IPHONE, "conv-iphone-001", 1, 10)
    account, _ = accounts.create(EMAIL, all_books=True)
    assert accounts.link(EMAIL, IPAD)[1] is True
    assert accounts.link(EMAIL, IPHONE, claim=False)[1] is False

    total = ledger.balance_of(Scope(account_id=account.id))
    assert (total.stars_earned, total.coins) == (23, 0)
    assert ledger.balance(IPHONE) == total
    # Kept on the server under the device, but counted for no one.
    assert len(store.stars_of(Scope(device_id=IPHONE))) == 12
    assert ledger.balance_of(Scope(device_id=IPHONE)).coins == 1
    # Signing in on the iPhone later doesn't claim them, and the app may not import its local stars.
    token, _, claimed = sign_in(accounts, clock, device=IPHONE)
    assert claimed is False
    with pytest.raises(AuthError) as err:
        accounts.import_device(accounts.session(token), stars=[], hearts=[])
    assert err.value.code == "not_claimed"
    assert ledger.balance_of(Scope(account_id=account.id)).stars_earned == 23


def test_new_stars_on_a_linked_device_are_the_accounts(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock, device=IPAD)
    sign_in(accounts, clock, device=IPHONE)
    give_stars(store, IPHONE, 3, "new")
    assert len(store.stars_of(Scope(account_id=account.id))) == 3
    assert store.stars_of(Scope(device_id=IPHONE)) == []


def test_the_page_limit_is_shared_by_the_accounts_devices(store, accounts, clock):
    sign_in(accounts, clock, device=IPAD)
    sign_in(accounts, clock, device=IPHONE)
    award = {"book": "paris", "page": "eiffel", "stars": 1, "awarded_at": "2026-01-01T10:00:00-08:00"}
    for i in range(5):
        assert store.record_star({**award, "id": f"cap-star-{i:04d}", "device_id": IPAD if i % 2 else IPHONE})
    assert not store.record_star({**award, "id": "cap-star-9999", "device_id": IPAD})


def test_a_second_account_does_not_take_the_first_ones_stars(store, accounts, clock):
    give_stars(store, IPAD, 5, "old")
    _, family, claimed = sign_in(accounts, clock, device=IPAD)
    assert claimed
    _, friend, claimed = sign_in(accounts, clock, email="friend@example.com", device=IPAD)
    assert not claimed
    assert len(store.stars_of(Scope(account_id=family.id))) == 5
    assert store.stars_of(Scope(account_id=friend.id)) == []


def test_unlink_and_sign_out(store, accounts, clock):
    token, account, _ = sign_in(accounts, clock, device=IPAD)
    give_stars(store, IPAD, 2, "x")
    accounts.sign_out(token)
    assert accounts.session(token) is None
    assert store.scope(IPAD) == Scope(device_id=IPAD)
    # What it earned while signed in stays with the account.
    assert len(store.stars_of(Scope(account_id=account.id))) == 2
    accounts.link(EMAIL, IPAD)
    assert accounts.unlink(IPAD) and not accounts.unlink(IPAD)


def test_shelf_grants(accounts):
    accounts.create(EMAIL)
    account = accounts.set_books(EMAIL, grant=["hawaii"], revoke=["shanghai"])
    assert "hawaii" in account.books and "shanghai" not in account.books
    assert accounts.set_books(EMAIL, all_books=True).may_read("chengdu")
    with pytest.raises(ValueError):
        accounts.set_books(EMAIL, grant=["Bad Id"])
    with pytest.raises(LookupError):
        accounts.set_books("nobody@example.com", grant=["paris"])


def test_default_books_from_env(store):
    accounts = Accounts.from_env(store, {"PIP_DEFAULT_BOOKS": "paris, london", "PIP_AUTH_SECRET": "s"})
    assert accounts.create(EMAIL)[0].books == ("london", "paris")


def test_import_adds_local_stars_and_hearts_once(store, accounts, clock):
    give_stars(store, IPAD, 2, "srv")
    token, account, claimed = sign_in(accounts, clock, device=IPAD)
    session = accounts.session(token)
    local = [
        {"id": "star-srv-BDFF5D95-0000", "book": "paris", "page": "p0", "stars": 1},  # already on the server
        {"id": "LOCAL-ONLY-STAR-1", "book": "london", "page": "big-ben", "stars": 2, "awarded_at": "2025-05-01T09:00:00Z"},
        {"id": "bad id!", "stars": 1},
    ]
    hearts = ["paris/eiffel", "london/big-ben", "not a page"]
    assert accounts.import_device(session, local, hearts) == {"stars_added": 1, "hearts_added": 2}
    assert accounts.import_device(session, local, hearts) == {"stars_added": 0, "hearts_added": 0}
    stars = store.stars_of(Scope(account_id=account.id))
    assert sum(s["stars"] for s in stars) == 4
    assert accounts.hearts(account.id) == ["london/big-ben", "paris/eiffel"]
    # Another account on the same device can't import into itself.
    other_token, _, _ = sign_in(accounts, clock, email="friend@example.com", device=IPAD)
    with pytest.raises(AuthError):
        accounts.import_device(accounts.session(other_token), local, hearts)


def test_delete_erases_the_account(store, accounts, clock):
    give_stars(store, IPAD, 3, "d")
    token, account, _ = sign_in(accounts, clock, device=IPAD)
    accounts.set_hearts(account.id, ["paris/eiffel"])
    clock.t += RESEND_AFTER_S + 1
    with pytest.raises(AuthError):
        accounts.delete(account.id, EMAIL, "123456")  # no delete code yet
    code, _ = accounts.start(EMAIL, "delete")
    counts = accounts.delete(account.id, EMAIL, code)
    assert counts["stars"] == 3 and counts["hearts"] == 1
    assert accounts.get(EMAIL) is None and accounts.session(token) is None
    assert store.scope(IPAD) == Scope(device_id=IPAD) and store.stars_for(IPAD) == []


def test_backup_and_restore_round_trip(store, accounts, clock, tmp_path):
    give_stars(store, IPAD, 4, "b")
    dump = store.backup()
    sign_in(accounts, clock, device=IPAD)
    assert accounts.get(EMAIL)
    copy = DeviceStore(sqlite_path=tmp_path / "copy.sqlite3", limits=Limits())
    copy.restore(dump)
    assert len(copy.stars_for(IPAD)) == 4
    store.restore(dump)
    assert accounts.get(EMAIL) is None and store.scope(IPAD) == Scope(device_id=IPAD)
    with pytest.raises(ValueError):
        store.restore({"tables": {"nope": {"columns": [], "rows": []}}})


# --- endpoints -----------------------------------------------------------------------------------


@pytest.fixture
def mailer():
    return Mailer(provider="console")


@pytest.fixture
async def client(store, accounts, mailer):
    app = create_app(
        access_key="app-key", store=store, admin_key="admin-secret", report_key="report-secret",
        accounts=accounts, mailer=mailer,
    )
    async with TestClient(TestServer(app)) as c:
        yield c


def last_code(mailer):
    return mailer.outbox[-1][1].rsplit(" ", 1)[-1]


async def api_sign_in(client, mailer, clock, email=EMAIL, device=IPAD):
    clock.t += RESEND_AFTER_S + 1
    resp = await client.post("/auth/start", json={"email": email}, headers=APP)
    assert resp.status == 200, await resp.text()
    resp = await client.post(
        "/auth/verify", json={"email": email, "code": last_code(mailer), "device": {"id": device}}, headers=APP
    )
    assert resp.status == 200, await resp.text()
    return await resp.json()


async def test_sign_in_endpoints(client, mailer, clock):
    assert (await client.post("/auth/start", json={"email": EMAIL})).status == 401
    resp = await client.post("/auth/start", json={"email": "nope"}, headers=APP)
    assert resp.status == 400 and (await resp.json())["error"] == "bad_email"
    resp = await client.post("/auth/start", json={"email": EMAIL}, headers=APP)
    assert (await resp.json()) == {"ok": True, "resend_after_s": RESEND_AFTER_S}
    assert mailer.outbox[-1][0] == EMAIL
    resp = await client.post("/auth/start", json={"email": EMAIL}, headers=APP)
    assert resp.status == 429 and (await resp.json())["error"] == "too_many"

    wrong = {"email": EMAIL, "code": "000000" if last_code(mailer) != "000000" else "111111", "device": {"id": IPAD}}
    resp = await client.post("/auth/verify", json=wrong, headers=APP)
    assert resp.status == 400 and (await resp.json())["tries_left"] == CODE_TRIES - 1
    resp = await client.post("/auth/verify", json={**wrong, "device": None}, headers=APP)
    assert resp.status == 400

    body = await api_sign_in(client, mailer, clock)
    assert body["claimed_device"] is True and body["account"]["books"] == sorted(DEFAULT_BOOKS)
    session = {**APP, "X-Pip-Session": body["token"]}
    assert (await (await client.get("/account", headers=session)).json())["account"]["email"] == EMAIL
    resp = await client.get("/account", headers={**APP, "X-Pip-Session": "nope"})
    assert resp.status == 401 and (await resp.json())["error"] == "signed_out"
    assert (await client.post("/auth/signout", headers=session)).status == 200
    assert (await client.get("/account", headers=session)).status == 401


async def test_email_off_and_failing(store, accounts):
    app = create_app(access_key="", store=store, accounts=accounts, mailer=Mailer())
    async with TestClient(TestServer(app)) as c:
        resp = await c.post("/auth/start", json={"email": EMAIL})
        assert resp.status == 503 and (await resp.json())["error"] == "email_off"

    class Broken(Mailer):
        def send(self, *_):
            from little_explorer_agent.mail import MailError

            raise MailError("smtp down")

    app = create_app(access_key="", store=store, accounts=accounts, mailer=Broken(provider="console"))
    async with TestClient(TestServer(app)) as c:
        resp = await c.post("/auth/start", json={"email": EMAIL})
        assert resp.status == 502 and (await resp.json())["error"] == "email_failed"
        # The failed email doesn't count against the minute's wait.
        assert (await c.post("/auth/start", json={"email": EMAIL})).status == 502


async def test_account_stars_coins_hearts_and_import(client, mailer, clock, store):
    give_stars(store, IPAD, 12, "e")
    body = await api_sign_in(client, mailer, clock)
    session = {**APP, "X-Pip-Session": body["token"]}
    stars = await (await client.get("/account/stars", headers=session)).json()
    assert stars["total"] == 12
    coins = await (await client.get("/account/coins", headers=session)).json()
    assert coins["stars"] == 12 and coins["can_convert"] == 1
    resp = await client.post(
        "/account/coins/convert", json={"id": "conv-acct-0001", "coins": 1, "stars_per_coin": 10}, headers=session
    )
    assert resp.status == 200 and (await resp.json())["coins"] == 1
    # The old per-device endpoint sees the same balance.
    assert (await (await client.get(f"/coins?device={IPAD}", headers=APP)).json())["coins"] == 1

    resp = await client.post("/account/hearts", json={"loved": ["paris/eiffel", "x"]}, headers=session)
    assert (await resp.json())["hearts"] == ["paris/eiffel"]
    resp = await client.post("/account/hearts", json={"unloved": ["paris/eiffel"]}, headers=session)
    assert (await resp.json())["hearts"] == []
    resp = await client.post(
        "/account/import", json={"stars": [{"id": "LOCAL-STAR-0001", "stars": 1}], "hearts": ["london/eye"]},
        headers=session,
    )
    assert (await resp.json()) == {"ok": True, "stars_added": 1, "hearts_added": 1}
    assert (await client.get("/account/stars")).status == 401


async def test_delete_endpoint(client, mailer, clock):
    body = await api_sign_in(client, mailer, clock)
    session = {**APP, "X-Pip-Session": body["token"]}
    assert (await client.post("/auth/start", json={"purpose": "delete"}, headers=APP)).status == 401
    clock.t += RESEND_AFTER_S + 1
    assert (await client.post("/auth/start", json={"purpose": "delete"}, headers=session)).status == 200
    assert "delete" in mailer.outbox[-1][1].lower()
    resp = await client.post("/account/delete", json={"code": last_code(mailer)}, headers=session)
    assert resp.status == 200
    assert (await client.get("/account", headers=session)).status == 401


async def test_admin_accounts(client, store):
    give_stars(store, IPAD, 7, "f")
    give_stars(store, IPHONE, 4, "g")
    assert (await client.get("/admin/accounts")).status == 401
    resp = await client.post("/admin/accounts", json={"email": EMAIL, "all_books": True}, headers=ADMIN)
    created = await resp.json()
    assert created["created"] is True and created["account"]["all_books"] is True
    for device in (IPAD, IPHONE):
        resp = await client.post(f"/admin/accounts/{EMAIL}/link", json={"device": device}, headers=ADMIN)
        assert resp.status == 200
    detail = await (await client.get(f"/admin/accounts/{EMAIL}", headers=ADMIN)).json()
    assert detail["stars"] == 11 and detail["coins"]["stars"] == 11 and len(detail["devices"]) == 2
    summary = (await (await client.get("/admin/accounts", headers=ADMIN)).json())["accounts"]
    assert summary[0]["stars"] == 11 and summary[0]["devices"] == 2
    devices = (await (await client.get("/admin/devices", headers=ADMIN)).json())["devices"]
    assert {d["account"] for d in devices} == {EMAIL}

    resp = await client.post(f"/admin/accounts/{EMAIL}/books", json={"all_books": False, "grant": ["paris"]}, headers=ADMIN)
    assert (await resp.json())["account"]["all_books"] is False
    resp = await client.post(f"/admin/accounts/{EMAIL}/books", json={"grant": ["atlantis"]}, headers=ADMIN)
    assert resp.status == 400
    resp = await client.post(f"/admin/accounts/{EMAIL}/unlink", json={"device": IPHONE}, headers=ADMIN)
    assert len([d for d in (await resp.json())["devices"] if d["linked"]]) == 1
    assert (await client.get("/admin/accounts/nobody@example.com", headers=ADMIN)).status == 404
    assert (await client.post("/admin/accounts/x/fly", json={}, headers=ADMIN)).status == 404


async def test_admin_backup_and_restore(client, store):
    give_stars(store, IPAD, 3, "h")
    dump = await (await client.get("/admin/backup", headers=ADMIN)).json()
    assert len(dump["tables"]["stars"]["rows"]) == 3
    give_stars(store, IPAD, 2, "later")
    assert (await client.post("/admin/restore", json={"backup": dump}, headers=ADMIN)).status == 400
    resp = await client.post("/admin/restore", json={"confirm": "restore", "backup": dump}, headers=ADMIN)
    assert resp.status == 200 and (await resp.json())["restored"]["stars"] == 3
    assert len(store.stars_for(IPAD)) == 3
