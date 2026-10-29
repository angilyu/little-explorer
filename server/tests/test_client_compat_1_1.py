"""The 1.1 build (and later ones) as it really calls: every request with its own ``X-App-*`` headers, on
both channels it ships on (the TestFlight beta app and the App Store). ``test_client_compat.py`` replays the
same contracts without headers and with generic 1.1 headers; this adds the build's own, and the update
pop-up path the headers make possible.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent.devices import DeviceStore

from test_client_compat import APP, CONTRACTS, livekit, load, mailer, make_app, replay, store  # noqa: F401

VERSIONED = [p for p in CONTRACTS if "headers" in load(p)]
# Gated by ``version_gate`` when enforcing (see token_server.enforced): a build told to update gets 426.
UNGATED = ("/content/", "/app/version", "/healthz")


def build_headers(contract: dict, channel: str) -> dict:
    return {**contract["headers"], "X-App-Channel": channel}


def test_there_is_a_versioned_contract():
    assert [p.stem for p in VERSIONED][:1] == ["app-1.1"]


@pytest.mark.parametrize("path", VERSIONED, ids=lambda p: p.stem)
@pytest.mark.parametrize("channel", ["testflight", "appstore"])
async def test_build_with_its_own_headers(path, channel, tmp_path, mailer, livekit):  # noqa: F811
    contract = load(path)
    assert channel in contract["channels"]
    runs = []
    for i, extra in enumerate((None, build_headers(contract, channel))):
        store = DeviceStore(sqlite_path=tmp_path / f"run{i}.sqlite3")  # noqa: F811
        async with TestClient(TestServer(make_app(store, mailer))) as client:
            runs.append(await replay(client, mailer, contract, extra))
    # Saying its version changes nothing while the build is supported.
    assert runs[0] == runs[1]


@pytest.mark.parametrize("path", VERSIONED, ids=lambda p: p.stem)
async def test_build_told_to_update_still_gets_content(path, store, mailer, livekit):  # noqa: F811
    """With ``enforce=versioned`` above this build, its account and Milo calls get the 426 its update
    pop-up reads; content, the version check and health stay open."""
    contract = load(path)
    headers = {**APP, **build_headers(contract, "appstore")}
    app = make_app(store, mailer, PIP_APP_MIN_VERSION="9.0", PIP_APP_ENFORCE="versioned",
                   PIP_APP_STORE_URL="https://apps.apple.com/app/id6823828609")
    async with TestClient(TestServer(app)) as client:
        for step in contract["requests"]:
            resp = await client.request(step["method"], step["path"], json=step.get("body"), headers=headers)
            where = f"{contract['app']} {step['name']}"
            if step["path"].startswith(UNGATED):
                assert resp.status == 200, where
                continue
            if resp.status == 426:
                data = await resp.json()
                assert data["error"] == "upgrade_required" and data["update_url"], where
            else:
                # Not gated: signed-in calls without a session, and the device's own star/coin reads.
                assert resp.status != 200 or step["path"].startswith(("/stars", "/coins")), where
        data = await (await client.get("/app/version", headers=headers)).json()
        assert data["status"] == "required" and data["update_url"].startswith("https://apps.apple.com/")
