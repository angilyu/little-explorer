"""Tiny token endpoint so the app never holds LiveKit secrets.

``POST /token`` with an optional ``{"context": {"book": ..., "page": ..., ...}}`` body returns
``{"serverUrl", "participantToken", "roomName"}``. The token dispatches the ``little-explorer``
agent into a fresh room and carries the opening page as participant attributes, so Milo knows the
page from the first word. An optional ``"pip_mode": "pipeline" | "realtime"`` picks Milo's voice
engine for that room (otherwise the agent's ``PIP_MODE`` applies), and ``"intro": "app"`` says the
app already played Milo's recorded intro, so the agent skips its greeting. Later page turns arrive as JSON
on the ``context`` data topic. When ``PIP_ACCESS_KEY`` is set, ``/token`` needs
``Authorization: Bearer <key>``, so a public deployment can't be used to run up the OpenAI bill.

The app also sends ``"device": {"id", "name", "model"}``. Each device has a daily allowance (see
:mod:`.devices`): a blocked device gets ``403 {"error": "blocked"}``, one that has used up today
gets ``429 {"error": "limit"}``, both with a kid-friendly ``message``. Otherwise the participant
identity is ``device-<id>`` and the agent learns the device and its remaining seconds through the
dispatch metadata. Requests without a device count as the shared ``anonymous`` device.

Stars (see :mod:`.stars`): the agent backs each award up with ``POST /stars`` (report key), and
the app restores them after a reinstall with ``GET /stars?device=<id>`` (access key), which
returns ``{"stars": [...], "total": N}``. Awards carry counts and labels only, never transcripts.

``POST /usage`` (``Authorization: Bearer $PIP_REPORT_KEY``) is how the agent reports each finished
session. ``/admin/...`` (``Authorization: Bearer $PIP_ADMIN_KEY``) is for the owner, usually via
``tools/devices.py``: ``GET /admin/devices``, ``GET /admin/usage?days=7`` and
``POST /admin/devices/<id or name>`` with ``{"status", "label", "daily_limit_min"}``.

It also serves the book content (see :mod:`.catalog`): ``GET /content/manifest.json`` and
``GET /content/<path>`` for each file in it, so books can change without an app update.

Run: ``uv run python -m little_explorer_agent.token_server``.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

from aiohttp import web

from . import agent_name
from .catalog import build_manifest, content_files, resources_dir
from .coins import CoinError, CoinLedger
from .config import load_env
from .content import BOOK_KEY, CITY_KEY, PAGE_KEY, PAGE_NUMBER_KEY, PAGE_TEXT_KEY
from .devices import ANONYMOUS, REST_MESSAGE, STATUSES, Decision, DeviceStore, valid_device_id
from .modes import PipMode, job_metadata, parse_intro, parse_mode

log = logging.getLogger("little_explorer.tokens")

TOKEN_TTL = timedelta(minutes=15)
CONTEXT_KEYS = (BOOK_KEY, PAGE_KEY, PAGE_NUMBER_KEY, CITY_KEY, PAGE_TEXT_KEY)
MAX_CONTEXT_VALUE = 400


def clean_context(raw: object) -> dict[str, str]:
    """Only the known page keys, as short strings."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: v[:MAX_CONTEXT_VALUE]
        for k, v in raw.items()
        if k in CONTEXT_KEYS and isinstance(v, str) and v
    }


def credentials(
    identity: str | None = None,
    context: dict[str, str] | None = None,
    mode: PipMode | None = None,
    metadata: str | None = None,
) -> dict[str, str]:
    from .voice.tokens import mint_token

    url = os.environ.get("LIVEKIT_URL", "").strip()
    if not url or not os.environ.get("LIVEKIT_API_KEY") or not os.environ.get("LIVEKIT_API_SECRET"):
        raise RuntimeError("LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set (see server/.env.example)")
    room = f"little-explorer-{uuid.uuid4().hex[:10]}"
    identity = identity or f"explorer-{uuid.uuid4().hex[:8]}"
    token = mint_token(
        room=room,
        identity=identity,
        name="Little Explorer",
        attributes=context or None,
        agent_name=agent_name(),
        agent_metadata=metadata if metadata is not None else job_metadata(mode),
        ttl=TOKEN_TTL,
    )
    return {"serverUrl": url, "participantToken": token, "roomName": room}


def authorized(request: web.Request, access_key: str, *, required: bool = False) -> bool:
    """Bearer check. An unset key lets everyone in, unless ``required`` (then nobody)."""
    if not access_key:
        return not required
    header = request.headers.get("Authorization", "")
    scheme, _, supplied = header.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(supplied.strip().encode(), access_key.encode())


async def handle_token(request: web.Request) -> web.Response:
    if not authorized(request, request.app[ACCESS_KEY]):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json() if request.can_read_body else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    identity = body.get("identity")
    if identity is not None and (not isinstance(identity, str) or not identity.isascii() or len(identity) > 64):
        return web.json_response({"error": "bad identity"}, status=400)
    mode = parse_mode(body.get("pip_mode"))
    if body.get("pip_mode") is not None and mode is None:
        return web.json_response({"error": "bad pip_mode"}, status=400)
    device = parse_device(body.get("device"))
    if device is None:
        return web.json_response({"error": "bad device"}, status=400)
    device_id, name, model = device
    decision = await check_device(request.app[STORE], device_id, name, model)
    if not decision.allowed:
        log.info("refused device %s: %s", device_id, decision.reason)
        status = 403 if decision.reason == "blocked" else 429
        return web.json_response({"error": decision.reason, "message": REST_MESSAGE}, status=status)
    if device_id != ANONYMOUS or identity is None:
        identity = f"device-{device_id}"
    try:
        payload = credentials(
            identity,
            clean_context(body.get("context")),
            metadata=job_metadata(mode, device_id, decision.remaining_s, parse_intro(body.get("intro"))),
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return web.json_response({"error": "token server is not configured"}, status=500)
    log.info("token for room %s, device %s, %.0f s left today", payload["roomName"], device_id, decision.remaining_s)
    return web.json_response(payload)


def parse_device(raw: object) -> tuple[str, str, str] | None:
    """``(id, name, model)`` from the request, ``anonymous`` if absent, None if malformed."""
    if raw is None:
        return ANONYMOUS, "", ""
    if not isinstance(raw, dict) or not valid_device_id(raw.get("id")):
        return None
    name, model = raw.get("name"), raw.get("model")
    return raw["id"], name if isinstance(name, str) else "", model if isinstance(model, str) else ""


async def check_device(store: DeviceStore, device_id: str, name: str, model: str) -> Decision:
    try:
        return await asyncio.to_thread(store.check, device_id, name, model)
    except Exception:
        # Fail open so a database hiccup doesn't silence Milo, but only for an unknown device's day.
        log.exception("device check failed; allowing a short session")
        return Decision(True, "ok", store.limits.unknown_min * 60)


async def _json_body(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def handle_usage(request: web.Request) -> web.Response:
    if not authorized(request, request.app[REPORT_KEY], required=True):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    try:
        device = await asyncio.to_thread(request.app[STORE].record_session, body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    log.info("usage: room %s, device %s, %.0f s", body.get("room"), device.id, float(body.get("duration_s") or 0))
    return web.json_response({"ok": True, "device_status": device.status})


async def handle_star_report(request: web.Request) -> web.Response:
    if not authorized(request, request.app[REPORT_KEY], required=True):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    try:
        new = await asyncio.to_thread(request.app[STORE].record_star, body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    log.info("star: device %s on %s (%s)", body.get("device_id"), body.get("book"), "new" if new else "not stored")
    return web.json_response({"ok": True, "new": new})


async def handle_stars(request: web.Request) -> web.Response:
    if not authorized(request, request.app[ACCESS_KEY]):
        return web.json_response({"error": "unauthorized"}, status=401)
    device_id = request.query.get("device", "")
    if not valid_device_id(device_id):
        return web.json_response({"error": "bad device"}, status=400)
    stars = await asyncio.to_thread(request.app[STORE].stars_for, device_id)
    return web.json_response({"stars": stars, "total": sum(s["stars"] for s in stars)})


async def handle_coins(request: web.Request) -> web.Response:
    if not authorized(request, request.app[ACCESS_KEY]):
        return web.json_response({"error": "unauthorized"}, status=401)
    device_id = request.query.get("device", "")
    if not valid_device_id(device_id):
        return web.json_response({"error": "bad device"}, status=400)
    balance = await asyncio.to_thread(request.app[COINS].balance, device_id)
    return web.json_response(balance.as_json())


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def handle_convert(request: web.Request) -> web.Response:
    if not authorized(request, request.app[ACCESS_KEY]):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    device_id = body.get("device")
    if not valid_device_id(device_id) or device_id == ANONYMOUS:
        return web.json_response({"error": "bad device"}, status=400)
    coins, rate = body.get("coins"), body.get("stars_per_coin")
    if (coins is not None and _whole(coins) is None) or (rate is not None and _whole(rate) is None):
        return web.json_response({"error": "coins and stars_per_coin must be whole numbers"}, status=400)
    ledger = request.app[COINS]
    try:
        conversion, balance, replayed = await asyncio.to_thread(ledger.convert, device_id, body.get("id"), coins, rate)
    except CoinError as exc:
        balance = await asyncio.to_thread(ledger.balance, device_id)
        log.info("coins: device %s refused (%s)", device_id, exc.code)
        return web.json_response({"error": exc.code, "message": exc.message, **balance.as_json()}, status=exc.status)
    if not replayed:
        log.info("coins: device %s turned %d stars into %d coins", device_id, conversion["stars_spent"], conversion["coins"])
    return web.json_response({**balance.as_json(), "conversion": {**conversion, "replayed": replayed}})


def _admin(request: web.Request) -> web.Response | None:
    if not authorized(request, request.app[ADMIN_KEY], required=True):
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def handle_admin_devices(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    store = request.app[STORE]
    return web.json_response(
        {
            "day": store.day_of(),
            "limits": {"unknown_min": store.limits.unknown_min, "approved_min": store.limits.approved_min},
            "devices": await asyncio.to_thread(store.summary),
        }
    )


async def handle_admin_usage(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    try:
        days = min(max(int(request.query.get("days", "7")), 1), 366)
    except ValueError:
        return web.json_response({"error": "days must be a number"}, status=400)
    return web.json_response({"days": await asyncio.to_thread(request.app[STORE].usage_by_day, days)})


async def handle_admin_update(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    status = body.get("status")
    if status is not None and status not in STATUSES:
        return web.json_response({"error": f"status must be one of {', '.join(STATUSES)}"}, status=400)
    label = body.get("label")
    if label is not None and not isinstance(label, str):
        return web.json_response({"error": "label must be text"}, status=400)
    clear_limit = "daily_limit_min" in body and body["daily_limit_min"] is None
    limit = body.get("daily_limit_min")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float))):
        return web.json_response({"error": "daily_limit_min must be a number or null"}, status=400)
    try:
        device = await asyncio.to_thread(
            request.app[STORE].update,
            request.match_info["ref"],
            status=status,
            label=label,
            daily_limit_min=limit,
            clear_limit=clear_limit,
        )
    except LookupError as exc:
        return web.json_response({"error": str(exc).strip("'\"")}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"device": {**device.__dict__, "display_name": device.display_name}})


async def handle_admin_coins(request: web.Request) -> web.Response:
    """``GET``: the coin rules in force, the env defaults and the overrides. ``POST``: change them,
    e.g. ``{"stars_per_coin": 20}``, ``{"enabled": false}``, ``{"max_coins": null}`` (back to the
    env default) or ``{"reset": true}`` (every override)."""
    if denied := _admin(request):
        return denied
    ledger = request.app[COINS]
    if request.method == "GET":
        return web.json_response(await asyncio.to_thread(ledger.settings))
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    reset = body.pop("reset", False) is True
    try:
        settings = await asyncio.to_thread(ledger.update, body, reset=reset)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    log.info("coin rules now %s", settings["settings"])
    return web.json_response(settings)


async def handle_health(request: web.Request) -> web.Response:
    # The deploy workflow polls this until the new commit and book content are live.
    commit = os.environ.get("RENDER_GIT_COMMIT", "").strip() or None
    return web.json_response({"ok": True, "commit": commit, "content": request.app[MANIFEST]["version"]})


async def handle_manifest(request: web.Request) -> web.Response:
    manifest = request.app[MANIFEST]
    headers = {"Cache-Control": "no-cache", "ETag": f'"{manifest["version"]}"'}
    if request.headers.get("If-None-Match") == headers["ETag"]:
        return web.Response(status=304, headers=headers)
    return web.json_response(manifest, headers=headers)


async def handle_content_file(request: web.Request) -> web.StreamResponse:
    path = request.app[FILES].get(request.match_info["path"])
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "no-cache"})


async def _keepalive(url: str, every: float) -> None:
    """Free hosts sleep when idle; pinging our own public URL keeps Milo ready to talk."""
    from aiohttp import ClientSession, ClientTimeout

    async with ClientSession(timeout=ClientTimeout(total=30)) as session:
        while True:
            await asyncio.sleep(every)
            try:
                async with session.get(url) as resp:
                    log.debug("keepalive %s -> %s", url, resp.status)
            except Exception as exc:  # noqa: BLE001 - keep pinging
                log.warning("keepalive failed: %s", exc)


def keepalive_target(environ: dict[str, str] | os._Environ[str] = os.environ) -> tuple[str, float] | None:
    every = float(environ.get("KEEPALIVE_SECONDS", "0") or 0)
    base = (environ.get("KEEPALIVE_URL") or environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    if every <= 0 or not base:
        return None
    return f"{base}/healthz", every


ACCESS_KEY = web.AppKey("access_key", str)
ADMIN_KEY = web.AppKey("admin_key", str)
REPORT_KEY = web.AppKey("report_key", str)
STORE = web.AppKey("device_store", DeviceStore)
COINS = web.AppKey("coin_ledger", CoinLedger)
MANIFEST = web.AppKey("manifest", dict)
FILES = web.AppKey("files", dict)


def create_app(
    *,
    access_key: str | None = None,
    content_root: Path | None = None,
    store: DeviceStore | None = None,
    admin_key: str | None = None,
    report_key: str | None = None,
    coins: CoinLedger | None = None,
) -> web.Application:
    app = web.Application()
    app[ACCESS_KEY] = (os.environ.get("PIP_ACCESS_KEY", "") if access_key is None else access_key).strip()
    app[ADMIN_KEY] = (os.environ.get("PIP_ADMIN_KEY", "") if admin_key is None else admin_key).strip()
    app[REPORT_KEY] = (os.environ.get("PIP_REPORT_KEY", "") if report_key is None else report_key).strip()
    app[STORE] = store or DeviceStore.from_env()
    app[COINS] = coins or CoinLedger.from_env(app[STORE])
    root = content_root or resources_dir()
    app[MANIFEST] = build_manifest(root)
    app[FILES] = content_files(root)
    app.router.add_post("/token", handle_token)
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/content/manifest.json", handle_manifest)
    app.router.add_get("/content/{path:.+}", handle_content_file)
    app.router.add_post("/usage", handle_usage)
    app.router.add_post("/stars", handle_star_report)
    app.router.add_get("/stars", handle_stars)
    app.router.add_get("/coins", handle_coins)
    app.router.add_post("/coins/convert", handle_convert)
    app.router.add_get("/admin/devices", handle_admin_devices)
    app.router.add_get("/admin/usage", handle_admin_usage)
    app.router.add_post("/admin/devices/{ref}", handle_admin_update)
    app.router.add_get("/admin/coins", handle_admin_coins)
    app.router.add_post("/admin/coins", handle_admin_coins)

    target = keepalive_target()
    if target:

        async def start_keepalive(app: web.Application) -> AsyncIterator[None]:
            task = asyncio.create_task(_keepalive(*target))
            yield
            task.cancel()

        app.cleanup_ctx.append(start_keepalive)
    return app


def main() -> None:
    load_env()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    # Hosts like Render pass PORT and need the server on all interfaces.
    port = int(os.environ.get("PORT") or os.environ.get("TOKEN_SERVER_PORT", "8787"))
    host = os.environ.get("TOKEN_SERVER_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    app = create_app()
    if not app[ACCESS_KEY] and host != "127.0.0.1":
        log.warning("PIP_ACCESS_KEY is not set: anyone who can reach this server can start Milo sessions")
    log.info("serving content version %s (%d files)", app[MANIFEST]["version"], len(app[FILES]))
    log.info("device limits in %s storage: %s", app[STORE].kind, app[STORE].limits)
    log.info("coin defaults (env): %s", app[COINS].defaults)
    if not app[ADMIN_KEY] or not app[REPORT_KEY]:
        log.warning("PIP_ADMIN_KEY or PIP_REPORT_KEY is not set: /admin or /usage is turned off")
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
