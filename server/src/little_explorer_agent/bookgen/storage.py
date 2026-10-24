"""Cloudflare R2 storage for generated books (S3 API, AWS Signature V4 by hand, no boto3).

The bucket is private. The app gets each photo or clip through a 302 to a short-lived presigned
GET URL, so nothing in the bucket is ever public. The token is scoped to the bucket's objects, so
only object calls are used (no list_buckets).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
from typing import Any
from urllib.parse import quote, urlsplit

from .config import R2Config

UNSIGNED = "UNSIGNED-PAYLOAD"


class StorageError(RuntimeError):
    pass


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _key_path(bucket: str, key: str) -> str:
    return "/" + quote(bucket, safe="") + "/" + quote(key, safe="/-_.~")


def _query(params: dict[str, str]) -> str:
    return "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))


class R2Storage:
    def __init__(self, config: R2Config, *, timeout: float = 60.0) -> None:
        self.config = config
        self._host = urlsplit(config.endpoint).netloc
        self._timeout = timeout
        self._session: Any = None

    def _signing_key(self, day: str) -> bytes:
        key = _hmac(("AWS4" + self.config.secret_access_key).encode(), day)
        return _hmac(_hmac(_hmac(key, self.config.region), "s3"), "aws4_request")

    def _scope(self, day: str) -> str:
        return f"{day}/{self.config.region}/s3/aws4_request"

    def _sign(self, method: str, path: str, query: str, headers: dict[str, str], payload_hash: str,
              when: dt.datetime) -> str:
        amz_date, day = when.strftime("%Y%m%dT%H%M%SZ"), when.strftime("%Y%m%d")
        names = sorted(h.lower() for h in headers)
        lower = {k.lower(): v.strip() for k, v in headers.items()}
        canonical = "\n".join([
            method, path, query,
            "".join(f"{n}:{lower[n]}\n" for n in names),
            ";".join(names), payload_hash,
        ])
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, self._scope(day),
                             hashlib.sha256(canonical.encode()).hexdigest()])
        return hmac.new(self._signing_key(day), to_sign.encode(), hashlib.sha256).hexdigest()

    def presign(self, key: str, expires: int = 3600, *, when: dt.datetime | None = None) -> str:
        """A GET URL that works for ``expires`` seconds without credentials."""
        when = when or dt.datetime.now(dt.timezone.utc)
        path = _key_path(self.config.bucket, key)
        params = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.config.access_key_id}/{self._scope(when.strftime('%Y%m%d'))}",
            "X-Amz-Date": when.strftime("%Y%m%dT%H%M%SZ"),
            "X-Amz-Expires": str(max(1, min(int(expires), 604800))),
            "X-Amz-SignedHeaders": "host",
        }
        query = _query(params)
        signature = self._sign("GET", path, query, {"host": self._host}, UNSIGNED, when)
        return f"{self.config.endpoint}{path}?{query}&X-Amz-Signature={signature}"

    def _headers(self, method: str, key: str, body: bytes, extra: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
        when = dt.datetime.now(dt.timezone.utc)
        path = _key_path(self.config.bucket, key)
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {"host": self._host, "x-amz-content-sha256": payload_hash,
                   "x-amz-date": when.strftime("%Y%m%dT%H%M%SZ"), **(extra or {})}
        signature = self._sign(method, path, "", headers, payload_hash, when)
        credential = f"{self.config.access_key_id}/{self._scope(when.strftime('%Y%m%d'))}"
        headers["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={credential}, "
                                    f"SignedHeaders={';'.join(sorted(h.lower() for h in headers if h != 'Authorization'))}, "
                                    f"Signature={signature}")
        headers.pop("host")
        return f"{self.config.endpoint}{path}", headers

    async def _http(self) -> Any:
        from aiohttp import ClientSession, ClientTimeout

        if self._session is None or self._session.closed:
            self._session = ClientSession(timeout=ClientTimeout(total=self._timeout))
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _call(self, method: str, key: str, body: bytes = b"", extra: dict[str, str] | None = None) -> tuple[int, bytes]:
        from aiohttp import ClientError

        http = await self._http()
        for attempt in range(4):
            url, headers = self._headers(method, key, body, extra)
            try:
                async with http.request(method, url, data=body or None, headers=headers) as resp:
                    data = await resp.read()
                    if resp.status >= 500 and attempt < 3:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return resp.status, data
            except (ClientError, asyncio.TimeoutError) as err:
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise StorageError(f"R2 {method} failed: {type(err).__name__}") from None
        raise StorageError(f"R2 {method} out of retries")

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        status, body = await self._call("PUT", key, data, {"content-type": content_type})
        if status >= 300:
            raise StorageError(f"R2 PUT {status}: {body[:200]!r}")

    async def get(self, key: str) -> bytes | None:
        status, body = await self._call("GET", key)
        if status == 404:
            return None
        if status >= 300:
            raise StorageError(f"R2 GET {status}")
        return body

    async def exists(self, key: str) -> bool:
        status, _ = await self._call("HEAD", key)
        if status == 404:
            return False
        if status >= 300:
            raise StorageError(f"R2 HEAD {status}")
        return True

    async def delete(self, key: str) -> None:
        status, _ = await self._call("DELETE", key)
        if status >= 300 and status != 404:
            raise StorageError(f"R2 DELETE {status}")


class MemoryStorage:
    """Stands in for R2 in tests and local runs: same methods, kept in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def presign(self, key: str, expires: int = 3600, **_: Any) -> str:
        return f"https://r2.invalid/{quote(key, safe='/-_.~')}?expires={expires}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (bytes(data), content_type)

    async def get(self, key: str) -> bytes | None:
        item = self.objects.get(key)
        return item[0] if item else None

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def close(self) -> None:
        pass
