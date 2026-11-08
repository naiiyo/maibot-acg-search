# 二次元特化搜索

**MaiBot ACG Search** · v0.1.0 · `naiiyo.acg-search`

想找有官方中文的 Galgame、看看两部作品适合先玩哪部，或者确认新番播到了第几集？直接问麦麦。插件会按问题选择作品目录和网页搜索，带回可核对的来源。资料只支持部分条件时，会说明查到了什么、还缺什么。

[能查什么](#能查什么) · [安装](#安装) · [配置](#配置) · [常见问题](#常见问题) · [更新记录](CHANGELOG.md)

## 能查什么

| 你可以这样问 | 插件会查什么 |
| --- | --- |
| 推荐两部 2024 年 Steam 上有官方中文的 Galgame，别剧透 | 用 Bangumi、VNDB、批评空间和月幕 Galgame 核对候选作品，再查 Steam 对应商店条目、发售状态和语言。 |
| 对比《命运石之门》和《混沌之子》两部 Galgame，哪部更适合先玩？ | 按作品名检索，对照可核实的首发日期、题材、时长、版本和各站评分。 |
| 最近有什么新发售的 Galgame？ | 查询新作档期，区分已发行作品和暂定发售日期。 |
| 《命运石之门》有汉化吗？ | 查找中文版线索，分别核对官方中文、民间汉化和商店语言。 |
| 推荐几部 2025 年的科幻番剧，有没有正版平台？ | 从 Bangumi 找对应动画，补查官方放送或正版平台资料。 |
| 《葬送的芙莉莲》第二季更新到第几集了？ | 结合 Bangumi 分集日期和官方放送资料，区分目录排期与平台实际更新。 |
| 推荐几部推理漫画 / 对比《原神》和《鸣潮》两款二次元游戏 | 查询 Bangumi 对应类别。二次元游戏推荐会检查目录中的 ACG 标签。 |
| 推荐一部 2026 年的游戏本 | 使用 MaiBot 原生联网搜索，或管理员选定的自定义搜索服务。 |

平时用自然语言提问，麦麦的 Planner 决定是否调用 `acg_search`。闲聊、创作和明确要求不联网的消息默认跳过搜索。只问「更新到第几集了」且缺少作品名时，插件会先请你补充标题。

想直接查看检索结果，可用以下命令。命令后加一个空格，再写完整问题。

```text
/二次元搜索 推荐两部 2024 年 Steam 上有中文的 Galgame，别剧透
/acgsearch 《葬送的芙莉莲》第二季更新到第几集了
```

## 安装

v0.1.0 的兼容目标为 MaiBot v1.4.3、插件 SDK 2.22.1。插件清单声明 `httpx` 依赖。[MaiBot v1.4.3 发布页](https://github.com/Mai-with-u/MaiBot/releases/tag/v1.4.3)

1. 在 MaiBot WebUI 的插件市场搜索「二次元特化搜索」并选择 `0.1.0` 安装。也可以从 GitHub Releases 下载手动安装 ZIP，在插件管理页上传，或将源码目录放进 MaiBot 的 `plugins/`。手动解压时确认 `_manifest.json` 和 `plugin.py` 在插件目录第一层。
2. 在插件管理页启用「二次元特化搜索」，并按 WebUI 提示重载或重启。安装后使用「可视化」页调整配置。
3. 若使用默认网页搜索，在 MaiBot 模型配置的功能分配里给「联网搜索」选择服务。若要补读放送页面，也为「网页读取」分配服务。回到聊天里发送上面的任一命令，检查能否返回带链接的结果。

同一插件 ID 升级时，备份插件目录，将新版代码覆盖到原目录，保留原 `config.toml` 后重载。如果正在从本地预览版「寻证罗盘」迁移，先备份配置并停用预览版，再启用本插件。两个版本的插件 ID 不同，所需的搜索和目录配置可通过 WebUI 重新填入。

## 配置

首次启用时 MaiBot 从配置模型生成 `config.toml`。可在 WebUI 的「可视化」页编辑，默认值见 [config.example.toml](config.example.toml)。

### 网页搜索

自定义 API 地址和 Key 均留空时，网页搜索使用 MaiBot 的「联网搜索」配置。要改用自定义服务，在「网页搜索」同时填入完整端点和 Key，并选择匹配的协议。

| 协议 | 官方端点 | 结果列表 |
| --- | --- | --- |
| Tavily | `https://api.tavily.com/search` | `results` |
| AnySearch | `https://api.anysearch.com/v1/search` | `data.results` |

兼容端点需接收 JSON POST，参数为 `query`、`max_results`，鉴权为 `Authorization: Bearer <Key>`。结果需要有效的 `url`，可带 `title`、摘要及日期。远程端点使用 HTTPS，本机回环地址也支持 HTTP。启用自定义服务后，通用搜索和目录补查使用同一服务。接口异常或只填一项时会报告原因。清空两项可切回 MaiBot 原生搜索。[Tavily 接口文档](https://docs.tavily.com/documentation/api-reference/endpoint/search) · [AnySearch 接口文档](https://www.anysearch.com/docs/api-endpoints/v1-search) · [MaiBot 模型配置](https://docs.maibot.chat/manual/configuration/model-config)

「优先推荐插件搜索工具」默认开启。识别到明确的查询需求时，插件会让 Planner 优先看到已注册的 `acg_search`。MaiBot 原生搜索及其他工具仍可供 Planner 调用。

### 作品目录

| 设置 | 用途 |
| --- | --- |
| Galgame 至少一站的最低评价人数 | 默认 100。候选作品需有至少两家目录的对应记录，其中含 VNDB 或批评空间，且至少一站达到评价人数门槛。 |
| 默认推荐数量 | 默认 3 项。实际返回数量取决于通过核验的结果。 |
| Galgame 识别词、作品别名 | 适合补充生僻作品名、厂商名和常用简称。别名每项写成 `别名=正式名称`。 |
| Bangumi 访问令牌 | 可选。使用令牌时，目录结果受该账号的可见范围影响。 |
| 批评空间代理 | 可选。填写批评空间专用 HTTP 或 HTTPS 代理。 |
| 月幕客户端 ID、客户端密钥 | 两项留空时使用月幕开发文档提供的公开客户端凭证。申请了专属凭证时，填写这两项。月幕的 API 适用于非商业用途。[月幕 Galgame 开放 API](https://www.ymgal.games/developer) |

### 查询和数据

- 年份筛选使用作品首发资料，另行呈现移植、汉化和 Steam 上架证据。指定 Steam 发行地区或语言时，会核对匹配商店条目中的可用信息。
- VNDB、批评空间、Bangumi 和 Steam 的统计口径各自保留。按某年作品热度筛选，使用查询时可见的累计数据和候选样本。查询某一年的历史热度快照时，插件会说明数据缺口。
- 目录结果、目录补查和网页来源分开显示。条目缺失、站点不可达、版本或别名歧义都会影响核验结果。
- Galgame 具名查询和推荐尽可能核对五个来源。作品确有相符的五站资料时才显示完整核验，候选查不到相同条目或某站超时时会标记缺口。一般行业新闻按月幕资讯和网页来源查询。
- 插件发起检索时会向所选服务提交问题或提取出的检索词。API Key、Bangumi 令牌、批评空间代理和月幕客户端密钥保存在本地 `config.toml`，在 WebUI 中以密码框显示。分享配置、日志或目录前请移除凭据。

## 常见问题

**可视化配置页提示缺少 `plugin.config_version`。** 在原始 TOML 的 `[plugin]` 段增加 `config_version = "0.1.0"`，保留其他值，保存后刷新配置页。若段已存在，直接在该段加一行。

**命令可以查，自然聊天没有查。** 自然语言查询由 Planner 选用工具。可先检查本会话是否启用了插件工具，再用一句包含作品名、作品类别或具体查询目标的话测试。

**网页搜索不可用。** 默认搜索请检查 MaiBot 的「联网搜索」服务分配。自定义搜索请核对端点、Key 和协议三项。批评空间、VNDB、Bangumi 和 Steam 目录请求也各自需要网络可达。

## 项目与许可

- 插件作者及文档作者为 Codex。构想和需求由 naiiyo 提供，GitHub 仓库由 naiiyo 维护。[naiiyo 的 GitHub 主页](https://github.com/naiiyo)
- ACG 查询规划参考了 AstrBot 插件伴境。Galgame 站点接入调研参考了 [mizuku003/maibot-galgame](https://github.com/mizuku003/maibot-galgame)。
- 接口资料见 [MaiBot 插件开发指南](https://docs.maibot.chat/plugin/)、[Bangumi API](https://github.com/bangumi/api/blob/master/open-api/v0.yaml)、[VNDB Kana API](https://api.vndb.org/kana)、[批评空间公开查询表单](https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/sql_for_erogamer_form.php)、[月幕 Galgame 开放 API](https://www.ymgal.games/developer) 和 [Steam 商店](https://store.steampowered.com/)。

本项目按 GNU Affero General Public License 第 3 版或任何后续版本（`AGPL-3.0-or-later`）发布。完整许可见 [LICENSE](LICENSE)。
