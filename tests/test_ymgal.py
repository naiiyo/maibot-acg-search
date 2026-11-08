import time
import unittest
from datetime import date

import httpx

from maibot_acg_search.ymgal import YmgalClient, YmgalError


class YmgalClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_refreshes_rejected_token_once_and_caches_valid_response(self):
        tokens = []
        search_calls = []

        def respond(request):
            if request.url.path == "/oauth/token":
                token = f"test-token-{len(tokens) + 1}"
                tokens.append(token)
                self.assertEqual(request.url.params["grant_type"], "client_credentials")
                return httpx.Response(200, json={"access_token": token})
            search_calls.append(request)
            self.assertEqual(request.headers["version"], "1")
            self.assertEqual(request.url.params["mode"], "list")
            if len(search_calls) == 1:
                return httpx.Response(401)
            self.assertEqual(request.headers["Authorization"], "Bearer test-token-2")
            return httpx.Response(200, json={"success": True, "code": 0, "data": {"result": [
                {"id": "9007199254740993", "name": "作品"},
            ]}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter = YmgalClient(client, client_id="private-id", client_secret="private-secret", min_interval=0)
            stats = {}
            with self.assertLogs("httpx", "INFO") as output:
                found = await adapter.search("别名", deadline=time.monotonic() + 5, stats=stats)
            self.assertEqual(found[0]["id"], "9007199254740993")
            self.assertEqual(stats["requests"], 4)
            self.assertNotIn("private-secret", "\n".join(output.output))
            cached = {}
            self.assertEqual(await adapter.search("别名", deadline=time.monotonic() + 5, stats=cached), found)
            self.assertEqual(cached, {"cached": 1})

    async def test_cooldown_and_half_configured_credentials_do_not_send_requests(self):
        calls = []

        def respond(request):
            calls.append(request)
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "fixture-token"})
            return httpx.Response(200, json={"success": False, "code": 429, "msg": "provider detail"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            deadline = time.monotonic() + 5
            invalid = YmgalClient(client, client_id="custom-only")
            with self.assertRaisesRegex(YmgalError, "invalid_configuration"):
                await invalid.search("关键词", deadline=deadline, stats={})
            self.assertEqual(calls, [])
            valid = YmgalClient(client, min_interval=0)
            with self.assertRaisesRegex(YmgalError, "rate_limited"):
                await valid.search("关键词", deadline=deadline, stats={})
            with self.assertRaisesRegex(YmgalError, "rate_limited"):
                await valid.search("另一个关键词", deadline=deadline, stats={})
            self.assertEqual(len(calls), 2)

    async def test_release_dates_translate_exclusive_end_to_site_interval(self):
        def respond(request):
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "fixture-token"})
            self.assertEqual(request.url.params["releaseStartDate"], "2026-11-01")
            self.assertEqual(request.url.params["releaseEndDate"], "2026-11-05")
            return httpx.Response(200, json={"success": True, "code": 0, "data": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter = YmgalClient(client, min_interval=0)
            deadline = time.monotonic() + 5
            self.assertEqual(await adapter.releases(date(2026, 11, 1), date(2026, 11, 6), deadline=deadline, stats={}), [])
            with self.assertRaisesRegex(YmgalError, "invalid_date_window"):
                await adapter.releases(date(2026, 11, 1), date(2027, 1, 1), deadline=deadline, stats={})
