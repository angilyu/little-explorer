"""Waking Milo's agent before a tap (``POST /milo/warm``): only for a signed-in account that may talk,
at most once per device per gap, never while the agent was just busy, without database writes, and
the agent leaves a warm-up job without joining, OpenAI or usage."""

import sqlite3

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import agent as agent_mod
from little_explorer_agent import content as content_mod
from little_explorer_agent import warm as warm_mod
from little_explorer_agent.accounts import RESEND_AFTER_S, Accounts
from little_explorer_agent.devices import DeviceStore, Limits
from little_explorer_agent.mail import Mailer
from little_explorer_agent.modes import job_metadata, parse_job_metadata
from little_explorer_agent.token_server import create_app
from little_explorer_agent.warm import WARM_ROOM_PREFIX, Warmer, WarmLimits, is_warm, warm_metadata

IPAD = "BDFF5D95-6EBF-47BC-AA79-5FE554F0502B"
IPHONE = "4EC4F17C-EEEF-4CED-8E46-D637337F48C6"
EMAIL = "family@example.com"
APP = {"Authorization": "Bearer app-key"}
REPORT = {"Authorization": "Bearer report-secret"}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def db(tmp_path):
    return tmp_path / "d.sqlite3"


@pytest.fixture
def store(db):
    return DeviceStore(sqlite_path=db, limits=Limits(unknown_min=10, approved_min=60))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def accounts(store, clock):
    return Accounts(store, secret="test-secret", clock=clock)


@pytest.fixture
def dispatched():
    return []


@pytest.fixture
def warm_clock():
    return Clock(1000.0)


@pytest.fixture
def warmer(dispatched, warm_clock):
    async def dispatch(room, metadata):
        dispatched.append((room, metadata))

    return Warmer(WarmLimits(), dispatch=dispatch, clock=warm_clock)


@pytest.fixture
async def client(monkeypatch, store, accounts, warmer):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-that-is-long-enough-for-hs256")
    app = create_app(
        access_key="app-key", store=store, admin_key="admin-secret", report_key="report-secret",
        accounts=accounts, mailer=Mailer(provider="console"), warmer=warmer,
    )
    async with TestClient(TestServer(app)) as c:
        yield c


def sign_in(accounts, clock, device=IPAD, consent=True):
    clock.t += RESEND_AFTER_S + 1
    code, _ = accounts.start(EMAIL)
    token, account, _ = accounts.verify(EMAIL, code, (device, "iPad", "iPad13,1"))
    if consent is not None:
        accounts.set_milo_consent(account.id, consent)
    return token, account


def snapshot(db):
    with sqlite3.connect(db) as conn:
        return list(conn.iterdump())


async def warm(client, token, device=IPAD, headers=APP):
    resp = await client.post(
        "/milo/warm", json={"device": {"id": device, "name": "iPad", "model": "iPad13,1"}},
        headers={**headers, "X-Pip-Session": token} if token else headers,
    )
    return resp.status, await resp.json()


async def test_warm_dispatches_the_agent_without_writing_the_database(client, accounts, clock, db, dispatched):
    token, _ = sign_in(accounts, clock)
    clock.t += 7200  # a session's last_seen would be stamped again by now
    before = snapshot(db)
    assert await warm(client, token) == (202, {"warmed": True})
    assert snapshot(db) == before
    [(room, metadata)] = dispatched
    assert room.startswith(WARM_ROOM_PREFIX)
    assert is_warm(parse_job_metadata(metadata))


async def test_warm_needs_the_app_key_a_session_and_a_device(client, accounts, clock, dispatched):
    token, _ = sign_in(accounts, clock)
    assert (await warm(client, token, headers={}))[0] == 401
    assert await warm(client, None) == (401, {"error": "signed_out", "message": "sign in again"})
    assert (await warm(client, "not-a-session"))[0] == 401
    resp = await client.post("/milo/warm", json={}, headers={**APP, "X-Pip-Session": token})
    assert resp.status == 400
    resp = await client.post("/milo/warm", json={"device": {"id": "no spaces/or slashes!"}}, headers={**APP, "X-Pip-Session": token})
    assert resp.status == 400
    assert dispatched == []


@pytest.mark.parametrize("consent", [None, False])
async def test_no_warm_without_a_grown_ups_yes(client, accounts, clock, dispatched, consent):
    token, _ = sign_in(accounts, clock, consent=consent)
    assert await warm(client, token) == (403, {"error": "milo_off"})
    assert dispatched == []


async def test_no_warm_for_a_blocked_account_or_a_used_up_day(client, store, accounts, clock, dispatched, warm_clock):
    token, account = sign_in(accounts, clock)
    accounts.set_milo(account.id, status="blocked")
    assert await warm(client, token) == (403, {"error": "blocked"})
    accounts.set_milo(account.id, status="unknown")
    now = store.now()
    store.record_session(
        {"room": "r1", "device_id": IPAD, "account_id": account.id, "started_at": now.isoformat(), "duration_s": 600}
    )
    warm_clock.t += 601
    assert await warm(client, token) == (429, {"error": "limit"})
    assert dispatched == []
    assert store.get(IPHONE) is None


async def test_one_warm_per_device_per_gap(client, accounts, clock, dispatched, warm_clock):
    token, _ = sign_in(accounts, clock)
    assert (await warm(client, token))[0] == 202
    warm_clock.t += 599
    assert await warm(client, token) == (200, {"warmed": False, "reason": "too_soon"})
    warm_clock.t += 2
    assert (await warm(client, token))[0] == 202
    assert len(dispatched) == 2


async def test_refused_attempts_also_wait_out_the_gap(client, accounts, clock, dispatched, warm_clock):
    """The account checks read the database at most once per device per gap."""
    token, _ = sign_in(accounts, clock, consent=None)
    assert (await warm(client, token))[0] == 403
    assert await warm(client, token) == (200, {"warmed": False, "reason": "too_soon"})


async def test_no_warm_while_the_agent_was_just_busy(client, accounts, clock, dispatched, warm_clock):
    token, account = sign_in(accounts, clock)
    other, _ = sign_in(accounts, clock, device=IPHONE)
    assert (await warm(client, token))[0] == 202
    # Another device a minute later: the replica that just woke up is still there.
    warm_clock.t += 60
    assert await warm(client, other, device=IPHONE) == (200, {"warmed": False, "reason": "fresh"})
    # A real session (token, then its usage report) keeps the replica up too.
    warm_clock.t += 600
    resp = await client.post("/token", json={"device": {"id": IPHONE}}, headers={**APP, "X-Pip-Session": other})
    assert resp.status == 200
    warm_clock.t += 299
    assert (await warm(client, token))[1] == {"warmed": False, "reason": "fresh"}
    resp = await client.post(
        "/usage",
        json={"room": "r1", "device_id": IPHONE, "account_id": account.id, "duration_s": 60},
        headers=REPORT,
    )
    assert resp.status == 200
    warm_clock.t += 299
    assert (await warm(client, token))[1] == {"warmed": False, "reason": "fresh"}
    warm_clock.t += 2
    assert (await warm(client, token))[0] == 202
    assert len(dispatched) == 2


async def test_a_failed_dispatch_is_reported_not_raised(client, accounts, clock, warmer):
    async def broken(room, metadata):
        raise RuntimeError("livekit down")

    warmer._dispatch = broken
    token, _ = sign_in(accounts, clock)
    assert await warm(client, token) == (502, {"warmed": False, "reason": "dispatch"})


async def test_warm_can_be_turned_off(client, accounts, clock, dispatched, warmer):
    warmer.limits = WarmLimits(enabled=False)
    token, _ = sign_in(accounts, clock)
    assert await warm(client, token) == (200, {"warmed": False, "reason": "off"})
    assert dispatched == []


def test_limits_from_env():
    assert WarmLimits.from_env({}) == WarmLimits(True, 600.0, 300.0)
    limits = WarmLimits.from_env({"PIP_WARM": "0", "PIP_WARM_DEVICE_GAP_S": "900", "PIP_WARM_FRESH_S": "120"})
    assert limits == WarmLimits(False, 900.0, 120.0)


def test_warm_metadata_is_not_a_session():
    assert is_warm(parse_job_metadata(warm_metadata()))
    assert not is_warm(parse_job_metadata(job_metadata("realtime", IPAD, 600)))
    assert not is_warm({"warm": "true"}) and not is_warm({})


async def test_dispatch_creates_an_empty_room_and_dispatches_the_agent(monkeypatch):
    from livekit import api

    calls = []

    class FakeRoom:
        async def create_room(self, req):
            calls.append(("room", req.name, req.empty_timeout))

    class FakeDispatch:
        async def create_dispatch(self, req):
            calls.append(("dispatch", req.agent_name, req.room, req.metadata))

    class FakeAPI:
        def __init__(self, **kwargs):
            self.room, self.agent_dispatch = FakeRoom(), FakeDispatch()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(api, "LiveKitAPI", FakeAPI)
    monkeypatch.delenv("LIVEKIT_AGENT_NAME", raising=False)
    await warm_mod.dispatch_agent("milo-warm-abc", warm_metadata())
    assert calls == [
        ("room", "milo-warm-abc", warm_mod.WARM_ROOM_EMPTY_TIMEOUT_S),
        ("dispatch", "little-explorer", "milo-warm-abc", '{"warm": true}'),
    ]


class FakeJob:
    def __init__(self, metadata):
        self.metadata = metadata
        self.room = type("Room", (), {"name": "milo-warm-abc"})()


class FakeCtx:
    def __init__(self, metadata):
        self.job = FakeJob(metadata)
        self.shutdown_callbacks = []
        self.shutdown_reason = None
        self.connected = False

    def add_shutdown_callback(self, callback):
        self.shutdown_callbacks.append(callback)

    async def connect(self):
        self.connected = True

    async def wait_for_participant(self):
        raise AssertionError("a warm-up never waits for anyone")

    def shutdown(self, reason=""):
        self.shutdown_reason = reason


async def test_agent_leaves_a_warm_job_without_joining_openai_or_usage(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("a warm-up must not start a session")

    monkeypatch.setenv("PIP_USAGE_FILE", str(tmp_path / "usage.csv"))
    for name in ("_realtime", "_pipeline", "refresh_remote", "report_usage", "UsageMeter", "StarGame"):
        monkeypatch.setattr(agent_mod, name, forbidden)
    ctx = FakeCtx(warm_metadata())
    await agent_mod.entrypoint(ctx)
    assert ctx.shutdown_reason == "warm"
    assert not ctx.connected
    assert ctx.shutdown_callbacks == []  # nothing reports usage when the job ends
    assert not (tmp_path / "usage.csv").exists()


def test_prewarm_reads_the_books(monkeypatch):
    loaded = []
    monkeypatch.setattr(agent_mod, "PREWARM_MODULES", ())
    monkeypatch.setattr(content_mod, "load_books", lambda: loaded.append(True))
    agent_mod.prewarm()
    assert loaded == [True]
