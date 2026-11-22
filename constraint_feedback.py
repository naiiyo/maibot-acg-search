"""Explain the limits of an empty catalogue sample and handle explicit retries."""

import re
from dataclasses import replace

from .intent import _clean, _constraints_text, _separate_exclusions


_REASONS = {
    "missing_galgame_title": "这项查询需要一个可辨认的作品名或条目链接。",
    "historical_popularity_snapshot_unavailable": "目录提供当前累计热度，缺少所问年份当时的榜单快照。",
    "unsupported_language_constraint": "当前目录不能核对指定语言。",
    "unsupported_platform_constraint": "当前目录不能核对指定平台组合。",
    "regional_availability_not_verified": "缺少指定商店地区的发行记录。",
    "release_date_constraint_has_no_dates": "所选日期与已发售或待发售条件没有重合。",
    "invalid_release_date_range": "需要明确的起止日期。",
    "invalid_score_range": "评分范围超出所选平台的分制，或上下限冲突。",
    "invalid_playtime_range": "游玩时长范围无效，请检查上下限。",
    "similarity_basis_not_verified": "参考作品名未能唯一匹配，暂时无法比较相似度。",
    "similarity_tags_unavailable": "参考作品缺少可供比对的题材标签。",
}


def explain_empty(directory, intent):
    if directory.get("items") or directory.get("results") or directory.get("news"):
        return
    if not directory.get("filter_notes"):
        reason = _REASONS.get(directory.get("reason"))
        directory["filter_notes"] = [reason or "本轮没有取得可逐项核对的目录条目，无法归因到某一个筛选条件。"]
    if intent.task == "recommendation" and (limits := active_limits(intent)):
        example = limits[0]
        directory["filter_notes"].append(f"想扩大范围，可以回复「放宽{example}」后重查。")


def active_limits(intent):
    whole_years = intent.years and intent.date_from == f"{min(intent.years)}-01-01" and intent.date_to == f"{max(intent.years)+1}-01-01"
    return [label for label, active in (
        ("时长", intent.min_hours is not None or intent.max_hours is not None),
        ("评分", intent.min_score is not None or intent.max_score is not None),
        ("语言", bool(intent.language)), ("年份" if whole_years else "日期范围", bool(intent.years or intent.date_from)),
        ("平台", bool(intent.platform) and not intent.region and intent.date_basis != "steam"),
    ) if active]


_RETRY = re.compile(
    r"^(?:那(?:就)?|好[的吧]?[,，]?|请|再|可以|帮我|就|只|先|把|将|麻烦|\s)*"
    r"(?:(?:不再限制|不限|取消|去掉|放宽|忽略)\s*(年份|日期范围|日期|时长|评分|分数|语言|平台)(?:的?限制|条件|要求)?"
    r"|(年份|日期范围|日期|时长|评分|分数|语言|平台)(?:的?限制|条件|要求)?\s*(?:放宽|不限|不限制|取消|不要了))"
    r"(?:吧|即可|就行|一下|[,， ]*(?:其他(?:条件)?(?:不变|照旧)|再(?:查|搜|推荐)(?:一下)?))*[。.!！?？ ]*$"
)
_N = r"(?:\d+(?:\.\d+)?|半|[一二两三四五六七八九十]+)"
_UNIT = r"(?:个?小时|钟头|分钟|h(?![a-z0-9]))"
_BOUND = r"(?:至少|不少于|不低于|不超过|最多|至多|不多于|不高于|超过|长于|高于|少于|低于|短于|大于|小于|>=|<=|≥|≤)"
_END = r"(?:以上|起步|起|之内|以内|以下|内|\+)"
_STRIP = {
    "时长": rf"(?:游玩|游戏|流程|通关)?(?:时长)?\s*(?:{_BOUND}\s*)?{_N}\s*(?:{_UNIT}?\s*(?:到|至|[-~—])\s*{_N}\s*)?{_UNIT}\s*{_END}?",
    "评分": rf"(?:评分|分数)?\s*(?:{_BOUND}\s*)?\d+(?:\.\d+)?\s*(?:分?\s*(?:到|至|[-~—])\s*\d+(?:\.\d+)?\s*)?(?:分(?!钟|鐘)\s*{_END}?|\+)",
    "语言": r"(?:必须|需要|支持|有)?(?:官方(?:简体中文|繁体中文|中文|简中|繁中|汉化)|简体中文|繁体中文|简中|繁中|官中|中文|汉化|日文|日语|生肉|英文|英语)",
    "平台": r"(?<![A-Za-z])(?:Steam|Windows|PC|macOS|Linux|Switch\s*2?|PS[45]|Xbox|Android|iOS)(?![A-Za-z])|电脑[端版上]|任天堂主机|安卓|手机[端版上]",
    "年份": r"(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}|(?:19|20)\d{2}\s*年?\s*(?:到|至|[-~—])\s*(?:19|20)\d{2}\s*年"
            r"|(?:(?:19|20)?\d{2}\s*(?:年|(?=Q[1-4]))|前年|去年|今年|明年|后年)"
            r"|第?\s*[一二三四1-4]\s*季度|Q\s*[1-4]|\d{1,2}\s*月(?:\s*\d{1,2}\s*[日号])?|[春夏秋冬](?:季|(?=番))"
            r"|(?:最近|近|过去)\s*\d+\s*天|(?:上|本|这(?:个)?|下)(?:周|星期|月)|昨日|今日|明日|昨天|今天|明天",
}


def relax_after_empty(intent, current_text, report):
    if not intent or intent.task != "recommendation" or not report or not isinstance(current_text, str):
        return None
    directory = report.get("catalogue")
    if not directory or any(directory.get(k) for k in ("items", "results", "news")):
        return None
    match = _RETRY.fullmatch(_clean(current_text))
    if not match:
        return None
    field = next(value for value in match.groups() if value)
    field = {"日期": "日期范围", "分数": "评分"}.get(field, field)
    limits = active_limits(intent)
    if field not in limits and not (field == "日期范围" and "年份" in limits):
        return None
    if field == "日期范围":
        field = "年份"
    text = _clean(intent.query)
    positive, _ = _separate_exclusions(text, None)
    names = (*intent.titles, *intent.excluded_titles, intent.similar_to)
    protected = [(m.start(), m.end(), word, "") for word in names if word
                 for m in re.finditer(re.escape(word), positive, re.I)]
    mask = _constraints_text(positive, protected)
    spans = list(re.finditer(_STRIP[field], mask, re.I))
    if field == "评分":
        spans = [m for m in spans if not (
            re.search(r"(?:人数|票数|评价数|投票数)\s*$", mask[:m.start()])
            or re.match(r"\s*(?:个?人|票|条评)", mask[m.end():])
        )]
    if not spans:
        return None
    for span in reversed(spans):
        text = text[:span.start()] + " " + text[span.end():]
    changes = {
        "时长": dict(min_hours=None, max_hours=None),
        "评分": dict(min_score=None, max_score=None),
        "语言": dict(language="", official_translation=False),
        "年份": dict(year=None, years=(), month=None, date_from="", date_to=""),
        "平台": dict(platform=""),
    }
    return replace(intent, query=" ".join(text.split()), web_queries=(), search_terms=(), broadcast_query="",
                   **changes[field])
