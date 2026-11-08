import json
import tomllib
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from maibot_sdk.context import PluginContext

from maibot_acg_search.intent import classify
from maibot_acg_search.plugin import _format_report, create_plugin


NATIVE_HIT = {"title": "官方资料", "url": "https://example.org/source", "snippet": "可核验的摘要"}


class SDKIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []

        async def rpc(method, plugin_id, payload, *, timeout_ms=None):
            self.assertEqual(plugin_id, "naiiyo.acg-search")
            self.calls.append((method, payload))
            if payload["capability"] == "web.search":
                return {"success": True, "result": {"results": [NATIVE_HIT], "service": "host-search"}}
            return {"success": True}

        self.plugin = create_plugin()
        self.plugin._set_context(PluginContext("naiiyo.acg-search", rpc_call=rpc))
        self.plugin.set_plugin_config({})
        await self.plugin.on_load()
        self.addAsyncCleanup(self.plugin.on_unload)

    def test_components_and_config_schema(self):
        manifest_path = Path(__file__).resolve().parents[1] / "_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        required_fields = {
            "manifest_version", "id", "version", "name", "description",
            "author", "license", "urls", "host_application", "sdk", "i18n",
        }
        self.assertEqual(required_fields - manifest.keys(), set())
        self.assertTrue(manifest["license"].strip())
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertIn("web.search", manifest["capabilities"])
        components = {item["name"]: item for item in self.plugin.get_components()}
        self.assertEqual({item["type"] for item in components.values()}, {"TOOL", "HOOK_HANDLER", "COMMAND"})
        self.assertTrue(components["acg_search"]["metadata"]["core_tool"])
        sections = self.plugin.get_webui_config_schema()["sections"]
        self.assertEqual(set(sections), {"plugin", "search", "catalogue"})
        self.assertTrue(sections["plugin"]["fields"]["config_version"]["hidden"])
        self.assertEqual(sections["catalogue"]["fields"]["egs_proxy"]["ui_type"], "password")
        fields = sections["search"]["fields"]
        self.assertEqual(fields["api_key"]["input_type"], "password")
        self.assertEqual(fields["api_url"]["default"], "")
        self.assertEqual(fields["api_protocol"]["default"], "tavily")

    def test_packaged_toml_matches_sdk_defaults_and_preserves_version_on_enable(self):
        config_path = Path(__file__).resolve().parents[1] / "config.example.toml"
        self.assertFalse(config_path.with_name("config.toml").exists())
        with config_path.open("rb") as handle:
            seed = tomllib.load(handle)
        self.assertEqual(seed, self.plugin.get_default_config())
        for enabled in (False, True):
            seed["plugin"]["enabled"] = enabled
            self.plugin.set_plugin_config(seed)
            self.assertEqual(self.plugin.config.plugin.config_version, "0.1.0")
            self.assertIs(self.plugin.config.plugin.enabled, enabled)

    async def test_default_uses_real_sdk_capability_for_general_questions(self):
        for text in ("推荐一部26年的手机/电脑", "评价使命召唤23（26年新出的系列游戏）"):
            with patch("maibot_acg_search.plugin._today", return_value=date(2026, 11, 6)):
                report = await self.plugin.handle_search(query=text)
            self.assertEqual((report["route"], report["status"]), ("web", "ok"))
            self.assertEqual(report["web"][0]["provider"], "maibot")
            method, payload = self.calls[-1]
            self.assertEqual((method, payload["capability"]), ("cap.call", "web.search"))
            self.assertIn("2026年", payload["args"]["query"])
        self.assertEqual(len(self.calls), 2)

    async def test_native_deepseek_length_error_keeps_user_query_and_explains_limit(self):
        async def rpc(method, plugin_id, payload, *, timeout_ms=None):
            self.calls.append(payload["args"]["query"])
            raise RuntimeError("DeepSeek 搜索词不能超过 128 个字符，请缩短后重试")

        self.plugin._set_context(PluginContext("naiiyo.acg-search", rpc_call=rpc))
        query = "请查一下 " + "原始条件" * 32
        report = await self.plugin.handle_search(query)
        self.assertEqual(report["status"], "error")
        self.assertIn("128 字", report["web"][0]["reason"])
        self.assertEqual(self.calls, [query])

    async def test_custom_switch_and_reset(self):
        requested = []

        async def transport(request):
            requested.append(request)
            return httpx.Response(200, json={"results": [{"title": "资料", "url": "https://example.org/custom",
                                                          "content": "兼容 Tavily 的摘要"}]})

        await self.plugin._client.aclose()
        self.plugin._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.plugin.set_plugin_config({
            "plugin": {"config_version": "0.1.0"},
            "search": {"api_url": "https://search.example/search", "api_key": "fake-key"},
        })
        await self.plugin.on_config_update("self", {}, "0.1.0")
        report = await self.plugin.handle_search("查一下手机推荐")
        self.assertEqual(report["web"][0]["provider"], "custom")
        self.assertEqual(report["web"][0]["results"][0]["snippet"], "兼容 Tavily 的摘要")
        self.assertEqual(self.calls, [])
        self.assertEqual(requested[0].headers["Authorization"], "Bearer fake-key")
        self.assertEqual(json.loads(requested[0].content)["max_results"], 6)
        self.assertNotIn("fake-key", json.dumps(report))
        self.plugin.set_plugin_config({})
        await self.plugin.handle_search("查一下手机推荐")
        self.assertEqual(self.calls[0][1]["capability"], "web.search")

    async def test_incomplete_custom_config_never_calls_host_or_custom(self):
        self.plugin.set_plugin_config({
            "plugin": {"config_version": "0.1.0"}, "search": {"api_key": "fake-key"},
        })
        report = await self.plugin.handle_search("推荐一部手机")
        self.assertEqual(report["status"], "unavailable")
        self.assertEqual(self.calls, [])

    async def test_anysearch_uses_documented_envelope_and_preserves_native_default(self):
        seen = []

        def transport(request):
            seen.append(request)
            return httpx.Response(200, json={"code": 0, "data": {"results": [NATIVE_HIT]}})

        await self.plugin._client.aclose()
        self.plugin._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.plugin.set_plugin_config({
            "plugin": {"config_version": "0.1.0"},
            "search": {"api_protocol": "anysearch"},
        })
        report = await self.plugin.handle_search("推荐一部手机")
        self.assertEqual(report["web"][0]["provider"], "maibot")
        self.assertEqual(seen, [])
        self.calls.clear()
        self.plugin.set_plugin_config({
            "plugin": {"config_version": "0.1.0"},
            "search": {
                "api_protocol": "anysearch", "api_url": "https://api.anysearch.com/v1/search", "api_key": "fake-key",
            },
        })
        report = await self.plugin.handle_search("推荐一部手机")
        self.assertEqual((report["status"], report["web"][0]["provider"]), ("ok", "custom"))
        self.assertEqual(report["web"][0]["results"][0]["url"], NATIVE_HIT["url"])
        self.assertEqual((seen[0].method, str(seen[0].url)), ("POST", "https://api.anysearch.com/v1/search"))
        self.assertEqual(seen[0].headers["Authorization"], "Bearer fake-key")
        self.assertEqual(json.loads(seen[0].content), {"query": "推荐一部手机", "max_results": 6})
        self.assertEqual(self.calls, [])

    async def test_anysearch_business_error_never_exposes_response_or_falls_back(self):
        def transport(request):
            return httpx.Response(200, json={"code": 402, "message": "private-body", "data": {"results": [NATIVE_HIT]}})

        await self.plugin._client.aclose()
        self.plugin._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.plugin.set_plugin_config({
            "plugin": {"config_version": "0.1.0"},
            "search": {
                "api_protocol": "anysearch", "api_url": "https://api.anysearch.com/v1/search", "api_key": "fake-key",
            },
        })
        report = await self.plugin.handle_search("推荐一部手机")
        self.assertEqual((report["status"], report["web"][0]["results"]), ("error", []))
        self.assertNotIn("private-body", json.dumps(report))
        self.assertEqual(self.calls, [])

    async def test_custom_redirect_is_rejected_without_native_fallback(self):
        seen = []

        def respond(request):
            seen.append(request.url.host)
            return httpx.Response(302, headers={"location": "https://elsewhere.example/search"})

        await self.plugin._client.aclose()
        self.plugin._client = httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True)
        self.plugin.set_plugin_config({
            "plugin": {"config_version": "0.1.0"},
            "search": {"api_url": "https://search.example/search", "api_key": "fake-key"},
        })
        report = await self.plugin.handle_search("推荐一部手机")
        self.assertEqual((report["status"], report["web"][0]["provider"]), ("error", "custom"))
        self.assertEqual(seen, ["search.example"])
        self.assertEqual(self.calls, [])

    async def test_no_web_and_clarification_do_not_dispatch(self):
        self.assertEqual((await self.plugin.handle_search("别联网，推荐个galgame"))["status"], "skipped")
        self.assertEqual((await self.plugin.handle_search("推荐一点二次元作品"))["status"], "clarify")
        self.assertEqual(self.calls, [])

    async def test_hook_prefers_plugin_without_removing_native_search(self):
        native = {"function": {"name": "web_search"}}
        tool = {"function": {"name": "acg_search"}}
        fetch = {"function": {"name": "web_fetch"}}
        self.assertEqual(await self.plugin.route_planner_search([native]), {})
        context = {
            "session_id": "session", "items": [{"item_type": "UserMessageItem", "parts": [
                {"type": "text", "text": '<message msg_id="101" time="12:00:00" user="Alice">\n请查 iPhone 17 当前价格'},
            ]}],
            "item_schema_version": 1, "selection_reason": "history", "selected_history_count": 1,
            "built_message_count": 2,
        }
        self.assertEqual(await self.plugin.route_planner_search([native, tool, fetch], **context),
                         {"modified_kwargs": {**context, "tool_definitions": [tool, native, fetch]}})
        self.assertEqual(
            await self.plugin.route_planner_search([native, tool, fetch], hook_name="maisaka.planner.before_request", **context),
            {"modified_kwargs": {**context, "tool_definitions": [tool, native, fetch]}},
        )

    async def test_command_executes_and_sends_sources(self):
        result = await self.plugin.handle_command(
            stream_id="session", matched_groups={"query": "手机推荐"},
        )
        self.assertTrue(result[0])
        self.assertEqual(self.calls[-1][1]["capability"], "send.text")
        self.assertIn(NATIVE_HIT["url"], self.calls[-1][1]["args"]["text"])

    async def test_command_keeps_original_query_and_respects_transform_guard(self):
        await self.plugin.handle_command(stream_id="session", matched_groups={"query": "Galgame是什么"})
        self.assertEqual(self.calls[0][1]["args"]["query"], "Galgame是什么")
        self.calls.clear()
        await self.plugin.handle_command(stream_id="session", matched_groups={"query": "翻译这段Galgame简介"})
        self.assertEqual([entry[1]["capability"] for entry in self.calls], ["send.text"])

    async def test_command_reports_failed_delivery(self):
        async def rpc(method, plugin_id, payload, *, timeout_ms=None):
            return {"success": False}

        self.plugin._set_context(PluginContext("naiiyo.acg-search", rpc_call=rpc))
        result = await self.plugin.handle_command(stream_id="session")
        self.assertEqual(result, (False, "发送检索结果失败", 1))

    async def test_supplemental_hits_do_not_verify_a_price_constraint(self):
        catalogue = AsyncMock(return_value={"status": "ok", "items": [{"title": "候选作品"}]})
        with patch("maibot_acg_search.search_flow.gal_recommend", catalogue):
            report = await self.plugin.handle_search("推荐一部 Galgame，预算 100 元")
        self.assertEqual(report["web"][0]["status"], "ok")
        self.assertEqual(report["status"], "partial")

    async def test_unverified_catalogue_uses_separate_native_supplement(self):
        catalogue = AsyncMock(return_value={"status": "insufficient_evidence", "items": [], "reason": "no_cross_match"})
        with patch("maibot_acg_search.search_flow.gal_recommend", catalogue):
            report = await self.plugin.handle_search("推荐一部2026年的galgame")
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["catalogue"]["items"], [])
        self.assertEqual(report["web"][0]["provider"], "maibot")
        self.assertEqual(catalogue.await_args.kwargs["year"], 2026)

    async def test_galgame_search_passes_requested_playtime_to_catalogue(self):
        catalogue = AsyncMock(return_value={"status": "ok", "items": [{"title": "候选作品"}]})
        with patch("maibot_acg_search.search_flow.gal_recommend", catalogue):
            report = await self.plugin.handle_search("推荐一部5小时内的Galgame")
        self.assertEqual(report["status"], "ok")
        self.assertEqual(catalogue.await_args.kwargs["max_hours"], 5)
        self.assertEqual(self.calls, [])

    async def test_galgame_search_distinguishes_official_chinese_from_translation(self):
        catalogue = AsyncMock(return_value={"status": "ok", "items": [{"title": "候选作品"}]})
        with patch("maibot_acg_search.search_flow.gal_recommend", catalogue):
            await self.plugin.handle_search("推荐一部有官中的Galgame")
            self.assertTrue(catalogue.await_args.kwargs["official_translation"])
            await self.plugin.handle_search("推荐一部有汉化的Galgame")
            self.assertFalse(catalogue.await_args.kwargs["official_translation"])

    async def test_named_galgame_and_relative_release_use_specialized_tasks(self):
        catalogue = AsyncMock(return_value={"status": "partial", "items": [{"title": "候选作品"}]})
        with patch("maibot_acg_search.search_flow.gal_recommend", catalogue):
            report = await self.plugin.handle_search("近月少女的礼仪通关要多长")
            self.assertEqual(report["status"], "partial")
            self.assertEqual(catalogue.await_args.kwargs["task"], "details")
            self.assertEqual(catalogue.await_args.kwargs["fields"], ("duration",))
            self.assertEqual(catalogue.await_args.kwargs["entities"], ("近月少女的礼仪",))
            await self.plugin.handle_search("这个月有什么Galgame新作")
            self.assertEqual(catalogue.await_args.kwargs["task"], "release")
            self.assertTrue(catalogue.await_args.kwargs["date_from"])

    async def test_related_medium_does_not_skip_galgame_sources(self):
        catalogue = AsyncMock(return_value={"status": "ok", "items": [{"title": "CLANNAD"}]})
        anime = AsyncMock(return_value={"status": "ok", "results": [{"title": "CLANNAD"}]})
        with (patch("maibot_acg_search.search_flow.gal_recommend", catalogue),
              patch("maibot_acg_search.search_flow.recommend_bangumi", anime)):
            report = await self.plugin.handle_search("动画和Galgame《CLANNAD》有什么不同")
        self.assertEqual(catalogue.await_args.kwargs["entities"], ("CLANNAD",))
        self.assertEqual(anime.await_args.kwargs["domain"], "anime")
        self.assertIn("anime", report["related_catalogues"])

    async def test_episode_path_calls_the_same_selected_web_backend(self):
        async def directory(client, query, **options):
            self.assertEqual((options["domain"], options["task"]), ("anime", "progress"))
            refs = await options["search_web"]("芙莉莲 官方放送")
            return {"status": "ok", "results": [{"title": "芙莉莲", "episode_evidence": {
                "last_catalogue_date_before_today": {"episode": 3, "catalogue_date": "2026-10-31"},
            }}], "official_broadcast": {"status": "unverified_references", "references": refs["results"]}}

        with patch("maibot_acg_search.search_flow.recommend_bangumi", directory):
            report = await self.plugin.handle_search("《葬送的芙莉莲》更新到第几集")
        self.assertEqual(report["status"], "partial")
        self.assertEqual(self.calls[0][1]["capability"], "web.search")
        rendered = _format_report(report)
        self.assertIn("目录日期 2026-10-31 · 第 3 集", rendered)
        self.assertIn("暂未取得能核对实际播出的官方放送资料", rendered)


class RoutingExampleTests(unittest.TestCase):
    def test_media_and_reference_follow_intent(self):
        for query, expected in {
            "原神更新进度": ("catalogue", "acg_game", "progress"),
            "原神今天的卡池": ("catalogue", "acg_game", "progress"),
            "《药屋少女的呢喃》漫画连载到哪一话": ("catalogue", "manga", "progress"),
            "gal圈最近有什么新闻": ("catalogue", "galgame", "news"),
            "推荐几部2025年的科幻番": ("catalogue", "anime", "recommendation"),
        }.items():
            with self.subTest(query=query):
                result = classify(query, today=date(2026, 11, 9))
                self.assertEqual((result.route, result.medium, result.task), expected)
        result = classify("推荐一部像《CLANNAD》的Galgame")
        self.assertEqual((result.titles, result.similar_to), ((), "CLANNAD"))

    def test_supplied_examples_and_specialized_routes(self):
        for text, expected in {
            "推荐一部26年的手机/电脑": ("web", "other"),
            "评价使命召唤23（26年新出的系列游戏）": ("web", "game"),
            "推荐一部2026年的galgame": ("catalogue", "galgame"),
            "推荐一款二游": ("catalogue", "acg_game"),
            "推荐一部番剧": ("catalogue", "anime"),
            "推荐几部漫画": ("catalogue", "manga"),
        }.items():
            with self.subTest(text=text):
                intent = classify(text, today=date(2026, 11, 6))
                self.assertEqual((intent.route, intent.medium), expected)
