# 二次元特化搜索

**MaiBot ACG Search** · v0.1.1 · `naiiyo.acg-search`

想找有官方中文的 Galgame、看看两部作品适合先玩哪部，或者确认新番播到了第几集？直接问麦麦。插件会按问题选择作品目录和网页搜索，带回可核对的来源。资料只支持部分条件时，会说明查到了什么、还缺什么。

> 我是维护者 naiiyo，还在学习怎么用 GitHub 和维护代码，有做得不到位的地方，也请大家多包涵。遇到问题或有改进建议，欢迎提 Issue。如果方便，附上插件版本和复现步骤，会更容易定位问题。[提交 Issue](https://github.com/naiiyo/maibot-acg-search/issues/new)

[能查什么](#能查什么) · [安装](#安装) · [配置](#配置) · [常见问题](#常见问题) · [更新记录](CHANGELOG.md) · [GitHub Releases](https://github.com/naiiyo/maibot-acg-search/releases)

## 能查什么

| 你可以这样问 | 插件会查什么 |
| --- | --- |
| 推荐两部 2024 年 Steam 上有官方中文的 Galgame，别剧透 | 先查作品首发，再用对应 Steam 商店条目确认发售状态和语言。目录还可以补充社区评价、别名和其他发行版本。 |
| 推荐几部 2023 年首发的校园题材 Galgame，排除我玩过的《天使☆嚣嚣 RE-BOOT!》 | 在指定年份的作品目录里找候选，核对作品首发年份和排除名单，按题材偏好挑选。 |
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
/二次元搜索 推荐几部 2023 年首发的校园题材 Galgame
/acgsearch 《葬送的芙莉莲》第二季更新到第几集了
```

## 安装

兼容范围为 MaiBot `1.3.16–1.4.99`、插件 SDK `2.17.0–2.22.99`，安装时需同时满足。下限由插件使用的原生联网搜索与网页读取接口决定，上限覆盖对应次版本的后续补丁更新。插件清单声明 `httpx` 依赖。[MaiBot 插件发布指南](https://docs.maibot.chat/plugin/submission)

1. 在 MaiBot WebUI 的插件市场搜索「二次元特化搜索」并选择 `0.1.1`。也可以在插件管理页上传 GitHub Release 提供的安装 ZIP，或手动将解压后的插件目录放进 MaiBot 的 `plugins/`。手动解压时确认 `_manifest.json` 和 `plugin.py` 位于插件目录第一层。[下载 v0.1.1 安装包](https://github.com/naiiyo/maibot-acg-search/releases/download/v0.1.1/maibot_acg_search_v0.1.1.zip)
2. 在插件管理页启用「二次元特化搜索」，并按 WebUI 提示重载或重启。安装后使用「可视化」页调整配置。
3. 若使用默认网页搜索，在 MaiBot 模型配置的功能分配里给「联网搜索」选择服务。若要补读放送页面，也为「网页读取」分配服务。回到聊天里发送上面的任一命令，检查能否返回带链接的结果。

手动从 v0.1.0 升级时，先备份已安装的插件目录，再用新版文件覆盖原目录，保留原 `config.toml`，然后重载。MaiBot 会用默认值补齐新增配置，原有搜索接口、凭据、作品别名及启用状态随原配置保留。

## 配置

首次启用时 MaiBot 从配置模型生成 `config.toml`。可在 WebUI 的「可视化」页编辑，默认值见 [config.example.toml](config.example.toml)。

### 网页搜索

自定义 API 地址和 Key 均留空时，网页搜索使用 MaiBot 的「联网搜索」配置。要改用自定义服务，在「网页搜索」同时填入完整端点和 Key，并选择匹配的协议。

| 协议 | 官方端点 | 结果列表 |
| --- | --- | --- |
| Tavily | `https://api.tavily.com/search` | `results` |
| AnySearch | `https://api.anysearch.com/v1/search` | `data.results` |

兼容端点需接收 JSON POST，参数为 `query`、`max_results`，鉴权为 `Authorization: Bearer <Key>`。结果需要有效的 `url`，可带 `title`、摘要及日期。远程端点使用 HTTPS，本机回环地址也支持 HTTP。启用自定义服务后，通用搜索和目录补查使用同一服务。接口异常或只填一项时会报告原因。清空两项可切回 MaiBot 原生搜索。[Tavily 接口文档](https://docs.tavily.com/documentation/api-reference/endpoint/search) · [AnySearch 接口文档](https://www.anysearch.com/docs/api-endpoints/v1-search) · [MaiBot 模型配置](https://docs.maibot.chat/manual/configuration/model-config)

「优先推荐插件搜索工具」默认开启。识别到 Galgame 目录查询时，Planner 通过 `acg_search` 获取目录结果，插件按需调用已配置的网页搜索服务。其他查询仍可直接使用 MaiBot 原生联网搜索。

对于已确认的点名 Galgame 简介或评价、指定时间范围的番剧推荐，如果 Planner 提供了 `acg_search` 却直接开始回复，插件会在生成回复前用原问题补查。同一条消息已取得的检索结果会复用。

「在回复中显示平台连接问题」默认关闭。目录检索和命令回复会使用已经取得的证据，并说明用户关心的资料缺口。需要查看超时、鉴权失败等原因时，可以打开此开关或查看后台的 `检索完成` 日志。

### 意图识别

在插件的「意图识别 → 意图识别模型」下拉框选择 MaiBot 已配置的文本模型，或选「仅使用本地规则」。在 MaiBot 增删模型后，重新打开插件配置页即可刷新选项。旧配置里的模型名会保留，找不到的名称会在列表中标明。

Planner 启动前只用本地规则判断是否提示搜索。配置快速模型后，实际执行检索时可由它补充查询词、候选标签和网页搜索式，默认最多等 3 秒。留空、超时或输出无效时，用从用户原话解析的标题、日期、媒体类型和来源返回的别名查询。

检索可以承接同一会话、同一发送者的近期追问。Galgame 推荐支持补充题材偏好，已点名的作品可以继续问某个目录的条目或评价。启用模型时，当前问题和用于理解追问的有限上下文会交给所选模型。作品日期、评分和平台信息仍以检索来源为准。

### 作品目录

| 设置 | 用途 |
| --- | --- |
| Galgame 评价人数参考 | 默认 100，仅作排序参考。手动改为其他值后，至少一个来源要达到所设门槛。 |
| 默认推荐数量 | 默认 3 项。实际返回数量取决于通过核验的结果。 |
| Galgame 识别词、作品别名 | 适合补充生僻作品名、厂商名和常用简称。别名每项写成 `别名=正式名称`。 |
| Bangumi 访问令牌 | 可选。令牌被拒绝时会尝试匿名查询，匿名查询使用公开条目。 |
| 批评空间代理 | 可选。填写批评空间专用 HTTP 或 HTTPS 代理。 |
| 月幕客户端 ID、客户端密钥 | 成对填写后启用月幕查询。两项留空时，插件会跳过月幕并继续查询其他来源。 |

启用月幕时，在官方开发文档找到「Public client identifier」，复制其中的 `client_id` 和 `client_secret`。打开 MaiBot WebUI 的「二次元特化搜索 → 作品目录」，分别填入「月幕客户端 ID」和「月幕客户端密钥」并保存。已有专属凭证的用户可填写自己的两项。月幕开放 API 适用于非商业用途。[月幕 Galgame 官方凭证说明](https://www.ymgal.games/developer#public-client-identifier)

### 查询和数据

| Galgame 来源 | 主要资料 |
| --- | --- |
| VNDB | 作品简介、首发、标签、时长和投票人数 |
| Bangumi | 中文条目、简介、收藏热度和评分 |
| 月幕 Galgame | 中文简介、别名、发售档期及发行版本 |
| EGS（批评空间） | 日本玩家评分和评价人数 |
| Steam | 商店简介、实际发行版本，以及已上架作品的公开玩家评价样本 |
| CnGal | 已点名中文 Galgame 的作品简介与关联评测文章 |

- 年份筛选使用作品首发资料，另行呈现移植、汉化和 Steam 上架证据。指定 Steam 发行地区或语言时，会核对匹配商店条目中的可用信息。
- 按历史年份推荐时，目录候选按查询年份取样。明确排除的作品不进入推荐。偏好如「偏日常温情」「偏剧情」用于选出更合适的候选，回答会标明题材依据和仍需核实的条件。
- VNDB、批评空间、Bangumi 和 Steam 的统计口径各自保留。按某年作品热度筛选，使用查询时可见的累计数据和候选样本。查询某一年的历史热度快照时，插件会说明数据缺口。
- 问「这部讲什么」「玩家怎么评价」时会查对应作品的简介或评论正文。Steam 正文是小样本，优先呈现样本里的中文评论及不同看法。CnGal 评测文章会标明出处。回答应以自己的话概述，区分作品简介、站内评测文章、个别玩家看法和平台汇总评分。
- 目录结果、目录补查和网页来源分开显示。条目缺失、站点不可达、版本或别名歧义都会影响核验结果。
- Bangumi 搜索失败时可改用条目列表或旧版标题搜索，再查条目详情。官方网页读取失败时，可对识别出的正版放送页进行限时直读；目录分集仍与官方页面证据分开显示。
- 番剧推荐支持「2026 年三季度」或「2026Q3」等季度写法。热门推荐以 Bangumi 查询时可见的收藏热度为依据，所选日期用于筛选首播作品。
- Bangumi 条目可按标题或条目链接查询。需要制作人员、角色和配音、关联作品时，插件按请求读取公开接口并保留条目链接。分集目录仍与实际放送证据分开。
- Galgame 推荐按具体字段收集证据。可靠的作品与首发记录即可作为候选依据，其他站点用来补充资料。指定语言、平台和排除条件仍按各自的证据处理。某站超时或缺少条目时会保留其他来源的结果。一般行业新闻按月幕资讯和网页来源查询。
- 插件发起检索时会向所选服务提交问题或提取出的检索词。API Key、Bangumi 令牌、批评空间代理和月幕客户端密钥保存在本地 `config.toml`，在 WebUI 中以密码框显示。分享配置、日志或目录前请移除凭据。

## 常见问题

**可视化配置页提示缺少 `plugin.config_version`。** 在原始 TOML 的 `[plugin]` 段增加 `config_version`，值可参照本包的 [config.example.toml](config.example.toml)。保留其他值，保存后刷新配置页。若段已存在，直接在该段加一行。

**命令可以查，自然聊天没有查。** 自然语言查询由 Planner 选用工具。可先检查本会话是否启用了插件工具，再用一句包含作品名、作品类别或具体查询目标的话测试。

**网页搜索不可用。** 默认搜索请检查 MaiBot 的「联网搜索」服务分配。自定义搜索请核对端点、Key 和协议三项。批评空间、VNDB、Bangumi 和 Steam 目录请求也各自需要网络可达。

## 项目与许可

- 插件作者及文档作者为 Codex。构想和需求由 naiiyo 提供，GitHub 仓库由 naiiyo 维护。[naiiyo 的 GitHub 主页](https://github.com/naiiyo)
- ACG 查询规划参考了 AstrBot 插件伴境。Galgame 站点接入调研参考了 [mizuku003/maibot-galgame](https://github.com/mizuku003/maibot-galgame)。
- 接口资料见 [MaiBot 插件开发指南](https://docs.maibot.chat/plugin/)、[Bangumi API](https://github.com/bangumi/api/blob/master/open-api/v0.yaml)、[VNDB Kana API](https://api.vndb.org/kana)、[批评空间公开查询表单](https://erogamescape.dyndns.org/~ap2/ero/toukei_kaiseki/sql_for_erogamer_form.php)、[月幕 Galgame 开放 API](https://www.ymgal.games/developer)、[Steam 玩家评价 API](https://partner.steamgames.com/doc/webAPI/IUserReviewsService) 和 [CnGal 开放 API](https://api.cngal.org/swagger/index.html)。

本项目按 GNU Affero General Public License 第 3 版或任何后续版本（`AGPL-3.0-or-later`）发布。完整许可见 [LICENSE](LICENSE)。
