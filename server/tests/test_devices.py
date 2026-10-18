"""Per-device limits, usage reports, owner tools, and Milo turning himself off."""

import asyncio
import base64
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import agent as agent_mod
from little_explorer_agent.devices import DeviceStore, Limits
from little_explorer_agent.modes import job_metadata, parse_job_metadata
from little_explorer_agent.token_server import create_app
from little_explorer_agent.usage import UsageMeter, report_target, report_usage

LA = ZoneInfo("America/Los_Angeles")
NOON = datetime(2026, 10, 16, 12, 0, tzinfo=LA)


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits(unknown_min=10, approved_min=60))


def session(store, device, minutes, room=None, when=NOON, **extra):
    return store.record_session(
        {
            "room": room or f"room-{device}-{minutes}-{when.isoformat()}",
            "device_id": device,
            "started_at": when.isoformat(),
            "duration_s": minutes * 60,
            "est_usd": 0.1 * minutes,
            **extra,
        }
    )


# --- store -------------------------------------------------------------------------------------


def test_new_device_is_unknown_with_ten_minutes(store):
    decision = store.check("ipad-1", "Leo's iPad", "iPad", now=NOON)
    assert decision.allowed and decision.reason == "ok"
    assert decision.remaining_s == 600
    device = store.get("ipad-1")
    assert (device.status, device.name, device.model) == ("unknown", "Leo's iPad", "iPad")


def test_minutes_count_down_through_the_day(store):
    store.check("ipad-1", now=NOON)
    session(store, "ipad-1", 4)
    assert store.check("ipad-1", now=NOON).remaining_s == 6 * 60


def test_unknown_device_over_its_limit_is_blocked_for_good(store):
    store.check("stranger", now=NOON)
    device = session(store, "stranger", 11)
    assert device.status == "blocked"
    assert store.check("stranger", now=NOON).reason == "blocked"
    # Still blocked tomorrow, until the owner says otherwise.
    tomorrow = datetime(2026, 10, 17, 9, 0, tzinfo=LA)
    assert store.check("stranger", now=tomorrow).reason == "blocked"


def test_unknown_device_with_too_little_left_is_blocked_at_the_door(store):
    store.check("stranger", now=NOON)
    session(store, "stranger", 9.6)  # 24 s left: not worth a session
    decision = store.check("stranger", now=NOON)
    assert (decision.allowed, decision.reason) == (False, "blocked")


def test_approved_device_gets_an_hour_then_rests_until_tomorrow(store):
    store.check("ipad-1", now=NOON)
    store.update("ipad-1", status="approved")
    session(store, "ipad-1", 30)
    assert store.check("ipad-1", now=NOON).remaining_s == 30 * 60
    device = session(store, "ipad-1", 31)
    assert device.status == "approved"  # approved devices are never auto-blocked
    decision = store.check("ipad-1", now=NOON)
    assert (decision.allowed, decision.reason) == (False, "limit")
    tomorrow = datetime(2026, 10, 17, 8, 0, tzinfo=LA)
    assert store.check("ipad-1", now=tomorrow).remaining_s == 60 * 60


def test_days_follow_the_local_timezone(store):
    store.check("ipad-1", now=NOON)
    # 11:30 pm in Los Angeles is already the next day in UTC; it still counts for the 16th.
    late = datetime(2026, 10, 16, 23, 30, tzinfo=LA)
    session(store, "ipad-1", 5, when=late.astimezone(ZoneInfo("UTC")))
    assert store.check("ipad-1", now=NOON).remaining_s == 5 * 60
    assert store.check("ipad-1", now=datetime(2026, 10, 17, 0, 30, tzinfo=LA)).remaining_s == 10 * 60


def test_unblock_gives_the_rest_of_today_without_reblocking(store):
    store.check("cousin", now=NOON)
    session(store, "cousin", 12)
    assert store.get("cousin").status == "blocked"
    store.update("cousin", status="unknown", now=NOON)
    decision = store.check("cousin", now=NOON)  # today's minutes are used, but it isn't blocked again
    assert decision.reason == "limit"
    assert store.get("cousin").status == "unknown"


def test_per_device_limit_overrides_the_status_default(store):
    store.check("ipad-1", now=NOON)
    store.update("ipad-1", status="approved", daily_limit_min=90)
    assert store.check("ipad-1", now=NOON).remaining_s == 90 * 60
    store.update("ipad-1", clear_limit=True)
    assert store.check("ipad-1", now=NOON).remaining_s == 60 * 60


def test_session_reports_are_idempotent_per_room(store):
    store.check("ipad-1", now=NOON)
    session(store, "ipad-1", 3, room="r1")
    session(store, "ipad-1", 4, room="r1")
    [row] = store.summary(now=NOON)
    assert row["today_min"] == 4 and row["sessions"] == 1


def test_find_by_name_or_label_and_refuse_ambiguous_names(store):
    store.check("a1", "iPad", now=NOON)
    store.check("b2", "iPad", now=NOON)
    with pytest.raises(LookupError, match="2 devices"):
        store.update("ipad", status="approved")
    store.update("a1", label="Leo's iPad")
    assert store.update("leo's ipad", status="approved").id == "a1"
    with pytest.raises(LookupError):
        store.update("nobody", status="approved")
    with pytest.raises(ValueError):
        store.update("a1", status="vip")


def test_summary_and_usage_by_day(store):
    store.check("ipad-1", "Leo's iPad", "iPad", now=NOON)
    session(store, "ipad-1", 6)
    session(store, "ipad-1", 3, when=datetime(2026, 10, 15, 10, 0, tzinfo=LA))
    [row] = store.summary(now=NOON)
    assert (row["today_min"], row["total_min"], row["sessions"]) == (6, 9, 2)
    assert row["today_usd"] == pytest.approx(0.6) and row["limit_min"] == 10
    days = store.usage_by_day(7, now=NOON)
    assert [(d["day"], d["minutes"]) for d in days] == [("2026-10-16", 6), ("2026-10-15", 3)]
    assert days[0]["display_name"] == "Leo's iPad"
    assert store.usage_by_day(1, now=NOON)[0]["day"] == "2026-10-16" and len(store.usage_by_day(1, now=NOON)) == 1


def test_limits_from_env():
    limits = Limits.from_env({"PIP_LIMIT_UNKNOWN_MIN": "5", "PIP_LIMIT_APPROVED_MIN": "45"})
    assert (limits.unknown_min, limits.approved_min) == (5, 45)
    assert Limits.from_env({}) == Limits()


def test_reports_need_a_room_and_a_valid_device(store):
    with pytest.raises(ValueError):
        store.record_session({"device_id": "ipad-1", "duration_s": 5})
    with pytest.raises(ValueError):
        store.record_session({"room": "r", "device_id": "bad id!", "duration_s": 5})


# --- token server ------------------------------------------------------------------------------


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.fixture
async def client(monkeypatch, store):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-that-is-long-enough-for-hs256")
    app = create_app(access_key="", store=store, admin_key="admin-secret", report_key="report-secret")
    async with TestClient(TestServer(app)) as c:
        yield c


DEVICE = {"id": "6F1C2A9E-0000-4000-8000-1234567890AB", "name": "Leo's iPad", "model": "iPad"}
ADMIN = {"Authorization": "Bearer admin-secret"}
REPORT = {"Authorization": "Bearer report-secret"}


async def test_token_uses_the_device_as_identity_and_passes_the_allowance(client):
    resp = await client.post("/token", json={"device": DEVICE})
    assert resp.status == 200
    token = claims((await resp.json())["participantToken"])
    assert token["sub"] == f"device-{DEVICE['id']}"
    meta = parse_job_metadata(token["roomConfig"]["agents"][0]["metadata"])
    assert meta == {"device_id": DEVICE["id"], "remaining_s": 600.0}


async def test_token_tells_the_agent_when_the_app_plays_the_intro(client):
    for body, intro in (({"intro": "app"}, "app"), ({"intro": "nonsense"}, None), ({}, None)):
        resp = await client.post("/token", json={"device": DEVICE, **body})
        assert resp.status == 200
        meta = parse_job_metadata(claims((await resp.json())["participantToken"])["roomConfig"]["agents"][0]["metadata"])
        assert meta.get("intro") == intro


async def test_token_rejects_a_malformed_device(client):
    for bad in ({"id": "has spaces"}, {"id": "x" * 65}, {"name": "no id"}, "ipad"):
        assert (await client.post("/token", json={"device": bad})).status == 400


async def test_old_apps_without_a_device_share_the_anonymous_allowance(client, store):
    assert (await client.post("/token", json={})).status == 200
    assert store.get("anonymous").status == "unknown"


async def test_blocked_and_tired_devices_get_a_kind_refusal(client, store):
    await client.post("/token", json={"device": DEVICE})
    store.update(DEVICE["id"], status="blocked")
    resp = await client.post("/token", json={"device": DEVICE})
    assert resp.status == 403
    body = await resp.json()
    assert body["error"] == "blocked" and "Milo needs a rest" in body["message"]

    store.update(DEVICE["id"], status="approved")
    session(store, DEVICE["id"], 60, when=datetime.now(LA))
    resp = await client.post("/token", json={"device": DEVICE})
    assert resp.status == 429
    assert (await resp.json())["error"] == "limit"


async def test_database_trouble_fails_open_with_a_short_session(client, store, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("database is down")

    monkeypatch.setattr(store, "check", broken)
    resp = await client.post("/token", json={"device": DEVICE})
    assert resp.status == 200
    meta = claims((await resp.json())["participantToken"])["roomConfig"]["agents"][0]["metadata"]
    assert parse_job_metadata(meta)["remaining_s"] == 600


async def test_usage_endpoint_needs_the_report_key(client, store):
    report = {"room": "r1", "device_id": DEVICE["id"], "duration_s": 120, "started_at": datetime.now(LA).isoformat()}
    assert (await client.post("/usage", json=report)).status == 401
    assert (await client.post("/usage", json=report, headers=ADMIN)).status == 401
    resp = await client.post("/usage", json=report, headers=REPORT)
    assert resp.status == 200 and (await resp.json())["device_status"] == "unknown"
    assert store.summary()[0]["today_min"] == 2
    assert (await client.post("/usage", json={"device_id": "x"}, headers=REPORT)).status == 400


async def test_usage_report_over_the_limit_blocks_an_unknown_device(client):
    report = {"room": "r2", "device_id": "stranger", "duration_s": 700, "started_at": datetime.now(LA).isoformat()}
    resp = await client.post("/usage", json=report, headers=REPORT)
    assert (await resp.json())["device_status"] == "blocked"
    assert (await client.post("/token", json={"device": {"id": "stranger"}})).status == 403


async def test_admin_endpoints_need_the_admin_key(client):
    assert (await client.get("/admin/devices")).status == 401
    assert (await client.get("/admin/devices", headers=REPORT)).status == 401
    assert (await client.post("/admin/devices/x", json={"status": "approved"})).status == 401


async def test_admin_lists_and_approves_by_name(client):
    await client.post("/token", json={"device": DEVICE})
    data = await (await client.get("/admin/devices", headers=ADMIN)).json()
    assert data["limits"] == {"unknown_min": 10, "approved_min": 60}
    assert [d["display_name"] for d in data["devices"]] == ["Leo's iPad"]

    resp = await client.post("/admin/devices/leo's ipad", json={"status": "approved"}, headers=ADMIN)
    assert resp.status == 200 and (await resp.json())["device"]["status"] == "approved"
    resp = await client.post(f"/admin/devices/{DEVICE['id']}", json={"daily_limit_min": 90}, headers=ADMIN)
    assert (await resp.json())["device"]["daily_limit_min"] == 90
    resp = await client.post(f"/admin/devices/{DEVICE['id']}", json={"daily_limit_min": None}, headers=ADMIN)
    assert (await resp.json())["device"]["daily_limit_min"] is None

    assert (await client.post("/admin/devices/nobody", json={"status": "blocked"}, headers=ADMIN)).status == 404
    assert (await client.post(f"/admin/devices/{DEVICE['id']}", json={"status": "vip"}, headers=ADMIN)).status == 400
    usage = await (await client.get("/admin/usage?days=3", headers=ADMIN)).json()
    assert usage == {"days": []}


async def test_admin_is_off_without_keys(monkeypatch, store):
    async with TestClient(TestServer(create_app(access_key="", store=store, admin_key="", report_key=""))) as c:
        assert (await c.get("/admin/devices")).status == 401
        assert (await c.post("/usage", json={"room": "r"})).status == 401


def test_job_metadata_round_trip():
    assert job_metadata() is None
    meta = parse_job_metadata(job_metadata("realtime", "ipad-1", 123.456))
    assert meta == {"pip_mode": "realtime", "device_id": "ipad-1", "remaining_s": 123.5}
    assert parse_job_metadata(job_metadata(intro="app")) == {"intro": "app"}
    assert agent_mod.remaining_seconds(meta) == 123.5
    assert agent_mod.remaining_seconds({}) is None
    assert agent_mod.remaining_seconds({"remaining_s": "lots"}) is None


# --- usage report from the agent ---------------------------------------------------------------


def test_meter_report_has_no_tokens_or_transcripts(tmp_path):
    meter = UsageMeter(mode="realtime", model="gpt-realtime", path=tmp_path / "u.csv", room="r1", device_id="ipad-1")
    meter.on_context({"book": "paris", "page": "eiffel"})
    assert meter.report() is None
    meter.finish()
    meter.ended_reason = "idle"
    report = meter.report("room disconnected")
    assert set(report) == {
        "room", "device_id", "started_at", "duration_s", "mode", "model",
        "book", "pages", "responses", "est_usd", "ended_reason",
    }  # fmt: skip
    assert (report["room"], report["device_id"], report["book"], report["pages"]) == ("r1", "ipad-1", "paris", 1)
    assert report["ended_reason"] == "idle"


def test_report_target_needs_url_and_key():
    assert report_target({}) is None
    assert report_target({"PIP_SERVER_URL": "https://x.example"}) is None
    assert report_target({"PIP_SERVER_URL": "https://x.example/", "PIP_REPORT_KEY": "k"}) == ("https://x.example/usage", "k")


async def test_report_usage_posts_and_retries_once():
    calls = []

    async def usage(request):
        calls.append((request.headers.get("Authorization"), await request.json()))
        return web.json_response({"ok": True}) if len(calls) > 1 else web.Response(status=503)

    app = web.Application()
    app.router.add_post("/usage", usage)
    async with TestServer(app) as server:
        target = (str(server.make_url("/usage")), "report-secret")
        ok = await report_usage({"room": "r1", "device_id": "d"}, target, pause=0)
    assert ok and len(calls) == 2
    assert calls[0] == ("Bearer report-secret", {"room": "r1", "device_id": "d"})


async def test_report_usage_gives_up_quietly():
    assert await report_usage(None, ("http://127.0.0.1:9/usage", "k")) is False
    assert await report_usage({"room": "r"}, None) is False
    # Nothing listening: both attempts fail fast and the shutdown carries on.
    assert await report_usage({"room": "r"}, ("http://127.0.0.1:9/usage", "k"), timeouts=(0.5, 0.5), pause=0) is False


# --- idle and limit ----------------------------------------------------------------------------


def test_idle_timeout_defaults_to_twenty_seconds():
    assert agent_mod.idle_timeout({}) == 20
    assert agent_mod.idle_timeout({"PIP_IDLE_TIMEOUT_S": "45"}) == 45
    assert agent_mod.idle_timeout({"PIP_IDLE_TIMEOUT_S": "0"}) is None
    assert agent_mod.agent_preset({"PIP_IDLE_TIMEOUT_S": "20"}).user_away_timeout == 20


class FakeParticipant:
    def __init__(self):
        self.attributes = {}

    async def set_attributes(self, attrs):
        self.attributes.update(attrs)


class FakeRoom:
    def __init__(self):
        self.local_participant = FakeParticipant()


class FakeCtx:
    def __init__(self):
        self.room = FakeRoom()
        self.shutdowns = []

    def shutdown(self, reason=""):
        self.shutdowns.append(reason)


class FakeSession:
    def __init__(self):
        self.handlers = {}

    def on(self, event, callback):
        self.handlers[event] = callback


class StateEvent:
    def __init__(self, new_state):
        self.new_state = new_state


async def test_silence_turns_milo_off_without_a_word():
    ctx, session, said, reasons = FakeCtx(), FakeSession(), [], []

    async def speak(text):
        said.append(text)

    ender = agent_mod.SessionEnder(ctx, speak, on_end=reasons.append)
    ender.watch(session)
    session.handlers["user_state_changed"](StateEvent("speaking"))
    await asyncio.sleep(0)
    assert ctx.shutdowns == []
    session.handlers["user_state_changed"](StateEvent("away"))
    await asyncio.sleep(0.01)
    assert ctx.room.local_participant.attributes == {"milo.ended": "idle"}
    assert ctx.shutdowns == ["milo idle"] and reasons == ["idle"] and said == []
    # A second "away" doesn't end it twice.
    session.handlers["user_state_changed"](StateEvent("away"))
    await asyncio.sleep(0.01)
    assert ctx.shutdowns == ["milo idle"]


async def test_limit_says_goodbye_then_leaves():
    ctx, session, said = FakeCtx(), FakeSession(), []

    async def speak(text):
        said.append(text)
        assert ctx.shutdowns == []  # goodbye comes before leaving

    ender = agent_mod.SessionEnder(ctx, speak)
    ender.watch(session, remaining_s=0.02)
    await asyncio.sleep(0.1)
    assert said == [agent_mod.LIMIT_GOODBYE]
    assert ctx.room.local_participant.attributes == {"milo.ended": "limit"}
    assert ctx.shutdowns == ["milo limit"]


async def test_a_stuck_goodbye_does_not_keep_milo_on():
    ctx = FakeCtx()

    async def speak(_text):
        await asyncio.sleep(10)

    ender = agent_mod.SessionEnder(ctx, speak, goodbye_timeout=0.05)
    await ender.end("limit", "bye")
    assert ctx.shutdowns == ["milo limit"]


async def test_ending_early_cancels_the_limit_timer():
    ctx, session = FakeCtx(), FakeSession()
    ender = agent_mod.SessionEnder(ctx)
    ender.watch(session, remaining_s=0.05)
    ender.cancel()
    await asyncio.sleep(0.1)
    assert ctx.shutdowns == []


class FakeHandle:
    def __init__(self):
        self.waited = False

    async def wait_for_playout(self):
        self.waited = True


class SpeakingSession:
    def __init__(self):
        self.calls = []
        self.handle = FakeHandle()

    def say(self, text, **kwargs):
        self.calls.append(("say", text, kwargs))
        return self.handle

    def generate_reply(self, **kwargs):
        self.calls.append(("generate_reply", kwargs["instructions"]))
        return self.handle


async def test_goodbye_works_in_both_engines():
    pipeline = SpeakingSession()
    await agent_mod.speaker(pipeline, "pipeline")("Bye!")
    assert pipeline.calls == [("say", "Bye!", {"allow_interruptions": False})] and pipeline.handle.waited
    realtime = SpeakingSession()
    await agent_mod.speaker(realtime, "realtime")("Bye!")
    assert realtime.calls == [("generate_reply", 'Say exactly: "Bye!"')] and realtime.handle.waited


# --- owner CLI ---------------------------------------------------------------------------------


def test_cli_lists_and_approves_directly(monkeypatch, capsys, tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("devices_cli", Path(__file__).parents[1] / "tools" / "devices.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "load_env", lambda: None)
    store = DeviceStore.from_env()
    store.check("ipad-1", "Leo's iPad", "iPad")

    assert cli.main(["--direct"]) == 0
    out = capsys.readouterr().out
    assert "Leo's iPad (iPad)" in out and "unknown" in out and "0/10 min" in out
    assert cli.main(["--direct", "approve", "leo's ipad", "--label", "Leo"]) == 0
    assert "Leo (ipad-1): approved, daily limit default" in capsys.readouterr().out
    assert cli.main(["--direct", "limit", "Leo", "90"]) == 0
    assert "daily limit 90 min" in capsys.readouterr().out
    assert cli.main(["--direct", "limit", "Leo", "default"]) == 0
    assert cli.main(["--direct", "block", "ipad-1"]) == 0
    assert store.get("ipad-1").status == "blocked"
    with pytest.raises(SystemExit, match="no device"):
        cli.main(["--direct", "approve", "nobody"])
    assert "COINS" in (cli.main(["--direct"]), capsys.readouterr().out)[1]

    assert cli.main(["--direct", "coins"]) == 0
    assert "on: 10 stars make 1 coin" in capsys.readouterr().out
    assert cli.main(["--direct", "coins", "rate=20", "max=5", "off"]) == 0
    out = capsys.readouterr().out
    assert "OFF: 20 stars make 1 coin" in out and "at most 5" in out and "stars_per_coin=20" in out
    assert cli.main(["--direct", "coins", "max=default", "on"]) == 0
    assert "on: 20 stars make 1 coin, at least 1 and no limit" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="max_coins"):
        cli.main(["--direct", "coins", "min=3", "max=2"])
    with pytest.raises(SystemExit, match="Not a coin setting"):
        cli.main(["--direct", "coins", "speed=3"])
    assert cli.main(["--direct", "coins", "reset"]) == 0
    assert "none (env defaults)" in capsys.readouterr().out

    monkeypatch.delenv("PIP_ADMIN_KEY", raising=False)
    with pytest.raises(SystemExit, match="PIP_ADMIN_KEY"):
        cli.main(["usage"])
