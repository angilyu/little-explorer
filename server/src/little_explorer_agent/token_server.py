"""Tiny token endpoint so the app never holds LiveKit secrets.

``POST /token`` with an optional ``{"context": {"book": ..., "page": ..., ...}}`` body returns
``{"serverUrl", "participantToken", "roomName"}``. The token dispatches the ``little-explorer``
agent into a fresh room and carries the opening page as participant attributes, so Milo knows the
page from the first word. An optional ``"pip_mode": "pipeline" | "realtime"`` picks Milo's voice
engine for that room (otherwise the agent's ``PIP_MODE`` applies), and ``"intro": "app"`` says the
app already played Milo's recorded intro, so the agent skips its greeting. Later page turns arrive as JSON
on the ``context`` data topic. When ``PIP_ACCESS_KEY`` is set, ``/token`` needs
``Authorization: Bearer <key>``, so a public deployment can't be used to run up the OpenAI bill.

The app sends its account session as ``X-Pip-Session`` and ``"device": {"id", "name", "model"}``.
Each account has a daily allowance shared by its devices (see :mod:`.devices`); an old build
without a session uses its device's linked account, or the device's own allowance if it has none.
A blocked account or device gets ``403 {"error": "blocked"}``, one that has used up today gets
``429 {"error": "limit"}``, both with a kid-friendly ``message``; a session that is no longer valid
gets ``401 {"error": "signed_out"}``. Otherwise the participant identity is ``device-<id>`` and the
agent learns the device, the account and the remaining seconds through the dispatch metadata.
Before any of that, a grown-up's choice about Milo counts: if the account (the session's, or for old
builds the device's linked account) has Milo turned off (``POST /account/milo-consent {"allow": false}``),
``/token`` answers ``403 {"error": "milo_off"}`` and no agent is dispatched. Accounts that never said
yes or no (``milo_consent`` null, which "Not now" leaves as it is) keep working here, so builds older than the
consent screen still work; current builds gate Milo themselves and don't ask for a token until the
account says yes. Requests without a device count as the
shared ``anonymous`` device. The app also sends ``"tz"``
(its IANA time zone), which the agent uses to count stars per local day.

Stars (see :mod:`.stars`): the agent backs each award up with ``POST /stars`` (report key), stamped
with the account, and reads the account's list with ``GET /stars?account=<id>`` (report key) when a
session starts, to keep the 5-stars-per-page daily limit across sessions. Old builds restore a
device's stars with ``GET /stars?device=<id>`` (access key); signed-in builds use
``/account/stars``. Both return ``{"stars": [...], "total": N}``. Awards carry counts and labels
only, never transcripts.

``POST /usage`` (``Authorization: Bearer $PIP_REPORT_KEY``) is how the agent reports each finished
session. ``/admin/...`` (``Authorization: Bearer $PIP_ADMIN_KEY``) is for the owner, usually via
``tools/devices.py``: ``GET /admin/devices``, ``GET /admin/usage?days=7``, ``GET /admin/reading?days=7``,
``POST /admin/accounts/<email or id>/milo`` with ``{"status", "daily_limit_min"}`` and, for old
builds on unlinked devices, ``POST /admin/devices/<id or name>`` with ``{"status", "label", "daily_limit_min"}``.

Accounts (see :mod:`.accounts`): a grown-up signs in with ``POST /auth/start {"email"}`` (emails a
6-digit code) and ``POST /auth/verify {"email", "code", "device"}``, which returns
``{"token", "account", "claimed_device"}``. The app sends the token as ``X-Pip-Session`` (with the
access key) to ``GET /account``, ``POST /auth/signout``, ``POST /account/delete {"code"}`` and the
account's ``/account/stars``, ``/account/coins``, ``/account/coins/convert``, ``/account/hearts`` and
``/account/import``, and ``POST /account/milo-consent {"allow": true | false}`` records a grown-up's
choice about Milo for the account (the account JSON carries it as ``milo_consent``).
``POST /account/reading {"events": [...]}`` stores a batch of the app's reading events (page views,
read-aloud use, a daily app open; see :mod:`.reading`) for the session's account and device, and
``GET /account/reading`` returns the account's reading progress for the badges (``{"progress": {"pages",
"milo_pages", "listened", "days"}}``).
``GET /account/library`` returns how a grown-up arranged the account's shelf (hidden books, the order
of the sections and of the books in each; see :mod:`.library`) as ``{"library": {...}}`` and
``PUT /account/library`` merges a device's changes into it and returns the result.
Errors are ``{"error": "bad_email" | "bad_code" | "expired" | "too_many" |
"signed_out" | "email_off" | "email_failed", ...}``. The owner manages accounts with
``/admin/accounts...`` and saves everything with ``GET /admin/backup``.

It also serves the book content (see :mod:`.catalog`): ``GET /content/manifest.json`` and
``GET /content/<path>`` for each file in it, so books and the Passport badges (``badges.json``, see
:mod:`.badges`) can change without an app update.

Made books (see :mod:`.bookgen`): ``POST /admin/books/generate`` with ``{"account", "title", "section",
"level", "note"}`` queues a book for an account that ``can_generate`` (``202 {"job", "usage"}``); the
in-process runner writes, illustrates and narrates it and publishes it into the manifest. Generated
books' JSON is served from memory and their photos and narration are ``302`` redirects to short-lived
presigned R2 URLs. ``GET /admin/books``, ``GET /admin/books/<id>``, ``POST /admin/books/<id>/publish |
unpublish | delete``, ``GET /admin/jobs``, ``GET /admin/jobs/<id>``, ``POST /admin/jobs/<id>/cancel``,
``GET | POST /admin/bookgen {"everyone"}`` and ``POST /admin/accounts/<email or id>/bookgen
{"can_generate"}`` manage it. ``GET /levels`` lists the reading levels for the parent screen.

Run: ``uv run python -m little_explorer_agent.token_server``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

from aiohttp import web

from . import agent_name
from .accounts import Accounts, AuthError, Session, normalize_email, valid_book_id
from .bookgen.service import BookgenService
from .bookgen.store import Asset, LimitError
from .bookgen.writer import validate_request
from .catalog import build_manifest, content_files, resources_dir
from .coins import CoinError, CoinLedger
from .config import load_env
from .content import BOOK_KEY, CITY_KEY, PAGE_KEY, PAGE_NUMBER_KEY, PAGE_TEXT_KEY
from .devices import ANONYMOUS, REST_MESSAGE, STATUSES, Decision, DeviceStore, Scope, valid_device_id
from .levels import levels_json
from .mail import Mailer, MailError, compose
from .modes import PipMode, job_metadata, parse_intro, parse_mode, parse_tz
from .library import LibraryError, LibraryPrefs
from .reading import ReadingError, ReadingLog

log = logging.getLogger("little_explorer.tokens")

TOKEN_TTL = timedelta(minutes=15)
MILO_OFF_MESSAGE = "A grown-up turned Milo off. Ask a grown-up to turn Milo on in the Grown-ups corner."
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
    account_id = None
    consent: bool | None = None
    if token := request.headers.get(SESSION_HEADER, "").strip():
        try:
            session = await asyncio.to_thread(request.app[ACCOUNTS].session, token)
        except Exception:
            log.exception("session check failed; going by the device")
        else:
            if session is None:
                return _signed_out()
            account_id = session.account.id
            consent = session.account.milo_consent
    if consent is False:
        return _milo_off(device_id, account_id)
    decision = await check_device(request.app[STORE], device_id, name, model, account_id)
    if not decision.allowed:
        log.info("refused device %s (account %s): %s", device_id, decision.account_id, decision.reason)
        status = 403 if decision.reason == "blocked" else 429
        return web.json_response({"error": decision.reason, "message": REST_MESSAGE}, status=status)
    if account_id is None and decision.account_id:
        # An old build without a session talks on its device's account, so that account's choice counts too.
        try:
            linked = await asyncio.to_thread(request.app[ACCOUNTS].get, decision.account_id)
        except Exception:
            log.exception("consent check failed for account %s", decision.account_id)
        else:
            if linked is not None and linked.milo_consent is False:
                return _milo_off(device_id, decision.account_id)
    if device_id != ANONYMOUS or identity is None:
        identity = f"device-{device_id}"
    try:
        payload = credentials(
            identity,
            clean_context(body.get("context")),
            metadata=job_metadata(
                mode,
                device_id,
                decision.remaining_s,
                parse_intro(body.get("intro")),
                parse_tz(body.get("tz")),
                decision.account_id,
            ),
        )
    except RuntimeError as exc:
        log.error("%s", exc)
        return web.json_response({"error": "token server is not configured"}, status=500)
    log.info(
        "token for room %s, device %s, account %s, %.0f s left today",
        payload["roomName"], device_id, decision.account_id, decision.remaining_s,
    )
    return web.json_response(payload)


def _milo_off(device_id: str, account_id: str | None) -> web.Response:
    log.info("refused device %s: a grown-up turned Milo off for account %s", device_id, account_id)
    return web.json_response({"error": "milo_off", "message": MILO_OFF_MESSAGE}, status=403)


def parse_device(raw: object) -> tuple[str, str, str] | None:
    """``(id, name, model)`` from the request, ``anonymous`` if absent, None if malformed."""
    if raw is None:
        return ANONYMOUS, "", ""
    if not isinstance(raw, dict) or not valid_device_id(raw.get("id")):
        return None
    name, model = raw.get("name"), raw.get("model")
    return raw["id"], name if isinstance(name, str) else "", model if isinstance(model, str) else ""


async def check_device(
    store: DeviceStore, device_id: str, name: str, model: str, account_id: str | None = None
) -> Decision:
    try:
        return await asyncio.to_thread(lambda: store.check(device_id, name, model, account_id=account_id))
    except Exception:
        # Fail open so a database hiccup doesn't silence Milo, but only for a new account's day.
        log.exception("device check failed; allowing a short session")
        return Decision(True, "ok", store.limits.unknown_min * 60, account_id=account_id)


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
    log.info(
        "star: device %s, account %s on %s (%s)",
        body.get("device_id"), body.get("account_id"), body.get("book"), "new" if new else "not stored",
    )
    return web.json_response({"ok": True, "new": new})


async def handle_stars(request: web.Request) -> web.Response:
    # Old apps restore a device's stars with the access key; Milo reads the per-page counts with the
    # report key, by account (only the agent may read an account's stars without its session).
    reporter = authorized(request, request.app[REPORT_KEY], required=True)
    if not (reporter or authorized(request, request.app[ACCESS_KEY])):
        return web.json_response({"error": "unauthorized"}, status=401)
    if "account" in request.query:
        account_id = request.query["account"]
        if not reporter:
            return web.json_response({"error": "unauthorized"}, status=401)
        if not valid_device_id(account_id):
            return web.json_response({"error": "bad account"}, status=400)
        stars = await asyncio.to_thread(request.app[STORE].stars_of, Scope(account_id=account_id))
        return web.json_response({"stars": stars, "total": sum(s["stars"] for s in stars)})
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


async def handle_admin_reading(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    try:
        days = min(max(int(request.query.get("days", "7")), 1), 366)
    except ValueError:
        return web.json_response({"error": "days must be a number"}, status=400)
    return web.json_response(await asyncio.to_thread(request.app[READING].report, days))


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


# -- accounts --------------------------------------------------------------------------------------

SESSION_HEADER = "X-Pip-Session"


def _client_ip(request: web.Request) -> str:
    # Render puts the caller first in X-Forwarded-For.
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded or request.remote or ""


def _refusal(exc: AuthError) -> web.Response:
    return web.json_response(exc.as_json(), status=exc.status)


def _signed_out() -> web.Response:
    return web.json_response({"error": "signed_out", "message": "sign in again"}, status=401)


async def _session(request: web.Request) -> Session | web.Response:
    if not authorized(request, request.app[ACCESS_KEY]):
        return web.json_response({"error": "unauthorized"}, status=401)
    token = request.headers.get(SESSION_HEADER, "").strip()
    session = await asyncio.to_thread(request.app[ACCOUNTS].session, token) if token else None
    return session or _signed_out()


async def handle_auth_start(request: web.Request) -> web.Response:
    if not authorized(request, request.app[ACCESS_KEY]):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    purpose = body.get("purpose") or "sign_in"
    if purpose == "delete":
        # Only a signed-in grown-up can ask to delete, and the code goes to the account's email.
        session = await _session(request)
        if isinstance(session, web.Response):
            return session
        email = session.account.email
    else:
        email = normalize_email(body.get("email"))
        if not email:
            return web.json_response({"error": "bad_email", "message": "not an email address"}, status=400)
    mailer, accounts = request.app[MAILER], request.app[ACCOUNTS]
    if not mailer.ready and not (accounts.review_code and email == accounts.review_email):
        log.error("sign-in asked for but email is %s", mailer.describe())
        return web.json_response({"error": "email_off", "message": "email sign-in isn't set up"}, status=503)
    try:
        code, resend_after = await asyncio.to_thread(accounts.start, email, purpose, _client_ip(request))
    except AuthError as exc:
        return _refusal(exc)
    if code is not None:
        subject, text = compose(purpose, code, 10)
        try:
            await asyncio.to_thread(mailer.send, email, subject, text)
        except MailError as exc:
            log.error("couldn't email a %s code: %s", purpose, exc)
            await asyncio.to_thread(accounts.forget_code, email, purpose)
            return web.json_response({"error": "email_failed", "message": "couldn't send the email"}, status=502)
    log.info("emailed a %s code", purpose)
    return web.json_response({"ok": True, "resend_after_s": resend_after})


async def handle_auth_verify(request: web.Request) -> web.Response:
    if not authorized(request, request.app[ACCESS_KEY]):
        return web.json_response({"error": "unauthorized"}, status=401)
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    email = normalize_email(body.get("email"))
    if not email:
        return web.json_response({"error": "bad_email", "message": "not an email address"}, status=400)
    device = parse_device(body.get("device"))
    if device is None or device[0] == ANONYMOUS:
        return web.json_response({"error": "bad device"}, status=400)
    try:
        token, account, claimed = await asyncio.to_thread(request.app[ACCOUNTS].verify, email, body.get("code"), device)
    except AuthError as exc:
        return _refusal(exc)
    log.info("device %s signed in to account %s (claimed: %s)", device[0], account.id, claimed)
    return web.json_response({"token": token, "account": account.app_json(), "claimed_device": claimed})


async def handle_account(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    return web.json_response({"account": session.account.app_json()})


async def handle_sign_out(request: web.Request) -> web.Response:
    if not authorized(request, request.app[ACCESS_KEY]):
        return web.json_response({"error": "unauthorized"}, status=401)
    token = request.headers.get(SESSION_HEADER, "").strip()
    if token:
        await asyncio.to_thread(request.app[ACCOUNTS].sign_out, token)
    return web.json_response({"ok": True})


async def handle_account_delete(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    body = await _json_body(request) or {}
    account = session.account
    bookgen = request.app[BOOKGEN]
    keys = await asyncio.to_thread(bookgen.books.asset_keys, account_id=account.id)
    try:
        counts = await asyncio.to_thread(request.app[ACCOUNTS].delete, account.id, account.email, body.get("code"))
    except AuthError as exc:
        return _refusal(exc)
    await bookgen.forget_account(keys)
    return web.json_response({"ok": True, "deleted": counts})


async def handle_account_milo_consent(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    body = await _json_body(request)
    if body is None or not isinstance(body.get("allow"), bool):
        return web.json_response({"error": "bad body", "message": 'send {"allow": true or false}'}, status=400)
    try:
        account = await asyncio.to_thread(request.app[ACCOUNTS].set_milo_consent, session.account.id, body["allow"])
    except LookupError:
        return _signed_out()
    return web.json_response({"account": account.app_json()})


async def handle_account_stars(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    stars = await asyncio.to_thread(request.app[STORE].stars_of, Scope(account_id=session.account.id))
    return web.json_response({"stars": stars, "total": sum(s["stars"] for s in stars)})


async def handle_account_coins(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    balance = await asyncio.to_thread(request.app[COINS].balance_of, Scope(account_id=session.account.id))
    return web.json_response(balance.as_json())


async def handle_account_convert(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    coins, rate = body.get("coins"), body.get("stars_per_coin")
    if (coins is not None and _whole(coins) is None) or (rate is not None and _whole(rate) is None):
        return web.json_response({"error": "coins and stars_per_coin must be whole numbers"}, status=400)
    ledger, scope = request.app[COINS], Scope(account_id=session.account.id)
    try:
        conversion, balance, replayed = await asyncio.to_thread(
            lambda: ledger.convert(session.device_id, body.get("id"), coins, rate, account_id=session.account.id)
        )
    except CoinError as exc:
        balance = await asyncio.to_thread(ledger.balance_of, scope)
        return web.json_response({"error": exc.code, "message": exc.message, **balance.as_json()}, status=exc.status)
    if not replayed:
        log.info("coins: account %s turned %d stars into %d coins", session.account.id, conversion["stars_spent"], conversion["coins"])
    return web.json_response({**balance.as_json(), "conversion": {**conversion, "replayed": replayed}})


async def handle_account_hearts(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    accounts = request.app[ACCOUNTS]
    if request.method == "GET":
        return web.json_response({"hearts": await asyncio.to_thread(accounts.hearts, session.account.id)})
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    loved, unloved = body.get("loved") or [], body.get("unloved") or []
    if not isinstance(loved, list) or not isinstance(unloved, list):
        return web.json_response({"error": "loved and unloved must be lists"}, status=400)
    hearts = await asyncio.to_thread(accounts.set_hearts, session.account.id, loved, unloved)
    return web.json_response({"hearts": hearts})


async def handle_account_import(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    stars, hearts = body.get("stars") or [], body.get("hearts") or []
    if not isinstance(stars, list) or not isinstance(hearts, list):
        return web.json_response({"error": "stars and hearts must be lists"}, status=400)
    try:
        added = await asyncio.to_thread(request.app[ACCOUNTS].import_device, session, stars, hearts)
    except AuthError as exc:
        return _refusal(exc)
    log.info("account %s imported device %s: %s", session.account.id, session.device_id, added)
    return web.json_response({"ok": True, **added})


async def handle_account_reading(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    if request.method == "GET":
        progress = await asyncio.to_thread(request.app[READING].progress, session.account.id)
        return web.json_response({"progress": progress})
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    known = request.app[MANIFEST].get("order", [])
    try:
        counts = await asyncio.to_thread(
            request.app[READING].record, session.account.id, session.device_id, body.get("events"), set(known)
        )
    except ReadingError as exc:
        return web.json_response(exc.as_json(), status=exc.status)
    return web.json_response({"ok": True, **counts})


async def handle_account_library(request: web.Request) -> web.Response:
    session = await _session(request)
    if isinstance(session, web.Response):
        return session
    prefs = request.app[LIBRARY]
    if request.method == "GET":
        return web.json_response({"library": await asyncio.to_thread(prefs.get, session.account.id)})
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    try:
        library = await asyncio.to_thread(prefs.put, session.account.id, body)
    except LibraryError as exc:
        return web.json_response(exc.as_json(), status=exc.status)
    return web.json_response({"library": library})


# -- owner: accounts ---------------------------------------------------------------------------------


async def _account_detail(app: web.Application, ref: str) -> dict | None:
    accounts = app[ACCOUNTS]
    account = await asyncio.to_thread(accounts.get, ref)
    if not account:
        return None
    scope = Scope(account_id=account.id)
    stars = await asyncio.to_thread(app[STORE].stars_of, scope)
    balance = await asyncio.to_thread(app[COINS].balance_of, scope)
    return {
        "account": {**account.as_json(), "created_at": account.created_at},
        "devices": await asyncio.to_thread(accounts.devices_of, account.id),
        "stars": sum(s["stars"] for s in stars),
        "star_days": len({s["awarded_at"][:10] for s in stars}),
        "coins": balance.as_json(),
        "hearts": len(await asyncio.to_thread(accounts.hearts, account.id)),
        "milo": await asyncio.to_thread(accounts.milo, account.id),
    }


async def handle_admin_accounts(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    accounts = request.app[ACCOUNTS]
    if request.method == "GET":
        return web.json_response({"accounts": await asyncio.to_thread(accounts.summary)})
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    try:
        account, created = await asyncio.to_thread(
            lambda: accounts.create(body.get("email") or "", all_books=bool(body.get("all_books")))
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    log.info("owner %s account %s", "created" if created else "found", account.id)
    detail = await _account_detail(request.app, account.id)
    return web.json_response({**(detail or {}), "created": created})


async def handle_admin_account(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    detail = await _account_detail(request.app, request.match_info["ref"])
    if detail is None:
        return web.json_response({"error": "no such account"}, status=404)
    return web.json_response(detail)


async def handle_admin_account_action(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    ref, action = request.match_info["ref"], request.match_info["action"]
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    accounts = request.app[ACCOUNTS]
    try:
        if action == "link":
            claim = body.get("claim", True) is not False
            account, claimed = await asyncio.to_thread(accounts.link, ref, body.get("device") or "", claim)
            log.info("owner linked device %s to account %s (claimed: %s)", body.get("device"), account.id, claimed)
        elif action == "unlink":
            device = body.get("device") or ""
            if not valid_device_id(device):
                return web.json_response({"error": "bad device id"}, status=400)
            await asyncio.to_thread(accounts.unlink, device)
        elif action == "books":
            grant, revoke, every = body.get("grant") or [], body.get("revoke") or [], body.get("all_books")
            if not isinstance(grant, list) or not isinstance(revoke, list) or every not in (None, True, False):
                return web.json_response({"error": "grant/revoke must be lists, all_books true or false"}, status=400)
            known = set(request.app[MANIFEST].get("order", []))
            unknown = [b for b in grant if valid_book_id(b) and b not in known]
            if unknown:
                return web.json_response({"error": f"no such book: {', '.join(unknown)}"}, status=400)
            await asyncio.to_thread(lambda: accounts.set_books(ref, grant=grant, revoke=revoke, all_books=every))
        elif action == "milo":
            status = body.get("status")
            if status is not None and status not in STATUSES:
                return web.json_response({"error": f"status must be one of {', '.join(STATUSES)}"}, status=400)
            clear_limit = "daily_limit_min" in body and body["daily_limit_min"] is None
            limit = body.get("daily_limit_min")
            if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float))):
                return web.json_response({"error": "daily_limit_min must be a number or null"}, status=400)
            account = await asyncio.to_thread(
                lambda: accounts.set_milo(ref, status=status, daily_limit_min=limit, clear_limit=clear_limit)
            )
            log.info("owner set account %s Milo: %s", account.id, body)
        elif action == "bookgen":
            allow = body.get("can_generate")
            if not isinstance(allow, bool):
                return web.json_response({"error": "send {\"can_generate\": true or false}"}, status=400)
            account = await asyncio.to_thread(accounts.set_can_generate, ref, allow)
            log.info("owner set account %s can_generate=%s", account.id, allow)
        else:
            return web.json_response({"error": "unknown action"}, status=404)
    except LookupError:
        return web.json_response({"error": "no such account"}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    detail = await _account_detail(request.app, ref)
    return web.json_response(detail or {})


async def handle_admin_backup(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    dump = await asyncio.to_thread(request.app[STORE].backup)
    log.info("owner took a backup (%s)", {t: len(v["rows"]) for t, v in dump["tables"].items()})
    return web.json_response(dump, dumps=lambda o: json.dumps(o, default=str))


async def handle_admin_restore(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    body = await _json_body(request)
    if body is None or body.get("confirm") != "restore" or not isinstance(body.get("backup"), dict):
        return web.json_response({"error": 'send {"confirm": "restore", "backup": {...}}'}, status=400)
    try:
        counts = await asyncio.to_thread(request.app[STORE].restore, body["backup"])
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    log.warning("owner restored a backup: %s", counts)
    await request.app[BOOKGEN].refresh()
    request.app[BOOKGEN].wake()  # the backup may hold unfinished jobs
    return web.json_response({"ok": True, "restored": counts})


# -- owner: making books ---------------------------------------------------------------------------


async def handle_levels(request: web.Request) -> web.Response:
    """The reading levels, with the short descriptions a grown-up sees when making a book."""
    return web.json_response({"levels": levels_json()})


async def handle_admin_bookgen(request: web.Request) -> web.Response:
    """Book making status; POST {"everyone": true} lets every account make books (not just dogfood)."""
    if denied := _admin(request):
        return denied
    accounts = request.app[ACCOUNTS]
    if request.method == "POST":
        body = await _json_body(request)
        if body is None or not isinstance(body.get("everyone"), bool):
            return web.json_response({"error": 'send {"everyone": true or false}'}, status=400)
        await asyncio.to_thread(accounts.set_bookgen_everyone, body["everyone"])
        log.info("owner turned book making for everyone %s", "on" if body["everyone"] else "off")
    status = await request.app[BOOKGEN].status()
    everyone = await asyncio.to_thread(accounts.bookgen_everyone_on)
    return web.json_response({**status, "everyone": everyone})


async def handle_admin_generate(request: web.Request) -> web.Response:
    """Queue a book for an account: {"account", "title", "section", "level", "about"?}."""
    if denied := _admin(request):
        return denied
    body = await _json_body(request)
    if body is None:
        return web.json_response({"error": "bad body"}, status=400)
    bookgen = request.app[BOOKGEN]
    if not bookgen.ready:
        return web.json_response({"error": "book making is not set up on this server"}, status=503)
    try:
        book_request = validate_request(body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    account = await asyncio.to_thread(request.app[ACCOUNTS].get, str(body.get("account") or ""))
    if account is None:
        return web.json_response({"error": "no such account"}, status=404)
    if not account.can_generate:
        return web.json_response({"error": "this account can't make books yet"}, status=403)
    try:
        job = await asyncio.to_thread(bookgen.queue, account.id, book_request)
    except LimitError as exc:
        return web.json_response(exc.as_json(), status=exc.status)
    bookgen.wake()
    log.info("owner queued book %s (job %s) for account %s", job.book_id, job.id, account.id)
    usage = await asyncio.to_thread(bookgen.books.usage, account.id)
    return web.json_response({"job": job.as_json(), "usage": usage}, status=202)


async def handle_admin_books(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    account = request.query.get("account")
    if account:
        found = await asyncio.to_thread(request.app[ACCOUNTS].get, account)
        if found is None:
            return web.json_response({"error": "no such account"}, status=404)
        account = found.id
    books = await asyncio.to_thread(request.app[BOOKGEN].books.books, account or None)
    return web.json_response({"books": [b.as_json() for b in books]})


async def handle_admin_book(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    book = await asyncio.to_thread(request.app[BOOKGEN].books.book, request.match_info["id"])
    if book is None:
        return web.json_response({"error": "no such book"}, status=404)
    return web.json_response(book.as_json(full=True))


async def handle_admin_book_action(request: web.Request) -> web.Response:
    """publish / unpublish (the kill switch) / delete a made book."""
    if denied := _admin(request):
        return denied
    book_id, action = request.match_info["id"], request.match_info["action"]
    bookgen = request.app[BOOKGEN]
    try:
        if action in ("publish", "unpublish"):
            book = await bookgen.set_published(book_id, action == "publish")
            log.info("owner %sed book %s", action, book_id)
            return web.json_response(book)
        if action == "delete":
            deleted = await bookgen.delete_book(book_id)
            log.info("owner deleted book %s (%d files)", book_id, deleted)
            return web.json_response({"ok": True, "deleted_files": deleted})
    except LookupError:
        return web.json_response({"error": "no such book"}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response({"error": "unknown action"}, status=404)


async def handle_admin_jobs(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    jobs = await asyncio.to_thread(request.app[BOOKGEN].books.jobs)
    return web.json_response({"jobs": [j.as_json() for j in jobs]})


async def handle_admin_job(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    job = await asyncio.to_thread(request.app[BOOKGEN].books.job, request.match_info["id"])
    if job is None:
        return web.json_response({"error": "no such job"}, status=404)
    return web.json_response(job.as_json())


async def handle_admin_job_cancel(request: web.Request) -> web.Response:
    if denied := _admin(request):
        return denied
    try:
        job = await request.app[BOOKGEN].cancel(request.match_info["id"])
    except LookupError:
        return web.json_response({"error": "no such job"}, status=404)
    log.info("owner cancelled job %s", job.id)
    return web.json_response(job.as_json())


async def handle_health(request: web.Request) -> web.Response:
    # The deploy workflow polls this until the new commit and book content are live. ``bundled`` is the
    # version of the commit's own books; ``content`` also counts generated books, so it differs once one is out.
    commit = os.environ.get("RENDER_GIT_COMMIT", "").strip() or None
    content = request.app[MANIFEST]["version"]
    bookgen = request.app.get(BOOKGEN)
    bundled = bookgen.static["version"] if bookgen is not None else content
    return web.json_response({"ok": True, "commit": commit, "content": content, "bundled": bundled})


async def handle_manifest(request: web.Request) -> web.Response:
    # The app asks on launch and when it comes back to the front: user traffic, which may wake the database.
    request.app[BOOKGEN].maybe_refresh()
    manifest = request.app[MANIFEST]
    headers = {"Cache-Control": "no-cache", "ETag": f'"{manifest["version"]}"'}
    if request.headers.get("If-None-Match") == headers["ETag"]:
        return web.Response(status=304, headers=headers)
    return web.json_response(manifest, headers=headers)


async def handle_content_file(request: web.Request) -> web.StreamResponse:
    rel = request.match_info["path"]
    bookgen = request.app[BOOKGEN]
    made = bookgen.extra.get(rel)
    if isinstance(made, bytes):
        return web.Response(body=made, content_type="application/json", headers={"Cache-Control": "no-cache"})
    if isinstance(made, Asset):
        # Made books' photos and clips live in a private R2 bucket: send a short-lived signed link.
        raise web.HTTPFound(bookgen.url_for(made), headers={"Cache-Control": "no-store"})
    path = request.app[FILES].get(rel)
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
ACCOUNTS = web.AppKey("accounts", Accounts)
MAILER = web.AppKey("mailer", Mailer)
READING = web.AppKey("reading_log", ReadingLog)
LIBRARY = web.AppKey("library_prefs", LibraryPrefs)
BOOKGEN = web.AppKey("bookgen", BookgenService)


def create_app(
    *,
    access_key: str | None = None,
    content_root: Path | None = None,
    store: DeviceStore | None = None,
    admin_key: str | None = None,
    report_key: str | None = None,
    coins: CoinLedger | None = None,
    accounts: Accounts | None = None,
    mailer: Mailer | None = None,
    reading: ReadingLog | None = None,
    library: LibraryPrefs | None = None,
    bookgen: BookgenService | None = None,
) -> web.Application:
    app = web.Application()
    app[ACCESS_KEY] = (os.environ.get("PIP_ACCESS_KEY", "") if access_key is None else access_key).strip()
    app[ADMIN_KEY] = (os.environ.get("PIP_ADMIN_KEY", "") if admin_key is None else admin_key).strip()
    app[REPORT_KEY] = (os.environ.get("PIP_REPORT_KEY", "") if report_key is None else report_key).strip()
    app[STORE] = store or DeviceStore.from_env()
    app[COINS] = coins or CoinLedger.from_env(app[STORE])
    app[ACCOUNTS] = accounts or Accounts.from_env(app[STORE])
    app[MAILER] = mailer or Mailer.from_env()
    app[READING] = reading or ReadingLog(app[STORE])
    app[LIBRARY] = library or LibraryPrefs(app[STORE])
    root = content_root or resources_dir()
    if bookgen is None:
        bookgen = BookgenService(app[STORE], static_manifest=build_manifest(root), static_files=content_files(root))
    app[BOOKGEN] = bookgen
    # all_books accounts read the hand-made books being served, never other families' made books.
    app[ACCOUNTS].catalog = lambda: bookgen.static["order"]
    # The same dict the book service updates in place when made books are published.
    app[MANIFEST] = bookgen.manifest
    app[FILES] = bookgen.static_files
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
    app.router.add_get("/admin/reading", handle_admin_reading)
    app.router.add_post("/admin/devices/{ref}", handle_admin_update)
    app.router.add_get("/admin/coins", handle_admin_coins)
    app.router.add_post("/admin/coins", handle_admin_coins)
    app.router.add_post("/auth/start", handle_auth_start)
    app.router.add_post("/auth/verify", handle_auth_verify)
    app.router.add_post("/auth/signout", handle_sign_out)
    app.router.add_get("/account", handle_account)
    app.router.add_post("/account/delete", handle_account_delete)
    app.router.add_post("/account/milo-consent", handle_account_milo_consent)
    app.router.add_get("/account/stars", handle_account_stars)
    app.router.add_get("/account/coins", handle_account_coins)
    app.router.add_post("/account/coins/convert", handle_account_convert)
    app.router.add_get("/account/hearts", handle_account_hearts)
    app.router.add_post("/account/hearts", handle_account_hearts)
    app.router.add_post("/account/import", handle_account_import)
    app.router.add_get("/account/reading", handle_account_reading)
    app.router.add_post("/account/reading", handle_account_reading)
    app.router.add_get("/account/library", handle_account_library)
    app.router.add_put("/account/library", handle_account_library)
    app.router.add_get("/admin/accounts", handle_admin_accounts)
    app.router.add_post("/admin/accounts", handle_admin_accounts)
    app.router.add_get("/admin/accounts/{ref}", handle_admin_account)
    app.router.add_post("/admin/accounts/{ref}/{action}", handle_admin_account_action)
    app.router.add_get("/admin/backup", handle_admin_backup)
    app.router.add_post("/admin/restore", handle_admin_restore)
    app.router.add_get("/levels", handle_levels)
    app.router.add_get("/admin/bookgen", handle_admin_bookgen)
    app.router.add_post("/admin/bookgen", handle_admin_bookgen)
    app.router.add_post("/admin/books/generate", handle_admin_generate)
    app.router.add_get("/admin/books", handle_admin_books)
    app.router.add_get("/admin/books/{id}", handle_admin_book)
    app.router.add_post("/admin/books/{id}/{action}", handle_admin_book_action)
    app.router.add_get("/admin/jobs", handle_admin_jobs)
    app.router.add_get("/admin/jobs/{id}", handle_admin_job)
    app.router.add_post("/admin/jobs/{id}/cancel", handle_admin_job_cancel)

    async def run_bookgen(app: web.Application) -> AsyncIterator[None]:
        await app[BOOKGEN].start()
        yield
        await app[BOOKGEN].stop()

    app.cleanup_ctx.append(run_bookgen)

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
    if app[BOOKGEN].ready:
        log.info("book generation: on (%s), limits %s", app[BOOKGEN].config.text_model, app[BOOKGEN].config.limits_json())
    else:
        log.info("book generation: off (needs BOOKGEN_OPENAI_API_KEY and R2_* settings)")
    log.info("sign-in email: %s; new accounts get %d books", app[MAILER].describe(), len(app[ACCOUNTS].default_books))
    if not app[ADMIN_KEY] or not app[REPORT_KEY]:
        log.warning("PIP_ADMIN_KEY or PIP_REPORT_KEY is not set: /admin or /usage is turned off")
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
