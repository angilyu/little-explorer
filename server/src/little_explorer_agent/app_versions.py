"""Which app build is calling, and which builds are still supported (force upgrade).

From 1.1 the app sends ``X-App-Version`` (e.g. ``1.1``), ``X-App-Build`` (e.g. ``20261101.0930``)
and ``X-App-Channel`` (``appstore``, ``testflight`` or ``debug``) on every request. A request
without ``X-App-Version`` is a **legacy** client: the 1.0 build (or an older TestFlight build),
which must keep working exactly as before.

The owner's :class:`VersionPolicy` says which versions are still supported. Its env defaults
(``PIP_APP_MIN_VERSION`` and friends) can be overridden at runtime, without a deploy, through
``POST /admin/app-versions`` or ``tools/devices.py versions`` (stored in the ``settings`` table as
``app.<field>``, like the coin rules):

* ``min_version``: builds below it must update. The app asks ``GET /app/version`` at launch and
  when it comes back to the foreground and shows a pop-up it can't close (1.1 and later).
* ``recommended_version``: builds below it see a gentle "new version" prompt they can dismiss.
* ``testflight_min_build``: TestFlight builds below this build number must update too (TestFlight
  builds often share a version number, so they go by build).
* ``enforce``: ``off`` (default) only tells the app; ``versioned`` also refuses app API calls
  from clients that sent a version below the minimum (``426 {"error": "upgrade_required"}``);
  ``all`` refuses legacy (header-less, 1.0) clients too. 1.0 has no update pop-up, so ``all``
  just breaks it: Milo, sign-in and coins stop working with a generic error.

The agent learns the client from the dispatch metadata (``app_version``, ``app_build``,
``app_channel``; absent for legacy clients) and can gate new behavior with
:meth:`ClientInfo.at_least`.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

log = logging.getLogger("little_explorer.versions")

VERSION_HEADER = "X-App-Version"
BUILD_HEADER = "X-App-Build"
CHANNEL_HEADER = "X-App-Channel"
# What a header-less client is taken to be: the first App Store release.
LEGACY_VERSION = "1.0"
CHANNELS = ("appstore", "testflight", "debug")
ENFORCE_MODES = ("off", "versioned", "all")
# Little Explorer: City Books (Apple ID 6823828609, bundle com.angilyu.littleexplorer).
DEFAULT_STORE_URL = "https://apps.apple.com/app/id6823828609"
DEFAULT_TESTFLIGHT_URL = "itms-beta://"
SETTING_PREFIX = "app."
CACHE_S = 30.0

Status = Literal["ok", "recommended", "required"]
_PART = re.compile(r"^\d{1,9}$")


def parse_version(value: object) -> tuple[int, ...] | None:
    """``"1.2.3"`` to ``(1, 2, 3)`` (1 to 4 numeric parts); None if it isn't a version.
    Trailing zeros don't count, so ``1.1`` and ``1.1.0`` are the same version."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(".")
    if not 1 <= len(parts) <= 4 or not all(_PART.match(p) for p in parts):
        return None
    numbers = [int(p) for p in parts]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def _clean(value: object, limit: int = 32) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


@dataclass(frozen=True)
class ClientInfo:
    """The calling app build. ``legacy`` means it sent no version (1.0 and older builds)."""

    version: str = LEGACY_VERSION
    build: str = ""
    channel: str = ""
    legacy: bool = True

    @classmethod
    def from_values(cls, version: object, build: object = None, channel: object = None) -> ClientInfo:
        """A malformed or missing version counts as legacy, so nothing a client sends can break it."""
        if parse_version(version) is None:
            return cls()
        build_s = _clean(build)
        channel_s = _clean(channel).lower()
        return cls(
            version=_clean(version),
            build=build_s if parse_version(build_s) else "",
            channel=channel_s if channel_s in CHANNELS else "",
            legacy=False,
        )

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> ClientInfo:
        return cls.from_values(headers.get(VERSION_HEADER), headers.get(BUILD_HEADER), headers.get(CHANNEL_HEADER))

    @classmethod
    def from_metadata(cls, meta: Mapping[str, object]) -> ClientInfo:
        """The client from the agent's dispatch metadata (see :func:`modes.job_metadata`)."""
        return cls.from_values(meta.get("app_version"), meta.get("app_build"), meta.get("app_channel"))

    def at_least(self, version: str) -> bool:
        """Whether this build is ``version`` or newer, for gating new behavior on old clients."""
        mine, theirs = parse_version(self.version), parse_version(version)
        if theirs is None:
            raise ValueError(f"not a version: {version!r}")
        return mine is not None and mine >= theirs

    def metadata(self) -> dict[str, str]:
        """Fields for the agent's dispatch metadata: nothing for a legacy client, as before."""
        if self.legacy:
            return {}
        out = {"app_version": self.version}
        if self.build:
            out["app_build"] = self.build
        if self.channel:
            out["app_channel"] = self.channel
        return out

    def describe(self) -> str:
        """``1.1 (20261101.0930, testflight)``; ``1.0`` for a legacy client."""
        extra = ", ".join(p for p in (self.build, self.channel) if p)
        return f"{self.version} ({extra})" if extra else self.version


@dataclass(frozen=True)
class VersionPolicy:
    """Which app builds are supported. Empty versions mean no limit."""

    min_version: str = ""
    recommended_version: str = ""
    testflight_min_build: str = ""
    store_url: str = DEFAULT_STORE_URL
    testflight_url: str = DEFAULT_TESTFLIGHT_URL
    enforce: str = "off"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> VersionPolicy:
        env = os.environ if env is None else env
        names = {
            "min_version": "PIP_APP_MIN_VERSION",
            "recommended_version": "PIP_APP_RECOMMENDED_VERSION",
            "testflight_min_build": "PIP_APP_TESTFLIGHT_MIN_BUILD",
            "store_url": "PIP_APP_STORE_URL",
            "testflight_url": "PIP_APP_TESTFLIGHT_URL",
            "enforce": "PIP_APP_ENFORCE",
        }
        changes = {field: raw for field, name in names.items() if (raw := (env.get(name) or "").strip())}
        return cls().with_changes(changes)

    def with_changes(self, changes: Mapping[str, Any]) -> VersionPolicy:
        """A copy with ``changes`` applied; ``ValueError`` if any is invalid."""
        unknown = set(changes) - set(asdict(self))
        if unknown:
            raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        values = {key: _check(key, value) for key, value in changes.items()}
        return replace(self, **values)

    def as_json(self) -> dict[str, str]:
        return asdict(self)

    def status(self, client: ClientInfo) -> Status:
        """``required`` below the minimum (or, on TestFlight, below the minimum build),
        ``recommended`` below the recommended version, else ``ok``."""
        mine = parse_version(client.version)
        if mine is None:
            return "ok"
        if (floor := parse_version(self.min_version)) and mine < floor:
            return "required"
        if (
            client.channel == "testflight"
            and (min_build := parse_version(self.testflight_min_build))
            and (build := parse_version(client.build))
            and build < min_build
        ):
            return "required"
        if (soft := parse_version(self.recommended_version)) and mine < soft:
            return "recommended"
        return "ok"

    def update_url(self, client: ClientInfo) -> str:
        return self.testflight_url if client.channel == "testflight" else self.store_url

    def refuses(self, client: ClientInfo) -> bool:
        """Whether the server turns this client away (``426``), not just tells it to update."""
        if self.enforce == "off" or (client.legacy and self.enforce != "all"):
            return False
        return self.status(client) == "required"

    def answer(self, client: ClientInfo) -> dict[str, Any]:
        """``GET /app/version``: what the app shows. The words live in the app."""
        return {
            "status": self.status(client),
            "update_url": self.update_url(client) or None,
            "min_version": self.min_version or None,
            "recommended_version": self.recommended_version or None,
            "client": {"version": client.version, "build": client.build or None, "channel": client.channel or None,
                       "legacy": client.legacy},
        }


def _check(key: str, value: Any) -> str:
    text = value.strip() if isinstance(value, str) else str(value) if isinstance(value, (int, float)) else None
    if text is None or isinstance(value, bool):
        raise ValueError(f"{key} must be text")
    if key in ("min_version", "recommended_version", "testflight_min_build"):
        if text and parse_version(text) is None:
            raise ValueError(f"{key} must look like 1.2 or 1.2.3 (or be empty), not {text!r}")
    elif key in ("store_url", "testflight_url"):
        if text and (not re.match(r"^(https://|itms-apps://|itms-beta://)\S*$", text) or len(text) > 300):
            raise ValueError(f"{key} must be an https://, itms-apps:// or itms-beta:// link (or empty)")
    elif key == "enforce":
        text = text.lower()
        if text not in ENFORCE_MODES:
            raise ValueError(f"enforce must be one of {', '.join(ENFORCE_MODES)}")
    return text


class VersionSettings:
    """The policy in force: env defaults plus ``settings`` overrides, cached for :data:`CACHE_S`."""

    def __init__(self, store: Any, defaults: VersionPolicy | None = None, cache_s: float = CACHE_S) -> None:
        self.store = store
        self.defaults = defaults or VersionPolicy()
        self.cache_s = cache_s
        self._cached: tuple[float, VersionPolicy] | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, store: Any, env: Mapping[str, str] | None = None) -> VersionSettings:
        return cls(store, VersionPolicy.from_env(env))

    def _overrides(self, cur: Any) -> dict[str, str]:
        rows = self.store._exec(cur, "SELECT key, value FROM settings WHERE key LIKE ?", (SETTING_PREFIX + "%",))
        return {key[len(SETTING_PREFIX) :]: value for key, value in rows.fetchall()}

    def _policy(self, cur: Any) -> VersionPolicy:
        try:
            return self.defaults.with_changes(self._overrides(cur))
        except ValueError:
            log.warning("stored app version settings are invalid; using the env defaults")
            return self.defaults

    def policy(self) -> VersionPolicy:
        """The policy in force, read at most every :data:`CACHE_S` seconds. A database error falls
        back to the last known policy (or the env defaults), so no child is blocked by an outage. The
        fallback is cached too, so an outage costs one slow read per :data:`CACHE_S`, not one per request."""
        now = time.monotonic()
        with self._lock:
            if self._cached and now - self._cached[0] < self.cache_s:
                return self._cached[1]
        try:
            policy = self.store._run(self._policy)
        except Exception:
            log.exception("could not read app version settings")
            with self._lock:
                policy = self._cached[1] if self._cached else self.defaults
                self._cached = (time.monotonic(), policy)
            return policy
        with self._lock:
            self._cached = (now, policy)
        return policy

    def settings(self) -> dict[str, Any]:
        """The policy in force, the env defaults, and the database overrides."""

        def run(cur: Any) -> dict[str, Any]:
            return {
                "settings": self._policy(cur).as_json(),
                "defaults": self.defaults.as_json(),
                "overrides": self._overrides(cur),
            }

        return self.store._run(run)

    def update(self, changes: Mapping[str, Any], *, reset: bool = False) -> dict[str, Any]:
        """Store overrides (``None`` clears one; ``reset`` clears all), checked as a whole first."""

        def run(cur: Any) -> None:
            current = {} if reset else self._overrides(cur)
            merged = {**current, **{k: v for k, v in changes.items() if v is not None}}
            for key in (k for k, v in changes.items() if v is None):
                merged.pop(key, None)
            policy = self.defaults.with_changes(merged)  # raises ValueError before anything is written
            stamp = self.store.now().isoformat(timespec="seconds")
            self.store._exec(cur, "DELETE FROM settings WHERE key LIKE ?", (SETTING_PREFIX + "%",))
            for key in merged:
                self.store._exec(
                    cur,
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (SETTING_PREFIX + key, getattr(policy, key), stamp),
                )

        self.store.transaction(run)
        with self._lock:
            self._cached = None
        return self.settings()
