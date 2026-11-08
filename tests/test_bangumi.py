import json
import unittest
from datetime import date

import httpx

from maibot_acg_search.bangumi import recommend_bangumi


def subject(identifier, title, *, kind=2, released='2025-07-01', tags=(), **extra):
    return {
        'id': identifier, 'name_cn': title, 'name': title, 'type': kind,
        'date': released, 'nsfw': False, 'rating': {'score': 8.1, 'rank': 122, 'total': 700},
        'tags': [{'name': tag} for tag in tags], **extra,
    }


class BangumiTests(unittest.IsolatedAsyncioTestCase):
    async def test_anime_recommendation_searches_official_platform_and_applies_cour(self):
        calls = []

        def handler(request):
            self.assertEqual(request.url.path, '/v0/search/subjects')
            payload = json.loads(request.content)
            self.assertEqual(payload['filter']['type'], [2])
            self.assertEqual(payload['filter']['air_date'], ['>=2025-07-01', '<2025-10-01'])
            self.assertFalse(payload['filter']['nsfw'])
            self.assertEqual(payload['filter']['tag'], ['科幻'])
            return httpx.Response(200, json={'data': [
                subject(4, '月球信箱', tags=['科幻']),
                subject(5, '其他季度', released='2025-10-01'),
                subject(6, '小说条目', kind=1),
            ]})

        async def search_web(query):
            calls.append(query)
            return {'status': 'ok', 'provider': 'fixture', 'results': [
                {'title': '月球信箱', 'url': 'https://www.bilibili.com/bangumi/media/md123', 'snippet': '作品页'},
                {'title': '月球信箱', 'url': 'https://bilibili.com.invalid/bangumi/play/ss123', 'snippet': '镜像'},
                {'title': '其他作品', 'url': 'https://www.crunchyroll.com/series/OTHER', 'snippet': '无关内容'},
            ]}

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            output = await recommend_bangumi(client, '推荐2025年7月科幻新番', domain='anime', task='recommendation',
                                             year=2025, month=7, preferences=('科幻',), search_web=search_web)
        self.assertEqual([row['id'] for row in output['results']], [4])
        self.assertEqual(len(calls), 1)
        self.assertIn('月球信箱', calls[0])
        broadcast = output['official_broadcast']
        self.assertEqual(broadcast['status'], 'broadcaster_references')
        self.assertEqual([row['source_kind'] for row in broadcast['references']],
                         ['known_broadcaster', 'unclassified', 'unclassified'])

    async def test_progress_separates_dates_from_actual_broadcast(self):
        def handler(request):
            if request.url.path == '/v0/search/subjects':
                self.assertNotIn('air_date', json.loads(request.content)['filter'])
                return httpx.Response(200, json={'data': [subject(10, '太空邮局')]})
            self.assertEqual(request.url.path, '/v0/episodes')
            self.assertEqual(dict(request.url.params), {'subject_id': '10', 'type': '0', 'limit': '200', 'offset': '0'})
            return httpx.Response(200, json={'total': 5, 'data': [
                {'id': 201, 'type': 0, 'ep': 1, 'sort': 21, 'airdate': '2025-07-01'},
                {'id': 202, 'type': 0, 'ep': 2, 'sort': 22, 'airdate': '2025-07-08'},
                {'id': 203, 'type': 0, 'ep': 3, 'sort': 23, 'airdate': '2025-07-15'},
                {'id': 204, 'type': 1, 'ep': 9, 'sort': 9, 'airdate': '2025-07-06'},
                {'id': 205, 'type': 0, 'ep': 4, 'sort': 24, 'airdate': ''},
            ]})

        async def search_web(query):
            self.assertIn('太空邮局', query)
            return {'status': 'empty', 'results': []}

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            output = await recommend_bangumi(client, '《太空邮局》今天更新到第几集？', domain='anime', task='progress',
                                             entities=('太空邮局',), date_from='2025-07-08', date_to='2025-07-09',
                                             today=date(2025, 7, 8), search_web=search_web)
        self.assertEqual(output['official_broadcast']['status'], 'empty')
        evidence = output['results'][0]['episode_evidence']
        self.assertEqual(evidence['status'], 'catalogue_dates_only')
        self.assertEqual(evidence['last_catalogue_date_before_today']['episode'], 1)
        self.assertEqual(evidence['catalogue_dates_today'][0]['episode'], 2)
        self.assertEqual(evidence['next_catalogue_date_after_today']['episode'], 3)
        self.assertEqual(evidence['undated_entries'], 1)
        self.assertNotIn('last_aired', evidence)

    async def test_recommendation_still_searches_web_if_catalogue_is_unavailable(self):
        searched = []

        async def search_web(query):
            searched.append(query)
            return {'status': 'ok', 'results': [
                {'title': '本季作品', 'url': 'https://tv-tokyo.co.jp/anime/title/', 'snippet': '放送予定'},
            ]}

        transport = httpx.MockTransport(lambda request: httpx.Response(503))
        async with httpx.AsyncClient(transport=transport) as client:
            output = await recommend_bangumi(client, '推荐2025年7月新番', domain='anime', task='recommendation',
                                             year=2025, month=7, search_web=search_web)
        self.assertEqual(output['status'], 'error')
        self.assertEqual(output['official_broadcast']['status'], 'unverified_references')
        self.assertEqual(len(searched), 1)

    async def test_progress_reads_only_matched_platform_pages_and_keeps_evidence(self):
        fetched = []

        async def search_web(query):
            self.assertIn('B站', query)
            return {'status': 'ok', 'results': [
                {'title': '太空邮局', 'url': 'https://www.bilibili.com/bangumi/play/ss101?tracking=1'},
                {'title': '太空邮局', 'url': 'https://bilibili.com.invalid/bangumi/play/ss202'},
                {'title': '另一部作品', 'url': 'https://www.crunchyroll.com/series/OTHER'},
            ]}

        async def fetch_web(url):
            fetched.append(url)
            return {'url': url, 'title': '太空邮局', 'content': '太空邮局第二季 更新至第3集'}

        def handler(request):
            data = [subject(10, '太空邮局')] if request.url.path.endswith('/subjects') else []
            return httpx.Response(200, json={'data': data, 'total': len(data)})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            output = await recommend_bangumi(client, '《太空邮局》B站更新到第几集', domain='anime', task='progress',
                                             entities=('太空邮局',), search_web=search_web, fetch_web=fetch_web)
        self.assertEqual(fetched, ['https://www.bilibili.com/bangumi/play/ss101'])
        self.assertIn('更新至第3集', output['official_broadcast']['official_page_evidence'][0]['episode_excerpt'])
        self.assertEqual(output['results'][0]['episode_evidence']['status'], 'no_catalogue_dates')

    async def test_manga_native_category_and_acg_game_tag_boundaries(self):
        def handler(request):
            if request.method == 'GET':
                self.assertEqual(request.url.params['type'], '1')
                self.assertEqual(request.url.params['cat'], '1001')
                return httpx.Response(200, json={'data': [
                    subject(31, '雨天漫画', kind=1), subject(32, '雨天小说', kind=1, category=1002),
                ]})
            return httpx.Response(200, json={'data': [
                subject(41, '无分类证据的游戏', kind=4), subject(42, '二次元游戏条目', kind=4, tags=['二次元']),
            ]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            manga = await recommend_bangumi(client, '推荐漫画', domain='manga', task='recommendation')
            games = await recommend_bangumi(client, '推荐二次元游戏', domain='acg_game', task='recommendation')
        self.assertEqual([row['id'] for row in manga['results']], [31])
        self.assertEqual([row['id'] for row in games['results']], [42])
        self.assertEqual(games['results'][0]['acg_tag_evidence'], ['二次元'])

    async def test_comparison_preserves_named_identity_and_misses(self):
        def handler(request):
            keyword = json.loads(request.content)['keyword']
            if keyword == 'Rocket Mail':
                rows = [subject(81, '火箭邮递', infobox=[{'key': '别名', 'value': [{'v': 'Rocket Mail'}]}])]
            else:
                rows = [subject(82, '海洋邮局 第二季')]
            return httpx.Response(200, json={'data': rows})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            output = await recommend_bangumi(client, 'Rocket Mail 和海洋邮局哪个更好？', domain='anime', task='comparison',
                                             entities=('Rocket Mail', '海洋邮局'))
        self.assertEqual(output['status'], 'partial')
        self.assertEqual(output['unmatched_entities'], ['海洋邮局'])
        self.assertEqual(output['matches_by_title'], {'Rocket Mail': [81], '海洋邮局': []})
