import os
from pathlib import Path
import tomllib
from typing import Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator


_RULE_ONLY = "__local_rules__"


def _configured_text_model_names() -> list[str]:
    shared = os.environ.get("MAIBOT_SHARED_MODEL_CATALOG")
    instance = Path(os.environ.get("MAIBOT_INSTANCE_DIR") or Path.cwd()).expanduser()
    path = Path(shared).expanduser() if shared else instance / "config" / "model_config.toml"
    try:
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return []
        catalogue = tomllib.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return []

    providers, models = catalogue.get("api_providers"), catalogue.get("models")
    if not isinstance(providers, list) or not isinstance(models, list):
        return []
    provider_names = {row["name"] for row in providers if isinstance(row, dict)
                      and isinstance(row.get("name"), str) and row["name"].strip()}
    names = set()
    for row in models:
        if not isinstance(row, dict) or row.get("model_type") != "llm":
            continue
        name, provider, identifier = row.get("name"), row.get("api_provider"), row.get("model_identifier")
        if (isinstance(name, str) and 0 < len(name) <= 150 and name == name.strip() and name != _RULE_ONLY
                and isinstance(provider, str) and provider in provider_names
                and isinstance(identifier, str) and identifier.strip()):
            names.add(name)
    return sorted(names, key=str.casefold)


def model_choices_schema(schema: dict, selected: str = "") -> dict:
    """Populate the native select without constraining legacy TOML values."""
    names = _configured_text_model_names()
    field = schema["sections"]["intent"]["fields"]["model_name"]
    choices = [_RULE_ONLY, *names]
    labels = {f"choice:{_RULE_ONLY}": "仅使用本地规则"}
    if selected and selected not in choices:
        choices.append(selected)
        labels[f"choice:{selected}"] = f"{selected}（当前模型不可用）"
    field.update(ui_type="select", choices=choices, placeholder="仅使用本地规则", i18n={"zh-CN": labels})
    if not names:
        field["hint"] = "没有检测到可选的文本模型。请在 MaiBot 模型管理中配置文本模型后，重新打开此页。"
    return schema


class PluginSettings(PluginConfigBase):
    """MaiBot 配置版本和插件开关。"""

    __ui_label__ = "插件设置"

    enabled: bool = Field(default=True, json_schema_extra={"label": "启用二次元特化搜索"})
    config_version: str = Field(default="0.1.1", json_schema_extra={"hidden": True})


class IntentConfig(PluginConfigBase):
    __ui_label__ = "意图识别"
    __ui_order__ = 5

    model_name: str = Field(
        default="", max_length=150,
        description="选择 MaiBot 已配置的快速文本模型，也可以仅使用本地规则。",
        json_schema_extra={"label": "意图识别模型", "hint": "复用 MaiBot 模型管理中的配置。模型不可用时使用本地规则。"},
    )
    timeout_seconds: float = Field(
        default=3.0, ge=1, le=5,
        json_schema_extra={"label": "识别等待上限（秒）", "hint": "模型超时或返回无效时继续使用本地规则。"},
    )

    @field_validator("model_name", mode="before")
    @classmethod
    def _normalize_rule_choice(cls, value):
        return "" if value == _RULE_ONLY else value


class SearchConfig(PluginConfigBase):
    """留空时复用 MaiBot 当前分配的联网搜索服务。"""

    __ui_label__ = "网页搜索"
    __ui_order__ = 10

    api_protocol: Literal["tavily", "anysearch"] = Field(
        default="tavily",
        description="自定义搜索接口的协议。留空地址和 Key 时仍使用 MaiBot 原生搜索。",
        json_schema_extra={"label": "自定义搜索协议", "hint": "自定义接口可选 Tavily 或 AnySearch。地址和 Key 均留空时走 MaiBot 原生搜索。"},
    )
    api_url: str = Field(
        default="", max_length=2048,
        description="完整搜索端点，须与 API Key 一起填写。AnySearch 官方端点为 https://api.anysearch.com/v1/search。",
        json_schema_extra={
            "label": "自定义搜索 API 地址",
            "placeholder": "https://api.tavily.com/search",
            "hint": "填写完整端点。AnySearch 的官方端点是 https://api.anysearch.com/v1/search。",
        },
    )
    api_key: str = Field(
        default="", max_length=4096, repr=False,
        description="自定义搜索 API 的 Bearer Key。清空地址和 Key 后恢复 MaiBot 原生搜索。",
        json_schema_extra={
            "label": "自定义搜索 API Key", "input_type": "password", "x-widget": "password",
            "hint": "与自定义 API 地址一起填写，清空两项即可切回 MaiBot 原生搜索。",
        },
    )
    web_results: int = Field(
        default=6, ge=1, le=10, json_schema_extra={"label": "每次网页搜索最多返回条数"},
    )
    request_timeout: int = Field(
        default=10, ge=3, le=18, json_schema_extra={"label": "单次网页搜索超时（秒）"},
    )
    planner_routing: bool = Field(
        default=True,
        description="明确需要搜索时，在 Planner 中优先提供二次元特化搜索入口。",
        json_schema_extra={"label": "优先推荐插件搜索工具"},
    )
    show_source_outages: bool = Field(
        default=False,
        description="在二次元检索回复中显示来源连接失败、超时和鉴权诊断。",
        json_schema_extra={"label": "在回复中显示平台连接问题", "hint": "关闭时仅反馈取得的资料和待核实事实。连接诊断保留在后台日志。"},
    )


class CatalogueConfig(PluginConfigBase):
    """目录检索配置，Bangumi 令牌可留空。"""

    __ui_label__ = "作品目录"
    __ui_order__ = 20

    bangumi_token: str = Field(
        default="", max_length=4096, repr=False,
        description="可选。按该账户的可见范围读取 Bangumi 目录。",
        json_schema_extra={"label": "Bangumi 访问令牌", "input_type": "password", "x-widget": "password"},
    )
    egs_proxy: str = Field(
        default="", max_length=2048, repr=False,
        description="可选。只给批评空间使用的 HTTP 或 HTTPS 代理地址，例如 http://127.0.0.1:7890。",
        json_schema_extra={"label": "批评空间代理", "input_type": "password", "x-widget": "password"},
    )
    ymgal_client_id: str = Field(
        default="", max_length=300,
        description="可选。从月幕开放 API 文档获取，和密钥一起填写。两项留空时跳过月幕查询。",
        json_schema_extra={"label": "月幕客户端 ID"},
    )
    ymgal_client_secret: str = Field(
        default="", max_length=4096, repr=False,
        description="可选。填写月幕官方公开或申请的专属客户端密钥，需与客户端 ID 配对。",
        json_schema_extra={"label": "月幕客户端密钥", "input_type": "password", "x-widget": "password"},
    )
    minimum_galgame_votes: int = Field(
        default=100, ge=1, le=10000,
        json_schema_extra={"label": "Galgame 评价人数参考", "hint": "默认 100 仅作排序参考，改为其他值时用作至少一个站点的筛选门槛。"},
    )
    default_count: int = Field(
        default=3, ge=1, le=5, json_schema_extra={"label": "默认推荐数量"},
    )
    galgame_terms: list[str] = Field(
        default_factory=list,
        description="额外的 Galgame 作品名、别称或厂商名，帮助识别没有写类别词的提问。",
        json_schema_extra={"label": "Galgame 识别词"},
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="已经确认的作品别名，每项写成「别名=正式名称」。",
        json_schema_extra={"label": "作品别名"},
    )


class AcgSearchConfig(PluginConfigBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    intent: IntentConfig = Field(default_factory=IntentConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    catalogue: CatalogueConfig = Field(default_factory=CatalogueConfig)
