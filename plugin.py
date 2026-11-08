from __future__ import annotations

import asyncio
import re
from contextlib import AsyncExitStack
from datetime import date, datetime, timedelta, timezone

import httpx
from maibot_sdk import Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import HookMode, ToolParameterInfo, ToolParamType

from .config import AcgSearchConfig
from .intent import classify
from .search_flow import TOOL_NAME, planner_tools, run_search
from .web_search import valid_source_url
from .ymgal import YmgalClient

MAX_QUERY_CHARS = 600
USER_AGENT = "naiiyo/maibot-acg-search/0.1.0 (https://github.com/naiiyo/maibot-acg-search)"
SOURCE_NAMES = {"bangumi": "Bangumi", "vndb": "VNDB", "erogamescape": "批评空间", "steam": "Steam", "ymgal": "月幕 Galgame"}
_PLANNER_MESSAGE_PREFIX = re.compile(
    r'<message(?P<attrs>(?: [a-z_]+="[^"\r\n]*")+)>\r?\n'
)
_PLANNER_RESPONSE_ITEMS = ("AssistantMessageItem", "FunctionCallItem", "FunctionCallOutputItem")
DESCRIPTION = (
    "需要联网查证时优先使用。Galgame、番剧、漫画和二次元游戏会查专门目录，"
    "番剧放送与分集进度会补查官方来源。普通网页检索使用当前配置的搜索后端。"
    "传入用户本轮的完整问题，可补全已有上下文支持的作品名或型号，保留年份、平台、地区、语言和排除条件。"
    "闲聊、创作、概念解释以及用户要求不联网时无需调用。"
)


def _today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def _latest_planner_question(items, schema_version) -> str:
    """Read the latest inbound IM message from MaiBot's v1 context snapshot."""
    if schema_version != 1 or not isinstance(items, list):
        return ""
    for item in reversed(items):
        if not isinstance(item, dict):
            return ""
        item_type = item.get("item_type")
        if item_type in _PLANNER_RESPONSE_ITEMS:
            return ""
        if item_type != "UserMessageItem":
            continue
        parts = item.get("parts")
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], dict):
            return ""
        first = parts[0]
        if first.get("type") != "text" or not isinstance(first.get("text"), str):
            return ""
        text = first["text"]
        # The planner appends synthetic user reminders after the history. Only the
        # host's IM prefix (planner_messages.py) marks a genuine session message.
        prefix = _PLANNER_MESSAGE_PREFIX.match(text, 0, 2048)
        if prefix is None:
            continue
        attrs = prefix["attrs"]
        if (' time="' not in attrs or ' user="' not in attrs or not attrs.startswith(' msg_id="')
                or attrs.startswith(' msg_id=""') or ' is_self_message="true"' in attrs):
            return ""
        fragments = [text[prefix.end():]]
        for part in parts[1:]:
            if not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str):
                continue
            if not part["text"].lstrip().startswith("@"):
                fragments.append(part["text"])
        query = " ".join(fragments).strip()
        if len(query) <= MAX_QUERY_CHARS and "[消息类型]转发消息" not in query:
            return query
        return ""
    return ""


def _add_link(lines: list[str], title: str, url) -> None:
    if valid_source_url(url):
        lines.append(f"{title} {url}")


def _episode_lines(evidence: dict) -> list[str]:
    rows = [
        evidence.get("last_catalogue_date_before_today"),
        *evidence.get("catalogue_dates_today", []),
        evidence.get("next_catalogue_date_after_today"),
    ]
    return [
        f"目录日期 {row['catalogue_date']} · 第 {row['episode'] or row['display_number']} 集"
        for row in rows if row and (row.get("episode") or row.get("display_number"))
    ]


def _format_report(report: dict) -> str:
    directory = report.get("catalogue") or {}
    items = directory.get("items") or directory.get("results") or []
    lines = ["二次元特化搜索"]
    if report.get("reason"):
        lines.append(report["reason"])
    if directory.get("reason"):
        lines.append("未能核验出满足当前条件的完整目录结果。")
    unavailable = [
        SOURCE_NAMES.get(name, name) for name, state in directory.get("sources", {}).items()
        if state.get("status") in {"partial", "unavailable"}
    ]
    if unavailable:
        lines.append("未取得完整资料：" + "、".join(unavailable) + "。")
    unmatched = directory.get("unmatched_titles") or directory.get("unmatched_entities") or []
    if unmatched:
        names = [row.get("title", "") if isinstance(row, dict) else row for row in unmatched]
        lines.append("待确认作品：" + "、".join(name for name in names if isinstance(name, str) and name) + "。")
    for article in directory.get("news", [])[:5]:
        _add_link(lines, article.get("title", "月幕 Galgame"), article.get("url"))
    for index, item in enumerate(items[:5], 1):
        title = item.get("title") or "作品"
        when = item.get("first_release") or item.get("date") or item.get("year") or ""
        lines.append(f"{index}. {title}" + (f"（{when}）" if when else ""))
        if item.get("length_minutes"):
            lines.append(f"VNDB 游玩时长约 {item['length_minutes'] / 60:g} 小时。")
        bangumi = "Bangumi"
        if item.get("score") is not None:
            bangumi += f" {item['score']:g}/10"
        if item.get("votes"):
            bangumi += f" · {item['votes']:g} 人评价"
        _add_link(lines, bangumi, item.get("url"))
        for record in item.get("records", []):
            key = record.get("source") or "资料"
            source = SOURCE_NAMES.get(key, key)
            if record.get("rating") is not None:
                source += f" {record['rating']:g}/{record['rating_scale']:g}"
            if record.get("votes"):
                source += f" · {record['votes']} 人评价"
            _add_link(lines, source, record.get("url"))
        edition = item.get("edition") or {}
        if edition:
            edition_label = "VNDB 官方发行版本" if edition.get("official") else "VNDB 发行版本"
            _add_link(lines, f"{edition_label} {edition.get('released', '')}".strip(), edition.get("url"))
        steam = item.get("steam") or {}
        steam_label = "Steam"
        if steam.get("date"):
            steam_label += f" · 商店日期 {steam['date']}"
        if steam.get("observed_price"):
            steam_label += f" · {steam['observed_price']}"
        if steam.get("languages"):
            steam_label += f" · 语言 {steam['languages'][:130]}"
        _add_link(lines, steam_label, steam.get("url"))
        if steam and not steam.get("url"):
            lines.append("该作品的 Steam 商店条目尚未确认。")
        missing = [SOURCE_NAMES.get(name, name) for name in item.get("missing_sources", []) if name != "steam"]
        if missing:
            lines.append("该作品待补目录证据：" + "、".join(missing) + "。")
        episode = item.get("episode_evidence") or {}
        lines.extend(_episode_lines(episode))
        if episode.get("notice"):
            lines.append(episode["notice"])
    broadcast = directory.get("official_broadcast") or {}
    if broadcast.get("notice"):
        lines.append(broadcast["notice"])
    for page in broadcast.get("official_page_evidence", [])[:2]:
        _add_link(lines, f"{page['publisher']} · {page['title']}", page['url'])
        lines.append(f"分集资料摘录：{page['episode_excerpt']}")
    for row in broadcast.get("references", [])[:3]:
        _add_link(lines, row["title"], row["url"])
    if (report.get("medium") == "anime" and report.get("task") in {"schedule", "progress", "availability"}
            and not broadcast.get("official_page_evidence")):
        lines.append("暂未取得能核对实际播出的官方放送资料。")
    for search in report.get("web", [])[:2]:
        for hit in search.get("results", [])[:3]:
            _add_link(lines, hit["title"], hit["url"])
    if len(lines) == 1:
        lines.append(next(
            (row["reason"] for row in report.get("web", []) if row.get("reason")),
            "这次没有取得足够的可核验资料。",
        ))
    kept, size = [], 0
    for line in lines:
        size += len(line) + 1
        if size > 3500:
            kept.append("其余来源已省略。")
            break
        kept.append(line)
    return "\n".join(kept)


class AcgSearchPlugin(MaiBotPlugin):
    config_model = AcgSearchConfig

    async def on_load(self) -> None:
        self._slots = asyncio.Semaphore(4)
        self._ymgal: YmgalClient | None = None
        self._ymgal_credentials: tuple[str, str] | None = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(8, connect=5),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
        self._loopback_client = httpx.AsyncClient(
            timeout=httpx.Timeout(8, connect=5),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": USER_AGENT},
        )

    async def on_unload(self) -> None:
        await asyncio.gather(self._client.aclose(), self._loopback_client.aclose())

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        # The Runner already refreshed self.config. Each search keeps its own snapshot.
        return None

    @HookHandler("maisaka.planner.before_request", mode=HookMode.BLOCKING)
    async def route_planner_search(self, tool_definitions=None, hook_name=None, **kwargs) -> dict:
        config = self.config
        if not config.search.planner_routing:
            return {}
        query = _latest_planner_question(kwargs.get("items"), kwargs.get("item_schema_version"))
        if not query:
            return {}
        intent = classify(
            query, today=_today(), aliases=config.catalogue.aliases,
            galgame_terms=config.catalogue.galgame_terms,
        )
        routed = planner_tools(tool_definitions, prefer_plugin=intent.route in {"catalogue", "web"})
        if routed is not None:
            return {"modified_kwargs": {**kwargs, "tool_definitions": routed}}
        return {}

    async def _run(self, query: str, *, force_search: bool = False) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
            return {"status": "invalid", "reason": f"请提供 1 至 {MAX_QUERY_CHARS} 字的检索问题。"}
        if self._slots.locked():
            return {"status": "busy", "reason": "搜索正在处理其他请求，请稍后再试。"}
        async with self._slots, AsyncExitStack() as resources:
            config = self.config
            today = _today()
            intent = classify(
                query, today=today,
                aliases=config.catalogue.aliases, galgame_terms=config.catalogue.galgame_terms,
                force_search=force_search,
            )
            egs_client = None
            ymgal = None
            if intent.route == "catalogue" and intent.medium == "galgame":
                credentials = (config.catalogue.ymgal_client_id.strip(), config.catalogue.ymgal_client_secret.strip())
                if self._ymgal is None or self._ymgal_credentials != credentials:
                    self._ymgal = YmgalClient(self._client, client_id=credentials[0], client_secret=credentials[1])
                    self._ymgal_credentials = credentials
                ymgal = self._ymgal
                proxy = config.catalogue.egs_proxy.strip()
                if proxy:
                    try:
                        egs_client = await resources.enter_async_context(httpx.AsyncClient(
                            proxy=proxy, timeout=httpx.Timeout(8, connect=5), follow_redirects=False,
                            headers={"User-Agent": USER_AGENT},
                        ))
                    except (ValueError, ImportError):
                        self.ctx.logger.warning("批评空间代理地址无效，跳过批评空间查询。")
                        egs_client = False
            return await run_search(
                self.ctx, self._client, config, intent, egs_client=egs_client, ymgal=ymgal,
                today=today, loopback_client=self._loopback_client,
            )

    @Tool(
        TOOL_NAME,
        description=DESCRIPTION,
        parameters=[
            ToolParameterInfo(
                name="query", param_type=ToolParamType.STRING,
                description="用户本轮完整的检索问题，保留明确的筛选条件。",
                required=True,
            ),
        ],
        core_tool=True,
        timeout_ms=35000,
    )
    async def handle_search(self, query: str, **kwargs) -> dict:
        return await self._run(query)

    @Command(
        "acg_search_command",
        pattern=r"^[/／](?:二次元搜索|acgsearch)(?:\s+(?P<query>[\s\S]+))?\s*$",
        description="使用二次元特化搜索查资料，用法 /二次元搜索 <问题> 或 /acgsearch <问题>",
        timeout_ms=35000,
    )
    async def handle_command(self, stream_id: str = "", matched_groups: dict | None = None, **kwargs):
        if not stream_id:
            return False, "缺少会话", 1
        query = ((matched_groups or {}).get("query") or "").strip()
        if query:
            report = await self._run(query, force_search=True)
            reply = _format_report(report)
        else:
            reply = "用法 /二次元搜索 <问题> 或 /acgsearch <问题>，例如 /二次元搜索 推荐一部 2026 年的 Galgame"
        sent = await self.ctx.send.text(reply, stream_id, storage_message=False)
        if not sent:
            self.ctx.logger.warning("二次元特化搜索未能发送命令结果")
            return False, "发送检索结果失败", 1
        return True, "", 1


def create_plugin() -> AcgSearchPlugin:
    return AcgSearchPlugin()
