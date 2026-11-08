from typing import Literal

from maibot_sdk import Field, PluginConfigBase


class PluginSettings(PluginConfigBase):
    """MaiBot 配置版本和插件开关。"""

    __ui_label__ = "插件设置"

    enabled: bool = Field(default=True, json_schema_extra={"label": "启用二次元特化搜索"})
    config_version: str = Field(default="0.1.0", json_schema_extra={"hidden": True})


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
        description="可选。与密钥一起填写；两项均留空时使用月幕开发文档提供的公开客户端凭证。",
        json_schema_extra={"label": "月幕客户端 ID"},
    )
    ymgal_client_secret: str = Field(
        default="", max_length=4096, repr=False,
        description="可选。自定义月幕 OAuth2 客户端密钥，与客户端 ID 一起填写。",
        json_schema_extra={"label": "月幕客户端密钥", "input_type": "password", "x-widget": "password"},
    )
    minimum_galgame_votes: int = Field(
        default=100, ge=1, le=10000, json_schema_extra={"label": "Galgame 至少一站的最低评价人数"},
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
    search: SearchConfig = Field(default_factory=SearchConfig)
    catalogue: CatalogueConfig = Field(default_factory=CatalogueConfig)
