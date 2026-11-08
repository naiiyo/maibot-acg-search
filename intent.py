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


_GALGAME = r"(?<![A-Za-z0-9])(?:gal\s*game|gal|visual novel|vndb|egs|erogamescape)(?![A-Za-z0-9])|旮[旯旮](?:游戏|game)?|视觉小说|美少女游戏|文字冒险|柚子社|Key社|型月|批评空间|月幕"
_ANIME = r"番剧|动画|动漫|新番|补番|追番|日番|国番|泡面番|(?:[一几新]部|科幻|恋爱|日常|搞笑|异世界|百合|悬疑|机战|校园|治愈)番|(?<![A-Za-z0-9])(?:anime|donghua)(?![A-Za-z0-9])"
_MANGA = r"漫画|条漫|单行本|(?<![A-Za-z0-9])manga(?![A-Za-z0-9])"
_ACG_GAME = r"二次元游戏|二游|手游二次元|抽卡游戏"
_GAME = r"游戏(?!本|手机|手柄)|端游|手游|主机游戏|使命召唤|战地|(?<![A-Za-z0-9])(?:cod\s*\d+|battlefield\s*\d*)(?![A-Za-z0-9])"
_GENERAL_ACG = r"二次元|(?<![A-Za-z0-9])acg(?![A-Za-z0-9])"
_GAME_UPDATE = r"卡池|复刻|前瞻|维护|更新进度|更新公告|活动|版本|开服|更新到|什么时候更新"
_MANGA_PROGRESS = r"连载|话数|刊载|出到第|更新到第"
_DURATION = r"多久(?:能|可以)?(?:玩完|通关|打完)|(?:通关|流程|游玩|全收集|全路线).{0,8}(?:多久|多长|时长|几小时|几个小时)|游玩时长|通关时长"
_NEWS = r"新闻|资讯|情报|最近有什么消息|最近发生了什么"
_RELEASE_LIST = r"(?:新作|新游|即将发售|即将发行|发售表|发售日历|发售清单|新发售|新出的|新发布)|(?:有哪些|有什么|哪几部|有啥).{0,24}(?:发售|发行|上线)"
_PROGRESS = r"更(?:新|到)(?:了|到)?\s*(?:第?\s*[\d一二三四五六七八九十几多]+\s*[集话季]|多少期|哪一?[集话季])|播到|放送到|第\s*几[集话]|哪一集了|完结(?:了|了吗)|更新进度"
_SCHEDULE = r"放送|开播|播出|首播|播映|(?:第\s*[\d一二三四五六七八九十几]+\s*集).{0,8}(?:播|更)|新番表|番剧排期|(?:几月|什么时候|何时).{0,8}(?:播|更)"
_RECOMMEND = r"推荐|安利|值得(?:买|玩|看|用)|怎么选|选哪个|(?:找|挑|求|给|来|换)\s*(?:几|一|两|三|四|五|\d+)?\s*(?:[个部款作]|一些|点)|(?:有没有|有啥|有什么|求|想玩|想看).*(?:[番漫]|gal|游戏|作品)|(?:找|挑|哪些|有没有|有啥|有什么).{0,50}(?:好玩|好看).{0,16}(?:gal|游戏|作品|番剧|动画|漫画)|\brecommend\b"
_COMPARE = r"对比|比较(?!推荐|喜欢|新|好|高|低|有趣|适合|热门|冷门|长|短|近期)|哪(?:个|部|款|本)更|哪个好|哪部好|哪款好|(?:选|玩|看)哪个|谁更|有[什么啥]+(?:区别|不同|差别)|\b(?:vs\.?|versus)\b"
_FACT = r"是谁|哪里|哪[年天家个些款部]|多少|几[个部天集分]|有(?:没有|无)|有吗|了吗|怎样|怎么样|如何|查询|资料|事实|评[价测分]|口碑|好玩吗|好看吗|能玩吗|能看吗|表现|值得|发售|上架|更新|新闻|信息|版本|发布时间|最新|查证|核实|实测|攻略|定价|价格|销量|评价|详情|介绍|背景|出了|开服|新作|\?$"
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
_GENRES = (
    "纯爱", "废萌", "拔作", "催泪", "治愈", "泣系", "郁系", "悬疑", "推理", "恐怖", "日常", "校园", "恋爱", "百合",
    "乙女", "BL", "后宫", "热血", "机战", "异世界", "科幻", "奇幻", "剧情向", "短篇", "长篇", "全年龄", "女性向", "战斗",
    "搞笑", "轻松", "刀子", "NTR", "RPG", "SLG", "ACT", "STG", "开放世界", "策略", "卡牌", "回合制",
)

# Small alias seed for unqualified requests. Explicit media wording takes priority.
_TITLE_ALIASES = {
    "galgame": {
        "CLANNAD": ("clannad", "团子大家族"),
        "Little Busters!": ("little busters", "little busters!", "小小克星"),
        "Rewrite": ("rewrite", "罚抄", "篝线"),
        "星之梦": ("星之梦", "planetarian"),
        "近月少女的礼仪": ("近月少女的礼仪", "近月", "月に寄りそう乙女の作法"),
        "苍之彼方的四重奏": ("苍之彼方的四重奏", "苍彼", "蒼の彼方のフォーリズム", "aokana"),
        "樱之诗": ("樱之诗", "サクラノ詩", "sakura no uta"),
        "兰斯10": ("兰斯10", "兰斯 10", "ランス10", "rance 10"),
        "白色相簿2": ("白色相簿2", "白色相簿 2", "白学2", "white album 2", "wa2"),
        "命运石之门": ("命运石之门", "steins;gate", "石头门"),
        "命运石之门0": ("命运石之门0", "命运石之门 0", "steins;gate 0", "石头门0"),
        "Fate/stay night": ("fate/stay night", "fate stay night", "命运长夜"),
        "千恋＊万花": ("千恋万花", "千恋*万花", "千恋·万花", "senren banka"),
        "沙耶之歌": ("沙耶之歌", "saya no uta"),
        "魔法使之夜": ("魔法使之夜", "魔夜", "witch on the holy night"),
        "缘之空": ("缘之空", "yosuga no sora"),
        "RIDDLE JOKER": ("riddle joker",),
        "Café Stella": ("cafe stella", "café stella", "星光咖啡馆与死神之蝶"),
        "DRACU-RIOT!": ("dracu-riot", "dracu riot", "吸血鬼之恋"),
        "天使☆嚣嚣 RE-BOOT!": ("天使嚣嚣", "天使☆嚣嚣", "tenshi souzou"),
    },
    "anime": {
        "CLANNAD AFTER STORY": ("clannad after story", "clannad as"),
        "葬送的芙莉莲": ("葬送的芙莉莲", "芙莉莲", "frieren"),
        "孤独摇滚！": ("孤独摇滚", "bocchi the rock", "波奇酱"),
        "间谍过家家": ("间谍过家家", "间谍家家酒", "spy x family"),
        "鬼灭之刃": ("鬼灭之刃", "鬼灭", "demon slayer"),
        "进击的巨人": ("进击的巨人", "attack on titan"),
        "药屋少女的呢喃": ("药屋少女的呢喃", "药屋少女"),
    },
    "acg_game": {
        "原神": ("原神", "genshin impact"),
        "崩坏：星穹铁道": ("崩坏:星穹铁道", "星穹铁道", "崩铁", "星铁", "honkai star rail"),
        "崩坏3": ("崩坏3", "崩坏三"),
        "绝区零": ("绝区零", "zenless zone zero"),
        "鸣潮": ("鸣潮", "wuthering waves"),
        "明日方舟": ("明日方舟", "arknights"),
        "碧蓝航线": ("碧蓝航线", "azur lane"),
        "蔚蓝档案": ("蔚蓝档案", "碧蓝档案", "blue archive"),
    },
}


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
    entries = [
        (alias, title, medium)
        for medium, titles in _TITLE_ALIASES.items()
        for title, names in titles.items()
        for alias in names
    ]
    if isinstance(aliases, Mapping):
        configured = aliases.items()
    elif isinstance(aliases, Sequence) and not isinstance(aliases, str):
        configured = (part.split("=", 1) for part in aliases if isinstance(part, str) and "=" in part)
    else:
        configured = ()
    for alias, title in configured:
        if isinstance(alias, str) and isinstance(title, str) and 0 < len(alias.strip()) <= 80 and 0 < len(title.strip()) <= 80:
            official = title.strip()
            medium = next((category for name, canonical, category in entries
                           if official.casefold() in {name.casefold(), canonical.casefold()}), "galgame")
            entries.append((alias.strip(), official, medium))
    matches = []
    for alias, title, medium in entries:
        match = _search(_term_pattern(alias), text)
        if match:
            matches.append((match.start(), match.end(), title, medium))
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
        return "news"
    if not _search(r"推荐|安利|值得|哪个好|怎么选", text) and _search(_RELEASE_LIST, text):
        return "release"
    if _search(_RECOMMEND, text):
        return "recommendation"
    if _search(_SCHEDULE, text):
        return "schedule"
    if _search(r"(?:有哪些|什么|哪个|哪部|哪款).{0,20}(?:最火|热门|高分|评价好)|(?:最火|热门|高分|评价好).{0,20}(?:有哪些|是什么|有啥)", text):
        return "recommendation"
    if _search(r"攻略|配队|全结局|真结局|路线|怎么通关|存档|\bwalkthrough\b", text):
        return "walkthrough"
    if _search(r"汉化|中文补丁|中文支持|支持中文|官中|有没有中文", text):
        return "localization"
    if _search(_DURATION, text):
        return "duration"
    if _search(r"声优|配音|\bcv\b|角色|画师|原画|制作组|制作公司|staff|\bcast\b", text):
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
    if task == "progress":
        if "acg_game" in known_media and _search(_GAME_UPDATE, text):
            return "acg_game"
        if _search(_GAME, text) and not known_media:
            return "game"
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
    value = re.sub(r"^(?:gal\s*game|gal|番剧|动画|漫画|二游)\s*[:：]?\s*", "", value, flags=re.IGNORECASE)
    value = re.split(r"(?:(?:玩|看|选)?哪个|哪部|哪款|谁更|区别|差别|有(?:没有|什么)|更值得|是不是|是什么|怎么[样选]|好不好|请问|什么时候|何时|更新|放送|播出|开播|声优|配音|攻略|支持中文|汉化|发售|定价|价格|[，,。；;?!])", value, maxsplit=1)[0]
    value = value.strip(" 《》「」『』“”\"':：")
    value = re.sub(r"(?:动画版|漫画版|番剧|动画|动漫|漫画|最近|的)$", "", value).strip()
    if not 1 < len(value) <= 80 or _search(r"^(?:这些|这几个|这个|那个|那几个|这两部|那个番|这部番|哪个|最新|本季|这季|最近|今年|明年|上季|上周|本周|下周|今天|明天|什么时候|第[几一二三四五六七八九十\d]+[季集话]|现在|下一集|完结|新番|番剧|动画|动漫|二游|游戏|漫画|作品|原作)$", value):
        return ""
    return value


def _detect_titles(text: str, task: str, medium: str, known) -> tuple[str, ...]:
    quoted = [(match.start(), next(value for value in match.groups() if value).strip()) for match in _QUOTED.finditer(text)]
    if quoted:
        # Prefer the canonical spelling of a quoted alias, without making a second
        # candidate out of the spelling the user provided.
        titles = [next((title for start, stop, title, _ in known if start == offset + 1 and stop == start + len(value)), value)
                  for offset, value in quoted]
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
    if medium == "galgame" and task not in {"recommendation", "news"}:
        candidate = _candidate_title(re.split(r"通关|游玩|流程|多长|多久|评分|发售|上线|上架|的?声优|的?制作|的?画师", text, maxsplit=1)[0])
        if candidate and not _search(_GALGAME + r"|本[月周]|这个月|最近|新作|日历", candidate):
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
    ranking = "popularity" if _search(r"热门|人气|最火|最多人|热销|热度|\bpopular\b", text) else ""
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


def _detect_preferences(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    exclusions = []
    for match in _EXCLUDED.finditer(text):
        negative = re.split(r"(?:但是|但要|最好|预算|支持中文|可以剧透|只要|想要|想看|想玩|想找)", match.group(1), maxsplit=1)[0]
        for phrase in re.split(r"\s*(?:以及|或者|[和与及或、/])\s*", negative):
            value = re.sub(r"(?:的(?:作品|游戏|番剧)?|类型|题材)$", "", phrase.strip())
            if value and not _search(r"剧透|联网|上网|搜索|检索|查资料", value):
                exclusions.append(value)
    positive = _EXCLUDED.sub(" ", text)
    genres = tuple(genre for genre in _GENRES if _search(_term_pattern(genre), positive))
    return genres, _unique(exclusions)[:8]


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

    task = _detect_task(text)
    known = _known_titles(text, aliases)
    if task in {"fact", "recommendation"} and "还是" in text and (len(known) >= 2 or len(list(_QUOTED.finditer(text))) >= 2):
        task = "comparison"
    extra = tuple(term for term in galgame_terms if isinstance(term, str) and term.strip())
    medium = _detect_medium(text, task, known, extra)
    titles = _detect_titles(text, task, medium, known)
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
    constraints = _constraints_text(text, known)
    positive = _EXCLUDED.sub(" ", constraints)
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
    genres, exclusions = _detect_preferences(constraints)
    fields = tuple(name for name, pattern in (
        ("store", r"steam|价格|多少钱|国区|上架|下架|折扣"),
        ("language", r"中文|汉化|官中|日文|英文"),
        ("release", r"发售|发行|首发|上市|新作|发布"),
        ("duration", _DURATION), ("score", r"评分|评价|口碑|分数|好评"),
        ("staff", r"声优|配音|画师|人员|制作|原画|脚本|编剧|\bcv\b"),
        ("tags", r"题材|类型|标签"),
    ) if _search(pattern, positive))
    bare_title = any(not (text[:start_at] + text[stop:]).strip(" 《》「」『』“”!?")
                     for start_at, stop, _, _ in known)
    bare_title = bare_title or bool(_QUOTED.fullmatch(text.strip("!?")))
    if not explicit and _search(_CASUAL, text) and not _search(r"[?]|请问|推荐|怎么|为什么|攻略", text):
        route, reason, task = "skip", "casual", "chat"
    elif medium == "anime" and task == "progress" and not titles:
        route, reason = "clarify", "missing_anime_title"
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
    )
