"""Classify a user request without making network or model calls."""

from __future__ import annotations

import calendar
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class SearchIntent:
    query: str
    medium: str = "other"
    task: str = "fact"
    route: str = "skip"
    titles: tuple[str, ...] = ()
    year: int | None = None
    month: int | None = None
    years: tuple[int, ...] = ()
    date_from: str = ""
    date_to: str = ""
    region: str = ""
    platform: str = ""
    language: str = ""
    count: int | None = None
    genres: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    spoiler_policy: str = "unspecified"
    ranking: str = ""
    min_score: float | None = None
    max_score: float | None = None
    score_source: str = ""
    explicit: bool = False
    reason: str = ""
    min_hours: float | None = None
    max_hours: float | None = None
    official_translation: bool = False
    similar_to: str = ""
    fields: tuple[str, ...] = ()
    related_media: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    excluded_titles: tuple[str, ...] = ()
    date_basis: str = "first_release"
    recognition: str = "rules"
    search_terms: tuple[str, ...] = ()
    search_tags: tuple[str, ...] = ()
    web_queries: tuple[str, ...] = ()
    broadcast_query: str = ""


_GALGAME_KIND = (r"(?<![A-Za-z0-9])(?:gal\s*game|gal|visual novel)(?![A-Za-z0-9])"
                 r"|旮[旯旮](?:\s*(?:游戏|game|给[木姆]|盖姆))?|(?:嘎[啦拉]|尬拉)\s*(?:给[木姆]|盖姆|game|游戏)"
                 r"|视觉小说|美少女游戏|文字冒险")
_GALGAME = (_GALGAME_KIND + r"|(?<![A-Za-z0-9])(?:vndb|egs|erogamescape)(?![A-Za-z0-9])"
            r"|批评空间|月幕")
_GALGAME_REFERENCE = rf"(?:这|那)(?:一)?[部款个]\s*(?:{_GALGAME_KIND})"
_ANIME = r"番剧|动画|动漫|新番|补番|追番|日番|国番|泡面番|(?:[一几新]部|科幻|恋爱|日常|搞笑|异世界|百合|悬疑|机战|校园|治愈|热门|人气)番|(?<![A-Za-z0-9])(?:anime|donghua)(?![A-Za-z0-9])"
_MANGA = r"漫画|条漫|单行本|(?<![A-Za-z0-9])manga(?![A-Za-z0-9])"
_ACG_GAME = r"二次元游戏|二游|手游二次元|抽卡游戏"
_GAME = r"游戏(?!本|手机|手柄)|端游|手游|主机游戏|配队|卡池|全结局|怎么通关"
_GENERAL_ACG = r"二次元|(?<![A-Za-z0-9])acg(?![A-Za-z0-9])"
_GAME_UPDATE = r"卡池|复刻|前瞻|维护|更新进度|更新公告|活动|版本|开服|更新到|什么时候更新"
_ACG_UPDATE_CUE = r"卡池|复刻|开服"
_MANGA_PROGRESS = r"连载|话数|刊载|出到第|更新到第"
_DURATION = r"多久(?:能|可以)?(?:玩完|通关|打完)|(?:通关|流程|游玩|全收集|全路线).{0,8}(?:多久|多长|时长|几小时|几个小时)|游玩时长|通关时长"
_NEWS = r"新闻|资讯|情报|最近有什么消息|最近发生了什么"
_RELEASE_LIST = r"(?:新作|新游|即将发售|即将发行|发售表|发售日历|发售清单|新发售|新出的|新发布)|(?:有哪些|有什么|哪几部|有啥).{0,24}(?:发售|发行|上线)"
_PROGRESS = r"更(?:新|到)(?:了|到)?\s*(?:第?\s*[\d一二三四五六七八九十几多]+\s*[集话季]|多少期|哪一?[集话季])|播到|放送到|第\s*几[集话]|哪一集了|完结(?:了|了吗)|更新进度"
_SCHEDULE = r"放送|开播|播出|首播|播映|(?:第\s*[\d一二三四五六七八九十几]+\s*集).{0,8}(?:播|更)|新番表|番剧排期|(?:几月|什么时候|何时).{0,8}(?:播|更)"
_EPISODE_CUE = r"播到|放送|开播|播出|首播|播映|(?:第\s*)?[\d一二三四五六七八九十几多]+\s*集|哪一?集|第\s*[\d一二三四五六七八九十]+\s*季"
_RECOMMEND = r"推荐|安利|值得(?:买|玩|看|用)|怎么选|选哪个|(?:找|挑|求|给|来|换)\s*(?:几|一|两|三|四|五|\d+)?\s*(?:[个部款作]|一些|点)|(?:有没有|有啥|有什么|求|想玩|想看).*(?:[番漫]|gal|游戏|作品)|(?:找|挑|哪些|有没有|有啥|有什么).{0,50}(?:好玩|好看).{0,16}(?:gal|游戏|作品|番剧|动画|漫画)|\brecommend\b"
_RANKING_QUALIFIER = r"最火|最热门|热门|高分|评价好|(?:热度|人气)最高|(?:热度|人气|评分)排(?:名)?前"
_RANKING_LIST = r"(?:热度|人气|口碑|评分)榜|热门排行|年度排行榜"
_COMPARE = r"对比|比较(?!推荐|喜欢|新|好|高|低|有趣|适合|热门|冷门|长|短|近期)|哪(?:个|部|款|本)更|哪个好|哪部好|哪款好|(?:选|玩|看)哪个|谁更|有[什么啥]+(?:区别|不同|差别)|\b(?:vs\.?|versus)\b"
_SUMMARY = r"简介|介绍|梗概|故事背景|剧情背景|讲(?:了|的)?(?:是)?什么|剧情(?:大概)?是啥"
_REVIEWS = r"评价|口碑|评测|评论|玩家(?:们)?怎么说|网友(?:们)?怎么说|评价如何"
_STAFF = r"声优|配音|画师|制作人员|制作组|制作公司|原画|脚本|编剧|\bcv\b|\bstaff\b|\bcast\b"
_CHARACTERS = r"角色|登场人物|出场人物|人物表|主人公|主角"
_RELATIONS = (r"关联(?:作品|条目)|系列(?:作品|顺序|有哪些)|(?:前作|续作|外传|番外|衍生作)(?:是(?:什么|哪[些部])|有(?:什么|哪些)|(?:的)?(?:名字|条目|列表))"
              r"|(?:有(?:没有|哪些|什么)?|查(?:一下)?|看看|列出)\s*(?:前作|续作|外传|番外|衍生作)")
_DETAIL_LEAD = (r"^(?:(?:请|帮我|给我|麻烦)\s*)*(?:简单|详细)?"
                r"(?:介绍(?:\s*(?:一下|介绍))?|评价\s*(?:一下|下)|评测\s*(?:一下|下))\s*")
_DETAIL_BOUNDARY = "|".join((
    r"通关|游玩|流程|多长|多久|评分|发售|上线|上架|的剧情", _SUMMARY, _STAFF, _CHARACTERS, _RELATIONS,
    rf"(?:(?:网友|玩家|用户)(?:们)?的?)?(?:{_REVIEWS})", r"(?:并(?:且|再)?|顺便)\s*(?:给我|帮我|简单|再|讲|说|查)",
))
_FACT = r"是谁|哪里|哪[年天家个些款部]|多少|几[个部天集分]|有(?:没有|无)|有吗|了吗|怎样|怎么样|如何|查询|资料|事实|评[价测分]|口碑|好玩吗|好看吗|能玩吗|能看吗|表现|值得|发售|上架|更新|新闻|信息|版本|发布时间|最新|查证|核实|实测|攻略|定价|价格|销量|详情|背景|出了|开服|新作|\?$|" + "|".join((_SUMMARY, _REVIEWS, _CHARACTERS, _RELATIONS))
_QUARTER = r"(?<![A-Za-z])Q\s*([1-4])(?![A-Za-z0-9])|(?<!\d)(?:第\s*)?([1-4一二三四])\s*季度"
_BANGUMI_SUBJECT = re.compile(
    r"(?<![A-Za-z0-9./])https?://(?:www\.)?(?:bgm\.tv|bangumi\.tv|chii\.in)/subject/([1-9]\d{0,9})(?=$|[/?#\s>\],;!?)\u3000-\u9fff])"
    r"|(?<![A-Za-z0-9])(?:Bangumi|bgm(?:\.tv)?|番组计划)\s*(?:条目\s*)?ID\s*[:=#]?\s*([1-9]\d{0,9})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_EXTERNAL = r"新闻|天气|股价|汇率|比分|赛程|政策|时刻表|营业时间|票价|在售|库存|价格|官方公告|最近(?:发布|上线)|当前版本|今天(?:下雨|有雨)|明天(?:下雨|有雨)|几度|怎么走|\b(?:weather|stock price|exchange rate|release date)\b"
_NO_WEB = r"(?:别|不(?:要|用|许|需要)|无需|禁止)\s*(?:再|去|进行|调用|使用)?\s*(?:联网|上网|搜索|检索|浏览网页|搜(?:索|网|资料)?|查(?:找)?(?:一下)?资料|去查|查网|查(?:了|一下)?(?=$|\s|[，,。；;!?]|直接))|\b(?:do not|don't|without)\s+(?:browse|search|web search)\b|只凭(?:你|自己的)?(?:记忆|印象)"
_EXPLICIT = r"^(?:(?:请|麻烦|能不能|能否|可以|帮我|给我|想让你|你能|先|再)\s*)*(?:联网|上网|网上)?\s*(?:查(?!看起来|收|房)|找资料|搜|检索|核实|查证)|(?:请|先|去|帮我)\s*(?:联网|上网)\s*(?:看看|查询?|搜(?:索)?|核实)|\b(?:look up|web search|search the web)\b"
_CREATIVE = r"^(?:(?:请|麻烦|帮我|给我|能不能|可以|只要|只需|先|再|把|将|这段|这句|这篇|下面|以下|的|文本|内容|话|文字|[\s:])\s*)*(?:翻译|润色|改写|扩写|续写|总结(?:下|这|上|以)|摘要|格式化|创作|编(?:一|个|篇|段|首)|写(?:一|个|篇|段|点|首|作|同人|剧本|文案|个角色)|生成(?:一|个|篇|段|图片|代码)|角色扮演|假装|translate\b|rewrite\b|summarize\b)"
_STABLE_CONCEPT = r"^(?:请问\s*)?(?:什么是|啥是|解释一下|给我解释)\s*(?:gal\s*game|gal|视觉小说|旮旯(?:游戏)?|番剧|漫画|二次元|二游|acg)[?啊呀呢 ]*$|^(?:gal\s*game|gal|视觉小说|旮旯(?:游戏)?|番剧|漫画|二次元|二游|acg)\s*(?:是什么|什么意思|是啥|的定义)[?啊呀呢 ]*$"
_ABOUT_BOT = r"^你(?:是谁|叫什么(?:名字)?|是哪个模型|是什么模型|叫什么模型|来自哪里|喜欢什么|在干什么)[?啊呀呢 ]*$|^你(?:自己|个人)?更喜欢.{1,60}还是.{1,60}$"
_CASUAL = r"^(?:你好|嗨|哈[哈]+|晚安|早上好|在吗|你在吗|辛苦了|谢谢|多谢|不错|好耶)[!?.~！。？～\s]*$|^(?:我刚(?:玩|看|通关)|我在(?:玩|看)|我昨晚(?:玩|看)|我超喜欢|我好喜欢|要不要一起|咱们一起|我们一起)"
_SPOILER_NO = r"(?:别|不要|不想|避免)\s*剧透|无剧透|不剧透|\bno spoilers\b"
_SPOILER_YES = r"(?:可以|允许|不怕|接受)\s*剧透|\bspoilers (?:ok|allowed)\b"
_QUOTED = re.compile(r"《([^《》\n]{1,80})》|「([^「」\n]{1,80})」|『([^『』\n]{1,80})』|“([^“”\n]{1,80})”")
_EXCLUDED = re.compile(r"(?:不要|不想要|不喜欢|不看|不玩|排除|拒绝|不含|别推荐|避开)\s*((?!剧透|联网|上网|搜索|检索)[^，,。；;!?\n]{1,50})")
_POST_EXCLUDED = re.compile(r"(?:^|[，,。；;])\s*([^，,。；;!?\n]{2,50}?)\s*(?:除外|不算在内|不考虑)\s*(?=$|[，,。；;!?])")
_GENRES = (
    "纯爱", "废萌", "拔作", "催泪", "治愈", "泣系", "郁系", "悬疑", "推理", "恐怖", "日常", "校园", "恋爱", "百合",
    "乙女", "BL", "后宫", "热血", "机战", "异世界", "科幻", "奇幻", "剧情向", "短篇", "长篇", "全年龄", "女性向", "战斗",
    "搞笑", "轻松", "刀子", "NTR", "RPG", "SLG", "ACT", "STG", "开放世界", "策略", "卡牌", "回合制",
)
_STYLES = ("剧情向", "日常温情", "温情", "温馨", "日常", "治愈", "轻松")
_STEAM_DATE = r"(?<![A-Za-z0-9])steam\s*(?:版)?\s*(?:上架|上线|发售|发行|首发)|(?:上架|上线|发售|发行)\s*(?:于|在|到)?\s*steam(?![A-Za-z0-9])"
_FOLLOW_UP_ACTION = r"(?:直接|现在|先|快|给我|继续)(?:就)?(?:推荐|查|搜)|(?:重新|再|继续)(?:查|搜|推荐)|(?:插件|vndb|月幕).{0,32}(?:查|搜)|不是\s*(?:20)?\d{2}\s*年"
_CHANGE_YEAR = r"(?:还是|改成|改查|换成|改要)(?:查|搜|推荐)?\s*((?:20)?\d{2}\s*年)(?:的|吧|好了)?[。!！]?$"
_SOURCE_FOLLOW_UP = r"bangumi|bgm(?:\.tv)?|番组计划|vndb|批评空间|egs|月幕|steam|cngal"
_SOURCE_ONLY = rf"^(?:{_SOURCE_FOLLOW_UP})(?:\s*(?:那边|这边|上面|站内|平台|网站|那儿|这里|上|有|的))*$"
_FOLLOWUP_MEDIUM = {"galgame": "Galgame", "anime": "动画", "manga": "漫画", "acg_game": "二次元游戏"}


def _search(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(pattern, text, re.IGNORECASE)


def _clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _term_pattern(term: str) -> str:
    value = re.escape(_clean(term))
    if term.isascii() and term[:1].isalnum():
        value = r"(?<![A-Za-z0-9])" + value
    if term.isascii() and term[-1:].isalnum():
        value += r"(?![A-Za-z0-9])"
    return value


def _unique(values) -> tuple[str, ...]:
    found: dict[str, str] = {}
    for value in values:
        if value:
            found.setdefault(_clean(value).casefold(), value)
    return tuple(found.values())


def _known_titles(text: str, aliases) -> list[tuple[int, int, str, str]]:
    if isinstance(aliases, Mapping):
        configured = aliases.items()
    elif isinstance(aliases, Sequence) and not isinstance(aliases, str):
        configured = (part.split("=", 1) for part in aliases if isinstance(part, str) and "=" in part)
    else:
        configured = ()
    matches = []
    for alias, title in configured:
        if isinstance(alias, str) and isinstance(title, str) and 0 < len(alias.strip()) <= 80 and 0 < len(title.strip()) <= 80:
            if match := _search(_term_pattern(alias.strip()), text):
                matches.append((match.start(), match.end(), title.strip(), "galgame"))
    matched = []
    end = -1
    for start, stop, title, medium in sorted(matches, key=lambda row: (row[0], -row[1])):
        if start >= end:
            matched.append((start, stop, title, medium))
            end = stop
    return matched


def _detect_task(text: str) -> str:
    if _search(_PROGRESS, text) or _search(_ANIME, text) and _search(r"更新了(?:吗|没)|更新没", text):
        return "progress"
    if _search(_COMPARE, text):
        return "comparison"
    if _search(_NEWS, text):
        main = re.split(r"[，,；;。]|顺便", text, maxsplit=1)[0]
        if main != text and _search(_RECOMMEND, main) and not _search(_NEWS, main):
            return "recommendation"
        return "news"
    if not _search(r"推荐|安利|值得|哪个好|怎么选", text) and _search(_RELEASE_LIST, text):
        return "release"
    if _search(_RECOMMEND, text) or _search(_RANKING_LIST, text):
        return "recommendation"
    if _search(_SCHEDULE, text):
        return "schedule"
    if _search(rf"(?:有哪些|什么|哪个|哪部|哪款).{{0,30}}(?:{_RANKING_QUALIFIER})|(?:{_RANKING_QUALIFIER}).{{0,30}}(?:有哪些|是什么|有啥|是哪几部)", text):
        return "recommendation"
    if _search(r"攻略|配队|全结局|真结局|路线|怎么通关|存档|\bwalkthrough\b", text):
        return "walkthrough"
    if _search(r"汉化|中文补丁|中文支持|支持中文|官中|有没有中文", text):
        return "localization"
    if _search(_DURATION, text):
        return "duration"
    if _search(_STAFF + "|" + _CHARACTERS, text):
        return "credits"
    if _search(r"发售|发行|上市|发布|首发|上线|新作|\brelease date\b", text):
        return "release"
    if _search(r"哪里(?:买|看|玩)|在哪(?:里)?(?:买|看|玩)|购买|在售|上架|下架|价格|多少钱", text):
        return "availability"
    return "fact"


def _detect_medium(text: str, task: str, known, extra) -> str:
    anime = bool(_search(_ANIME, text))
    manga = bool(_search(_MANGA, text))
    galgame = bool(_search(_GALGAME, text)) or any(_search(_term_pattern(term), text) for term in extra)
    acg_game = bool(_search(_ACG_GAME, text))
    known_media = {medium for _, _, _, medium in known}
    if task == "comparison" and galgame and (anime or manga or acg_game):
        return "galgame"
    if _search(r"(?:改编|改成|出|有|看)(?:成|了|的)?\s*(?:TV\s*)?(?:版)?动画|动画版|TV版|动画化", text):
        return "anime"
    if _search(r"漫画版|原作漫画|漫画原作", text) and not anime:
        return "manga"
    explicit_media = [medium for medium, exists in (
        ("anime", anime), ("manga", manga), ("galgame", galgame), ("acg_game", acg_game),
    ) if exists]
    if len(explicit_media) > 1:
        return "other"
    if explicit_media:
        return explicit_media[0]
    if _search(_ACG_UPDATE_CUE, text):
        return "acg_game"
    if task in {"progress", "schedule"} and not _search(_GAME, text) and _search(_EPISODE_CUE, text):
        return "anime"
    if len(known_media) == 1:
        return next(iter(known_media))
    if not known_media and _search(r"\d{1,2}\s*月(?:新)?番|[春夏秋冬](?:季)?(?:新)?番", text):
        return "anime"
    if _search(_GAME, text):
        return "game"
    return "other"


def _candidate_title(value: str) -> str:
    value = re.sub(r"^(?:(?:请问|请|麻烦|帮我|给我|想知道|查(?:一下)?|搜(?:一下)?|看看|比较一下|对比一下|比较|对比|这部|那部)\s*)+", "", value)
    value = re.sub(r"^(?:(?:gal\s*game|gal)(?![A-Za-z0-9])|视觉小说|美少女游戏|文字冒险|番剧|动画|漫画|二次元游戏|二游|手游|端游|主机游戏)\s*[:：]?\s*", "", value, flags=re.IGNORECASE)
    value = re.split(r"(?:(?:玩|看|选)?哪个|哪部|哪款|谁更|区别|差别|有(?:没有|什么)|更值得|是不是|是什么|怎么[样选]|好不好|请问|什么时候|何时|更新|放送|播出|开播|声优|配音|攻略|支持中文|汉化|发售|定价|价格|[，,。；;?!])", value, maxsplit=1)[0]
    value = value.strip(" 《》「」『』“”\"':：")
    value = re.sub(r"(?:动画版|漫画版|番剧|动画|动漫|漫画|最近|的)$", "", value).strip()
    if not 1 < len(value) <= 80 or _search(r"^(?:这些|这几个|这个|那个|那几个|这两部|那个番|这部番|哪个|最新|本季|这季|最近|今年|明年|上季|上周|本周|下周|今天|明天|什么时候|第[几一二三四五六七八九十\d]+[季集话]|现在|下一集|完结|新番|番剧|动画|动漫|二游|游戏|漫画|作品|原作)$", value):
        return ""
    return value


def _detect_titles(text: str, task: str, medium: str, known) -> tuple[str, ...]:
    quoted = [(match.start(), match.end(), next(value for value in match.groups() if value).strip())
              for match in _QUOTED.finditer(text)]
    if quoted:
        # Prefer the canonical spelling of a quoted alias, without making a second
        # candidate out of the spelling the user provided.
        titles = [next((title for start, stop, title, _ in known if start == offset + 1 and stop == start + len(value)), value)
                  for offset, _, value in quoted]
        if medium == "anime" and len(quoted) == 1:
            tail = text[quoted[0][1]:]
            season = _search(r"^\s*(?:的\s*)?(第\s*[一二三四五六七八九十\d]+\s*[季期]|S\s*\d+|续篇|续作)", tail)
            if season:
                titles[0] += " " + season[1]
        if task != "comparison" or len(titles) > 1:
            return _unique(titles)[:4]
    else:
        titles = []
    if task == "comparison":
        sides = re.split(r"\s*(?:\bvs\.?\b|\bversus\b|和|与|跟|还是)\s*", text, maxsplit=1, flags=re.IGNORECASE)
        if len(sides) == 2:
            left, right = map(_candidate_title, sides)
            if left and right:
                boundary = len(sides[0])
                left = next((title for start, stop, title, _ in known if stop <= boundary and _clean(text[start:stop]).casefold() == _clean(left).casefold()), left)
                right = next((title for start, stop, title, _ in known if start >= boundary and _clean(text[start:stop]).casefold() == _clean(right).casefold()), right)
                return _unique((left, right))
    if medium == "anime" and task in {"progress", "schedule", "availability"}:
        candidate = re.split(r"(?:更新|更到|播到|放送到|放送|第[一二三四五六七八九十几\d]+[集话]|哪一集|什么时候|何时|播出|开播|首播|完结)", text, maxsplit=1)[0]
        candidate = _candidate_title(candidate)
        if candidate and not _search(r"\d{1,2}\s*月(?:新)?番|[春夏秋冬](?:季)?番|新番表|排期|本季|本月|下季|本周|下周|最新番剧", candidate):
            if not known or _search(r"第[一二三四五六七八九十\d]+季|续篇|续作", candidate):
                return (candidate,)
    if known:
        return _unique(title for _, _, title, _ in known)[:4]
    if (task == "progress" and medium in {"other", "game", "acg_game"}
            or task == "fact" and medium == "acg_game" and _search(_GAME_UPDATE, text)):
        subject = re.split(_PROGRESS + "|" + _GAME_UPDATE, text, maxsplit=1)[0]
        subject = re.sub(r"(?:的)?(?:最新|现在|目前|今天|今日|这期|本期)\s*$", "", subject)
        if title := _candidate_title(subject):
            return (title,)
    if task not in {"recommendation", "news"} and (medium == "galgame" or _search("|".join((_SUMMARY, _REVIEWS, _STAFF, _CHARACTERS, _RELATIONS)), text)):
        subject = re.sub(_DETAIL_LEAD, "", text, flags=re.IGNORECASE)
        subject = re.sub(r"^(?:最近|近期|刚出的|新出的)的?\s*", "", subject)
        if medium == "galgame" and _search(_FACT + "|" + _EXPLICIT, text) and (reference := _search(_GALGAME_REFERENCE, subject)):
            subject = subject[:reference.start()]
        candidate = _candidate_title(re.split(_DETAIL_BOUNDARY, subject, maxsplit=1, flags=re.IGNORECASE)[0])
        if candidate and not _search(_GALGAME + "|" + _SOURCE_ONLY + r"|本[月周]|这个月|最近|新作|日历|^[你您](?:自己|是|的)|^这个问题", candidate):
            return (candidate,)
    return _unique(titles)[:4]


def _detect_dates(text: str, today: date) -> tuple[tuple[int, ...], int | None, str, str]:
    year_pattern = r"(?<![A-Za-z0-9.])((?:19|20)\d{2})(?![A-Za-z0-9.])\s*年|(?<![A-Za-z0-9.])(\d{2})\s*年"
    years = [int(full) if full else 2000 + int(short) for full, short in re.findall(year_pattern, text)]
    for term, shift in (("前年", -2), ("去年", -1), ("今年", 0), ("明年", 1), ("后年", 2)):
        if term in text:
            years.append(today.year + shift)
    for match in re.finditer(r"(?<![A-Za-z0-9.])((?:19|20)\d{2})\s*年?\s*(?:到|至|[-~—])\s*((?:19|20)\d{2})\s*年", text):
        low, high = sorted(map(int, match.groups()))
        if high - low <= 20:
            years.extend(range(low, high + 1))
    quarter = _search(_QUARTER, text)
    if quarter and not years:
        year = _search(r"(?<![A-Za-z0-9.])((?:19|20)?\d{2})\s*$", text[:quarter.start()])
        year = year or _search(r"^\s*((?:19|20)\d{2})(?![A-Za-z0-9])", text[quarter.end():])
        value = int(year[1]) if year else today.year
        years.append(2000 + value if value < 100 else value)
    month = None
    month_match = _search(r"(?<!\d)(1[0-2]|0?[1-9])\s*月(?!\s*(?:份前|份后))", text)
    if month_match:
        month = int(month_match.group(1))
    season = _search(r"([春夏秋冬])(?:季(?:新?番)?|新?番)", text)
    if season and month is None:
        month = {"冬": 1, "春": 4, "夏": 7, "秋": 10}[season.group(1)]
    if not years and month is not None:
        years.append(today.year)
    years = tuple(sorted(set(years)))

    def dates(first: date, last: date) -> tuple[tuple[int, ...], int | None, str, str]:
        return years, month, first.isoformat(), last.isoformat()

    absolute = _search(r"(?<!\d)((?:19|20)\d{2})[-/年]\s*(\d{1,2})[-/月]\s*(\d{1,2})(?:日|号|\b)", text)
    if absolute:
        try:
            exact = date(*map(int, absolute.groups()))
            return dates(exact, exact + timedelta(days=1))
        except ValueError:
            pass
    if quarter and len(years) == 1:
        number = int(quarter[1] or quarter[2].translate(str.maketrans("一二三四", "1234")))
        first = date(years[0], (number - 1) * 3 + 1, 1)
        year, next_month = divmod(years[0] * 12 + number * 3, 12)
        return dates(first, date(year, next_month + 1, 1))
    for names, shift in ((r"昨天|昨日", -1), (r"今天|今日", 0), (r"明天|明日", 1)):
        if _search(names, text):
            day = today + timedelta(days=shift)
            return dates(day, day + timedelta(days=1))
    for names, shift in ((r"上周|上星期", -7), (r"本周|这周|本星期", 0), (r"下周|下星期", 7)):
        if _search(names, text):
            first = today - timedelta(days=today.weekday()) + timedelta(days=shift)
            return dates(first, first + timedelta(days=7))
    month_relative = _search(r"(上|本|下|这[个]?)月", text)
    if month_relative:
        offset = {"上": -1, "下": 1}.get(month_relative.group(1), 0)
        full_month = today.year * 12 + today.month - 1 + offset
        year, zero_month = divmod(full_month, 12)
        first = date(year, zero_month + 1, 1)
        return dates(first, first + timedelta(days=calendar.monthrange(year, zero_month + 1)[1]))
    days = _search(r"(?:过去|最近|近)\s*(\d{1,3})\s*天", text)
    if days and 1 <= int(days.group(1)) <= 366:
        return dates(today - timedelta(days=int(days.group(1)) - 1), today + timedelta(days=1))
    if len(years) == 1 and month is not None:
        first = date(years[0], month, 1)
        span = 3 if season or _search(rf"{month}\s*月(?:新)?番", text) else 1
        year, zero_month = divmod(years[0] * 12 + month - 1 + span, 12)
        return dates(first, date(year, zero_month + 1, 1))
    if years:
        return dates(date(years[0], 1, 1), date(years[-1] + 1, 1, 1))
    return years, month, "", ""


def _detect_score(text: str) -> tuple[str, float | None, float | None, str]:
    ranking = "popularity" if _search(r"热门|人气|最火|最多人|热销|热度|热榜|\bpopular\b", text) else ""
    if _search(r"高分|评分|口碑|最高分|评价最好|(?:满|低于|高于)\s*\d+(?:\.\d+)?\s*分|\b(?:top-rated|best rated)\b", text):
        ranking = "rating"
    sources = [name for name, pattern in (
        ("vndb", r"(?<![A-Za-z0-9])vndb(?![A-Za-z0-9])"),
        ("erogamescape", r"批评空间|(?<![A-Za-z0-9])(?:egs|erogamescape)(?![A-Za-z0-9])"),
        ("bangumi", r"番组计划|(?<![A-Za-z0-9])(?:bangumi|bgm\.tv)(?![A-Za-z0-9])"),
    ) if _search(pattern, text)]
    source = sources[0] if len(sources) == 1 else ""
    number = r"(?<![\d.])([0-9]{1,3}(?:\.\d+)?)(?![\d.])"
    between = _search(rf"{number}\s*分?\s*(?:到|至|[-~—])\s*{number}\s*分", text)
    if between:
        low, high = sorted(map(float, between.groups()))
        return "rating", low if low <= 100 else None, high if high <= 100 else None, source
    minimum = _search(rf"(?:至少|不低于|高于|大于|≥|>=)\s*{number}\s*分|{number}\s*分\s*(?:以上|起|起步|\+)", text)
    maximum = _search(rf"(?:至多|不高于|低于|小于|≤|<=)\s*{number}\s*分|{number}\s*分\s*(?:以下|以内)", text)
    if (source or ranking == "rating") and not minimum:
        minimum = _search(rf"{number}\s*\+(?!\d)", text)
    low = float(next(value for value in minimum.groups() if value is not None)) if minimum else None
    high = float(next(value for value in maximum.groups() if value is not None)) if maximum else None
    return (ranking or ("rating" if minimum or maximum else ""), low if low is not None and low <= 100 else None,
            high if high is not None and high <= 100 else None, source)


def _detect_playtime(text: str) -> tuple[float | None, float | None]:
    number = r"(?:[0-9]{1,4}(?:\.[0-9]{1,2})?|半|[一二两三四五六七八九]?十[一二三四五六七八九]?|[一二两三四五六七八九])"
    unit = r"(?:个?小时|钟头|分钟|h(?![A-Za-z0-9]))"
    digits = {character: index for index, character in enumerate("一二三四五六七八九", 1)} | {"两": 2}

    def hours(value: str, scale: str) -> float:
        if value == "半":
            result = 0.5
        elif "十" in value:
            tens, ones = value.split("十")
            result = 10 * digits.get(tens, 1) + digits.get(ones, 0)
        else:
            result = digits[value] if value in digits else float(value)
        return result / 60 if scale == "分钟" else float(result)

    def release_window(match: re.Match[str]) -> bool:
        return bool(re.search(r"(?:最近|近|过去|未来|接下来|发售后|上线后)\s*$", text[:match.start()])
                    or re.match(r"\s*(?:新)?(?:发售|发行|发布|上线|开服|更新)", text[match.end():]))

    span = _search(rf"(?<![\d.])({number})\s*({unit})?\s*(?:到|至|[-~—])\s*({number})\s*({unit})", text)
    if span and not release_window(span):
        first, first_unit, second, second_unit = span.groups()
        return hours(first, first_unit or second_unit), hours(second, second_unit)

    duration = rf"(?<![\d.])({number})\s*({unit})"
    lower, upper = [], []
    for pattern, bounds in (
        (rf"(?:至少|不少于|不低于|(?<!不)(?:超过|长于|高于)|>=|≥)\s*{duration}", lower),
        (rf"{duration}\s*(?:以上|起(?:步)?)", lower),
        (rf"(?:不超过|最多|至多|不多于|不高于|(?<!不)(?:少于|低于|短于)|<=|≤)\s*{duration}", upper),
        (rf"{duration}\s*(?:之内|以内|以下|内)", upper),
    ):
        bounds.extend(hours(*match.groups()) for match in re.finditer(pattern, text, re.I) if not release_window(match))
    return max(lower, default=None), min(upper, default=None)


def _exclusion_parts(phrase: str):
    """Keep conjunctions inside quoted work names intact."""
    offset = 0
    for match in _QUOTED.finditer(phrase):
        for value in re.split(r"以及|或者|[和与及或、/]", phrase[offset:match.start()]):
            yield value.strip(), False
        yield next(value for value in match.groups() if value).strip(), True
        offset = match.end()
    for value in re.split(r"以及|或者|[和与及或、/]", phrase[offset:]):
        yield value.strip(), False


def _separate_exclusions(text: str, aliases) -> tuple[str, tuple[tuple[str, bool], ...]]:
    positive = list(text)
    exclusions = []
    for match in _POST_EXCLUDED.finditer(text):
        value = match.group(1).strip()
        if not _search(_RECOMMEND + "|" + _EXPLICIT, value):
            positive[match.start(1):match.end()] = ' ' * (match.end() - match.start(1))
            exclusions.extend((phrase, quoted) for phrase, quoted in _exclusion_parts(value) if phrase)
    for match in _EXCLUDED.finditer(''.join(positive)):
        raw = match.group(1)
        boundary = _search(r"但是|但要|最好|预算|支持中文|可以剧透|只要|想要|想看|想玩|想找", raw)
        end = boundary.start() if boundary else len(raw)
        known = _known_titles(raw[:end], aliases)
        if known and known[0][0] == 0:
            tail = raw[known[0][1]:end].lstrip()
            if any(tail.startswith(style) for style in _STYLES):
                end = known[0][1]
        if suffix := _search(r"(?:剧情向|日常温情|温馨日常|日常治愈)(?:的(?:作品|游戏)?)?\s*$", raw[:end]):
            if len(raw[:suffix.start()].strip()) >= 2:
                end = suffix.start()
        stop = match.start(1) + end
        positive[match.start():stop] = ' ' * (stop - match.start())
        for phrase, quoted in _exclusion_parts(raw[:end]):
            value = phrase if quoted else re.sub(r"(?:的(?:作品|游戏|番剧)?|类型|题材)$", "", phrase)
            if value and not _search(r"剧透|联网|上网|搜索|检索|查资料", value):
                exclusions.append((value, quoted))
    return ''.join(positive), tuple(exclusions[:8])


def _preferences(text: str, medium: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    genres = tuple(genre for genre in _GENRES if _search(_term_pattern(genre), text))
    if medium != "galgame":
        return genres, ()
    style_text = re.sub(r"日常\s*[、/与和]?\s*(?:温情|温馨)", "日常温情", text)
    warm_daily = "日常温情" in style_text
    styles = tuple(style for style in _STYLES if style in style_text and (
        not warm_daily or style not in {"温情", "温馨", "日常"}
    ))
    required = tuple(genre for genre in genres if genre not in _STYLES or _search(
        rf"(?:只要|仅限|必须|一定要)\s*{_term_pattern(genre)}", text,
    ))
    return required, styles


def _exclusions(terms, medium, aliases) -> tuple[tuple[str, ...], tuple[str, ...]]:
    tags, titles = [], []
    known_tags = {name.casefold() for name in (*_GENRES, *_STYLES, "虐心", "猎奇", "重口", "惊悚")}
    for value, quoted in terms:
        known = _known_titles(value, aliases)
        exact = next((title for start, stop, title, _ in known if start == 0 and stop == len(value)), "")
        if quoted or exact or medium == "galgame" and value.casefold() not in known_tags:
            titles.extend((value, exact) if exact else (value,))
        else:
            tags.append(value)
    return _unique(tags), _unique(titles)


def _constraints_text(text: str, known: list[tuple[int, int, str, str]]) -> str:
    """Title words such as 明日 in 明日方舟 are not date constraints."""
    excluded = [match.span() for match in _QUOTED.finditer(text)]
    excluded.extend((start, stop) for start, stop, _, _ in known)
    characters = list(text)
    for start, stop in excluded:
        characters[start:stop] = " " * (stop - start)
    return "".join(characters)


def _detect_count(text: str) -> int | None:
    match = _search(r"(?:推荐|安利|给我|找|挑|求|来|换|只(?:要|推荐|选)|列出)\s*(?:给我|我|一下)?\s*(\d{1,2}|[一二两三四五六七八九十])?\s*(?:个(?!人)|部|款|作)", text)
    if not match:
        return None
    word = match.group(1) or "一"
    if word.isdecimal():
        return max(1, min(20, int(word)))
    return {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}[word]


def classify(
    query: str,
    *,
    today: date | None = None,
    aliases: Mapping[str, str] | Sequence[str] | None = None,
    galgame_terms: Sequence[str] = (),
    force_search: bool = False,
) -> SearchIntent:
    """Route original text; date_to is exclusive. Aliases are alias -> official title."""
    if not isinstance(query, str) or not query.strip():
        return SearchIntent("", task="chat", reason="empty")
    text = _clean(query)
    if _search(_NO_WEB, text):
        return SearchIntent(query, task="chat", reason="no_web")
    if _search(_CREATIVE, text):
        return SearchIntent(query, task="creative", reason="transform_or_creative")
    lookup_request = _search(_EXPLICIT, text)
    explicit = force_search or bool(lookup_request)
    if not explicit and _search(_ABOUT_BOT, text):
        return SearchIntent(query, task="chat", reason="about_bot")
    concept_text = re.sub(r"^(?:一下|一下子)\s*", "", text[lookup_request.end():]).lstrip() if lookup_request else text
    if _search(_STABLE_CONCEPT, concept_text):
        return SearchIntent(query, route="web" if explicit else "skip", explicit=explicit, reason="stable_concept")

    request, exclusions_raw = _separate_exclusions(text, aliases)
    task = _detect_task(request)
    known = _known_titles(request, aliases)
    if task in {"fact", "recommendation"} and "还是" in request and (len(known) >= 2 or len(list(_QUOTED.finditer(request))) >= 2):
        task = "comparison"
    extra = tuple(term for term in galgame_terms if isinstance(term, str) and term.strip())
    medium = _detect_medium(request, task, known, extra)
    if (task == "fact" and not known and not _QUOTED.search(request)
            and not _search(r"是|算|属于|[这那](?:一)?[部款个]", request)
            and _search(rf"(?:{_RANKING_QUALIFIER}|人气)\s*(?:的\s*)?(?:{_ANIME}|{_MANGA}|{_GALGAME_KIND}|{_ACG_GAME})[?!。]*$", request)):
        task = "recommendation"
    titles = _detect_titles(request, task, medium, known)
    if task != "recommendation" and (subjects := _unique(
        f"https://bgm.tv/subject/{match[1] or match[2]}" for match in _BANGUMI_SUBJECT.finditer(request)
    )):
        medium, titles = "bangumi", subjects[:4]
    if medium == "manga" and _search(_MANGA_PROGRESS, text) and task != "recommendation":
        task = "progress"
    if medium == "acg_game" and _search(_GAME_UPDATE, text) and task in {"fact", "schedule", "progress"}:
        task = "progress"
    if medium == "anime" and task == "availability" and _search(r"第[几一二三四五六七八九十\d]+集|更到|更新", text):
        task = "progress"
    related_media = tuple(name for name, pattern in (("anime", _ANIME), ("manga", _MANGA), ("acg_game", _ACG_GAME))
                          if medium == "galgame" and task == "comparison" and _search(pattern, text))
    similar_to = ""
    if task == "recommendation" and titles and _search(r"类似|像.{1,80}的|同类|同风格|差不多|\bsimilar to\b", text):
        similar_to, *remaining = titles
        titles = tuple(remaining)
    positive = _constraints_text(request, known)
    years, month, start, end = _detect_dates(positive, today or date.today())
    platforms = tuple(name for name, pattern in (
        ("Steam", r"(?<![A-Za-z0-9])steam(?![A-Za-z0-9])"),
        ("PC", r"(?<![A-Za-z0-9])(?:pc|windows|macos|linux)(?![A-Za-z0-9])|电脑端|电脑版|电脑上"),
        ("Switch", r"\bswitch(?:\s*2)?\b|任天堂主机"),
        ("PS5", r"\b(?:ps5|playstation\s*5)\b"), ("PS4", r"\b(?:ps4|playstation\s*4)\b"),
        ("Xbox", r"\bxbox\b"),
        ("手机", r"手机(?:端|版|上)?|手游|安卓|(?<![A-Za-z])(?:android|ios)(?![A-Za-z])|移动端"),
    ) if _search(pattern, positive))
    language = next((name for name, pattern in (
        ("简体中文", r"简中|简体中文"), ("繁体中文", r"繁中|繁体中文"), ("中文", r"中文|汉化|官中"),
        ("日文", r"日文|日语|生肉"), ("英文", r"英文|英语"),
    ) if _search(pattern, positive)), "")
    official_translation = medium == "galgame" and bool(_search(
        r"官中|官方(?:简体中文|繁体中文|中文|简中|繁中|汉化)", positive,
    ))
    region = _search(r"中国大陆|中国香港|中国台湾|中国|大陆|国区|国服|日区|日服|日本|美区|美服|美国|港区|港服|香港|台区|台服|台湾|国际服|海外|北美|欧洲|全球", positive)
    spoiler = "avoid" if _search(_SPOILER_NO, text) else "allow" if _search(_SPOILER_YES, text) else "unspecified"
    ranking, minimum, maximum, score_source = _detect_score(positive)
    min_hours, max_hours = _detect_playtime(positive) if medium == "galgame" and task in {"recommendation", "comparison"} else (None, None)
    genres, styles = _preferences(positive, medium)
    exclusions, excluded_titles = _exclusions(exclusions_raw, medium, aliases)
    date_basis = "steam" if medium == "galgame" and _search(_STEAM_DATE, positive) else "first_release"
    fields = tuple(name for name, pattern in (
        ("store", r"steam|价格|多少钱|国区|上架|下架|折扣"),
        ("language", r"中文|汉化|官中|日文|英文"),
        ("release", r"发售|发行|首发|上市|新作|发布"),
        ("duration", _DURATION), ("score", r"评分|评价|口碑|分数|好评"),
        ("summary", _SUMMARY), ("reviews", _REVIEWS),
        ("staff", _STAFF),
        ("characters", _CHARACTERS), ("relations", _RELATIONS),
        ("tags", r"题材|类型|标签"),
    ) if _search(pattern, positive))
    bare_title = any(not (text[:start_at] + text[stop:]).strip(" 《》「」『』“”!?")
                     for start_at, stop, _, _ in known)
    bare_title = bare_title or bool(_QUOTED.fullmatch(text.strip("!?")))
    if not explicit and _search(_CASUAL, text) and not _search(r"[?]|请问|推荐|怎么|为什么|攻略", text):
        route, reason, task = "skip", "casual", "chat"
    elif medium == "anime" and task == "progress" and not titles:
        route, reason = "clarify", "missing_anime_title"
    elif medium == "bangumi":
        route, reason = "catalogue", "specialized_topic"
    elif medium == "galgame" and (explicit or titles or fields or task != "fact" or _search(_FACT, text)):
        route, reason = "catalogue", "specialized_topic"
    elif medium in {"anime", "manga", "acg_game"} and (
        task in {"recommendation", "comparison", "schedule", "progress", "availability", "release", "news", "credits"}
        or task != "walkthrough" and titles and (explicit or bare_title or _search(_FACT, text))
    ):
        route, reason = "catalogue", "specialized_topic"
    elif medium == "other" and task == "recommendation" and _search(_GENERAL_ACG, text) and not titles:
        route, reason = "clarify", "missing_acg_medium"
    elif (explicit or bare_title or _search(_EXTERNAL, text) or task in {"recommendation", "comparison"}
          or (medium != "other" or bool(titles)) and (task != "fact" or _search(_FACT, text))
          or _search(r"^.{1,70}(?:是谁|是哪个|是哪里人|出生在哪里|现在多少钱|现任|最新版本|什么时候(?:出生|发布|发售|上线|开播)|几时上线|有更新吗|出了没|发布了吗)[?啊呀呢 ]*$", text)):
        route, reason = "web", "external_information"
    else:
        route, reason, task = "skip", "no_lookup_signal", "chat"
    return SearchIntent(
        query=query, medium=medium, task=task, route=route, titles=titles,
        year=years[0] if len(years) == 1 else None, month=month, years=years, date_from=start, date_to=end,
        region=region.group() if region else "", platform=" ".join(platforms), language=language,
        count=_detect_count(positive), genres=genres, exclusions=exclusions, spoiler_policy=spoiler,
        ranking=ranking, min_score=minimum, max_score=maximum, score_source=score_source,
        min_hours=min_hours, max_hours=max_hours,
        official_translation=official_translation,
        explicit=explicit, reason=reason, similar_to=similar_to, fields=fields, related_media=related_media,
        styles=styles, excluded_titles=excluded_titles, date_basis=date_basis,
    )


def merge_followup(previous: str, current: str, *, today=None, aliases=None, galgame_terms=()) -> str | None:
    """Resolve short continuations after the host verifies conversation and sender."""
    options = {"today": today, "aliases": aliases, "galgame_terms": galgame_terms}
    prior = classify(previous, **options)
    if prior.route != "catalogue" or prior.medium not in _FOLLOWUP_MEDIUM:
        return None
    current = _clean(current)
    if not current or len(current) > 140 or _search(_NO_WEB, current) or _search(_CREATIVE, current):
        return None
    update = classify(current, **options)
    topic = _search(r"^那(?:么)?\s*([^，,。；;!?]{2,36}?)\s*(?:呢|怎么样|如何)\s*[?？]?$", current)
    source = _search(rf"(?<![A-Za-z0-9])(?:{_SOURCE_FOLLOW_UP})(?![A-Za-z0-9])", current)
    if source and prior.medium != "galgame" and not _search(r"bangumi|bgm|番组计划", source.group()):
        source = None
    medium = _FOLLOWUP_MEDIUM[prior.medium]
    if (topic and update.medium in {"other", prior.medium} and not source
            and not _search(r"^(?:这个|那个|其他|别的|结果|评分|价格|你|这部|那部)|令牌|配置|怎么|可以|能不能|^再", topic[1])):
        title = update.titles[0] if len(update.titles) == 1 else _candidate_title(topic[1])
        if title:
            return f"查询 {medium}《{title}》的资料和评价"
    if len(prior.titles) == 1 and (update.medium in {"other", prior.medium} or source):
        title = prior.titles[0]
        if source and (not update.titles or update.titles == prior.titles) and len(current) <= 65:
            return f"查询 {medium}《{title}》在 {source.group()} 的条目和评价"
        if len(current) <= 90 and (title.casefold() in current.casefold() or _search(r"^(?:这|那)(?:个|部|作|款)", current)):
            wants_reviews = _search(_REVIEWS + r"|评分", current)
            wants_summary = _search(_SUMMARY, current)
            if wants_reviews or wants_summary:
                parts = "和".join(part for part, requested in (("作品简介", wants_summary), ("玩家评价", wants_reviews)) if requested)
                return f"查询 {medium}《{title}》的{parts}"
    if (prior.medium, prior.task) != ("galgame", "recommendation"):
        return None
    changed = re.fullmatch(_CHANGE_YEAR, current)
    if changed and len(prior.years) <= 1:
        text = _clean(previous)
        positive, _ = _separate_exclusions(text, aliases)
        scope = _constraints_text(positive, _known_titles(positive, aliases))
        year = _search(r"(?<!\d)(?:(?:19|20)?\d{2}\s*年|前年|去年|今年|明年|后年)(?!\d|\s*\d{1,2}\s*月)", scope)
        if year:
            return text[:year.start()] + changed[1] + text[year.end():]
        return f"{text}，{changed[1]}" if not prior.date_from and len(text) + len(changed[1]) < 600 else None
    if update.route == "catalogue" and update.task == "recommendation" and (
        update.year or update.date_from or update.titles or _search(_GALGAME, current)
    ):
        return None
    if len(current) <= 45 and ((update.route == "skip" and update.genres) or any(style in current for style in _STYLES)):
        if update.titles or update.years or _search(_ANIME + "|" + _MANGA + "|" + _GAME, current):
            return None
        candidate = f"{previous}，偏好{current}"
        return candidate if len(candidate) <= 600 else previous
    if _search(_FOLLOW_UP_ACTION, current) and (update.medium in {"galgame", "other"} and not update.years
            or update.route == "skip" and update.years == prior.years):
        return previous
    return None
