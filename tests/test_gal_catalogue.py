import gzip
import json
import unittest
from datetime import date

import httpx

from maibot_acg_search.gal_catalogue import Entry, _egs_statement, _merge, gal_recommend
from maibot_acg_search.ymgal import YmgalClient


EGS_HTML = """<div id="query_result_main"><table>
<tr><th>id</th><th>gamename</th><th>sellday</th><th>median</th><th>count2</th></tr>
<tr><td>19</td><td>月の旅</td><td>2024-05-01</td><td>84</td><td>230</td></tr>
</table></div>"""


class GalCatalogueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        self.steam_chinese = True
        self.steam_link = False
        self.steam_links = ()
        self.steam_languages = {}
        self.steam_type = "game"
        self.official_edition = True
        self.vndb_developers = []
        self.steam_developers = []
        self.bangumi_available = self.egs_available = self.ymgal_available = True

    def response(self, request):
        self.calls.append((request.url.host, request.url.path, request))
        if request.url.host == "www.ymgal.games":
            if not self.ymgal_available:
                return httpx.Response(503)
            if request.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "test-token"})
            game = {"gid": "21", "name": "月の旅", "chineseName": "月之旅", "releaseDate": "2024-05-01",
                    "haveChinese": True, "extensionName": [{"name": "月旅"}], "state": "PUBLIC_PUBLISHED"}
            if request.url.path.endswith("/search-game"):
                data = {"result": [game]}
            elif request.url.path == "/open/archive":
                data = {"game": {**game, "staff": [{"empName": "作家甲", "jobName": "脚本"}]}}
            elif request.url.path.endswith("/news"):
                data = [{"topicId": "22", "title": "一篇资讯", "publishTime": "2026-11-05 10:00:00"}]
            else:
                data = [game]
            return httpx.Response(200, json={"success": True, "code": 0, "data": data})
        if request.url.host == "api.bgm.tv":
            if not self.bangumi_available:
                return httpx.Response(403)
            return httpx.Response(200, json={"data": [{"id": 17, "type": 4, "name": "月の旅", "name_cn": "月之旅",
                "date": "2024-05-01", "rating": {"score": 8, "total": 120}, "collection": {"collect": 300}}]})
        if request.url.host == "erogamescape.dyndns.org":
            return (httpx.Response(200, content=gzip.compress(EGS_HTML.encode()), headers={"Content-Encoding": "gzip"})
                    if self.egs_available else httpx.Response(503))
        if request.url.path.endswith("/vn"):
            return httpx.Response(200, json={"results": [{"id": "v18", "title": "Moon Journey", "alttitle": "月の旅",
                "released": "2024-05-01", "rating": 78, "votecount": 180, "length_minutes": 180,
                "languages": ["en", "zh-Hans"], "platforms": ["win"], "tags": [
                    {"name": "Mystery", "rating": 2, "spoiler": 0}, {"name": "Netorare", "rating": 2, "spoiler": 2}],
                "developers": [{"name": name} for name in self.vndb_developers],
                "extlinks": [{"url": f"https://store.steampowered.com/app/{id_}/"}
                             for id_ in self.steam_links or (("50",) if self.steam_link else ())]}]})
        if request.url.path.endswith("/tag"):
            return httpx.Response(200, json={"results": [{"id": "g9", "name": "Mystery"}]})
        if request.url.path.endswith("/release"):
            return httpx.Response(200, json={"results": [{"id": "r20", "released": "2024-05-01", "title": "月の旅",
                "languages": [{"lang": "zh-Hans", "mtl": False}], "platforms": ["win"],
                "vns": [{"id": "v18", "rtype": "complete"}], "official": self.official_edition,
                "patch": not self.official_edition, "extlinks": []}]})
        if request.url.path.endswith("/storesearch"):
            return httpx.Response(200, json={"items": [{"id": 50, "name": "Moon Journey"}, {"id": 51, "name": "Moon Journey 2"}]})
        if request.url.path.endswith("/appdetails"):
            id_ = request.url.params["appids"]
            return httpx.Response(200, json={id_: {"success": True, "data": {"name": "Moon Journey", "steam_appid": int(id_),
                "type": self.steam_type, "release_date": {"coming_soon": False, "date": "1 May, 2024"},
                "developers": self.steam_developers,
                "platforms": {"windows": True, "linux": False}, "price_overview": {"currency": "USD", "final_formatted": "$9.99"},
                "supported_languages": self.steam_languages.get(id_, "English, Simplified Chinese" if self.steam_chinese else "English")}}})
        if "/IUserReviewsService/" in request.url.path:
            return httpx.Response(200, json={"response": {"query_summary": {
                "total_positive": 125, "total_negative": 10, "total_reviews": 135, "review_score_desc": "Very Positive"}}})
        raise AssertionError(str(request.url))

    async def search(self, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.response)) as client:
            return await gal_recommend(client, "推荐一部 Galgame", now=date(2026, 11, 6),
                                       ymgal=YmgalClient(client, min_interval=0), **{"count": 1, **kwargs})

    async def test_five_independent_sites_and_platform_specific_rating(self):
        result = await self.search(year=2024, ranking="egs", min_score=80, max_hours=5, preferences=("推理",), count=1)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["score_filter_source"], "erogamescape")
        item = result["items"][0]
        self.assertEqual(item["title"], "月之旅")
        self.assertEqual({row["source"]: row["rating"] for row in item["records"]},
                         {"vndb": 78, "bangumi": 8, "erogamescape": 84, "ymgal": None})
        self.assertEqual(item["steam"]["match"], "exact_title")
        self.assertEqual(item["steam"]["status"], "verified_released_listing")
        self.assertEqual(item["tags"], ["推理"])
        self.assertEqual(result["sources"]["steam"]["requests"], 2)
        self.assertEqual(set(result["sources"]), {"bangumi", "vndb", "erogamescape", "steam", "ymgal"})
        self.assertEqual(item["missing_sources"], [])

    async def test_explicit_steam_language_requires_valve_evidence(self):
        self.steam_link = True
        result = await self.search(platform="Steam", language="简中")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["items"][0]["steam"]["match"], "vndb_link")
        self.steam_chinese = False
        rejected = await self.search(platform="Steam", language="简中")
        self.assertEqual(rejected["reason"], "steam_listing_or_language_not_verified")

    async def test_no_verified_steam_listing_does_not_cancel_general_recommendation(self):
        self.steam_type = "dlc"
        result = await self.search()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["items"][0]["missing_sources"], ["steam"])
        self.assertEqual(result["items"][0]["steam"]["status"], "no_verified_listing")
        strict = await self.search(platform="Steam")
        self.assertEqual(strict["status"], "insufficient_evidence")

    async def test_two_catalogues_required_and_exclusions_check_spoiler_tags(self):
        self.egs_available = False
        self.assertEqual((await self.search())["status"], "partial")
        self.assertEqual((await self.search(exclusions=("NTR",)))["status"], "insufficient_evidence")
        self.bangumi_available = False
        self.assertEqual((await self.search())["status"], "partial")
        self.ymgal_available = False
        self.assertEqual((await self.search())["status"], "insufficient_evidence")

    async def test_edition_constraints_are_bound_to_one_release(self):
        result = await self.search(platform="PC", language="中文")
        self.assertEqual(result["items"][0]["edition"]["url"], "https://vndb.org/r20")
        requests = [req for _, path, req in self.calls if path.endswith("/release")]
        self.assertTrue(any(["vn", "=", ["id", "=", "v18"]] in json.loads(req.content)["filters"]
                            for req in requests))

    async def test_official_chinese_does_not_accept_unofficial_translation(self):
        self.official_edition = False
        self.assertEqual((await self.search(language="中文"))["status"], "ok")
        result = await self.search(official_translation=True)
        self.assertEqual(result["reason"], "official_localized_edition_not_verified")
        self.official_edition = True
        result = await self.search(official_translation=True)
        self.assertTrue(result["items"][0]["edition"]["official"])

    async def test_official_chinese_on_steam_uses_verified_store_languages(self):
        self.official_edition = False
        self.steam_chinese = False
        result = await self.search(platform="Steam", official_translation=True)
        self.assertEqual(result["reason"], "steam_listing_or_language_not_verified")
        self.steam_chinese = True
        self.assertEqual((await self.search(platform="Steam", official_translation=True))["status"], "ok")

    async def test_same_steam_title_with_conflicting_developer_is_not_verified(self):
        self.vndb_developers = ["Catalogue Studio"]
        self.steam_developers = ["Different Studio"]
        result = await self.search()
        self.assertEqual(result["items"][0]["steam"]["status"], "unconfirmed_identity")
        self.assertEqual((await self.search(platform="Steam"))["status"], "insufficient_evidence")

    async def test_release_ranges_are_exclusive_and_rechecked_locally(self):
        good = await self.search(years=(2024, 2026), date_from="2024-05-01", date_to="2024-05-02")
        self.assertEqual(good["status"], "ok")
        bad = await self.search(date_from="2024-04-01", date_to="2024-05-01")
        self.assertEqual(bad["reason"], "no_cross_checked_catalogue_matches")
        gap = await self.search(years=(2023, 2025))
        self.assertEqual(gap["reason"], "no_cross_checked_catalogue_matches")

    async def test_steam_selects_later_linked_app_that_supports_requested_language(self):
        self.steam_links = ("50", "51")
        self.steam_languages = {"50": "Japanese", "51": "Simplified Chinese"}
        result = await self.search(platform="Steam", language="简中")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["items"][0]["steam"]["app_id"], "51")

    async def test_ymgal_alias_detail_and_steam_review_evidence(self):
        result = await self.search(entities=("月旅",), task="details", fields=("duration", "staff", "reviews"))
        self.assertEqual(result["status"], "ok")
        item = result["items"][0]
        self.assertEqual(item["title"], "月之旅")
        self.assertEqual(item["length_minutes"], 180)
        self.assertEqual(item["steam"]["review_summary"]["reviews"], 135)
        self.assertEqual(next(row for row in item["records"] if row["source"] == "ymgal")["staff"][0]["name"], "作家甲")

    async def test_calendar_and_news_keep_their_scope(self):
        release = await self.search(task="release", date_from="2024-05-01", date_to="2024-05-03")
        self.assertEqual(release["items"][0]["first_release"], "2024-05-01")
        self.assertTrue(any(path == "/open/archive/game" for _, path, _ in self.calls))
        news = await self.search(task="news")
        self.assertEqual(news["news"][0]["id"], "22")
        self.assertEqual(news["sources"]["steam"]["status"], "not_applicable")

    async def test_invalid_egs_proxy_isolated_to_egs(self):
        result = await self.search(egs_client=False)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["sources"]["erogamescape"]["errors"], ["invalid_proxy"])
        self.assertFalse(any(host == "erogamescape.dyndns.org" for host, _, _ in self.calls))


class GalIdentityTests(unittest.TestCase):
    def test_different_years_or_ambiguous_title_are_not_merged_by_name(self):
        first = Entry("vndb", "v1", "Title", "", ("Title",), released="2024")
        earlier = Entry("bangumi", "1", "Title", "", ("Title",), released="2020")
        newer = Entry("bangumi", "2", "Title", "", ("Title",), released="2024")
        duplicate = Entry("bangumi", "3", "Title", "", ("Title",), released="2024")
        self.assertEqual([len(group) for group in _merge([first, earlier])], [1, 1])
        self.assertEqual([len(group) for group in _merge([first, newer, duplicate])], [1, 1, 1])
        first.bindings["bangumi"] = "2"
        self.assertEqual([len(group) for group in _merge([first, newer, duplicate])], [2, 1])

    def test_egs_sql_escapes_title_as_a_literal(self):
        query = _egs_statement(("It's a title\\'; SELECT 2",), ("2024-01-01", "2025-01-01"), 30, "erogamescape", 80, None)
        self.assertIn(r"gamename = E'It''s a title\\''; SELECT 2'", query)
        self.assertTrue(query.endswith("ORDER BY median DESC, count2 DESC, id LIMIT 30"))
