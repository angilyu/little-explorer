"""See who talks to Milo, and approve, block or unblock devices.

Run from server/ (it reads ``PIP_SERVER_URL`` and ``PIP_ADMIN_KEY`` from the usual env file)::

    uv run python tools/devices.py                     # every device: today, totals, $
    uv run python tools/devices.py usage --days 7      # minutes and $ per day and device
    uv run python tools/devices.py approve "Leo's iPad" --label "Leo's iPad"
    uv run python tools/devices.py block 3F2A...       # by id or name
    uv run python tools/devices.py unblock 3F2A...
    uv run python tools/devices.py limit "Leo's iPad" 90   # minutes a day; "default" to reset
    uv run python tools/devices.py rename 3F2A... "Grandma's iPhone"
    uv run python tools/devices.py coins               # star-to-coin rules in force
    uv run python tools/devices.py coins rate=20 max=5 # change them; "off"/"on", "<key>=default"
    uv run python tools/devices.py coins reset         # back to the PIP_COINS* env defaults

``--direct`` skips the token server and uses ``DATABASE_URL`` (or the local SQLite file) directly.
The app shows its device id in the Grown-ups corner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from little_explorer_agent.config import load_env  # noqa: E402


class Admin:
    """The token server's ``/admin`` endpoints, or the database itself with ``direct``."""

    def __init__(self, server: str | None, key: str | None, direct: bool = False) -> None:
        self.direct = direct
        if direct:
            from little_explorer_agent.devices import DeviceStore

            self.store = DeviceStore.from_env()
            return
        if not server or not key:
            raise SystemExit("Set PIP_SERVER_URL and PIP_ADMIN_KEY (or use --direct).")
        self.server = server.rstrip("/")
        self.key = key

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.server + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read()).get("error", exc.reason)
            except ValueError:
                message = exc.reason
            raise SystemExit(f"{exc.code}: {message}") from None

    def devices(self) -> dict[str, Any]:
        if self.direct:
            s = self.store
            return {
                "day": s.day_of(),
                "limits": {"unknown_min": s.limits.unknown_min, "approved_min": s.limits.approved_min},
                "devices": s.summary(),
            }
        return self._call("GET", "/admin/devices")

    def usage(self, days: int) -> list[dict[str, Any]]:
        if self.direct:
            return self.store.usage_by_day(days)
        return self._call("GET", f"/admin/usage?days={days}")["days"]

    def update(self, ref: str, **changes: Any) -> dict[str, Any]:
        if self.direct:
            limit = changes.get("daily_limit_min", ...)
            try:
                device = self.store.update(
                    ref,
                    status=changes.get("status"),
                    label=changes.get("label"),
                    daily_limit_min=None if limit is ... else limit,
                    clear_limit=limit is None,
                )
            except (LookupError, ValueError) as exc:
                raise SystemExit(str(exc)) from None
            return {**device.__dict__, "display_name": device.display_name}
        return self._call("POST", "/admin/devices/" + urllib.parse.quote(ref, safe=""), changes)["device"]

    def coins(self, changes: dict[str, Any] | None = None, reset: bool = False) -> dict[str, Any]:
        if self.direct:
            from little_explorer_agent.coins import CoinLedger

            ledger = CoinLedger.from_env(self.store)
            if changes is None and not reset:
                return ledger.settings()
            try:
                return ledger.update(changes or {}, reset=reset)
            except ValueError as exc:
                raise SystemExit(str(exc)) from None
        if changes is None and not reset:
            return self._call("GET", "/admin/coins")
        return self._call("POST", "/admin/coins", {**(changes or {}), **({"reset": True} if reset else {})})


def _table(rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip() for r in rows)


def show_devices(data: dict[str, Any]) -> str:
    limits = data["limits"]
    head = (
        f"Today is {data['day']}. Daily limits: unknown {limits['unknown_min']:g} min "
        f"(blocked when used up), approved {limits['approved_min']:g} min."
    )
    if not data["devices"]:
        return head + "\nNo devices yet."
    rows = [["DEVICE", "STATUS", "TODAY", "TODAY $", "TOTAL", "TOTAL $", "SESSIONS", "STARS", "COINS", "LAST SEEN", "ID"]]
    for d in data["devices"]:
        name = d["display_name"] + (f" ({d['model']})" if d.get("model") else "")
        rows.append(
            [
                name[:40],
                d["status"],
                f"{d['today_min']:g}/{d['limit_min']:g} min",
                f"${d['today_usd']:.2f}",
                f"{d['total_min']:g} min",
                f"${d['total_usd']:.2f}",
                str(d["sessions"]),
                str(d.get("stars", 0)),
                str(d.get("coins", 0)),
                (d.get("last_seen") or "")[:16].replace("T", " "),
                d["id"],
            ]
        )
    return head + "\n\n" + _table(rows)


def show_usage(days: list[dict[str, Any]]) -> str:
    if not days:
        return "No sessions in that time."
    rows = [["DAY", "DEVICE", "SESSIONS", "MINUTES", "$"]]
    rows += [[d["day"], d["display_name"][:40], str(d["sessions"]), f"{d['minutes']:g}", f"${d['usd']:.2f}"] for d in days]
    return _table(rows)


COIN_KEYS = {"enabled": "enabled", "on": "enabled", "rate": "stars_per_coin", "stars_per_coin": "stars_per_coin",
             "min": "min_coins", "min_coins": "min_coins", "max": "max_coins", "max_coins": "max_coins"}


def coin_changes(items: list[str]) -> dict[str, Any]:
    """``rate=20 max=5 off`` to ``{"stars_per_coin": 20, "max_coins": 5, "enabled": False}``."""
    changes: dict[str, Any] = {}
    for item in items:
        if item in ("on", "off"):
            changes["enabled"] = item == "on"
            continue
        key, sep, value = item.partition("=")
        if not sep or key not in COIN_KEYS:
            raise SystemExit(f"Not a coin setting: {item!r}. Use rate=N, min=N, max=N (0 = no limit), on or off.")
        changes[COIN_KEYS[key]] = None if value == "default" else value
    return changes


def show_coins(data: dict[str, Any]) -> str:
    s = data["settings"]
    most = "no limit" if not s["max_coins"] else f"at most {s['max_coins']}"
    state = "on" if s["enabled"] else "OFF"
    lines = [
        f"Converting is {state}: {s['stars_per_coin']} stars make 1 coin, "
        f"at least {s['min_coins']} and {most} coin(s) at a time."
    ]
    overrides = data.get("overrides") or {}
    lines.append("Overrides: " + (", ".join(f"{k}={v}" for k, v in sorted(overrides.items())) or "none (env defaults)"))
    return "\n".join(lines)


def show_device(d: dict[str, Any]) -> str:
    limit = "default" if d.get("daily_limit_min") is None else f"{d['daily_limit_min']:g} min"
    return f"{d['display_name']} ({d['id']}): {d['status']}, daily limit {limit}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", help="token server URL (default $PIP_SERVER_URL)")
    parser.add_argument("--direct", action="store_true", help="use the database instead of the token server")
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("list", help="every device with today's and total minutes and $")
    usage = sub.add_parser("usage", help="minutes and $ per day and device")
    usage.add_argument("--days", type=int, default=7)
    for name, help_text in (
        ("approve", "allow the approved daily limit"),
        ("block", "refuse all sessions"),
        ("unblock", "back to unknown (not blocked again until tomorrow)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("device", help="device id or name")
        if name == "approve":
            p.add_argument("--label", help="a name to show for it")
    limit = sub.add_parser("limit", help="minutes a day for one device, or 'default'")
    limit.add_argument("device")
    limit.add_argument("minutes")
    rename = sub.add_parser("rename", help="the name to show for a device")
    rename.add_argument("device")
    rename.add_argument("label")
    coins = sub.add_parser("coins", help="star-to-coin rules: show, change (rate=20 max=5 off) or reset")
    coins.add_argument("changes", nargs="*", help="rate=N, min=N, max=N (0 = no limit), on, off, <key>=default, reset")
    args = parser.parse_args(argv)

    load_env()
    admin = Admin(
        args.server or os.environ.get("PIP_SERVER_URL"),
        os.environ.get("PIP_ADMIN_KEY"),
        direct=args.direct,
    )
    command = args.command or "list"
    if command == "list":
        data = admin.devices()
        print(json.dumps(data, indent=2) if args.json else show_devices(data))
        return 0
    if command == "usage":
        days = admin.usage(args.days)
        print(json.dumps(days, indent=2) if args.json else show_usage(days))
        return 0
    if command == "coins":
        reset = "reset" in args.changes
        items = [c for c in args.changes if c != "reset"]
        data = admin.coins(coin_changes(items) if items else None, reset=reset)
        print(json.dumps(data, indent=2) if args.json else show_coins(data))
        return 0
    changes: dict[str, Any]
    if command == "approve":
        changes = {"status": "approved"}
        if args.label:
            changes["label"] = args.label
    elif command == "block":
        changes = {"status": "blocked"}
    elif command == "unblock":
        changes = {"status": "unknown"}
    elif command == "rename":
        changes = {"label": args.label}
    else:
        if args.minutes.lower() == "default":
            changes = {"daily_limit_min": None}
        else:
            try:
                changes = {"daily_limit_min": float(args.minutes)}
            except ValueError:
                parser.error("minutes must be a number or 'default'")
    device = admin.update(args.device, **changes)
    print(json.dumps(device, indent=2) if args.json else show_device(device))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
