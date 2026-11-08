import gzip
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import httpx

from maibot_acg_search.web_search import MAX_RESPONSE_BYTES, search_web


class RecordedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk


class WebSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ctx = SimpleNamespace(web=SimpleNamespace(search=AsyncMock()))

    async def test_custom_redacts_reflected_key_before_truncation_and_drops_unsafe_urls(self):
        key = "example-secret/+=@"
        source_url = "https://sources.example/article"
        results = [
            {"url": f"{source_url}?raw={key}"},
            {"url": f"{source_url}?encoded={quote(key, safe='')}"},
            {
                "url": source_url,
                "title": "a" * 170 + key,
                "snippet": f"Upstream reflected Bearer {key}.",
                "published_at": f"2026-11-09 {key}",
            },
        ]

        def endpoint(request):
            self.assertEqual(request.headers["Authorization"], f"Bearer {key}")
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            self.assertEqual(json.loads(request.content), {"query": "查询", "max_results": 6})
            return httpx.Response(200, json={"code": 0, "data": {"results": results}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
            report = await search_web(
                self.ctx, client, "查询", api_url="https://api.example/search",
                api_key=key, api_protocol="anysearch",
            )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["results"], [{
            "url": source_url,
            "title": "a" * 170 + "[已隐藏]",
            "snippet": "Upstream reflected Bearer [已隐藏].",
            "published_at": "2026-11-09 [已隐藏]",
        }])
        self.ctx.web.search.assert_not_awaited()

    async def test_loopback_uses_direct_client_external_and_native_keep_their_routes(self):
        calls = []

        def endpoint(label):
            def handle(request):
                calls.append((label, request.url.host))
                return httpx.Response(200, json={"results": [{"url": "https://sources.example/article"}]})
            return handle

        self.ctx.web.search.return_value = {"results": [{"url": "https://sources.example/native"}]}
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(endpoint("shared"))) as client,
            httpx.AsyncClient(transport=httpx.MockTransport(endpoint("direct")), trust_env=False) as direct,
        ):
            for url in ("http://localhost:8080/search", "http://[::1]:8080/search", "https://api.example/search"):
                report = await search_web(
                    self.ctx, client, "查询", api_url=url, api_key="a-test-key",
                    loopback_client=direct,
                )
                self.assertEqual((report["provider"], report["status"]), ("custom", "ok"))
            report = await search_web(self.ctx, client, "查询", loopback_client=direct)

        self.assertEqual((report["provider"], report["status"]), ("maibot", "ok"))
        self.assertEqual(calls, [("direct", "localhost"), ("direct", "::1"), ("shared", "api.example")])
        self.ctx.web.search.assert_awaited_once_with("查询", max_results=6)

    async def test_stream_rejects_oversized_json_without_reading_further(self):
        stream = RecordedStream([b"x" * MAX_RESPONSE_BYTES, b"x", b"unread"])
        transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
        async with httpx.AsyncClient(transport=transport) as client:
            report = await search_web(
                self.ctx, client, "查询", api_url="https://api.example/search", api_key="a-test-key",
            )

        self.assertEqual((report["provider"], report["status"], report["results"]), ("custom", "error", []))
        self.assertEqual(stream.reads, 2)
        self.ctx.web.search.assert_not_awaited()

    async def test_stream_refuses_unrequested_compression_before_reading_body(self):
        stream = RecordedStream([gzip.compress(b"x" * (MAX_RESPONSE_BYTES + 1))])
        transport = httpx.MockTransport(lambda _: httpx.Response(
            200, stream=stream, headers={"Content-Encoding": "gzip"},
        ))
        async with httpx.AsyncClient(transport=transport) as client:
            report = await search_web(
                self.ctx, client, "查询", api_url="https://api.example/search", api_key="a-test-key",
            )

        self.assertEqual((report["provider"], report["status"], report["results"]), ("custom", "error", []))
        self.assertEqual(stream.reads, 0)
        self.ctx.web.search.assert_not_awaited()
