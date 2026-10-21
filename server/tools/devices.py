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
    uv run python tools/devices.py accounts            # every grown-up account and its totals
    uv run python tools/devices.py account mom@example.com            # devices, stars, coins, books
    uv run python tools/devices.py create-account mom@example.com --all-books
    uv run python tools/devices.py link mom@example.com 3F2A...       # the device's stars join the account
    uv run python tools/devices.py link mom@example.com 3F2A... --no-claim   # ...or are left out
    uv run python tools/devices.py unlink mom@example.com 3F2A...
    uv run python tools/devices.py grant mom@example.com hawaii lake-tahoe   # show more books
    uv run python tools/devices.py revoke mom@example.com hawaii
    uv run python tools/devices.py all-books mom@example.com on       # every book, now and later
    uv run python tools/devices.py backup              # every table to backups/<time>.json
    uv run python tools/devices.py restore backups/<time>.json        # asks before replacing all data

``--direct`` skips the token server and uses ``DATABASE_URL`` (or the local SQLite file) directly.
The app shows its device id in the Grown-ups corner. Accounts take an email or an account id.
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


    def accounts(self) -> list[dict[str, Any]]:
        if self.direct:
            return self._accounts().summary()
        return self._call("GET", "/admin/accounts")["accounts"]

    def account(self, ref: str) -> dict[str, Any]:
        if self.direct:
            return self._detail(ref)
        return self._call("GET", "/admin/accounts/" + urllib.parse.quote(ref, safe=""))

    def create_account(self, email: str, all_books: bool) -> dict[str, Any]:
        if self.direct:
            try:
                account, created = self._accounts().create(email, all_books=all_books)
            except ValueError as exc:
                raise SystemExit(str(exc)) from None
            return {**self._detail(account.id), "created": created}
        return self._call("POST", "/admin/accounts", {"email": email, "all_books": all_books})

    def account_action(self, ref: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.direct:
            accounts = self._accounts()
            try:
                if action == "link":
                    accounts.link(ref, body["device"], body.get("claim", True))
                elif action == "unlink":
                    accounts.unlink(body["device"])
                else:
                    accounts.set_books(
                        ref, grant=body.get("grant") or [], revoke=body.get("revoke") or [], all_books=body.get("all_books")
                    )
            except (LookupError, ValueError) as exc:
                raise SystemExit(str(exc) or "no such account") from None
            return self._detail(ref)
        return self._call("POST", f"/admin/accounts/{urllib.parse.quote(ref, safe='')}/{action}", body)

    def backup(self) -> dict[str, Any]:
        if self.direct:
            return json.loads(json.dumps(self.store.backup(), default=str))
        return self._call("GET", "/admin/backup")

    def restore(self, dump: dict[str, Any]) -> dict[str, Any]:
        if self.direct:
            try:
                return {"ok": True, "restored": self.store.restore(dump)}
            except ValueError as exc:
                raise SystemExit(str(exc)) from None
        return self._call("POST", "/admin/restore", {"confirm": "restore", "backup": dump})

    def _accounts(self) -> Any:
        from little_explorer_agent.accounts import Accounts

        if not hasattr(self, "_account_book"):
            self._account_book = Accounts.from_env(self.store)
        return self._account_book

    def _detail(self, ref: str) -> dict[str, Any]:
        from little_explorer_agent.coins import CoinLedger
        from little_explorer_agent.devices import Scope

        accounts = self._accounts()
        account = accounts.get(ref)
        if not account:
            raise SystemExit("no such account")
        scope = Scope(account_id=account.id)
        stars = self.store.stars_of(scope)
        return {
            "account": {**account.as_json(), "created_at": account.created_at},
            "devices": accounts.devices_of(account.id),
            "stars": sum(s["stars"] for s in stars),
            "star_days": len({str(s["awarded_at"])[:10] for s in stars}),
            "coins": CoinLedger.from_env(self.store).balance_of(scope).as_json(),
            "hearts": len(accounts.hearts(account.id)),
        }


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


def show_accounts(accounts: list[dict[str, Any]]) -> str:
    if not accounts:
        return "No accounts yet."
    rows = [["EMAIL", "BOOKS", "DEVICES", "STARS", "COINS", "HEARTS", "CREATED", "ID"]]
    for a in accounts:
        rows.append(
            [
                a["email"],
                "all" if a["all_books"] else str(a["books"]),
                str(a["devices"]),
                str(a["stars"]),
                str(a["coins"]),
                str(a["hearts"]),
                str(a.get("created_at") or "")[:10],
                a["id"],
            ]
        )
    return _table(rows)


def show_account(d: dict[str, Any]) -> str:
    a, coins = d["account"], d["coins"]
    books = "every book" if a["all_books"] else ", ".join(a["books"]) or "none"
    lines = [
        ("Created" if d.get("created") else "Account") + f" {a['email']} ({a['id']})",
        f"Books: {books}",
        f"Stars: {d['stars']} earned on {d['star_days']} day(s); {coins['stars']} left after {coins['coins']} coin(s)",
        f"Hearts: {d['hearts']}",
    ]
    if d["devices"]:
        lines.append("Devices:")
        for dev in d["devices"]:
            state = "signed in" if dev["linked"] else "signed out"
            lines.append(f"  {dev['name']} ({dev['id']}): {state}" + (", its old stars are here" if dev["claimed"] else ""))
    else:
        lines.append("Devices: none")
    return "\n".join(lines)


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
    sub.add_parser("accounts", help="every grown-up account with its totals")
    account = sub.add_parser("account", help="one account: devices, books, stars, coins")
    account.add_argument("account", help="email or account id")
    create = sub.add_parser("create-account", help="make an account for an email (or show it if it exists)")
    create.add_argument("email")
    create.add_argument("--all-books", action="store_true", help="every book, now and later")
    for name, help_text in (
        ("link", "sign a device in to an account; its earlier stars and coins join the account"),
        ("unlink", "sign a device out of an account"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("account", help="email or account id")
        p.add_argument("device", help="device id")
        if name == "link":
            p.add_argument(
                "--no-claim", action="store_true", help="leave the device's earlier stars and coins out of the account"
            )
    for name, help_text in (("grant", "show more books on an account"), ("revoke", "hide books again")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("account", help="email or account id")
        p.add_argument("books", nargs="+", help="book ids, as in library.json")
    every = sub.add_parser("all-books", help="turn every-book access on or off")
    every.add_argument("account", help="email or account id")
    every.add_argument("state", choices=["on", "off"])
    backup = sub.add_parser("backup", help="save every table to a JSON file")
    backup.add_argument("file", nargs="?", help="default backups/<time>.json")
    restore = sub.add_parser("restore", help="replace ALL data with a backup file")
    restore.add_argument("file")
    restore.add_argument("--yes", action="store_true", help="don't ask first")
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
    if command == "accounts":
        rows = admin.accounts()
        print(json.dumps(rows, indent=2) if args.json else show_accounts(rows))
        return 0
    if command in ("account", "create-account", "link", "unlink", "grant", "revoke", "all-books"):
        if command == "account":
            data = admin.account(args.account)
        elif command == "create-account":
            data = admin.create_account(args.email, args.all_books)
        elif command == "link":
            data = admin.account_action(args.account, "link", {"device": args.device, "claim": not args.no_claim})
        elif command == "unlink":
            data = admin.account_action(args.account, "unlink", {"device": args.device})
        elif command == "all-books":
            data = admin.account_action(args.account, "books", {"all_books": args.state == "on"})
        else:
            data = admin.account_action(args.account, "books", {command: args.books})
        print(json.dumps(data, indent=2) if args.json else show_account(data))
        return 0
    if command == "backup":
        dump = admin.backup()
        path = Path(args.file or f"backups/{dump['taken_at'][:19].replace(':', '-')}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dump, indent=1, default=str))
        path.chmod(0o600)
        counts = ", ".join(f"{t} {len(v['rows'])}" for t, v in dump["tables"].items())
        print(f"Saved {path} ({counts})")
        return 0
    if command == "restore":
        dump = json.loads(Path(args.file).read_text())
        counts = ", ".join(f"{t} {len(v['rows'])}" for t, v in dump.get("tables", {}).items())
        print(f"This REPLACES all devices, stars, coins and accounts with {args.file} ({dump.get('taken_at')}): {counts}")
        if not args.yes and input("Type 'restore' to go on: ").strip() != "restore":
            print("Nothing changed.")
            return 1
        result = admin.restore(dump)
        print(f"Restored: {result['restored']}")
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
