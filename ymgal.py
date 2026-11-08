"""Bounded client for the public 月幕Galgame archive API."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import OrderedDict
from datetime import date, timedelta

import httpx

YMGAL = "https://www.ymgal.games"
# These two values are explicitly designated as the public client credentials.
# https://www.ymgal.games/developer
PUBLIC_CLIENT_ID = "ymgal"
PUBLIC_CLIENT_SECRET = "luna0327"
MAX_RESPONSE_BYTES = 2_000_000


class YmgalError(RuntimeError):
    """A safe status code without request URLs, provider text or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _RedactTokenURL(logging.Filter):
    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) >= 2 and isinstance(args[1], httpx.URL):
            url = args[1]
            if url.host == "www.ymgal.games" and url.path == "/oauth/token":
                record.args = (args[0], YMGAL + "/oauth/token", *args[2:])
        return True


class YmgalClient:
    def __init__(self, client: httpx.AsyncClient, *, client_id="", client_secret="", min_interval=0.5):
        self.client = client
        client_id, client_secret = client_id.strip(), client_secret.strip()
        self._invalid_credentials = bool(client_id) != bool(client_secret)
        self._credentials = (client_id, client_secret) if client_id else (PUBLIC_CLIENT_ID, PUBLIC_CLIENT_SECRET)
        self._lock = asyncio.Lock()
        self._token = ""
        self._next_request = 0.0
        self._cooldown = 0.0
        self._interval = max(0, min_interval)
        self._cache = OrderedDict()
        logger = logging.getLogger("httpx")
        if not any(isinstance(item, _RedactTokenURL) for item in logger.filters):
            logger.addFilter(_RedactTokenURL())

    async def _request(self, path, params, headers, deadline, stats):
        now = time.monotonic()
        if now < self._cooldown:
            raise YmgalError("rate_limited")
        pause = max(0, self._next_request - now)
        if now + pause >= deadline:
            raise YmgalError("timeout")
        if pause:
            await asyncio.sleep(pause)
        self._next_request = time.monotonic() + self._interval
        stats["requests"] = stats.get("requests", 0) + 1
        try:
            async with asyncio.timeout(min(4, max(0, deadline - time.monotonic()))):
                async with self.client.stream("GET", YMGAL + path, params=params, headers=headers,
                                              follow_redirects=False) as response:
                    if response.status_code == 429:
                        retry = response.headers.get("Retry-After", "")
                        self._cooldown = time.monotonic() + (min(300, int(retry)) if retry.isascii() and retry.isdigit() and len(retry) < 6 else 30)
                        raise YmgalError("rate_limited")
                    if response.status_code != 200:
                        raise YmgalError(f"HTTP_{response.status_code}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise YmgalError("response_too_large")
                    payload = json.loads(body)
        except (httpx.TimeoutException, TimeoutError):
            raise YmgalError("timeout") from None
        except (httpx.HTTPError, ValueError):
            raise YmgalError("invalid_response") from None
        if not isinstance(payload, dict):
            raise YmgalError("invalid_response")
        return payload

    async def _get(self, path, params, deadline, stats, *, ttl):
        if self._invalid_credentials:
            raise YmgalError("invalid_configuration")
        cache_key = (path, tuple(sorted(params.items())))
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                async with self._lock:
                    cached = self._cache.get(cache_key)
                    if cached and cached[0] > time.monotonic():
                        self._cache.move_to_end(cache_key)
                        stats["cached"] = stats.get("cached", 0) + 1
                        return cached[1]
                    for attempt in range(2):
                        if not self._token:
                            payload = await self._request("/oauth/token", {
                                "grant_type": "client_credentials", "scope": "public",
                                "client_id": self._credentials[0], "client_secret": self._credentials[1],
                            }, {}, deadline, stats)
                            token = payload.get("access_token")
                            if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,4096}", token):
                                raise YmgalError("authentication_failed")
                            self._token = token
                        try:
                            payload = await self._request(path, params, {
                                "Authorization": "Bearer " + self._token,
                                "Accept": "application/json;charset=utf-8", "version": "1",
                            }, deadline, stats)
                        except YmgalError as error:
                            if error.code == "HTTP_401":
                                self._token = ""
                                if not attempt:
                                    continue
                            raise
                        if payload.get("code") == 401:
                            self._token = ""
                            if not attempt:
                                continue
                        if payload.get("code") == 429:
                            self._cooldown = time.monotonic() + 30
                            raise YmgalError("rate_limited")
                        if payload.get("success") is not True or payload.get("code") != 0:
                            code = payload.get("code")
                            raise YmgalError(f"API_{code}" if type(code) is int else "invalid_response")
                        value = payload.get("data")
                        self._cache[cache_key] = (time.monotonic() + ttl, value)
                        self._cache.move_to_end(cache_key)
                        while len(self._cache) > 128:
                            self._cache.popitem(last=False)
                        return value
        except TimeoutError:
            raise YmgalError("timeout") from None
        raise YmgalError("authentication_failed")

    async def search(self, keyword: str, *, deadline: float, stats: dict) -> list[dict]:
        keyword = " ".join(keyword.split())[:95]
        if not keyword:
            return []
        page = await self._get("/open/archive/search-game", {
            "mode": "list", "keyword": keyword, "pageNum": 1, "pageSize": 8,
        }, deadline, stats, ttl=300)
        return _records(page.get("result") if isinstance(page, dict) else None)

    async def detail(self, gid: str, *, deadline: float, stats: dict) -> dict:
        if not re.fullmatch(r"[0-9]{1,20}", gid):
            raise YmgalError("invalid_id")
        value = await self._get("/open/archive", {"gid": gid}, deadline, stats, ttl=1800)
        if not isinstance(value, dict) or not isinstance(value.get("game"), dict):
            raise YmgalError("invalid_response")
        return value

    async def releases(self, start: date, end: date, *, deadline: float, stats: dict) -> list[dict]:
        if not 0 < (end - start).days <= 50:
            raise YmgalError("invalid_date_window")
        value = await self._get("/open/archive/game", {
            "releaseStartDate": start.isoformat(), "releaseEndDate": (end - timedelta(days=1)).isoformat(),
        }, deadline, stats, ttl=300)
        return _records(value)

    async def news(self, *, deadline: float, stats: dict) -> list[dict]:
        value = await self._get("/open/topic/news", {"page": 1}, deadline, stats, ttl=300)
        return _records(value)


def _records(value) -> list[dict]:
    if not isinstance(value, list):
        raise YmgalError("invalid_response")
    return [row for row in value if isinstance(row, dict)]
