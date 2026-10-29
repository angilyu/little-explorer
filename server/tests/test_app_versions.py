"""The app version policy: parsing what a build says it is, deciding whether it must update, and
the owner's overrides. HTTP behavior is in test_client_compat.py."""

import importlib.util
from pathlib import Path

import pytest

from little_explorer_agent.app_versions import ClientInfo, VersionPolicy, VersionSettings, parse_version
from little_explorer_agent.devices import DeviceStore
from little_explorer_agent.modes import job_metadata, parse_job_metadata


def test_parse_version():
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version(" 1.10 ") == (1, 10)
    assert parse_version("1.1.0") == parse_version("1.1") == (1, 1)
    assert parse_version("2") == (2,)
    assert parse_version("20261101.0930") == (20261101, 930)
    for bad in (None, 1.1, "", "1.", "v1.2", "1.2-beta", "1.2.3.4.5", "1..2"):
        assert parse_version(bad) is None, bad


def test_client_info():
    legacy = ClientInfo.from_headers({})
    assert legacy.legacy and legacy.version == "1.0" and legacy.metadata() == {} and legacy.describe() == "1.0"
    assert ClientInfo.from_headers({"X-App-Version": "banana"}) == legacy

    client = ClientInfo.from_headers({"X-App-Version": "1.1", "X-App-Build": "42", "X-App-Channel": "TestFlight"})
    assert not client.legacy and client.channel == "testflight"
    assert client.metadata() == {"app_version": "1.1", "app_build": "42", "app_channel": "testflight"}
    assert client.describe() == "1.1 (42, testflight)"
    assert ClientInfo.from_values("1.1", "x" * 40, "sideloaded").metadata() == {"app_version": "1.1"}

    assert client.at_least("1.1") and client.at_least("1.0.9") and not client.at_least("1.1.1")
    assert legacy.at_least("1.0") and not legacy.at_least("1.1")
    with pytest.raises(ValueError):
        client.at_least("soon")


def test_agent_reads_the_build_from_dispatch_metadata():
    client = ClientInfo.from_values("1.2", "7", "appstore")
    meta = parse_job_metadata(job_metadata("realtime", "ipad-1", 60, client=client.metadata()))
    assert ClientInfo.from_metadata(meta) == client
    old = parse_job_metadata(job_metadata("realtime", "ipad-1", 60))
    assert ClientInfo.from_metadata(old).legacy


def test_policy_status():
    policy = VersionPolicy(min_version="1.1", recommended_version="1.3")
    assert policy.status(ClientInfo.from_values("1.0.9")) == "required"
    assert policy.status(ClientInfo.from_values("1.1")) == "recommended"
    assert policy.status(ClientInfo.from_values("1.3.0")) == "ok"
    assert policy.status(ClientInfo()) == "required"  # legacy is 1.0
    assert VersionPolicy().status(ClientInfo()) == "ok"


def test_testflight_builds():
    policy = VersionPolicy(testflight_min_build="20261101", store_url="https://apps.apple.com/app/id1")
    beta = ClientInfo.from_values("1.2", "20261030.1", "testflight")
    assert policy.status(beta) == "required"
    assert policy.answer(beta)["update_url"] == "itms-beta://"
    assert policy.status(ClientInfo.from_values("1.2", "20261101.0930", "testflight")) == "ok"
    # Store builds and builds without a number aren't held to the TestFlight floor.
    assert policy.status(ClientInfo.from_values("1.2", "20261030.1", "appstore")) == "ok"
    assert policy.status(ClientInfo.from_values("1.2", None, "testflight")) == "ok"
    assert policy.answer(ClientInfo.from_values("1.2", None, "appstore"))["update_url"] == "https://apps.apple.com/app/id1"


def test_refuses_depends_on_enforce():
    old, legacy = ClientInfo.from_values("1.0.5"), ClientInfo()
    assert not VersionPolicy(min_version="1.1").refuses(old)
    versioned = VersionPolicy(min_version="1.1", enforce="versioned")
    assert versioned.refuses(old) and not versioned.refuses(legacy)
    assert not versioned.refuses(ClientInfo.from_values("1.1"))
    everyone = VersionPolicy(min_version="1.1", enforce="all")
    assert everyone.refuses(old) and everyone.refuses(legacy)
    # Recommended never refuses.
    assert not VersionPolicy(recommended_version="9", enforce="all").refuses(legacy)


def test_policy_validation_and_env():
    for changes in ({"min_version": "soon"}, {"enforce": "sometimes"}, {"store_url": "http://apple.com"},
                    {"store_url": "javascript:alert(1)"}, {"color": "red"}, {"min_version": True}):
        with pytest.raises(ValueError):
            VersionPolicy().with_changes(changes)
    ok = VersionPolicy().with_changes({"store_url": "itms-apps://apps.apple.com/app/id1", "enforce": "ALL", "min_version": ""})
    assert ok.enforce == "all" and ok.min_version == ""

    env = {"PIP_APP_MIN_VERSION": "1.1", "PIP_APP_ENFORCE": "versioned", "PIP_APP_STORE_URL": " "}
    policy = VersionPolicy.from_env(env)
    assert policy.min_version == "1.1" and policy.enforce == "versioned" and policy.store_url == ""
    with pytest.raises(ValueError):
        VersionPolicy.from_env({"PIP_APP_ENFORCE": "yes"})


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "v.sqlite3")


def test_settings_override_the_env_and_reset(store):
    settings = VersionSettings(store, VersionPolicy(min_version="1.1"), cache_s=0)
    assert settings.policy().min_version == "1.1"
    data = settings.update({"recommended_version": "1.2", "enforce": "versioned"})
    assert data["overrides"] == {"recommended_version": "1.2", "enforce": "versioned"}
    assert data["defaults"]["min_version"] == "1.1" and data["settings"]["enforce"] == "versioned"
    assert settings.update({"enforce": None})["overrides"] == {"recommended_version": "1.2"}
    with pytest.raises(ValueError):
        settings.update({"recommended_version": "later"})
    assert settings.settings()["overrides"] == {"recommended_version": "1.2"}  # nothing written
    assert settings.update({}, reset=True)["overrides"] == {}
    assert settings.policy() == VersionPolicy(min_version="1.1")


def test_settings_are_cached_and_an_update_clears_the_cache(store):
    settings = VersionSettings(store, cache_s=3600)
    assert settings.policy().min_version == ""
    other = VersionSettings(store, cache_s=3600)  # another server process
    other.update({"min_version": "1.1"})
    assert settings.policy().min_version == ""  # still cached
    settings.update({"recommended_version": "1.2"})
    assert settings.policy().min_version == "1.1"


def test_a_database_outage_never_blocks_anyone(store, monkeypatch):
    settings = VersionSettings(store, VersionPolicy(recommended_version="1.1"), cache_s=0)

    def boom(fn):
        raise RuntimeError("database is down")

    monkeypatch.setattr(store, "_run", boom)
    assert settings.policy() == VersionPolicy(recommended_version="1.1")


def test_a_database_outage_keeps_the_last_policy(store, monkeypatch):
    settings = VersionSettings(store, cache_s=0)
    settings.update({"min_version": "1.1"})
    assert settings.policy().min_version == "1.1"
    monkeypatch.setattr(store, "_run", lambda fn: (_ for _ in ()).throw(RuntimeError("down")))
    assert settings.policy().min_version == "1.1"


def test_a_database_outage_costs_one_read_per_cache_window(store, monkeypatch):
    settings = VersionSettings(store, VersionPolicy(min_version="1.1"), cache_s=60)
    calls = []

    def down(fn):
        calls.append(fn)
        raise RuntimeError("down")

    monkeypatch.setattr(store, "_run", down)
    for _ in range(5):
        assert settings.policy().min_version == "1.1"
    assert len(calls) == 1


def test_bad_stored_settings_fall_back_to_the_env(store):
    settings = VersionSettings(store, VersionPolicy(min_version="1.1"), cache_s=0)
    stamp = store.now().isoformat()
    store.transaction(lambda cur: store._exec(
        cur, "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)", ("app.enforce", "maybe", stamp)))
    assert settings.policy() == VersionPolicy(min_version="1.1")


def test_device_remembers_its_app_build(store):
    store.check("ipad-1", "iPad", "iPad13,18", app_version="1.1 (42, testflight)")
    assert store.get("ipad-1").app_version == "1.1 (42, testflight)"
    store.check("ipad-1", "iPad", "iPad13,18")  # a caller that doesn't know keeps the last one
    assert store.get("ipad-1").app_version == "1.1 (42, testflight)"
    assert store.summary()[0]["app_version"] == "1.1 (42, testflight)"


# --- owner CLI ---------------------------------------------------------------------------------


def cli_module():
    spec = importlib.util.spec_from_file_location("devices_cli", Path(__file__).parents[1] / "tools" / "devices.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


def test_cli_versions(monkeypatch, capsys):
    cli = cli_module()
    monkeypatch.setattr(cli, "load_env", lambda: None)
    store = DeviceStore.from_env()
    store.check("ipad-1", "Leo's iPad", "iPad", app_version="1.0")

    assert cli.main(["--direct", "versions"]) == 0
    out = capsys.readouterr().out
    assert "Must update below: nothing" in out and "Server refuses too-old builds: off" in out
    assert "1.0" in out and "BUILD" in out
    assert cli.main(["--direct", "versions", "min=1.1", "recommended=1.2", "enforce=versioned"]) == 0
    out = capsys.readouterr().out
    assert "Must update below: 1.1" in out and "Asked to update below: 1.2" in out
    assert "enforce=versioned" in out and "WARNING" not in out
    assert cli.main(["--direct", "versions", "enforce=all"]) == 0
    assert "WARNING: enforce=all also refuses 1.0" in capsys.readouterr().out
    assert cli.main(["--direct", "versions", "enforce=default"]) == 0
    assert "Server refuses too-old builds: off" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="min_version"):
        cli.main(["--direct", "versions", "min=soon"])
    with pytest.raises(SystemExit, match="Not a version setting"):
        cli.main(["--direct", "versions", "speed=3"])
    assert cli.main(["--direct", "versions", "reset"]) == 0
    assert "none (env defaults)" in capsys.readouterr().out


def test_cli_version_changes():
    cli = cli_module()
    assert cli.version_changes(["min=1.1", "testflight-build=20261101", "store=default"]) == {
        "min_version": "1.1", "testflight_min_build": "20261101", "store_url": None,
    }
    rows = cli.app_builds([
        {"app_version": "1.0", "last_seen": "2026-10-20T10:00"},
        {"app_version": "1.1 (42, testflight)", "last_seen": "2026-10-22T09:00"},
        {"app_version": None, "last_seen": "2026-10-01T09:00"},
        {"app_version": "1.0", "last_seen": "2026-10-21T08:00"},
    ])
    assert rows == [["1.1 (42, testflight)", "1", "2026-10-22T09:00"], ["1.0", "2", "2026-10-21T08:00"],
                    ["not seen yet", "1", "2026-10-01T09:00"]]
