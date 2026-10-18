"""Turning stars into coins: the rules, and each device's coin ledger.

A grown-up converts his stars into coins from the app's Grown-ups corner (behind the parent gate). The
server owns the rules and the balance, so they survive a reinstall and an app build can't bend them:

* ``stars_per_coin`` (``PIP_STARS_PER_COIN``, default 10): how many stars make one coin.
* ``enabled`` (``PIP_COINS``, default on): whether conversions are allowed at all.
* ``min_coins`` / ``max_coins`` (``PIP_COINS_MIN`` 1, ``PIP_COINS_MAX`` 0 = no limit): coins per conversion.

The env values are the defaults. ``POST /admin/coins`` stores overrides in the database (they win
over the env, survive redeploys, and ``null`` clears one), so the owner can change the rules without
an app release or a redeploy.

Converted stars are *spent*: his spendable stars are every star he has earned (the ``stars`` table)
minus the stars spent on coins. Badges keep counting every star he earned. Each conversion carries a
client-chosen id, so a retried request never converts twice.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from typing import Any

CONVERSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
SETTING_PREFIX = "coins."
MAX_STARS_PER_COIN = 1000


class CoinError(Exception):
    """A conversion the rules refuse. ``status`` is the HTTP status the server answers with."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class CoinRules:
    enabled: bool = True
    stars_per_coin: int = 10
    min_coins: int = 1
    max_coins: int = 0  # 0 = no limit

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CoinRules:
        env = os.environ if env is None else env
        raw: dict[str, Any] = {}
        for name, key in (
            ("PIP_COINS", "enabled"),
            ("PIP_STARS_PER_COIN", "stars_per_coin"),
            ("PIP_COINS_MIN", "min_coins"),
            ("PIP_COINS_MAX", "max_coins"),
        ):
            value = (env.get(name) or "").strip()
            if value:
                raw[key] = value
        return cls().with_changes(raw)

    def with_changes(self, changes: Mapping[str, Any]) -> CoinRules:
        """A copy with ``changes`` applied; raises ``ValueError`` on an unknown key or bad value."""
        known = {f.name for f in fields(self)}
        unknown = set(changes) - known
        if unknown:
            raise ValueError(f"unknown coin setting(s): {', '.join(sorted(unknown))}")
        values = {k: _parse(k, v) for k, v in changes.items()}
        rules = replace(self, **values)
        rules.check()
        return rules

    def check(self) -> None:
        if not 1 <= self.stars_per_coin <= MAX_STARS_PER_COIN:
            raise ValueError(f"stars_per_coin must be 1 to {MAX_STARS_PER_COIN}")
        if self.min_coins < 1:
            raise ValueError("min_coins must be at least 1")
        if self.max_coins < 0:
            raise ValueError("max_coins must be 0 (no limit) or more")
        if self.max_coins and self.max_coins < self.min_coins:
            raise ValueError("max_coins must be 0 (no limit) or at least min_coins")

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def _parse(key: str, value: Any) -> bool | int:
    if key == "enabled":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "on", "yes"):
            return True
        if text in ("0", "false", "off", "no"):
            return False
        raise ValueError("enabled must be true or false")
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a whole number")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise ValueError(f"{key} must be a whole number")
    return value


@dataclass(frozen=True)
class Balance:
    """What a device has, and what a conversion could give right now under ``rules``."""

    rules: CoinRules
    stars_earned: int
    stars_spent: int
    coins: int

    @property
    def stars(self) -> int:
        return max(self.stars_earned - self.stars_spent, 0)

    @property
    def can_convert(self) -> int:
        """Most coins one conversion may make now (0 if off or not enough stars)."""
        if not self.rules.enabled:
            return 0
        most = self.stars // self.rules.stars_per_coin
        if self.rules.max_coins:
            most = min(most, self.rules.max_coins)
        return most if most >= self.rules.min_coins else 0

    def as_json(self) -> dict[str, Any]:
        convert = self.can_convert
        return {
            **self.rules.as_json(),
            "stars_earned": self.stars_earned,
            "stars_spent": self.stars_spent,
            "stars": self.stars,
            "coins": self.coins,
            "can_convert": convert,
            "leftover_stars": self.stars - convert * self.rules.stars_per_coin,
        }


def plan(balance: Balance, coins: int | None, stars_per_coin: int | None = None) -> int:
    """How many coins this request makes, or a :class:`CoinError` saying why not.

    ``coins`` None means as many as allowed. ``stars_per_coin`` is the rate the grown-up saw; if
    the owner changed it since, the request is refused so nobody converts at a rate they didn't see."""
    rules = balance.rules
    if not rules.enabled:
        raise CoinError("disabled", "Turning stars into coins is switched off right now.", 403)
    if stars_per_coin is not None and stars_per_coin != rules.stars_per_coin:
        raise CoinError("rate_changed", f"The rate changed: it is now {rules.stars_per_coin} stars for 1 coin.")
    if coins is None:
        coins = balance.can_convert
        if coins == 0:
            raise CoinError("not_enough_stars", _need_more(rules))
    if coins < rules.min_coins:
        raise CoinError("too_few", f"Convert at least {rules.min_coins} coin(s) at a time.", 400)
    if rules.max_coins and coins > rules.max_coins:
        raise CoinError("too_many", f"Convert at most {rules.max_coins} coin(s) at a time.", 400)
    if coins * rules.stars_per_coin > balance.stars:
        raise CoinError("not_enough_stars", _need_more(rules))
    return coins


def _need_more(rules: CoinRules) -> str:
    return f"Not enough stars yet: {rules.min_coins * rules.stars_per_coin} stars make {rules.min_coins} coin(s)."


class CoinLedger:
    """Coin rules and conversions, stored in the :class:`~.devices.DeviceStore` database."""

    def __init__(self, store: Any, defaults: CoinRules | None = None) -> None:
        self.store = store
        self.defaults = defaults or CoinRules()

    @classmethod
    def from_env(cls, store: Any, env: Mapping[str, str] | None = None) -> CoinLedger:
        return cls(store, CoinRules.from_env(env))

    # -- rules ----------------------------------------------------------------------------------

    def _overrides(self, cur: Any) -> dict[str, str]:
        rows = self.store._exec(cur, "SELECT key, value FROM settings WHERE key LIKE ?", (SETTING_PREFIX + "%",))
        return {key[len(SETTING_PREFIX) :]: value for key, value in rows.fetchall()}

    def _rules(self, cur: Any) -> CoinRules:
        try:
            return self.defaults.with_changes(self._overrides(cur))
        except ValueError:
            # A stored override that no longer fits (e.g. with a new env default) falls back to the env.
            return self.defaults

    def rules(self) -> CoinRules:
        return self.store._run(self._rules)

    def settings(self) -> dict[str, Any]:
        """The rules in force, the env defaults, and the database overrides."""

        def run(cur: Any) -> dict[str, Any]:
            return {
                "settings": self._rules(cur).as_json(),
                "defaults": self.defaults.as_json(),
                "overrides": self._overrides(cur),
            }

        return self.store._run(run)

    def update(self, changes: Mapping[str, Any], *, reset: bool = False) -> dict[str, Any]:
        """Store overrides (``None`` clears one; ``reset`` clears all), checked as a whole first."""

        def run(cur: Any) -> None:
            current = {} if reset else self._overrides(cur)
            merged = {**current, **{k: v for k, v in changes.items() if v is not None}}
            for k in (k for k, v in changes.items() if v is None):
                merged.pop(k, None)
            self.defaults.with_changes(merged)  # raises ValueError before anything is written
            stamp = self.store.now().isoformat(timespec="seconds")
            self.store._exec(cur, "DELETE FROM settings WHERE key LIKE ?", (SETTING_PREFIX + "%",))
            for key, value in merged.items():
                stored = str(_parse(key, value)).lower() if key == "enabled" else str(_parse(key, value))
                self.store._exec(
                    cur,
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (SETTING_PREFIX + key, stored, stamp),
                )

        self.store._run(run)
        return self.settings()

    # -- balances ---------------------------------------------------------------------------------

    def _balance(self, cur: Any, device_id: str) -> Balance:
        earned = self.store._exec(
            cur, "SELECT COALESCE(SUM(stars), 0) FROM stars WHERE device_id = ?", (device_id,)
        ).fetchone()[0]
        spent, coins = self.store._exec(
            cur,
            "SELECT COALESCE(SUM(stars_spent), 0), COALESCE(SUM(coins), 0) FROM coin_conversions WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        return Balance(self._rules(cur), int(earned), int(spent), int(coins))

    def balance(self, device_id: str) -> Balance:
        return self.store._run(lambda cur: self._balance(cur, device_id))

    def convert(
        self,
        device_id: str,
        conversion_id: str,
        coins: int | None = None,
        stars_per_coin: int | None = None,
        now: datetime | None = None,
    ) -> tuple[dict[str, Any], Balance, bool]:
        """Spend stars on coins. Returns ``(conversion, balance after, replayed)``.

        The same ``conversion_id`` again returns the first result (``replayed``) and changes nothing.
        The check and the insert run under the store's lock, so two taps can't both spend the same
        stars (the token server is a single process)."""
        if not isinstance(conversion_id, str) or not CONVERSION_ID.match(conversion_id):
            raise CoinError("bad_id", "a conversion id of 8-64 letters, digits or dashes is required", 400)
        stamp = (now or self.store.now()).isoformat(timespec="seconds")

        def run(cur: Any) -> tuple[dict[str, Any], Balance, bool]:
            row = self.store._exec(
                cur,
                "SELECT id, device_id, converted_at, stars_spent, coins, stars_per_coin FROM coin_conversions WHERE id = ?",
                (conversion_id,),
            ).fetchone()
            if row:
                if row[1] != device_id:
                    raise CoinError("bad_id", "that conversion id belongs to another device", 409)
                return _conversion(row), self._balance(cur, device_id), True
            before = self._balance(cur, device_id)
            made = plan(before, coins, stars_per_coin)
            spent = made * before.rules.stars_per_coin
            values = (conversion_id, device_id, stamp, spent, made, before.rules.stars_per_coin)
            self.store._exec(
                cur,
                """INSERT INTO coin_conversions (id, device_id, converted_at, stars_spent, coins, stars_per_coin)
                VALUES (?, ?, ?, ?, ?, ?)""",
                values,
            )
            return _conversion(values), self._balance(cur, device_id), False

        return self.store._run(run)


def _conversion(row: tuple[Any, ...]) -> dict[str, Any]:
    keys = ("id", "device_id", "converted_at", "stars_spent", "coins", "stars_per_coin")
    item = dict(zip(keys, row))
    for k in ("stars_spent", "coins", "stars_per_coin"):
        item[k] = int(item[k])
    return item
