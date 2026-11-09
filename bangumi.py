"""Bangumi catalogue and separately attributed broadcast evidence."""

from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from .web_search import valid_source_url


# Official contract: https://github.com/bangumi/api/blob/master/open-api/v0.yaml
_API = 'https://api.bgm.tv'
_TYPE = {'anime': 2, 'manga': 1, 'acg_game': 4}
_DIRECT_DOMAINS = {1: 'book', 2: 'anime', 3: 'music', 4: 'acg_game', 6: 'real'}
_DISCOVERY = {'recommend', 'recommendation', 'list', 'discover'}
_BROADCAST = {'airing', 'availability', 'broadcast', 'current', 'episode_progress', 'progress', 'release', 'schedule'}
_ACG_TAGS = {'二次元', '二游', '二次元游戏', '二次元手游', '二次元风格', 'acg', 'acg游戏', 'acg向', 'acgn'}
_TABLETOP_PLATFORMS = {'桌游', '桌面游戏', '桌上游戏', 'ボードゲーム'}
_VOLUME_SUFFIX = re.compile(r'\s*[（(]\s*\d{1,3}\s*[）)]$|第\s*[0-9一二三四五六七八九十百]+\s*[卷巻册冊]$')
_VOLUME_REQUEST = re.compile(r'单行本|单卷|第\s*[0-9一二三四五六七八九十百]+\s*[卷巻册冊]')
_ALIAS_FIELDS = {'别名', '別名', '英文名', '简体中文名', '繁体中文名', '原名'}
_SUBJECT_LINK = re.compile(r'https?://(?:www\.)?(?:bgm\.tv|bangumi\.tv|chii\.in)/subject/([1-9]\d{0,11})(?=[/?#\s，。！？）】]|$)', re.I)
_RELATED = {'staff': ('persons', 'people'), 'characters': ('characters', 'characters'), 'relations': ('subjects', 'relations')}
_COLLECTION_TYPES = ('wish', 'collect', 'doing', 'on_hold', 'dropped')
_MAX_RESPONSE = 2_000_000
_USER_AGENT = 'naiiyo/maibot-acg-search/0.1.1 (https://github.com/naiiyo/maibot-acg-search)'
_EPISODE_MARKER = re.compile(
    r'更新至第?\s*\d+\s*[集话話]|第\s*\d+\s*[集话話]|\b(?:episode|ep\.?)[ #]*\d+\b'
    r'|(?<!\w)[#＃]\s*\d{1,4}\b|(?<![\w全共])\d{1,4}\s*[集话話]', re.I,
)
_MONTHS = {'冬': 1, '春': 4, '夏': 7, '秋': 10}
_PLATFORMS = {
    'abema.tv': ('ABEMA', ('/video/', '/channels/')),
    'bilibili.com': ('哔哩哔哩', ('/bangumi/play/', '/bangumi/media/')),
    'bilibili.tv': ('Bilibili', ('/play/', '/media/')),
    'crunchyroll.com': ('Crunchyroll', ('/series/', '/watch/')),
    'disneyplus.com': ('Disney+', ('/series/', '/movies/', '/browse/entity-')),
    'iqiyi.com': ('爱奇艺', ('/a_', '/v_')),
    'netflix.com': ('Netflix', ('/title/', '/watch/')),
    'ntv.co.jp': ('日本电视台', ('/',)),
    'tbs.co.jp': ('TBS', ('/anime/', '/animerico/')),
    'tv-tokyo.co.jp': ('东京电视台', ('/anime/', '/animest/')),
    'tv-asahi.co.jp': ('朝日电视台', ('/anime/',)),
    'tver.jp': ('TVer', ('/series/', '/episodes/')),
    'u-next.jp': ('U-NEXT', ('/title/', '/play/')),
    'v.qq.com': ('腾讯视频', ('/x/cover/', '/x/page/')),
    'youku.com': ('优酷', ('/v_show/', '/show_page/', '/v_nextstage/')),
}


class _CatalogueError(ValueError):
    pass


def _text(value, limit=160):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def _terms(values, limit):
    if not isinstance(values, (tuple, list)):
        return []
    return list(dict.fromkeys(term for value in values if (term := _text(value, 80))))[:limit]


def _key(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def _parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _window(year, month, season, today, query):
    if type(month) is not int or not 1 <= month <= 12:
        month = None
    if isinstance(season, str):
        quarter = re.fullmatch(r'Q([1-4])', season.strip(), re.I)
        if quarter:
            season = (int(quarter[1]) - 1) * 3 + 1
        else:
            label = re.fullmatch(r'([冬春夏秋])(?:季)?(?:新?番)?', season.strip())
            season = _MONTHS[label[1]] if label else None
    if type(season) is int and 1 <= season <= 12:
        month = season
        months = 3
    else:
        months = 1
        # For anime, 1/4/7/10 月番 means an entire seasonal cour. A plain
        # calendar month (e.g. a specific game's 7 月 release) remains a month.
        if month in {1, 4, 7, 10} and re.search(r'月\s*(?:新番|番|动画档)|新番', query):
            months = 3
    if type(year) is not int or not 1900 <= year <= 2200:
        if month is None:
            return None
        year = today.year
    start = date(year, month or 1, 1)
    end_month = start.month - 1 + (months if month else 12)
    end = date(year + end_month // 12, end_month % 12 + 1, 1)
    return start, end


async def _json(client, method, path, headers, *, budget=6, expect_list=False, **kwargs):
    # Fixed origin, bounded response, no token forwarding on redirect.
    async with asyncio.timeout(budget):
        async with client.stream(method, _API + path, headers=headers, follow_redirects=False, **kwargs) as response:
            response.raise_for_status()
            body = bytearray()
            async for block in response.aiter_bytes():
                if len(body) + len(block) > _MAX_RESPONSE:
                    raise _CatalogueError('response_too_large')
                body.extend(block)
    result = json.loads(body)
    if not isinstance(result, list if expect_list else dict):
        raise _CatalogueError('invalid_response')
    return result


def _failure(error):
    if isinstance(error, httpx.HTTPStatusError):
        return f'HTTP {error.response.status_code}'
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return 'timeout'
    if isinstance(error, httpx.RequestError):
        return 'network_error'
    return 'invalid_response'


async def _request(client, method, path, headers, diagnostics, route, **kwargs):
    try:
        return await _json(client, method, path, headers, **kwargs)
    except (httpx.HTTPError, TimeoutError, ValueError) as error:
        diagnostics.add((route, _failure(error)))
        if 'Authorization' not in headers or _failure(error) not in {'HTTP 401', 'HTTP 403'}:
            raise
    diagnostics.add((route, 'anonymous_retry'))
    anonymous = {key: value for key, value in headers.items() if key != 'Authorization'}
    return await _request(client, method, path, anonymous, diagnostics, route, **kwargs)


def _rows(response):
    rows = response.get('data')
    if not isinstance(rows, list):
        raise _CatalogueError('invalid_results')
    valid = [row for row in rows if isinstance(row, dict)]
    if rows and not valid:
        raise _CatalogueError('invalid_results')
    return valid


def _number(value, limit):
    if type(value) in (int, float) and 0 < value <= limit and math.isfinite(value):
        return value
    return None


def _aliases(row):
    infobox = row.get('infobox')
    if not isinstance(infobox, list):
        return []
    values = []
    for field in infobox:
        if not isinstance(field, dict) or field.get('key') not in _ALIAS_FIELDS:
            continue
        value = field.get('value')
        if isinstance(value, list):
            values.extend(item.get('v') for item in value if isinstance(item, dict))
        else:
            values.append(value)
    return _terms(values, 8)


def _official_site(row):
    infobox = row.get('infobox')
    if not isinstance(infobox, list):
        return None
    for entry in infobox:
        if not isinstance(entry, dict) or entry.get('key') not in {'官方网站', '官方網站', '官网', '公式サイト'}:
            continue
        value = entry.get('value')
        values = [item.get('v') for item in value if isinstance(item, dict)] if isinstance(value, list) else [value]
        if url := next((item.strip() for item in values if isinstance(item, str) and valid_source_url(item.strip())), None):
            return url
    return None


def _subject(row, domain, window, exclusions, category_filtered, preferences=()):
    identifier = row.get('id')
    kind = row.get('type')
    if (type(identifier) is not int or identifier < 1 or type(kind) is not int or row.get('nsfw') is True
            or (kind not in _DIRECT_DOMAINS if domain == 'bangumi' else kind != _TYPE[domain])):
        return None
    actual_domain = _DIRECT_DOMAINS[kind] if domain == 'bangumi' else domain
    titles = [_text(row.get(name)) for name in ('name_cn', 'name')]
    title, original_title = titles
    if not title and not original_title:
        return None
    released = _parse_date(row.get('date'))
    if window and (released is None or not window[0] <= released < window[1]):
        return None
    tags = row.get('tags')
    tags = [item.get('name') for item in tags if isinstance(item, dict)] if isinstance(tags, list) else []
    meta_tags = row.get('meta_tags')
    tags = _terms(tags + (meta_tags if isinstance(meta_tags, list) else []), 100)
    platform = _text(row.get('platform'), 70)
    tag_keys = {_key(tag) for tag in [platform, *tags]}
    if any(_key(tag) not in tag_keys for tag in preferences):
        return None
    if domain == 'manga':
        category = row.get('category')
        if (category is not None and category != 1001
                or not (category_filtered or category == 1001 or tag_keys & {'漫画', 'マンガ'})):
            return None
    if any(_key(term) in tag_keys or any(_key(term) in _key(name) for name in titles) for term in exclusions if _key(term)):
        return None
    rating = row['rating'] if isinstance(row.get('rating'), dict) else {}
    collection = row['collection'] if isinstance(row.get('collection'), dict) else {}
    counts = {kind: collection[kind] for kind in _COLLECTION_TYPES if type(collection.get(kind)) is int
              and 0 <= collection[kind] <= 100_000_000}
    heat = _number(row.get('collection_total'), 100_000_000)
    if heat is None and counts:
        heat = sum(counts.values())
    acg_tags = [tag for tag in tags if _key(tag) in _ACG_TAGS]
    return {
        'id': identifier, 'title': title or original_title, 'original_title': original_title,
        'subject_domain': actual_domain,
        'url': f'https://bgm.tv/subject/{identifier}', 'date': released.isoformat() if released else None,
        'score': _number(rating.get('score', row.get('score')), 10),
        'votes': _number(rating.get('total'), 100_000_000),
        'rank': _number(rating.get('rank', row.get('rank')), 100_000_000), 'heat': heat,
        'collection': counts,
        'tags': tags[:12], 'platform': platform, 'aliases': _aliases(row),
        'summary': _text(row.get('summary'), 500),
        'official_website': _official_site(row),
        'catalogue_episode_count': _number(row.get('total_episodes'), 100_000) or _number(row.get('eps'), 100_000)
        if actual_domain == 'anime' else None,
        **({'volumes': _number(row.get('volumes'), 100_000), 'chapters': _number(row.get('total_episodes'), 100_000)}
           if actual_domain in {'book', 'manga'} else {}),
        **({'acg_tag_evidence': acg_tags, 'catalogue_category': '游戏'} if domain == 'acg_game' else {}),
    }


def _exact_match(row, term):
    def title_key(value):
        normalized = unicodedata.normalize('NFKC', value)
        normalized = re.sub(r'第([一二三四五六七八九])(?:季|期|部)',
                            lambda m: f'第{"一二三四五六七八九".index(m[1]) + 1}季', normalized)
        normalized = re.sub(r'第([1-9])(?:季|期|部)', r'第\1季', normalized)
        return _key(normalized)

    keyword = title_key(term)
    return keyword and keyword in {title_key(name) for name in (row['title'], row['original_title'], *row['aliases'])}


async def resolve_bangumi_title(client, title, *, token='') -> tuple[str, str] | None:
    """Use a unique exact public match to infer the medium of a literal title."""
    headers = {'User-Agent': _USER_AGENT, 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        async with asyncio.timeout(5):
            payload = await _request(client, 'POST', '/v0/search/subjects', headers, set(), 'title_probe',
                                     params={'limit': 10}, json={
                                         'keyword': title, 'sort': 'match',
                                         'filter': {'type': list(_TYPE.values()), 'nsfw': False},
                                     })
            matches = {row['id']: (source, row) for source in _rows(payload)
                       if (row := _subject(source, 'bangumi', None, (), False)) and _exact_match(row, title)}
    except (httpx.HTTPError, TimeoutError, ValueError):
        return None
    if len(matches) != 1:
        return None
    source, row = next(iter(matches.values()))
    if row['subject_domain'] == 'anime':
        return 'anime', row['title']
    if _subject(source, 'manga', None, (), False):
        return 'manga', row['title']
    if game := _subject(source, 'acg_game', None, (), False):
        tags = {_key(tag) for tag in game['tags']}
        if tags & {'galgame', '视觉小说', '視覺小說', 'visualnovel', 'ノベルゲーム'}:
            return 'galgame', row['title']
        if game['acg_tag_evidence']:
            return 'acg_game', row['title']
    return None


async def _browse_subjects(client, headers, domain, window, preferences, exclusions, sort, limit, diagnostics, *, series=False):
    params = {'type': _TYPE[domain], 'limit': limit, 'sort': 'date' if sort == 'date' else 'rank'}
    if domain == 'manga':
        params['cat'] = 1001
        if series:
            params['series'] = 'true'
    periods = [{}]
    if window:
        start, end = window[0], window[1] - timedelta(days=1)
        first_month = start.year * 12 + start.month - 1
        last_month = end.year * 12 + end.month - 1
        if last_month - first_month < 3:
            periods = [{'year': month // 12, 'month': month % 12 + 1} for month in range(first_month, last_month + 1)]
        else:
            periods = [{'year': year} for year in range(start.year, min(end.year, start.year + 1) + 1)]
    pages = await asyncio.gather(*(_request(
        client, 'GET', '/v0/subjects', headers, diagnostics, 'browse', params={**params, **period},
        budget=8, timeout=httpx.Timeout(8, connect=7),
    ) for period in periods), return_exceptions=True)
    rows, failures = [], []
    for page in pages:
        if isinstance(page, BaseException):
            if not isinstance(page, Exception):
                raise page
            failures.append(page)
        else:
            try:
                rows.extend(_rows(page)[:limit])
            except _CatalogueError as error:
                diagnostics.add(('browse', _failure(error)))
                failures.append(error)
    if len(failures) == len(pages):
        raise failures[0]
    return [subject for row in rows
            if (subject := _subject(row, domain, window, exclusions, domain == 'manga', preferences))]


async def _legacy_subjects(client, headers, domain, keyword, window, exclusions, diagnostics):
    legacy = await _request(client, 'GET', f'/search/subject/{quote(keyword, safe="")}', headers, diagnostics,
                            'legacy_search', params={'type': _TYPE[domain], 'responseGroup': 'small', 'max_results': 8})
    rows = legacy.get('list')
    if rows is None and (legacy.get('results') == 0 or legacy.get('code') == 404):
        return []
    if not isinstance(rows, list):
        raise _CatalogueError('invalid_legacy_results')
    seeds = [row for row in rows[:8] if isinstance(row, dict) and type(row.get('id')) is int and row['id'] > 0]
    exact = [row for row in seeds if _exact_match({'title': _text(row.get('name_cn')),
                                                  'original_title': _text(row.get('name')), 'aliases': []}, keyword)]
    details = await asyncio.gather(*(_request(client, 'GET', f"/v0/subjects/{row['id']}", headers, diagnostics,
                                              'subject_detail') for row in (exact or seeds)[:4]), return_exceptions=True)
    matches = []
    for detail in details:
        if isinstance(detail, BaseException):
            if not isinstance(detail, Exception):
                raise detail
            continue
        if match := _subject(detail, domain, window, exclusions, False):
            matches.append(match)
    if details and all(isinstance(detail, Exception) for detail in details):
        raise details[0]
    return matches


async def _subjects(client, headers, domain, keyword, window, preferences, exclusions, sort, limit, diagnostics, *,
                    series=False, search_terms=(), search_tags=()):
    if identifier := _SUBJECT_LINK.fullmatch(keyword):
        detail = await _request(client, 'GET', f'/v0/subjects/{identifier[1]}', headers, diagnostics, 'subject_detail')
        match = _subject(detail, domain, window, exclusions, False) if detail.get('id') == int(identifier[1]) else None
        return ([match] if match else []), 'subject_detail'
    # v0 catalogue browse has a dedicated manga category. The full-text
    # endpoint only specifies a general book type; match its returned metadata.
    months = 0 if not window else (window[1].year - window[0].year) * 12 + window[1].month - window[0].month
    can_browse = not keyword and not preferences and not search_tags and months in {0, 1, 12}
    category_filtered = domain == 'manga' and can_browse and sort in {'rank', 'date'}
    browse = category_filtered or sort == 'date' and can_browse
    if browse:
        try:
            rows = await _browse_subjects(client, headers, domain, window, preferences, exclusions, sort, limit,
                                          diagnostics, series=series)
            if rows:
                return rows, 'browse'
        except (httpx.HTTPError, TimeoutError, ValueError) as error:
            diagnostics.add(('browse', _failure(error)))
            if _failure(error) == 'HTTP 429':
                raise
    filters = {'type': [_TYPE[domain]], 'nsfw': False}
    if window:
        filters['air_date'] = [f'>={window[0].isoformat()}', f'<{window[1].isoformat()}']
    base_tags = list(preferences)
    # Model tags may improve discovery, but never narrow an unqualified heat list.
    model_tags = _terms(search_tags, 2) if not keyword and not preferences and (domain == 'acg_game' or sort != 'heat') else []

    async def search(term, tags, budget):
        query_filters = {**filters, **({'tag': tags} if tags else {})}
        response = await _request(client, 'POST', '/v0/search/subjects', headers, diagnostics, 'search',
                                  params={'limit': limit}, budget=budget, timeout=httpx.Timeout(budget, connect=min(7, budget)),
                                  json={'keyword': term, 'filter': query_filters,
                                        'sort': 'match' if term or sort == 'date' else sort})
        return [subject for row in _rows(response)[:limit]
                if (subject := _subject(row, domain, window, exclusions, False, preferences))]

    def has_match(rows):
        return any(_exact_match(row, keyword) if keyword else domain != 'acg_game' or row['acg_tag_evidence'] for row in rows)

    primary_error = None
    rows = []
    try:
        rows = await search(keyword, model_tags or base_tags, 9)
        if model_tags and not has_match(rows):
            rows = await search(keyword, base_tags, 4)
        if keyword and not has_match(rows):
            alternate = next((name for name in _terms(search_terms, 3) if _key(name) != _key(keyword)), '')
            if alternate:
                try:
                    extra = await search(alternate, (), 4)
                except (httpx.HTTPError, TimeoutError, ValueError) as error:
                    if _failure(error) == 'HTTP 429':
                        raise
                else:
                    rows = [*rows, *extra]
    except (httpx.HTTPError, TimeoutError, ValueError) as error:
        primary_error = error
        diagnostics.add(('search', _failure(error)))
        if _failure(error) == 'HTTP 429':
            raise
    if has_match(rows):
        return rows, 'search'
    if browse:
        if primary_error is not None:
            raise primary_error
        return rows, 'search'
    try:
        async with asyncio.timeout(8):
            if keyword:
                recovered = await _legacy_subjects(client, headers, domain, keyword, window, exclusions, diagnostics)
                method = 'legacy_search'
            else:
                recovered = await _browse_subjects(client, headers, domain, window, preferences, exclusions, sort, limit,
                                                   diagnostics, series=series)
                method = 'browse'
            return (recovered, method) if recovered or primary_error is not None else (rows, 'search')
    except (httpx.HTTPError, TimeoutError, ValueError) as error:
        diagnostics.add(('legacy_search' if keyword else 'browse', _failure(error)))
        if primary_error is not None:
            raise primary_error from error
        return rows, 'search'


def _episode(row, subject_id):
    if row.get('type') != 0 or row.get('subject_id', subject_id) != subject_id:
        return None
    identifier = row.get('id')
    local_number = _number(row.get('ep'), 100_000)
    display_number = _number(row.get('sort'), 100_000)
    if type(identifier) is not int or identifier < 1 or (local_number is None and display_number is None):
        return None
    aired = _parse_date(row.get('airdate'))
    return {'episode': local_number, 'display_number': display_number, 'catalogue_date': aired.isoformat() if aired else None,
            'url': f'https://bgm.tv/ep/{identifier}'}


async def _episodes(client, headers, identifier, today, diagnostics):
    # The public API has dates, with no aired/available flag. Keep scheduled
    # dates separate from official platform listings and exclude specials.
    episodes = []
    coverage_complete = False
    deadline = asyncio.get_running_loop().time() + 12
    for offset in (0, 200):
        budget = min(11, deadline - asyncio.get_running_loop().time())
        if budget <= 0:
            break
        try:
            response = await _request(client, 'GET', '/v0/episodes', headers, diagnostics, 'episodes',
                                      budget=budget, timeout=httpx.Timeout(10, connect=5),
                                      params={'subject_id': identifier, 'type': 0, 'limit': 200, 'offset': offset})
            rows = _rows(response)[:200]
        except (httpx.HTTPError, TimeoutError, ValueError) as error:
            diagnostics.add(('episodes', _failure(error)))
            if not episodes:
                raise
            break
        episodes.extend(episode for row in rows if (episode := _episode(row, identifier)))
        total = response.get('total')
        if len(rows) < 200 or type(total) is int and offset + len(rows) >= total:
            coverage_complete = True
            break
    episodes = list({episode['url']: episode for episode in episodes}.values())
    dated = sorted((row for row in episodes if row['catalogue_date']),
                   key=lambda row: (row['catalogue_date'], row['episode'] or row['display_number']))
    current = today.isoformat()
    return {
        'status': 'catalogue_dates_only' if dated else 'no_catalogue_dates',
        'as_of': current,
        'last_catalogue_date_before_today': next((row for row in reversed(dated) if row['catalogue_date'] < current), None),
        'catalogue_dates_today': [row for row in dated if row['catalogue_date'] == current][:5],
        'next_catalogue_date_after_today': next((row for row in dated if row['catalogue_date'] > current), None),
        'undated_entries': len(episodes) - len(dated), 'coverage_complete': coverage_complete,
        'notice': 'Bangumi 记录的是目录日期。实际已播集数、平台上线和地区以官方放送或正版平台为准。',
    }


def _linked_record(row, kind):
    if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0:
        return None
    title = _text(row.get('name_cn') or row.get('name'), 90)
    if not title:
        return None
    path = {'persons': 'person', 'characters': 'character', 'subjects': 'subject'}[kind]
    record = {'name': title, 'relation': _text(row.get('relation'), 55), 'url': f'https://bgm.tv/{path}/{row["id"]}'}
    if kind == 'characters' and isinstance(row.get('actors'), list):
        record['actors'] = [actor for item in row['actors'][:4] if (actor := _linked_record(item, 'persons'))]
    return record


async def _related_records(client, headers, identifier, kind, diagnostics):
    try:
        response = await _request(client, 'GET', f'/v0/subjects/{identifier}/{kind}', headers, diagnostics,
                                  kind, expect_list=True)
    except (httpx.HTTPError, TimeoutError, ValueError):
        return None
    return [record for row in response[:12] if (record := _linked_record(row, kind))]


def _broadcaster(url):
    if not valid_source_url(url):
        return ''
    parsed = urlsplit(url)
    host = parsed.hostname
    return next((label for domain, (label, paths) in _PLATFORMS.items()
                 if (host == domain or host.endswith('.' + domain)) and parsed.path.startswith(paths)), '')


def _broadcast_ref(row, titles):
    url = row.get('url')
    if not valid_source_url(url):
        return None
    host = urlsplit(url).hostname
    title = _text(row.get('title'))
    snippet = _text(row.get('snippet'), 550)
    if not title and not snippet:
        return None
    broadcaster = _broadcaster(url)
    haystack = _key(title + ' ' + snippet)
    matched_titles = [name for name in titles if len(_key(name)) >= 2 and _key(name) in haystack]
    return {'title': title, 'url': url, 'snippet': snippet,
            'publisher': broadcaster or _text(row.get('publisher') or row.get('source'), 100) or host,
            'source_kind': 'known_broadcaster' if broadcaster and matched_titles else 'unclassified',
            'matched_titles': matched_titles, 'published_at': _text(row.get('published_at'), 40) or None}


async def _fetch_broadcast_page(fetch_web, fetch_direct, reference, titles):
    source = urlsplit(reference['url'])
    if source.scheme != 'https' or source.netloc != source.hostname:
        return None, {'url': reference['url'], 'status': 'unsafe_url'}
    url = urlunsplit((source.scheme, source.netloc, source.path, '', ''))
    attempts = {'url': url}
    for backend, fetch in (('host', fetch_web), ('direct', fetch_direct)):
        if fetch is None:
            continue
        try:
            page = await fetch(url)
        except Exception:
            attempts[backend] = 'unavailable'
            continue
        if evidence := _broadcast_page_evidence(page, reference, titles, url):
            attempts[backend] = 'ok'
            return {**evidence, 'fetch_source': backend}, attempts
        attempts[backend] = 'no_usable_evidence' if isinstance(page, dict) else 'unavailable'
    return None, attempts


def _broadcast_page_evidence(page, reference, titles, url):
    if not isinstance(page, dict):
        return None
    resolved = page.get('url') or url
    if _broadcaster(resolved) != reference['publisher']:
        return None
    title = _text(page.get('title'), 180)
    content = _text(page.get('content'), 12_000)
    if not content or not any(len(_key(name)) >= 2 and _key(name) in _key(title + ' ' + content) for name in titles):
        return None
    material = title + ' ' + content
    marker = _EPISODE_MARKER.search(material)
    if not marker:
        return None
    excerpt = material[max(0, marker.start() - 80):marker.end() + 145].strip()
    return {'url': resolved, 'title': title or reference['title'], 'publisher': reference['publisher'],
            'episode_excerpt': excerpt, 'retrieved_at': _text(page.get('retrieved_at'), 50) or None}


async def _broadcast(search_web, fetch_web, fetch_direct, query, titles, alternate_titles, region, task, broadcast_query):
    # Search is independent of Bangumi. Provider snippets are evidence to
    # inspect, not a confirmation of an episode, timezone or regional rights.
    names = ' OR '.join('"' + name.replace('"', '') + '"' for name in titles[:3])
    subject = f'({names})' if names else ''
    guidance = _text(broadcast_query, 120) or _text(query, 140)
    search_query = ' '.join(filter(None, (subject, guidance, _text(region, 30))))[:260]
    base = {'status': 'unavailable', 'query': search_query, 'references': [],
            'notice': '这里只返回搜索片段与来源。平台链接和条目日期不能证明已播第几集、更新时间或指定地区可看。'}
    if search_web is None:
        return base
    try:
        result = await search_web(search_query)
    except (httpx.HTTPError, ValueError, OSError, RuntimeError, TimeoutError):
        return {**base, 'status': 'error'}
    if not isinstance(result, dict):
        return {**base, 'status': 'error'}
    status = result.get('status')
    if status in {'unavailable', 'error', 'empty'}:
        return {**base, 'status': status}
    rows = result.get('results')
    if not isinstance(rows, list):
        return {**base, 'status': 'error'}
    references = [item for row in rows[:15]
                  if isinstance(row, dict) and (item := _broadcast_ref(row, alternate_titles))]
    references = sorted({row['url']: row for row in references}.values(),
                        key=lambda row: row['source_kind'] != 'known_broadcaster')[:6]
    official = [row for row in references if row['source_kind'] == 'known_broadcaster']
    pages, attempts = [], []
    if (fetch_web or fetch_direct) and official and task in _BROADCAST:
        fetched = await asyncio.gather(*(_fetch_broadcast_page(fetch_web, fetch_direct, row, alternate_titles)
                                         for row in official[:2]))
        pages = [row for row, _ in fetched if row]
        attempts = [attempt for _, attempt in fetched]
    return {**base, 'status': 'broadcaster_references' if official else 'unverified_references' if references else 'empty',
            'references': references, 'official_page_evidence': pages, 'fetch_attempts': attempts,
            'provider': _text(result.get('provider'), 80),
            'notice': ('官方页面摘录需要结合平台地区、季数和发布时间判断，目录日期仅作对照。' if pages else base['notice'])}


async def recommend_bangumi(client, query, *, domain, task, entities=(), year=None, month=None, season=None,
                            date_from='', date_to='',
                            preferences=(), exclusions=(), excluded_titles=(), count=3, token='', search_web=None, fetch_web=None,
                            fetch_direct=None, fields=(), search_terms=(), search_tags=(), broadcast_query='',
                            region='', sort='rank', today=None, user_agent=_USER_AGENT):
    """Return bounded catalogue matches and independent, attributed web evidence."""
    output = {'source': 'Bangumi', 'status': 'empty', 'results': [], 'warnings': [],
              'official_broadcast': {'status': 'not_requested', 'references': []}}
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    count = min(max(count, 1), 10) if type(count) is int else 3
    discovery = task in _DISCOVERY
    if domain == 'acg_game' and discovery and not search_tags:
        # Preserve a media label the user actually typed when it also appears in this site's taxonomy.
        wording = _key(query)
        search_tags = tuple(sorted((tag for tag in _ACG_TAGS if tag in wording), key=lambda tag: (len(tag), tag)))[:1]
    link = _SUBJECT_LINK.search(query) if not discovery else None
    sort = sort if sort in {'rank', 'score', 'heat', 'date'} else 'rank'
    titles = _terms(entities, 4) or _terms(re.findall(r'《([^》]+)》', query), 4)
    if domain == 'bangumi':
        titles = [title for title in titles if _SUBJECT_LINK.fullmatch(title)] or ([link[0]] if link else [])
        if discovery or not titles:
            return {**output, 'status': 'unsupported'}
    elif domain not in _TYPE:
        return {**output, 'status': 'unsupported'}
    elif link and len(titles) <= 1:
        titles = [link[0]]
    window = _window(year, month, season, today, query if domain == 'anime' else '')
    start, end = _parse_date(date_from), _parse_date(date_to)
    if start and end and start < end:
        window = (start, end)
    # A named progress/schedule question concerns episode dates, not the
    # first air date of the whole series.
    if domain in {'anime', 'bangumi'} and task in _BROADCAST and titles:
        window = None
    elif domain == 'anime' and task in {'airing', 'schedule'} and window:
        discovery = True
    output['catalogue_sort'] = sort if discovery else 'match'
    if discovery:
        output['catalogue_scope'] = 'bounded_sample'
        if sort == 'heat':
            output['catalogue_metric'] = 'bangumi_current_collection_states'
    output['catalogue_window'] = [day.isoformat() for day in window] if window else None
    if not discovery and not titles and task not in {'compare', 'comparison'}:
        raw_key = _key(query)
        titles = [term for term in _terms(search_terms, 2) if _key(term) and _key(term) in raw_key] or _terms([query], 1)
    keywords = [''] if discovery else titles
    headers = {'User-Agent': _text(user_agent, 250) or _USER_AGENT, 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    errors = 0
    diagnostics, methods = set(), set()
    unmatched, ambiguous, matches_by_title = [], [], {}
    selected = []
    if keywords:
        limit = min(max(count * 6, 15), 40) if discovery else 12
        responses = await asyncio.gather(*(_subjects(
            client, headers, domain, keyword, window,
            _terms(preferences, 6) if discovery else [], _terms(exclusions, 6), sort, limit, diagnostics,
            series=domain == 'manga' and discovery and not _VOLUME_REQUEST.search(query),
            search_terms=search_terms if len(keywords) == 1 else (), search_tags=search_tags,
        ) for keyword in keywords), return_exceptions=True)
        for keyword, response in zip(keywords, responses):
            if isinstance(response, Exception):
                errors += 1
                if keyword:
                    unmatched.append(keyword)
                continue
            if isinstance(response, BaseException):
                raise response
            rows, method = response
            methods.add(method)
            if keyword:
                matches = rows if method == 'subject_detail' else [row for row in rows if _exact_match(row, keyword)]
                if len(matches) > 1:
                    ambiguous.append(keyword)
                elif not matches:
                    unmatched.append(keyword)
                matches_by_title[keyword] = [row['id'] for row in matches[:3]]
                selected.extend(matches[:3])
            else:
                selected.extend(row for row in rows if domain != 'acg_game' or row['acg_tag_evidence'])
    if discovery and domain == 'manga' and not _VOLUME_REQUEST.search(query):
        selected = [row for row in selected if not any(_VOLUME_SUFFIX.search(name)
                                                     for name in (row['title'], row['original_title']))]
    if discovery and domain == 'acg_game' and not any(platform in query for platform in _TABLETOP_PLATFORMS):
        selected = [row for row in selected if row['platform'] not in _TABLETOP_PLATFORMS]
    blocked = _terms(excluded_titles, 8)
    records = list({row['id']: row for row in selected
                    if not any(_exact_match(row, name) for name in blocked)}.values())
    if discovery:
        if sort == 'heat':
            records = [row for row in records if row['heat'] is not None]
        output['catalogue_candidate_count'] = len(records)
        if sort == 'rank':
            records.sort(key=lambda row: (row['rank'] or math.inf, -(row['votes'] or 0), -(row['heat'] or 0)))
        else:
            records.sort(key=lambda row: row[sort] or ('' if sort == 'date' else 0), reverse=True)
        records = records[:count]
    output.update(results=records, unmatched_entities=unmatched, ambiguous_entities=ambiguous,
                  matches_by_title=matches_by_title, catalogue_methods=sorted(methods))
    if errors:
        output['warnings'].append('Bangumi 目录请求未完成。' if errors == len(keywords) else 'Bangumi 部分请求未完成。')
    if domain == 'acg_game':
        output['warnings'].append('Bangumi 的原生类型是游戏。推荐仅保留有明确 ACG 标签的目录候选，实际题材以作品资料为准。')
    if exclusions:
        output['warnings'].append('排除条件仅按返回的标题和标签检查，标签缺失时仍需核对。')
    if task in {'compare', 'comparison'} and len(titles) < 2:
        output['warnings'].append('比较需要至少两个可辨认的作品名。')
    jobs = {}
    is_anime = domain == 'anime' or (domain == 'bangumi' and any(row['subject_domain'] == 'anime' for row in records))
    if is_anime and (discovery or task in _BROADCAST):
        names = _terms([row['title'] for row in records] + [name for name in titles if not _SUBJECT_LINK.fullmatch(name)], 3)
        aliases = _terms(names + [name for row in records[:3] for name in (row['original_title'], *row['aliases'])], 12)
        jobs[(None, 'official_broadcast')] = _broadcast(search_web, fetch_web, fetch_direct, query, names, aliases,
                                                        region, task, broadcast_query)
        episode_targets = records[:3] if task in _BROADCAST and not discovery and not ambiguous else []
        for row in episode_targets:
            jobs[(row['id'], 'episode_evidence')] = _episodes(client, headers, row['id'], today, diagnostics)
    if records and not discovery and not ambiguous:
        for field, (kind, key) in _RELATED.items():
            if field in fields:
                for row in records[:2]:
                    jobs[(row['id'], key)] = _related_records(client, headers, row['id'], kind, diagnostics)
    if jobs:
        by_id = {row['id']: row for row in records}
        answers = await asyncio.gather(*jobs.values(), return_exceptions=True)
        for (identifier, key), result in zip(jobs, answers):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if identifier is None:
                output[key] = result if isinstance(result, dict) else {'status': 'error', 'references': []}
            elif key == 'episode_evidence':
                by_id[identifier][key] = result if isinstance(result, dict) else {'status': 'unavailable'}
            elif isinstance(result, list):
                by_id[identifier][key] = result
    if diagnostics:
        output['request_diagnostics'] = [{'endpoint': route, 'status': status} for route, status in sorted(diagnostics)]
    output['status'] = ('partial' if records and (errors or unmatched or ambiguous) else 'ok' if records
                        else 'error' if errors and errors == len(keywords) else 'empty')
    return output
