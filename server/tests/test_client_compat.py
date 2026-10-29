"""Old app builds keep working. Each ``contracts/app-*.json`` is what one App Store build sends and
relies on; these tests replay it against the server and the agent's wire format, with and without
the ``X-App-*`` headers newer builds send, and check the update policy can't break them by accident.
"""

import base64
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import content
from little_explorer_agent.accounts import Accounts
from little_explorer_agent.agent import ENDED_ATTRIBUTE
from little_explorer_agent.app_versions import VersionSettings
from little_explorer_agent.devices import DeviceStore
from little_explorer_agent.mail import Mailer
from little_explorer_agent.modes import parse_job_metadata
from little_explorer_agent.stars import STAR_TOPIC, StarAward, star_payload
from little_explorer_agent.token_server import create_app
from little_explorer_agent.voice.session import CONTEXT_TOPIC

CONTRACTS = sorted((Path(__file__).parent / "contracts").glob("app-*.json"))
APP = {"Authorization": "Bearer app-key"}
ADMIN = {"Authorization": "Bearer admin-secret"}
NEW_APP = {"X-App-Version": "1.1", "X-App-Build": "20261101.0930", "X-App-Channel": "appstore"}
TYPES = {"str": str, "int": int, "bool": bool, "list": list, "dict": dict, "number": (int, float)}


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def check_shape(value: object, shape: object, where: str) -> None:
    """``value`` has every field of ``shape`` with its type (``"str?"`` may be null); more is fine."""
    if isinstance(shape, dict):
        assert isinstance(value, dict), f"{where}: expected an object, got {value!r}"
        for key, inner in shape.items():
            assert key in value, f"{where}: {key!r} is missing (old apps read it)"
            check_shape(value[key], inner, f"{where}.{key}")
        return
    kind, optional = str(shape).rstrip("?"), str(shape).endswith("?")
    if value is None:
        assert optional, f"{where}: is null"
        return
    expected = TYPES[kind]
    assert isinstance(value, expected) and not (kind in ("int", "number") and isinstance(value, bool)), (
        f"{where}: expected {kind}, got {value!r}"
    )


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.fixture
def livekit(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-that-is-long-enough-for-hs256")


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "compat.sqlite3")


@pytest.fixture
def mailer():
    return Mailer(provider="console")


def make_app(store, mailer, **env):
    versions = VersionSettings.from_env(store, env) if env else None
    return create_app(
        access_key="app-key", admin_key="admin-secret", report_key="report-secret", store=store,
        accounts=Accounts(store, secret="test-secret"), mailer=mailer, versions=versions,
    )


async def replay(client, mailer, contract: dict, extra: dict | None = None) -> list[tuple[str, int, object]]:
    """Send every request in ``contract`` as that build does (plus ``extra`` headers), checking
    each answer; returns ``(name, status, body)`` for comparing runs."""
    token = None
    seen = []
    for step in contract["requests"]:
        headers = {**APP, **(extra or {})}
        if step.get("session"):
            headers["X-Pip-Session"] = token
        body = step.get("body")
        if body and body.get("code") == "<code>":
            body = {**body, "code": mailer.outbox[-1][1].rsplit(" ", 1)[-1]}
        resp = await client.request(step["method"], step["path"], json=body, headers=headers)
        data = await resp.json() if resp.content_type == "application/json" else None
        where = f"{contract['app']} {step['name']}"
        assert resp.status == step["status"], f"{where}: {resp.status} {data}"
        check_shape(data, step["shape"], where)
        if "error" in step:
            assert data["error"] == step["error"], where
        if step.get("save_token"):
            token = data["token"]
        if "agent_metadata" in step:
            grant = claims(data["participantToken"])
            meta = parse_job_metadata(grant["roomConfig"]["agents"][0]["metadata"])
            # Old builds' metadata: the same keys, plus the app build only when it says one.
            assert set(meta) - {"app_version", "app_build", "app_channel"} == set(step["agent_metadata"]), where
            assert grant["attributes"] == step["attributes"], where
        seen.append((step["name"], resp.status, sorted(data) if isinstance(data, dict) else data))
    return seen


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.stem)
async def test_old_builds_requests_still_work(path, store, mailer, livekit):
    contract = load(path)
    async with TestClient(TestServer(make_app(store, mailer))) as client:
        await replay(client, mailer, contract)


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.stem)
async def test_headers_change_nothing_by_default(path, tmp_path, mailer, livekit):
    """A build that sends its version gets the same answers as one that doesn't."""
    contract = load(path)
    runs = []
    for i, extra in enumerate((None, NEW_APP)):
        store = DeviceStore(sqlite_path=tmp_path / f"run{i}.sqlite3")
        async with TestClient(TestServer(make_app(store, mailer))) as client:
            runs.append(await replay(client, mailer, contract, extra))
    assert runs[0] == runs[1]


@pytest.mark.parametrize("path", CONTRACTS, ids=lambda p: p.stem)
async def test_old_builds_survive_enforcing_for_versioned_builds(path, store, mailer, livekit):
    """``enforce=versioned`` turns away too-old builds that say their version, never the ones that
    can't (1.0 has no update pop-up)."""
    contract = load(path)
    app = make_app(store, mailer, PIP_APP_MIN_VERSION="9.0", PIP_APP_ENFORCE="versioned")
    async with TestClient(TestServer(app)) as client:
        await replay(client, mailer, contract)


def test_agent_wire_format(store):
    """The data topics, attributes and star message the app's LiveKit code reads."""
    for path in CONTRACTS:
        agent = load(path)["agent"]
        assert CONTEXT_TOPIC == agent["context_topic"]
        assert STAR_TOPIC == agent["star_topic"]
        assert ENDED_ATTRIBUTE == agent["ended_attribute"]
        keys = (content.BOOK_KEY, content.PAGE_KEY, content.PAGE_NUMBER_KEY, content.CITY_KEY, content.PAGE_TEXT_KEY)
        assert set(agent["page_attributes"]) <= set(keys)
        award = StarAward(
            id="star-0001-aaaa", device_id="ipad", room="r", awarded_at="2026-10-20T10:00:00-07:00", book="paris",
            page="paris-eiffel-tower", city="Paris", kind="question", long=False, why_how=True, followup=False,
            stars=1, words=6, reason="asked why", account_id="acct",
        )
        check_shape(json.loads(star_payload(award, 3)), agent["star_message"], f"{path.stem} milo.star")


# --- the update policy over HTTP -------------------------------------------------------------------


async def test_app_version_endpoint(store, mailer, livekit):
    app = make_app(store, mailer, PIP_APP_MIN_VERSION="1.1", PIP_APP_RECOMMENDED_VERSION="1.2")
    async with TestClient(TestServer(app)) as client:
        # No key needed, so even a build with an old key learns it must update.
        resp = await client.get("/app/version", headers={"X-App-Version": "1.0.1", "X-App-Channel": "appstore"})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "required" and data["min_version"] == "1.1"
        assert data["update_url"] == "https://apps.apple.com/app/id6823828609"
        assert data["client"] == {"version": "1.0.1", "build": None, "channel": "appstore", "legacy": False}
        resp = await client.get("/app/version", headers={"X-App-Version": "1.1"})
        assert (await resp.json())["status"] == "recommended"
        resp = await client.get("/app/version?version=1.2&channel=testflight")
        assert (await resp.json())["status"] == "ok"
        resp = await client.get("/app/version")
        assert (await resp.json())["client"]["legacy"] is True


async def test_enforcing_refuses_too_old_builds_but_not_content_or_the_agent(store, mailer, livekit):
    app = make_app(store, mailer, PIP_APP_MIN_VERSION="1.2", PIP_APP_ENFORCE="versioned",
                   PIP_APP_STORE_URL="https://apps.apple.com/app/id123")
    old = {**APP, "X-App-Version": "1.1"}
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/token", json={}, headers=old)
        assert resp.status == 426
        data = await resp.json()
        assert data["error"] == "upgrade_required" and data["update_url"] == "https://apps.apple.com/app/id123"
        assert data["message"]
        for path in ("/account", "/account/stars", "/coins?device=ipad-1", "/stars?device=ipad-1"):
            assert (await client.get(path, headers=old)).status == 426, path
        assert (await client.post("/auth/start", json={"email": "a@example.com"}, headers=old)).status == 426
        # Still served: content, health, the policy itself, the owner and the agent (which sends no version).
        for path in ("/content/manifest.json", "/healthz", "/app/version"):
            assert (await client.get(path, headers=old)).status == 200, path
        assert (await client.get("/admin/app-versions", headers=ADMIN)).status == 200
        report = {"Authorization": "Bearer report-secret"}
        assert (await client.get("/stars?account=acct-1", headers=report)).status == 200
        assert (await client.post("/token", json={}, headers={**APP, "X-App-Version": "1.2"})).status == 200
        # Legacy builds are left alone unless enforce=all.
        assert (await client.post("/token", json={}, headers=APP)).status == 200


async def test_enforce_all_also_refuses_legacy_builds(store, mailer, livekit):
    app = make_app(store, mailer, PIP_APP_MIN_VERSION="1.1", PIP_APP_ENFORCE="all")
    async with TestClient(TestServer(app)) as client:
        assert (await client.post("/token", json={}, headers=APP)).status == 426
        assert (await client.post("/token", json={}, headers={**APP, **NEW_APP})).status == 200
        # Milo reads a device's earlier stars with the report key and no version; that's never the app's call.
        milo = {"Authorization": "Bear" + "er report-secret"}
        assert (await client.get("/stars?device=ipad-1", headers=milo)).status == 200
        assert (await client.get("/stars?device=ipad-1", headers=APP)).status == 426


async def test_owner_changes_the_policy_without_a_deploy(store, mailer, livekit):
    async with TestClient(TestServer(make_app(store, mailer))) as client:
        assert (await client.get("/admin/app-versions")).status == 401
        resp = await client.get("/admin/app-versions", headers=ADMIN)
        assert (await resp.json())["settings"]["min_version"] == ""
        resp = await client.post("/admin/app-versions", json={"min_version": "1.1", "enforce": "versioned"}, headers=ADMIN)
        assert resp.status == 200
        assert (await resp.json())["overrides"] == {"min_version": "1.1", "enforce": "versioned"}
        resp = await client.get("/app/version", headers={"X-App-Version": "1.0.5"})
        assert (await resp.json())["status"] == "required"
        assert (await client.post("/token", json={}, headers={**APP, "X-App-Version": "1.0.5"})).status == 426
        bad = await client.post("/admin/app-versions", json={"min_version": "soon"}, headers=ADMIN)
        assert bad.status == 400
        resp = await client.post("/admin/app-versions", json={"reset": True}, headers=ADMIN)
        assert (await resp.json())["overrides"] == {}
        assert (await client.post("/token", json={}, headers={**APP, "X-App-Version": "1.0.5"})).status == 200


async def test_token_passes_the_build_to_the_agent_and_the_device(store, mailer, livekit):
    device = {"id": "ipad-1", "name": "iPad", "model": "iPad13,18"}
    async with TestClient(TestServer(make_app(store, mailer))) as client:
        resp = await client.post("/token", json={"device": device}, headers={**APP, **NEW_APP})
        meta = parse_job_metadata(claims((await resp.json())["participantToken"])["roomConfig"]["agents"][0]["metadata"])
        assert meta["app_version"] == "1.1" and meta["app_build"] == "20261101.0930" and meta["app_channel"] == "appstore"
        assert store.get("ipad-1").app_version == "1.1 (20261101.0930, appstore)"
        resp = await client.post("/token", json={"device": device}, headers=APP)
        meta = parse_job_metadata(claims((await resp.json())["participantToken"])["roomConfig"]["agents"][0]["metadata"])
        assert not {"app_version", "app_build", "app_channel"} & set(meta)
        assert store.get("ipad-1").app_version == "1.0"
        devices = await (await client.get("/admin/devices", headers=ADMIN)).json()
        assert devices["devices"][0]["app_version"] == "1.0"


async def test_the_beta_app_is_a_testflight_build(store, mailer, livekit):
    """The beta app (``com.angilyu.littleexplorer.beta``) is the App Store app's code under another
    bundle ID; it says ``testflight``, so TestFlight's rules and link apply to it."""
    beta = {"X-App-Version": "1.1", "X-App-Build": "20261102.1015", "X-App-Channel": "testflight"}
    app = make_app(store, mailer, PIP_APP_TESTFLIGHT_MIN_BUILD="20261103.0000", PIP_APP_ENFORCE="versioned")
    device = {"id": "beta-iphone", "name": "iPhone", "model": "iPhone18,1"}
    async with TestClient(TestServer(app)) as client:
        data = await (await client.get("/app/version", headers=beta)).json()
        assert data["status"] == "required" and data["update_url"].startswith("itms-beta://")
        assert data["client"] == {"version": "1.1", "build": "20261102.1015", "channel": "testflight", "legacy": False}
        assert (await client.post("/token", json={"device": device}, headers={**APP, **beta})).status == 426
        newer = {**beta, "X-App-Build": "20261103.0900"}
        assert (await (await client.get("/app/version", headers=newer)).json())["status"] == "ok"
        resp = await client.post("/token", json={"device": device}, headers={**APP, **newer})
        assert resp.status == 200
        meta = parse_job_metadata(claims((await resp.json())["participantToken"])["roomConfig"]["agents"][0]["metadata"])
        assert meta["app_channel"] == "testflight" and meta["app_build"] == "20261103.0900"
        assert store.get("beta-iphone").app_version == "1.1 (20261103.0900, testflight)"
        # The same build number from the App Store app isn't held to TestFlight's minimum.
        store_app = {**beta, "X-App-Channel": "appstore"}
        assert (await (await client.get("/app/version", headers=store_app)).json())["status"] == "ok"
