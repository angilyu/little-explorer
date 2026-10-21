"""What each Milo session costs: one CSV row per session, written when the session ends.

The row has timings, the engine and model, the book and how many pages were visited, the number
of model responses, token counts split into text and audio, and an estimated price. It never holds
transcripts or audio.

Tokens come from LiveKit's per-session usage (``AgentSession.usage``), which adds up the
Realtime API's ``response.done`` usage and the input-transcription usage. Responses are counted
from ``session_usage_updated``: each billed response of the main model grows its input tokens.

When ``PIP_SERVER_URL`` and ``PIP_REPORT_KEY`` are set, the agent also posts a short summary (no
token columns) to the token server's ``/usage`` so per-device minutes survive cloud deploys; see
:func:`report_usage` and :mod:`.devices`.

Prices are USD per 1M tokens from https://developers.openai.com/api/docs/pricing (checked
2026-08). Override any rate with ``PIP_PRICE_<MODEL>_<RATE>``, e.g.
``PIP_PRICE_GPT_REALTIME_AUDIO_OUT=64`` (model upper-cased, non-alphanumerics -> ``_``).
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("little_explorer.usage")

USAGE_FILE = Path("~/.config/little-explorer/usage.csv")
USAGE_FILE_ENV = "PIP_USAGE_FILE"


@dataclass(frozen=True)
class Rates:
    """USD per 1M tokens. ``*_cached`` applies to cached input tokens."""

    text_in: float = 0.0
    text_cached: float = 0.0
    text_out: float = 0.0
    audio_in: float = 0.0
    audio_cached: float = 0.0
    audio_out: float = 0.0
    # Transcription billed per minute of audio (gpt-realtime-whisper) instead of tokens.
    per_minute: float = 0.0


# Source: https://developers.openai.com/api/docs/pricing ("Realtime and audio generation models",
# "Transcription models", and the standard text table for the pipeline's chat models).
PRICES: dict[str, Rates] = {
    "gpt-realtime": Rates(text_in=4.00, text_cached=0.40, text_out=16.00, audio_in=32.00, audio_cached=0.40, audio_out=64.00),
    "gpt-realtime-mini": Rates(text_in=0.60, text_cached=0.06, text_out=2.40, audio_in=10.00, audio_cached=0.30, audio_out=20.00),
    # Input transcription inside a realtime session: audio in, text out.
    "gpt-4o-mini-transcribe": Rates(text_in=1.25, audio_in=1.25, text_out=5.00),
    "gpt-4o-transcribe": Rates(text_in=2.50, audio_in=2.50, text_out=10.00),
    # Pipeline mode (PIP_MODE=pipeline).
    "gpt-realtime-whisper": Rates(per_minute=0.017),
    "gpt-4o-mini-tts": Rates(text_in=0.60, audio_out=12.00),
    "gpt-5.4-mini": Rates(text_in=0.75, text_cached=0.075, text_out=4.50),
    "gpt-5.4-nano": Rates(text_in=0.20, text_cached=0.02, text_out=1.25),
}

COLUMNS = [
    "started_at",
    "duration_s",
    "mode",
    "model",
    "book",
    "pages_visited",
    "responses",
    "input_text_tokens",
    "input_audio_tokens",
    "cached_text_tokens",
    "cached_audio_tokens",
    "output_text_tokens",
    "output_audio_tokens",
    "transcribe_model",
    "transcribe_input_tokens",
    "transcribe_output_tokens",
    "transcribe_audio_s",
    "other_models",
    "est_usd",
    "unpriced_models",
]


def _env_name(model: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", model.upper()).strip("_")


def rates_for(model: str, env: Mapping[str, str] | None = None) -> Rates | None:
    """Rates for ``model`` (longest known prefix, so dated snapshots match), with env overrides."""
    env = os.environ if env is None else env
    base = PRICES.get(model)
    if base is None:
        prefixes = [name for name in PRICES if model.startswith(name + "-")]
        base = PRICES[max(prefixes, key=len)] if prefixes else None
    overrides: dict[str, float] = {}
    for f in fields(Rates):
        raw = env.get(f"PIP_PRICE_{_env_name(model)}_{f.name.upper()}")
        if raw:
            try:
                overrides[f.name] = float(raw)
            except ValueError:
                log.warning("ignoring non-numeric price override for %s %s", model, f.name)
    if base is None and not overrides:
        return None
    return replace(base or Rates(), **overrides)


@dataclass
class Tokens:
    """Token counts for one model. Cached counts are part of the input counts."""

    model: str = ""
    kind: str = "llm"  # llm | stt | tts
    input_text: int = 0
    input_audio: int = 0
    cached_text: int = 0
    cached_audio: int = 0
    output_text: int = 0
    output_audio: int = 0
    audio_s: float = 0.0

    @property
    def used(self) -> bool:
        return any((self.input_text, self.input_audio, self.output_text, self.output_audio, self.audio_s))

    def cost(self, rates: Rates) -> float:
        usd = (
            max(self.input_text - self.cached_text, 0) * rates.text_in
            + self.cached_text * rates.text_cached
            + max(self.input_audio - self.cached_audio, 0) * rates.audio_in
            + self.cached_audio * rates.audio_cached
            + self.output_text * rates.text_out
            + self.output_audio * rates.audio_out
        ) / 1_000_000
        return usd + self.audio_s / 60 * rates.per_minute


def tokens_from_usage(model_usage: Iterable[Any]) -> list[Tokens]:
    """Turn LiveKit ``ModelUsage`` entries (LLM/realtime, STT, TTS) into :class:`Tokens`."""
    out: list[Tokens] = []
    for u in model_usage:
        kind = getattr(u, "type", "")
        model = getattr(u, "model", "") or ""
        if kind == "llm_usage":
            text_in = u.input_text_tokens
            audio_in = u.input_audio_tokens
            if not text_in and not audio_in:  # text-only LLMs don't split their input
                text_in = u.input_tokens
            text_out = u.output_text_tokens
            audio_out = u.output_audio_tokens
            if not text_out and not audio_out:
                text_out = u.output_tokens
            cached_text = u.input_cached_text_tokens
            cached_audio = u.input_cached_audio_tokens
            if not cached_text and not cached_audio:
                cached_text = u.input_cached_tokens
            out.append(Tokens(model, "llm", text_in, audio_in, cached_text, cached_audio, text_out, audio_out))
        elif kind == "stt_usage":
            audio_in = u.input_audio_tokens or u.input_tokens
            text_in = max(u.input_tokens - audio_in, 0)
            out.append(Tokens(model, "stt", text_in, audio_in, output_text=u.output_tokens, audio_s=u.audio_duration))
        elif kind == "tts_usage":
            out.append(Tokens(model, "tts", input_text=u.input_tokens, output_audio=u.output_tokens))
    return out


@dataclass
class SessionUsage:
    """Everything one CSV row needs. Built up during the session by :class:`UsageMeter`."""

    started_at: datetime
    mode: str
    model: str
    book: str = ""
    pages: set[str] = field(default_factory=set)
    responses: int = 0
    tokens: list[Tokens] = field(default_factory=list)
    duration_s: float = 0.0

    def row(self, env: Mapping[str, str] | None = None) -> dict[str, Any]:
        main = [t for t in self.tokens if t.kind == "llm" and t.model == self.model]
        stt = [t for t in self.tokens if t.kind == "stt"]
        others = sorted({t.model for t in self.tokens if t not in main and t not in stt})
        usd = 0.0
        unpriced: set[str] = set()
        for t in self.tokens:
            rates = rates_for(t.model, env)
            if rates is None:
                if t.used:
                    unpriced.add(t.model or "?")
                continue
            usd += t.cost(rates)

        def total(items: list[Tokens], name: str) -> int:
            return sum(getattr(t, name) for t in items)

        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "duration_s": round(self.duration_s, 1),
            "mode": self.mode,
            "model": self.model,
            "book": self.book,
            "pages_visited": len(self.pages),
            "responses": self.responses,
            "input_text_tokens": total(main, "input_text"),
            "input_audio_tokens": total(main, "input_audio"),
            "cached_text_tokens": total(main, "cached_text"),
            "cached_audio_tokens": total(main, "cached_audio"),
            "output_text_tokens": total(main, "output_text"),
            "output_audio_tokens": total(main, "output_audio"),
            "transcribe_model": "+".join(sorted({t.model for t in stt})),
            "transcribe_input_tokens": total(stt, "input_text") + total(stt, "input_audio"),
            "transcribe_output_tokens": total(stt, "output_text"),
            "transcribe_audio_s": round(sum(t.audio_s for t in stt), 1),
            "other_models": "+".join(others),
            "est_usd": f"{usd:.4f}",
            "unpriced_models": "+".join(sorted(unpriced)),
        }


def usage_file(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get(USAGE_FILE_ENV) or USAGE_FILE).expanduser()


def append_row(row: Mapping[str, Any], path: Path | None = None) -> Path:
    """Append ``row`` to the usage CSV, creating it (mode 600, with a header) if needed."""
    path = usage_file() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", newline="") as f:
        if f.tell() == 0:
            csv.writer(f).writerow(COLUMNS)
        csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore").writerow(row)
    return path


class UsageMeter:
    """Follows one session and writes its row exactly once, however the session ends."""

    def __init__(
        self,
        *,
        mode: str,
        model: str,
        path: Path | None = None,
        clock=time.monotonic,
        room: str = "",
        device_id: str = "",
        account_id: str = "",
    ) -> None:
        self.usage = SessionUsage(started_at=datetime.now().astimezone(), mode=mode, model=model)
        self.path = path
        self.room = room
        self.device_id = device_id
        self.account_id = account_id
        self.ended_reason = ""
        self.row: dict[str, Any] | None = None
        self._clock = clock
        self._t0 = clock()
        self._main_input = 0
        self._session: Any = None
        self._written = False

    def on_context(self, context: Mapping[str, Any]) -> None:
        """Note the book and page from the app's context (only ids, never page text)."""
        if book := context.get("book"):
            self.usage.book = str(book)
        if page := context.get("page"):
            self.usage.pages.add(f"{self.usage.book}/{page}")

    def on_usage(self, model_usage: Iterable[Any]) -> None:
        """Handle ``session_usage_updated``: keep the latest totals and count new responses."""
        tokens = tokens_from_usage(model_usage)
        main_input = sum(t.input_text + t.input_audio for t in tokens if t.kind == "llm" and t.model == self.usage.model)
        if main_input > self._main_input:
            self.usage.responses += 1
            self._main_input = main_input
        self.usage.tokens = tokens

    def bind(self, session: Any) -> None:
        """Follow an ``AgentSession``; the row is written when it closes (if not already)."""
        self._session = session
        session.on("session_usage_updated", lambda ev: self.on_usage(ev.usage.model_usage))
        session.on("close", lambda _ev: self.finish())

    def finish(self) -> dict[str, Any] | None:
        """Write the row (idempotent). Safe to call from the job's shutdown callback."""
        if self._written:
            return None
        self._written = True
        if self._session is not None:
            try:
                self.on_usage_totals(self._session.usage.model_usage)
            except Exception:
                log.exception("could not read session usage")
        self.usage.duration_s = self._clock() - self._t0
        row = self.row = self.usage.row()
        try:
            path = append_row(row, self.path)
            log.info("usage: %ss, %s responses, ~$%s -> %s", row["duration_s"], row["responses"], row["est_usd"], path)
        except OSError:
            log.exception("could not write the usage row")
        return row

    def on_usage_totals(self, model_usage: Iterable[Any]) -> None:
        tokens = tokens_from_usage(model_usage)
        if tokens:
            self.usage.tokens = tokens

    def report(self, reason: str = "") -> dict[str, Any] | None:
        """What :func:`report_usage` posts for this session (after :meth:`finish`)."""
        if self.row is None:
            return None
        return {
            "room": self.room,
            "device_id": self.device_id,
            **({"account_id": self.account_id} if self.account_id else {}),
            "started_at": self.row["started_at"],
            "duration_s": self.row["duration_s"],
            "mode": self.row["mode"],
            "model": self.row["model"],
            "book": self.row["book"],
            "pages": self.row["pages_visited"],
            "responses": self.row["responses"],
            "est_usd": float(self.row["est_usd"]),
            "ended_reason": self.ended_reason or reason,
        }


def report_target(env: Mapping[str, str] | None = None, path: str = "usage") -> tuple[str, str] | None:
    """``(url, key)`` for ``POST /<path>`` on the token server, or None when reporting is not set up."""
    env = os.environ if env is None else env
    base = (env.get("PIP_SERVER_URL") or "").strip().rstrip("/")
    key = (env.get("PIP_REPORT_KEY") or "").strip()
    return (f"{base}/{path}", key) if base and key else None


async def post_report(
    payload: Mapping[str, Any],
    target: tuple[str, str],
    *,
    label: str,
    timeouts: Sequence[float] = (4.0, 3.0),
    pause: float = 0.5,
) -> bool:
    """POST ``payload`` with the report key, retrying once on network trouble or a 5xx."""
    from aiohttp import ClientSession, ClientTimeout

    url, key = target
    for attempt, timeout in enumerate(timeouts, 1):
        try:
            async with ClientSession(timeout=ClientTimeout(total=timeout)) as http:
                async with http.post(url, json=dict(payload), headers={"Authorization": "Bearer " + key}) as resp:
                    if resp.status == 200:
                        log.info("reported %s", label)
                        return True
                    log.warning("%s report got HTTP %s", label, resp.status)
                    if 400 <= resp.status < 500:
                        return False
        except Exception as exc:  # noqa: BLE001 - network trouble; retry
            log.warning("%s report attempt %d failed: %s", label, attempt, exc)
        if attempt < len(timeouts):
            await asyncio.sleep(pause)
    return False


async def report_usage(
    report: Mapping[str, Any] | None,
    target: tuple[str, str] | None = None,
    *,
    timeouts: Sequence[float] = (4.0, 3.0),
    pause: float = 0.5,
) -> bool:
    """Post ``report`` to the token server, retrying once. Fits the job's ~10 s shutdown window."""
    target = report_target() if target is None else target
    if not report or not target or not report.get("room"):
        return False
    return await post_report(report, target, label=f"usage for {report['room']}", timeouts=timeouts, pause=pause)
