"""Tiny token endpoint so the app never holds LiveKit secrets.

``POST /token`` with an optional ``{"context": {"book": ..., "page": ..., ...}}`` body returns
``{"serverUrl", "participantToken", "roomName"}``. The token dispatches the ``little-explorer``
agent into a fresh room and carries the opening page as participant attributes, so Pip knows the
page from the first word. An optional ``"pip_mode": "pipeline" | "realtime"`` picks Pip's voice
engine for that room (otherwise the agent's ``PIP_MODE`` applies). Later page turns arrive as JSON
on the ``context`` data topic. When ``PIP_ACCESS_KEY`` is set, ``/token`` needs
``Authorization: Bearer <key>``, so a public deployment can't be used to run up the OpenAI bill.

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

from . import AGENT_NAME
from .catalog import build_manifest, content_files, resources_dir
from .config import load_env
from .content import BOOK_KEY, CITY_KEY, PAGE_KEY, PAGE_NUMBER_KEY, PAGE_TEXT_KEY
from .modes import PipMode, mode_metadata, parse_mode

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
    identity: str | None = None, context: dict[str, str] | None = None, mode: PipMode | None = None
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
        agent_name=AGENT_NAME,
        agent_metadata=mode_metadata(mode) if mode else None,
        ttl=TOKEN_TTL,
    )
    return {"serverUrl": url, "participantToken": token, "roomName": room}


def authorized(request: web.Request, access_key: str) -> bool:
    if not access_key:
        return True
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
    try:
        payload = credentials(identity, clean_context(body.get("context")), mode)
    except RuntimeError as exc:
        log.error("%s", exc)
        return web.json_response({"error": "token server is not configured"}, status=500)
    log.info("token for room %s", payload["roomName"])
    return web.json_response(payload)


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


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
    """Free hosts sleep when idle; pinging our own public URL keeps Pip ready to talk."""
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
MANIFEST = web.AppKey("manifest", dict)
FILES = web.AppKey("files", dict)


def create_app(*, access_key: str | None = None, content_root: Path | None = None) -> web.Application:
    app = web.Application()
    app[ACCESS_KEY] = (os.environ.get("PIP_ACCESS_KEY", "") if access_key is None else access_key).strip()
    root = content_root or resources_dir()
    app[MANIFEST] = build_manifest(root)
    app[FILES] = content_files(root)
    app.router.add_post("/token", handle_token)
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/content/manifest.json", handle_manifest)
    app.router.add_get("/content/{path:.+}", handle_content_file)

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
        log.warning("PIP_ACCESS_KEY is not set: anyone who can reach this server can start Pip sessions")
    log.info("serving content version %s (%d files)", app[MANIFEST]["version"], len(app[FILES]))
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
