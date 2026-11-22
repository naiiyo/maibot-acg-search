"""Resolve supported catalogue links by their exact work, release, or store ID."""

from __future__ import annotations

import asyncio
import calendar
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import httpx

from .gal_catalogue import (
    LANGUAGE_ALIASES, LANGUAGES, PLATFORM_ALIASES, PLATFORMS, VNDB, VN_FIELDS,
    _Lookup, _date, _edition, _key, _links, _official_steam_links, _parse_vndb, _parse_ymgal,
    _rows, _source_excerpt, _steam_languages, _strings, _terms,
)
from .ymgal import YmgalError


@dataclass(frozen=True)
class WorkLink:
    source: str
    id: str
    kind: str
    url: str


_URL = re.compile(
    r"(?<![a-z0-9./=@%\\:-])(?:[a-z][a-z0-9+.-]{0,15}://|(?=(?:www\.)?(?:bgm\.tv|bangumi\.tv|chii\.in|"
    r"vndb\.org|ymgal\.games|store\.steampowered\.com)/))(?:(?!https?://)[^\s\u3000<>《》「」『』【】（）。，！？；、,;!?#\"'()\[\]])+"
    r"(?:[?#](?:(?![,;]https?://)[^\s\u3000<>《》「」『』【】（）。，！？；、\"'()\[\]])*)?",
    re.I | re.ASCII,
)
_BANGUMI_HOSTS = {"bgm.tv", "bangumi.tv", "chii.in"}
_COUNTRIES = {"国区": "CN", "大陆": "CN", "中国": "CN", "中国大陆": "CN", "日区": "JP", "日本": "JP",
              "美区": "US", "美国": "US", "港区": "HK", "香港": "HK", "中国香港": "HK", "台区": "TW",
              "台湾": "TW", "中国台湾": "TW"}
_EDITION_FIELDS = (
    "title,released,official,patch,languages{lang,mtl},platforms,"
    "vns{id,title,rtype},extlinks{name,id,url}"
)
_LABEL = {"bangumi": "Bangumi", "vndb": "VNDB", "steam": "Steam", "ymgal": "月幕"}
_FIELD_NAMES = {"summary": "作品简介", "reviews": "评论样本", "score": "评分", "duration": "游玩时长",
                "tags": "题材标签", "language": "对应版本的语言", "store": "商店条目", "release": "发行日期",
                "staff": "制作人员", "characters": "角色", "relations": "系列关系"}


def _work_link(token):
    try:
        url = urlsplit(token if "://" in token else "https://" + token)
        host = url.hostname or ""
        if url.scheme not in {"https", "http"} or url.netloc.casefold() != host or "\\" in token:
            return None
    except ValueError:
        return None
    host = host.removeprefix("www.")
    path = url.path.rstrip("/")
    if host in _BANGUMI_HOSTS and (m := re.fullmatch(r"/subject/([1-9][0-9]{0,11})(?:/(?:ep|characters|persons|relations|reviews|comments))?", path)):
        return WorkLink("bangumi", m[1], "work", f"https://bgm.tv/subject/{m[1]}")
    if host == "vndb.org" and (m := re.fullmatch(r"/([vr])([1-9][0-9]{0,11})", path)):
        identifier = m[1] + m[2]
        return WorkLink("vndb", identifier, "work" if m[1] == "v" else "edition", f"https://vndb.org/{identifier}")
    if host == "ymgal.games" and (m := re.fullmatch(r"/ga([1-9][0-9]{0,19})", path, re.I)):
        return WorkLink("ymgal", m[1], "work", f"https://www.ymgal.games/ga{m[1]}")
    if host == "store.steampowered.com" and (m := re.fullmatch(r"/app/([1-9][0-9]{0,9})(?:/[^/]+)?", path)):
        return WorkLink("steam", m[1], "app", f"https://store.steampowered.com/app/{m[1]}/")
    return None


def _token_parts(token):
    token = token.rstrip(".,;!?")
    # No incoming query or fragment will be forwarded. Chinese prose may directly follow an ID.
    if suffix := re.search(r"(?<=[0-9])[\u3400-\u9fff][^/#?@%\\]*$", token):
        if "?" not in token and "#" not in token and (link := _work_link(token[:suffix.start()])):
            return link, suffix[0]
    return _work_link(token), ""


def public_link_question(query: str) -> str:
    """Retain the question and exact public IDs without sharing raw link parameters."""
    def canonical(match):
        link, suffix = _token_parts(match[0])
        return f" {link.url} {suffix}" if link else " "

    return " ".join(_URL.sub(canonical, query).split())


def extract_work_links(query: str) -> tuple[WorkLink, ...]:
    """Keep input order and canonical IDs. Never fetch input URLs or their query strings."""
    found = {}
    for match in _URL.finditer(query):
        link, _ = _token_parts(match[0])
        if link is not None:
            found[link] = None
        if len(found) == 4:
            break
    return tuple(found)


def _entry_item(entry):
    return {"title": entry.display or entry.title, "first_release": entry.released,
            "first_release_source": entry.source if entry.released else "",
            "length_minutes": entry.minutes, "records": [entry.evidence()], "tags": entry.tags, "link_notes": [],
            "_names": entry.names, "_exclusion_tags": (*entry.tags, *entry.hidden_tags)}


def _edition_notes(row):
    notes = []
    labels = []
    if type(row.get("official")) is bool:
        labels.append("官方发行" if row["official"] else "非官方发行")
    if row.get("patch") is True:
        labels.append("补丁")
    if languages := _strings(language.get("lang") for language in _rows(row.get("languages"))):
        labels.append("语言 " + "、".join(languages))
    if platforms := _strings(row.get("platforms", [])):
        labels.append("平台 " + "、".join(platforms))
    if labels:
        notes.append("该发行版本 · " + " · ".join(labels))
    types = {"complete": "完整版", "trial": "试玩版", "partial": "部分内容"}
    for work in _rows(row.get("vns"))[:6]:
        identifier = str(work.get("id") or "")
        title = _source_excerpt(work.get("title"), 120)
        if title and re.fullmatch(r"v[1-9][0-9]{0,11}", identifier):
            kind = types.get(work.get("rtype"), "关联作品")
            notes.append(f"收录作品（{kind}）· {title} https://vndb.org/{identifier}")
    return notes


async def _release_query(lookup, filters, limit):
    response = await lookup.request("vndb", "POST", f"{VNDB}/release", limit_seconds=4,
                                    json={"filters": filters, "fields": _EDITION_FIELDS, "results": limit})
    return _rows(response.json().get("results"))


async def _steam_item(lookup, link, country, fields):
    store = await lookup.steam((), (link.id,), country, (), (), direct_app=True, synopsis=True,
                               reviews="reviews" in fields, review_text="reviews" in fields,
                               link_origins={link.id: "user"})
    if store.get("app_id") != link.id:
        return None
    kinds = {"game": "游戏", "demo": "试玩版", "dlc": "DLC", "music": "原声带", "video": "视频"}
    notes = ["链接指向 Steam 商店版本" + (" · " + kinds.get(kind, kind) if (kind := store.get("app_type")) else "")]
    names = [store["title"]]
    if parent := store.get("base_game"):
        notes.append(f"商店列出的本体 · {parent['title']} {parent['url']}")
    try:
        by_app = await _release_query(lookup, ["extlink", "=", ["steam", int(link.id)]], 4)
    except (httpx.HTTPError, TimeoutError, ValueError, AttributeError):
        by_app = []
    for release in by_app:
        if not re.fullmatch(r"r[1-9][0-9]{0,11}", str(release.get("id", ""))):
            continue
        if link.id not in _links(release)[1]:
            continue
        notes.append(f"VNDB 对应发行 · {_source_excerpt(release.get('title'), 120)} https://vndb.org/{release['id']}")
        notes.extend(_edition_notes(release))
        names.extend(value for vn in _rows(release.get("vns")) for value in (vn.get("title"), vn.get("id")))
    return {"title": store["title"], "steam": store, "link_notes": notes, "_names": _strings(names)}


async def _vndb_item(lookup, link, country, fields, language, platform, official_translation, today):
    if link.kind == "edition":
        releases = await _release_query(lookup, ["id", "=", link.id], 1)
        row = next((r for r in releases if r.get("id") == link.id and r.get("title")), None)
        if row is None:
            return None
        edition = {"url": link.url, "released": _date(row.get("released")), "official": row.get("official"),
                   "languages": _strings(lang.get("lang") for lang in _rows(row.get("languages"))),
                   "platforms": _strings(row.get("platforms"))}
        names = _strings(value for vn in _rows(row.get("vns")) for value in (vn.get("title"), vn.get("id")))
        item = {"title": _source_excerpt(row["title"], 150), "edition": edition, "link_notes": _edition_notes(row), "_names": names}
        if "store" in fields and (store_ids := _links(row)[1]):
            store = await lookup.steam((), store_ids, country, (), (), direct_app=True)
            if store.get("app_id"):
                item["steam"] = store
        return item
    columns = VN_FIELDS + ",description"
    if "staff" in fields:
        columns += ",staff{role,name,original}"
    response = await lookup.request("vndb", "POST", f"{VNDB}/vn", limit_seconds=5,
                                    json={"filters": ["id", "=", link.id], "fields": columns, "results": 1})
    entry = next((e for e in _parse_vndb(response.json()) if e.id == link.id), None)
    if not entry:
        return None
    item = _entry_item(entry)
    notes = item["link_notes"]
    if item["records"][0].get("staff"):
        notes.append("制作人员 · " + "、".join(f"{row['name']}（{row['role']}）" for row in entry.details["staff"] if row["name"]))
    if not (language or platform or official_translation or {"language", "store", "reviews"}.intersection(fields)):
        return item
    language_key = LANGUAGE_ALIASES.get(language.casefold(), language or ("zh" if official_translation else ""))
    languages = LANGUAGES.get(language_key, ())
    platform_key = platform.casefold().strip()
    platforms = PLATFORMS.get(PLATFORM_ALIASES.get(platform_key, platform_key), ())
    want_store = bool({"store", "reviews"}.intersection(fields)) or "steam" in platform_key.split()
    editions = await lookup.releases((link.id,), today, languages=languages, platforms=platforms,
                                     official_only=official_translation, for_steam=want_store)
    for release in editions:
        edition = _edition(release, link.id, today, languages, platforms, official_only=official_translation)
        if not edition:
            continue
        if want_store:
            store_ids = _official_steam_links(release, link.id, ())
            if not store_ids:
                continue
            if "reviews" in fields and any(vn.get("id") != link.id for vn in _rows(release.get("vns"))):
                # The store's audience reviewed the whole bundle, not this individual work.
                continue
            store = await lookup.steam((), store_ids, country, (), (), languages=languages,
                                       reviews="reviews" in fields, review_text="reviews" in fields)
            if store.get("app_id"):
                item["steam"] = store
        item["edition"] = edition
        labels = [*edition["languages"], *edition["platforms"]]
        notes.append("对应发行版本 · " + " · ".join([_source_excerpt(edition["title"], 130), *labels]))
        break
    if "edition" not in item and (language or platform or official_translation):
        item["link_gap"] = "条目已定位，本轮还没有核实符合所问条件的发行版本。"
    return item


def _missing_fields(item, wanted):
    store = item.get("steam") or {}
    edition = item.get("edition") or {}
    records = item.get("records") or []
    available = {
        "summary": item.get("summary") or store.get("description") or any(r.get("description") for r in records),
        "reviews": store.get("review_samples"),
        "score": item.get("score") is not None or store.get("review_summary") or any(r.get("rating") is not None for r in records),
        "duration": item.get("length_minutes"),
        "tags": item.get("tags"), "language": store.get("languages") or edition.get("languages"),
        "store": store.get("app_id"),
        "release": item.get("first_release") or item.get("date") or edition.get("released") or store.get("date"),
        "staff": item.get("people") or any(r.get("staff") for r in records),
        "characters": item.get("characters") or any(r.get("voice_cast") for r in records),
        "relations": item.get("relations") or item.get("series_guide"),
    }
    return [_FIELD_NAMES[key] for key in wanted if key in _FIELD_NAMES and not available.get(key)]


def _date_in_scope(value, intent):
    if not (value := _date(value)):
        return None
    years = intent.years or ((intent.year,) if intent.year else ())
    if years and int(value[:4]) not in years:
        return False
    if not (intent.date_from and intent.date_to):
        return True
    first = value + {4: "-01-01", 7: "-01"}.get(len(value), "")
    last = value
    if len(value) == 4:
        last += "-12-31"
    elif len(value) == 7:
        last += f"-{calendar.monthrange(int(value[:4]), int(value[5:]))[1]:02d}"
    if last < intent.date_from or first >= intent.date_to:
        return False
    return True if intent.date_from <= first and last < intent.date_to else None


def _check_constraints(item, link, intent):
    """Return an exclusion, known mismatches, and conditions missing evidence, in that order."""
    names = {_key(value) for value in (link.url, link.id, item["title"], item.get("original_title") or "",
                                       *item.get("aliases", ()), *item.get("_names", ()))}
    excluded = next((term for term in intent.excluded_titles if _key(term) in names), "")
    tag_keys = set(map(_key, item.get("tags", ())))
    all_tags = tag_keys | set(map(_key, item.get("_exclusion_tags", ())))
    excluded = excluded or next((term for term in intent.exclusions if all_tags.intersection(map(_key, _terms(term)))), "")
    mismatch, unknown = [], []
    def check(label, passed):
        if passed is not True:
            (unknown if passed is None else mismatch).append(label)

    store, edition = item.get("steam") or {}, item.get("edition") or {}
    if intent.years or intent.year or intent.date_from:
        first = item.get("first_release") or item.get("date")
        label = "首发日期"
        if intent.date_basis == "steam":
            first, label = store.get("release_date"), "Steam 上架日期"
        elif link.kind in {"app", "edition"} and not re.search(r"首发|原作|初版|最早", intent.query):
            first, label = edition.get("released") or store.get("release_date"), "链接对应版本的发行日期"
        check(label + (f"（{first}）" if first else ""), _date_in_scope(first, intent))
    if intent.min_score is not None or intent.max_score is not None:
        source = intent.score_source or link.source
        value = item.get("score") if source == "bangumi" and link.source == "bangumi" else next(
            (record["rating"] for record in item.get("records", ()) if record.get("source") == source
             and record.get("rating") is not None), None)
        check(f"{_LABEL.get(source, '指定来源')} 评分" + (f"（{value:g}）" if value is not None else ""),
              None if value is None else (intent.min_score is None or value >= intent.min_score)
              and (intent.max_score is None or value <= intent.max_score))
    if intent.min_hours is not None or intent.max_hours is not None:
        hours = (item.get("length_minutes") or 0) / 60
        check("游玩时长" + (f"（{hours:g} 小时）" if hours else ""), None if not hours else
              (intent.min_hours is None or hours >= intent.min_hours) and (intent.max_hours is None or hours <= intent.max_hours))
    if intent.language:
        language = LANGUAGE_ALIASES.get(intent.language.casefold(), intent.language)
        requested = LANGUAGES.get(language, ())
        if link.kind == "app":
            actual = _steam_languages(store)
        else:
            actual = set(edition.get("languages", ()))
        check(intent.language, bool(actual.intersection(requested)) if actual and requested else None)
        if intent.official_translation and link.kind != "app":
            check("官方翻译", edition.get("official"))
    if intent.platform:
        requested_platforms = intent.platform.casefold().split()
        if "steam" in requested_platforms:
            check("Steam 商店版本", True if store.get("app_id") else None)
        available = set(edition.get("platforms", ()))
        available.update({"windows": "win", "mac": "mac", "linux": "lin"}.get(p, p) for p in store.get("platforms", ()))
        available.update(PLATFORMS.get(str(item.get("platform") or "").casefold(), ()))
        for platform in requested_platforms:
            if platform != "steam":
                codes = PLATFORMS.get(PLATFORM_ALIASES.get(platform, platform), ())
                check(f"{platform} 平台", bool(available.intersection(codes)) if codes and available else None)
    for term in (*intent.genres, *intent.styles):
        if not tag_keys.intersection(map(_key, _terms(term))):
            unknown.append(f"「{term}」的题材或风格依据")
    if intent.exclusions and not all_tags:
        unknown.append("需排除的题材")
    if intent.task in {"progress", "schedule"} and item.get("subject_domain") == "anime":
        evidence = (item.get("official_broadcast") or {}).get("official_page_evidence") or ()
        if intent.task == "progress" and not any(row.get("explicit_latest") for row in evidence):
            unknown.append("官方平台确认的最新集数")
        elif intent.task == "schedule" and not evidence:
            unknown.append("官方平台排期")
    if "series_order" in intent.fields and not (item.get("series_guide") or {}).get("main_order"):
        unknown.append("已确认的系列主线顺序")
    return excluded, mismatch, unknown


async def lookup_work_links(client, links, *, ymgal=None, region="", fields=(), bangumi_lookup=None,
                            language="", platform="", official_translation=False, today=None, intent=None, timeout=16) -> dict:
    """Query only IDs provided by the user, retaining independent successes and input order."""
    timeout = max(0.01, min(float(timeout), 16))
    lookup = _Lookup(client, timeout * 0.95, False, ymgal)
    country = _COUNTRIES.get(region, region.upper() if re.fullmatch(r"[A-Za-z]{2}", region) else "")

    async def resolve(link):
        if link.source == "steam":
            return await _steam_item(lookup, link, country, fields)
        if link.source == "vndb":
            return await _vndb_item(lookup, link, country, fields, language, platform, official_translation,
                                    today or date.today())
        if link.source == "ymgal":
            if lookup.ymgal_client.configuration_error:
                return None
            detail = await lookup.ymgal_client.detail(link.id, deadline=lookup.deadline, stats={})
            entry = _parse_ymgal(detail["game"], detail, synopsis=True)
            if entry and entry.id == link.id:
                item = _entry_item(entry)
                if "language" in fields and entry.details.get("has_chinese_edition") is True:
                    item["link_notes"].append("月幕档案标注有中文版本；是否属于官方翻译需看具体发行记录。")
                for edition in entry.details.get("editions", [])[:4]:
                    labels = [edition[key] for key in ("title", "released", "language", "platform") if edition.get(key)]
                    if labels:
                        item["link_notes"].append("月幕收录发行 · " + " · ".join(labels))
                return item
        if link.source == "bangumi" and bangumi_lookup:
            directory = await bangumi_lookup(link.url)
            item = next((row for row in directory.get("results", []) if str(row.get("id")) == link.id), None)
            if item:
                return {**item, **{key: directory[key] for key in ("official_broadcast", "series_guide") if key in directory}}
        return None

    async def safely(link):
        try:
            async with asyncio.timeout(timeout):
                return await resolve(link)
        except (httpx.HTTPError, TimeoutError, YmgalError, ValueError, AttributeError, KeyError, TypeError):
            return None

    items, missing, notes = [], [], []
    links = tuple(links)[:4]
    for link, item in zip(links, await asyncio.gather(*(safely(link) for link in links))):
        if item:
            if intent:
                excluded, mismatch, unknown = _check_constraints(item, link, intent)
                if excluded:
                    notes.append(f"已按要求排除与「{excluded}」匹配的链接。")
                    continue
                if mismatch:
                    notes.append(f"《{item['title']}》不符合所问条件 · " + "、".join(mismatch))
                if unknown:
                    notes.append(f"《{item['title']}》还缺少证据核对 · " + "、".join(unknown))
                recommending = intent.task == "recommendation" or re.search(r"推荐|筛选|只列(?:出)?|只推", intent.query)
                if recommending and (mismatch or unknown):
                    continue
            items.append({key: value for key, value in item.items() if not key.startswith("_")})
            items[-1]["direct_url"] = link.url
            if gap := item.get("link_gap"):
                notes.append(f"《{item['title']}》{gap}")
            if missing_fields := _missing_fields(item, fields):
                notes.append(f"《{item['title']}》已定位，尚待核实" + "、".join(missing_fields) + "。")
            if "tags" in fields and item.get("tags") and link.source != "bangumi":
                items[-1].setdefault("link_notes", []).append("题材标签 · " + "、".join(item["tags"][:8]))
            if region and not country and (store := item.get("steam")):
                for key in ("observed_price", "currency", "is_free"):
                    store.pop(key, None)
                notes.append(f"《{item['title']}》的{region}商店信息还需要具体国家或地区。")
        else:
            missing.append(link.url)
            notes.append(f"{_LABEL[link.source]} {link.id} 的确切条目尚未核实。")
    return {"items": items, "unmatched_titles": missing, "filter_notes": notes,
            "status": "insufficient_evidence" if not items else "partial" if notes else "ok"}
