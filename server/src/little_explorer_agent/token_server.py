"""Tiny token endpoint so the app never holds LiveKit secrets.

``POST /token`` with an optional ``{"context": {"book": ..., "page": ..., ...}}`` body returns
``{"serverUrl", "participantToken", "roomName"}``. The token dispatches the ``little-explorer``
agent into a fresh room and carries the opening page as participant attributes, so Pip knows the
page from the first word. Later page turns arrive as JSON on the ``context`` data topic. Run: ``uv run python -m little_explorer_agent.token_server``.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta

from aiohttp import web
from dotenv import load_dotenv

from . import AGENT_NAME
from .content import BOOK_KEY, CITY_KEY, PAGE_KEY, PAGE_NUMBER_KEY, PAGE_TEXT_KEY

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


def credentials(identity: str | None = None, context: dict[str, str] | None = None) -> dict[str, str]:
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
        ttl=TOKEN_TTL,
    )
    return {"serverUrl": url, "participantToken": token, "roomName": room}


async def handle_token(request: web.Request) -> web.Response:
    try:
        body = await request.json() if request.can_read_body else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    identity = body.get("identity")
    if identity is not None and (not isinstance(identity, str) or not identity.isascii() or len(identity) > 64):
        return web.json_response({"error": "bad identity"}, status=400)
    try:
        payload = credentials(identity, clean_context(body.get("context")))
    except RuntimeError as exc:
        log.error("%s", exc)
        return web.json_response({"error": "token server is not configured"}, status=500)
    log.info("token for room %s", payload["roomName"])
    return web.json_response(payload)


async def handle_health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/token", handle_token)
    app.router.add_get("/healthz", handle_health)
    return app


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    host = os.environ.get("TOKEN_SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("TOKEN_SERVER_PORT", "8787"))
    web.run_app(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
