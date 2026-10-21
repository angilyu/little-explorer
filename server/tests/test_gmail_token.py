"""tools/gmail_token.py: the env file update and the loopback consent flow (no network)."""

import http.client
import importlib.util
import io
import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "gmail_token.py"


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location("gmail_token", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_save_env_replaces_and_keeps(tool, tmp_path):
    env = tmp_path / ".env"
    env.write_text('# comment\nPIP_EMAIL_FROM="Little Explorer <a@b.co>"\nGMAIL_CLIENT_ID=old\n')
    tool.save_env(env, {"GMAIL_CLIENT_ID": "new.apps.googleusercontent.com", "GMAIL_REFRESH_TOKEN": "r"})
    assert env.read_text().splitlines() == [
        "# comment",
        'PIP_EMAIL_FROM="Little Explorer <a@b.co>"',
        "GMAIL_CLIENT_ID=new.apps.googleusercontent.com",
        "GMAIL_REFRESH_TOKEN=r",
    ]
    assert env.stat().st_mode & 0o777 == 0o600


class Reply(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_consent_returns_the_refresh_token(tool, monkeypatch):
    exchanged = {}

    def browser(url):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        assert query["scope"] == tool.SCOPE and query["access_type"] == "offline"
        assert query["code_challenge_method"] == "S256" and query["login_hint"] == "family@gmail.com"
        port = urllib.parse.urlparse(query["redirect_uri"]).port

        def answer():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/?" + urllib.parse.urlencode({"code": "auth-code", "state": query["state"]}))
            conn.getresponse().read()

        threading.Thread(target=answer).start()
        return True

    def token_endpoint(request, timeout):
        exchanged.update(urllib.parse.parse_qsl(request.data.decode()))
        return Reply(json.dumps({"refresh_token": "refresh-1", "scope": tool.SCOPE}).encode())

    monkeypatch.setattr(tool.webbrowser, "open", browser)
    monkeypatch.setattr(urllib.request, "urlopen", token_endpoint)
    assert tool.consent("id.apps.googleusercontent.com", "secret", "family@gmail.com") == "refresh-1"
    assert exchanged["code"] == "auth-code" and exchanged["grant_type"] == "authorization_code"
    assert exchanged["code_verifier"] and exchanged["redirect_uri"].startswith("http://127.0.0.1:")
