"""A small OpenAI client for book generation (aiohttp, no SDK), with retries on rate limits.

Only the calls a book needs: structured text (Responses API, optionally with web search),
moderation, text to speech and word-timed transcription. Tests swap in a fake with the same methods.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("little_explorer.bookgen")

API = "https://api.openai.com/v1"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


class OpenAIError(RuntimeError):
    pass


@dataclass
class TextResult:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    web_searches: int = 0


@dataclass
class Moderation:
    flagged: bool
    categories: list[str]


def _output_text(body: dict[str, Any]) -> str:
    if isinstance(body.get("output_text"), str):
        return body["output_text"]
    parts = []
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "refusal":
                raise OpenAIError(f"model refused: {content.get('refusal', '')[:200]}")
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    return "".join(parts)


class OpenAIClient:
    def __init__(self, api_key: str, *, base: str = API, timeout: float = 180.0, attempts: int = 5) -> None:
        self._key = api_key
        self._base = base.rstrip("/")
        self._timeout = timeout
        self._attempts = attempts
        self._session: Any = None

    async def _http(self) -> Any:
        from aiohttp import ClientSession, ClientTimeout

        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=self._timeout))
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _post(self, path: str, *, json_body: Any = None, data: bytes | None = None,
                    content_type: str = "application/json") -> bytes:
        from aiohttp import ClientError

        http = await self._http()
        payload = json.dumps(json_body).encode() if json_body is not None else data
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": content_type}
        for attempt in range(self._attempts):
            try:
                async with http.post(f"{self._base}{path}", data=payload, headers=headers) as resp:
                    body = await resp.read()
                    if resp.status < 300:
                        return body
                    if resp.status in RETRY_STATUS and attempt < self._attempts - 1:
                        await asyncio.sleep(2 ** attempt * 2)
                        continue
                    raise OpenAIError(f"{path}: HTTP {resp.status} {body[:300]!r}")
            except (ClientError, asyncio.TimeoutError) as err:
                if attempt < self._attempts - 1:
                    await asyncio.sleep(2 ** attempt * 2)
                    continue
                raise OpenAIError(f"{path}: {type(err).__name__}") from None
        raise OpenAIError(f"{path}: out of retries")

    async def structured(self, *, model: str, instructions: str, input: str | list[dict[str, Any]],
                         schema_name: str, schema: dict[str, Any], web_search: bool = False,
                         max_output_tokens: int = 6000, reasoning: str | None = None) -> TextResult:
        """One Responses API call whose answer must match ``schema`` (strict JSON)."""
        body: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input,
            "max_output_tokens": max_output_tokens,
            "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
            "store": False,
        }
        if reasoning:
            body["reasoning"] = {"effort": reasoning}
        if web_search:
            body["tools"] = [{"type": "web_search"}]
        raw = json.loads(await self._post("/responses", json_body=body))
        if raw.get("status") not in (None, "completed"):
            raise OpenAIError(f"response {raw.get('status')}: {raw.get('incomplete_details')}")
        text = _output_text(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise OpenAIError("model answer was not JSON") from None
        usage = raw.get("usage") or {}
        searches = sum(1 for item in raw.get("output") or [] if item.get("type") == "web_search_call")
        return TextResult(data, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), searches)

    async def moderate(self, items: list[dict[str, Any]], *, model: str = "omni-moderation-latest") -> list[Moderation]:
        """Moderate a batch of inputs: ``{"type": "text", "text": ...}`` or one image
        ``{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}``."""
        raw = json.loads(await self._post("/moderations", json_body={"model": model, "input": items}))
        results = raw.get("results")
        if not isinstance(results, list) or not results:
            raise OpenAIError("moderation returned no results")
        return [Moderation(bool(r.get("flagged")), sorted(k for k, v in (r.get("categories") or {}).items() if v))
                for r in results]

    async def speak(self, text: str, *, model: str, voice: str, instructions: str) -> bytes:
        body = {"model": model, "voice": voice, "input": text, "instructions": instructions, "response_format": "mp3"}
        return await self._post("/audio/speech", json_body=body)

    async def transcribe(self, audio: bytes, prompt: str, *, model: str = "whisper-1") -> dict[str, Any]:
        boundary = uuid.uuid4().hex
        fields = [("model", model), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word"),
                  ("language", "en"), ("prompt", prompt)]
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode() for n, v in fields]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="clip.mp3"\r\n'
                     "Content-Type: audio/mpeg\r\n\r\n".encode() + audio + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        raw = await self._post("/audio/transcriptions", data=b"".join(parts),
                               content_type=f"multipart/form-data; boundary={boundary}")
        return json.loads(raw)
