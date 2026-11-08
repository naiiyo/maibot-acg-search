"""Bangumi catalogue and separately attributed broadcast evidence."""

from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx

from .web_search import valid_source_url


# Official contract: https://github.com/bangumi/api/blob/master/open-api/v0.yaml
_API = 'https://api.bgm.tv/v0'
_TYPE = {'anime': 2, 'manga': 1, 'acg_game': 4}
_DISCOVERY = {'recommend', 'recommendation', 'list', 'discover'}
_BROADCAST = {'airing', 'availability', 'broadcast', 'current', 'episode_progress', 'progress', 'release', 'schedule'}
_ACG_TAGS = {'二次元', '二游', '二次元游戏', '二次元手游', '二次元风格', 'acg', 'acg游戏', 'acg向', 'acgn'}
_ALIAS_FIELDS = {'别名', '別名', '英文名', '简体中文名', '繁体中文名', '原名'}
_MAX_RESPONSE = 2_000_000
_USER_AGENT = 'naiiyo/maibot-acg-search/0.1.0 (https://github.com/naiiyo/maibot-acg-search)'
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


async def _json(client, method, path, headers, **kwargs):
    # Fixed origin, bounded response, no token forwarding on redirect.
    async with client.stream(method, _API + path, headers=headers, follow_redirects=False, **kwargs) as response:
        response.raise_for_status()
        body = bytearray()
        async for block in response.aiter_bytes():
            if len(body) + len(block) > _MAX_RESPONSE:
                raise _CatalogueError('response_too_large')
            body.extend(block)
    result = json.loads(body)
    if not isinstance(result, dict):
        raise _CatalogueError('invalid_response')
    return result


def _rows(response):
    rows = response.get('data')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise _CatalogueError('invalid_results')
    return rows


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


def _subject(row, domain, window, exclusions, category_filtered):
    identifier = row.get('id')
    if type(identifier) is not int or identifier < 1 or row.get('type') != _TYPE[domain] or row.get('nsfw') is True:
        return None
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
    tags = _terms(tags + (meta_tags if isinstance(meta_tags, list) else []), 30)
    platform = _text(row.get('platform'), 70)
    tag_keys = {_key(tag) for tag in [platform, *tags]}
    if domain == 'manga':
        category = row.get('category')
        if (category is not None and category != 1001
                or not (category_filtered or category == 1001 or tag_keys & {'漫画', 'マンガ'})):
            return None
    if any(_key(term) in tag_keys or any(_key(term) in _key(name) for name in titles) for term in exclusions if _key(term)):
        return None
    rating = row['rating'] if isinstance(row.get('rating'), dict) else {}
    collection = row['collection'] if isinstance(row.get('collection'), dict) else {}
    heat = _number(row.get('collection_total'), 100_000_000)
    if heat is None:
        collection_counts = [_number(collection.get(kind), 100_000_000) or 0
                             for kind in ('wish', 'collect', 'doing', 'on_hold', 'dropped')]
        heat = sum(collection_counts) or None
    acg_tags = [tag for tag in tags if _key(tag) in _ACG_TAGS]
    return {
        'id': identifier, 'title': title or original_title, 'original_title': original_title,
        'url': f'https://bgm.tv/subject/{identifier}', 'date': released.isoformat() if released else None,
        'score': _number(rating.get('score', row.get('score')), 10),
        'votes': _number(rating.get('total'), 100_000_000),
        'rank': _number(rating.get('rank', row.get('rank')), 100_000_000), 'heat': heat,
        'tags': tags[:12], 'platform': platform, 'aliases': _aliases(row),
        'catalogue_episode_count': _number(row.get('eps'), 100_000) if domain == 'anime' else None,
        **({'acg_tag_evidence': acg_tags, 'catalogue_category': '游戏'} if domain == 'acg_game' else {}),
    }


def _exact_match(row, term):
    keyword = _key(term)
    return keyword and keyword in {_key(name) for name in (row['title'], row['original_title'], *row['aliases'])}


async def _subjects(client, headers, domain, keyword, window, preferences, exclusions, sort, limit):
    # v0 catalogue browse has a dedicated manga category. The full-text
    # endpoint only specifies a general book type; match its returned metadata.
    months = 0 if not window else (window[1].year - window[0].year) * 12 + window[1].month - window[0].month
    can_browse = not keyword and not preferences and months in {0, 1, 12}
    category_filtered = domain == 'manga' and can_browse and sort in {'rank', 'date'}
    browse = category_filtered or sort == 'date' and can_browse
    if browse:
        params = {'type': _TYPE[domain], 'limit': limit, 'sort': sort}
        if category_filtered:
            params['cat'] = 1001
        if window:
            params['year'] = window[0].year
            if months == 1:
                params['month'] = window[0].month
        response = await _json(client, 'GET', '/subjects', headers, params=params)
    else:
        filters = {'type': [_TYPE[domain]], 'nsfw': False}
        if window:
            filters['air_date'] = [f'>={window[0].isoformat()}', f'<{window[1].isoformat()}']
        if preferences:
            filters['tag'] = preferences
        response = await _json(client, 'POST', '/search/subjects', headers, params={'limit': limit}, json={
            'keyword': keyword, 'filter': filters,
            'sort': 'match' if keyword or sort == 'date' else sort,
        })
    rows = [_subject(row, domain, window, exclusions, category_filtered) for row in _rows(response)[:limit]]
    return [row for row in rows if row is not None]


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


async def _episodes(client, headers, identifier, today):
    # The public API has dates, with no aired/available flag. Keep scheduled
    # dates separate from official platform listings and exclude specials.
    episodes = []
    coverage_complete = False
    for offset in (0, 200):
        response = await _json(client, 'GET', '/episodes', headers,
                               params={'subject_id': identifier, 'type': 0, 'limit': 200, 'offset': offset})
        rows = _rows(response)[:200]
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


async def _fetch_broadcast_page(fetch_web, reference, titles):
    source = urlsplit(reference['url'])
    if source.scheme != 'https' or source.netloc != source.hostname:
        return None
    url = urlunsplit((source.scheme, source.netloc, source.path, '', ''))
    try:
        page = await fetch_web(url)
    except Exception:
        return None
    if not isinstance(page, dict):
        return None
    resolved = page.get('url') or url
    if _broadcaster(resolved) != reference['publisher']:
        return None
    title = _text(page.get('title'), 180)
    content = _text(page.get('content'), 12_000)
    if not content or not any(len(_key(name)) >= 2 and _key(name) in _key(title + ' ' + content) for name in titles):
        return None
    marker = re.search(r'更新至第?\s*\d+\s*[集话話]|第\s*\d+\s*[集话話]|\b(?:episode|ep\.?)[ #]*\d+\b', title + ' ' + content, re.I)
    material = title + ' ' + content
    if not marker:
        return None
    excerpt = material[max(0, marker.start() - 80):marker.end() + 145].strip()
    return {'url': resolved, 'title': title or reference['title'], 'publisher': reference['publisher'],
            'episode_excerpt': excerpt, 'retrieved_at': _text(page.get('retrieved_at'), 50) or None}


async def _broadcast(search_web, fetch_web, query, titles, alternate_titles, region, task):
    # Search is independent of Bangumi. Provider snippets are evidence to
    # inspect, not a confirmation of an episode, timezone or regional rights.
    names = ' OR '.join('"' + name.replace('"', '') + '"' for name in titles[:3])
    subject = f'({names})' if names else _text(query, 140)
    details = ('最新一集 第几集 播出 上线 时间 公式 放送 配信' if task in _BROADCAST
               else '正版 播出 平台 公式 放送 配信')
    platform = re.search(r'B站|哔哩哔哩|Bilibili|Crunchyroll|ABEMA|网飞|Netflix|爱奇艺|腾讯视频|优酷|Disney\+', query, re.I)
    hint = f' {platform.group()}' if platform else ''
    search_query = f'{subject} 动画 {details} {_text(region, 30)}{hint}'.strip()[:260]
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
    pages = []
    if fetch_web and official and task in _BROADCAST:
        fetched = await asyncio.gather(*(_fetch_broadcast_page(fetch_web, row, alternate_titles) for row in official[:2]))
        pages = [row for row in fetched if row]
    return {**base, 'status': 'broadcaster_references' if official else 'unverified_references' if references else 'empty',
            'references': references, 'official_page_evidence': pages, 'provider': _text(result.get('provider'), 80),
            'notice': ('官方页面摘录需要结合平台地区、季数和发布时间判断，目录日期仅作对照。' if pages else base['notice'])}


async def recommend_bangumi(client, query, *, domain, task, entities=(), year=None, month=None, season=None,
                            date_from='', date_to='',
                            preferences=(), exclusions=(), count=3, token='', search_web=None, fetch_web=None,
                            region='', sort='rank', today=None, user_agent=_USER_AGENT):
    """Return bounded catalogue matches and independent, attributed web evidence."""
    output = {'source': 'Bangumi', 'status': 'empty', 'results': [], 'warnings': [],
              'official_broadcast': {'status': 'not_requested', 'references': []}}
    if domain not in _TYPE:
        return {**output, 'status': 'unsupported'}
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    count = min(max(count, 1), 10) if type(count) is int else 3
    discovery = task in _DISCOVERY
    sort = sort if sort in {'rank', 'score', 'heat', 'date'} else 'rank'
    titles = _terms(entities, 4) or _terms(re.findall(r'《([^》]+)》', query), 4)
    window = _window(year, month, season, today, query if domain == 'anime' else '')
    start, end = _parse_date(date_from), _parse_date(date_to)
    if start and end and start < end:
        window = (start, end)
    # A named progress/schedule question concerns episode dates, not the
    # first air date of the whole series.
    if domain == 'anime' and task in _BROADCAST and titles:
        window = None
    elif domain == 'anime' and task in {'airing', 'schedule'} and window:
        discovery = True
    output['catalogue_sort'] = sort if discovery else 'match'
    output['catalogue_window'] = [day.isoformat() for day in window] if window else None
    if not discovery and not titles and task not in {'compare', 'comparison'}:
        titles = _terms([query], 1)
    keywords = [''] if discovery else titles
    headers = {'User-Agent': _text(user_agent, 250) or _USER_AGENT, 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    errors = 0
    unmatched, ambiguous, matches_by_title = [], [], {}
    selected = []
    if keywords:
        limit = min(max(count * 6, 15), 40) if discovery else 12
        responses = await asyncio.gather(*(_subjects(
            client, headers, domain, keyword, window,
            _terms(preferences, 6) if discovery else [], _terms(exclusions, 6), sort, limit,
        ) for keyword in keywords), return_exceptions=True)
        for keyword, response in zip(keywords, responses):
            if isinstance(response, Exception):
                errors += 1
                if keyword:
                    unmatched.append(keyword)
                continue
            if isinstance(response, BaseException):
                raise response
            if keyword:
                matches = [row for row in response if _exact_match(row, keyword)]
                if len(matches) > 1:
                    ambiguous.append(keyword)
                elif not matches:
                    unmatched.append(keyword)
                matches_by_title[keyword] = [row['id'] for row in matches[:3]]
                selected.extend(matches[:3])
            else:
                selected.extend(row for row in response if domain != 'acg_game' or row['acg_tag_evidence'])
    records = list({row['id']: row for row in selected}.values())
    if discovery:
        if sort == 'rank':
            records.sort(key=lambda row: row['rank'] or math.inf)
        else:
            records.sort(key=lambda row: row[sort] or ('' if sort == 'date' else 0), reverse=True)
        records = records[:count]
    output.update(results=records, unmatched_entities=unmatched, ambiguous_entities=ambiguous,
                  matches_by_title=matches_by_title)
    if errors:
        output['warnings'].append('Bangumi 目录请求未完成。' if errors == len(keywords) else 'Bangumi 部分请求未完成。')
    if domain == 'acg_game':
        output['warnings'].append('Bangumi 的原生类型是游戏。推荐仅保留有明确 ACG 标签的目录候选，实际题材以作品资料为准。')
    if exclusions:
        output['warnings'].append('排除条件仅按返回的标题和标签检查，标签缺失时仍需核对。')
    if task in {'compare', 'comparison'} and len(titles) < 2:
        output['warnings'].append('比较需要至少两个可辨认的作品名。')
    if domain == 'anime' and (discovery or task in _BROADCAST):
        names = _terms([row['title'] for row in records] + titles, 3)
        aliases = _terms(names + [name for row in records[:3] for name in (row['original_title'], *row['aliases'])], 12)
        jobs = [_broadcast(search_web, fetch_web, query, names, aliases, region, task)]
        episode_targets = records[:3] if task in _BROADCAST and not discovery and not ambiguous else []
        jobs.extend(_episodes(client, headers, row['id'], today) for row in episode_targets)
        broadcast, *episodes = await asyncio.gather(*jobs, return_exceptions=True)
        if isinstance(broadcast, asyncio.CancelledError):
            raise broadcast
        output['official_broadcast'] = broadcast if isinstance(broadcast, dict) else {'status': 'error', 'references': []}
        for row, episode_info in zip(episode_targets, episodes):
            if isinstance(episode_info, asyncio.CancelledError):
                raise episode_info
            row['episode_evidence'] = episode_info if isinstance(episode_info, dict) else {'status': 'unavailable'}
    output['status'] = ('partial' if records and (errors or unmatched or ambiguous) else 'ok' if records
                        else 'error' if errors and errors == len(keywords) else 'empty')
    return output
