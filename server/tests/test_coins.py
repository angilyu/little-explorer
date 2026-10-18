"""Stars to coins: the server's rules, the per-device ledger, and its endpoints."""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from little_explorer_agent.coins import Balance, CoinError, CoinLedger, CoinRules, plan
from little_explorer_agent.devices import DeviceStore, Limits
from little_explorer_agent.token_server import create_app

DEVICE = "6F1C2A9E-0000-4000-8000-1234567890AB"
APP = {"Authorization": "Bearer app-key"}
ADMIN = {"Authorization": "Bearer admin-secret"}
REPORT = {"Authorization": "Bearer report-secret"}


@pytest.fixture
def store(tmp_path):
    return DeviceStore(sqlite_path=tmp_path / "d.sqlite3", limits=Limits())


@pytest.fixture
def ledger(store):
    return CoinLedger(store)


def give_stars(store, device=DEVICE, count=1, room="room-1"):
    for i in range(count):
        store.record_star(
            {"id": f"star-{device[:8]}-{room}-{i:04d}", "device_id": device, "room": room, "book": "paris",
             "page": f"p{i}", "stars": 1}
        )


# --- rules -------------------------------------------------------------------------------------


def test_rules_default_to_ten_stars_a_coin():
    rules = CoinRules.from_env({})
    assert rules == CoinRules(enabled=True, stars_per_coin=10, min_coins=1, max_coins=0)


def test_rules_from_env():
    env = {"PIP_COINS": "off", "PIP_STARS_PER_COIN": "20", "PIP_COINS_MIN": "2", "PIP_COINS_MAX": "5"}
    assert CoinRules.from_env(env) == CoinRules(enabled=False, stars_per_coin=20, min_coins=2, max_coins=5)
    for bad in ({"PIP_STARS_PER_COIN": "0"}, {"PIP_STARS_PER_COIN": "lots"}, {"PIP_COINS": "maybe"},
                {"PIP_COINS_MIN": "3", "PIP_COINS_MAX": "2"}, {"PIP_COINS_MIN": "0"}):
        with pytest.raises(ValueError):
            CoinRules.from_env(bad)


def test_plan_converts_whole_coins_and_leaves_the_rest():
    balance = Balance(CoinRules(), stars_earned=27, stars_spent=0, coins=0)
    assert balance.can_convert == 2
    assert balance.as_json()["leftover_stars"] == 7
    assert plan(balance, None) == 2
    assert plan(balance, 1) == 1
    with pytest.raises(CoinError) as exc:
        plan(balance, 3)
    assert exc.value.code == "not_enough_stars" and exc.value.status == 409


def test_plan_respects_min_max_and_the_switch():
    rules = CoinRules(min_coins=2, max_coins=3)
    rich = Balance(rules, 100, 0, 0)
    assert rich.can_convert == 3 and plan(rich, None) == 3
    with pytest.raises(CoinError, match="at most 3"):
        plan(rich, 4)
    with pytest.raises(CoinError, match="at least 2"):
        plan(rich, 1)
    poor = Balance(rules, 15, 0, 0)  # one coin's worth, but the minimum is two
    assert poor.can_convert == 0
    with pytest.raises(CoinError) as exc:
        plan(poor, None)
    assert exc.value.code == "not_enough_stars"
    off = Balance(CoinRules(enabled=False), 100, 0, 0)
    assert off.can_convert == 0
    with pytest.raises(CoinError) as exc:
        plan(off, 1)
    assert (exc.value.code, exc.value.status) == ("disabled", 403)


def test_plan_refuses_a_rate_the_grown_up_did_not_see():
    with pytest.raises(CoinError) as exc:
        plan(Balance(CoinRules(stars_per_coin=20), 100, 0, 0), 1, stars_per_coin=10)
    assert exc.value.code == "rate_changed"


# --- ledger ------------------------------------------------------------------------------------


def test_converting_spends_stars_and_keeps_lifetime_stars(store, ledger):
    store.check(DEVICE, "Leo's iPad", "iPad")
    give_stars(store, count=23)
    conversion, balance, replayed = ledger.convert(DEVICE, "conv-0001", None)
    assert not replayed
    assert (conversion["coins"], conversion["stars_spent"], conversion["stars_per_coin"]) == (2, 20, 10)
    assert (balance.stars_earned, balance.stars_spent, balance.stars, balance.coins) == (23, 20, 3, 2)
    assert balance.can_convert == 0
    # Badges and the restore still see every star he earned.
    assert len(store.stars_for(DEVICE)) == 23
    (row,) = store.summary()
    assert (row["stars"], row["stars_spent"], row["coins"]) == (23, 20, 2)


def test_a_retried_conversion_is_not_counted_twice(store, ledger):
    give_stars(store, count=30)
    first, _, _ = ledger.convert(DEVICE, "conv-0001", 1)
    again, balance, replayed = ledger.convert(DEVICE, "conv-0001", 1)
    assert replayed and again == first
    assert (balance.coins, balance.stars) == (1, 20)
    with pytest.raises(CoinError) as exc:
        ledger.convert("someone-else", "conv-0001", 1)
    assert exc.value.code == "bad_id"
    with pytest.raises(CoinError):
        ledger.convert(DEVICE, "short", 1)


def test_devices_have_their_own_balances(store, ledger):
    give_stars(store, count=10)
    give_stars(store, device="other-ipad", count=5)
    ledger.convert(DEVICE, "conv-0001", 1)
    assert ledger.balance("other-ipad").as_json()["stars"] == 5
    assert ledger.balance("other-ipad").coins == 0
    with pytest.raises(CoinError):
        ledger.convert("other-ipad", "conv-0002", 1)


def test_new_stars_after_a_conversion_add_up(store, ledger):
    give_stars(store, count=12)
    ledger.convert(DEVICE, "conv-0001", None)
    give_stars(store, count=9, room="room-2")
    balance = ledger.balance(DEVICE)
    assert (balance.stars, balance.coins, balance.can_convert) == (11, 1, 1)


def test_owner_overrides_win_over_env_and_can_be_cleared(store):
    ledger = CoinLedger(store, CoinRules(stars_per_coin=10))
    give_stars(store, count=40)
    settings = ledger.update({"stars_per_coin": 20, "max_coins": 1})
    assert settings["settings"]["stars_per_coin"] == 20 and settings["overrides"] == {"stars_per_coin": "20", "max_coins": "1"}
    assert ledger.balance(DEVICE).can_convert == 1
    conversion, balance, _ = ledger.convert(DEVICE, "conv-0001", None)
    assert (conversion["stars_spent"], conversion["stars_per_coin"]) == (20, 20)
    # Old conversions keep the rate they were made at.
    ledger.update({"stars_per_coin": None})
    assert ledger.rules().stars_per_coin == 10
    assert ledger.balance(DEVICE).stars == 20
    ledger.update({"enabled": False})
    assert ledger.rules().enabled is False
    with pytest.raises(ValueError):
        ledger.update({"stars_per_coin": 0})
    with pytest.raises(ValueError):
        ledger.update({"colour": "gold"})
    assert ledger.rules().enabled is False  # a bad update changes nothing
    assert ledger.update({}, reset=True)["overrides"] == {}
    assert ledger.rules() == CoinRules()


def test_overrides_survive_a_restart(tmp_path):
    path = tmp_path / "d.sqlite3"
    CoinLedger(DeviceStore(sqlite_path=path)).update({"stars_per_coin": 5})
    assert CoinLedger(DeviceStore(sqlite_path=path)).rules().stars_per_coin == 5


# --- endpoints ---------------------------------------------------------------------------------


@pytest.fixture
async def client(store):
    app = create_app(access_key="app-key", store=store, admin_key="admin-secret", report_key="report-secret")
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_balance_endpoint_needs_the_app_key(client, store):
    give_stars(store, count=14)
    assert (await client.get(f"/coins?device={DEVICE}")).status == 401
    assert (await client.get("/coins?device=bad id", headers=APP)).status == 400
    body = await (await client.get(f"/coins?device={DEVICE}", headers=APP)).json()
    assert body == {
        "enabled": True, "stars_per_coin": 10, "min_coins": 1, "max_coins": 0,
        "stars_earned": 14, "stars_spent": 0, "stars": 14, "coins": 0, "can_convert": 1, "leftover_stars": 4,
    }


async def test_convert_endpoint(client, store):
    give_stars(store, count=25)
    request = {"device": DEVICE, "id": "conv-aaaa-0001", "coins": 2, "stars_per_coin": 10}
    assert (await client.post("/coins/convert", json=request)).status == 401
    resp = await client.post("/coins/convert", json=request, headers=APP)
    assert resp.status == 200
    body = await resp.json()
    assert (body["coins"], body["stars"], body["stars_spent"]) == (2, 5, 20)
    assert body["conversion"]["coins"] == 2 and body["conversion"]["replayed"] is False

    again = await (await client.post("/coins/convert", json=request, headers=APP)).json()
    assert again["conversion"]["replayed"] is True and again["coins"] == 2

    broke = await client.post("/coins/convert", json={**request, "id": "conv-aaaa-0002", "coins": 1}, headers=APP)
    assert broke.status == 409
    refused = await broke.json()
    assert refused["error"] == "not_enough_stars" and refused["stars"] == 5 and "message" in refused


async def test_convert_rejects_bad_requests(client, store):
    give_stars(store, count=25)
    for bad in (
        {"id": "conv-aaaa-0001"},
        {"device": "anonymous", "id": "conv-aaaa-0001"},
        {"device": DEVICE, "id": "conv-aaaa-0001", "coins": "2"},
        {"device": DEVICE, "id": "conv-aaaa-0001", "coins": True},
        {"device": DEVICE, "id": "conv-aaaa-0001", "stars_per_coin": 1.5},
        {"device": DEVICE, "id": "x"},
    ):
        assert (await client.post("/coins/convert", json=bad, headers=APP)).status == 400, bad
    assert (await client.post("/coins/convert", data="nope", headers=APP)).status == 400


async def test_admin_controls_the_rules(client, store):
    give_stars(store, count=25)
    assert (await client.get("/admin/coins", headers=APP)).status == 401
    assert (await client.post("/admin/coins", json={"enabled": False}, headers=REPORT)).status == 401
    start = await (await client.get("/admin/coins", headers=ADMIN)).json()
    assert start["settings"]["stars_per_coin"] == 10 and start["overrides"] == {}

    off = await client.post("/admin/coins", json={"enabled": False}, headers=ADMIN)
    assert (await off.json())["settings"]["enabled"] is False
    request = {"device": DEVICE, "id": "conv-aaaa-0001", "coins": 1}
    refused = await client.post("/coins/convert", json=request, headers=APP)
    assert refused.status == 403 and (await refused.json())["enabled"] is False

    await client.post("/admin/coins", json={"enabled": True, "stars_per_coin": 5}, headers=ADMIN)
    body = await (await client.get(f"/coins?device={DEVICE}", headers=APP)).json()
    assert (body["stars_per_coin"], body["can_convert"]) == (5, 5)
    stale = await client.post("/coins/convert", json={**request, "stars_per_coin": 10}, headers=APP)
    assert stale.status == 409 and (await stale.json())["error"] == "rate_changed"

    assert (await client.post("/admin/coins", json={"stars_per_coin": -1}, headers=ADMIN)).status == 400
    reset = await (await client.post("/admin/coins", json={"reset": True}, headers=ADMIN)).json()
    assert reset["settings"] == CoinRules().as_json()



async def test_parallel_taps_cannot_spend_the_same_stars(client, store):
    give_stars(store, count=10)
    taps = [
        client.post("/coins/convert", json={"device": DEVICE, "id": f"conv-tap-{i:04d}", "coins": 1}, headers=APP)
        for i in range(5)
    ]
    statuses = sorted([r.status for r in await asyncio.gather(*taps)])
    assert statuses == [200, 409, 409, 409, 409]
    assert (await (await client.get(f"/coins?device={DEVICE}", headers=APP)).json())["coins"] == 1
