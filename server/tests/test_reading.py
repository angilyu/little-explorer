"""Reading events: what the app sends (page views, read-aloud, a daily app open), the endpoint's checks
and limits, the owner's reports, backups, and deleting them with the account."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import reading as reading_module
from little_explorer_agent.accounts import RESEND_AFTER_S, Accounts
from little_explorer_agent.devices import DeviceStore, Limits
from little_explorer_agent.mail import Mailer
from little_explorer_agent.reading import MAX_BATCH, MAX_PAGE_SECONDS, ReadingError, ReadingLog
from little_explorer_agent.token_server import create_app

IPAD = "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B"
IPHONE = "4EC4F17C-EEEF-4CED-8E46-D637337F48C6"
EMAIL = "family@example.com"
OTHER_EMAIL = "grandma@example.com"
APP = {"Authorization": "Bearer app-key"}
ADMIN = {"Authorization": "Bearer admin-secret"}
BOOKS = {"paris", "london", "tokyo"}
PACIFIC = timezone(timedelta(hours=-8))


class Clock:
    def __init__(self, t=1_800_043_200.0):  # 2027-01-15, noon in Los Angeles
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


@pytest.fixture
def log(store, clock):
    return ReadingLog(store, clock=clock)


def sign_in(accounts, clock, email=EMAIL, device=IPAD):
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(email)
    return accounts.verify(email, code, (device, "iPad", "iPad13,1"))


def at(clock, days_ago=0):
    """An hour before the clock, ``days_ago`` days back, as a Pacific-time device stamps it."""
    moment = datetime.fromtimestamp(clock.t, tz=PACIFIC)
    return (moment - timedelta(days=days_ago, hours=1)).isoformat(timespec="seconds")


_ids = iter(range(1, 1_000_000))


def event(kind="page_view", **fields):
    base = {"id": f"evt-{next(_ids):08d}", "kind": kind, "at": fields.pop("when")}
    if kind == "page_view":
        base.update(book="paris", page="eiffel", page_index=4, seconds=12.5, milo=False)
    elif kind == "read_aloud":
        base.update(book="paris", page="eiffel", page_index=4, outcome="started", mode="clip")
    base.update(fields)
    return base


# --- recording -------------------------------------------------------------------------------------


def test_records_each_kind_for_the_account_and_device(log, clock):
    when = at(clock)
    counts = log.record(
        "acct-1",
        IPAD,
        [
            event("app_open", when=when),
            event(when=when, milo=True, seconds=10_000),
            event("read_aloud", when=when),
            event("read_aloud", when=when, outcome="finished", mode="system", seconds=31.26),
        ],
        BOOKS,
    )
    assert counts == {"stored": 4, "duplicates": 0, "rejected": 0}
    rows = log.events_of("acct-1")
    assert {r["device_id"] for r in rows} == {IPAD} and {r["day"] for r in rows} == {"2027-01-15"}
    by_kind = {(r["kind"], r["outcome"]): r for r in rows}
    assert by_kind[("app_open", None)]["book"] is None
    view = by_kind[("page_view", None)]
    assert view["seconds"] == MAX_PAGE_SECONDS and view["milo"] == 1 and view["page"] == "eiffel"
    assert view["page_index"] == 4
    assert by_kind[("read_aloud", "started")]["seconds"] is None
    finished = by_kind[("read_aloud", "finished")]
    assert finished["mode"] == "system" and finished["seconds"] == 31.3


def test_day_is_the_devices_own_date(log, clock):
    # 9:30 on the 15th in Tokyo is still the 14th in Los Angeles; the device's date counts.
    log.record("acct-1", IPAD, [event("app_open", when="2027-01-15T09:30:00+09:00")], BOOKS)
    assert log.events_of("acct-1")[0]["day"] == "2027-01-15"


def test_rejects_events_that_dont_fit(log, clock):
    when = at(clock)
    bad = [
        "not an event",
        event(when=when, id="short"),
        event(when=when, kind="star"),
        event(when=when, book="atlantis"),
        event(when=when, book="Paris!"),
        event(when=when, page="../etc"),
        event(when=when, page=None),
        event(when=when, seconds=-1),
        event(when=when, seconds="12"),
        event(when=when, seconds=True),
        event("read_aloud", when=when, outcome="paused"),
        event("read_aloud", when=when, mode="robot"),
        event(when="yesterday"),
        event(when=at(clock, days_ago=31)),
        event(when=(datetime.fromtimestamp(clock.t).astimezone() + timedelta(days=2)).isoformat()),
        event(when="2027-01-14T10:00:00-08:00" + " " * 40),
    ]
    good = event(when=when, page_index=5000)  # an index out of range is dropped, not the event
    counts = log.record("acct-1", IPAD, [*bad, good], BOOKS)
    assert counts == {"stored": 1, "duplicates": 0, "rejected": len(bad)}
    assert log.events_of("acct-1")[0]["page_index"] is None


def test_a_retried_batch_is_stored_once(log, clock):
    batch = [event(when=at(clock)), event("app_open", when=at(clock))]
    assert log.record("acct-1", IPAD, batch, BOOKS)["stored"] == 2
    assert log.record("acct-1", IPAD, batch, BOOKS) == {"stored": 0, "duplicates": 2, "rejected": 0}
    assert len(log.events_of("acct-1")) == 2


def test_batch_shape_and_size(log, clock):
    with pytest.raises(ReadingError) as exc:
        log.record("acct-1", IPAD, {"events": []}, BOOKS)
    assert exc.value.status == 400
    with pytest.raises(ReadingError) as exc:
        log.record("acct-1", IPAD, [event(when=at(clock)) for _ in range(MAX_BATCH + 1)], BOOKS)
    assert exc.value.status == 413 and exc.value.code == "too_many_events"
    assert log.record("acct-1", IPAD, [], BOOKS) == {"stored": 0, "duplicates": 0, "rejected": 0}


def test_rate_limit_per_account(log, clock, monkeypatch):
    monkeypatch.setattr(reading_module, "PER_ACCOUNT_PER_HOUR", 5)
    monkeypatch.setattr(reading_module, "PER_ACCOUNT_PER_DAY", 8)
    assert log.record("acct-1", IPAD, [event(when=at(clock)) for _ in range(4)], BOOKS)["stored"] == 4
    with pytest.raises(ReadingError) as exc:
        log.record("acct-1", IPHONE, [event(when=at(clock)) for _ in range(2)], BOOKS)
    assert exc.value.status == 429 and exc.value.code == "too_many"
    assert 60 <= exc.value.extra["retry_after_s"] <= 3600
    # Another account has its own allowance, and rejected events don't count.
    assert log.record("acct-2", IPAD, [event(when=at(clock)) for _ in range(5)], BOOKS)["stored"] == 5
    assert log.record("acct-1", IPAD, [event(when=at(clock), book="atlantis")] * 3, BOOKS)["rejected"] == 3
    clock.t += 3601
    assert log.record("acct-1", IPAD, [event(when=at(clock)) for _ in range(4)], BOOKS)["stored"] == 4
    with pytest.raises(ReadingError) as exc:
        log.record("acct-1", IPAD, [event(when=at(clock))], BOOKS)
    assert exc.value.status == 429 and exc.value.extra["retry_after_s"] > 3600


# --- reports ---------------------------------------------------------------------------------------


def sample(log, clock):
    """Two families over two days: one reads Paris and London on an iPad and an iPhone, the other
    opens the app on an iPad and reads Tokyo."""
    yesterday, today = at(clock, days_ago=1), at(clock)
    log.record(
        "acct-1",
        IPAD,
        [
            event("app_open", when=yesterday),
            event(when=yesterday, page="welcome", page_index=0, seconds=30),
            event(when=yesterday, page="bonjour", page_index=1, seconds=90),
            event("read_aloud", when=yesterday, page="bonjour"),
            event("read_aloud", when=yesterday, page="bonjour", outcome="finished", seconds=20),
            event("app_open", when=today),
            event(when=today, book="london", page="welcome", page_index=0, seconds=60, milo=True),
            event("read_aloud", when=today, book="london", page="welcome", mode="system"),
            event("read_aloud", when=today, book="london", page="welcome", outcome="stopped", mode="system", seconds=4),
        ],
        BOOKS,
    )
    log.record("acct-1", IPHONE, [event("app_open", when=today), event(when=today, seconds=120)], BOOKS)
    log.record(
        "acct-2",
        "IPAD-2",
        [event("app_open", when=today), event(when=today, book="tokyo", page="welcome", page_index=0, seconds=45)],
        BOOKS,
    )


def test_report_per_day_account_and_book(log, clock, accounts):
    _, family, _ = sign_in(accounts, clock)
    sample(log, clock)
    # Give acct-1's events to the signed-in family, so the report shows its email.
    log.store._run(lambda cur: cur.execute("UPDATE reading_events SET account_id = ? WHERE account_id = 'acct-1'", (family.id,)))
    report = log.report(7)
    assert report["since"] == "2027-01-09"
    today, yesterday = report["days"]
    assert today == {
        "day": "2027-01-15", "accounts": 2, "devices": 3, "opens": 3, "books": 3, "pages": 3, "minutes": 3.8,
        "read_aloud_started": 1, "read_aloud_finished": 0, "read_aloud_stopped": 1,
    }
    assert yesterday == {
        "day": "2027-01-14", "accounts": 1, "devices": 1, "opens": 1, "books": 1, "pages": 2, "minutes": 2.0,
        "read_aloud_started": 1, "read_aloud_finished": 1, "read_aloud_stopped": 0,
    }
    first, second = report["accounts"]
    assert first["account"] == EMAIL and first["devices"] == 2 and first["active_days"] == 2
    assert first["books"] == 2 and first["pages"] == 4 and first["minutes"] == 5.0
    assert second == {
        "account_id": "acct-2", "account": None, "devices": 1, "active_days": 1, "books": 1, "pages": 1,
        "minutes": 0.8, "read_aloud_started": 0, "read_aloud_finished": 0, "read_aloud_stopped": 0,
    }
    books = {b["book"]: b for b in report["books"]}
    assert [b["book"] for b in report["books"]] == ["paris", "london", "tokyo"]
    assert books["paris"]["pages"] == 3 and books["paris"]["devices"] == 2 and books["paris"]["minutes"] == 4.0
    assert books["london"]["read_aloud_started"] == 1 and books["london"]["read_aloud_stopped"] == 1
    assert report["total"]["accounts"] == 2 and report["total"]["devices"] == 3 and report["total"]["pages"] == 5

    one_day = log.report(1)
    assert [d["day"] for d in one_day["days"]] == ["2027-01-15"] and one_day["total"]["pages"] == 3


def test_empty_report(log):
    report = log.report(30)
    assert report["days"] == [] and report["accounts"] == [] and report["books"] == []
    assert report["total"]["accounts"] == 0 and report["total"]["minutes"] == 0


# --- deleting, backups -----------------------------------------------------------------------------


def test_deleting_the_account_deletes_its_events(store, accounts, clock, log):
    token, account, _ = sign_in(accounts, clock)
    log.record(account.id, IPAD, [event(when=at(clock)), event("app_open", when=at(clock))], BOOKS)
    log.record("someone-else", IPHONE, [event(when=at(clock))], BOOKS)
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(EMAIL, "delete")
    counts = accounts.delete(account.id, EMAIL, code)
    assert counts["reading_events"] == 2
    assert log.events_of(account.id) == [] and len(log.events_of("someone-else")) == 1


def test_backup_and_restore_keep_events(store, log, clock, tmp_path):
    log.record("acct-1", IPAD, [event(when=at(clock)), event("read_aloud", when=at(clock))], BOOKS)
    dump = store.backup()
    assert len(dump["tables"]["reading_events"]["rows"]) == 2
    copy = DeviceStore(sqlite_path=tmp_path / "copy.sqlite3", limits=Limits())
    assert copy.restore(dump)["reading_events"] == 2
    assert len(ReadingLog(copy, clock=clock).events_of("acct-1")) == 2
    # A backup from before reading events still restores.
    del dump["tables"]["reading_events"]
    assert "reading_events" not in copy.restore(dump)


# --- endpoints -------------------------------------------------------------------------------------


@pytest.fixture
async def client(store, accounts, log):
    app = create_app(
        access_key="app-key", store=store, admin_key="admin-secret", report_key="report-secret",
        accounts=accounts, mailer=Mailer(provider="console"), reading=log,
    )
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_reading_endpoint(client, accounts, clock, log):
    events = [event("app_open", when=at(clock)), event(when=at(clock), account_id="nope", device_id="nope")]
    assert (await client.post("/account/reading", json={"events": events})).status == 401
    resp = await client.post("/account/reading", json={"events": events}, headers=APP)
    assert resp.status == 401 and (await resp.json())["error"] == "signed_out"

    token, account, _ = sign_in(accounts, clock)
    session = {**APP, "X-Pip-Session": token}
    resp = await client.post("/account/reading", json={"events": events}, headers=session)
    assert resp.status == 200 and (await resp.json()) == {"ok": True, "stored": 2, "duplicates": 0, "rejected": 0}
    rows = log.events_of(account.id)
    assert {(r["account_id"], r["device_id"]) for r in rows} == {(account.id, IPAD)}

    # Book ids come from the served library; others are skipped.
    resp = await client.post(
        "/account/reading", json={"events": [event(when=at(clock), book="atlantis")]}, headers=session
    )
    assert (await resp.json())["rejected"] == 1
    resp = await client.post("/account/reading", data="nope", headers=session)
    assert resp.status == 400
    resp = await client.post("/account/reading", json={"events": "nope"}, headers=session)
    assert resp.status == 400 and (await resp.json())["error"] == "bad_events"
    resp = await client.post(
        "/account/reading", json={"events": [event(when=at(clock)) for _ in range(MAX_BATCH + 1)]}, headers=session
    )
    assert resp.status == 413 and (await resp.json())["max"] == MAX_BATCH

    # Signed out, the token no longer records anything.
    assert (await client.post("/auth/signout", headers=session)).status == 200
    resp = await client.post("/account/reading", json={"events": [event(when=at(clock))]}, headers=session)
    assert resp.status == 401


def test_progress_merges_every_device(log, clock):
    assert log.progress("acct-1") == {"pages": [], "milo_pages": [], "listened": [], "days": []}
    log.record("acct-1", IPAD, [
        event(when=at(clock, 1), page="eiffel", milo=True),
        event(when=at(clock, 1), page="eiffel"),
        event("read_aloud", when=at(clock), page="eiffel", outcome="finished"),
        event("read_aloud", when=at(clock), page="louvre", outcome="stopped"),
        event("app_open", when=at(clock, 3)),
    ], BOOKS)
    log.record("acct-1", IPHONE, [event(when=at(clock), book="london", page="big-ben")], BOOKS)
    log.record("acct-2", IPHONE, [event(when=at(clock, 5), book="tokyo", page="tower")], BOOKS)
    today = datetime.fromtimestamp(clock.t, tz=PACIFIC).date()
    assert log.progress("acct-1") == {
        "pages": ["london/big-ben", "paris/eiffel"],
        "milo_pages": ["paris/eiffel"],
        "listened": ["paris/eiffel"],
        "days": [str(today - timedelta(days=1)), str(today)],
    }


async def test_reading_progress_endpoint(client, accounts, clock):
    assert (await client.get("/account/reading", headers=APP)).status == 401
    token, _, _ = sign_in(accounts, clock)
    session = {**APP, "X-Pip-Session": token}
    resp = await client.get("/account/reading", headers=session)
    assert resp.status == 200 and (await resp.json()) == {"progress": {"pages": [], "milo_pages": [], "listened": [], "days": []}}
    await client.post("/account/reading", json={"events": [event(when=at(clock), milo=True)]}, headers=session)
    progress = (await (await client.get("/account/reading", headers=session)).json())["progress"]
    assert progress["pages"] == ["paris/eiffel"] and progress["milo_pages"] == ["paris/eiffel"] and len(progress["days"]) == 1


async def test_reading_endpoint_rate_limit(client, accounts, clock, monkeypatch):
    monkeypatch.setattr(reading_module, "PER_ACCOUNT_PER_HOUR", 3)
    token, _, _ = sign_in(accounts, clock)
    session = {**APP, "X-Pip-Session": token}
    resp = await client.post("/account/reading", json={"events": [event(when=at(clock)) for _ in range(3)]}, headers=session)
    assert resp.status == 200
    resp = await client.post("/account/reading", json={"events": [event(when=at(clock))]}, headers=session)
    body = await resp.json()
    assert resp.status == 429 and body["error"] == "too_many" and body["retry_after_s"] >= 60


async def test_admin_reading_report(client, accounts, clock, log):
    sample(log, clock)
    assert (await client.get("/admin/reading")).status == 401
    assert (await client.get("/admin/reading", headers=APP)).status == 401
    assert (await client.get("/admin/reading?days=x", headers=ADMIN)).status == 400
    report = await (await client.get("/admin/reading?days=14", headers=ADMIN)).json()
    assert [d["day"] for d in report["days"]] == ["2027-01-15", "2027-01-14"]
    assert {a["account_id"] for a in report["accounts"]} == {"acct-1", "acct-2"}
    assert report["total"]["read_aloud_started"] == 2 and report["total"]["read_aloud_finished"] == 1
    clamped = await (await client.get("/admin/reading?days=0", headers=ADMIN)).json()
    assert [d["day"] for d in clamped["days"]] == ["2027-01-15"]


# --- owner CLI -------------------------------------------------------------------------------------


def test_cli_reading_report(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("devices_cli", Path(__file__).parents[1] / "tools" / "devices.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "load_env", lambda: None)
    store = DeviceStore.from_env()
    clock = Clock(datetime.now().timestamp())
    assert cli.main(["--direct", "reading"]) == 0
    assert "No reading since" in capsys.readouterr().out
    sample(ReadingLog(store, clock=clock), clock)
    assert cli.main(["--direct", "reading", "--days", "3"]) == 0
    out = capsys.readouterr().out
    assert "2 accounts and 3 devices read 5 pages" in out
    assert "PER DAY" in out and "PER ACCOUNT" in out and "PER BOOK" in out
    assert "acct-1" in out and "tokyo" in out
    assert cli.main(["--direct", "--json", "reading"]) == 0
    assert '"read_aloud_started"' in capsys.readouterr().out
