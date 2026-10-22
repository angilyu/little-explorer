"""Milo's daily minutes and stars belong to the account: shared by its devices, approved, blocked
or limited by email; old builds on linked devices count for the account, unlinked ones keep the
per-device rules; and the one-time move from devices to accounts."""

import base64
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import devices as devices_mod
from little_explorer_agent.accounts import RESEND_AFTER_S, Accounts
from little_explorer_agent.devices import MILO_PER_ACCOUNT, DeviceStore, Limits, Scope
from little_explorer_agent.mail import Mailer
from little_explorer_agent.modes import job_metadata, parse_job_metadata
from little_explorer_agent.stars import StarAward, fetch_page_stars, star_payload
from little_explorer_agent.token_server import create_app
from little_explorer_agent.usage import UsageMeter

LA = ZoneInfo("America/Los_Angeles")
NOON = datetime(2026, 10, 16, 12, 0, tzinfo=LA)
IPAD = "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B"
IPHONE = "4EC4F17C-EEEF-4CED-8E46-D637337F48C6"
OLD = "0000AAAA-1111-4222-8333-444455556666"
EMAIL = "family@example.com"


def bearer(key):
    return {"Authorization": "Bearer " + key}


APP, ADMIN, REPORT = bearer("app-key"), bearer("admin-secret"), bearer("report-secret")


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits(unknown_min=10, approved_min=60))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def accounts(store, clock):
    return Accounts(store, secret="test-secret", clock=clock)


def sign_in(accounts, clock, email=EMAIL, device=IPAD):
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(email)
    return accounts.verify(email, code, (device, "iPad", "iPad13,1"))


def talk(store, device, minutes, when=NOON, account_id=None, room=None):
    report = {
        "room": room or f"room-{device}-{minutes}-{when.isoformat()}",
        "device_id": device,
        "started_at": when.isoformat(),
        "duration_s": minutes * 60,
    }
    if account_id:
        report["account_id"] = account_id
    return store.record_session(report)


def star(store, device, n, account_id=None, page="eiffel"):
    award = {"id": f"star-{device[:8]}-{n:04d}", "device_id": device, "book": "paris", "page": page, "stars": 1}
    if account_id:
        award["account_id"] = account_id
    return store.record_star(award)


# --- the store ----------------------------------------------------------------------------------


def test_a_new_account_gets_ten_minutes_then_rests_until_tomorrow_without_a_block(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock)
    first = store.check(IPAD, now=NOON, account_id=account.id)
    assert first.allowed and first.remaining_s == 600 and first.account_id == account.id
    talk(store, IPAD, 10, account_id=account.id)
    tired = store.check(IPAD, now=NOON, account_id=account.id)
    assert not tired.allowed and tired.reason == "limit"
    assert accounts.milo(account.id)["status"] == "unknown"  # never blocked automatically
    assert store.get(IPAD).status == "unknown"
    assert store.check(IPAD, now=NOON + timedelta(days=1), account_id=account.id).remaining_s == 600


def test_the_accounts_devices_share_its_minutes(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock, device=IPAD)
    sign_in(accounts, clock, device=IPHONE)
    accounts.set_milo(EMAIL, status="approved")
    talk(store, IPAD, 35, account_id=account.id)
    talk(store, IPHONE, 20, account_id=account.id)
    decision = store.check(IPHONE, now=NOON, account_id=account.id)
    assert decision.allowed and decision.used_s == 55 * 60 and decision.remaining_s == 5 * 60
    talk(store, IPAD, 5, account_id=account.id, room="last-bit")
    assert store.check(IPAD, now=NOON, account_id=account.id).reason == "limit"


def test_owner_approves_blocks_unblocks_and_limits_an_account_by_email(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock)
    accounts.set_milo(EMAIL, status="approved")
    assert store.check(IPAD, now=NOON, account_id=account.id).remaining_s == 3600
    accounts.set_milo(EMAIL, daily_limit_min=25)
    assert store.check(IPAD, now=NOON, account_id=account.id).remaining_s == 25 * 60
    accounts.set_milo(account.id, status="blocked")
    blocked = store.check(IPAD, now=NOON, account_id=account.id)
    assert not blocked.allowed and blocked.reason == "blocked"
    accounts.set_milo(EMAIL, status="unknown", clear_limit=True)
    assert store.check(IPAD, now=NOON, account_id=account.id).remaining_s == 600
    assert accounts.milo(account.id) == {"status": "unknown", "daily_limit_min": None, "limit_min": 10, "today_min": 0}
    with pytest.raises(LookupError):
        accounts.set_milo("nobody@example.com", status="approved")
    with pytest.raises(ValueError):
        accounts.set_milo(EMAIL, status="sleepy")
    with pytest.raises(ValueError):
        accounts.set_milo(EMAIL, daily_limit_min=-1)


def test_the_devices_own_status_no_longer_matters_once_its_account_is_known(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock)
    store.update(IPAD, status="blocked")
    assert store.check(IPAD, now=NOON, account_id=account.id).allowed


def test_an_old_build_on_a_linked_device_counts_for_the_account(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock)
    accounts.set_milo(EMAIL, status="approved")
    talk(store, IPAD, 30)  # an old agent: no account_id in the report
    decision = store.check(IPAD, now=NOON)  # an old app: no session header
    assert decision.account_id == account.id and decision.remaining_s == 30 * 60
    assert star(store, IPAD, 1)
    assert len(store.stars_of(Scope(account_id=account.id))) == 1


def test_an_unlinked_old_device_keeps_the_per_device_rules(store):
    assert store.check(OLD, now=NOON).account_id is None
    talk(store, OLD, 10)
    assert store.get(OLD).status == "blocked"  # unknown devices still get blocked for going over
    store.update(OLD, status="approved")
    assert store.check(OLD, now=NOON).remaining_s == 50 * 60


def test_sessions_and_stars_go_to_the_account_the_agent_names(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock, device=IPAD)
    accounts.unlink(IPAD)  # the device signed out since; the agent's account still counts
    talk(store, IPAD, 4, account_id=account.id)
    assert store.check(IPAD, now=NOON, account_id=account.id).used_s == 4 * 60
    assert store.get(IPAD).status == "unknown"  # an account's session never blocks the device
    assert star(store, IPAD, 1, account_id=account.id)
    assert len(store.stars_of(Scope(account_id=account.id))) == 1
    assert store.stars_of(Scope(device_id=IPAD)) == []
    # An account that doesn't exist falls back to the device's link (none here).
    assert star(store, IPAD, 2, account_id="no-such-account")
    assert len(store.stars_of(Scope(device_id=IPAD))) == 1


def test_the_page_limit_follows_the_account_across_devices(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock, device=IPAD)
    for n in range(5):
        assert star(store, IPAD if n % 2 else IPHONE, n, account_id=account.id)
    assert not star(store, IPHONE, 9, account_id=account.id)


def test_reporting_a_session_twice_keeps_its_account(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock)
    talk(store, IPAD, 3, account_id=account.id, room="r1")
    talk(store, IPAD, 6, room="r1")  # a retry without the account (and the device since unlinked)
    assert store.check(IPAD, now=NOON, account_id=account.id).used_s == 6 * 60


def test_deleting_the_account_keeps_the_minutes_as_the_devices(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock)
    talk(store, IPAD, 3, account_id=account.id)
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(EMAIL, "delete")
    accounts.delete(account.id, EMAIL, code)
    assert store.check(IPAD, now=NOON).used_s == 3 * 60
    assert store.usage_by_day(now=NOON)[0]["account"] is None


def test_summaries_show_the_accounts_milo_time(store, accounts, clock):
    _, account, _ = sign_in(accounts, clock)
    accounts.set_milo(EMAIL, status="approved")
    talk(store, IPAD, 12, when=store.now(), account_id=account.id)
    row = accounts.summary()[0]
    assert (row["milo_status"], row["limit_min"], row["today_min"]) == ("approved", 60, 12)
    assert store.usage_by_day()[0]["account"] == EMAIL
    accounts.set_milo(EMAIL, status="blocked")
    assert accounts.summary()[0]["limit_min"] == 0


# --- the one-time move --------------------------------------------------------------------------


def old_database(path):
    """A database as the last release left it: no Milo columns on accounts or sessions."""
    conn = sqlite3.connect(path)
    for statement in devices_mod.SCHEMA:
        conn.execute(statement)
    for table, column, kind in devices_mod.COLUMNS[:4]:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    stamp = NOON.isoformat()
    conn.executemany(
        "INSERT INTO accounts (id, email, created_at, last_seen) VALUES (?, ?, ?, ?)",
        [("fam", EMAIL, stamp, stamp), ("friend", "friend@example.com", stamp, stamp), ("new", "new@example.com", stamp, stamp)],
    )
    conn.executemany(
        "INSERT INTO devices (id, status, daily_limit_min, account_id, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (IPAD, "approved", None, "fam", stamp, stamp),
            (IPHONE, "unknown", None, "fam", stamp, stamp),
            ("FRIEND-PHONE", "unknown", 20.0, "friend", stamp, stamp),
            (OLD, "approved", None, None, stamp, stamp),
        ],
    )
    day = NOON.date().isoformat()
    conn.executemany(
        "INSERT INTO sessions (room, device_id, started_at, day, duration_s) VALUES (?, ?, ?, ?, ?)",
        [("a", IPAD, stamp, day, 20 * 60), ("b", IPHONE, stamp, day, 15 * 60), ("c", OLD, stamp, day, 5 * 60),
         ("d", IPAD, stamp, "2026-10-15", 50 * 60)],
    )
    conn.commit()
    conn.close()


def test_the_move_carries_todays_minutes_and_approvals_to_accounts(tmp_path):
    old_database(tmp_path / "old.sqlite3")
    store = DeviceStore(sqlite_path=tmp_path / "old.sqlite3", limits=Limits(unknown_min=10, approved_min=60))
    family = store.check(IPHONE, now=NOON)
    assert family.account_id == "fam" and family.remaining_s == 25 * 60  # 60 - (20 + 15), not a fresh hour
    friend = store.check("FRIEND-PHONE", now=NOON)
    assert friend.account_id == "friend" and friend.limit_s == 20 * 60
    assert store.check(OLD, now=NOON).remaining_s == 55 * 60  # unlinked: still its own approved hour
    accounts = Accounts(store, secret="s")
    assert accounts.milo("new")["status"] == "unknown"
    assert accounts.milo("fam")["status"] == "approved"
    rows = store.backup()["tables"]["settings"]["rows"]
    assert [r for r in rows if r[0] == MILO_PER_ACCOUNT]


def test_the_move_runs_once(tmp_path):
    old_database(tmp_path / "old.sqlite3")
    DeviceStore(sqlite_path=tmp_path / "old.sqlite3").check(IPAD, now=NOON)
    conn = sqlite3.connect(tmp_path / "old.sqlite3")
    # The owner turned the family account back to unknown, and an old session shows up unlinked.
    conn.execute("UPDATE accounts SET milo_status = 'unknown' WHERE id = 'fam'")
    conn.execute("INSERT INTO sessions (room, device_id, started_at, day, duration_s) VALUES ('e', ?, '', '2026-10-16', 60)", (IPAD,))
    conn.commit()
    conn.close()
    again = DeviceStore(sqlite_path=tmp_path / "old.sqlite3")
    assert Accounts(again, secret="s").milo("fam")["status"] == "unknown"
    rows = again.backup()["tables"]["sessions"]
    room, account = rows["columns"].index("room"), rows["columns"].index("account_id")
    assert {r[room]: r[account] for r in rows["rows"]}["e"] is None


# --- the token server and the agent -------------------------------------------------------------


def claims(token):
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def metadata(body):
    return parse_job_metadata(claims(body["participantToken"])["roomConfig"]["agents"][0]["metadata"])


@pytest.fixture
async def client(monkeypatch, store, accounts):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-that-is-long-enough-for-hs256")
    app = create_app(
        access_key="app-key", store=store, admin_key="admin-secret", report_key="report-secret",
        accounts=accounts, mailer=Mailer(provider="console"),
    )
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_token_counts_the_signed_in_accounts_minutes(client, store, accounts, clock):
    token, account, _ = sign_in(accounts, clock)
    headers = {**APP, "X-Pip-Session": token}
    resp = await client.post("/token", json={"device": {"id": IPHONE}}, headers=headers)
    assert resp.status == 200
    meta = metadata(await resp.json())
    assert meta["account_id"] == account.id and meta["device_id"] == IPHONE and meta["remaining_s"] == 600
    talk(store, IPHONE, 10, when=store.now(), account_id=account.id)
    resp = await client.post("/token", json={"device": {"id": IPAD}}, headers=headers)
    assert resp.status == 429 and (await resp.json())["error"] == "limit"


async def test_token_with_a_dead_session_says_signed_out(client):
    resp = await client.post("/token", json={"device": {"id": IPAD}}, headers={**APP, "X-Pip-Session": "stale"})
    assert resp.status == 401 and (await resp.json())["error"] == "signed_out"


async def test_token_for_old_builds_goes_by_the_device(client, accounts, clock):
    _, account, _ = sign_in(accounts, clock, device=IPAD)
    linked = await client.post("/token", json={"device": {"id": IPAD}}, headers=APP)
    assert metadata(await linked.json())["account_id"] == account.id
    unlinked = await client.post("/token", json={"device": {"id": OLD}}, headers=APP)
    assert "account_id" not in metadata(await unlinked.json())


async def test_the_agent_reads_and_reports_by_account(client, store, accounts, clock):
    _, account, _ = sign_in(accounts, clock, device=IPAD)
    accounts.unlink(IPAD)
    award = {"id": "star-account-0001", "device_id": IPAD, "account_id": account.id, "book": "paris",
             "page": "eiffel", "stars": 1, "awarded_at": NOON.isoformat()}
    assert (await client.post("/stars", json=award, headers=REPORT)).status == 200
    resp = await client.post("/usage", json={"room": "r9", "device_id": IPAD, "account_id": account.id,
                                             "started_at": store.now().isoformat(), "duration_s": 120}, headers=REPORT)
    assert resp.status == 200
    assert store.check(IPAD, account_id=account.id).used_s == 120
    assert (await client.get(f"/stars?account={account.id}", headers=APP)).status == 401  # not with the app's key
    assert (await client.get("/stars?account=bad id", headers=REPORT)).status == 400
    body = await (await client.get(f"/stars?account={account.id}", headers=REPORT)).json()
    assert body["total"] == 1
    url = str(client.make_url("/stars"))
    counts = await fetch_page_stars(IPAD, (url, "report-secret"), account_id=account.id)
    assert counts == {("paris", "eiffel", NOON.date().isoformat()): 1}
    assert await fetch_page_stars(IPAD, (url, "report-secret")) == {}  # the device alone has none
    assert await fetch_page_stars("", (url, "report-secret"), account_id=account.id) == counts


async def test_admin_sets_an_accounts_milo_time(client, accounts, clock):
    sign_in(accounts, clock)
    path = f"/admin/accounts/{EMAIL}/milo"
    assert (await client.post(path, json={"status": "approved"})).status == 401
    resp = await client.post(path, json={"status": "approved"}, headers=ADMIN)
    assert resp.status == 200 and (await resp.json())["milo"]["limit_min"] == 60
    resp = await client.post(path, json={"daily_limit_min": 45}, headers=ADMIN)
    assert (await resp.json())["milo"]["limit_min"] == 45
    resp = await client.post(path, json={"daily_limit_min": None}, headers=ADMIN)
    assert (await resp.json())["milo"] == {"status": "approved", "daily_limit_min": None, "limit_min": 60, "today_min": 0}
    assert (await client.post(path, json={"status": "sleepy"}, headers=ADMIN)).status == 400
    assert (await client.post(path, json={"daily_limit_min": "lots"}, headers=ADMIN)).status == 400
    assert (await client.post("/admin/accounts/nobody@example.com/milo", json={}, headers=ADMIN)).status == 404
    summary = (await (await client.get("/admin/accounts", headers=ADMIN)).json())["accounts"]
    assert summary[0]["milo_status"] == "approved"


def test_metadata_meter_and_star_carry_the_account(tmp_path):
    meta = parse_job_metadata(job_metadata("realtime", "ipad-1", 60, account_id="acct-1"))
    assert meta["account_id"] == "acct-1"
    assert "account_id" not in parse_job_metadata(job_metadata("realtime", "ipad-1", 60))
    meter = UsageMeter(mode="realtime", model="m", path=tmp_path / "u.csv", room="r", device_id="d", account_id="acct-1")
    meter.finish()
    assert meter.report()["account_id"] == "acct-1"
    award = StarAward("x" * 8, "d", "r", NOON.isoformat(), "paris", "eiffel", "Paris", "answer",
                      False, False, False, 1, 3, "", account_id="acct-1")
    sent = json.loads(star_payload(award, 1))
    assert "account_id" not in sent and "room" not in sent and award.to_dict()["account_id"] == "acct-1"


def test_cli_approves_blocks_and_limits_accounts_by_email(monkeypatch, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("devices_cli", Path(__file__).parents[1] / "tools" / "devices.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "load_env", lambda: None)
    store = DeviceStore.from_env()
    accounts = Accounts.from_env(store)
    account, _ = accounts.create(EMAIL)
    accounts.link(EMAIL, IPAD)
    talk(store, IPAD, 7, when=store.now(), account_id=account.id)

    assert cli.main(["--direct", "accounts"]) == 0
    assert "unknown, 7/10 min today" in capsys.readouterr().out
    assert cli.main(["--direct", "approve", EMAIL]) == 0
    assert "Milo: approved, 7/60 min today" in capsys.readouterr().out
    assert cli.main(["--direct", "limit", EMAIL, "45"]) == 0
    assert "7/45 min custom today" in capsys.readouterr().out
    assert cli.main(["--direct", "limit", EMAIL, "default"]) == 0
    assert "7/60 min today" in capsys.readouterr().out
    assert cli.main(["--direct", "block", EMAIL]) == 0
    assert "Milo: blocked" in capsys.readouterr().out
    assert cli.main(["--direct", "unblock", EMAIL]) == 0
    assert accounts.milo(account.id)["status"] == "unknown"
    assert store.get(IPAD).status == "unknown"  # the device itself was never touched
    capsys.readouterr()
    with pytest.raises(SystemExit, match="no such account"):
        cli.main(["--direct", "approve", "nobody@example.com"])
    with pytest.raises(SystemExit):
        cli.main(["--direct", "approve", EMAIL, "--label", "Mom"])
    assert cli.main(["--direct", "usage"]) == 0
    assert EMAIL in capsys.readouterr().out
    assert cli.main(["--direct"]) == 0
    assert EMAIL in capsys.readouterr().out


# --- a grown-up's permission for Milo -----------------------------------------------------------


def test_milo_consent_is_unasked_until_a_grown_up_chooses_and_belongs_to_the_account(accounts, clock):
    _, account, _ = sign_in(accounts, clock)
    assert account.milo_consent is None and account.as_json()["milo_consent"] is None
    assert accounts.set_milo_consent(account.id, True).milo_consent is True
    _, again, _ = sign_in(accounts, clock, device=IPHONE)  # a second device sees the account's choice
    assert again.milo_consent is True
    assert accounts.set_milo_consent(account.id, False).as_json()["milo_consent"] is False
    assert accounts.summary()[0]["milo_consent"] is False
    with pytest.raises(LookupError):
        accounts.set_milo_consent("missing", True)


async def test_the_app_records_milo_consent_for_the_account(client, accounts, clock):
    token, account, _ = sign_in(accounts, clock)
    session = {**APP, "X-Pip-Session": token}
    assert (await client.post("/account/milo-consent", json={"allow": True}, headers=APP)).status == 401
    assert (await client.post("/account/milo-consent", json={"allow": "yes"}, headers=session)).status == 400
    resp = await client.post("/account/milo-consent", json={"allow": True}, headers=session)
    assert resp.status == 200 and (await resp.json())["account"]["milo_consent"] is True
    body = await (await client.get("/account", headers=session)).json()
    assert body["account"]["milo_consent"] is True


async def test_token_refuses_an_account_whose_grown_up_turned_milo_off(client, accounts, clock):
    token, account, _ = sign_in(accounts, clock, device=IPAD)
    session = {**APP, "X-Pip-Session": token}
    # Never asked (families on builds before the consent screen) keeps working.
    assert (await client.post("/token", json={"device": {"id": IPAD}}, headers=session)).status == 200
    accounts.set_milo_consent(account.id, False)
    resp = await client.post("/token", json={"device": {"id": IPAD}}, headers=session)
    assert resp.status == 403 and (await resp.json())["error"] == "milo_off"
    old_build = await client.post("/token", json={"device": {"id": IPAD}}, headers=APP)  # linked device, no session
    assert old_build.status == 403 and (await old_build.json())["error"] == "milo_off"
    unlinked = await client.post("/token", json={"device": {"id": OLD}}, headers=APP)
    assert unlinked.status == 200
    accounts.set_milo_consent(account.id, True)
    assert (await client.post("/token", json={"device": {"id": IPAD}}, headers=session)).status == 200
