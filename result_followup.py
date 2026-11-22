"""Bind ordinal questions to the catalogue works actually presented to one sender."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from .intent import SearchIntent, classify
from .work_links import extract_work_links


_MEDIA = {"galgame": "Galgame", "anime": "动画", "manga": "漫画", "acg_game": "二次元游戏",
          "game": "游戏", "bangumi": "Bangumi作品"}
_NUMBER = r"(?:\d{1,2}|[一二两三四五六七八九十]{1,3})"
_UNIT = r"[部款本个项作名]"
_REFERENCE = re.compile(
    rf"第\s*(?P<sequence>{_NUMBER}(?:\s*(?:、|和|与|跟|到|至)\s*第?\s*{_NUMBER})+)\s*{_UNIT}"
    rf"|第\s*(?P<ordinal>{_NUMBER})\s*(?:{_UNIT}|(?=[、和与跟]))"
    rf"|(?P<edge>[前后])\s*(?P<count>{_NUMBER})\s*{_UNIT}"
    rf"|(?P<last>最后(?:一)?\s*{_UNIT})"
)
_LEAD = re.compile(r"(?:(?:好的?|那(?:么)?|刚才|刚刚|上(?:一)?[轮次]|上面|之前|你|给我|帮我|请|再|把|将|其中|列表|推荐[的了]?|说的|提到的|的|"
                   r"介绍(?:一下|下)?|对比(?:一下|下)?|比较(?:一下|下)?|比一下|换(?:掉|一下|下)?|看看|查(?:一下|下)?|关于|我想(?:问|了解)|"
                   r"[,，。\s]))*")
_REPLACE = re.compile(r"(?:换掉|换一下|替换|换下|换(?:一?个|一?款|一?部)?(?:别的|新的|其他的)|(?:^|把|请|帮我|给我)换第|换一[部款个本])")


@dataclass(frozen=True)
class Work:
    title: str
    names: tuple[str, ...]
    url: str
    vndb_id: str


@dataclass(frozen=True)
class PresentedList:
    intent: SearchIntent
    works: tuple[Work, ...]


@dataclass(frozen=True)
class ResultQuestion:
    intent: SearchIntent
    clarification: str = ""


def _key(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def presented_list(report: dict, intent: SearchIntent) -> PresentedList | None:
    if report.get("medium") not in _MEDIA:
        return None
    directory = report.get("catalogue") or {}
    rows = directory.get("items") or directory.get("results") or []
    if comparison := report.get("comparison"):
        matches = comparison.get("works", ())
        if not all(w.get("status") == "matched" and len(w.get("candidates", ())) == 1 for w in matches):
            return PresentedList(intent, ())
        rows = [dict(w["candidates"][0], names=(w["requested_title"],)) for w in matches]
    elif report.get("task") != "recommendation" and intent.reason != "work_link":
        return None
    works = []
    for row in rows[:10]:
        title = row.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        names = (title, row.get("original_title"), *row.get("names", ()), *row.get("aliases", ()))
        records = row.get("records", ())
        vndb = next((r for r in records if r.get("source") == "vndb"), {})
        candidates = [row.get("direct_url"), row.get("url"), vndb.get("url"), *(r.get("url") for r in records),
                      (row.get("steam") or {}).get("url")]
        url = next((links[0].url for url in candidates if isinstance(url, str) and (links := extract_work_links(url))), "")
        names = tuple(dict.fromkeys(n.strip() for n in names if isinstance(n, str) and n.strip()))
        works.append(Work(title.strip(), names, url, str(vndb.get("id") or "")))
    return PresentedList(intent, tuple(works))


def in_reply_order(result: PresentedList, response: str) -> PresentedList:
    """Prefer the user's visible subset/order; avoid matching a base title inside its sequel."""
    reply = _key(response)
    hits = {}
    for index, work in enumerate(result.works):
        for name in {*work.names, work.url}:
            needle = _key(name)
            if not needle or len(needle) == 1 and f"《{name}》" not in response:
                continue
            for match in re.finditer(re.escape(needle), reply):
                hits.setdefault((match.start(), -len(needle)), set()).add(index)
    ordered, end = [], -1
    for start, neg_length in sorted(hits):
        if start < end:
            continue
        indices = hits[start, neg_length]
        if len(indices) != 1:
            # A shared alias alone cannot identify which work this sentence presented.
            continue
        index = next(iter(indices))
        end = start - neg_length
        if index not in ordered:
            ordered.append(index)
    return replace(result, works=tuple(result.works[i] for i in ordered))


def _number(text):
    if text.isdigit():
        return int(text)
    digits = "零一二三四五六七八九"
    text = text.replace("两", "二")
    if "十" in text:
        high, low = text.split("十", 1)
        if (high and (len(high) != 1 or high not in digits)) or (low and (len(low) != 1 or low not in digits)):
            return 0
        return (digits.index(high) if high else 1) * 10 + (digits.index(low) if low else 0)
    return digits.index(text) if len(text) == 1 else 0


def _positions(match, count):
    if match["last"]:
        return [count]
    if sequence := match["sequence"]:
        numbers = [_number(s) for s in re.findall(_NUMBER, sequence)]
        if any(n < 1 or n > count for n in numbers):
            return []
        if re.search(r"[到至]", sequence):
            return list(range(numbers[0], numbers[1] + 1)) if len(numbers) == 2 else []
        return numbers
    n = _number(match["ordinal"] or match["count"])
    if n < 1 or n > count:
        return []
    if match["ordinal"]:
        return [n]
    return list(range(1, n + 1)) if match["edge"] == "前" else list(range(count - n + 1, count + 1))


def resolve_result_question(text: str, result: PresentedList | None, settings) -> ResultQuestion | None:
    text = unicodedata.normalize("NFKC", text).strip()
    matches = list(_REFERENCE.finditer(text))
    if not matches or len(text) > 180 or not _LEAD.fullmatch(text[:matches[0].start()]):
        return None
    current = classify(text, **settings)
    if current.reason in {"no_web", "transform_or_creative"}:
        return None
    if re.search(r"《[^》]+》|https?://", text, re.I):
        # An explicitly named other subject can be a separate comparison. Let the normal router handle it.
        return None
    works = result.works if result else ()
    if not works:
        return ResultQuestion(current, "这轮没有可按序号定位的推荐列表，请给作品名或目录链接。")

    selected, phrases = [], {}
    for match in matches:
        positions = _positions(match, len(works))
        if not positions:
            return ResultQuestion(current, f"上次推荐中能定位到 {len(works)} 部作品。请用这个范围内的序号，或直接给作品名。")
        selected.extend(works[i - 1] for i in positions if works[i - 1] not in selected)
        phrases[match.start()] = "、".join(f"《{works[i - 1].title}》" for i in positions)
    original = result.intent
    if _REPLACE.search(text):
        if original.task != "recommendation":
            return ResultQuestion(current, "这份列表来自指定作品查询。想换一部推荐的话，请告诉我想找的作品类型或条件。")
        # Return only the requested number of new candidates and never repeat another displayed work.
        extra = classify(f"推荐{_MEDIA[original.medium]}，{text}", **settings)
        excluded = tuple(dict.fromkeys((*original.excluded_titles, *extra.excluded_titles, *(w.title for w in works),
                                         *(w.vndb_id for w in works if w.vndb_id))))
        query = f"{original.query}。{text}"
        if len(query) > 600:
            return ResultQuestion(current, "原筛选条件和待替换的作品名较长，请缩短条件后再试。")
        changes = {k: getattr(extra, k) for k in ("platform", "region", "language") if getattr(extra, k)}
        if extra.language:
            changes["official_translation"] = extra.official_translation
        if extra.date_from:
            changes.update({k: getattr(extra, k) for k in ("date_from", "date_to", "year", "years", "month", "date_basis")})
        for field in ("min_hours", "max_hours", "min_score", "max_score"):
            if getattr(extra, field) is not None:
                changes[field] = getattr(extra, field)
        for field in ("genres", "styles", "exclusions"):
            changes[field] = tuple(dict.fromkeys((*getattr(original, field), *getattr(extra, field))))
        intent = replace(original, query=query, count=len(selected), excluded_titles=excluded, web_queries=(), **changes)
        return ResultQuestion(intent)
    titles = tuple(w.title for w in selected)
    if len(set(titles)) != len(titles) or len(titles) > 4:
        return ResultQuestion(current, "一次最多比较四部能分别识别的作品。请指定作品名或目录链接。")
    expanded = _REFERENCE.sub(lambda match: phrases[match.start()], text)
    query = f"查询{_MEDIA[original.medium]}，{expanded}"
    intent = classify(query, **settings)
    # A unique catalogue ID keeps identically named anime seasons distinct. Named comparisons
    # retain their human-readable entities so the existing per-work field matching still applies.
    if len(titles) == 1:
        work = selected[0]
        if original.medium != "galgame" and re.fullmatch(r"https://bgm\.tv/subject/[1-9]\d{0,9}", work.url):
            titles = (work.url,)
        elif work.url:
            query = f"{query} {work.url}"
            intent = classify(query, **settings)
            return ResultQuestion(replace(intent, query=query, spoiler_policy=original.spoiler_policy
                                          if intent.spoiler_policy == "unspecified" else intent.spoiler_policy))
        elif sum(w.title == work.title for w in works) > 1:
            return ResultQuestion(current, "列表中有同名作品，请补充具体的目录链接。")
    intent = replace(intent, query=query, route="catalogue", medium=original.medium, titles=titles,
                     task="comparison" if len(titles) > 1 else intent.task,
                     spoiler_policy=original.spoiler_policy if intent.spoiler_policy == "unspecified" else intent.spoiler_policy)
    return ResultQuestion(intent)
