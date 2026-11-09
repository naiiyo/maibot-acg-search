"""Keep work identity, attributable facts, and unverified leads separate."""

from __future__ import annotations

import re
import unicodedata
from itertools import zip_longest
from urllib.parse import urlsplit

from .web_search import valid_source_url


_SOURCES = {
    "bangumi": (r"(?<![A-Za-z0-9])(?:bangumi|bgm(?:\.tv)?)(?![A-Za-z0-9])|番组计划", "bgm.tv", "Bangumi"),
    "vndb": (r"\bvndb\b", "vndb.org", "VNDB"),
    "ymgal": (r"\bymgal\b|月幕", "ymgal.games", "月幕"),
    "erogamescape": (r"\b(?:egs|erogamescape)\b|批评空间", "erogamescape.dyndns.org", "批评空间"),
    "steam": (r"\bsteam(?:community|powered)?\b", "steampowered.com", "Steam"),
    "cngal": (r"\bcngal\b", "cngal.org", "CnGal"),
}
_FIELDS = {
    "summary": "作品简介", "tags": "题材标签", "release": "发行或播出日期", "score": "评分",
    "reviews": "玩家评论或评测", "duration": "游玩时长", "language": "支持语言",
    "store": "商店条目", "price": "价格", "staff": "制作人员", "characters": "角色", "relations": "关联作品",
}
_DEFAULT_FIELDS = ("summary", "tags", "release", "score")
_PRICE = r"价格|预算|多少钱|折扣|[0-9]\s*(?:元|块)"
_STORE = r"商店|上架|下架|购买|在售|买得到|在哪买|\bSteam\s*版"
_FIELD_CUES = (
    (r"简介|介绍|梗概|剧情|故事", ("summary",)), (r"题材|标签|类型", ("tags",)),
    (r"发售|发行|首发|上市|播出|放送|日期", ("release",)), (r"评分|分数|分值", ("score",)),
    (r"评价|口碑|好评", ("score", "reviews")), (r"评测|评论|观点", ("reviews",)),
    (r"时长|多久|几小时", ("duration",)), (r"语言|官中|中文|日文|英文|汉化", ("language",)),
    (_STORE, ("store",)), (_PRICE, ("price",)), (r"声优|配音|制作|画师|编剧", ("staff",)),
    (r"角色|人物", ("characters",)), (r"关联|系列|前作|续作", ("relations",)),
)
_MEDIA = {"galgame": "Galgame", "anime": "动画", "manga": "漫画", "acg_game": "二次元游戏",
          "game": "游戏", "book": "书籍", "music": "音乐", "real": "真人作品"}
_MAX_GAP_QUERIES = 6
_MAX_QUERY_CHARS = 128
_SOURCE_ALIASES = {"bangumi": ("bangumi.tv", "chii.in"), "steam": ("steamcommunity.com",)}


def comparison_fields(intent, title=None) -> tuple[str, ...]:
    query = _without_titles(intent)
    fields = tuple(field for field in intent.fields if field in _FIELDS
                   and (field != "store" or re.search(_STORE, query, re.IGNORECASE)))
    if re.search(_PRICE, query) and "price" not in fields:
        fields += ("price",)
    fields = fields or _DEFAULT_FIELDS
    if title is not None:
        work_fields = _source_scopes(intent)[3].get(title)
        if work_fields is not None:
            return tuple(field for field in fields if field in work_fields)
    return fields


def _without_titles(intent) -> str:
    query = intent.query
    for title in sorted(intent.titles, key=len, reverse=True):
        query = re.sub(re.escape(title), lambda match: " " * len(match[0]), query, flags=re.IGNORECASE)
    return query


def _source_scopes(intent) -> tuple[tuple[str, ...], dict, dict, dict]:
    query = _without_titles(intent)
    mentions = sorted((match.start(), match.end(), source) for source, (pattern, _, _) in _SOURCES.items()
                      for match in re.finditer(pattern, query, re.IGNORECASE))
    fields = comparison_fields(intent)
    cues = [(match.start(), match.end(), names) for pattern, names in _FIELD_CUES
            for match in re.finditer(pattern, query, re.IGNORECASE) if set(names).intersection(fields)]
    scopes = {field: [] for field in fields}
    work_scopes = {}
    breaks = (0, *(match.end() for match in re.finditer(r"[，,。；;！!？?]", query)), len(query) + 1)
    clauses = []
    work_fields = {}
    shared_fields = set()
    for left, right in zip(breaks, breaks[1:]):
        clause = intent.query[left:right]
        titles = [title for title in intent.titles if re.search(re.escape(title), clause, re.IGNORECASE)]
        requested = {field for start, _, names in cues if left <= start < right for field in names if field in fields}
        title = titles[0] if len(titles) == 1 else None
        clauses.append((left, right, title, requested))
        if title and requested:
            work_fields.setdefault(title, set()).update(requested)
        else:
            shared_fields.update(requested)
    for start, stop, source in mentions:
        clause_start, clause_end, title, requested = next(clause for clause in clauses if clause[0] <= start < clause[1])
        targets = fields
        candidates = []
        if not re.search(r"(?:只|仅|限定)(?:查|看|使用|用|参考|按|以|在|限)*\s*$", query[clause_start:start]):
            for left, right, names in cues:
                if not clause_start <= left < clause_end:
                    continue
                if left >= stop:
                    distance, bridge = left - stop, query[stop:left]
                elif right <= start:
                    distance, bridge = start - right + 2, query[right:start]
                else:
                    continue
                if distance <= 22 and not re.search(r"[，,。；;！!？?]", bridge):
                    candidates.append((distance, left, names))
            if candidates:
                targets = min(candidates)[2]
            elif source == "steam" and "store" in fields:
                targets = ("store",)
        destination = work_scopes.setdefault(title, {}) if title and requested else scopes
        for field in targets:
            if field in fields and source not in (assigned := destination.setdefault(field, [])):
                assigned.append(source)
    if set(work_fields) == set(intent.titles):
        unassigned = set(fields).difference(shared_fields, *work_fields.values())
        work_fields = {title: requested | shared_fields | unassigned for title, requested in work_fields.items()}
    else:
        work_fields = {}
    return tuple(dict.fromkeys(source for _, _, source in mentions)), scopes, work_scopes, work_fields


def comparison_sources(intent, field=None, title=None) -> tuple[str, ...]:
    sources, scopes, work_scopes, _ = _source_scopes(intent)
    if field is None:
        return sources
    return tuple(work_scopes.get(title, {}).get(field, scopes.get(field, ())))


def matches_comparison_source(url, source) -> bool:
    if source not in _SOURCES or not valid_source_url(url):
        return False
    host = urlsplit(url).hostname or ""
    return any(host == domain or host.endswith("." + domain)
               for domain in (_SOURCES[source][1], *_SOURCE_ALIASES.get(source, ())))


def _key(value) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def _text(value, limit: int = 180) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _names(rows, limit: int = 4) -> str:
    names = [_text(row.get("name") or row.get("actor") if isinstance(row, dict) else row, 50) for row in rows or ()]
    return "、".join(tuple(dict.fromkeys(name for name in names if name))[:limit])


def _same_name(title, item) -> bool:
    key = _key(title)
    return bool(key and any(_key(name) == key for name in (
        item.get("title"), item.get("original_title"), item.get("requested_title"),
        *item.get("names", ()), *item.get("aliases", ()),
    )))


def _candidate(medium, item) -> dict:
    records = item.get("records") or ()
    record = records[0] if records else {}
    return {
        "title": _text(item.get("title"), 100),
        "medium": item.get("subject_domain") or medium,
        "date": item.get("first_release") or item.get("date") or "",
        "platform": _text(item.get("platform"), 60),
        "url": item.get("url") or record.get("url") or "",
    }


def _facts(medium, item, context) -> list[dict]:
    facts = []

    def add(field, source, value, url):
        if value := _text(value):
            if valid_source_url(url) and source in _SOURCES:
                facts.append({"field": field, "source": source, "value": value, "url": url})

    def score(value, scale, votes):
        if type(value) not in (int, float) or type(scale) not in (int, float):
            return ""
        return f"{value:g}/{scale:g}" + (f"（{votes:g} 人评价）" if type(votes) in (int, float) and votes > 0 else "")

    def details(source, row, url):
        add("summary", source, row.get("description") or row.get("summary"), url)
        add("tags", source, _names(row.get("tags")), url)
        add("staff", source, _names(row.get("people") or row.get("staff") or row.get("voice_cast")), url)
        add("characters", source, _names(row.get("characters")), url)
        add("relations", source, _names(row.get("relations")), url)

    if medium == "cngal":
        url = item.get("url")
        details("cngal", item, url)
        for review in item.get("reviews", ())[:2]:
            if review.get("summary"):
                add("reviews", "cngal", "社区评测文章摘要 · " + review["summary"], review.get("url"))
        return facts

    if medium != "galgame":
        url = item.get("url")
        details("bangumi", item, url)
        add("score", "bangumi", score(item.get("score"), 10, item.get("votes")), url)
        if item.get("date"):
            add("release", "bangumi", "目录日期 " + item["date"], url)
        return facts

    records = item.get("records") or ()
    for record in records:
        source, url = record.get("source"), record.get("url")
        details(source, record, url)
        add("score", source, score(record.get("rating"), record.get("rating_scale"), record.get("votes")), url)
        if record.get("released") and context["date_basis"] != "steam":
            label = "首发依据 " if source == item.get("first_release_source") else "目录发行日期 "
            add("release", source, label + record["released"], url)
        if source == "vndb" and item.get("length_minutes"):
            add("duration", source, f"约 {item['length_minutes'] / 60:g} 小时", url)
        if source == "vndb" and not context["official_translation"] and (languages := _names(record.get("work_languages"))):
            add("language", source, f"作品目录语言 · {languages}", url)
        if source == "ymgal" and not context["official_translation"] and record.get("has_chinese_edition") is True:
            add("language", source, "目录标注有中文版本", url)
    edition = item.get("edition") or {}
    languages = _names(edition.get("languages"))
    if languages:
        kind = "官方发行版本" if edition.get("official") else "发行版本"
        add("language", "vndb", f"{kind} · {languages}", edition.get("url"))
    steam = item.get("steam") or {}
    if not steam.get("status", "").startswith("verified_"):
        return facts
    url = steam.get("url")
    details("steam", steam, url)
    country = steam.get("country") or ""
    price = steam.get("observed_price") or ("免费" if steam.get("is_free") else "")
    listing = "已上架" if steam["status"] == "verified_released_listing" else "商店条目"
    if price:
        listing = " · ".join(filter(None, (listing, country, price)))
    add("store", "steam", listing, url)
    if price and (not context["region"] or country):
        add("price", "steam", " · ".join(filter(None, (country, price))), url)
    store_date = steam.get("release_date") or steam.get("date")
    if store_date and (context["date_basis"] == "steam" or "steam" in context["release_sources"]):
        add("release", "steam", "商店日期 " + store_date, url)
    add("language", "steam", steam.get("languages"), url)
    summary = steam.get("review_summary") or {}
    review_url = steam.get("review_url") or url
    if summary.get("reviews"):
        add("score", "steam", f"{summary.get('positive', 0)} 条好评／{summary['reviews']} 条评价", review_url)
    for sample in steam.get("review_samples", ())[:2]:
        if sample.get("text"):
            add("reviews", "steam", "玩家评论样本 · " + sample["text"], review_url)
    return facts


def build_comparison(intent, report) -> dict:
    fields = comparison_fields(intent)
    sources, shared_scopes, work_scopes, work_fields = _source_scopes(intent)
    context = {"region": intent.region, "platform": intent.platform, "language": intent.language,
               "official_translation": intent.official_translation, "date_basis": intent.date_basis,
               "years": list(intent.years or ((intent.year,) if intent.year else ())), "date_from": intent.date_from}
    constrained = bool(intent.years or intent.year or intent.date_from or intent.date_to or intent.language or intent.platform
                       or intent.region or intent.official_translation or intent.excluded_titles or intent.exclusions
                       or any(value is not None for value in (intent.min_score, intent.max_score, intent.min_hours, intent.max_hours)))
    directories = {intent.medium: report.get("catalogue") or {}, **report.get("related_catalogues", {})}
    works = []
    for title in intent.titles:
        scopes = {**shared_scopes, **work_scopes.get(title, {})}
        requested_fields = tuple(field for field in fields if title not in work_fields or field in work_fields[title])
        matches = []
        candidates = {}
        ambiguous = False
        for medium, directory in directories.items():
            ids = directory.get("matches_by_title", {}).get(title) or ()
            ambiguous |= title in directory.get("ambiguous_entities", ()) or any(
                isinstance(row, dict) and row.get("title") == title and row.get("reason") == "ambiguous"
                for row in directory.get("unmatched_titles", ())
            )
            for candidate in directory.get("ambiguous_candidates", {}).get(title, ()):
                item = _candidate(medium, candidate)
                candidates[item["url"] or item["title"]] = item
            for row in directory.get("items") or directory.get("results") or ():
                if row.get("id") in ids or _same_name(title, row):
                    item = _candidate(medium, row)
                    key = item["url"] or (medium, item["title"], item["date"])
                    if key not in candidates:
                        matches.append((medium, row))
                        candidates[key] = item
        supplements = [row for row in report.get("cngal", ()) if _same_name(title, row)]
        standalone = not candidates and bool(supplements)
        if not candidates:
            for row in supplements:
                item = _candidate("galgame", row)
                candidates[item["url"] or item["title"]] = item
        status = "unverified"
        if ambiguous or len(candidates) > 1:
            status = "ambiguous"
        elif candidates and not (standalone and constrained):
            status = "matched"
        facts = []
        if status == "matched":
            work_context = {**context, "release_sources": scopes.get("release", [])}
            for medium, row in matches:
                facts.extend(_facts(medium, row, work_context))
            if next(iter(candidates.values()))["medium"] == "galgame":
                for row in supplements:
                    if not matches or any(_same_name(row["title"], match) for medium, match in matches if medium == "galgame"):
                        facts.extend(_facts("cngal", row, work_context))
        facts = [fact for fact in facts if fact["field"] in requested_fields]
        requested_facts = [fact for fact in facts if not scopes[fact["field"]] or fact["source"] in scopes[fact["field"]]]
        coverage = {(fact["field"], fact["source"] if scopes[fact["field"]] else "") for fact in requested_facts}
        gaps = [{"field": field, "source": source} for field in requested_fields for source in (scopes[field] or ("",))
                if (field, source) not in coverage]
        works.append({"requested_title": title, "status": status, "candidates": list(candidates.values())[:4],
                      "fields": list(requested_fields), "facts": requested_facts,
                      "supplemental_facts": [fact for fact in facts if fact not in requested_facts],
                      "gaps": gaps})
    anchors = [work["candidates"][0]["url"] for work in works if work["status"] == "matched"]
    same_work = all(anchors) and len(anchors) != len(set(anchors))
    complete = (len(works) >= 2 and len(anchors) == len(works) and not same_work
                and not any(work["gaps"] for work in works))
    return {"fields": list(fields), "sources": list(sources), "works": works, "context": context,
            "complete": complete, "same_work": same_work}


def comparison_queries(comparison) -> tuple[tuple[str, str, tuple[str, ...], str], ...]:
    """Query the missing fields of each identifiable work, respecting source scope."""
    queries = []
    context = comparison["context"]
    for source in ("", *comparison["sources"]):
        for work in comparison["works"]:
            if work["status"] == "ambiguous":
                continue
            fields = tuple(gap["field"] for gap in work["gaps"] if gap["source"] == source)
            if not fields:
                continue
            title = work["requested_title"]
            candidate = next(iter(work["candidates"]), {})
            name = _text(candidate.get("title") or title, 80).replace('"', '')
            scope = []
            if {"store", "price", "language", "release"}.intersection(fields):
                scope += [context["region"], context["platform"]]
            if "language" in fields:
                scope += ["官方" if context["official_translation"] else "", context["language"]]
            if source:
                domain = ("steamcommunity.com" if source == "steam" and "reviews" in fields
                          and set(fields) <= {"reviews", "score"} else _SOURCES[source][1])
                scope.insert(0, f"site:{domain}")
            years = context["years"]
            date_term = (" ".join(f"{year}年" for year in years[:3]) if years else context["date_from"]
                         or candidate.get("date", "")[:4])
            suffix = tuple(filter(None, (*scope, _MEDIA.get(candidate.get("medium"), ""), date_term)))
            parts = [f'"{name}"']
            for term in suffix:
                if len(" ".join((*parts, term))) <= _MAX_QUERY_CHARS:
                    parts.append(term)
            included_fields = []
            for field in fields:
                if len(" ".join((*parts, _FIELDS[field]))) <= _MAX_QUERY_CHARS:
                    parts.append(_FIELDS[field])
                    included_fields.append(field)
            if parts[1:2] and parts[1].startswith("site:"):
                parts[0], parts[1] = parts[1], parts[0]
            queries.append((" ".join(parts), title, tuple(included_fields), source))
    return tuple(queries[:_MAX_GAP_QUERIES])


def comparison_lines(comparison, *, for_tool=False) -> list[str]:
    """Prioritize each work and its gaps before the detailed evidence lines."""
    works = comparison.get("works", ())
    lines = ["对比项 · " + "、".join(_FIELDS[field] for field in comparison.get("fields", ()))]
    if len(works) < 2:
        lines.append("请给出至少两个可辨认的作品名。")
    if comparison.get("same_work"):
        lines.append("部分输入名称指向同一条目。需要对比不同版本时，请补充版本或平台。")
    for work in works:
        title = _text(work["requested_title"], 80)
        candidates = work["candidates"]
        if work["status"] == "ambiguous":
            lines.append(f"{title} · 存在多个可能条目，请确认年份、平台或条目链接。")
            for candidate in candidates[:3]:
                label = " · ".join(filter(None, (candidate["title"], _MEDIA.get(candidate["medium"], ""),
                                                  candidate["date"], candidate["platform"])))
                url = candidate["url"]
                lines.append(f"候选 · {label} {url}" if valid_source_url(url) else f"候选 · {label}")
            continue
        if work["status"] == "unverified":
            message = "已有条目线索，指定条件仍需核实。" if candidates else "作品身份待确认。"
            lines.append(f"{title} · {message}")
            continue
        if work["gaps"]:
            missing = []
            for source in ("", *comparison["sources"]):
                fields = [_FIELDS[gap["field"]] for gap in work["gaps"] if gap["source"] == source]
                if fields:
                    name = _SOURCES[source][2] + " " if source else ""
                    missing.append(name + "、".join(fields))
            lines.append(f"{title} · 待核实 " + " / ".join(missing))
    detail_lines = []
    for work in works:
        title = _text(work["requested_title"], 80)
        # Give each comparison field a first pass before adding alternate platforms.
        first = {field: next((fact for fact in work["facts"] if fact["field"] == field), None)
                 for field in comparison["fields"]}
        prioritized = [fact for fact in first.values() if fact]
        prioritized.extend(fact for fact in work["facts"] if fact not in prioritized)
        if not for_tool:
            prioritized.extend(work["supplemental_facts"])
        details = []
        for fact in prioritized[:5 if for_tool else 7]:
            source = _SOURCES[fact["source"]][2]
            value = _text(fact["value"], 100 if for_tool else 160)
            scope = " · 其他来源参考" if fact in work["supplemental_facts"] else ""
            details.append(f"{title} · {_FIELDS[fact['field']]}{scope} · {source} · {value} {fact['url']}")
        detail_lines.append(details)
    lines.extend(line for group in zip_longest(*detail_lines) for line in group if line)
    return lines
