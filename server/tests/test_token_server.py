import base64
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent import AGENT_NAME
from little_explorer_agent.token_server import create_app


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-that-is-long-enough-for-hs256")
    async with TestClient(TestServer(create_app(access_key=""))) as c:
        yield c


async def test_token_endpoint_dispatches_agent(client):
    resp = await client.post("/token", json={})
    assert resp.status == 200
    data = await resp.json()
    assert data["serverUrl"] == "wss://example.livekit.cloud"
    assert data["roomName"].startswith("little-explorer-")
    c = claims(data["participantToken"])
    assert c["video"]["room"] == data["roomName"]
    assert c["roomConfig"]["agents"][0]["agentName"] == AGENT_NAME
    # The app may update its own attributes (current page) after joining.
    assert c["video"]["canUpdateOwnMetadata"] is True
    assert c["video"]["canPublishData"] is True


async def test_token_without_body(client):
    resp = await client.post("/token")
    assert resp.status == 200


async def test_rejects_odd_identity(client):
    resp = await client.post("/token", json={"identity": "x" * 200})
    assert resp.status == 400


async def test_missing_credentials_is_a_server_error(client, monkeypatch):
    monkeypatch.delenv("LIVEKIT_API_SECRET")
    resp = await client.post("/token", json={})
    assert resp.status == 500


async def test_opening_page_rides_in_the_token(client):
    context = {"book": "paris", "page": "eiffel", "page_text": "The Eiffel Tower is very tall.", "secret": "x"}
    resp = await client.post("/token", json={"context": context})
    c = claims((await resp.json())["participantToken"])
    assert c["attributes"] == {"book": "paris", "page": "eiffel", "page_text": "The Eiffel Tower is very tall."}


async def test_access_key_guards_tokens(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "test-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "test-secret-that-is-long-enough-for-hs256")
    async with TestClient(TestServer(create_app(access_key="sesame"))) as c:
        assert (await c.post("/token", json={})).status == 401
        assert (await c.post("/token", headers={"Authorization": "Bearer nope"})).status == 401
        ok = await c.post("/token", json={}, headers={"Authorization": "Bearer sesame"})
        assert ok.status == 200
        # Health and book content stay open: the content is the public repo's.
        assert (await c.get("/healthz")).status == 200
        assert (await c.get("/content/manifest.json")).status == 200


async def test_serves_manifest_and_listed_files_only(client):
    resp = await client.get("/content/manifest.json")
    assert resp.status == 200
    manifest = await resp.json()
    assert manifest["order"][0] == "paris"
    assert "Books/paris.json" in manifest["files"]
    etag = resp.headers["ETag"]
    assert (await client.get("/content/manifest.json", headers={"If-None-Match": etag})).status == 304

    book = await client.get("/content/Books/paris.json")
    assert book.status == 200
    assert (await book.json())["id"] == "paris"
    photo = next(p for p in manifest["files"] if p.startswith("Photos/"))
    assert (await client.get(f"/content/{photo}")).status == 200
    for bad in ("Books/nope.json", "../server/.env", "Books/../../server/.env", "manifest"):
        assert (await client.get(f"/content/{bad}")).status == 404


def test_keepalive_target():
    from little_explorer_agent.token_server import keepalive_target

    assert keepalive_target({}) is None
    assert keepalive_target({"RENDER_EXTERNAL_URL": "https://x.onrender.com"}) is None
    assert keepalive_target({"KEEPALIVE_SECONDS": "600", "RENDER_EXTERNAL_URL": "https://x.onrender.com/"}) == (
        "https://x.onrender.com/healthz",
        600.0,
    )
