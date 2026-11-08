"""Search backend selection and normalization. Source text stays untrusted."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import unquote, urlsplit

MAX_RESPONSE_BYTES = 512 * 1024
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _text(value, limit: int, *, secret: str = "") -> str:
    if not isinstance(value, str):
        return ""
    if secret:
        value = value.replace(secret, "[已隐藏]")
    return " ".join(value.split())[:limit]


def valid_source_url(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
        return False
    try:
        url = urlsplit(value)
        return bool(url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password)
    except ValueError:
        return False


def valid_api_url(value: str) -> bool:
    if not valid_source_url(value):
        return False
    try:
        url = urlsplit(value)
        if url.port is not None and not 1 <= url.port <= 65535:
            return False
    except ValueError:
        return False
    return not url.fragment and not url.query and (
        url.scheme == "https" or url.hostname in LOOPBACK_HOSTS
    )


def _normalize(payload, max_results: int, *, secret: str = "") -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("invalid_results")
    found = []
    seen = set()
    for item in payload["results"][:100]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not valid_source_url(url) or url in seen:
            continue
        if secret and (secret in url or secret in unquote(url)):
            continue
        seen.add(url)
        snippets = (_text(item.get(key), 800, secret=secret) for key in ("snippet", "content", "summary"))
        found.append({
            "title": _text(item.get("title"), 180, secret=secret) or urlsplit(url).hostname,
            "url": url,
            "snippet": next((value for value in snippets if value), ""),
            "published_at": _text(item.get("published_at") or item.get("published_date"), 50, secret=secret),
        })
        if len(found) >= max_results:
            break
    return found


def backend_status(api_url: str, api_key: str) -> tuple[str, str]:
    if not api_url and not api_key:
        return "maibot", ""
    if not api_url or not api_key:
        return "custom", "自定义搜索需要同时填写 API 地址和 Key。"
    if not valid_api_url(api_url):
        return "custom", "自定义搜索需要完整 HTTPS 地址，本机回环地址也可使用 HTTP。"
    if any(not 32 < ord(char) < 127 for char in api_key):
        return "custom", "自定义搜索 API Key 格式无效。"
    return "custom", ""


async def search_web(
    ctx, client, query: str, *, api_url: str = "", api_key: str = "",
    api_protocol: str = "tavily", max_results: int = 6, timeout: float = 10,
    loopback_client=None,
) -> dict:
    """When configured, send web queries exclusively to the custom endpoint."""
    api_url, api_key = api_url.strip(), api_key.strip()
    provider, reason = backend_status(api_url, api_key)
    result = {"provider": provider, "results": []}
    if reason:
        return {**result, "status": "unavailable", "reason": reason}
    query = _text(query, 1000)
    if not query:
        return {**result, "status": "empty", "reason": "没有可检索的问题。"}

    try:
        async with asyncio.timeout(timeout):
            if provider == "maibot":
                payload = await ctx.web.search(query, max_results=max_results)
            else:
                custom_client = client
                if loopback_client is not None and urlsplit(api_url).hostname in LOOPBACK_HOSTS:
                    custom_client = loopback_client
                async with custom_client.stream(
                    "POST", api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}", "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    json={"query": query, "max_results": max_results},
                    follow_redirects=False,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        return {
                            **result, "status": "error",
                            "reason": f"自定义搜索返回 HTTP {response.status_code}。",
                        }
                    # Bound the buffered JSON without expanding compressed response data.
                    if response.headers.get("Content-Encoding", "").strip().lower() not in {"", "identity"}:
                        raise ValueError("unexpected_content_encoding")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ValueError("response_too_large")
                        body.extend(chunk)
                    payload = json.loads(body)
                if api_protocol == "anysearch":
                    if isinstance(payload, dict) and payload.get("code") not in (None, 0):
                        return {**result, "status": "error", "reason": "AnySearch 返回业务错误。"}
                    payload = payload.get("data") if isinstance(payload, dict) else None
            hits = _normalize(payload, max_results, secret=api_key if provider == "custom" else "")
    except TimeoutError:
        return {**result, "status": "error", "reason": "网页搜索超时。"}
    except Exception as error:
        if provider == "maibot" and len(query) > 128 and "DeepSeek 搜索词不能超过 128 个字符" in str(error):
            return {**result, "status": "error", "reason": "MaiBot 当前的 DeepSeek 搜索要求查询不超过 128 字，请缩短后重试。"}
        reason = (
            "MaiBot 原生搜索未能返回结果，请检查 WebUI 中是否分配了联网搜索服务。"
            if provider == "maibot" else "自定义搜索未能返回有效结果，请检查接口、凭据和返回格式。"
        )
        return {**result, "status": "error", "reason": reason}
    if not hits:
        return {**result, "status": "empty", "reason": "搜索未返回带有效来源链接的资料。"}
    return {**result, "status": "ok", "results": hits}
