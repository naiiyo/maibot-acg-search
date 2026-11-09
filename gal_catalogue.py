"""Assemble Galgame evidence from catalogues and public Steam store listings."""

from __future__ import annotations

import asyncio
import calendar
import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from .ymgal import YMGAL, YmgalClient, YmgalError

BANGUMI = "https://api.bgm.tv/v0/search/subjects"
BANGUMI_SUBJECTS = "https://api.bgm.tv/v0/subjects"
VNDB = "https://api.vndb.org/kana"
EGS_ROOT = "https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki"
STEAM = "https://store.steampowered.com"
STEAM_REVIEWS = "https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/"
CATALOGUE_SOURCES = ("bangumi", "vndb", "erogamescape", "ymgal")
FIRST_RELEASE_SOURCES = ("vndb", "ymgal", "erogamescape", "bangumi")
VISUAL_NOVEL_TAGS = ("视觉小说", "視覺小說", "文字冒险", "文字冒險", "文字ADV", "Visual Novel", "ノベルゲーム", "ビジュアルノベル")
USER_AGENT = "naiiyo/maibot-acg-search/0.1.1 (https://github.com/naiiyo/maibot-acg-search)"
VN_FIELDS = (
    "title,alttitle,aliases,titles{lang,title,latin},released,rating,votecount,"
    "length_minutes,languages,platforms,tags{name,rating,spoiler,lie},extlinks{url},developers{name,original}"
)
RELEASE_FIELDS = "title,released,patch,official,languages{lang,mtl},platforms,vns{id,rtype},extlinks{url}"
GENRES = {
    "纯爱": ("Pure Love Story", "纯爱", "純愛"), "恋爱": ("Romance", "恋爱", "恋愛"),
    "催泪": ("Nakige", "催泪", "泣系", "泣きゲー"), "废萌": ("Moege", "废萌", "萌え"),
    "治愈": ("Iyashikei", "治愈", "癒し"), "推理": ("Mystery", "推理", "悬疑", "ミステリー"),
    "恐怖": ("Horror", "恐怖", "ホラー"), "百合": ("Yuri", "百合"),
    "校园": ("School Life", "校园", "学園"), "日常": ("Slice of Life", "日常"),
    "NTR": ("Netorare", "NTR", "寝取られ"), "科幻": ("Science Fiction", "科幻"),
    "奇幻": ("Fantasy", "奇幻", "幻想"), "后宫": ("Harem", "后宫", "ハーレム"),
    "乙女": ("Otome Game", "乙女", "Otome"), "战斗": ("Battle", "战斗"),
    "搞笑": ("Comedy", "搞笑", "喜剧"), "郁系": ("Utsuge", "郁系", "鬱系"),
    "全年龄": ("All Ages", "全年龄", "全年齢"),
}
WARMTH = ("温情", "温馨", "暖心", "ほのぼの", "Heartwarming", "Wholesome", "heartwarming story", "heartwarming tale", "tender relationship", "心温まる物語")
DAILY = (*GENRES["日常"], "Slice of Life Comedy", "everyday life", "daily life", "living together", "恋爱日常", "生活日常")
WARM_DAILY = ("温馨日常", "日常温情", "Heartwarming Slice of Life", "heartwarming story about living with", "心温まる日常")
STYLES = {
    "剧情向": (("剧情向", "重剧情", "故事丰富", "ストーリー重視", "Story Rich", "Story-driven", "Plot-driven"),),
    "日常温情": (DAILY, (*WARMTH, "治愈", "Iyashikei")),
    "温情": (WARMTH,),
}
STYLE_ALIASES = {"剧情": "剧情向", "重剧情": "剧情向", "剧情驱动": "剧情向", "温馨日常": "日常温情", "日常治愈": "日常温情"}
DESCRIPTION_STYLE_PHRASES = (*WARM_DAILY, "heartwarming story", "heartwarming tale", "tender relationship", "心温まる物語",
                             "everyday life", "daily life", "living together", "恋爱日常", "生活日常",
                             "Story Rich", "Story-driven", "Plot-driven")
LANGUAGES = {
    "zh": ("zh-Hans", "zh-Hant"), "zh-Hans": ("zh-Hans",), "zh-Hant": ("zh-Hant",),
    "ja": ("ja",), "en": ("en",), "ko": ("ko",),
}
LANGUAGE_ALIASES = {
    "中文": "zh", "汉化": "zh", "官中": "zh", "chinese": "zh", "简中": "zh-Hans",
    "简体": "zh-Hans", "简体中文": "zh-Hans", "zh-cn": "zh-Hans", "zh-hans": "zh-Hans",
    "繁中": "zh-Hant", "繁體中文": "zh-Hant", "繁体中文": "zh-Hant", "繁体": "zh-Hant",
    "zh-tw": "zh-Hant", "zh-hant": "zh-Hant", "日语": "ja", "日文": "ja", "japanese": "ja",
    "英文": "en", "英语": "en", "english": "en", "韩文": "ko", "韩语": "ko", "korean": "ko",
}
PLATFORMS = {
    "pc": ("win", "mac", "lin"), "windows": ("win",), "macos": ("mac",), "linux": ("lin",),
    "switch": ("swi",), "ps4": ("ps4",), "ps5": ("ps5",), "手机": ("and", "ios"),
    "android": ("and",), "ios": ("ios",), "steam": (),
}
PLATFORM_ALIASES = {"电脑": "pc", "电脑端": "pc", "ns": "switch", "安卓": "android", "mac": "macos"}


def _key(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def _strings(values) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    return tuple(dict.fromkeys(v.strip()[:200] for v in (values or ()) if isinstance(v, str) and v.strip()))


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _number(value, high: float) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= high else None
    except (TypeError, ValueError, OverflowError):
        return None


def _votes(value) -> int:
    return int(_number(value, 1_000_000_000) or 0)


def _date(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[12]\d{3}(?:-\d{2}(?:-\d{2})?)?", value):
        return ""
    try:
        date.fromisoformat(value + {4: "-01-01", 7: "-01", 10: ""}[len(value)])
    except ValueError:
        return ""
    return value


def _known_released(value: str, today: date) -> bool:
    if len(value) != 10:
        return False
    try:
        return date.fromisoformat(value) <= today
    except ValueError:
        return False


def _earliest_possible_date(value: str) -> str:
    return value + {4: "-01-01", 7: "-01"}.get(len(value), "") if value else ""


def _fits_release_date(value: str, start: str, end: str) -> bool:
    try:
        if len(value) == 4:
            earliest, latest = f"{value}-01-01", f"{value}-12-31"
        elif len(value) == 7:
            year, month = map(int, value.split("-"))
            earliest, latest = f"{value}-01", f"{value}-{calendar.monthrange(year, month)[1]:02d}"
        else:
            earliest = latest = date.fromisoformat(value).isoformat()
    except ValueError:
        return False
    return start <= earliest and latest < end


def _terms(value: str) -> tuple[str, ...]:
    key = _key(value)
    return next((terms for terms in GENRES.values() if key in {_key(term) for term in terms}), (value,))


def _excluded_work(values):
    values = _strings(values)
    keys = set(map(_key, values))
    ids = {("vndb", value.casefold()) for value in values if re.fullmatch(r"v[0-9]{1,16}", value, re.I)}
    return keys, ids


def _description_style_signals(text):
    description = unescape(re.sub(r"<[^>]*>", " ", str(text or "")[:1200])).casefold()
    return [phrase for phrase in DESCRIPTION_STYLE_PHRASES
            if re.search(rf"(?<![a-z]){re.escape(phrase.casefold())}(?![a-z])", description)]


def _source_excerpt(value, limit):
    if not isinstance(value, str):
        return ""
    text = unescape(value[:6000])
    text = re.sub(r"\[spoiler(?:=[^\]]*)?\].*?(?:\[/spoiler\]|$)", " ", text, flags=re.I | re.S)
    text = re.sub(r"\[/?[a-z][\w-]*(?:=[^\]]*)?\]", "", text, flags=re.I)
    text = re.sub(r"<(script|style)\b[^>]*>.*?(?:</\1\s*>|$)", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]*>|https?://\S+", " ", text, flags=re.I)
    text = " ".join(text.split()).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _style_evidence(group, styles, store=None, store_tags=(), store_description=()):
    tags = [(row.source, tag, row.url, "catalogue_tag") for row in group for tag in row.tags]
    tags.extend((row.source, phrase, row.url, "catalogue_description") for row in group
                for phrase in row.details.get("style_description_signals", ()))
    if store:
        tags.extend(("steam", tag, store["url"], "community_tag") for tag in store_tags)
        tags.extend(("steam", text, store["url"], "store_description") for text in store_description)
    evidence, unverified = [], []
    for style in styles:
        alternatives = STYLES.get(style, (_terms(style),))
        if style == "日常温情":
            direct = set(map(_key, WARM_DAILY))
            complete = next((row for row in tags if _key(row[1]) in direct), None)
        else:
            complete = None
        matched = [complete] if complete else []
        if not matched:
            for aliases in alternatives:
                keys = set(map(_key, aliases))
                matched.append(next((row for row in tags if _key(row[1]) in keys), None))
        if all(matched):
            evidence.append({"preference": style, "evidence": [
                {"source": source, "tag": tag, "url": url, "kind": kind} for source, tag, url, kind in matched
            ]})
        else:
            unverified.append(style)
    return evidence, unverified


def _style_affinity(group, styles):
    visible = {_key(tag) for row in group for tag in (*row.tags, *row.details.get("style_description_signals", ()))}
    return sum(bool(visible.intersection(map(_key, terms)))
               for style in styles for terms in STYLES.get(style, (_terms(style),)))


def _style_order(unverified):
    return sum(style in {"日常温情", "温情"} for style in unverified), len(unverified)


def _conjunction(filters: list, operator: str = "and") -> list:
    if len(filters) < 2:
        return filters[0] if filters else []
    return [operator, *filters]


@dataclass
class Entry:
    source: str
    id: str
    title: str
    url: str
    names: tuple[str, ...]
    display: str = ""
    released: str = ""
    rating: float | None = None
    scale: int = 100
    votes: int = 0
    heat: int = 0
    position: int = 0
    tags: tuple[str, ...] = ()
    hidden_tags: tuple[str, ...] = ()
    bindings: dict[str, str] = field(default_factory=dict)
    steam_ids: tuple[str, ...] = ()
    minutes: int = 0
    developers: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)

    def evidence(self) -> dict:
        return {
            "source": self.source, "id": self.id, "url": self.url, "title": self.title,
            "released": self.released, "rating": self.rating, "rating_scale": self.scale,
            "rating_kind": {"bangumi": "Bangumi score", "vndb": "VNDB Bayesian rating", "erogamescape": "EGS median"}.get(self.source),
            "votes": self.votes, "heat": self.heat,
            "heat_kind": {"bangumi": "collections", "vndb": "votes", "erogamescape": "ratings"}.get(self.source),
            **self.details,
        }


class _EgsTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scope = 0
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "div" and (self.scope or dict(attrs).get("id") == "query_result_main"):
            self.scope += 1
        if self.scope and tag == "tr":
            self.row = []
        if self.row is not None and tag in {"td", "th"}:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if self.scope and tag == "div":
            self.scope -= 1
        if self.cell is not None and self.row is not None and tag in {"td", "th"}:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        if self.row is not None and tag == "tr":
            if self.row:
                self.rows.append(self.row)
            self.row = None


class _StoreTags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.content = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "app_tag" in (attrs.get("class") or "").split():
            self.content = []

    def handle_data(self, data):
        if self.content is not None:
            self.content.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.content is not None:
            self.tags.append(" ".join("".join(self.content).split()))
            self.content = None


def _egs_statement(entities, release_range, limit, ranking, low, high, ids=(), *, rated=True):
    clauses = ["count2 >= 1"] if rated else ["id > 0"]
    if release_range:
        start, end = release_range
        clauses.extend((f"sellday >= '{start}'", f"sellday < '{end}'"))
    if entities or ids:
        names = [name.replace("\\", "\\\\").replace("'", "''") for name in entities]
        identities = [f"gamename = E'{name}'" for name in names]
        identities.extend(f"id = {id_}" for id_ in ids if re.fullmatch(r"[0-9]{1,16}", str(id_)))
        if identities:
            clauses.append("(" + " OR ".join(identities) + ")")
    if low is not None:
        clauses.append(f"median >= {low}")
    if high is not None:
        clauses.append(f"median <= {high}")
    sort = "median DESC, count2 DESC" if ranking in {"rating", "erogamescape"} else "count2 DESC, median DESC"
    return f"SELECT id, gamename, sellday, median, count2 FROM gamelist WHERE {' AND '.join(clauses)} ORDER BY {sort}, id LIMIT {limit}"


def _parse_bangumi(body, *, synopsis=False) -> list[Entry]:
    entries = []
    for position, row in enumerate(_rows(body.get("data")), 1):
        id_ = str(row.get("id", ""))
        title = str(row.get("name") or "").strip()
        if not id_.isdigit() or row.get("type") != 4 or not title:
            continue
        display = str(row.get("name_cn") or "")
        names = [title, display]
        for item in _rows(row.get("infobox")):
            if item.get("key") in {"别名", "別名"}:
                value = item.get("value")
                names.extend([value] if isinstance(value, str) else (item.get("v") for item in _rows(value)))
        rating, collection = row.get("rating") or {}, row.get("collection") or {}
        tags = [tag.get("name") for tag in _rows(row.get("tags")) if _votes(tag.get("count")) >= 3]
        tags.extend(_strings(row.get("meta_tags")))
        details = {"style_description_signals": _description_style_signals(row.get("summary"))}
        if synopsis and (summary := _source_excerpt(row.get("summary"), 500)):
            details["description"] = summary
        entries.append(Entry("bangumi", id_, title, f"https://bgm.tv/subject/{id_}", _strings(names), display,
                             _date(row.get("date")), _number(rating.get("score"), 10), 10,
                             _votes(rating.get("total")), sum(_votes(collection.get(k)) for k in
                             ("wish", "collect", "doing", "on_hold", "dropped")), position, _strings(tags),
                             details=details))
    return entries


def _links(row: dict) -> tuple[dict[str, str], tuple[str, ...]]:
    bindings, steam_ids = {}, []
    for link in _rows(row.get("extlinks")):
        try:
            url = urlsplit(str(link.get("url") or ""))
        except ValueError:
            continue
        if url.hostname in {"bgm.tv", "bangumi.tv", "chii.in"} and re.fullmatch(r"/subject/\d+/?", url.path):
            bindings["bangumi"] = url.path.split("/")[2]
        if url.hostname == "vndb.org" and re.fullmatch(r"/v\d+/?", url.path):
            bindings["vndb"] = url.path.strip("/")
        if url.hostname in {"www.ymgal.games", "ymgal.games"} and (match := re.fullmatch(r"/ga(\d+)/?", url.path, re.I)):
            bindings["ymgal"] = match[1]
        if url.hostname == "erogamescape.dyndns.org":
            id_ = parse_qs(url.query).get("game", [""])[0]
            if id_.isdigit():
                bindings["erogamescape"] = id_
        if url.hostname == "store.steampowered.com":
            match = re.match(r"/app/(\d+)(?:/|$)", url.path)
            if match:
                steam_ids.append(match[1])
    return bindings, _strings(steam_ids)


def _parse_vndb(body) -> list[Entry]:
    entries = []
    for position, row in enumerate(_rows(body.get("results")), 1):
        id_ = str(row.get("id", ""))
        title = str(row.get("title") or "").strip()
        if not re.fullmatch(r"v\d+", id_) or not title:
            continue
        titles = _rows(row.get("titles"))
        display = next((str(t.get("title") or "") for t in titles if t.get("lang") == "zh-Hans"), "")
        names = [title, row.get("alttitle"), *_strings(row.get("aliases"))]
        names.extend(value for item in titles for value in (item.get("title"), item.get("latin")))
        visible, hidden = [], []
        for tag in _rows(row.get("tags")):
            if not tag.get("lie") and (_number(tag.get("rating"), 3) or 0) >= 1:
                (hidden if tag.get("spoiler") else visible).append(tag.get("name"))
        bindings, steam_ids = _links(row)
        votes = _votes(row.get("votecount"))
        developers = _strings(value for developer in _rows(row.get("developers")) for value in
                              (developer.get("name"), developer.get("original")))
        staff = [{"name": str(member.get("original") or member.get("name") or "")[:100],
                  "role": str(member.get("role") or "")[:50]} for member in _rows(row.get("staff"))[:18]]
        voice_cast = [{"character": str((link.get("character") or {}).get("name") or "")[:100],
                       "actor": str((link.get("staff") or {}).get("original") or (link.get("staff") or {}).get("name") or "")[:100]}
                      for link in _rows(row.get("va"))[:18]]
        details = {"developers": developers, "length_minutes": _votes(row.get("length_minutes")) or None,
                   "work_languages": _strings(row.get("languages")), "work_platforms": _strings(row.get("platforms"))}
        if description := _source_excerpt(row.get("description"), 500):
            details["description"] = description
        if staff or voice_cast:
            details.update(staff=staff, voice_cast=voice_cast)
        entries.append(Entry("vndb", id_, title, f"https://vndb.org/{id_}", _strings(names), display,
                             _date(row.get("released")), _number(row.get("rating"), 100), 100, votes,
                             votes, position, _strings(visible), _strings(hidden), bindings, steam_ids,
                             _votes(row.get("length_minutes")), developers, details))
    return entries


def _parse_egs(body: str) -> list[Entry]:
    table = _EgsTable()
    table.feed(body)
    if not table.rows or table.rows[0] != ["id", "gamename", "sellday", "median", "count2"]:
        raise ValueError("EGS result table schema unavailable")
    entries = []
    for position, row in enumerate(table.rows[1:], 1):
        if len(row) != 5 or not row[0].isdigit() or not row[1]:
            continue
        id_, title, released, rating, count = row
        votes = _votes(count)
        entries.append(Entry("erogamescape", id_, title, f"{EGS_ROOT}/game.php?game={id_}", (title,),
                             released=_date(released), rating=_number(rating, 100), votes=votes, heat=votes,
                             position=position))
    return entries


def _parse_ymgal(body: dict, detail=None, *, synopsis=False) -> Entry | None:
    id_ = str(body.get("gid") or body.get("id") or "")
    title = str(body.get("name") or "").strip()
    if not re.fullmatch(r"[0-9]{1,20}", id_) or not title or body.get("state") == "FREEZE":
        return None
    display = str(body.get("chineseName") or "").strip()
    names = _strings([title, display, *(row.get("name") for row in _rows(body.get("extensionName")))])
    binding, steam_ids = _links({"extlinks": [{"url": site.get("link")} for site in _rows(body.get("website"))]})
    developer = str(body.get("orgName") or "").strip()[:100]
    evidence = {"has_chinese_edition": body.get("haveChinese") if type(body.get("haveChinese")) is bool else None,
                "type_description": str(body.get("typeDesc") or "")[:100], "developer": developer}
    if detail or synopsis:
        if description := _source_excerpt(body.get("introduction"), 500):
            evidence["description"] = description
    if detail:
        evidence["editions"] = [{"id": str(row.get("id") or "")[:25], "title": str(row.get("releaseName") or "")[:100],
                                 "released": _date(row.get("releaseDate")), "language": str(row.get("releaseLanguage") or "")[:50],
                                 "platform": str(row.get("platform") or "")[:50]} for row in _rows(body.get("releases"))[:10]]
        characters, persons = detail.get("cidMapping") or {}, detail.get("pidMapping") or {}
        if isinstance(characters, dict) and isinstance(persons, dict):
            evidence["voice_cast"] = [{"character": _mapped_name(characters, row.get("cid")),
                                       "actor": _mapped_name(persons, row.get("cvId"))} for row in _rows(body.get("characters"))[:15]]
        evidence["staff"] = [{"name": str(row.get("empName") or "")[:100], "role": str(row.get("jobName") or "")[:50]}
                             for row in _rows(body.get("staff"))[:18]]
    return Entry("ymgal", id_, title, f"{YMGAL}/ga{id_}", names, display, _date(body.get("releaseDate")),
                 bindings=binding, steam_ids=steam_ids, developers=_strings([developer]), details=evidence)


def _mapped_name(mapping: dict, id_) -> str:
    row = mapping.get(str(id_))
    return str(row.get("chineseName") or row.get("name") or "")[:100] if isinstance(row, dict) else ""


def _bind(first: Entry, second: Entry) -> int:
    first_link, second_link = first.bindings.get(second.source), second.bindings.get(first.source)
    if (first_link and first_link != second.id) or (second_link and second_link != first.id):
        return -1
    if first_link == second.id or second_link == first.id:
        return 2
    shared_names = {_key(n) for n in first.names if _key(n)} & {_key(n) for n in second.names if _key(n)}
    if first.released and second.released and first.released[:4] != second.released[:4]:
        shared_developers = {_key(name) for name in first.developers if _key(name)} & {
            _key(name) for name in second.developers if _key(name)
        }
        return int(bool(shared_names and shared_developers))
    return int(bool(shared_names))


def _merge(entries: list[Entry]) -> list[list[Entry]]:
    groups: list[list[Entry]] = []
    for entry in entries:
        matches = []
        for group in groups:
            if any(record.source == entry.source for record in group):
                continue
            signals = [_bind(record, entry) for record in group]
            for index, record in enumerate(group):
                if signals[index] == 1 and any(other.id != entry.id and other.source == entry.source
                                               and _bind(record, other) >= 1 for other in entries):
                    signals[index] = 0
            if -1 not in signals and max(signals) > 0:
                matches.append((max(signals), group))
        best = max((strength for strength, _ in matches), default=0)
        strongest = [group for strength, group in matches if strength == best]
        if len(strongest) == 1:
            strongest[0].append(entry)
        else:
            groups.append([entry])
    return groups


class _Lookup:
    def __init__(self, client: httpx.AsyncClient, timeout: float, egs_client: httpx.AsyncClient | Literal[False] | None, ymgal=None):
        self.client = client
        self.egs_client = egs_client if egs_client is not None else client
        self.ymgal_client = ymgal if ymgal is not None else YmgalClient(client)
        self.deadline = time.monotonic() + timeout
        self.pre_store_deadline = self.deadline - min(5, timeout * 0.35)
        self.sources = {site: {"status": "unqueried", "requests": 0, "rows": 0}
                        for site in (*CATALOGUE_SOURCES, "steam")}
        self.seen = {site: set() for site in CATALOGUE_SOURCES}
        self.successful = set()

    async def request(self, site: str, method: str, url: str, *, headers=None, limit_seconds=6, reserve_seconds=0, **kwargs):
        timeout = min(limit_seconds, self.deadline - time.monotonic() - reserve_seconds)
        if timeout <= 0:
            raise TimeoutError("catalogue deadline")
        self.sources[site]["requests"] += 1
        client = self.egs_client if site == "erogamescape" else self.client
        async with asyncio.timeout(timeout):
            async with client.stream(method, url, timeout=timeout, follow_redirects=False,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})}, **kwargs) as response:
                response.raise_for_status()
                parts = []
                length = 0
                async for chunk in response.aiter_bytes():
                    length += len(chunk)
                    if length > 3_000_000:
                        raise ValueError("catalogue response size")
                    parts.append(chunk)
                content = b"".join(parts)
        return httpx.Response(response.status_code, content=content)

    async def bangumi(self, entities, release_range, limit, ranking, low, high, token, *, ids=(), focused=False,
                      tags=("Galgame",), synopsis=False):
        filters = {"type": [4], "tag": list(tags)} if not entities else {"type": [4]}
        if release_range:
            start, end = release_range
            filters["air_date"] = [f">={start}", f"<{end}"]
        if low is not None or high is not None:
            filters["rating"] = [*([f">={low}"] if low is not None else []), *([f"<={high}"] if high is not None else [])]
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        budget = ({"limit_seconds": 4 if synopsis else 3, "reserve_seconds": 5} if focused
                  else {"limit_seconds": 8} if synopsis else {})
        sort = "match" if entities else "score" if ranking in {"rating", "bangumi"} else "heat"

        async def fetch(method, url, *, fallback=False, **kwargs):
            request_budget = {"limit_seconds": 2, "reserve_seconds": 5} if fallback else budget
            try:
                return await self.request("bangumi", method, url, headers=headers, **request_budget, **kwargs)
            except httpx.HTTPStatusError as error:
                if not token or error.response.status_code not in {401, 403}:
                    raise
                self.sources["bangumi"]["auth_fallback"] = True
                return await self.request("bangumi", method, url, **request_budget, **kwargs)

        async def by_id(id_, *, fallback=False):
            response = await fetch("GET", f"{BANGUMI_SUBJECTS}/{id_}", fallback=fallback)
            return _parse_bangumi({"data": [response.json()]}, synopsis=synopsis)

        async def search(name):
            entries, primary_error = [], None
            try:
                response = await fetch("POST", BANGUMI, params={"limit": min(limit, 8) if focused else limit, "offset": 0},
                                       json={"keyword": name, "filter": filters, "sort": sort})
                entries = _parse_bangumi(response.json(), synopsis=synopsis)
            except (httpx.HTTPError, TimeoutError, ValueError, AttributeError) as error:
                if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
                    raise
                primary_error = error
            if (not name and primary_error is None) or (name and any(_key(name) in map(_key, row.names) for row in entries)):
                return entries
            try:
                if name:
                    # Legacy search returns a short list; v0 detail supplies typed, dated evidence.
                    response = await fetch("GET", f"https://api.bgm.tv/search/subject/{quote(name, safe='')}",
                                           fallback=True, params={"type": 4, "responseGroup": "small", "max_results": 5})
                    legacy = _rows(response.json().get("list"))
                    ids = _strings(str(row.get("id", "")) for row in legacy if _key(name) in {
                        _key(str(row.get("name") or "")), _key(str(row.get("name_cn") or "")),
                    })
                    details = await asyncio.gather(*(by_id(id_, fallback=True) for id_ in ids[:3] if id_.isdigit()))
                    rows = [row for part in details for row in part]
                else:
                    params = {"type": 4, "sort": "rank", "limit": limit}
                    if release_range:
                        params["year"] = release_range[0][:4]
                    response = await fetch("GET", BANGUMI_SUBJECTS, fallback=True, params=params)
                    # The browse endpoint cannot apply Galgame tags. Only explicit visual novels seed this fallback.
                    rows = [row for row in _parse_bangumi(response.json(), synopsis=synopsis)
                            if set(map(_key, row.tags)).intersection(map(_key, VISUAL_NOVEL_TAGS))]
            except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
                if primary_error is not None:
                    raise primary_error
                return entries
            self.sources["bangumi"]["fallback"] = "legacy_subject_search" if name else "subject_browse"
            if primary_error is not None:
                code = (f"HTTP_{primary_error.response.status_code}" if isinstance(primary_error, httpx.HTTPStatusError)
                        else type(primary_error).__name__)
                self.record("bangumi", [], False, [code])
            return [*entries, *rows]

        return await self._group("bangumi", [*(search(name) for name in entities or (() if ids else ("",))),
                                              *(by_id(id_) for id_ in ids if re.fullmatch(r"[0-9]{1,16}", id_))])

    async def _tag_ids(self, preferences, exclusions, style_seeds=()):
        names = _strings((*(_terms(term)[0] for term in (*preferences, *exclusions)), *style_seeds))[:14]
        if not names:
            return {}
        try:
            response = await self.request("vndb", "POST", f"{VNDB}/tag", limit_seconds=3, json={
                "filters": _conjunction([["search", "=", name] for name in names], "or"),
                "fields": "name,aliases", "results": 50,
            })
            rows = _rows(response.json().get("results"))
        except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
            self.sources["vndb"]["tag_query"] = "unavailable"
            return {}
        aliases = {_key(alias): row["id"] for row in rows if re.fullmatch(r"g\d+", str(row.get("id", "")))
                   for alias in _strings(row.get("aliases"))}
        aliases.update({_key(row["name"]): row["id"] for row in rows if isinstance(row.get("name"), str)
                        and re.fullmatch(r"g\d+", str(row.get("id", "")))})
        return aliases

    async def _vndb_release_aliases(self, names, *, synopsis=False):
        try:
            response = await self.request("vndb", "POST", f"{VNDB}/release", limit_seconds=4 if synopsis else 3, reserve_seconds=5, json={
                "filters": _conjunction([
                    ["rtype", "=", "complete"], _conjunction([["search", "=", name] for name in names], "or"),
                ]),
                "fields": "title,alttitle,languages{title,latin},vns{id,rtype}",
                "results": 30,
            })
            body = response.json()
            if body.get("more"):
                return {}
        except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
            self.sources["vndb"]["release_alias_query"] = "unavailable"
            return {}
        ids_by_name = {name: set() for name in names}
        for row in _rows(body.get("results")):
            titles = [row.get("title"), row.get("alttitle")]
            titles.extend(value for lang in _rows(row.get("languages")) for value in (lang.get("title"), lang.get("latin")))
            keys = set(map(_key, _strings(titles)))
            links = _rows(row.get("vns"))
            if len(links) != 1 or links[0].get("rtype") != "complete":
                continue
            for name, ids in ids_by_name.items():
                if _key(name) in keys and re.fullmatch(r"v\d+", str(id_ := links[0].get("id", ""))):
                    ids.add(id_)
        return {name: next(iter(ids)) for name, ids in ids_by_name.items() if len(ids) == 1}

    async def vndb(self, entities, release_range, limit, ranking, low, high, languages, platforms, preferences, exclusions, steam_only,
                   min_minutes, max_minutes, *, staff=False, synopsis=False, ids=(), focused=False,
                   steam_dates=(), style_seeds=()):
        filters = []
        if release_range:
            start, end = release_range
            filters.extend((["released", ">=", start], ["released", "<", end]))
        if steam_dates:
            start, end = steam_dates
            filters.append(["release", "=", ["and", ["released", ">=", start], ["released", "<", end],
                                              ["extlink", "=", "steam"], ["official", "=", 1],
                                              ["rtype", "!=", "trial"], ["patch", "!=", 1]]])
        matches = [["search", "=", name] for name in entities]
        matches.extend(["id", "=", id_] for id_ in ids if re.fullmatch(r"v[0-9]{1,16}", id_))
        if matches:
            filters.append(_conjunction(matches, "or"))
        if low is not None:
            filters.append(["rating", ">=", math.ceil(low)])
        if high is not None:
            filters.append(["rating", "<=", math.floor(high)])
        if languages and not steam_only:
            filters.append(_conjunction([["lang", "=", language] for language in languages], "or"))
        if platforms:
            filters.append(_conjunction([["platform", "=", platform] for platform in platforms], "or"))
        if min_minutes is not None or max_minutes is not None:
            buckets = ((0, 120), (120, 600), (600, 1800), (1800, 3000), (3000, float("inf")))
            lengths = [index for index, (start, end) in enumerate(buckets, 1)
                       if (min_minutes is None or end >= min_minutes) and (max_minutes is None or start <= max_minutes)]
            filters.append(_conjunction([["length", "=", length] for length in lengths], "or"))
        tag_ids = await self._tag_ids(preferences, exclusions, (term for group in style_seeds for term in group)) if not focused else {}
        if style_seeds:
            seeds = [_strings(tag_ids.get(_key(value)) for value in group) for group in style_seeds]
            seeds = [_conjunction([["tag", "=", [id_, 0, 0.6]] for id_ in group], "or") for group in seeds if group]
            if not seeds:
                return []
            filters.append(_conjunction(seeds))
        for terms, op, spoiler in ((preferences, "=", 0), (exclusions, "!=", 2)):
            for term in terms:
                id_ = tag_ids.get(_key(_terms(term)[0]))
                if id_:
                    filters.append(["tag", op, [id_, spoiler, 1.0]])
        sort = "searchrank" if len(entities) == 1 else "rating" if ranking in {"rating", "vndb"} else "votecount"
        fields = VN_FIELDS + (",description" if synopsis else "")
        if staff:
            fields += ",staff{name,original,role},va{staff{name,original},character{name}}"

        async def search():
            budget = ({"limit_seconds": 4 if synopsis else 3, "reserve_seconds": 5} if focused
                      else {"limit_seconds": 8} if synopsis else {})
            response = await self.request("vndb", "POST", f"{VNDB}/vn", **budget, json={
                "filters": _conjunction(filters), "fields": fields,
                "results": limit, "sort": sort,
                "reverse": sort != "searchrank",
            })
            return _parse_vndb(response.json())

        entries = await self._group("vndb", [search()])
        known_names = {_key(name) for row in entries for name in row.names}
        unresolved = tuple(name for name in entities if _key(name) not in known_names)
        state = self.sources["vndb"]
        search_timed_out = synopsis and bool({"TimeoutError", "ReadTimeout", "ConnectTimeout"}.intersection(state.get("errors", ())))
        if not focused and unresolved and (state["status"] != "unavailable" or search_timed_out):
            aliases = await self._vndb_release_aliases(unresolved, synopsis=synopsis)
            known_ids = {row.id for row in entries}
            missing_ids = tuple(sorted(set(aliases.values()) - known_ids))
            if missing_ids:
                entries.extend(await self.vndb((), (), limit, ranking, low, high, languages, platforms, preferences,
                                               exclusions, steam_only, min_minutes, max_minutes, staff=staff,
                                               synopsis=synopsis, ids=missing_ids, focused=True))
            for row in entries:
                row.names = _strings((*row.names, *(alias for alias, id_ in aliases.items() if id_ == row.id)))
        return entries

    async def egs(self, entities, release_range, limit, ranking, low, high, *, ids=(), rated=True, focused=False):
        if self.egs_client is False:
            self.sources["erogamescape"].update(status="unavailable", errors=["invalid_proxy"])
            return []

        async def search():
            budget = {"limit_seconds": 3, "reserve_seconds": 5} if focused else {}
            response = await self.request("erogamescape", "POST", f"{EGS_ROOT}/sql_for_erogamer_form.php",
                                          data={"sql": _egs_statement(entities, release_range, limit, ranking, low, high, ids, rated=rated)}, **budget)
            return _parse_egs(response.content.decode("utf-8", errors="replace"))

        return await self._group("erogamescape", [search()])

    async def ymgal(self, names=(), *, release_range=(), detail=False, synopsis=False, ids=(), limit=40):
        stats = self.sources["ymgal"]
        if self.ymgal_client.configuration_error == "not_configured":
            stats["status"] = "not_configured"
            return []
        deadline = self.pre_store_deadline
        matches = []
        errors = []
        successful = 0

        async def collect(call):
            nonlocal successful
            try:
                rows = await call
            except YmgalError as error:
                errors.append(error.code)
                return []
            successful += 1
            return rows

        if release_range:
            start, end = map(date.fromisoformat, release_range)
            days = (end - start).days
            offsets = list(range(0, days, 50)) if days <= 150 else [0, (days - 50) // 2, days - 50]
            sampled = []
            for offset in offsets:
                if errors and errors[-1] in {"rate_limited", "timeout"}:
                    break
                cursor = start + timedelta(days=offset)
                until = min(end, cursor + timedelta(days=50))
                before = successful
                rows = await collect(self.ymgal_client.releases(cursor, until, deadline=deadline, stats=stats))
                if successful > before:
                    sampled.append([cursor.isoformat(), until.isoformat()])
                entries = [entry for row in rows if (entry := _parse_ymgal(row))
                           and _fits_release_date(entry.released, cursor.isoformat(), until.isoformat())]
                matches.extend(entries[:max(5, limit // len(offsets))])
            stats["sampled_windows"] = sampled
            stats["coverage_complete"] = days <= 150 and successful == len(offsets)
            if not stats["coverage_complete"]:
                errors.append("date_window_not_fully_sampled")
        else:
            for id_ in ids[:4]:
                try:
                    payload = await self.ymgal_client.detail(id_, deadline=deadline, stats=stats)
                except YmgalError as error:
                    errors.append(error.code)
                else:
                    successful += 1
                    if (entry := _parse_ymgal(payload["game"], payload)) and entry.id == id_:
                        matches.append(entry)
                if errors and errors[-1] in {"rate_limited", "timeout"}:
                    break
            for name in _strings(names)[:8]:
                if errors and errors[-1] in {"rate_limited", "timeout"}:
                    break
                rows = await collect(self.ymgal_client.search(name, deadline=deadline, stats=stats))
                parsed = [entry for row in rows if (entry := _parse_ymgal(row, synopsis=synopsis))]
                if parsed and detail:
                    exact = [entry for entry in parsed if _key(name) in {_key(alias) for alias in entry.names}]
                    target = exact[0] if len(exact) == 1 else parsed[0] if not exact else None
                    if target:
                        try:
                            payload = await self.ymgal_client.detail(target.id, deadline=deadline, stats=stats)
                        except YmgalError as error:
                            errors.append(error.code)
                        else:
                            if (expanded := _parse_ymgal(payload["game"], payload)) and expanded.id == target.id:
                                expanded.names = _strings((*expanded.names, *target.names))
                                expanded.display = expanded.display or target.display
                                if target.details.get("description"):
                                    expanded.details.setdefault("description", target.details["description"])
                                parsed = [expanded if row.id == target.id else row for row in parsed]
                matches.extend(parsed)
        matches = list({entry.id: entry for entry in matches}.values())
        self.record("ymgal", matches, bool(successful), errors)
        return matches

    async def _group(self, site, requests):
        results = await asyncio.gather(*requests, return_exceptions=True)
        errors = [result for result in results if isinstance(result, BaseException)]
        for error in errors:
            if isinstance(error, asyncio.CancelledError):
                raise error
        good = [result for result in results if isinstance(result, list)]
        entries = list({entry.id: entry for rows in good for entry in rows}.values())
        self.record(site, entries, bool(good), [f"HTTP_{error.response.status_code}" if isinstance(error, httpx.HTTPStatusError)
                                                else type(error).__name__ for error in errors])
        return entries

    def record(self, site, rows, successful, errors):
        status = self.sources[site]
        self.seen[site].update(row.id for row in rows)
        if successful:
            self.successful.add(site)
        if errors:
            status["errors"] = list(dict.fromkeys([*status.get("errors", ()), *errors]))
        status["rows"] = len(self.seen[site])
        status["status"] = ("partial" if status.get("errors") else "ok") if site in self.successful else "unavailable"

    async def releases(self, vn_ids, today, *, for_steam=False, include_versions=False, languages=(), platforms=(),
                       official_only=False, release_range=()):
        if not vn_ids:
            return []
        filters = [
            ["vn", "=", _conjunction([["id", "=", id_] for id_ in vn_ids], "or")],
            ["rtype", "!=", "trial"] if for_steam else ["rtype", "=", "complete"],
        ]
        if for_steam:
            filters.extend((["official", "=", 1], ["patch", "!=", 1]))
            if not include_versions:
                filters.append(["extlink", "=", "steam"])
            if release_range:
                filters.extend((["released", ">=", release_range[0]], ["released", "<", release_range[1]]))
        else:
            filters.append(["released", "<=", today.isoformat()])
            if official_only:
                filters.append(["official", "=", 1])
        if languages:
            filters.append(_conjunction([["lang", "=", language] for language in languages], "or"))
        if platforms:
            filters.append(_conjunction([["platform", "=", platform] for platform in platforms], "or"))
        try:
            response = await self.request("vndb", "POST", f"{VNDB}/release", limit_seconds=4, reserve_seconds=3, json={
                "filters": _conjunction(filters), "fields": RELEASE_FIELDS, "results": 50,
                **({"sort": "released"} if include_versions else {}),
            })
            return _rows(response.json().get("results"))
        except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
            self.sources["vndb"]["steam_link_query" if for_steam else "edition_query"] = "unavailable"
            return []

    async def steam(self, names, ids, country, years, developers, *, languages=(), require_price=False, reviews=False,
                    review_text=False, synopsis=False, link_origins=None, release_range=(), allowed_years=(), styles=()):
        names = _strings(names)
        keys = {_key(name) for name in names if _key(name)}
        params = {"l": "english", **({"cc": country} if country else {})}
        linked = bool(ids)
        if not ids:
            try:
                response = await self.request("steam", "GET", f"{STEAM}/api/storesearch", limit_seconds=4,
                                              params={**params, "term": names[0]})
                rows = _rows(response.json().get("items"))
                ids = _strings(str(row.get("id", "")) for row in rows if str(row.get("id", "")).isdigit()
                               and isinstance(row.get("name"), str) and _key(row["name"]) in keys)
            except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
                return {"status": "lookup_unavailable"}
            if len(ids) != 1:
                return {"status": "ambiguous_match" if ids else "no_verified_listing"}
        fallback = {"status": "no_verified_listing"}
        for id_ in ids[:4]:
            try:
                response = await self.request("steam", "GET", f"{STEAM}/api/appdetails", limit_seconds=4,
                                              params={**params, "appids": id_})
                data = response.json().get(id_, {})
                if data.get("success") is not True:
                    continue
                app = data.get("data") or {}
                title = app.get("name")
                if (str(app.get("steam_appid")) != id_ or app.get("type") != "game"
                        or not isinstance(title, str) or (not linked and _key(title) not in keys)):
                    continue
                release = app.get("release_date") or {}
                release_date = str(release.get("date") or "")
                if not linked and not _store_identity_matches(years, developers, release_date, app.get("developers")):
                    fallback = {"status": "unconfirmed_identity"}
                    continue
                upcoming = release.get("coming_soon")
                price = app.get("price_overview") or {}
                language_text = unescape(re.sub(r"<[^>]*>", " ", str(app.get("supported_languages") or "")))
                result = {"status": "verified_released_listing" if upcoming is False else "verified_upcoming_listing" if upcoming is True else "verified_listing",
                          "app_id": id_, "title": title, "url": f"{STEAM}/app/{id_}/", "date": release_date,
                          "release_date": _steam_date(release_date),
                          "languages": re.sub(r"\s+", " ", language_text).strip()[:450],
                          "platforms": [name for name, supported in (app.get("platforms") or {}).items() if supported is True],
                          "match": (link_origins or {}).get(id_, "catalogue") + "_link" if linked else "exact_title", "country": country}
                if any(str(genre.get("description", "")).casefold() == "early access" for genre in _rows(app.get("genres"))):
                    result["early_access"] = True
                if price.get("final_formatted"):
                    result["observed_price"] = str(price["final_formatted"])
                    result["currency"] = str(price.get("currency") or "")
                if app.get("is_free") is True:
                    result["is_free"] = True
                if styles:
                    result["style_description_signals"] = _description_style_signals(app.get("short_description"))
                if synopsis:
                    for field in ("short_description", "about_the_game"):
                        if description := _source_excerpt(app.get(field), 500):
                            result["description"] = description
                            break
                if upcoming is False and _steam_matches(result, languages, require_price, release_range, allowed_years):
                    if reviews:
                        result.update(await self.steam_reviews(id_, samples=review_text))
                    return result
                if not fallback["status"].startswith("verified_") or upcoming is False and _steam_in_scope(result, release_range, allowed_years):
                    fallback = result
            except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
                if fallback["status"] == "no_verified_listing":
                    fallback = {"status": "lookup_unavailable"}
        return fallback

    async def steam_reviews(self, id_, *, samples=False):
        try:
            response = await self.request("steam", "GET", STEAM_REVIEWS, limit_seconds=3, params={
                "input_json": json.dumps({
                    "appid": int(id_), "languages": ["all"], "num_per_page": 10 if samples else 1,
                }, separators=(",", ":")),
            })
            body = response.json().get("response", {})
            summary = body.get("query_summary") or {}
        except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
            return {}
        result = {}
        if isinstance(summary, dict) and all(key in summary for key in ("total_positive", "total_negative", "total_reviews")):
            result["review_summary"] = {
                "positive": _votes(summary["total_positive"]), "negative": _votes(summary["total_negative"]),
                "reviews": _votes(summary["total_reviews"]), "label": _source_excerpt(summary.get("review_score_desc"), 60),
                "scope": "all_languages_steam_purchases",
            }
        if samples:
            excerpts = []
            seen = set()
            for row in _rows(body.get("reviews"))[:10]:
                text = _source_excerpt(row.get("review"), 180)
                if len(text) < 8 or text in seen:
                    continue
                seen.add(text)
                excerpts.append({"text": text, "language": _source_excerpt(row.get("language"), 30),
                                 "recommended": row.get("voted_up") if type(row.get("voted_up")) is bool else None})
            excerpts.sort(key=lambda row: row["language"] not in {"schinese", "tchinese"})
            first = excerpts[:1]
            if first and (other := next((row for row in excerpts if row["recommended"] is not None
                                         and row["recommended"] != first[0]["recommended"]), None)):
                first.append(other)
            first.extend(row for row in excerpts if row not in first)
            if first:
                result["review_samples"] = first[:3]
        if result:
            result["review_url"] = f"https://steamcommunity.com/app/{id_}/reviews/"
        return result

    async def steam_tags(self, id_):
        try:
            response = await self.request("steam", "GET", f"{STEAM}/app/{id_}/", limit_seconds=2.5, params={"l": "english"})
            parser = _StoreTags()
            parser.feed(response.text)
            parser.close()
            return _strings(parser.tags)[:30]
        except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
            return ()


def _store_identity_matches(years, developers, release_date, store_developers):
    def names(values):
        return {_key(part) for value in _strings(values) for part in (value, *re.split(r"[/／+&]", value)) if _key(part)}

    catalogue_names, store_names = names(developers), names(store_developers)
    if catalogue_names and store_names:
        return bool(catalogue_names & store_names)
    return bool(set(re.findall(r"(?<!\d)[12]\d{3}(?!\d)", release_date)) & set(years))


def _steam_languages(steam: dict) -> set[str]:
    text = steam.get("languages", "").casefold()
    names = {"zh-Hans": "simplified chinese", "zh-Hant": "traditional chinese", "ja": "japanese", "en": "english", "ko": "korean"}
    return {code for code, name in names.items() if name in text}


def _steam_date(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.replace(",", " ")).strip()
    for pattern in ("%Y-%m-%d", "%d %b %Y", "%b %d %Y", "%d %B %Y", "%B %d %Y"):
        try:
            return datetime.strptime(cleaned, pattern).date().isoformat()
        except ValueError:
            continue
    for pattern in ("%b %Y", "%B %Y"):
        try:
            return datetime.strptime(cleaned, pattern).date().isoformat()[:7]
        except ValueError:
            continue
    return cleaned if re.fullmatch(r"[12][0-9]{3}", cleaned) else ""


def _steam_in_scope(steam, release_range, allowed_years):
    released = steam.get("release_date", "")
    return ((not release_range or bool(released and _fits_release_date(released, *release_range)))
            and (not allowed_years or released[:4] in allowed_years))


def _steam_matches(steam, languages=(), require_price=False, release_range=(), allowed_years=()):
    return (steam["status"] == "verified_released_listing"
            and (not languages or bool(set(languages).intersection(_steam_languages(steam))))
            and (not require_price or bool(steam.get("observed_price") or steam.get("is_free")))
            and _steam_in_scope(steam, release_range, allowed_years))


def _edition(row, vn_id, today, languages, platforms, *, official_only=False):
    if not any(vn.get("id") == vn_id and vn.get("rtype") == "complete" for vn in _rows(row.get("vns"))):
        return None
    id_ = str(row.get("id", ""))
    released = _date(row.get("released"))
    if not re.fullmatch(r"r\d+", id_) or not _known_released(released, today):
        return None
    actual_languages = _strings(item.get("lang") for item in _rows(row.get("languages")) if item.get("mtl") is not True)
    actual_platforms = _strings(row.get("platforms"))
    if languages and not set(languages).intersection(actual_languages):
        return None
    if platforms and not set(platforms).intersection(actual_platforms):
        return None
    if official_only and row.get("official") is not True:
        return None
    return {"id": id_, "title": str(row.get("title") or ""), "released": released, "languages": actual_languages,
            "platforms": actual_platforms, "url": f"https://vndb.org/{id_}", "is_patch": row.get("patch") is True,
            "official": row.get("official") is True}


def _release_record(group: list[Entry]) -> Entry | None:
    dated = [row for source in FIRST_RELEASE_SOURCES for row in group if row.source == source and row.released]
    if not dated:
        return None
    preferred = dated[0]
    return max((row for row in dated if row.released.startswith(preferred.released)), key=lambda row: len(row.released))


def _official_steam_links(row, vn_id, steam_dates):
    if (row.get("official") is not True or row.get("patch") is True
            or not any(vn.get("id") == vn_id and vn.get("rtype") in {"complete", "partial"}
                       for vn in _rows(row.get("vns")))):
        return ()
    if steam_dates and not _fits_release_date(_date(row.get("released")), *steam_dates):
        return ()
    return _links(row)[1]


def _is_partial_version_date(rows, vn_id, released, store):
    if not released or (store["status"] == "verified_released_listing"
                        and _date(store.get("release_date"))[:4] == released[:4]):
        return False
    matches = {
        vn.get("rtype") for row in rows if row.get("official") is True and row.get("patch") is not True
        and _date(row.get("released")) == released
        for vn in _rows(row.get("vns")) if vn.get("id") == vn_id
    }
    return matches == {"partial"}


def _earlier_steam_release(store: dict, released: str, today: date) -> str:
    steam_date = store.get("release_date", "")
    if store["status"] != "verified_released_listing" or not _known_released(steam_date, today):
        return ""
    # A storefront's one-day offset can reflect timezone differences.
    if not released or date.fromisoformat(_earliest_possible_date(released)) - date.fromisoformat(steam_date) > timedelta(days=1):
        return steam_date
    return ""


def _sort_groups(groups: list[list[Entry]], ranking: str):
    if ranking in {"bangumi", "vndb", "erogamescape"}:
        def source_rank(group):
            row = next((entry for entry in group if entry.source == ranking and entry.rating is not None), None)
            return (row.rating, row.votes) if row else (-1, 0)

        return sorted(groups, key=source_rank, reverse=True)
    entries = [row for group in groups for row in group]
    scores = {}
    for metric in ("heat", "rating"):
        source_scores = {}
        for source in ("bangumi", "vndb", "erogamescape"):
            values = sorted({getattr(row, metric) for row in entries if row.source == source and getattr(row, metric)},
                            reverse=True)
            ranks = {value: 1 / (60 + position) for position, value in enumerate(values, 1)}
            source_scores.update({(row.source, row.id): ranks[getattr(row, metric)] for row in entries
                                  if row.source == source and getattr(row, metric) in ranks})
        scores[metric] = source_scores

    def mean_score(group, metric):
        values = [scores[metric][(row.source, row.id)] for row in group if (row.source, row.id) in scores[metric]]
        return sum(values) / len(values) if values else 0

    def score(group):
        if ranking == "popularity":
            return mean_score(group, "heat")
        if ranking == "rating":
            return mean_score(group, "rating")
        return (mean_score(group, "heat") + mean_score(group, "rating")) / 2

    return sorted(groups, key=score, reverse=True)


def _passes(group, allowed_years, release_range, preferences, exclusions, min_votes, min_minutes, max_minutes,
            score_source, low, high, *, allow_missing=False):
    if not allow_missing and max(row.votes for row in group) < min_votes:
        return False
    release_record = _release_record(group)
    released = release_record.released if release_record else ""
    if allowed_years and (released or not allow_missing) and released[:4] not in allowed_years:
        return False
    if release_range and (released or not allow_missing) and not _fits_release_date(released, *release_range):
        return False
    visible_tags = {_key(tag) for row in group for tag in row.tags}
    all_tags = visible_tags | {_key(tag) for row in group for tag in row.hidden_tags}
    if (not allow_missing and all(row.source == "bangumi" for row in group)
            and not visible_tags.intersection(map(_key, VISUAL_NOVEL_TAGS))):
        return False
    if not allow_missing and any(not visible_tags.intersection(map(_key, _terms(term))) for term in preferences):
        return False
    if exclusions and ((not allow_missing and not any(row.source == "vndb" for row in group))
                       or any(all_tags.intersection(map(_key, _terms(term))) for term in exclusions)):
        return False
    minutes = next((row.minutes for row in group if row.minutes), 0)
    if (minutes or not allow_missing) and ((min_minutes is not None and (not minutes or minutes < min_minutes))
                                          or (max_minutes is not None and (not minutes or minutes > max_minutes))):
        return False
    if score_source:
        rating = next((row.rating for row in group if row.source == score_source), None)
        if (rating is not None or not allow_missing) and (
            rating is None or (low is not None and rating < low) or (high is not None and rating > high)
        ):
            return False
    return True


def _candidate_keyword(group):
    names = [name for row in group for name in row.names]
    return next((name for name in names if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", name)), group[0].title)


async def _gal_news(lookup, result, count, release_range, titles):
    for site in lookup.sources:
        if site != "ymgal":
            lookup.sources[site]["status"] = "not_applicable"
    stats = lookup.sources["ymgal"]
    if lookup.ymgal_client.configuration_error == "not_configured":
        stats["status"] = "not_configured"
        return {**result, "reason": "ymgal_news_not_configured"}
    try:
        rows = await lookup.ymgal_client.news(deadline=lookup.deadline, stats=stats)
    except YmgalError as error:
        stats.update(status="unavailable", errors=[error.code])
        return {**result, "reason": "ymgal_news_unavailable"}
    articles = []
    for row in rows:
        id_ = str(row.get("topicId") or "")
        title = str(row.get("title") or "").strip()
        published_at = str(row.get("publishTime") or "")[:35]
        summary = re.sub(r"<[^>]*>", " ", str(row.get("introduction") or ""))[:350]
        if release_range:
            try:
                published_date = datetime.fromisoformat(published_at).date().isoformat()
            except ValueError:
                continue
            if not release_range[0] <= published_date < release_range[1]:
                continue
        if titles and not any(_key(value) and _key(value) in _key(title + " " + summary) for value in titles):
            continue
        if title and re.fullmatch(r"[0-9]{1,20}", id_):
            articles.append({"id": id_, "title": title[:160], "url": f"{YMGAL}/co/article/{id_}",
                             "published_at": published_at, "summary": summary})
    stats.update(status="ok", rows=len(articles))
    return {**result, "status": "ok" if articles else "insufficient_evidence", "news": articles[:count],
            "scope": "ymgal_editorial_news", "coverage": "latest_page_only",
            "reason": "" if articles else "ymgal_news_empty"}


async def gal_recommend(
    client: httpx.AsyncClient, query: str, *, entities=(), year=None, language="", platform="", preferences=(),
    exclusions=(), ranking="", count=3, min_votes=100, candidate_limit=30, timeout=25, bangumi_token="",
    min_score=None, max_score=None, score_source="", min_hours=None, max_hours=None, region="", now=None,
    excluded_titles=(), historical_popularity=False, egs_client: httpx.AsyncClient | Literal[False] | None = None,
    years=(), date_from="", date_to="", official_translation=False, ymgal: YmgalClient | None = None,
    task="recommendation", fields=(), similar_to="", styles=(), date_basis="first_release", min_votes_required=False,
    search_terms=(), search_tags=(),
) -> dict:
    """Combine complementary catalogue evidence and verify Steam listings where possible."""
    today = now or date.today()
    if hasattr(today, "date"):
        today = today.date()
    lookup = _Lookup(client, max(3, min(40, float(timeout))), egs_client, ymgal)
    exclusions = _strings(exclusions)[:6]
    excluded_titles = _strings(excluded_titles)
    rejected_names, rejected_ids = _excluded_work(excluded_titles)
    names = _strings(entities)[:4]
    if not names:
        names = _strings(a or b for a, b in re.findall(r"《([^》]+)》|「([^」]+)」", query or ""))[:4]
    names = tuple(name for name in names if _key(name) not in rejected_names)
    if similar_to:
        names = tuple(name for name in names if _key(name) != _key(similar_to))
    wanted_count = len(names) if names else max(1, min(5, int(count)))
    result = {"status": "insufficient_evidence", "sources": lookup.sources, "items": [],
              "as_of": today.isoformat(), "requested_count": wanted_count}
    fields = set(_strings(fields))
    discovery = task == "recommendation"
    synopsis = bool(names and not discovery and "summary" in fields)
    review_text = bool(names and not discovery and "reviews" in fields)
    released_only = discovery and not re.search(r"即将(?:发售|发行|上线)|待发售|未发售|预定发售", query or "")
    if date_basis not in {"first_release", "steam"}:
        return {**result, "reason": "unknown_release_date_basis"}
    result["date_basis"] = date_basis
    if task in {"details", "comparison"} and not names:
        return {**result, "reason": "missing_galgame_title"}
    if historical_popularity:
        return {**result, "reason": "historical_popularity_snapshot_unavailable"}

    language_key = LANGUAGE_ALIASES.get(str(language).strip().casefold(), str(language).strip().casefold())
    if official_translation and not language_key:
        language_key = "zh"
    platform_key = " ".join(str(platform).casefold().split())
    platform_key = PLATFORM_ALIASES.get(platform_key, platform_key)
    if set(platform_key.split()) == {"steam", "pc"}:
        platform_key = "steam"
    languages = LANGUAGES.get(language_key, ())
    platforms = PLATFORMS.get(platform_key, ())
    steam_only = platform_key == "steam" or date_basis == "steam"
    if language and not languages:
        return {**result, "reason": "unsupported_language_constraint"}
    if platform and platform_key not in PLATFORMS:
        return {**result, "reason": "unsupported_platform_constraint"}
    country = {"国区": "CN", "中国大陆": "CN", "中国": "CN", "日区": "JP", "日本": "JP", "港区": "HK",
               "美区": "US", "美国": "US", "台区": "TW"}.get(str(region).strip(), str(region).strip().upper())
    if region and (not steam_only or not re.fullmatch(r"[A-Z]{2}", country)):
        return {**result, "reason": "regional_availability_not_verified"}
    year_values = (*years, *(() if year is None else (year,)))
    if any(not str(value).isdigit() or not 1900 <= int(value) <= 9998 for value in year_values):
        return {**result, "reason": "invalid_release_year"}
    allowed_years = {str(int(value)) for value in year_values}
    if date_from or date_to:
        try:
            start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except (TypeError, ValueError):
            return {**result, "reason": "invalid_release_date_range"}
        if start >= end:
            return {**result, "reason": "invalid_release_date_range"}
        release_range = (start.isoformat(), end.isoformat())
    elif allowed_years:
        release_range = (f"{min(allowed_years)}-01-01", f"{int(max(allowed_years)) + 1:04d}-01-01")
    elif task == "release" and not names:
        future = bool(re.search(r"未来|即将|待发售|预定|接下来", query or ""))
        start = today + timedelta(days=1) if future else today - timedelta(days=29)
        release_range = (start.isoformat(), (start + timedelta(days=30)).isoformat())
    else:
        release_range = ()
    if task == "news":
        return await _gal_news(lookup, result, wanted_count, release_range, names)
    if task == "release" and not names and release_range:
        if re.search(r"未来|即将|待发售|预定|接下来", query or ""):
            release_range = (max(release_range[0], (today + timedelta(days=1)).isoformat()), release_range[1])
        elif re.search(r"已发售|已经发售|已发行|已经发行", query or ""):
            release_range = (release_range[0], min(release_range[1], (today + timedelta(days=1)).isoformat()))
        if release_range[0] >= release_range[1]:
            return {**result, "reason": "release_date_constraint_has_no_dates"}
    steam_dates, steam_years = (release_range, allowed_years) if date_basis == "steam" else ((), set())
    if date_basis == "steam":
        release_range, allowed_years = (), set()
    score_aliases = {"egs": "erogamescape", "批评空间": "erogamescape", "bgm": "bangumi"}
    ranking = str(ranking or "").strip().casefold()
    ranking = score_aliases.get(ranking, ranking)
    score_source = str(score_source or "").strip().casefold()
    score_source = score_aliases.get(score_source, score_source)
    if not score_source and (min_score is not None or max_score is not None):
        score_source = ranking if ranking in {"vndb", "bangumi", "erogamescape"} else "erogamescape"
    if score_source and score_source not in {"vndb", "bangumi", "erogamescape"}:
        return {**result, "reason": "unknown_score_source"}
    if ranking == "rating" and score_source:
        ranking = score_source
    scale = 10 if score_source == "bangumi" else 100
    low, high = _number(min_score, scale), _number(max_score, scale)
    if (min_score is not None and low is None) or (max_score is not None and high is None) or (low is not None and high is not None and low > high):
        return {**result, "reason": "invalid_score_range"}
    min_length, max_length = _number(min_hours, 2000), _number(max_hours, 2000)
    if ((min_hours is not None and min_length is None) or (max_hours is not None and max_length is None)
            or (min_length is not None and max_length is not None and min_length > max_length)):
        return {**result, "reason": "invalid_playtime_range"}
    min_minutes = min_length * 60 if min_length is not None else None
    max_minutes = max_length * 60 if max_length is not None else None
    limit = max(5, min(50, int(candidate_limit)))
    min_votes = max(0, int(min_votes)) if discovery else 0
    required_votes = min_votes if min_votes_required else 0
    preferences = _strings(preferences)[:6]
    style_terms = tuple(value for value in preferences if STYLE_ALIASES.get(value, value) in STYLES)
    preferences = tuple(value for value in preferences if value not in style_terms)
    styles = _strings(STYLE_ALIASES.get(value, value) for value in (*_strings(styles), *style_terms))[:6]
    if discovery and similar_to:
        anchor = await lookup.vndb((similar_to,), (), 8, "", None, None, (), (), (), (), False, None, None)
        exact = [row for row in anchor if _key(similar_to) in {_key(name) for name in row.names}]
        if len(exact) != 1:
            return {**result, "reason": "similarity_basis_not_verified"}
        anchor = exact[0]
        tag_keys = {_key(tag) for tag in anchor.tags}
        similar_genres = [genre for genre, aliases in GENRES.items()
                          if tag_keys.intersection(map(_key, aliases))
                          and not {_key(name) for name in exclusions}.intersection(map(_key, aliases))]
        if not similar_genres:
            return {**result, "reason": "similarity_tags_unavailable"}
        preferences = _strings((*preferences, similar_genres[0]))[:6]
        excluded_titles = (*_strings(excluded_titles), *anchor.names)
        rejected_names, rejected_ids = _excluded_work(excluded_titles)
        rejected_ids.add((anchor.source, anchor.id))
        result["similarity_basis"] = {"title": anchor.display or anchor.title, "url": anchor.url, "shared_tag": similar_genres[0]}

    def bounds(site):
        return (low, high) if site == score_source else (None, None)

    def bangumi_lookup(titles, ids=(), *, focused=False, tags=("Galgame",), discovery_hint=False):
        window = release_range if discovery_hint or not (focused or titles) else ()
        return lookup.bangumi(titles, window, limit, ranking, *bounds("bangumi"), bangumi_token,
                              ids=ids, focused=focused, tags=tags, synopsis=synopsis)

    def vndb_lookup(titles, ids=(), *, focused=False, style_seeds=(), discovery_hint=False):
        window = release_range if discovery_hint or not (focused or titles) else ()
        return lookup.vndb(titles, window, limit, ranking, *bounds("vndb"),
                           languages if discovery else (), platforms if discovery else (), preferences, exclusions,
                           steam_only, min_minutes, max_minutes, staff="staff" in fields, synopsis=synopsis, ids=ids, focused=focused,
                           steam_dates=steam_dates if not names else (), style_seeds=style_seeds)

    def egs_lookup(titles, ids=(), *, focused=False):
        return lookup.egs(titles, () if focused or titles else release_range, limit, ranking, *bounds("erogamescape"), ids=ids,
                          rated=discovery and not focused, focused=focused)

    def ymgal_lookup(titles, ids=(), *, focused=False):
        return lookup.ymgal(titles, ids=ids, detail=bool(names), synopsis=synopsis,
                            release_range=release_range if not titles and not ids and not focused else (), limit=limit)

    title_keys = set(map(_key, names))
    unavailable_hints = title_keys | rejected_names | {""}
    query_hints = tuple(value for value in _strings(search_terms) if len(value) <= 90
                        and _key(value) not in unavailable_hints)[:3]
    tag_hints = tuple(value for value in _strings(search_tags) if len(value) <= 45
                      and _key(value) not in {"", "galgame"})[:4]
    jobs = [bangumi_lookup(names), vndb_lookup(names), egs_lookup(names)]
    has_ym_seed = bool(names or release_range)
    if has_ym_seed:
        jobs.append(ymgal_lookup(names))
    if query_hints and (discovery or names):
        jobs.append(vndb_lookup(query_hints, focused=True, discovery_hint=discovery))
        jobs.append(bangumi_lookup(query_hints[:2], focused=True, discovery_hint=discovery))
    if discovery and not names and tag_hints:
        jobs.append(vndb_lookup((), style_seeds=(tag_hints,)))
        jobs.extend(bangumi_lookup((), tags=("Galgame", tag)) for tag in tag_hints[:2])
    bangumi, vndb, egs, *extras = await asyncio.gather(*jobs)
    ym_rows = extras.pop(0) if has_ym_seed else []
    initial = {(row.source, row.id): row for row in [*vndb, *bangumi, *egs, *ym_rows]}
    for rows in extras:
        for row in rows:
            initial.setdefault((row.source, row.id), row)
    entries = list(initial.values())
    known_keys = {_key(name) for row in entries for name in row.names}
    unresolved_exclusions = tuple(value for value in excluded_titles if len(value) <= 90 and _key(value)
                                  and _key(value) not in known_keys and ("vndb", value.casefold()) not in rejected_ids)[:4]
    if entries and unresolved_exclusions and "vndb" in lookup.successful:
        aliases = await lookup._vndb_release_aliases(unresolved_exclusions)
        rejected_ids.update(("vndb", id_) for id_ in aliases.values())

    def excluded(group):
        return any((row.source, row.id) in rejected_ids or rejected_ids.intersection(row.bindings.items())
                   or rejected_names.intersection(map(_key, row.names)) for row in group)

    def filtered_groups(*, allow_missing=False):
        return [group for group in _merge(entries)
                if not excluded(group)
                and not (released_only and (release := _release_record(group))
                         and _earliest_possible_date(release.released) > today.isoformat())
                and _passes(group, allowed_years, release_range, preferences, exclusions, required_votes,
                            min_minutes, max_minutes, score_source if discovery else "", low, high,
                            allow_missing=allow_missing)]

    def recommendation_order(group):
        return (bool(steam_dates and not any(row.source == "vndb" for row in group)),
                _style_order(_style_evidence(group, styles)[1]), -_style_affinity(group, styles),
                ranking != "popularity" and max(row.votes for row in group) < min_votes)

    leads = _sort_groups(filtered_groups(allow_missing=True), ranking)
    if names:
        leads = [group for group in leads if any(title_keys.intersection(map(_key, row.names)) for row in group)]
    elif discovery:
        leads.sort(key=recommendation_order)
    leads = leads[:min(8, wanted_count + 3)]
    if not leads and not has_ym_seed and not steam_dates:
        current_window = ((today - timedelta(days=29)).isoformat(), (today + timedelta(days=1)).isoformat())
        entries.extend(await lookup.ymgal(release_range=current_window, limit=limit))
        leads = _sort_groups(filtered_groups(allow_missing=True), ranking)[:min(8, wanted_count + 3)]

    supplements = []
    if time.monotonic() < lookup.pre_store_deadline:
        for site, query_missing in (("bangumi", bangumi_lookup), ("vndb", vndb_lookup),
                                    ("erogamescape", egs_lookup), ("ymgal", ymgal_lookup)):
            if site == "erogamescape" and lookup.sources[site]["status"] == "unavailable":
                continue
            needs_role = {
                "vndb": True,
                "bangumi": bool(names or preferences or styles or ranking in {"", "balanced", "popularity", "bangumi"}),
                "erogamescape": bool(names or score_source == "erogamescape" or ranking in {"", "balanced", "rating", "erogamescape"}),
                "ymgal": bool(names or styles or not ym_rows or "staff" in fields),
            }[site]
            missing = [group for group in leads if not any(row.source == site for row in group)
                       and (needs_role or _release_record(group) is None or any(site in row.bindings for row in group))][:4]
            ids, canonical = [], []
            for group in missing:
                bound = _strings(row.bindings.get(site) for row in group)
                if bound:
                    ids.extend(bound)
                elif _key(title := _candidate_keyword(group)) not in title_keys:
                    canonical.append(title)
            if ids or canonical:
                supplements.append((missing, query_missing(_strings(canonical), _strings(ids), focused=True)))
    if supplements:
        known = {(row.source, row.id) for row in entries}
        additions = await asyncio.gather(*(search for _, search in supplements))
        for (missing, _), rows in zip(supplements, additions, strict=True):
            entries.extend(row for row in rows if (row.source, row.id) not in known
                           and any(_bind(row, member) > 0 for group in missing for member in group))

    groups = filtered_groups()
    if ranking in {"bangumi", "vndb", "erogamescape"}:
        groups = [group for group in groups if any(row.source == ranking and row.rating is not None for row in group)]
    groups = _sort_groups(groups, ranking)
    if discovery and not names:
        groups.sort(key=recommendation_order)
    if task == "release" and not names:
        groups.sort(key=lambda group: release.released if (release := _release_record(group)) else "",
                    reverse=bool(release_range and release_range[0] <= today.isoformat()))
    unmatched = []
    if names:
        selected = []
        for name in names:
            matches = [group for group in groups if any(_key(name) == _key(alias) for row in group for alias in row.names)]
            if len(matches) != 1:
                unmatched.append({"title": name, "reason": "ambiguous" if matches else "unverified"})
                continue
            if matches[0] not in selected:
                selected.append(matches[0])
        groups = selected
        for site in CATALOGUE_SOURCES:
            state = lookup.sources[site]
            matched = sum(row.source == site for group in groups for row in group)
            state["matched_rows"] = matched
            if state["status"] == "ok" and not matched:
                state["status"] = "no_match"
    if not groups:
        lookup.sources["steam"]["status"] = "no_candidate"
        return {**result, "reason": "no_eligible_catalogue_matches", "unmatched_titles": unmatched}
    needs_edition = bool((languages and not steam_only) or platforms)
    groups = groups[:min(10, wanted_count + (3 if discovery or needs_edition or steam_only else 0))]
    vn_ids = [row.id for group in groups for row in group if row.source == "vndb"]
    check_versions = bool(released_only and release_range)
    needing_links = (vn_ids if check_versions else
                     [row.id for group in groups for row in group if row.source == "vndb"
                      and (not row.steam_ids or steam_only and (languages or country or steam_dates))])
    if needs_edition:
        editions, steam_releases = await asyncio.gather(
            lookup.releases(vn_ids, today, languages=languages, platforms=platforms, official_only=official_translation),
            lookup.releases(needing_links, today, for_steam=True, include_versions=check_versions, release_range=steam_dates),
        )
        matching_editions = {}
        for vn_id in vn_ids:
            for row in editions:
                edition = _edition(row, vn_id, today, languages, platforms, official_only=official_translation)
                if edition:
                    matching_editions[vn_id] = edition
                    break
    else:
        matching_editions = {}
        steam_releases = await lookup.releases(
            needing_links, today, for_steam=True, include_versions=check_versions, release_range=steam_dates,
        )

    steam_workers = asyncio.Semaphore(3)
    style_workers = asyncio.Semaphore(3)

    async def verify(group):
        vndb_row = next((row for row in group if row.source == "vndb"), None)
        titles = _strings(name for row in group for name in row.names)
        linked = {id_: row.source for row in reversed(group) for id_ in row.steam_ids}
        if vndb_row:
            for row in steam_releases:
                linked.update({id_: "vndb" for id_ in _official_steam_links(row, vndb_row.id, steam_dates)})
        ids = _strings(linked)
        async with steam_workers:
            store = await lookup.steam(titles, ids, country, {row.released[:4] for row in group if row.released},
                                       _strings(dev for row in group for dev in row.developers),
                                       languages=languages if steam_only else (), require_price=bool(country),
                                       reviews=bool(names and not discovery and ("score" in fields or review_text)),
                                       review_text=review_text, synopsis=synopsis, link_origins=linked,
                                       release_range=steam_dates, allowed_years=steam_years, styles=styles)
        description_signals = store.pop("style_description_signals", ())
        style_result = _style_evidence(group, styles, store, store_description=description_signals)
        if style_result[1] and store["status"].startswith("verified_"):
            async with style_workers:
                tags = await lookup.steam_tags(store["app_id"])
            style_result = _style_evidence(group, styles, store, tags, description_signals)
        return group, titles, vndb_row, store, style_result

    checked = await asyncio.gather(*(verify(group) for group in groups))
    store_status = lookup.sources["steam"]
    verified = [store for _, _, _, store, _ in checked if store["status"].startswith("verified_")]
    unavailable = [store for _, _, _, store, _ in checked if store["status"] == "lookup_unavailable"]
    store_status.update(status="unavailable" if len(unavailable) == len(checked) else "partial" if unavailable else "ok" if verified else "no_match",
                        rows=len(verified), checked_candidates=len(checked))
    eligible_items = []
    for group, titles, vndb_row, store, style_result in checked:
        steam_valid = _steam_matches(store, languages if steam_only else (), bool(country), steam_dates, steam_years)
        if task == "release" and store["status"] == "verified_upcoming_listing" and not languages and not country:
            steam_valid = _steam_in_scope(store, steam_dates, steam_years)
        edition = matching_editions.get(vndb_row.id) if vndb_row else None
        if steam_dates and not steam_valid:
            continue
        if discovery and (steam_only and not steam_valid or needs_edition and not edition):
            continue
        display = next((row.display for row in group if row.source == "ymgal" and row.display), "")
        display = display or next((row.display for row in group if row.display), group[0].title)
        release = _release_record(group)
        released = release.released if release else ""
        release_source = release.source if release else None
        if check_versions and vndb_row and release_source == "vndb" and _is_partial_version_date(
                steam_releases, vndb_row.id, released, store):
            continue
        if earlier := _earlier_steam_release(store, released, today):
            if ((allowed_years and earlier[:4] not in allowed_years)
                    or (release_range and not _fits_release_date(earlier, *release_range))):
                continue
            released, release_source = earlier, "steam"
        release_disagreements = [{"source": row.source, "date": row.released, "url": row.url} for row in group
                                 if row.released and not (row.released.startswith(released) or released.startswith(row.released))]
        verified_tags = {_key(tag) for row in group for tag in row.tags}
        visible_genres = [genre for genre, terms in GENRES.items() if verified_tags.intersection(map(_key, terms))]
        present = {row.source for row in group}
        missing_sites = [source for source in CATALOGUE_SOURCES if source not in present]
        if not steam_valid:
            missing_sites.append("steam")
        missing_fields = []
        if needs_edition and not edition or steam_only and not steam_valid:
            missing_fields.append("official_translation" if official_translation else "release_edition")
        if "release" in fields and not (store.get("release_date") if date_basis == "steam" else released):
            missing_fields.append("release")
        if "store" in fields and not steam_valid:
            missing_fields.append("steam_store")
        score_records = (row for row in group if not score_source or row.source == score_source)
        if "score" in fields and not (any(row.rating is not None for row in score_records)
                                      or not score_source and store.get("review_summary")):
            missing_fields.append("score")
        if "duration" in fields and not (vndb_row and vndb_row.minutes):
            missing_fields.append("duration")
        if "staff" in fields and not any(row.details.get("staff") or row.details.get("voice_cast") for row in group):
            missing_fields.append("staff")
        if synopsis and not (any(row.details.get("description") for row in group) or store.get("description")):
            missing_fields.append("summary")
        if review_text and not store.get("review_samples"):
            missing_fields.append("steam_reviews")
        style_evidence, style_unverified = style_result
        if style_unverified:
            missing_fields.append("style")
        eligible_items.append({
            "title": display, "names": list(titles), "year": int(released[:4]) if released else None,
            "first_release": released, "first_release_source": release_source,
            "release_disagreements": release_disagreements,
            "release_status": "upcoming" if len(released) == 10 and released > today.isoformat() else
            "released" if _known_released(released, today) else "date_unconfirmed",
            "records": [row.evidence() for row in group], "tags": visible_genres,
            "style_evidence": style_evidence, "style_unverified": style_unverified,
            "length_minutes": vndb_row.minutes or None if vndb_row else None,
            "edition": edition, "steam": store, "missing_sources": missing_sites, "missing_fields": missing_fields,
        })
    if not eligible_items:
        if steam_dates and not any(_steam_in_scope(store, steam_dates, steam_years) for _, _, _, store, _ in checked):
            reason = "steam_release_date_not_verified"
        elif steam_only:
            reason = "steam_listing_or_language_not_verified"
        elif needs_edition:
            reason = "official_localized_edition_not_verified" if official_translation else "matching_edition_not_verified"
        else:
            reason = "no_eligible_catalogue_matches"
        return {**result, "reason": reason, "unmatched_titles": unmatched}
    if discovery and not names:
        eligible_items.sort(key=lambda item: (_style_order(item["style_unverified"]), bool(item["missing_fields"])))
    result["items"] = eligible_items[:wanted_count]
    full = not unmatched and len(result["items"]) == wanted_count and all(
        not item["missing_fields"] for item in result["items"]
    )
    reason = "" if full else "requested_evidence_incomplete"
    return {**result, "status": "ok" if full else "partial", "reason": reason,
            "unmatched_titles": unmatched, "ranking": ranking or "balanced",
            "score_filter_source": score_source, "scope": "bounded_catalogue_sample_current_cumulative_stats"}
