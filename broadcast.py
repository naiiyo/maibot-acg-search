"""Attribute each episode statement to its title, edition, publisher and explicit scope."""

from __future__ import annotations

import re
import unicodedata


_NUM = r'[0-9一二三四五六七八九十]+'
_SEASON = re.compile(rf'第\s*({_NUM})\s*[季期]|\b(?:Season\s*|S)([0-9]{{1,2}})\b|\b([0-9]{{1,2}})(?:st|nd|rd|th)\s+Season\b', re.I)
_EPISODE = re.compile(r'第\s*[0-9]{1,4}\s*[集话話]|\b(?:episode|ep\.?)[ #]*[0-9]{1,4}\b|[#＃]\s*[0-9]{1,4}\b|(?:(?<![\w全共])|(?<=[至到]))[0-9]{1,4}\s*[集话話]', re.I)
_SEPARATORS = r'[\s\-:：・｜|/《》「」『』【】()（）<>·,，]*'
_STATUS_PHRASES = (
    r'更新[至到]|已(?:经)?(?:更新(?:至|到)?|上线|播出|播至|播到|播完|放送)|现已上线|最新(?:一集)?|'
    r'正在播放|开始播放|预告|预定|预计|将(?:于|播出)?|放送予定|配信予定|配信中|配信開始|配信中の|'
    r'本篇|正片|本編|アニメ|番剧|动画|TVアニメ|全|共'
)
_GAP = re.compile(rf'{_SEPARATORS}(?:(?:{_SEASON.pattern}){_SEPARATORS})?(?:(?:{_STATUS_PHRASES}){_SEPARATORS}){{0,3}}', re.I)
_PLANNED = re.compile(r'将于|即将|下周|下週|预计|预定|预告|预约|尚未|还未|未上线|未播|待播|延期|休播|休止|放送予定|配信予定|予定|予告|次回|配信前|放送前|trailer|preview|upcoming|scheduled', re.I)
_AVAILABLE = re.compile(r'更新[至到]|已(?:经)?(?:上线|播出|开播|播完|更新|播至)|现已上线|正在播放|配信中|配信開始|放送済|now streaming|available now', re.I)
_TIMEZONE = re.compile(r'日本時間|日本时间|北京时间|北京時間|香港時間|台北時間|\b(?:JST|UTC|GMT)(?:[+-]\d{1,2}(?::\d{2})?)?\b', re.I)
_REGIONS = {
    '中国大陆': ('中国大陆', '中國大陸', '中国内地', '国区', '大陆'),
    '中国香港': ('中国香港', '中國香港', '香港', '港区'),
    '中国台湾': ('中国台湾', '中國臺灣', '中国台湾地区', '台湾', '臺灣', '台区'),
    '日本': ('日本国内', '日本地区', '日本地區', '日本', '日区'),
    '美国': ('美国地区', '美国', '美國', '美区'),
}
_PLATFORM_ALIASES = {'哔哩哔哩': ('b站', '哔哩哔哩'), '爱奇艺': ('爱奇艺', 'iqiyi'), '腾讯视频': ('腾讯视频',),
                     '优酷': ('优酷', 'youku'), 'ABEMA': ('abema',), 'Netflix': ('netflix', '网飞'),
                     'Crunchyroll': ('crunchyroll',), 'Disney+': ('disney+', '迪士尼+'), 'TVer': ('tver',),
                     'Bilibili': ('bilibili',), 'U-NEXT': ('u-next',), 'TBS': ('tbs',),
                     '日本电视台': ('日本电视台',), '东京电视台': ('东京电视台',), '朝日电视台': ('朝日电视台',)}
_PLATFORM_WORD = '(?:' + '|'.join(re.escape(alias) for aliases in _PLATFORM_ALIASES.values() for alias in aliases) + ')'
_REGION_WORD = '(?:' + '|'.join(re.escape(alias) for aliases in _REGIONS.values() for alias in aliases) + ')'
_PLATFORM_SCOPE = rf'(?:{_REGION_WORD}\s*)?{_PLATFORM_WORD}(?:\s*{_REGION_WORD})?'
_TAIL = re.compile(
    r'(?:[\s,:，：（）()]*(?:'
    + '|'.join((_PLANNED.pattern, _AVAILABLE.pattern, _TIMEZONE.pattern,
                 r'[0-9]{1,4}[-/:年月日时時点分]', r'\d{1,2}:\d{2}', r'\d{1,2}分',
                 r'仅限|僅限|限于|面向|可在|仅在|仅面向|配信地域|适用地区|地区|地區|国内|可观看|可看|上线|同步播出|不支持|不可观看',
                 *(re.escape(alias) for aliases in _REGIONS.values() for alias in aliases)))
    + r'))*', re.I,
)


def requested_publishers(query, labels=()):
    text = query.casefold()
    named = {label for label in labels if label.casefold() in text}
    if 'bilibili' in text:
        named.add('哔哩哔哩')
    return named | {publisher for publisher, aliases in _PLATFORM_ALIASES.items() if any(name in text for name in aliases)}


def strip_broadcast_scope(title):
    title = re.sub(rf'^(?:在)?{_PLATFORM_SCOPE}\s*(?:上|播放|播出)?\s*的\s*', '', title, flags=re.I)
    title = re.sub(rf'\s*(?:在|的|于)\s*{_PLATFORM_SCOPE}\s*(?:上|播出|播放)?\s*$', '', title, flags=re.I)
    title = re.sub(rf'\s*{_REGION_WORD}\s*{_PLATFORM_WORD}\s*$', '', title, flags=re.I)
    return title.strip()


def _seasons(text):
    numbers = set()
    for match in _SEASON.finditer(text):
        value = next(item for item in match.groups() if item)
        if value.isdigit():
            numbers.add(int(value))
        else:
            left, tens, right = value.partition('十')
            digits = '一二三四五六七八九'
            if all(letter in digits for letter in left + right) and len(left) <= 1 and len(right) <= 1:
                number = (digits.index(left) + 1 if left else 1) * 10 if tens else 0
                number += digits.index(right) + 1 if right else (digits.index(left) + 1 if left and not tens else 0)
                numbers.add(number)
    return numbers


def _region(value):
    return next((name for name, aliases in _REGIONS.items() if value in aliases), value)


def _regions_on_page(text):
    # A timezone or a country mentioned in an unrelated context is not a rights statement.
    return {name for name, aliases in _REGIONS.items() for alias in aliases
            if re.search(rf'(?:仅限|僅限|限于|面向|可在|仅在|仅面向|配信地域[：:]?|适用地区[：:]?)\s*{alias}'
                         rf'|{alias}\s*(?:地区|地區|国内)?\s*(?:可看|可观看|上线|配信中|同步播出)', text)}


def _title_pattern(name):
    # Spaces, punctuation and digit width can differ between the official and catalogue titles.
    name = unicodedata.normalize('NFKC', name).casefold().strip()
    if len(name) < 2:
        return None
    pattern = r'\s*'.join(re.escape(word) for word in name.split())
    if name[0].isascii() and name[0].isalnum():
        pattern = r'(?<![A-Za-z0-9])' + pattern
    if name[-1].isascii() and name[-1].isalnum():
        pattern += r'(?![A-Za-z0-9])'
    return re.compile(pattern, re.I)


def episode_statement(title, content, titles, region='', expected_seasons=(), dedicated=False):
    title = unicodedata.normalize('NFKC', title)
    content = unicodedata.normalize('NFKC', content[:12000])
    expected = set(expected_seasons)
    names = list(titles)
    if expected:
        names.extend(_SEASON.sub('', name).strip() for name in titles)
    aliases = [pattern for name in dict.fromkeys(names) if (pattern := _title_pattern(name))]
    page_seasons = _seasons(title)
    if expected and page_seasons and page_seasons != expected:
        return None
    # Never concatenate different cards, headings or sentences from a listings page.
    blocks = [title, *re.split(r'[\r\n。！？!?；;]|\s+\|\|\s+', content)]
    head_only = dedicated and any(pattern.fullmatch(title.strip()) for pattern in aliases)
    best = None
    for index, block in enumerate(blocks):
        if not block or len(block) > 1600:
            continue
        for marker in _EPISODE.finditer(block):
            before = block[max(0, marker.start() - 180):marker.start()]
            after = block[marker.end():marker.end() + 100]
            starts = [match.start() for alias in aliases for match in alias.finditer(before)
                      if _GAP.fullmatch(before[match.end():])]
            if starts:
                start = max(starts)
                qualifier = re.search(rf'(?:{_PLANNED.pattern})[\s:：|《「]*$', before[:start], re.I)
                prefix = before[qualifier.start() if qualifier else start:]
            elif head_only and index == 1 and len(before) <= 32 and _GAP.fullmatch(before):
                prefix = title + ' ' + before
            else:
                continue
            # A later title or episode must not lend availability, timezone or territory to this one.
            tail = _TAIL.match(after).group()
            fragment = prefix + marker.group() + tail
            # Distinct seasons are edition boundaries even when they share a catalogue alias.
            seasons = _seasons(fragment) | page_seasons
            if expected and seasons != expected:
                continue
            requested = _region(region)
            regions = _regions_on_page(fragment)
            if requested and (requested not in regions or re.search(r'不(?:能|可|支持)|无法|不可|対象外|提供していま[せん]', fragment)):
                continue
            planned = bool(_PLANNED.search(fragment))
            available = bool(_AVAILABLE.search(fragment)) and not planned
            kind = 'scheduled' if planned else 'available' if available else 'listing'
            if not expected and seasons:
                kind = 'season_scoped_' + kind
            label = {'scheduled': '平台预告或排期', 'available': '平台已播或已上线说明', 'listing': '平台分集条目'}[kind.removeprefix('season_scoped_')]
            if kind.startswith('season_scoped'):
                label += '（仅适用于摘录所列季数）'
            latest = bool(re.search(r'更新[至到]|最新|已播至', prefix)) and available
            evidence = {'episode_excerpt': fragment.strip()[:210], 'episode_status': kind, 'explicit_latest': latest,
                        'evidence_label': label, 'region': requested or '、'.join(sorted(regions)) or None,
                        'timezone': zone.group() if (zone := _TIMEZONE.search(fragment)) else None}
            priority = 3 if latest else 2 if available else 1 if planned else 0
            if best is None or priority > best[0]:
                best = priority, evidence
    return best[1] if best else None


def explicit_seasons(query, titles):
    # Catalogue aliases of a seasonal entry sometimes omit its season. Require the
    # selected canonical title's season (or an explicit one in the question) on the page.
    return _seasons(query) or set().union(*(_seasons(title) for title in titles))
